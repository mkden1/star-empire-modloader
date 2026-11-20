"""click_diag.py - say in the log why a click did, or did not, select what it was aimed at.

Selecting a warp gate or a bot sometimes does nothing, more often when the camera is zoomed out
(this client lets it go out to 0.05, vanilla stops at 0.10). Nothing in the client's own log says
why, and the cause can be any of several things in the vanilla click handler (an object of a higher
priority hit first, a ship's click-assist suppressed by something else in range, a click taken by a
panel before the world sees it, ...). So each left click is recorded, as ONE log line, with what was
in range and what the picker decided:

    click_diag: zoom=0.050 click=(812,455) previous=None hits=[_targeted_planet_id:3, _targeted_gate_id:7]
      -> ('_targeted_planet_id', 3) | ship-pick=(None, None, None) | ships: bot-3 41/5 | gates: Casthoil 18/24*
      | planets: Feshi 14/20* | stations: - | asteroids: 2 in reach, nearest 11/18* | boxes: - | sun: -

Every "a/b" is a distance in pixels over the click radius the game uses for that object; "*" marks
a distance inside the radius (the object was hit by the click). A click the world picker never
received is logged too, when nothing visible explains it ("click_diag: click at (x,y) ... not given
to the world picker").

How it hooks in (all from outside Client.py, applied after the window class):

  * ``pump_watch.get_events`` hands every batch of events to ``note_events``, which remembers the
    left-button presses,
  * ``install(SolarSystemWindow)`` wraps ``_thr_begin_drag`` (the handler calls it for every click
    that reaches the world, before any picking), ``_pick_ship_click`` (to keep what the ship picker
    returned) and ``_step_local_physics`` (once per frame, after the events: any press that never
    reached the world picker is reported),
  * ``install_picker(globals())`` wraps Client.py's global ``_cycle_overlapping_target`` - the one
    call where the handler turns every hit into the single target - and that is where the line is
    written.

The radii copied below mirror the click handler in Client.py (marked there "Left click: target the
ship under the cursor"); they are for the log only and never change what a click does. Costs a few
list scans per click, writes through the client log's queue (never the disk on this thread), and is
switched on/off on the Data Capture page ("Click diagnostics"). Any error turns it off for the
session and says so once.
"""

from __future__ import annotations

import functools
import logging
import math
import time

import pygame

import capture_settings
from warp_gate_fx import OUTER_SCALE as _WARP_VIS_SCALE

logger = logging.getLogger(__name__)

LISTED_PER_KIND = 3
SHIP_ASSIST_PX = 30.0                    # target_picking.SHIP_CLICK_ASSIST_RADIUS_PX
_PENDING_MAX = 16

_pending: list = []                      # (time, pos) of left presses not yet matched to the world picker
_last: dict = {"snap": None, "pick": None}
_failed = False


def reset_for_tests() -> None:
    global _failed
    _failed = False
    _pending.clear()
    _last["snap"] = _last["pick"] = None


def has_failed() -> bool:
    return _failed


def _on() -> bool:
    return (not _failed) and capture_settings.enabled(capture_settings.CLICK_DIAG)


def _fail(where: str) -> None:
    global _failed
    if not _failed:
        _failed = True
        logger.exception("click_diag: %s failed; click diagnostics are off until the client restarts", where)


# -- the event pump ----------------------------------------------------------------------------------

def note_events(events) -> None:
    """Remember every left-button press in a batch from ``pygame.event.get()``."""
    if not _on():
        return
    try:
        for event in events:
            if event.type == pygame.MOUSEBUTTONDOWN and getattr(event, "button", 0) == 1:
                _pending.append((time.monotonic(), (int(event.pos[0]), int(event.pos[1]))))
        del _pending[:-_PENDING_MAX]
    except Exception:
        _fail("event scan")


# -- what is under the click --------------------------------------------------------------------------

def _screen_size() -> "tuple[int, int]":
    surface = pygame.display.get_surface()
    if surface is not None:
        return surface.get_size()
    return pygame.display.get_window_size()


def _fmt(items, mx_hit_factor: float = 3.0) -> str:
    """Nearest few as "label dist/hit" (a * when inside the hit radius), or "-" for nothing in reach."""
    near = sorted((i for i in items if i[1] <= max(i[2] * mx_hit_factor, 120.0)), key=lambda i: i[1])
    if not near:
        return "-"
    return ", ".join(f"{label} {d:.0f}/{hit:.0f}{'*' if d <= hit else ''}"
                     for label, d, hit in near[:LISTED_PER_KIND])


def snapshot(win, mx: int, my: int) -> dict:
    """What a click at (mx, my) would find, by object type, at the window's current camera."""
    zoom = float(win._zoom)
    cam_x, cam_y = float(win._cam_x), float(win._cam_y)
    ww, wh = _screen_size()

    def dist(wx, wy):
        return math.hypot(mx - ((wx - cam_x) * zoom + ww * 0.5), my - ((wy - cam_y) * zoom + wh * 0.5))

    with win._lock:
        layout = win._layout or {}
        npcs = tuple(win._npc_entities.values()) if not win._in_dungeon else ()
        stations = list(win._npc_stations)
        deployed = list(win._deployed_stations)
        asteroids = dict(win._asteroids)
        boxes = {**dict(win._cargo_boxes), **dict(win._dungeon_item_boxes)}
    detection = float((win._find_my_entity() or {}).get("effective_detection", 1.0))
    now = time.monotonic()

    ships = []
    for is_npc, rows in ((False, win._get_interpolated_entities()), (True, npcs)):
        for entity in rows:
            if entity.get("docked", False) or entity.get("username") == win._username:
                continue
            vx, vy = float(entity.get("vx", 0.0)), float(entity.get("vy", 0.0))
            age = max(0.0, now - entity.get("recv_time", now))
            if is_npc:
                wx = float(entity.get("_rx", float(entity["x"]) + vx * age))
                wy = float(entity.get("_ry", float(entity["y"]) + vy * age))
            else:
                wx, wy = float(entity["x"]), float(entity["y"])
            radius = float(entity.get("radius", 36 if is_npc else 8))
            hull = max(5, min(int(radius), int(radius * zoom)))
            pid = entity.get("npc_id" if is_npc else "player_id")
            name = ("npc" if is_npc else ("bot" if pid in win._rc_bots else "ship")) + f"-{pid}"
            if not win._visible_to_local(entity, detection):
                name += "(unseen)"
            ships.append((name, dist(wx, wy), hull))

    gates = [(f"gate-{g.get('id')}", dist(float(g.get("x", 0)), float(g.get("y", 0))),
              max(max(8, int(g.get("radius", 40) * zoom * _WARP_VIS_SCALE)) + 8, 24))
             for g in layout.get("warp_gates", [])]
    planets_in = layout.get("planets", [])
    planets = []
    for pl in planets_in:
        wx, wy = win._planet_body_world_pos(pl, planets_in)
        planets.append((f"planet-{pl.get('id')}", dist(wx, wy), max(max(8, int(pl.get("radius", 80) * zoom)) + 8, 20)))
    stns = [(f"station-{s.get('station_id')}", dist(float(s.get("x", s.get("cx", 0))), float(s.get("y", s.get("cy", 0)))),
             max(max(8, int(s.get("radius", 36) * zoom)) + 20, 30)) for s in stations]
    for dep in deployed:
        pos = win._deployed_station_world_pos(dep)
        if pos is not None:
            hit = max(max(8, int(float(dep.get("station_radius", 60)) * zoom * 3.0)) + 12, 24)
            stns.append((f"pstation-{dep.get('attached_station_id')}", dist(*pos), hit))
    rocks = [(f"rock-{aid}", dist(*win._asteroid_world_pos(a)),
              max(max(5, int(a.get("radius", 30) * zoom * 3.0)) + 6, 18)) for aid, a in asteroids.items()]
    rocks_hit = sum(1 for _l, d, h in rocks if d <= h)
    crates = [(f"box-{cid}", dist(float(b["x"]), float(b["y"])), max(14, int(8 * zoom * 3.0)))
              for cid, b in boxes.items()]
    sun = layout.get("sun") or {}
    suns = []
    if sun:
        suns.append(("sun", dist(float(sun.get("x", 0)), float(sun.get("y", 0))),
                     max(max(8, int(sun.get("radius", 180) * zoom * 3.0)) + 12, 30)))
    nearest_rock = min(rocks, key=lambda r: r[1]) if rocks else None
    rock_text = "-" if nearest_rock is None else (
        f"{rocks_hit} in reach, nearest {nearest_rock[1]:.0f}/{nearest_rock[2]:.0f}"
        f"{'*' if nearest_rock[1] <= nearest_rock[2] else ''} of {len(rocks)}")
    return {"t": time.monotonic(), "pos": (mx, my), "zoom": zoom,
            "ships": _fmt(ships, SHIP_ASSIST_PX / 5.0), "gates": _fmt(gates), "planets": _fmt(planets),
            "stations": _fmt(stns), "asteroids": rock_text, "boxes": _fmt(crates), "sun": _fmt(suns)}


# -- hooks ---------------------------------------------------------------------------------------------

def _take_pending(pos) -> None:
    for index, (_t, where) in enumerate(_pending):
        if where == pos:
            del _pending[index]
            return


def _on_world_click(win, mx, my) -> None:
    _take_pending((int(mx), int(my)))
    _last["pick"] = None
    try:
        _last["snap"] = snapshot(win, int(mx), int(my))
    except Exception:
        _last["snap"] = None
        _fail("click snapshot")


def _report_swallowed(win) -> None:
    """Presses the event pump delivered that no world click handler saw this frame. Clicks that a
    visible panel, menu or the minimap took are expected and not reported."""
    pending = list(_pending)
    _pending.clear()
    for _t, (px, py) in pending:
        owner = None
        try:
            owner = win._gui_windows.route_pointer((px, py)).owner
        except Exception:
            pass
        overlays = [name for name in ("_esc_menu_open", "_options_open", "_menu_open", "_galaxy_map_open")
                    if getattr(win, name, False)]
        try:
            import galaxy_map_window
        except ImportError:                          # the Galaxy Map mod is not installed: the map is the game's own
            galaxy_map_window = None
        if "_galaxy_map_open" in overlays and galaxy_map_window is not None and galaxy_map_window.windowed(win):
            overlays.remove("_galaxy_map_open")     # the map window leaves the rest of the screen to the game
        if getattr(win, "_station_overlay", None) is not None:
            overlays.append("station overlay")
        mm_rect = getattr(win, "_mm_rect", None)
        if owner is not None or overlays or (mm_rect is not None and mm_rect.collidepoint((px, py))):
            continue
        logger.info("click_diag: click at (%d,%d) zoom=%.3f was not given to the world picker "
                    "and no panel, menu or minimap is under it", px, py, float(win._zoom))


def _log_decision(candidates, current, result) -> None:
    snap, pick = _last["snap"], _last["pick"]
    _last["snap"] = _last["pick"] = None
    hits = "[" + ", ".join(f"{slot}:{value}" for slot, value in candidates) + "]"
    if snap is None:
        logger.info("click_diag: previous=%s hits=%s -> %s (no geometry)", current, hits, result)
        return
    logger.info("click_diag: zoom=%.3f click=(%d,%d) previous=%s hits=%s -> %s | ship-pick=%s | ships: %s "
                "| gates: %s | planets: %s | stations: %s | asteroids: %s | boxes: %s | sun: %s",
                snap["zoom"], snap["pos"][0], snap["pos"][1], current, hits, result, pick, snap["ships"],
                snap["gates"], snap["planets"], snap["stations"], snap["asteroids"], snap["boxes"], snap["sun"])


def install(window_cls) -> bool:
    """Wrap the three window methods named above. Safe to call twice."""
    if window_cls.__dict__.get("_click_diag_installed", False):
        return False
    begin = window_cls._thr_begin_drag
    pick = window_cls._pick_ship_click
    step = window_cls._step_local_physics

    @functools.wraps(begin)
    def thr_begin_drag(self, mx, my):
        if _on():
            _on_world_click(self, mx, my)
        return begin(self, mx, my)

    @functools.wraps(pick)
    def pick_ship_click(self, click_pos, entities, world_to_screen):
        result = pick(self, click_pos, entities, world_to_screen)
        if _on():
            _last["pick"] = result
        return result

    @functools.wraps(step)
    def step_local_physics(self, dt):
        if _pending and _on():
            try:
                _report_swallowed(self)
            except Exception:
                _fail("swallowed-click report")
        return step(self, dt)

    window_cls._thr_begin_drag = thr_begin_drag
    window_cls._pick_ship_click = pick_ship_click
    window_cls._step_local_physics = step_local_physics
    window_cls._click_diag_installed = True
    return True


def install_picker(namespace) -> bool:
    """Wrap ``_cycle_overlapping_target`` (where every hit becomes the one target) in ``namespace``,
    the globals dict of Client.py. Pass ``globals()``, not ``sys.modules[__name__]``: the modded client
    is started by custom_build/run_client.py, which ``exec``s Client.py in a fresh dict as
    ``__main__`` while ``sys.modules['__main__']`` is run_client.py itself."""
    original = namespace["_cycle_overlapping_target"]
    if getattr(original, "_click_diag_wrapped", False):
        return False

    @functools.wraps(original)
    def cycle_overlapping_target(candidates, current):
        candidates = list(candidates)
        result = original(candidates, current)
        if _on():
            try:
                _log_decision(candidates, current, result)
            except Exception:
                _fail("decision log")
        return result

    cycle_overlapping_target._click_diag_wrapped = True
    namespace["_cycle_overlapping_target"] = cycle_overlapping_target
    return True

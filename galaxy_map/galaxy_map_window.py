"""galaxy_map_window.py - the galaxy map as a small window you can move and resize, that stays open while you fly.

The vanilla map is a full-screen modal: it dims the game, takes every click, and closes itself when you set an
autopilot route from it. This module adds a second form of the same map:

  * a button left of the close-X switches between FULL SCREEN and WINDOW (the choice is remembered, so M opens
    the map the way you last used it);
  * in the window the header is dragged to move it and any edge or corner is dragged to resize it (it never
    leaves the screen and has a minimum size), the game behind is not dimmed, clicks outside it go to the game
    as usual, and the keys that are meant for chat (Enter, /) work;
  * the window is for flying and finding systems, so it has the system search and Favorites but not the
    Coalitions / Territory / Survey tabs or VIEW SCANS (those stay full-screen only; the window always shows
    the coalition view and the full-screen map gets its own tab back);
  * setting an autopilot route from the window (right-click a system, or a Favorite) leaves it open, and with
    Auto-center ticked the map follows you as you jump from system to system.

The vanilla code is only touched where its geometry was written for a full-screen panel (render_mixin's
``_draw_galaxy_map`` and the header / footer helpers it calls, the zoom step, and a few "the map is open, so
ignore the game" tests in Client.py): each of those lines asks this module and is marked ``# MOD``. Everything
else is hooked from outside by ``install()`` (called once from Client.py): ``_draw_galaxy_map`` (per-frame
bookkeeping), ``_engage_route_autopilot_to`` (never closes a windowed map), and the window manager's
``queue_draw`` / ``route_pointer`` (a windowed map is a non-modal POPOVER that only owns the pointer inside its
rectangle, instead of a full-screen modal that owns it everywhere).

Every function the vanilla code calls here is total: an error switches the window feature off for the session
(the map goes back to the vanilla full-screen form) instead of taking the render loop down.
``STAR_EMPIRE_NO_MAP_WINDOW=1`` turns the feature off. The mode and the window's rectangle are kept in
``galaxy_map_window.json`` next to the client (git-ignored, written on the background writer thread).
"""

from __future__ import annotations

import json
import logging
import os

import modloader
import weakref

import pygame

from gui_window_manager import GuiLayer, PointerRoute
from scan_writer import submit
from window_geometry import (EDGE_B, EDGE_L, EDGE_R, EDGE_T, clamp_rect, draw_plate, moved_rect,  # noqa: F401
                             plate_chamfer, resize_zone, resized_rect)   # core: shared with ship_layouts_window

logger = logging.getLogger(__name__)

ENV_OFF = "STAR_EMPIRE_NO_MAP_WINDOW"
MAP_ID = "galaxy_map"


_MIN_W, _MIN_H = 580, 300        # UI pixels at scale 1; the favourites list needs the height
_GRAB, _GRAB_CORNER = 6, 16
_DEFAULT_SIZE = (0.40, 0.45)     # of the screen, when the window has never been placed

_failed = False


def _default_path() -> str:
    client_root = str(modloader.REPO_DIR)
    return os.path.join(client_root, "galaxy_map_window.json")


_path: str = _default_path()
_persisted: dict = {"windowed": False, "rect": None}


def _switch_off(where: str) -> None:
    global _failed
    if not _failed:
        _failed = True
        logger.exception("galaxy_map_window: switched off for this session (%s); the map is full-screen again",
                         where)


def _enabled() -> bool:
    return not _failed and not os.environ.get(ENV_OFF)


# ── persistence ──────────────────────────────────────────────────────────────

def load(path: "str | None" = None) -> dict:
    """(Re)read the settings file. Missing or malformed means "full screen, never placed"."""
    global _path, _persisted
    if path is not None:
        _path = path
    state = {"windowed": False, "rect": None}
    try:
        with open(_path, encoding="utf-8") as handle:
            stored = json.load(handle)
        if isinstance(stored, dict):
            state["windowed"] = stored.get("windowed") is True
            rect = stored.get("rect")
            if (isinstance(rect, list) and len(rect) == 4
                    and all(isinstance(v, int) and not isinstance(v, bool) for v in rect)):
                state["rect"] = [int(v) for v in rect]
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        logger.warning("galaxy_map_window: could not read %s; using defaults", _path)
    _persisted = state
    return dict(state)


def _write(path: str, state: dict) -> None:
    """Writer-thread side of _save: temp file + atomic replace."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        logger.exception("galaxy_map_window: could not save %s", path)


def _save(win) -> None:
    global _persisted
    rect = getattr(win, "_gm_win_rect", None)
    state = {"windowed": bool(getattr(win, "_gm_windowed", False)),
             "rect": [rect.x, rect.y, rect.w, rect.h] if rect is not None else _persisted.get("rect")}
    _persisted = state
    submit(_write, _path, dict(state))


def configure_for_tests(path: "str | None") -> None:
    """Point at another settings file (None restores the real one) and reload it."""
    global _path
    _path = _default_path() if path is None else path
    load()


# ── per-window state ─────────────────────────────────────────────────────────

def _ensure(win) -> None:
    """Give the window its map-window attributes (from the settings file) the first time they are needed."""
    if "_gm_win_ready" in win.__dict__:
        return
    win._gm_win_ready = True
    win._gm_windowed = bool(_persisted.get("windowed"))
    rect = _persisted.get("rect")
    win._gm_win_rect = pygame.Rect(*rect) if rect else None
    win._gm_win_drag = None
    win._gm_win_toggle_rect = None
    win._gm_win_saved_view = None
    win._gm_win_last_system = None
    win._gm_win_centre = None
    win._gm_win_chamfer = 0


def windowed(win) -> bool:
    """Is the galaxy map in its window form? (Total: never raises.)"""
    if not _enabled():
        return False
    try:
        _ensure(win)
        return bool(win._gm_windowed)
    except Exception:
        _switch_off("windowed()")
        return False


def modal_open(win) -> bool:
    """The map is open AND it is the full-screen modal (what the vanilla "map open, ignore the game" tests mean)."""
    try:
        return bool(win._galaxy_map_open) and not windowed(win)
    except Exception:
        return bool(getattr(win, "_galaxy_map_open", False))


def min_size(win) -> "tuple[int, int]":
    return win._s(_MIN_W), win._s(_MIN_H)


def _default_rect(win, screen_w: int, screen_h: int) -> pygame.Rect:
    pad = win._s(28)
    min_w, min_h = min_size(win)
    width = min(max(min_w, int(screen_w * _DEFAULT_SIZE[0])), max(1, screen_w - 2 * pad))
    height = min(max(min_h, int(screen_h * _DEFAULT_SIZE[1])), max(1, screen_h - 2 * pad))
    return pygame.Rect(screen_w - pad - width, pad, width, height)


def window_rect(win, screen_w: int, screen_h: int) -> pygame.Rect:
    """The window's rectangle on a screen of this size (placed on first use, always on screen)."""
    _ensure(win)
    rect = win._gm_win_rect
    if rect is None:
        rect = _default_rect(win, screen_w, screen_h)
    min_w, min_h = min_size(win)
    rect = clamp_rect(rect, screen_w, screen_h, min_w, min_h)
    win._gm_win_rect = rect
    return rect


def panel_geometry(win, win_w: int, win_h: int, pad: int) -> "tuple[int, int, int, int]":
    """(x, y, w, h) of the map panel: the full-screen margin box, or the window. Total."""
    full = (pad, pad, win_w - pad * 2, win_h - pad * 2)
    if not windowed(win):
        return full
    try:
        rect = window_rect(win, win_w, win_h)
        return rect.x, rect.y, rect.w, rect.h
    except Exception:
        _switch_off("panel_geometry()")
        return full


def origin(win, pad: int) -> "tuple[int, int]":
    """The panel's top-left corner: the full-screen margin, or the window's corner (the header and footer
    helpers draw from it; the panel rectangle is published by the draw just before they run)."""
    if windowed(win):
        rect = getattr(win, "_galaxy_map_panel_rect", None)
        if rect is not None:
            return rect.x, rect.y
    return pad, pad


def fit_rows(win, items, top: int, row_h: int):
    """The rows of a dropdown that fit between *top* and the bottom of the screen. A dropdown opened from the
    small window can reach below the window (it is still the map's, see _MapHitArea) but never off the screen;
    the full-screen map keeps every row, as before."""
    if not windowed(win):
        return items
    try:
        return items[:max(1, (_bounds(win)[1] - int(top)) // max(1, int(row_h)))]
    except Exception:
        _switch_off("fit_rows()")
        return items


class _WindowPlate(dict):
    """The plate of a window: its chamfer at once, the baked picture only if somebody asks for it. The GPU
    draws the window's plate with primitives (``draw_plate``), because a window that is being resized has a
    new size every frame and a baked plate per size would fill the texture atlas (and push the minimap's and
    ship vitals' plates out of the shared cache); only the software path (the tests) blits the picture."""

    def __init__(self, w, h, chrome_h, chamfer_max):
        super().__init__(chamfer=plate_chamfer(h, chamfer_max))
        self._args = (w, h, chrome_h, chamfer_max)

    def __missing__(self, key):
        if key != "plate":
            raise KeyError(key)
        from render_cache import build_hud_panel_plate
        w, h, chrome_h, chamfer_max = self._args
        self[key] = build_hud_panel_plate(w, h, chrome_h, chamfer_max=chamfer_max)["plate"]
        return self[key]


def plate_layers(win, w: int, h: int, chrome_h: int, chamfer_max: int):
    """The panel plate: the shared baked one full screen, a primitive-drawn one in the window."""
    if not windowed(win):
        return win._hud_plate_layers(w, h, chrome_h, chamfer_max=chamfer_max)
    layers = _WindowPlate(w, h, chrome_h, chamfer_max)
    win._gm_win_chamfer = layers["chamfer"]
    return layers


def view_centre(win, win_w: int, win_h: int, canvas_rect) -> "tuple[int, int]":
    """The point the pan offset is measured from: the screen centre, or the middle of the window's map area."""
    if not windowed(win):
        win._gm_win_centre = None
        return win_w // 2, win_h // 2
    win._gm_win_centre = (int(canvas_rect.centerx), int(canvas_rect.centery))
    return win._gm_win_centre


def zoom_centre(win, screen) -> "tuple[int, int]":
    """The pan offset's origin for the zoom step (same point as view_centre)."""
    centre = getattr(win, "_gm_win_centre", None) if windowed(win) else None
    if centre is not None:
        return centre
    width, height = screen.get_size()
    return width // 2, height // 2


def keys_zoom_map(win) -> bool:
    """+ / - zoom the map (rather than the game view) when it is the full-screen map, or the window with the
    pointer over it."""
    try:
        if not windowed(win):
            return bool(win._galaxy_map_open)
        rect = getattr(win, "_galaxy_map_panel_rect", None)
        return bool(win._galaxy_map_open and rect is not None and rect.collidepoint(pygame.mouse.get_pos()))
    except Exception:
        return bool(getattr(win, "_galaxy_map_open", False))


# ── mode switch ──────────────────────────────────────────────────────────────

def _clear_popups(win) -> None:
    win._galaxy_map_info_system = None
    win._galaxy_map_info_rect = None
    win._galaxy_map_info_favorite_rect = None
    win._galaxy_map_csw_info_quarter = None
    win._galaxy_map_csw_info_anchor = None
    win._galaxy_map_csw_info_rect = None
    win._gm_favorites_open = False
    win._gm_favorite_item_rects = []
    win._gm_favorite_remove_rects = []
    win._gm_favorites_dropdown_rect = None
    win._gm_reset_search()


def _sync_view(win) -> None:
    """The window always shows the coalition view; the full-screen map gets the last tab back."""
    if win._gm_windowed:
        view = getattr(win, "_galaxy_map_view", "coalitions")
        if view != "coalitions":
            if win._gm_win_saved_view is None:
                win._gm_win_saved_view = view
            win._galaxy_map_view = "coalitions"
            win._gm_survey_selected = set()
            win._galaxy_map_csw_info_quarter = None
            win._galaxy_map_csw_info_anchor = None
            win._galaxy_map_csw_info_rect = None
    elif win._gm_win_saved_view is not None:
        win._galaxy_map_view = win._gm_win_saved_view
        win._gm_win_saved_view = None


def set_windowed(win, on: bool) -> None:
    """Switch the open map between its window and full-screen forms (remembered)."""
    _ensure(win)
    on = bool(on)
    if on == bool(win._gm_windowed):
        return
    win._gm_windowed = on
    win._gm_win_drag = None
    _sync_view(win)
    _clear_popups(win)
    # The map area changes size and place: with Auto-center on, put the player in the middle of it again.
    win._galaxy_map_apply_autocenter()
    _save(win)


def toggle_windowed(win) -> None:
    set_windowed(win, not windowed(win))


# ── header / footer pieces (called from render_mixin) ───────────────────────

def _palette(render_module) -> dict:
    def pick(name, default):
        return tuple(getattr(render_module, name, default))[:3]

    return {"value": pick("_GM_VALUE", (228, 237, 250)), "steel": pick("_GM_STEEL", (56, 88, 138)),
            "accent": pick("_GM_ACCENT", (95, 170, 255)), "plate": pick("_GM_PLATE_BG", (7, 11, 20)),
            "header": pick("_GM_HEADER_BG", (13, 21, 38)), "caption": pick("_GM_CAPTION", (96, 128, 176)),
            "text": pick("_GM_TEXT", (185, 215, 250))}


_colours: dict = _palette(None)


def _icon(win, to_window: bool, hover: bool, w: int, h: int):
    """The toggle button's picture: two stacked frames (go to the window) or one big frame (go full screen)."""
    cache = win.__dict__.setdefault("_gm_win_icons", {})
    key = (to_window, hover, w, h)
    surf = cache.get(key)
    if surf is None:
        colour = _colours["value"] if hover else _colours["text"]
        iw, ih = max(6, w - win._s(8)), max(6, h - win._s(8))
        surf = pygame.Surface((iw, ih), pygame.SRCALPHA)
        if to_window:
            fw, fh = max(3, iw * 2 // 3), max(3, ih * 2 // 3)
            pygame.draw.rect(surf, colour, (iw - fw, 0, fw, fh), 1)
            pygame.draw.rect(surf, (*_colours["plate"], 255), (0, ih - fh, fw, fh))
            pygame.draw.rect(surf, colour, (0, ih - fh, fw, fh), 1)
        else:
            pygame.draw.rect(surf, colour, (0, 0, iw, ih), 1)
            pygame.draw.line(surf, colour, (1, 1), (iw - 2, 1))
        cache[key] = surf
    return surf


def draw_toggle(win, ov, font, close_r) -> int:
    """Draw the window / full-screen button left of the close-X; returns the left edge of the header's
    right-hand controls (the button's, or the close-X's when the feature is off)."""
    if not _enabled():
        win._gm_win_toggle_rect = None
        return close_r.x
    try:
        _ensure(win)
        now_windowed = bool(win._gm_windowed)
        gap = win._s(5)
        rect = pygame.Rect(close_r.x - gap - close_r.w, close_r.y, close_r.w, close_r.h)
        win._gm_win_toggle_rect = rect
        hover = rect.collidepoint(pygame.mouse.get_pos())
        fill = (14, 29, 43) if hover else _colours["plate"]
        border = _colours["accent"] if hover else _colours["steel"]
        key = ("__gmwintoggle__", now_windowed, hover, rect.w, rect.h, win._uk)
        button = win._gm_chrome_button(rect.w, rect.h, _icon(win, not now_windowed, hover, rect.w, rect.h),
                                       fill, border, 1, 0, False, key)
        win._floor_blit(button, key, rect.x, rect.y, batch=ov)
        if hover:
            text = "WINDOW" if not now_windowed else "FULL SCREEN"
            label_key = ("__gmwintip__", text, win._uk)
            label = win._gm_text(font, text, _colours["value"], label_key)
            tw, th = label.get_size()
            pad = win._s(4)
            plate = win._cached_fill(tw + pad * 2, th + pad, (5, 9, 18, 236))
            x = max(0, rect.right - tw - pad * 2)
            y = rect.bottom + win._s(3)
            win._floor_blit(plate, ("__gmwintipplate__", tw, th, win._uk), x, y, batch=ov)
            win._floor_blit(label, label_key, x + pad, y + pad // 2, batch=ov)
        return rect.x
    except Exception:
        _switch_off("draw_toggle()")
        return close_r.x


def draw_chrome(win, ov) -> None:
    """The window's resize affordances: a grip in the bottom-right corner, and the edges the pointer would drag."""
    if not windowed(win):
        return
    try:
        rect = getattr(win, "_galaxy_map_panel_rect", None)
        if rect is None:
            return
        drag = win._gm_win_drag
        if drag is not None and drag["kind"] == "resize":
            edges = drag["edges"]
        elif drag is None:
            edges = resize_zone(rect, pygame.mouse.get_pos(), win._s(_GRAB), win._s(_GRAB_CORNER))
        else:
            edges = 0
        accent = (*_colours["accent"], 210)
        top_cut = win._gm_win_chamfer
        bottom_cut = max(4, top_cut // 2)
        thick = max(2, win._s(2))
        if edges & EDGE_L:
            _line(win, ov, rect.x, rect.y, thick, rect.h, accent)
        if edges & EDGE_R:
            _line(win, ov, rect.right - thick, rect.y + top_cut, thick, rect.h - top_cut - bottom_cut, accent)
        if edges & EDGE_T:
            _line(win, ov, rect.x, rect.y, rect.w - top_cut, thick, accent)
        if edges & EDGE_B:
            _line(win, ov, rect.x, rect.bottom - thick, rect.w - bottom_cut, thick, accent)
        grip = _grip(win, bottom_cut)
        win._floor_blit(grip, ("__gmwingrip__", grip.get_size(), win._uk),
                        rect.right - grip.get_width(), rect.bottom - grip.get_height(), batch=ov)
    except Exception:
        _switch_off("draw_chrome()")


def _line(win, ov, x, y, w, h, colour) -> None:
    if w > 0 and h > 0:
        win._floor_blit(win._cached_fill(int(w), int(h), colour), ("__gmwinedge__", int(w), int(h), colour),
                        x, y, batch=ov)


def _grip(win, cut: int):
    cache = win.__dict__.setdefault("_gm_win_icons", {})
    key = ("grip", cut, win._uk)
    surf = cache.get(key)
    if surf is None:
        size = cut + win._s(18)
        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        colour = (*_colours["steel"], 255)
        for k in range(3):
            off = cut + win._s(4) + win._s(4) * k
            pygame.draw.line(surf, colour, (size - off, size - 3), (size - 3, size - off))
        cache[key] = surf
    return surf


# ── input ────────────────────────────────────────────────────────────────────

def _bounds(win) -> "tuple[int, int]":
    surface = pygame.display.get_surface()
    if surface is not None:
        return surface.get_size()
    rect = getattr(win, "_galaxy_map_panel_rect", None)
    return (rect.right, rect.bottom) if rect is not None else (1280, 720)


def _header_controls(win):
    for name in ("_galaxy_map_close_rect", "_gm_win_toggle_rect", "_gm_search_rect", "_gm_favorites_btn_rect"):
        rect = getattr(win, name, None)
        if rect is not None:
            yield rect


def _press(win, event) -> bool:
    """A left press. True when it was the window / full-screen button or started a move or resize (the press
    is consumed)."""
    rect = getattr(win, "_galaxy_map_panel_rect", None)
    if rect is None:
        return False
    pos = event.pos
    owned = win._gui_windows.route_pointer(pos).owner == MAP_ID
    toggle = win._gm_win_toggle_rect
    if owned and toggle is not None and toggle.collidepoint(pos):
        toggle_windowed(win)
        return True
    if not win._gm_windowed:
        return False
    if not owned:
        # The window does not take this press, so the game (or whatever is in front of the window) does: a
        # search box left focused would keep eating the flight keys, and an open Favorites list would stay
        # hanging over nothing.
        if win._gm_search_focused or win._gm_search_text or win._gm_favorites_open:
            _clear_popups(win)
        return False
    edges = resize_zone(rect, pos, win._s(_GRAB), win._s(_GRAB_CORNER))
    if edges:
        kind = "resize"
    elif (pos[1] - rect.top < win._s(46)
          and not any(control.collidepoint(pos) for control in _header_controls(win))):
        kind = "move"
    else:
        return False
    win._gm_win_drag = {"kind": kind, "edges": edges, "start": pygame.Rect(rect), "origin": tuple(pos)}
    # A press that begins a drag is not a click on the map: drop what the map may have been doing.
    win._galaxy_map_dragging = False
    return True


def _drag(win, pos) -> None:
    drag = win._gm_win_drag
    width, height = _bounds(win)
    dx, dy = pos[0] - drag["origin"][0], pos[1] - drag["origin"][1]
    if drag["kind"] == "move":
        rect = moved_rect(drag["start"], dx, dy, width, height)
    else:
        min_w, min_h = min_size(win)
        rect = resized_rect(drag["start"], drag["edges"], dx, dy, width, height, min_w, min_h)
    win._gm_win_rect = rect
    win._galaxy_map_panel_rect = pygame.Rect(rect)     # the pointer route follows the window at once


def _finish(win) -> None:
    win._gm_win_drag = None
    _save(win)


def _handle(win, event) -> bool:
    kind = event.type
    drag = win._gm_win_drag
    if kind == pygame.MOUSEBUTTONDOWN:
        return event.button == 1 and drag is None and _press(win, event)
    if drag is None:
        return False
    if kind == pygame.MOUSEMOTION:
        if not event.buttons[0]:                       # the release happened where we could not see it
            _finish(win)
            return False
        _drag(win, event.pos)
        return True
    if kind == pygame.MOUSEBUTTONUP and event.button == 1:
        _drag(win, event.pos)
        _finish(win)
        return True
    return False


def filter_events(win, events):
    """Called once per frame with the frame's events (Client.py). Takes the presses, moves and releases that
    move or resize the map window out of the list, so the game's own handlers never see them. Total."""
    if not _enabled():
        return events
    try:
        _ensure(win)
        manager = getattr(win, "_gui_windows", None)
        if manager is not None:
            manager._gm_owner = weakref.ref(win)
        if not win._galaxy_map_open:
            win._gm_win_drag = None
            return events
        kept = [event for event in events if not _handle(win, event)]
        return kept if len(kept) != len(events) else events
    except Exception:
        _switch_off("filter_events()")
        return events


# ── hooks installed from outside ─────────────────────────────────────────────

class _MapHitArea:
    """Where the windowed map owns the pointer: its panel, plus the dropdowns (search results, Favorites) that
    hang below it, which can reach outside the panel on a small window. Exposes ``collidepoint`` like a Rect."""

    def __init__(self, win):
        self._win = win

    def _rects(self):
        win = self._win
        panel = getattr(win, "_galaxy_map_panel_rect", None)
        if panel is None:
            return
        yield panel
        favourites = getattr(win, "_gm_favorites_dropdown_rect", None)
        if favourites is not None:
            yield favourites
        for rect, _name in getattr(win, "_gm_search_item_rects", None) or ():
            yield rect

    def collidepoint(self, pos) -> bool:
        return any(rect.collidepoint(pos) for rect in self._rects())


class _OutsideRoute(PointerRoute):
    """A route for a pointer that is not over the windowed map: the map must not take it, even though nothing
    else claimed it either (``allows`` is otherwise true for every id when the route is not blocked)."""

    def allows(self, window_id) -> bool:
        return False if window_id == MAP_ID else PointerRoute.allows(self, window_id)


def _windowed_map_registered(manager) -> bool:
    window = manager._windows.get(MAP_ID)
    return window is not None and not window.modal and manager._is_visible(window)


def _install_manager(manager_cls) -> bool:
    if manager_cls.__dict__.get("_gm_window_installed"):
        return False
    queue_draw = manager_cls.queue_draw
    route_pointer = manager_cls.route_pointer

    def owner_of(manager):
        ref = getattr(manager, "_gm_owner", None)
        return ref() if ref is not None else None

    def patched_queue_draw(self, window_id, draw, rect_getter, **kwargs):
        if window_id == MAP_ID and _enabled():
            try:
                owner = owner_of(self)
                if owner is not None and windowed(owner):
                    kwargs.update(layer=GuiLayer.POPOVER, modal=False, captures_outside=False, focusable=False)
                    area = _MapHitArea(owner)
                    rect_getter = lambda area=area, owner=owner: (
                        area if getattr(owner, "_galaxy_map_panel_rect", None) is not None else None)
            except Exception:
                _switch_off("queue_draw()")
        return queue_draw(self, window_id, draw, rect_getter, **kwargs)

    def patched_route_pointer(self, pos, *, focus: bool = False):
        route = route_pointer(self, pos, focus=focus)
        if not _enabled() or route.owner == MAP_ID:
            return route
        try:
            if _windowed_map_registered(self):
                owner = owner_of(self)
                if (owner is not None and not route.modal
                        and getattr(owner, "_galaxy_map_dragging", False)):
                    # Panning: the pointer stays with the map even when it strays outside the window.
                    return PointerRoute(MAP_ID, True, False, False)
                return _OutsideRoute(route.owner, route.blocked, route.inside_owner, route.modal)
        except Exception:
            _switch_off("route_pointer()")
        return route

    patched_queue_draw.__wrapped__ = queue_draw
    patched_route_pointer.__wrapped__ = route_pointer
    manager_cls.queue_draw = patched_queue_draw
    manager_cls.route_pointer = patched_route_pointer
    manager_cls._gm_window_installed = True
    return True


def _frame(win) -> None:
    """Bookkeeping before the map is drawn."""
    _ensure(win)
    _sync_view(win)
    if not win._gm_windowed:
        win._gm_win_last_system = None
        return
    # Follow the ship from system to system while Auto-center is on (a window left open during an autopilot
    # route would otherwise be showing the system you left).
    with win._lock:
        layout = win._layout
    system = layout.get("system_name") if layout else None
    previous = win._gm_win_last_system
    win._gm_win_last_system = system
    if (previous is not None and system is not None and system != previous
            and win._galaxy_map_autocenter and not win._galaxy_map_dragging and win._gm_win_drag is None):
        win._galaxy_map_recenter_pending = True


def install(window_cls, render_module=None, manager_cls=None) -> bool:
    """Wrap ``_draw_galaxy_map`` and ``_engage_route_autopilot_to`` (Client.py's SolarSystemWindow) and the
    window manager's ``queue_draw`` / ``route_pointer``. Idempotent; True if hooked."""
    global _colours
    if window_cls.__dict__.get("_galaxy_map_window_installed"):
        return False
    if manager_cls is None:
        from gui_window_manager import GuiWindowManager as manager_cls
    _colours = _palette(render_module)
    load()
    _install_manager(manager_cls)

    draw = window_cls._draw_galaxy_map
    engage = window_cls._engage_route_autopilot_to

    def _draw_galaxy_map(self, screen):
        if _enabled():
            try:
                _frame(self)
            except Exception:
                _switch_off("per-frame bookkeeping")
        return draw(self, screen)

    def _engage_route_autopilot_to(self, dest_system, *args, **kwargs):
        # A route set from the window must not close it (the full-screen map closes itself on a success).
        if kwargs.get("close_map") and getattr(self, "_galaxy_map_open", False) and windowed(self):
            kwargs["close_map"] = False
        return engage(self, dest_system, *args, **kwargs)

    for new, old in ((_draw_galaxy_map, draw), (_engage_route_autopilot_to, engage)):
        new.__wrapped__ = old
        new.__doc__ = old.__doc__
    window_cls._draw_galaxy_map = _draw_galaxy_map
    window_cls._engage_route_autopilot_to = _engage_route_autopilot_to
    window_cls._galaxy_map_window_installed = True
    return True


def reset_for_tests() -> None:
    global _failed, _persisted
    _failed = False
    _persisted = {"windowed": False, "rect": None}

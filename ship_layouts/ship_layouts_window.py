"""ship_layouts_window.py - the SHIP LAYOUTS window: save the ship you are flying under a name, and see how any
ship's current fit differs from a saved one (ship_layouts.py is the model; this is the window).

Open it from the HUD menu (SHIP LAYOUTS) or with ``/layouts``. Left: your saved layouts and the buttons
SAVE CURRENT SHIP / UPDATE / RENAME / DELETE. Right: the comparison of the selected layout against the ship you
are flying right now, live (swap a module and its row turns green). Only what differs is listed (SHOW MATCHING
lists the rest); the stats table shows saved, now and the change, green when it is better, red when worse.

A save asks the server for fresh Ship Specs and waits for the answer, so the stats are the ones the server
computes for the setup you are looking at (the specs are what the Stats > Ship Specs tab shows). The turret
rack and the plugins come from data the client already holds.

Hooked in from outside the vanilla code (``install`` is called once from Client.py): ``set_ship_specs_data`` is
wrapped to count the answers, and the flight-key text gate is wrapped so typing a layout name never flies the
ship. The vanilla lines that open the window (the HUD menu row, ``/layouts``), draw it (one queue call before the
retained windows are flushed) and feed it events (one filter call after the event pump) are marked ``# MOD``.
Every function the vanilla code calls is total: an error switches the window off for the session.
``STAR_EMPIRE_NO_SHIP_LAYOUTS=1`` turns the feature off.
"""

from __future__ import annotations

import json
import logging
import os

import modloader
import time
import weakref

import pygame

import window_geometry as wg
import ship_layouts
from gui_window_manager import GuiLayer
from scan_writer import submit

logger = logging.getLogger(__name__)

ID = "ship_layouts"
FIELD_KEY = "ship_layouts_name"   # the name box's TextField in the window's text_field_host registry (v0.5.73: every
                                  # client text box is a TextField - test_text_box_guard)
ENV_OFF = "STAR_EMPIRE_NO_SHIP_LAYOUTS"
STORE = ship_layouts.LayoutStore()

_MIN_W, _MIN_H = 600, 400
_DEFAULT_W, _DEFAULT_H = 820, 560
SPECS_POLL_S = 2.0            # while the window is open and a layout is selected (the Stats tab polls every 1 s)
SPECS_WAIT_S = 3.0            # how long a save waits for fresh Ship Specs before saving without them
REFRESH_S = 15.0              # re-read the folder now and then: another client may have saved a layout
CONFIRM_S = 3.0               # how long a first DELETE click stays armed
STATUS_S = 6.0

_failed = False
_windows: list = []            # weak references to every window that has used the feature


def _default_path() -> str:
    client_root = str(modloader.REPO_DIR)
    return os.path.join(client_root, "ship_layouts_window.json")


_path: str = _default_path()
_persisted: dict = {"rect": None, "selected": None, "show_matching": False}


def _switch_off(where: str) -> None:
    global _failed
    if not _failed:
        _failed = True
        logger.exception("ship_layouts_window: switched off for this session (%s)", where)


def _enabled() -> bool:
    return not _failed and not os.environ.get(ENV_OFF)


# ── settings (window rectangle, last selection) ──────────────────────────────

def load(path: "str | None" = None) -> dict:
    global _path, _persisted
    if path is not None:
        _path = path
    state = {"rect": None, "selected": None, "show_matching": False}
    try:
        with open(_path, encoding="utf-8") as handle:
            stored = json.load(handle)
        if isinstance(stored, dict):
            rect = stored.get("rect")
            if (isinstance(rect, list) and len(rect) == 4
                    and all(isinstance(v, int) and not isinstance(v, bool) for v in rect)):
                state["rect"] = list(rect)
            if isinstance(stored.get("selected"), str):
                state["selected"] = stored["selected"]
            state["show_matching"] = stored.get("show_matching") is True
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        logger.warning("ship_layouts_window: could not read %s; using defaults", _path)
    _persisted = state
    return dict(state)


def _write(path: str, state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        logger.exception("ship_layouts_window: could not save %s", path)


def _save_settings(win) -> None:
    global _persisted
    rect = win._sl_rect
    _persisted = {"rect": [rect.x, rect.y, rect.w, rect.h] if rect is not None else _persisted.get("rect"),
                  "selected": win._sl_selected, "show_matching": bool(win._sl_show_matching)}
    submit(_write, _path, dict(_persisted))


def configure_for_tests(path: "str | None") -> None:
    global _path
    _path = _default_path() if path is None else path
    load()


def reset_for_tests() -> None:
    global _failed, _persisted
    _failed = False
    _persisted = {"rect": None, "selected": None, "show_matching": False}
    _windows.clear()                # a window an earlier test left typing must not hold the flight keys


# ── window state ─────────────────────────────────────────────────────────────

def _ensure(win) -> None:
    if "_sl_ready" in win.__dict__:
        return
    win._sl_ready = True
    _windows.append(weakref.ref(win))
    win._sl_open = False
    win._sl_store = STORE
    rect = _persisted.get("rect")
    win._sl_rect = pygame.Rect(*rect) if rect else None
    win._sl_selected = _persisted.get("selected")
    win._sl_show_matching = bool(_persisted.get("show_matching"))
    win._sl_cargo_open = False
    win._sl_scroll_list = 0
    win._sl_scroll_cmp = 0
    win._sl_input = None            # {"mode": "save"|"rename", "text": str, "target": id | None}; caret/selection live in
                                    # the box's TextField (FIELD_KEY, owned by this dict)
    win._sl_pending = None          # a save waiting for fresh Ship Specs
    win._sl_confirm_delete = None   # (layout id, monotonic deadline)
    win._sl_status = None           # (text, kind, monotonic deadline)
    win._sl_drag = None
    win._sl_hits = {}
    win._sl_diff_cache = None
    win._sl_specs_count = 0
    win._sl_current_cache = None
    win._sl_last_poll = 0.0
    win._sl_last_refresh = 0.0
    win._sl_max_scroll_list = 0
    win._sl_max_scroll_cmp = 0


def is_open(win) -> bool:
    if not _enabled():
        return False
    try:
        _ensure(win)
        return bool(win._sl_open)
    except Exception:
        _switch_off("is_open()")
        return False


def text_focused(win) -> bool:
    """Is a layout name being typed? (Keeps the flight keys quiet while it is.)"""
    return bool(is_open(win) and win._sl_input is not None)


def any_text_focused() -> bool:
    return any(text_focused(w) for w in (ref() for ref in list(_windows)) if w is not None)


def open_window(win) -> None:
    _ensure(win)
    win._sl_open = True
    now = time.monotonic()
    win._sl_last_refresh = now
    win._sl_store.refresh()
    _request_specs(win, now)
    _auto_select(win)
    _manager_owner(win)


def close_window(win) -> None:
    _ensure(win)
    win._sl_open = False
    win._sl_input = None
    win._sl_drag = None
    win._sl_confirm_delete = None
    _save_settings(win)


def toggle(win) -> None:
    if is_open(win):
        close_window(win)
    else:
        open_window(win)


def _manager_owner(win) -> None:
    manager = getattr(win, "_gui_windows", None)
    if manager is not None:
        manager._sl_owner = weakref.ref(win)


def _request_specs(win, now: float) -> None:
    win._sl_last_poll = now
    try:
        win._send_fn("SHIP_SPECS")
    except Exception:
        logger.debug("ship_layouts_window: could not ask for ship specs", exc_info=True)


def _say(win, text: str, kind: str = "info") -> None:
    win._sl_status = (text, kind, time.monotonic() + STATUS_S)


def _auto_select(win) -> None:
    """Keep the selection if it still exists; else the newest layout saved on this ship; else the newest."""
    store = win._sl_store
    if win._sl_selected and store.get(win._sl_selected):
        return
    layouts = store.layouts
    if not layouts:
        win._sl_selected = None
        return
    ship_type = (current_cached(win).get("ship") or {}).get("ship_type")
    same = [d for d in layouts if ship_type and (d.get("ship") or {}).get("ship_type") == ship_type]
    win._sl_selected = (same or list(layouts))[0]["id"]


# ── the live data ────────────────────────────────────────────────────────────

def _live(win) -> tuple:
    with win._lock:
        inventory = list(getattr(win, "_inventory", None) or ())
        plugin_data = getattr(win, "_ship_plugin_data", None)
        turret_state = getattr(win, "_turret_state", None)
        specs = getattr(win, "_ship_specs_data", None)
    return inventory, plugin_data, turret_state, specs


def _entity_ship(win) -> dict:
    try:
        entity = win._find_my_entity()
    except Exception:
        entity = None
    if not entity or not entity.get("ship_type"):
        return {}
    return {"ship_type": str(entity["ship_type"]), "display_name": str(entity.get("ship_display_name") or ""),
            "classification": ""}


def current_layout(win, name: str = "") -> dict:
    """The ship being flown, in layout form (what ``compare`` takes as "now")."""
    inventory, plugin_data, turret_state, specs = _live(win)
    layout = ship_layouts.capture(inventory, plugin_data, turret_state, specs, name, now=time.time(),
                                  username=getattr(win, "_username", ""))
    if not layout["ship"].get("ship_type"):
        layout["ship"] = _entity_ship(win)
    return layout


def _live_key(win) -> tuple:
    return (getattr(win, "_inventory_version", 0), getattr(win, "_ship_plugin_version", 0),
            id(getattr(win, "_turret_state", None)), win._sl_specs_count)


def current_cached(win) -> dict:
    """``current_layout`` again only when the inventory, plugins, turrets or specs changed."""
    key = _live_key(win)
    cache = win._sl_current_cache
    if cache is not None and cache[0] == key:
        return cache[1]
    layout = current_layout(win)
    win._sl_current_cache = (key, layout)
    return layout


def current_diff(win):
    """``(saved layout, diff)`` for the selection, rebuilt only when something it depends on changed."""
    saved = win._sl_store.get(win._sl_selected)
    if saved is None:
        return None, None
    key = _live_key(win) + (saved["id"], saved["saved_at"], saved["name"])
    cache = win._sl_diff_cache
    if cache is not None and cache[0] == key:
        return saved, cache[1]
    diff = ship_layouts.compare(saved, current_cached(win))
    win._sl_diff_cache = (key, diff)
    return saved, diff


# ── save / update / rename / delete ─────────────────────────────────────────

def begin_input(win, mode: str) -> None:
    """Open the name field for a new layout (``save``) or a new name for the selected one (``rename``)."""
    target = win._sl_selected if mode == "rename" else None
    if mode == "rename" and not win._sl_store.get(target):
        return
    if mode == "rename":
        text = win._sl_store.get(target)["name"]
    else:
        ship = current_cached(win)["ship"].get("display_name") or "Ship"
        text = f"{ship} layout"
    text = text[:ship_layouts.MAX_NAME_LEN]
    box = {"mode": mode, "text": text, "target": target}
    win._sl_input = box
    win._text_cursor_to_end(FIELD_KEY, text, owner=box)   # a fresh TextField per opening, caret after the text
    win._sl_confirm_delete = None


def cancel_input(win) -> None:
    win._sl_input = None


def commit_input(win) -> None:
    box = win._sl_input
    if box is None:
        return
    name = ship_layouts.clean_name(box["text"])
    if not name:
        _say(win, "A layout needs a name.", "warn")
        return
    win._sl_input = None
    if box["mode"] == "rename":
        saved = win._sl_store.get(box["target"])
        if saved is None:
            return
        others = [d for d in win._sl_store.layouts if d["id"] != saved["id"]]
        taken = {d["name"].lower() for d in others}
        final = name if name.lower() not in taken else win._sl_store.unique_name(name)
        win._sl_store.save({**saved, "name": final})
        _say(win, f"Renamed to '{final}'.", "ok")
        return
    begin_save(win, win._sl_store.unique_name(name))


def begin_save(win, name: str, *, target_id: "str | None" = None) -> None:
    """Ask for fresh Ship Specs; ``tick`` finishes the save when they arrive (or after SPECS_WAIT_S)."""
    now = time.monotonic()
    win._sl_pending = {"name": name, "target": target_id, "since": now, "count": win._sl_specs_count}
    _request_specs(win, now)


def begin_update(win) -> None:
    saved = win._sl_store.get(win._sl_selected)
    if saved is None or win._sl_pending is not None:
        return
    begin_save(win, saved["name"], target_id=saved["id"])


def _finish_save(win, fresh: bool) -> None:
    pending, win._sl_pending = win._sl_pending, None
    if pending is None:
        return
    previous = win._sl_store.get(pending["target"]) if pending["target"] else None
    layout = current_layout(win, pending["name"])
    if previous is not None:                      # an update keeps its identity (and so its place in the list)
        layout["id"] = previous["id"]
    win._sl_store.save(layout)
    win._sl_selected = layout["id"]
    win._sl_diff_cache = None
    verb = "Updated" if previous is not None else "Saved"
    _log_readings(win, layout)
    if fresh and layout.get("specs_state") == "docked":
        _say(win, f"{verb} '{layout['name']}' while docked: the server reports a lighter ship then, so "
                  f"acceleration and turn rate read high. Save in space for flight figures.", "warn")
    elif fresh:
        _say(win, f"{verb} '{layout['name']}'.", "ok")
    else:
        _say(win, f"{verb} '{layout['name']}' without fresh stats (the server did not answer).", "warn")
    _save_settings(win)


def _log_readings(win, layout: dict) -> None:
    """One log line with the hull / mass figures from both sources, so a docked-vs-space difference can be read
    off the log later (the specs and the inventory summary have disagreed docked)."""
    try:
        inventory, _plugins, _turrets, specs = _live(win)
        ship = (specs or {}).get("ship") or {}
        summary = ship_layouts.extract_cargo_summary(inventory)
        logger.info("ship_layouts_window: saved %r (%s): specs cargo %s/%s gear %s hold %s, inventory summary "
                    "%s/%s gear %s hold %s, effective mass %s, accel %s, turn rate %s",
                    layout["name"], layout.get("specs_state") or "no specs", ship.get("cargo_used"),
                    ship.get("cargo_capacity"), ship.get("equipped_cargo_used"), ship.get("hold_cargo_used"),
                    summary.get("cargo_used"), summary.get("cargo_capacity"), summary.get("equipped_cargo_used"),
                    summary.get("hold_cargo_used"), ship.get("effective_mass"),
                    ((specs or {}).get("engine") or {}).get("accel"),
                    ((specs or {}).get("engine") or {}).get("turn_rate"))
    except Exception:
        logger.debug("ship_layouts_window: could not log the readings", exc_info=True)


def delete_selected(win) -> None:
    """The first click arms the button, a second one within CONFIRM_S deletes."""
    saved = win._sl_store.get(win._sl_selected)
    if saved is None:
        return
    now = time.monotonic()
    armed = win._sl_confirm_delete
    if armed is not None and armed[0] == saved["id"] and now <= armed[1]:
        win._sl_confirm_delete = None
        win._sl_store.delete(saved["id"])
        win._sl_selected = None
        _auto_select(win)
        win._sl_diff_cache = None
        _say(win, f"Deleted '{saved['name']}'.", "ok")
        _save_settings(win)
    else:
        win._sl_confirm_delete = (saved["id"], now + CONFIRM_S)


def select(win, layout_id) -> None:
    if layout_id != win._sl_selected:
        win._sl_selected = layout_id
        win._sl_scroll_cmp = 0
        win._sl_confirm_delete = None
        _save_settings(win)


def toggle_matching(win) -> None:
    win._sl_show_matching = not win._sl_show_matching
    win._sl_scroll_cmp = 0
    _save_settings(win)


def tick(win) -> None:
    """Once per frame while the window is open: finish a waiting save, poll the specs, expire hints."""
    now = time.monotonic()
    pending = win._sl_pending
    if pending is not None:
        if win._sl_specs_count > pending["count"]:
            _finish_save(win, True)
        elif now - pending["since"] >= SPECS_WAIT_S:
            _finish_save(win, False)
    elif win._sl_selected and now - win._sl_last_poll >= SPECS_POLL_S:
        _request_specs(win, now)
    if now - win._sl_last_refresh >= REFRESH_S:
        win._sl_last_refresh = now
        win._sl_store.refresh()
    if win._sl_status is not None and now > win._sl_status[2]:
        win._sl_status = None
    armed = win._sl_confirm_delete
    if armed is not None and now > armed[1]:
        win._sl_confirm_delete = None
    if win._sl_selected is None or not win._sl_store.get(win._sl_selected):
        _auto_select(win)


# ── geometry ─────────────────────────────────────────────────────────────────

def min_size(win) -> tuple:
    return win._s(_MIN_W), win._s(_MIN_H)


def window_rect(win, screen_w: int, screen_h: int) -> pygame.Rect:
    rect = win._sl_rect
    if rect is None:
        width = min(win._s(_DEFAULT_W), screen_w)
        height = min(win._s(_DEFAULT_H), screen_h)
        rect = pygame.Rect((screen_w - width) // 2, max(0, (screen_h - height) // 3), width, height)
    min_w, min_h = min_size(win)
    rect = wg.clamp_rect(rect, screen_w, screen_h, min_w, min_h)
    win._sl_rect = rect
    return rect


def layout_rects(rect: pygame.Rect, s) -> dict:
    """Every rectangle of the window, from the window rectangle alone (``s`` scales UI pixels)."""
    pad = s(10)
    header_h = s(34)
    status_h = s(20)
    body = pygame.Rect(rect.x + pad, rect.y + header_h + pad, rect.w - pad * 2,
                       rect.h - header_h - pad * 2 - status_h)
    left_w = max(s(190), min(s(280), rect.w * 3 // 10))
    left = pygame.Rect(body.x, body.y, left_w, body.h)
    right = pygame.Rect(left.right + pad, body.y, body.right - left.right - pad, body.h)
    button_h = s(26)
    gap = s(6)
    save = pygame.Rect(left.x, left.y, left.w, s(28))
    buttons_y = left.bottom - button_h
    third = (left.w - gap * 2) // 3
    update = pygame.Rect(left.x, buttons_y, third, button_h)
    rename = pygame.Rect(update.right + gap, buttons_y, third, button_h)
    delete = pygame.Rect(rename.right + gap, buttons_y, left.right - rename.right - gap, button_h)
    list_area = pygame.Rect(left.x, save.bottom + gap, left.w, buttons_y - gap - (save.bottom + gap))
    info_h = s(58)
    options = pygame.Rect(right.x, right.y + info_h, right.w, s(22))
    compare = pygame.Rect(right.x, options.bottom + gap, right.w, right.bottom - options.bottom - gap)
    close = pygame.Rect(rect.right - pad - s(22), rect.y + (header_h - s(20)) // 2, s(22), s(20))
    ok = pygame.Rect(save.right - s(44) - s(40) - gap, save.y, s(40), save.h)
    cancel = pygame.Rect(save.right - s(44), save.y, s(44), save.h)
    field = pygame.Rect(save.x, save.y, ok.x - gap - save.x, save.h)
    return {"header": pygame.Rect(rect.x, rect.y, rect.w, header_h), "close": close, "body": body, "left": left,
            "right": right, "save": save, "field": field, "ok": ok, "cancel": cancel, "list": list_area,
            "update": update, "rename": rename, "delete": delete,
            "info": pygame.Rect(right.x, right.y, right.w, info_h), "options": options, "compare": compare,
            "status": pygame.Rect(rect.x + pad, rect.bottom - pad - status_h + pad // 2, rect.w - pad * 2, status_h)}


LIST_ROW_H = 38
ROW_H, SECTION_H, GROUP_H, GAP_H = 18, 22, 18, 6


# ── the comparison as a list of drawable lines ──────────────────────────────

def build_view(diff, show_matching: bool, cargo_open: bool) -> list:
    """The right-hand pane as ``(kind, data)`` lines: ``section``, ``row``, ``note``, ``group``, ``stat``, ``gap``."""
    lines: list = []
    for section in diff.sections:
        collapsed = section.key == "cargo" and not cargo_open
        lines.append(("section", section))
        if collapsed:
            continue
        rows = section.visible_rows(show_matching)
        if not rows:
            if not section.rows:
                lines.append(("note", "none on either ship"))
            elif not show_matching:
                lines.append(("note", f"all {section.matching} match"))
        for row in rows:
            lines.append(("row", row))
        lines.append(("gap", None))
    if diff.stats:
        lines.append(("section", ship_layouts.Section("stats", "Stats")))
        group = None
        for stat in diff.stats:
            if stat.group != group:
                group = stat.group
                lines.append(("group", group))
            lines.append(("stat", stat))
        for text in diff.state_note:
            lines.append(("note", text))
    return lines


def view_height(lines, s) -> int:
    heights = {"section": SECTION_H, "row": ROW_H, "note": ROW_H, "group": GROUP_H, "stat": ROW_H, "gap": GAP_H}
    return sum(s(heights[kind]) for kind, _data in lines)


def format_age(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    days = int(seconds // 86400)
    return f"{days} day{'s' if days != 1 else ''} ago"


# ── drawing ──────────────────────────────────────────────────────────────────

def _palette(render_module) -> dict:
    def pick(name, default):
        return tuple(getattr(render_module, name, default))[:3]

    return {"plate": pick("_GM_PLATE_BG", (7, 11, 20)), "header": pick("_GM_HEADER_BG", (13, 21, 38)),
            "canvas": pick("_GM_CANVAS_BG", (5, 9, 18)), "steel": pick("_GM_STEEL", (56, 88, 138)),
            "steel_dim": pick("_GM_STEEL_DIM", (45, 80, 130)), "accent": pick("_GM_ACCENT", (95, 170, 255)),
            "text": pick("_GM_TEXT", (185, 215, 250)), "value": pick("_GM_VALUE", (228, 237, 250)),
            "caption": pick("_GM_CAPTION", (96, 128, 176)), "ok": pick("_GM_SUCCESS", (100, 230, 180)),
            "warn": pick("_GM_WARN", (255, 92, 72)), "amber": (255, 190, 70)}


_colours: dict = _palette(None)

_STATUS_STYLE = {ship_layouts.STATUS_SAME: ("=", "ok"), ship_layouts.STATUS_CHANGED: ("~", "amber"),
                 ship_layouts.STATUS_MISSING: ("-", "warn"), ship_layouts.STATUS_EXTRA: ("+", "accent"),
                 ship_layouts.STATUS_NOTE: ("i", "caption")}
_VERDICT_COLOUR = {"better": "ok", "worse": "warn", "changed": "amber", "same": "caption", "unknown": "caption"}


def _text_surface(win, text: str, size: int, colour: tuple, bold: bool = True):
    cache = win.__dict__.setdefault("_sl_text_cache", {})
    key = (text, size, colour, bold, win._uk)
    surface = cache.get(key)
    if surface is None:
        surface = win._instrument_font(size, bold=bold).render(text, True, colour)
        try:
            from gl_ui_renderer import mark_static_surface
            mark_static_surface(surface)
        except Exception:
            pass
        if len(cache) >= 600:
            cache.clear()
        cache[key] = surface
    return surface


def _fit(win, text: str, size: int, max_w: int, bold: bool = True) -> str:
    """*text* shortened with ``...`` to fit *max_w* pixels."""
    if max_w <= 0:
        return ""
    cache = win.__dict__.setdefault("_sl_fit_cache", {})
    key = (text, size, max_w, bold, win._uk)
    fitted = cache.get(key)
    if fitted is None:
        font = win._instrument_font(size, bold=bold)
        fitted = text
        if font.size(text)[0] > max_w:
            while fitted and font.size(fitted + "...")[0] > max_w:
                fitted = fitted[:-1]
            fitted = (fitted + "...") if fitted else ""
        if len(cache) >= 2000:
            cache.clear()
        cache[key] = fitted
    return fitted


def _blit_text(win, screen, text, size, colour, x, y, max_w=None, bold=True, right=False):
    if max_w is not None:
        text = _fit(win, text, size, max_w, bold)
    if not text:
        return 0
    surface = _text_surface(win, text, size, colour, bold)
    screen.blit(surface, (x - surface.get_width() if right else x, y))
    return surface.get_width()


def _button(win, screen, rect, label, *, enabled=True, hot=False, armed=False):
    c = _colours
    fill = (60, 24, 30) if armed else ((14, 29, 43) if (hot and enabled) else c["plate"])
    border = c["warn"] if armed else (c["accent"] if (hot and enabled) else c["steel"])
    pygame.draw.rect(screen, fill, rect)
    pygame.draw.rect(screen, border, rect, 1)
    colour = c["value"] if enabled else c["caption"]
    surface = _text_surface(win, label, 10, colour)
    screen.blit(surface, (rect.centerx - surface.get_width() // 2, rect.centery - surface.get_height() // 2))


def draw(win, screen) -> None:
    """Draw the window. Total: an error switches the feature off."""
    try:
        _draw(win, screen)
    except Exception:
        _switch_off("draw()")


def _draw(win, screen) -> None:
    c = _colours
    s = win._s
    width, height = screen.get_size()
    rect = window_rect(win, width, height)
    r = layout_rects(rect, s)
    mouse = pygame.mouse.get_pos()
    hits: dict = {"rects": r, "rows": [], "headers": []}
    win._sl_hits = hits

    chamfer = wg.plate_chamfer(rect.h, s(20))
    wg.draw_plate(screen, rect.x, rect.y, rect.w, rect.h, r["header"].h, chamfer)
    _blit_text(win, screen, "S H I P   L A Y O U T S", 12, c["text"], rect.x + s(20), rect.y + s(10))
    hot = r["close"].collidepoint(mouse)
    pygame.draw.rect(screen, (64, 22, 28) if hot else (12, 18, 30), r["close"])
    pygame.draw.rect(screen, c["warn"] if hot else c["steel"], r["close"], 1)
    close_x = _text_surface(win, "X", 12, c["value"])
    screen.blit(close_x, (r["close"].centerx - close_x.get_width() // 2, r["close"].centery - close_x.get_height() // 2))

    store = win._sl_store
    saved = store.get(win._sl_selected)
    pending = win._sl_pending

    # ---- left column: the save control, the list, the buttons
    if win._sl_input is not None:
        _draw_input(win, screen, r, mouse)
    else:
        _button(win, screen, r["save"], "SAVING..." if pending else "SAVE CURRENT SHIP",
                enabled=pending is None, hot=r["save"].collidepoint(mouse))
    _draw_list(win, screen, r, hits, mouse)
    can_edit = saved is not None and pending is None
    armed = win._sl_confirm_delete is not None and saved is not None and win._sl_confirm_delete[0] == saved["id"]
    for key, label in (("update", "UPDATE"), ("rename", "RENAME"), ("delete", "SURE?" if armed else "DELETE")):
        _button(win, screen, r[key], label, enabled=can_edit, hot=r[key].collidepoint(mouse),
                armed=(key == "delete" and armed))

    # ---- right column: the comparison
    _draw_compare(win, screen, r, hits, mouse, saved)

    # ---- status line and the resize grip
    status = win._sl_status
    if status is not None:
        colour = {"ok": c["ok"], "warn": c["amber"], "info": c["text"]}.get(status[1], c["text"])
        _blit_text(win, screen, status[0], 10, colour, r["status"].x, r["status"].y, max_w=r["status"].w)
    elif store.error:
        _blit_text(win, screen, store.error, 10, c["warn"], r["status"].x, r["status"].y, max_w=r["status"].w)
    grip = rect.bottomright
    for k in range(3):
        offset = s(5) + k * s(4)
        pygame.draw.line(screen, c["steel"], (grip[0] - offset - s(4), grip[1] - s(3)),
                         (grip[0] - s(3), grip[1] - offset - s(4)))


def _draw_input(win, screen, r, mouse):
    c = _colours
    box = win._sl_input
    pygame.draw.rect(screen, c["header"], r["field"])
    pygame.draw.rect(screen, c["accent"], r["field"], 1)
    s = win._s
    # The name box (TextField): text, caret and selection; the whole box takes clicks and drags.
    win._draw_text_field(FIELD_KEY, box["text"], screen, win._instrument_font(11, bold=True),
                         r["field"].inflate(-s(12), 0), c["value"], hit_rect=r["field"], owner=box)
    _button(win, screen, r["ok"], "OK", hot=r["ok"].collidepoint(mouse))
    _button(win, screen, r["cancel"], "ESC", hot=r["cancel"].collidepoint(mouse))


def _draw_list(win, screen, r, hits, mouse):
    c = _colours
    s = win._s
    area = r["list"]
    pygame.draw.rect(screen, c["canvas"], area)
    pygame.draw.rect(screen, c["steel"], area, 1)
    layouts = win._sl_store.layouts
    row_h = s(LIST_ROW_H)
    win._sl_max_scroll_list = max(0, len(layouts) * row_h - area.h)
    win._sl_scroll_list = max(0, min(win._sl_scroll_list, win._sl_max_scroll_list))
    previous = screen.get_clip()
    screen.set_clip(area.inflate(-2, -2).clip(previous))
    if not layouts:
        message = "Loading..." if not win._sl_store.loaded else "No layouts yet"
        _blit_text(win, screen, message, 11, c["caption"], area.x + s(10), area.y + s(10))
        _blit_text(win, screen, "Fit a ship, then SAVE CURRENT SHIP.", 10, c["caption"], area.x + s(10),
                   area.y + s(28), max_w=area.w - s(16), bold=False)
    ship_type = (current_cached(win).get("ship") or {}).get("ship_type") if layouts else None
    for index, layout in enumerate(layouts):
        row = pygame.Rect(area.x + 1, area.y + 1 + index * row_h - win._sl_scroll_list, area.w - 2, row_h)
        if row.bottom < area.top or row.top > area.bottom:
            continue
        selected = layout["id"] == win._sl_selected
        hovered = row.collidepoint(mouse) and area.collidepoint(mouse)
        if selected or hovered:
            pygame.draw.rect(screen, (18, 58, 94) if selected else (14, 29, 43), row)
        if selected:
            pygame.draw.rect(screen, c["accent"], (row.x, row.y, s(3), row.h))
        _blit_text(win, screen, layout["name"], 11, c["value"] if selected else c["text"], row.x + s(10),
                   row.y + s(4), max_w=row.w - s(16))
        same_ship = bool(ship_type) and (layout.get("ship") or {}).get("ship_type") == ship_type
        _blit_text(win, screen, ship_layouts.summary_line(layout), 9, c["ok"] if same_ship else c["caption"],
                   row.x + s(10), row.y + s(21), max_w=row.w - s(16), bold=False)
        hits["rows"].append((row.clip(area), layout["id"]))
    screen.set_clip(previous)
    if win._sl_max_scroll_list:
        _scrollbar(win, screen, area, win._sl_scroll_list, win._sl_max_scroll_list, area.h)


def _scrollbar(win, screen, area, scroll, max_scroll, view_h):
    c = _colours
    s = win._s
    track = pygame.Rect(area.right - s(5), area.y + 1, s(4), area.h - 2)
    total = view_h + max_scroll
    thumb_h = max(s(16), int(track.h * view_h / total))
    thumb_y = track.y + int((track.h - thumb_h) * scroll / max_scroll)
    pygame.draw.rect(screen, c["steel_dim"], (track.x, thumb_y, track.w, thumb_h))


def _draw_compare(win, screen, r, hits, mouse, saved):
    c = _colours
    s = win._s
    info, options, area = r["info"], r["options"], r["compare"]
    pygame.draw.rect(screen, c["canvas"], area)
    pygame.draw.rect(screen, c["steel"], area, 1)
    if saved is None:
        _blit_text(win, screen, "Compare", 12, c["text"], info.x, info.y + s(2))
        _blit_text(win, screen, "Select a saved layout to see how it differs from the ship you are flying,",
                   10, c["caption"], area.x + s(10), area.y + s(10), max_w=area.w - s(20), bold=False)
        _blit_text(win, screen, "or save the current ship as a layout first.", 10, c["caption"],
                   area.x + s(10), area.y + s(26), max_w=area.w - s(20), bold=False)
        return
    _saved, diff = current_diff(win)
    ship = saved.get("ship") or {}
    _blit_text(win, screen, saved["name"], 13, c["value"], info.x, info.y, max_w=info.w)
    saved_on = ship.get("display_name") or "an unknown ship"
    by = f" by {saved['saved_by']}" if saved.get("saved_by") else ""
    _blit_text(win, screen, f"Saved on {saved_on}, {format_age(time.time() - saved['saved_at'])}{by}", 10,
               c["caption"], info.x, info.y + s(19), max_w=info.w, bold=False)
    if diff is None:
        return
    now_ship = diff.current_ship.get("display_name") or "unknown"
    if diff.same_ship is False:
        _blit_text(win, screen, f"Different ship: you are flying the {now_ship}, this was saved on the {saved_on}.",
                   10, c["amber"], info.x, info.y + s(33), max_w=info.w)
    else:
        _blit_text(win, screen, f"Flying: {now_ship}", 10, c["text"], info.x, info.y + s(33), max_w=info.w,
                   bold=False)
    changes, stat_changes = diff.differences, diff.stat_changes
    if changes == 0 and stat_changes == 0:
        summary, colour = "Matches your current ship.", c["ok"]
    else:
        summary = f"{changes} difference{'s' if changes != 1 else ''}, {stat_changes} stat change" \
                  f"{'s' if stat_changes != 1 else ''}"
        colour = c["amber"]
    _blit_text(win, screen, summary, 11, colour, info.x, info.y + s(45), max_w=info.w)

    label = "HIDE MATCHING" if win._sl_show_matching else "SHOW MATCHING"
    toggle_w = _text_surface(win, label, 10, c["value"]).get_width() + s(16)
    toggle = pygame.Rect(options.right - toggle_w, options.y, toggle_w, options.h)
    _button(win, screen, toggle, label, hot=toggle.collidepoint(mouse))
    hits["toggle"] = toggle

    lines = build_view(diff, win._sl_show_matching, win._sl_cargo_open)
    total = view_height(lines, s)
    win._sl_max_scroll_cmp = max(0, total - area.h + s(4))
    win._sl_scroll_cmp = max(0, min(win._sl_scroll_cmp, win._sl_max_scroll_cmp))
    previous = screen.get_clip()
    screen.set_clip(area.inflate(-2, -2).clip(previous))
    y = area.y + s(2) - win._sl_scroll_cmp
    pad = s(8)
    inner = area.w - pad * 2 - (s(6) if win._sl_max_scroll_cmp else 0)
    heights = {"section": SECTION_H, "row": ROW_H, "note": ROW_H, "group": GROUP_H, "stat": ROW_H, "gap": GAP_H}
    for kind, data in lines:
        h = s(heights[kind])
        if y + h >= area.top and y <= area.bottom:
            _draw_line(win, screen, kind, data, area.x + pad, y, inner, h, hits, area)
        y += h
    screen.set_clip(previous)
    if win._sl_max_scroll_cmp:
        _scrollbar(win, screen, area, win._sl_scroll_cmp, win._sl_max_scroll_cmp, area.h)


def _draw_line(win, screen, kind, data, x, y, w, h, hits, area):
    c = _colours
    s = win._s
    mid = y + (h - _text_surface(win, "Ag", 10, c["text"]).get_height()) // 2
    if kind == "section":
        pygame.draw.rect(screen, c["header"], (x - s(4), y + s(1), w + s(8), h - s(2)))
        pygame.draw.rect(screen, c["accent"], (x - s(4), y + s(1), s(3), h - s(2)))
        title = data.title.upper()
        collapsed = data.key == "cargo" and not win._sl_cargo_open
        if data.key == "stats":
            note, colour = "", c["caption"]
            label_w = int(w * 0.34)
            cols = (w - label_w) // 3
            for caption, edge in (("SAVED", x + label_w + cols - s(6)), ("NOW", x + label_w + cols * 2 - s(6)),
                                  ("CHANGE", x + w - s(4))):
                _blit_text(win, screen, caption, 9, c["caption"], edge, mid, right=True, bold=False)
        elif data.differences:
            note, colour = f"{data.differences} different", c["amber"]
        elif data.rows:
            note, colour = f"{data.matching} match", c["ok"]
        else:
            note, colour = "", c["caption"]
        if data.key == "cargo":
            title += "   [+]" if collapsed else "   [-]"
            hits["headers"].append((pygame.Rect(x - s(4), y, w + s(8), h).clip(area), "cargo"))
        _blit_text(win, screen, title, 10, c["text"], x + s(6), mid)
        if note and data.key != "stats":
            _blit_text(win, screen, note, 9, colour, x + w, mid, right=True, bold=False)
        return
    if kind == "gap":
        return
    if kind == "note":
        _blit_text(win, screen, data, 10, c["caption"], x + s(8), mid, max_w=w - s(8), bold=False)
        return
    if kind == "group":
        _blit_text(win, screen, data.upper(), 9, c["caption"], x + s(2), mid)
        return
    if kind == "row":
        icon, name = _STATUS_STYLE[data.status]
        colour = c[name]
        _blit_text(win, screen, icon, 11, colour, x + s(2), mid)
        label_w = int(w * 0.24)
        _blit_text(win, screen, data.label, 10, c["text"], x + s(16), mid, max_w=label_w - s(16))
        col = x + label_w
        half = (w - label_w) // 2
        if data.status == ship_layouts.STATUS_NOTE:
            return
        saved_colour = {ship_layouts.STATUS_MISSING: c["warn"], ship_layouts.STATUS_CHANGED: c["caption"],
                        ship_layouts.STATUS_EXTRA: c["caption"]}.get(data.status, c["text"])
        now_colour = {ship_layouts.STATUS_MISSING: c["caption"], ship_layouts.STATUS_CHANGED: c["amber"],
                      ship_layouts.STATUS_EXTRA: c["accent"]}.get(data.status, c["value"])
        _blit_text(win, screen, data.saved, 10, saved_colour, col, mid, max_w=half - s(14), bold=False)
        _blit_text(win, screen, ">", 10, c["caption"], col + half - s(12), mid)
        _blit_text(win, screen, data.current, 10, now_colour, col + half, mid, max_w=w - label_w - half,
                   bold=False)
        return
    if kind == "stat":
        label_w = int(w * 0.34)
        _blit_text(win, screen, data.label, 10, c["text"], x + s(4), mid, max_w=label_w - s(6))
        cols = (w - label_w) // 3
        _blit_text(win, screen, data.saved_text, 10, c["caption"], x + label_w + cols - s(6), mid, right=True,
                   bold=False)
        _blit_text(win, screen, data.current_text, 10, c["value"], x + label_w + cols * 2 - s(6), mid, right=True)
        delta = data.delta_text
        if delta:
            _blit_text(win, screen, delta, 10, c[_VERDICT_COLOUR[data.verdict]], x + w - s(4), mid, right=True)


# ── input ────────────────────────────────────────────────────────────────────

def _handle_key(win, event) -> bool:
    box = win._sl_input
    if box is None:
        return False
    if event.type == pygame.KEYUP:
        return True
    key = event.key
    if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
        commit_input(win)
    elif key == pygame.K_ESCAPE:
        cancel_input(win)
    else:
        # Typing, caret keys, selection and the clipboard are the shared TextField's (text_field_host.py).
        box["text"] = win._apply_text_edit(FIELD_KEY, box["text"], event, max_length=ship_layouts.MAX_NAME_LEN,
                                           owner=box).text
    return True


def _owns(win, pos, focus: bool = False) -> bool:
    """Does the window own the pointer at *pos*? ``focus`` raises it above the other windows (a press on it)."""
    manager = getattr(win, "_gui_windows", None)
    return manager is not None and manager.route_pointer(pos, focus=focus).owner == ID


def _click(win, pos) -> None:
    hits = win._sl_hits
    r = hits.get("rects")
    if not r:
        return
    box = win._sl_input
    if r["close"].collidepoint(pos):
        close_window(win)
        return
    if box is not None:
        if r["ok"].collidepoint(pos):
            commit_input(win)
        elif r["cancel"].collidepoint(pos):
            cancel_input(win)
        elif r["field"].collidepoint(pos):
            win._press_text_field_at(FIELD_KEY, box["text"], pos, owner=box)   # caret to the press, a drag-select begins
        return
    if r["save"].collidepoint(pos):
        if win._sl_pending is None:
            begin_input(win, "save")
        return
    for key, action in (("update", begin_update), ("rename", lambda w: begin_input(w, "rename")),
                        ("delete", delete_selected)):
        if r[key].collidepoint(pos):
            if win._sl_store.get(win._sl_selected) and win._sl_pending is None:
                action(win)
            return
    if hits.get("toggle") is not None and hits["toggle"].collidepoint(pos):
        toggle_matching(win)
        return
    for rect, key in hits.get("headers", ()):
        if rect.collidepoint(pos) and key == "cargo":
            win._sl_cargo_open = not win._sl_cargo_open
            return
    for rect, layout_id in hits.get("rows", ()):
        if rect.collidepoint(pos):
            select(win, layout_id)
            return


def _begin_drag(win, pos) -> bool:
    r = win._sl_hits.get("rects")
    rect = win._sl_rect
    if not r or rect is None:
        return False
    edges = wg.resize_zone(rect, pos, win._s(6), win._s(16))
    if edges:
        kind = "resize"
    elif r["header"].collidepoint(pos) and not r["close"].collidepoint(pos):
        kind = "move"
    else:
        return False
    win._sl_drag = {"kind": kind, "edges": edges, "start": pygame.Rect(rect), "origin": tuple(pos)}
    return True


def _drag(win, pos) -> None:
    drag = win._sl_drag
    surface = pygame.display.get_surface()
    width, height = surface.get_size() if surface is not None else (1280, 720)
    dx, dy = pos[0] - drag["origin"][0], pos[1] - drag["origin"][1]
    if drag["kind"] == "move":
        win._sl_rect = wg.moved_rect(drag["start"], dx, dy, width, height)
    else:
        min_w, min_h = min_size(win)
        win._sl_rect = wg.resized_rect(drag["start"], drag["edges"], dx, dy, width, height, min_w, min_h)


def _scroll(win, pos, amount: int) -> bool:
    r = win._sl_hits.get("rects")
    if not r:
        return False
    step = win._s(36) * -amount
    if r["list"].collidepoint(pos):
        win._sl_scroll_list = max(0, min(win._sl_max_scroll_list, win._sl_scroll_list + step))
        return True
    if r["compare"].collidepoint(pos):
        win._sl_scroll_cmp = max(0, min(win._sl_max_scroll_cmp, win._sl_scroll_cmp + step))
        return True
    return True


def _handle(win, event) -> bool:
    kind = event.type
    drag = win._sl_drag
    if kind in (pygame.KEYDOWN, pygame.KEYUP):
        return _handle_key(win, event)
    if kind == pygame.MOUSEBUTTONDOWN:
        if not _owns(win, event.pos, focus=True):
            return False
        if event.button == 1 and drag is None:
            if not _begin_drag(win, event.pos):
                _click(win, event.pos)
        return True
    if kind == pygame.MOUSEMOTION and drag is not None:
        if not event.buttons[0]:
            win._sl_drag = None
            _save_settings(win)
            return False
        _drag(win, event.pos)
        return True
    if kind == pygame.MOUSEBUTTONUP and event.button == 1 and drag is not None:
        _drag(win, event.pos)
        win._sl_drag = None
        _save_settings(win)
        return True
    if kind == pygame.MOUSEBUTTONUP:
        if win._sl_input is not None:
            # This filter runs before the game's event loop, so a release over this window never reaches the
            # text box's drag-select: end it here (the call does not use the release up).
            win._text_field_drag_event(event)
        return _owns(win, event.pos)
    if kind == pygame.MOUSEWHEEL:
        pos = pygame.mouse.get_pos()
        if _owns(win, pos):
            return _scroll(win, pos, int(getattr(event, "y", 0)))
    return False


def filter_events(win, events):
    """Called once per frame (Client.py) with the frame's events: takes the ones that belong to the window out of
    the list, so the game's own handlers never see them. Total."""
    if not _enabled():
        return events
    try:
        _ensure(win)
        _manager_owner(win)
        if not win._sl_open:
            return events
        kept = [event for event in events if not _handle(win, event)]
        return kept if len(kept) != len(events) else events
    except Exception:
        _switch_off("filter_events()")
        return events


# ── hooks ────────────────────────────────────────────────────────────────────

def queue(win, frame) -> None:
    """Called once per frame (Client.py) before the retained windows are flushed: tick, then queue the draw."""
    if not _enabled():
        return
    try:
        _ensure(win)
        if not win._sl_open:
            return
        tick(win)
        screen = frame.screen
        win._gui_windows.queue_draw(
            ID, lambda: draw(win, screen), lambda: win._sl_rect, layer=GuiLayer.WINDOW, focusable=True,
            visible=lambda: bool(win._sl_open and _enabled()))
        _manager_owner(win)
        if win._sl_rect is None:
            window_rect(win, *screen.get_size())
    except Exception:
        _switch_off("queue()")


def handle_command(win, args: str = "") -> None:
    """``/layouts``: toggle the window."""
    if not _enabled():
        win.chat_log("Ship layouts are switched off.", (255, 180, 60))
        return
    toggle(win)


def install(window_cls, render_module=None, client_globals=None) -> bool:
    """Wrap ``set_ship_specs_data`` (to count the answers a save waits for) and, given Client.py's ``globals()``,
    the flight-key text gate (so typing a name never flies the ship). Idempotent; True if hooked."""
    global _colours
    if window_cls.__dict__.get("_ship_layouts_installed"):
        return False
    _colours = _palette(render_module)
    load()
    specs_setter = window_cls.set_ship_specs_data

    def set_ship_specs_data(self, data):
        result = specs_setter(self, data)
        try:
            if "_sl_ready" in self.__dict__:
                self._sl_specs_count += 1
        except Exception:
            pass
        return result

    set_ship_specs_data.__wrapped__ = specs_setter
    set_ship_specs_data.__doc__ = specs_setter.__doc__
    window_cls.set_ship_specs_data = set_ship_specs_data

    if client_globals is not None and "_flight_text_entry_active" in client_globals:
        gate = client_globals["_flight_text_entry_active"]

        def flight_text_entry_active(**kwargs):
            return bool(gate(**kwargs) or any_text_focused())

        flight_text_entry_active.__wrapped__ = gate
        client_globals["_flight_text_entry_active"] = flight_text_entry_active
    window_cls._ship_layouts_installed = True
    return True

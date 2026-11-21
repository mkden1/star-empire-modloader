"""ui_layouts_window.py - the UI LAYOUTS window: save where the HUD's windows sit under a name, and switch between the
saved layouts (ui_layouts.py is the model; this is the window and the glue to the game).

Made for a client that is played on screens of different sizes: the game pins every window to the screen corner it was nearest to
when the window changes size, and that is not reversible, so after a trip to a smaller screen and back the windows are somewhere
else. Arrange the HUD once on each screen, SAVE it under a name, and LOAD that name whenever the client is on that screen again.

Open it from the HUD menu (UI LAYOUTS) or with ``/uilayout``. SAVE CURRENT stores where everything is now; select a layout and
LOAD (or double-click it) puts it back; UNDO takes the last LOAD back; UPDATE replaces a layout with what is on screen now;
RENAME and DELETE do what they say (UPDATE and DELETE ask for a second click). A layout made for another window size is
fitted to this one when it is loaded, the way the game itself repositions windows when you resize it.

``/uilayout`` toggles the window, ``/uilayout list`` lists the layouts, ``/uilayout save <name>`` saves (or updates) one and
``/uilayout load <name>`` loads one: handy when the windows are out of reach.

The window and the vanilla lines that open it (the HUD menu row, ``/uilayout``), draw it (one queue call before the retained windows
are flushed) and feed it events (one filter call after the event pump) are hooked in from Client.py by the mod's patch; every
function the vanilla code calls is total: an error switches the window off for the session. ``STAR_EMPIRE_NO_UI_LAYOUTS=1``
turns it off.
"""

from __future__ import annotations

import logging
import os
import sys
import time
import weakref

import pygame

import window_geometry as wg
import ui_layouts
from gui_window_manager import GuiLayer

logger = logging.getLogger(__name__)

ID = "ui_layouts"
FIELD_KEY = "ui_layouts_name"       # the name box's TextField in the window's text_field_host registry
ENV_OFF = "STAR_EMPIRE_NO_UI_LAYOUTS"

_MIN_W, _MIN_H = 420, 300
_DEFAULT_W, _DEFAULT_H = 480, 380
REFRESH_S = 15.0               # re-read the file now and then: another client may have saved a layout
CONFIRM_S = 3.0                # how long a first UPDATE / DELETE click stays armed
STATUS_S = 6.0
DOUBLE_CLICK_S = 0.45
LIST_ROW_H = 40

# The windows other mods keep in their own file instead of the game's layout dict: (layout key, the rectangle's attribute on the game
# window, the mod module, its function that saves the window's state). Only a window that has been opened this session has the
# attribute; the others are left alone.
EXTRA_WINDOWS = (
    ("galaxy_map", "_gm_win_rect", "galaxy_map_window", "_save"),
    ("ship_layouts", "_sl_rect", "ship_layouts_window", "_save_settings"),
)

# Mods whose windows are not rectangles on the game window (the Quick Bars mod's extra bars are placed by fractions of the screen): (the
# key their data is kept under in a layout, the mod module, its function that returns that data (or None) for this window, its function
# that puts the data back). Looked up by module name at the time, so a mod that is not installed is simply not there.
PROVIDERS = (
    ("quick_bars", "extra_bars", "layout_capture", "layout_apply"),
)

# Where each section of a layout keeps its position on the game window, and what "the default place" is. The game's loader ignores a
# null position, so a layout saved with a window at its default place would leave a window that was dragged since where it is.
DEFAULT_PLACE = {
    "hotbar_pos": (("_hotbar_pos_x", None), ("_hotbar_pos_y", None)),
    "stats": (("_stats_pos_x", None), ("_stats_pos_y", None)),
    "squad": (("_squad_pos_x", None), ("_squad_pos_y", None)),
    "minimap": (("_mm_pos_x", None), ("_mm_pos_y", None)),
    "coalition": (("_coalition_pos_x", None), ("_coalition_pos_y", None)),
    "friends": (("_friends_pos_x", None), ("_friends_pos_y", None)),
    "mission_detail": (("_mission_detail_pos_x", None), ("_mission_detail_pos_y", None)),
    "combat_log": (("_combat_log_pos_x", None), ("_combat_log_pos_y", None)),
    "bot_haul": (("_rc_haul_panel_pos", None), ("_rc_haul_pos_custom", False)),
    "bot_dock_picker": (("_rc_dock_pos", None),),
    "run_summary": (("_run_summary_pos", None),),
    "mechlings": (("_mechlings_pos", None),),
}
# The same for a size that can be null (the Mission Detail window's, until the player resizes it).
DEFAULT_SIZE = {
    ("mission_detail", "width"): "_mission_detail_w",
    ("mission_detail", "height"): "_mission_detail_h",
}

STORE = ui_layouts.LayoutStore()
_failed = False
_windows: list = []            # weak references to every window that has used the feature


def _switch_off(where: str) -> None:
    global _failed
    if not _failed:
        _failed = True
        logger.exception("ui_layouts_window: switched off for this session (%s)", where)


def _enabled() -> bool:
    return not _failed and not os.environ.get(ENV_OFF)


def configure_for_tests(path: "str | None", submit=None) -> None:
    global STORE
    STORE = ui_layouts.LayoutStore(path, submit)


def reset_for_tests() -> None:
    global _failed, STORE
    _failed = False
    STORE = ui_layouts.LayoutStore()
    _windows.clear()                # a window an earlier test left typing must not hold the flight keys


# ── window state ─────────────────────────────────────────────────────────────

def _ensure(win) -> None:
    if "_ul_ready" in win.__dict__:
        return
    win._ul_ready = True
    _windows.append(weakref.ref(win))
    win._ul_open = False
    win._ul_store = STORE
    win._ul_rect = None
    win._ul_selected = None
    win._ul_input = None            # {"mode": "save"|"rename", "text": str, "target": id | None}; caret / selection live in the
                                    # box's TextField (FIELD_KEY, owned by this dict)
    win._ul_confirm = None          # (action, layout id, monotonic deadline) for UPDATE / DELETE
    win._ul_status = None           # (text, kind, monotonic deadline)
    win._ul_undo = None             # the layout the last LOAD replaced
    win._ul_drag = None
    win._ul_hits = {}
    win._ul_size = None             # the screen's size, noted every frame by ``queue``
    win._ul_scroll = 0
    win._ul_max_scroll = 0
    win._ul_last_refresh = 0.0
    win._ul_last_click = None
    if not win._ul_store.loaded:
        win._ul_store.refresh()


def is_open(win) -> bool:
    if not _enabled():
        return False
    try:
        _ensure(win)
        return bool(win._ul_open)
    except Exception:
        _switch_off("is_open()")
        return False


def text_focused(win) -> bool:
    """Is a layout name being typed? (Keeps the flight keys quiet while it is.)"""
    return bool(is_open(win) and win._ul_input is not None)


def any_text_focused() -> bool:
    return any(text_focused(w) for w in (ref() for ref in list(_windows)) if w is not None)


def open_window(win) -> None:
    _ensure(win)
    win._ul_open = True
    win._ul_last_refresh = time.monotonic()
    win._ul_store.refresh()
    _auto_select(win)
    _manager_owner(win)


def close_window(win) -> None:
    _ensure(win)
    win._ul_open = False
    win._ul_input = None
    win._ul_drag = None
    win._ul_confirm = None


def toggle(win) -> None:
    if is_open(win):
        close_window(win)
    else:
        open_window(win)


def _manager_owner(win) -> None:
    manager = getattr(win, "_gui_windows", None)
    if manager is not None:
        manager._ul_owner = weakref.ref(win)


def _say(win, text: str, kind: str = "info") -> None:
    win._ul_status = (text, kind, time.monotonic() + STATUS_S)


def _auto_select(win) -> None:
    """Keep the selection if it still exists; else the layout made for this window size; else the first."""
    store = win._ul_store
    if store.get(win._ul_selected) is not None or not store.layouts:
        return
    size = window_size(win)
    fits = [d for d in store.layouts if size is not None and tuple(d["size"]) == size]
    win._ul_selected = (fits or store.layouts)[0]["id"]


# ── what the game has: the size, the places ──────────────────────────────────

def window_size(win) -> "tuple | None":
    """The game window's size now: the size ``queue`` noted last frame, else the draw canvas, else the display."""
    noted = win.__dict__.get("_ul_size")
    if noted:
        return tuple(noted)
    for source in (lambda: win._gl_canvas_above.get_size(), lambda: pygame.display.get_window_size()):
        try:
            width, height = source()
            if width > 0 and height > 0:
                return int(width), int(height)
        except Exception:
            continue
    return None


def capture(win) -> "tuple[dict, dict]":
    """Where everything is now: the game's own layout dict reduced to its places, and the rectangles of the mods' windows."""
    geometry = ui_layouts.places(win._panel_layout_dict())
    windows = {}
    for key, attr, _module, _save in EXTRA_WINDOWS:
        rect = getattr(win, attr, None)
        if isinstance(rect, pygame.Rect):
            windows[key] = [rect.x, rect.y, rect.w, rect.h]
    return geometry, windows


def capture_extras(win) -> dict:
    """The places the other mods keep (``PROVIDERS``): ``{mod key: data}`` for each that is installed and has something to say."""
    extras = {}
    for key, module, capture_name, _apply in PROVIDERS:
        capture_fn = getattr(sys.modules.get(module), capture_name, None)
        if not callable(capture_fn):
            continue
        try:
            data = capture_fn(win)
        except Exception:
            logger.debug("ui_layouts_window: %s could not say where its windows are", module, exc_info=True)
            continue
        if isinstance(data, dict) and data:
            extras[key] = data
    return extras


def _put(win, geometry: dict, windows: dict, extras: "dict | None" = None) -> None:
    """Make *geometry*, *windows* and *extras* the places on screen. Raises when the game's loader does."""
    win.apply_panel_layout(ui_layouts.merge(win._panel_layout_dict(), geometry))
    for section in ui_layouts.default_sections(geometry):
        for attr, default in DEFAULT_PLACE.get(section, ()):
            if hasattr(win, attr):
                setattr(win, attr, default)
    for key in ui_layouts.null_sizes(geometry):
        attr = DEFAULT_SIZE.get(key)
        if attr and hasattr(win, attr):
            setattr(win, attr, None)
    for key, attr, module, save in EXTRA_WINDOWS:
        rect = windows.get(key)
        if rect is None or not hasattr(win, attr):
            continue
        setattr(win, attr, pygame.Rect(*rect))
        saver = getattr(sys.modules.get(module), save, None)
        if callable(saver):
            try:
                saver(win)
            except Exception:
                logger.debug("ui_layouts_window: %s could not save its window", module, exc_info=True)
    for key, module, _capture, apply_name in PROVIDERS:
        data = (extras or {}).get(key)
        apply_fn = getattr(sys.modules.get(module), apply_name, None)
        if data is None or not callable(apply_fn):
            continue
        try:
            apply_fn(win, data)
        except Exception:
            logger.debug("ui_layouts_window: %s could not put its windows back", module, exc_info=True)


def apply_layout(win, layout: dict, remember: bool = True) -> "tuple[bool, str]":
    """Put *layout* on screen (fitted to the window when it was made for another size) and keep what it replaced for UNDO.
    Returns (ok, what to tell the player)."""
    if getattr(win, "_ui_editor", None) is not None:
        return False, "Finish the game's UI editor first."
    size = window_size(win)
    saved_size = tuple(layout["size"])
    geometry, windows = layout["geometry"], layout["windows"]
    fitted = size is not None and size != saved_size
    if fitted:
        geometry = ui_layouts.fit_geometry(geometry, saved_size, size)
        windows = ui_layouts.fit_windows(windows, saved_size, size)
    extras = layout.get("extras") or {}                              # (relative places: nothing to fit)
    before = ui_layouts.new_layout("before", size or saved_size, *capture(win), extras=capture_extras(win))
    try:
        _put(win, geometry, windows, extras)
    except Exception:
        logger.exception("ui_layouts_window: could not apply %r", layout.get("name"))
        try:
            _put(win, before["geometry"], before["windows"], before["extras"])   # best effort: leave things where they were
        except Exception:
            logger.exception("ui_layouts_window: could not put the windows back either")
        return False, f"Could not load '{layout['name']}' (the log says why); the windows were left as they were."
    if remember:
        win._ul_undo = before
    try:
        win._save_options_fn(panels=win._panel_layout_dict())
    except Exception:
        logger.debug("ui_layouts_window: could not save options.txt", exc_info=True)
    note = f", fitted from {saved_size[0]} x {saved_size[1]} to {size[0]} x {size[1]}" if fitted else ""
    return True, f"Loaded '{layout['name']}'{note}."


def default_name(win) -> str:
    size = window_size(win)
    return f"{size[0]} x {size[1]}" if size else "My layout"


def save_current(win, name: str, target_id: "str | None" = None) -> "tuple[bool, str]":
    """Store where everything is now. With *target_id* the layout of that id is replaced (name and place in the list kept)."""
    size = window_size(win)
    if size is None:
        return False, "The window size is not known yet; try again in a moment."
    if not win._ul_store.loaded:
        return False, "Still loading the saved layouts; try again in a moment."
    previous = win._ul_store.get(target_id) if target_id else None
    layout = ui_layouts.new_layout(previous["name"] if previous else name, size, *capture(win), extras=capture_extras(win))
    if previous is not None:
        layout["id"] = previous["id"]
    win._ul_store.save(layout)
    win._ul_selected = layout["id"]
    verb = "Updated" if previous is not None else "Saved"
    return True, f"{verb} '{layout['name']}' ({size[0]} x {size[1]})."


def selected(win) -> "dict | None":
    return win._ul_store.get(win._ul_selected)


def load_selected(win) -> None:
    layout = selected(win)
    if layout is not None:
        ok, text = apply_layout(win, layout)
        _say(win, text, "ok" if ok else "warn")


def undo(win) -> None:
    before = win._ul_undo
    if before is None:
        return
    ok, text = apply_layout(win, {**before, "name": "the previous places"}, remember=False)
    if ok:
        win._ul_undo = None
        text = "Put the windows back where they were before the last LOAD."
    _say(win, text, "ok" if ok else "warn")


# ── save / update / rename / delete ──────────────────────────────────────────

def begin_input(win, mode: str) -> None:
    """Open the name field for a new layout (``save``) or a new name for the selected one (``rename``)."""
    target = win._ul_selected if mode == "rename" else None
    if mode == "rename" and selected(win) is None:
        return
    text = selected(win)["name"] if mode == "rename" else win._ul_store.unique_name(default_name(win))
    text = text[:ui_layouts.MAX_NAME_LEN]
    box = {"mode": mode, "text": text, "target": target}
    win._ul_input = box
    win._text_cursor_to_end(FIELD_KEY, text, owner=box)     # a fresh TextField per opening, caret after the text
    win._ul_confirm = None


def cancel_input(win) -> None:
    win._ul_input = None


def commit_input(win) -> None:
    box = win._ul_input
    if box is None:
        return
    name = ui_layouts.clean_name(box["text"])
    if not name:
        _say(win, "A layout needs a name.", "warn")
        return
    win._ul_input = None
    if box["mode"] == "rename":
        saved = win._ul_store.get(box["target"])
        if saved is None:
            return
        taken = {d["name"].lower() for d in win._ul_store.layouts if d["id"] != saved["id"]}
        final = name if name.lower() not in taken else win._ul_store.unique_name(name)
        win._ul_store.save({**saved, "name": final})
        _say(win, f"Renamed to '{final}'.", "ok")
        return
    ok, text = save_current(win, win._ul_store.unique_name(name))
    _say(win, text, "ok" if ok else "warn")


def _armed(win, action: str, layout_id: str) -> bool:
    """The first click on UPDATE / DELETE arms it; a second one within CONFIRM_S on the same layout is the go-ahead."""
    now = time.monotonic()
    armed = win._ul_confirm
    if armed is not None and armed[0] == action and armed[1] == layout_id and now <= armed[2]:
        win._ul_confirm = None
        return True
    win._ul_confirm = (action, layout_id, now + CONFIRM_S)
    return False


def update_selected(win) -> None:
    saved = selected(win)
    if saved is not None and _armed(win, "update", saved["id"]):
        ok, text = save_current(win, saved["name"], target_id=saved["id"])
        _say(win, text, "ok" if ok else "warn")


def delete_selected(win) -> None:
    saved = selected(win)
    if saved is not None and _armed(win, "delete", saved["id"]):
        win._ul_store.delete(saved["id"])
        win._ul_selected = None
        _auto_select(win)
        _say(win, f"Deleted '{saved['name']}'.", "ok")


def select(win, layout_id) -> None:
    if layout_id != win._ul_selected:
        win._ul_selected = layout_id
        win._ul_confirm = None


def tick(win) -> None:
    """Once per frame while the window is open: re-read the file now and then, expire hints, keep a selection."""
    now = time.monotonic()
    if now - win._ul_last_refresh >= REFRESH_S:
        win._ul_last_refresh = now
        win._ul_store.refresh()
    if win._ul_status is not None and now > win._ul_status[2]:
        win._ul_status = None
    armed = win._ul_confirm
    if armed is not None and now > armed[2]:
        win._ul_confirm = None
    if win._ul_store.get(win._ul_selected) is None:
        win._ul_selected = None
        _auto_select(win)


# ── geometry ─────────────────────────────────────────────────────────────────

def min_size(win) -> tuple:
    return win._s(_MIN_W), win._s(_MIN_H)


def window_rect(win, screen_w: int, screen_h: int) -> pygame.Rect:
    rect = win._ul_rect
    if rect is None:
        width = min(win._s(_DEFAULT_W), screen_w)
        height = min(win._s(_DEFAULT_H), screen_h)
        rect = pygame.Rect((screen_w - width) // 2, max(0, (screen_h - height) // 3), width, height)
    min_w, min_h = min_size(win)
    rect = wg.clamp_rect(rect, screen_w, screen_h, min_w, min_h)
    win._ul_rect = rect
    return rect


def layout_rects(rect: pygame.Rect, s) -> dict:
    """Every rectangle of the window, from the window rectangle alone (``s`` scales UI pixels)."""
    pad, gap = s(10), s(6)
    header_h, status_h, button_h = s(34), s(20), s(26)
    body = pygame.Rect(rect.x + pad, rect.y + header_h + pad, rect.w - pad * 2, rect.h - header_h - pad * 2 - status_h)
    save = pygame.Rect(body.x, body.y, body.w, s(28))
    buttons_y = body.bottom - button_h
    info = pygame.Rect(body.x, buttons_y - gap - s(32), body.w, s(32))
    list_area = pygame.Rect(body.x, save.bottom + gap, body.w, info.y - gap - (save.bottom + gap))
    fifth = (body.w - gap * 4) // 5
    names = ("load", "undo", "update", "rename", "delete")
    buttons = {}
    x = body.x
    for index, name in enumerate(names):
        width = fifth if index < 4 else body.right - x
        buttons[name] = pygame.Rect(x, buttons_y, width, button_h)
        x += width + gap
    close = pygame.Rect(rect.right - pad - s(22), rect.y + (header_h - s(20)) // 2, s(22), s(20))
    ok = pygame.Rect(save.right - s(44) - s(40) - gap, save.y, s(40), save.h)
    cancel = pygame.Rect(save.right - s(44), save.y, s(44), save.h)
    field = pygame.Rect(save.x, save.y, ok.x - gap - save.x, save.h)
    return {"header": pygame.Rect(rect.x, rect.y, rect.w, header_h), "close": close, "body": body, "save": save,
            "field": field, "ok": ok, "cancel": cancel, "list": list_area, "info": info, **buttons,
            "status": pygame.Rect(rect.x + pad, rect.bottom - pad - status_h + pad // 2, rect.w - pad * 2, status_h)}


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


def _text_surface(win, text: str, size: int, colour: tuple, bold: bool = True):
    cache = win.__dict__.setdefault("_ul_text_cache", {})
    key = (text, size, colour, bold, win._uk)
    surface = cache.get(key)
    if surface is None:
        surface = win._instrument_font(size, bold=bold).render(text, True, colour)
        try:
            from gl_ui_renderer import mark_static_surface
            mark_static_surface(surface)
        except Exception:
            pass
        if len(cache) >= 400:
            cache.clear()
        cache[key] = surface
    return surface


def _fit(win, text: str, size: int, max_w: int, bold: bool = True) -> str:
    """*text* shortened with ``...`` to fit *max_w* pixels."""
    if max_w <= 0:
        return ""
    font = win._instrument_font(size, bold=bold)
    if font.size(text)[0] <= max_w:
        return text
    fitted = text
    while fitted and font.size(fitted + "...")[0] > max_w:
        fitted = fitted[:-1]
    return (fitted + "...") if fitted else ""


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
    surface = _text_surface(win, label, 10, c["value"] if enabled else c["caption"])
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
    hits: dict = {"rects": r, "rows": []}
    win._ul_hits = hits

    wg.draw_plate(screen, rect.x, rect.y, rect.w, rect.h, r["header"].h, wg.plate_chamfer(rect.h, s(20)))
    _blit_text(win, screen, "U I   L A Y O U T S", 12, c["text"], rect.x + s(20), rect.y + s(10))
    hot = r["close"].collidepoint(mouse)
    pygame.draw.rect(screen, (64, 22, 28) if hot else (12, 18, 30), r["close"])
    pygame.draw.rect(screen, c["warn"] if hot else c["steel"], r["close"], 1)
    close_x = _text_surface(win, "X", 12, c["value"])
    screen.blit(close_x, (r["close"].centerx - close_x.get_width() // 2, r["close"].centery - close_x.get_height() // 2))

    if win._ul_input is not None:
        _draw_input(win, screen, r, mouse)
    else:
        _button(win, screen, r["save"], "SAVE CURRENT", hot=r["save"].collidepoint(mouse))
    _draw_list(win, screen, r, hits, mouse)
    _draw_info(win, screen, r)

    saved = selected(win)
    armed = win._ul_confirm
    for key, label in (("load", "LOAD"), ("undo", "UNDO"), ("update", "UPDATE"), ("rename", "RENAME"), ("delete", "DELETE")):
        enabled = win._ul_undo is not None if key == "undo" else saved is not None
        is_armed = bool(armed and saved is not None and armed[0] == key and armed[1] == saved["id"])
        _button(win, screen, r[key], "SURE?" if is_armed else label, enabled=enabled,
                hot=r[key].collidepoint(mouse), armed=is_armed)

    status = win._ul_status
    if status is not None:
        colour = {"ok": c["ok"], "warn": c["amber"], "info": c["text"]}.get(status[1], c["text"])
        _blit_text(win, screen, status[0], 10, colour, r["status"].x, r["status"].y, max_w=r["status"].w)
    elif win._ul_store.error:
        _blit_text(win, screen, win._ul_store.error, 10, c["warn"], r["status"].x, r["status"].y, max_w=r["status"].w)
    grip = rect.bottomright
    for k in range(3):
        offset = s(5) + k * s(4)
        pygame.draw.line(screen, c["steel"], (grip[0] - offset - s(4), grip[1] - s(3)), (grip[0] - s(3), grip[1] - offset - s(4)))


def _draw_input(win, screen, r, mouse):
    c = _colours
    box = win._ul_input
    pygame.draw.rect(screen, c["header"], r["field"])
    pygame.draw.rect(screen, c["accent"], r["field"], 1)
    win._draw_text_field(FIELD_KEY, box["text"], screen, win._instrument_font(11, bold=True),
                         r["field"].inflate(-win._s(12), 0), c["value"], hit_rect=r["field"], owner=box)
    _button(win, screen, r["ok"], "OK", hot=r["ok"].collidepoint(mouse))
    _button(win, screen, r["cancel"], "ESC", hot=r["cancel"].collidepoint(mouse))


def _draw_list(win, screen, r, hits, mouse):
    c = _colours
    s = win._s
    area = r["list"]
    pygame.draw.rect(screen, c["canvas"], area)
    pygame.draw.rect(screen, c["steel"], area, 1)
    layouts = win._ul_store.layouts
    row_h = s(LIST_ROW_H)
    win._ul_max_scroll = max(0, len(layouts) * row_h - area.h)
    win._ul_scroll = max(0, min(win._ul_scroll, win._ul_max_scroll))
    previous = screen.get_clip()
    screen.set_clip(area.inflate(-2, -2).clip(previous))
    if not layouts:
        message = "Loading..." if not win._ul_store.loaded else "No layouts yet"
        _blit_text(win, screen, message, 11, c["caption"], area.x + s(10), area.y + s(10))
        _blit_text(win, screen, "Arrange the windows, then SAVE CURRENT.", 10, c["caption"], area.x + s(10),
                   area.y + s(28), max_w=area.w - s(16), bold=False)
    size = window_size(win)
    now = time.time()
    for index, layout in enumerate(layouts):
        row = pygame.Rect(area.x + 1, area.y + 1 + index * row_h - win._ul_scroll, area.w - 2, row_h)
        if row.bottom < area.top or row.top > area.bottom:
            continue
        chosen = layout["id"] == win._ul_selected
        hovered = row.collidepoint(mouse) and area.collidepoint(mouse)
        if chosen or hovered:
            pygame.draw.rect(screen, (18, 58, 94) if chosen else (14, 29, 43), row)
        if chosen:
            pygame.draw.rect(screen, c["accent"], (row.x, row.y, s(3), row.h))
        fits = size is not None and tuple(layout["size"]) == size
        tag_w = 0
        if fits:
            tag_w = _blit_text(win, screen, "THIS SIZE", 9, c["ok"], row.right - s(8), row.y + s(5), right=True) + s(8)
        _blit_text(win, screen, layout["name"], 11, c["value"] if chosen else c["text"], row.x + s(10), row.y + s(4),
                   max_w=row.w - s(20) - tag_w)
        caption = f"{ui_layouts.summary(layout)}  -  {ui_layouts.format_age(now - layout['saved_at'])}"
        _blit_text(win, screen, caption, 9, c["caption"], row.x + s(10), row.y + s(21), max_w=row.w - s(16), bold=False)
        hits["rows"].append((row.clip(area), layout["id"]))
    screen.set_clip(previous)
    if win._ul_max_scroll:
        track = pygame.Rect(area.right - s(5), area.y + 1, s(4), area.h - 2)
        total = area.h + win._ul_max_scroll
        thumb_h = max(s(16), int(track.h * area.h / total))
        thumb_y = track.y + int((track.h - thumb_h) * win._ul_scroll / win._ul_max_scroll)
        pygame.draw.rect(screen, c["steel_dim"], (track.x, thumb_y, track.w, thumb_h))


def _draw_info(win, screen, r):
    c = _colours
    info = r["info"]
    size = window_size(win)
    now = f"Window now: {size[0]} x {size[1]}" if size else "Window size not known yet"
    _blit_text(win, screen, now, 10, c["text"], info.x, info.y, max_w=info.w)
    saved = selected(win)
    if saved is None:
        line, colour = "Save the layout you have now; switch with LOAD (or double-click).", c["caption"]
    elif size is not None and tuple(saved["size"]) == size:
        line, colour = "Made for this window size: LOAD puts every window back exactly.", c["ok"]
    else:
        line, colour = (f"Made at {saved['size'][0]} x {saved['size'][1]}: LOAD fits it to this window.", c["amber"])
    _blit_text(win, screen, line, 10, colour, info.x, info.y + win._s(15), max_w=info.w, bold=False)


# ── input ────────────────────────────────────────────────────────────────────

def _handle_key(win, event) -> bool:
    box = win._ul_input
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
        box["text"] = win._apply_text_edit(FIELD_KEY, box["text"], event, max_length=ui_layouts.MAX_NAME_LEN, owner=box).text
    return True


def _owns(win, pos, focus: bool = False) -> bool:
    """Does the window own the pointer at *pos*? ``focus`` raises it above the other windows (a press on it)."""
    manager = getattr(win, "_gui_windows", None)
    return manager is not None and manager.route_pointer(pos, focus=focus).owner == ID


def _click(win, pos) -> None:
    hits = win._ul_hits
    r = hits.get("rects")
    if not r:
        return
    box = win._ul_input
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
        begin_input(win, "save")
        return
    for key, action, needs_selection in (("load", load_selected, True), ("undo", undo, False),
                                         ("update", update_selected, True), ("rename", lambda w: begin_input(w, "rename"), True),
                                         ("delete", delete_selected, True)):
        if r[key].collidepoint(pos):
            if (selected(win) is not None) if needs_selection else (win._ul_undo is not None):
                action(win)
            return
    for rect, layout_id in hits.get("rows", ()):
        if rect.collidepoint(pos):
            now = time.monotonic()
            last = win._ul_last_click
            select(win, layout_id)
            if last is not None and last[0] == layout_id and now - last[1] <= DOUBLE_CLICK_S:
                win._ul_last_click = None
                load_selected(win)
            else:
                win._ul_last_click = (layout_id, now)
            return


def _begin_drag(win, pos) -> bool:
    r = win._ul_hits.get("rects")
    rect = win._ul_rect
    if not r or rect is None:
        return False
    edges = wg.resize_zone(rect, pos, win._s(6), win._s(16))
    if edges:
        kind = "resize"
    elif r["header"].collidepoint(pos) and not r["close"].collidepoint(pos):
        kind = "move"
    else:
        return False
    win._ul_drag = {"kind": kind, "edges": edges, "start": pygame.Rect(rect), "origin": tuple(pos)}
    return True


def _drag(win, pos) -> None:
    drag = win._ul_drag
    size = window_size(win) or (1280, 720)
    dx, dy = pos[0] - drag["origin"][0], pos[1] - drag["origin"][1]
    if drag["kind"] == "move":
        win._ul_rect = wg.moved_rect(drag["start"], dx, dy, *size)
    else:
        min_w, min_h = min_size(win)
        win._ul_rect = wg.resized_rect(drag["start"], drag["edges"], dx, dy, size[0], size[1], min_w, min_h)


def _scroll(win, pos, amount: int) -> bool:
    r = win._ul_hits.get("rects")
    if not r:
        return False
    if r["list"].collidepoint(pos):
        win._ul_scroll = max(0, min(win._ul_max_scroll, win._ul_scroll + win._s(36) * -amount))
    return True


def _handle(win, event) -> bool:
    kind = event.type
    drag = win._ul_drag
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
            win._ul_drag = None
            return False
        _drag(win, event.pos)
        return True
    if kind == pygame.MOUSEBUTTONUP and event.button == 1 and drag is not None:
        _drag(win, event.pos)
        win._ul_drag = None
        return True
    if kind == pygame.MOUSEBUTTONUP:
        if win._ul_input is not None:
            # This filter runs before the game's event loop, so a release over this window never reaches the text box's
            # drag-select: end it here (the call does not use the release up).
            win._text_field_drag_event(event)
        return _owns(win, event.pos)
    if kind == pygame.MOUSEWHEEL:
        pos = pygame.mouse.get_pos()
        if _owns(win, pos):
            return _scroll(win, pos, int(getattr(event, "y", 0)))
    return False


def filter_events(win, events):
    """Called once per frame (Client.py) with the frame's events: takes the ones that belong to the window out of the list,
    so the game's own handlers never see them. Total."""
    if not _enabled():
        return events
    try:
        _ensure(win)
        _manager_owner(win)
        if not win._ul_open:
            return events
        kept = [event for event in events if not _handle(win, event)]
        return kept if len(kept) != len(events) else events
    except Exception:
        _switch_off("filter_events()")
        return events


# ── hooks ────────────────────────────────────────────────────────────────────

def queue(win, frame) -> None:
    """Called once per frame (Client.py) before the retained windows are flushed: note the screen size, then (open) tick and
    queue the draw."""
    if not _enabled():
        return
    try:
        _ensure(win)
        screen = frame.screen
        win._ul_size = tuple(screen.get_size())
        if not win._ul_open:
            return
        tick(win)
        win._gui_windows.queue_draw(
            ID, lambda: draw(win, screen), lambda: win._ul_rect, layer=GuiLayer.WINDOW, focusable=True,
            visible=lambda: bool(win._ul_open and _enabled()))
        _manager_owner(win)
        if win._ul_rect is None:
            window_rect(win, *screen.get_size())
    except Exception:
        _switch_off("queue()")


# ── the chat command ─────────────────────────────────────────────────────────

_HELP = "/uilayout  (window)   |   /uilayout list   |   /uilayout save <name>   |   /uilayout load <name>"
_INFO, _OK, _WARN = (100, 200, 255), (120, 220, 150), (255, 180, 60)


def handle_command(win, text: str = "") -> None:
    """``/uilayout`` and ``/uilayouts`` from the chat box: *text* is the whole line the player typed."""
    if not _enabled():
        win.chat_log("UI layouts are switched off.", _WARN)
        return
    try:
        _ensure(win)
        parts = (text or "").strip().split(None, 2)          # ["/uilayout", "save", "My layout"]
        verb = parts[1].lower() if len(parts) > 1 else ""
        name = parts[2].strip() if len(parts) > 2 else ""
        store = win._ul_store
        if not verb:
            toggle(win)
        elif verb in ("list", "ls"):
            if not store.loaded:
                win.chat_log("Still loading the saved layouts; try again in a moment.", _WARN)
            elif not store.layouts:
                win.chat_log("No UI layouts saved yet.  " + _HELP, _INFO)
            else:
                size = window_size(win)
                for layout in store.layouts:
                    here = "   <- this window size" if size is not None and tuple(layout["size"]) == size else ""
                    win.chat_log(f"  {layout['name']}   {layout['size'][0]} x {layout['size'][1]}{here}", _INFO)
        elif verb == "save":
            wanted = ui_layouts.clean_name(name) or default_name(win)
            existing = store.find(wanted)
            exact = existing is not None and existing["name"].lower() == wanted.lower()   # find() also matches a prefix
            ok, message = save_current(win, wanted, target_id=existing["id"] if exact else None)
            win.chat_log(message, _OK if ok else _WARN)
        elif verb in ("load", "use"):
            layout = store.find(name)
            if layout is None:
                win.chat_log(f"No UI layout called '{name}'. /uilayout list shows them." if name else "Usage: /uilayout load <name>",
                             _WARN)
            else:
                win._ul_selected = layout["id"]
                ok, message = apply_layout(win, layout)
                win.chat_log(message, _OK if ok else _WARN)
        else:
            win.chat_log(_HELP, _INFO)
    except Exception:
        _switch_off("handle_command()")


# ── install ──────────────────────────────────────────────────────────────────

def install(window_cls, client_globals=None) -> bool:
    """Take the palette from the game's render_mixin and, given Client.py's ``globals()``, wrap the flight-key text gate so
    typing a layout name never flies the ship. Idempotent; True if hooked."""
    global _colours
    if window_cls.__dict__.get("_ui_layouts_installed"):
        return False
    _colours = _palette(sys.modules.get("render_mixin"))
    if client_globals is not None and "_flight_text_entry_active" in client_globals:
        gate = client_globals["_flight_text_entry_active"]

        def flight_text_entry_active(**kwargs):
            return bool(gate(**kwargs) or any_text_focused())

        flight_text_entry_active.__wrapped__ = gate
        client_globals["_flight_text_entry_active"] = flight_text_entry_active
    window_cls._ui_layouts_installed = True
    return True

"""quick_bars_window.py - the QUICK BARS window: the KEYS tab (every action's key) and the BARS tab (the extra quick action bars).

Open it from the HUD menu (QUICK BARS), with ``/keys`` (the KEYS tab) or with ``/bars`` (the BARS tab).

KEYS. Every action that can have a key is listed with the combo it has now (amber when you changed it, dim when nothing is bound):
the game's quick bar slots, its hotkeys, and the ten slots of every extra bar. Click a row, then press the new key, with Ctrl, Shift or
Alt held if the binding should need them. Esc cancels, Backspace unbinds. A combo has one action: binding one that another action has
takes it from that action, and the status line says so. UNBIND and RESET act on the selected row, RESET ALL on every row (a second
click confirms).

BARS. Your extra quick action bars (each character has its own set): ADD BAR makes one, SHOW / HIDE, RENAME, RESET PLACE and DELETE (a
second click confirms) act on the selected bar. A bar is dragged by its padding or the gaps between its slots; items are dragged onto its
slots from the inventory, a slot is fired by clicking it or by its key, right-click clears it.

``/keys`` and ``/bars`` open the window; ``/keys list`` lists what you changed, ``/keys reset`` puts every default back, ``/bars list``
lists the bars and ``/bars add <name>``, ``/bars show <name>`` and ``/bars hide <name>`` work from chat, for when the window is out of reach.

The window and the vanilla lines that open it (the HUD menu row, the commands), draw it (one queue call before the retained windows are
flushed) and feed it events (one filter call after the event pump, which also hands the extra bars their mouse events) are hooked in from
Client.py by the mod's patch; every function the vanilla code calls is total: an error switches the window off for the session.
``STAR_EMPIRE_NO_QUICK_BARS=1`` turns it off (``STAR_EMPIRE_NO_QUICK_KEYS=1`` only the key handling, ``STAR_EMPIRE_NO_EXTRA_BARS=1``
only the extra bars).
"""

from __future__ import annotations

import logging
import os
import sys
import time
import weakref

import pygame

import bars as bars_model
import extra_bars
import keybinds as kb
import quick_keys
import window_geometry as wg
from gui_window_manager import GuiLayer

logger = logging.getLogger(__name__)

ID = "quick_bars"
FIELD_KEY = "quick_bars_name"       # the name box's TextField in the window's text_field_host registry
ENV_OFF = "STAR_EMPIRE_NO_QUICK_BARS"
TABS = (("keys", "KEYS"), ("bars", "BARS"))

_MIN_W, _MIN_H = 420, 320
_DEFAULT_W, _DEFAULT_H = 460, 540
ROW_H, GROUP_H, BAR_ROW_H = 24, 22, 40
CONFIRM_S = 3.0
STATUS_S = 6.0

_failed = False
_windows: list = []            # weak references to every window that has used the feature


def _switch_off(where: str) -> None:
    global _failed
    if not _failed:
        _failed = True
        logger.exception("quick_bars_window: switched off for this session (%s)", where)


def _enabled() -> bool:
    return not _failed and not os.environ.get(ENV_OFF)


def reset_for_tests() -> None:
    global _failed
    _failed = False
    _windows.clear()                # a window an earlier test left waiting for a key must not hold the flight keys


# ── window state ─────────────────────────────────────────────────────────────

def _ensure(win) -> None:
    if "_qb_ready" in win.__dict__:
        return
    win._qb_ready = True
    _windows.append(weakref.ref(win))
    win._qb_open = False
    win._qb_tab = "keys"
    win._qb_rect = None
    win._qb_selected = None         # a key action id (KEYS tab)
    win._qb_bar_selected = None     # an extra bar id (BARS tab)
    win._qb_capture = None          # the action id whose new key is being waited for
    win._qb_input = None            # {"mode": "add"|"rename", "text": str, "target": id | None}; caret / selection live in the
                                    # name box's TextField (FIELD_KEY, owned by this dict)
    win._qb_confirm_all = 0.0       # monotonic deadline of an armed RESET ALL
    win._qb_confirm_bar = None      # (bar id, monotonic deadline) of an armed DELETE
    win._qb_status = None           # (text, kind, monotonic deadline)
    win._qb_drag = None
    win._qb_hits = {}
    win._qb_scroll = 0
    win._qb_max_scroll = 0
    win._qb_size = None


def is_open(win) -> bool:
    if not _enabled():
        return False
    try:
        _ensure(win)
        return bool(win._qb_open)
    except Exception:
        _switch_off("is_open()")
        return False


def capturing(win) -> bool:
    """Is the window waiting for a key, or is a bar's name being typed? (Keeps the flight keys quiet while it is.)"""
    return bool(is_open(win) and (win._qb_capture is not None or win._qb_input is not None))


def any_capturing() -> bool:
    return any(capturing(w) for w in (ref() for ref in list(_windows)) if w is not None)


def open_window(win, tab: "str | None" = None) -> None:
    _ensure(win)
    win._qb_open = True
    if tab in dict(TABS):
        win._qb_tab = tab
    if win._qb_selected is None:
        win._qb_selected = kb.CATALOG[0].id
    _select_first_bar(win)
    _manager_owner(win)


def close_window(win) -> None:
    _ensure(win)
    win._qb_open = False
    win._qb_capture = None
    win._qb_input = None
    win._qb_drag = None
    win._qb_confirm_all = 0.0
    win._qb_confirm_bar = None


def toggle(win, tab: "str | None" = None) -> None:
    """Open the window (on *tab*, or where it was), or close it when it is already open there."""
    if is_open(win) and (tab is None or win._qb_tab == tab):
        close_window(win)
    else:
        open_window(win, tab)


def set_tab(win, tab: str) -> None:
    if tab in dict(TABS) and tab != win._qb_tab:
        win._qb_tab = tab
        win._qb_capture = None
        win._qb_input = None
        win._qb_confirm_all = 0.0
        win._qb_confirm_bar = None
        win._qb_scroll = 0
        _select_first_bar(win)


def _manager_owner(win) -> None:
    manager = getattr(win, "_gui_windows", None)
    if manager is not None:
        manager._qb_owner = weakref.ref(win)


def _say(win, text: str, kind: str = "info") -> None:
    win._qb_status = (text, kind, time.monotonic() + STATUS_S)


def _table():
    return quick_keys.table()


# ── the KEYS tab: the bindings ───────────────────────────────────────────────

def view_rows(catalog=kb.CATALOG) -> list:
    """The list as ``(kind, data)``: a ``group`` title, then its ``action`` rows."""
    rows: list = []
    group = None
    for action in catalog:
        if action.group != group:
            group = action.group
            rows.append(("group", group))
        rows.append(("action", action))
    return rows


def view_height(s, catalog=kb.CATALOG) -> int:
    return sum(s(GROUP_H if kind == "group" else ROW_H) for kind, _data in view_rows(catalog))


def begin_capture(win, action_id: str) -> None:
    win._qb_selected = action_id
    win._qb_capture = action_id
    win._qb_confirm_all = 0.0


def cancel_capture(win) -> None:
    win._qb_capture = None


def bind(win, action_id: str, combo: "kb.Combo | None") -> None:
    """Give an action a combo (None unbinds), say what happened, leave capture mode."""
    action = _table().by_id[action_id]
    label = f"{action.group}: {action.label}" if action.kind == "barslot" else action.label
    try:
        displaced = quick_keys.set_binding(action_id, combo)
    except ValueError:
        _say(win, f"{kb.format_combo(combo)} is used by the game and cannot be bound.", "warn")
        return
    win._qb_capture = None
    if combo is None:
        _say(win, f"{label}: unbound.", "ok")
    elif displaced:
        other = displaced[0]
        name = f"{other.group}: {other.label}" if other.kind == "barslot" else other.label
        _say(win, f"{label}: {kb.format_combo(combo)}. {name} had it and is now unbound.", "warn")
    else:
        _say(win, f"{label}: {kb.format_combo(combo)}.", "ok")


def reset_selected(win) -> None:
    action = _table().by_id.get(win._qb_selected)
    if action is None:
        return
    quick_keys.reset_binding(action.id)
    win._qb_capture = None
    _say(win, f"{action.label}: back to {kb.format_combo(action.default)}.", "ok")


def unbind_selected(win) -> None:
    if win._qb_selected in _table().by_id:
        bind(win, win._qb_selected, None)


def reset_all(win) -> None:
    """The first click arms RESET ALL, a second one within CONFIRM_S does it."""
    now = time.monotonic()
    if win._qb_confirm_all and now <= win._qb_confirm_all:
        win._qb_confirm_all = 0.0
        quick_keys.reset_all()
        win._qb_capture = None
        _say(win, "Every key is back to the default.", "ok")
    else:
        win._qb_confirm_all = now + CONFIRM_S


# ── the BARS tab: the extra bars ─────────────────────────────────────────────

def _runtime(win):
    return extra_bars.ensure_loaded(win)


def bar_list(win) -> list:
    return list(_runtime(win).barset)


def selected_bar(win):
    return _runtime(win).barset.get(win._qb_bar_selected)


def _select_first_bar(win) -> None:
    if win._qb_tab == "bars" and selected_bar(win) is None:
        bars = bar_list(win)
        win._qb_bar_selected = bars[0].id if bars else None


def mods_text(bar) -> str:
    return "+".join(m.title() for m in kb.MODS if m in bar.mods)


def begin_input(win, mode: str) -> None:
    """Open the name box for a new bar (``add``) or a new name for the selected one (``rename``)."""
    rt = _runtime(win)
    if mode == "add":
        if not rt.account or len(rt.barset) >= bars_model.MAX_BARS:
            return
        target, text = None, rt.barset.unique_name(f"Bar {len(rt.barset) + 2}")
    else:
        bar = selected_bar(win)
        if bar is None:
            return
        target, text = bar.id, bar.name
    box = {"mode": mode, "text": text[:bars_model.MAX_NAME_LEN], "target": target}
    win._qb_input = box
    win._text_cursor_to_end(FIELD_KEY, box["text"], owner=box)          # a fresh TextField per opening, caret after the text
    win._qb_confirm_bar = None


def cancel_input(win) -> None:
    win._qb_input = None


def commit_input(win) -> None:
    box = win._qb_input
    if box is None:
        return
    name = bars_model.clean_name(box["text"])
    if not name:
        _say(win, "A bar needs a name.", "warn")
        return
    win._qb_input = None
    if box["mode"] == "rename":
        final = extra_bars.rename_bar(win, box["target"], name)
        if final is not None:
            _say(win, f"Renamed to '{final}'.", "ok")
        return
    bar = extra_bars.add_bar(win, name)
    if bar is None:
        _say(win, "Could not add a bar.", "warn")
        return
    win._qb_bar_selected = bar.id
    _say(win, f"Added '{bar.name}': {mods_text(bar)}+1 ... 0 fire its slots.", "ok")


def toggle_visible(win) -> None:
    bar = selected_bar(win)
    if bar is not None:
        extra_bars.set_visible(win, bar.id, not bar.visible)
        _say(win, f"'{bar.name}' is {'shown' if bar.visible else 'hidden'}.", "ok")


def reset_place(win) -> None:
    bar = selected_bar(win)
    if bar is not None:
        extra_bars.reset_place(win, bar.id)
        _say(win, f"'{bar.name}' is back above the game's bar.", "ok")


def delete_selected(win) -> None:
    """The first click arms DELETE, a second one within CONFIRM_S on the same bar does it."""
    bar = selected_bar(win)
    if bar is None:
        return
    now = time.monotonic()
    armed = win._qb_confirm_bar
    if armed is not None and armed[0] == bar.id and now <= armed[1]:
        win._qb_confirm_bar = None
        extra_bars.remove_bar(win, bar.id)
        win._qb_bar_selected = None
        _select_first_bar(win)
        _say(win, f"Deleted '{bar.name}' ({bar.filled} slots were filled).", "ok")
    else:
        win._qb_confirm_bar = (bar.id, now + CONFIRM_S)


def select_bar(win, bar_id) -> None:
    if bar_id != win._qb_bar_selected:
        win._qb_bar_selected = bar_id
        win._qb_confirm_bar = None


def button_enabled(win, name: str) -> bool:
    """Whether a button has anything to do (drawn dim and ignored when not)."""
    if name in ("unbind", "reset", "reset_all"):
        table = _table()
        chosen = win._qb_selected in table.by_id
        if name == "unbind":
            return chosen and table.combo(win._qb_selected) is not None
        if name == "reset":
            return chosen and table.is_changed(win._qb_selected)
        return not table.is_default
    rt = _runtime(win)
    if name == "add":
        return bool(rt.account) and len(rt.barset) < bars_model.MAX_BARS
    return selected_bar(win) is not None


def tick(win) -> None:
    extra_bars.ensure_loaded(win)               # the key list has the account's extra bars in it before the first frame is drawn
    now = time.monotonic()
    if win._qb_status is not None and now > win._qb_status[2]:
        win._qb_status = None
    if win._qb_confirm_all and now > win._qb_confirm_all:
        win._qb_confirm_all = 0.0
    if win._qb_confirm_bar is not None and now > win._qb_confirm_bar[1]:
        win._qb_confirm_bar = None
    if win._qb_tab == "bars" and selected_bar(win) is None:
        win._qb_bar_selected = None
        _select_first_bar(win)


# ── geometry ─────────────────────────────────────────────────────────────────

def min_size(win) -> tuple:
    return win._s(_MIN_W), win._s(_MIN_H)


def window_rect(win, screen_w: int, screen_h: int) -> pygame.Rect:
    rect = win._qb_rect
    if rect is None:
        width = min(win._s(_DEFAULT_W), screen_w)
        height = min(win._s(_DEFAULT_H), screen_h)
        rect = pygame.Rect((screen_w - width) // 2, max(0, (screen_h - height) // 3), width, height)
    min_w, min_h = min_size(win)
    rect = wg.clamp_rect(rect, screen_w, screen_h, min_w, min_h)
    win._qb_rect = rect
    return rect


def layout_rects(rect: pygame.Rect, s, tab: str = "keys") -> dict:
    """Every rectangle of the window for a tab, from the window rectangle alone (``s`` scales UI pixels)."""
    pad, gap = s(10), s(6)
    header_h, status_h, button_h, tabs_h = s(34), s(20), s(26), s(24)
    body = pygame.Rect(rect.x + pad, rect.y + header_h + pad, rect.w - pad * 2, rect.h - header_h - pad * 2 - status_h)
    tabs = pygame.Rect(body.x, body.y, body.w, tabs_h)
    tab_w = min(s(110), (tabs.w - gap) // 2)
    top = tabs.bottom + gap
    buttons_y = body.bottom - button_h
    info = pygame.Rect(body.x, buttons_y - gap - s(32), body.w, s(32))
    close = pygame.Rect(rect.right - pad - s(22), rect.y + (header_h - s(20)) // 2, s(22), s(20))
    out = {"header": pygame.Rect(rect.x, rect.y, rect.w, header_h), "close": close, "body": body, "tabs": tabs,
           "tab_keys": pygame.Rect(tabs.x, tabs.y, tab_w, tabs_h),
           "tab_bars": pygame.Rect(tabs.x + tab_w + gap, tabs.y, tab_w, tabs_h), "info": info,
           "status": pygame.Rect(rect.x + pad, rect.bottom - pad - status_h + pad // 2, rect.w - pad * 2, status_h)}
    if tab == "keys":
        third = (body.w - gap * 2) // 3
        unbind = pygame.Rect(body.x, buttons_y, third, button_h)
        reset = pygame.Rect(unbind.right + gap, buttons_y, third, button_h)
        out.update(list=pygame.Rect(body.x, top, body.w, info.y - gap - top), unbind=unbind, reset=reset,
                   reset_all=pygame.Rect(reset.right + gap, buttons_y, body.right - reset.right - gap, button_h))
        return out
    add = pygame.Rect(body.x, top, body.w, s(28))
    quarter = (body.w - gap * 3) // 4
    visible = pygame.Rect(body.x, buttons_y, quarter, button_h)
    rename = pygame.Rect(visible.right + gap, buttons_y, quarter, button_h)
    place = pygame.Rect(rename.right + gap, buttons_y, quarter, button_h)
    delete = pygame.Rect(place.right + gap, buttons_y, body.right - place.right - gap, button_h)
    ok = pygame.Rect(add.right - s(44) - s(40) - gap, add.y, s(40), add.h)
    cancel = pygame.Rect(add.right - s(44), add.y, s(44), add.h)
    out.update(add=add, field=pygame.Rect(add.x, add.y, ok.x - gap - add.x, add.h), ok=ok, cancel=cancel,
               list=pygame.Rect(body.x, add.bottom + gap, body.w, info.y - gap - (add.bottom + gap)),
               bar_visible=visible, bar_rename=rename, bar_place=place, bar_delete=delete)
    return out


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
    cache = win.__dict__.setdefault("_qb_text_cache", {})
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
    tab = win._qb_tab
    r = layout_rects(rect, s, tab)
    mouse = pygame.mouse.get_pos()
    hits: dict = {"rects": r, "rows": [], "tab": tab}
    win._qb_hits = hits

    wg.draw_plate(screen, rect.x, rect.y, rect.w, rect.h, r["header"].h, wg.plate_chamfer(rect.h, s(20)))
    _blit_text(win, screen, "Q U I C K   B A R S", 12, c["text"], rect.x + s(20), rect.y + s(10))
    hot = r["close"].collidepoint(mouse)
    pygame.draw.rect(screen, (64, 22, 28) if hot else (12, 18, 30), r["close"])
    pygame.draw.rect(screen, c["warn"] if hot else c["steel"], r["close"], 1)
    close_x = _text_surface(win, "X", 12, c["value"])
    screen.blit(close_x, (r["close"].centerx - close_x.get_width() // 2, r["close"].centery - close_x.get_height() // 2))

    for key, label in TABS:
        tab_rect = r["tab_" + key]
        chosen = key == tab
        pygame.draw.rect(screen, (18, 58, 94) if chosen else (c["plate"] if not tab_rect.collidepoint(mouse) else (14, 29, 43)), tab_rect)
        pygame.draw.rect(screen, c["accent"] if chosen else c["steel"], tab_rect, 1)
        text = _text_surface(win, label, 10, c["value"] if chosen else c["text"])
        screen.blit(text, (tab_rect.centerx - text.get_width() // 2, tab_rect.centery - text.get_height() // 2))

    if tab == "keys":
        _draw_keys(win, screen, r, hits, mouse)
    else:
        _draw_bars(win, screen, r, hits, mouse)

    status = win._qb_status
    if status is not None:
        colour = {"ok": c["ok"], "warn": c["amber"], "info": c["text"]}.get(status[1], c["text"])
        _blit_text(win, screen, status[0], 10, colour, r["status"].x, r["status"].y, max_w=r["status"].w)
    grip = rect.bottomright
    for k in range(3):
        offset = s(5) + k * s(4)
        pygame.draw.line(screen, c["steel"], (grip[0] - offset - s(4), grip[1] - s(3)), (grip[0] - s(3), grip[1] - offset - s(4)))


def _scrollbar(win, screen, area, scroll, max_scroll):
    c = _colours
    s = win._s
    track = pygame.Rect(area.right - s(5), area.y + 1, s(4), area.h - 2)
    total = area.h + max_scroll
    thumb_h = max(s(16), int(track.h * area.h / total))
    thumb_y = track.y + int((track.h - thumb_h) * scroll / max_scroll)
    pygame.draw.rect(screen, c["steel_dim"], (track.x, thumb_y, track.w, thumb_h))


# the KEYS tab

def _draw_keys(win, screen, r, hits, mouse):
    c = _colours
    s = win._s
    area = r["list"]
    table = _table()
    pygame.draw.rect(screen, c["canvas"], area)
    pygame.draw.rect(screen, c["steel"], area, 1)
    win._qb_max_scroll = max(0, view_height(s, table.catalog) - area.h)
    win._qb_scroll = max(0, min(win._qb_scroll, win._qb_max_scroll))
    previous = screen.get_clip()
    screen.set_clip(area.inflate(-2, -2).clip(previous))
    y = area.y + 1 - win._qb_scroll
    cell_w = s(150)
    right = area.right - (s(10) if win._qb_max_scroll else s(4))
    for kind, data in view_rows(table.catalog):
        row_h = s(GROUP_H if kind == "group" else ROW_H)
        row = pygame.Rect(area.x + 1, y, area.w - 2, row_h)
        y += row_h
        if row.bottom < area.top or row.top > area.bottom:
            continue
        if kind == "group":
            _blit_text(win, screen, data.upper(), 10, c["accent"], row.x + s(10), row.y + s(6), max_w=row.w - s(20))
            pygame.draw.line(screen, c["steel_dim"], (row.x + s(8), row.bottom - 2), (row.right - s(8), row.bottom - 2))
            continue
        action = data
        chosen = action.id == win._qb_selected
        waiting = win._qb_capture == action.id
        hovered = row.collidepoint(mouse) and area.collidepoint(mouse)
        if chosen or hovered:
            pygame.draw.rect(screen, (18, 58, 94) if chosen else (14, 29, 43), row)
        if chosen:
            pygame.draw.rect(screen, c["accent"], (row.x, row.y, s(3), row.h))
        _blit_text(win, screen, action.label, 11, c["value"] if chosen else c["text"], row.x + s(10), row.y + s(6),
                   max_w=row.w - cell_w - s(30), bold=False)
        cell = pygame.Rect(right - cell_w, row.y + s(2), cell_w, row.h - s(4))
        combo = table.combo(action.id)
        if waiting:
            pygame.draw.rect(screen, c["header"], cell)
            pygame.draw.rect(screen, c["accent"], cell, 1)
            blink = int(time.monotonic() * 2) % 2 == 0
            text, colour = ("press a key..." if blink else "press a key"), c["accent"]
        else:
            pygame.draw.rect(screen, c["plate"], cell)
            pygame.draw.rect(screen, c["steel"] if chosen else c["steel_dim"], cell, 1)
            if combo is None:
                text, colour = "unbound", c["caption"]
            else:
                text, colour = kb.format_combo(combo), (c["amber"] if table.is_changed(action.id) else c["value"])
        label = _text_surface(win, text, 10, colour)
        screen.blit(label, (cell.centerx - label.get_width() // 2, cell.centery - label.get_height() // 2))
        hits["rows"].append((row.clip(area), action.id))
    screen.set_clip(previous)
    if win._qb_max_scroll:
        _scrollbar(win, screen, area, win._qb_scroll, win._qb_max_scroll)

    info = r["info"]
    action = table.by_id.get(win._qb_selected)
    if win._qb_capture is not None:
        first, colour1 = "Press the new key, with Ctrl / Shift / Alt if it should need them.", c["accent"]
        second, colour2 = "Esc cancels, Backspace unbinds.", c["caption"]
    elif action is None:
        first, colour1 = "Click an action, then press its new key.", c["text"]
        second, colour2 = "", c["caption"]
    else:
        first, colour1 = f"{action.label}: {kb.format_combo(table.combo(action.id))}", c["text"]
        second = f"Default: {kb.format_combo(action.default)}" if action.default is not None else "Default: not bound"
        colour2 = c["caption"]
    _blit_text(win, screen, first, 10, colour1, info.x, info.y, max_w=info.w)
    if second:
        _blit_text(win, screen, second, 10, colour2, info.x, info.y + s(15), max_w=info.w, bold=False)

    _button(win, screen, r["unbind"], "UNBIND", enabled=button_enabled(win, "unbind"), hot=r["unbind"].collidepoint(mouse))
    _button(win, screen, r["reset"], "RESET", enabled=button_enabled(win, "reset"), hot=r["reset"].collidepoint(mouse))
    armed = bool(win._qb_confirm_all)
    _button(win, screen, r["reset_all"], "SURE?" if armed else "RESET ALL", enabled=button_enabled(win, "reset_all"),
            hot=r["reset_all"].collidepoint(mouse), armed=armed)


# the BARS tab

def _draw_bars(win, screen, r, hits, mouse):
    c = _colours
    s = win._s
    rt = _runtime(win)
    if win._qb_input is not None:
        box = win._qb_input
        pygame.draw.rect(screen, c["header"], r["field"])
        pygame.draw.rect(screen, c["accent"], r["field"], 1)
        win._draw_text_field(FIELD_KEY, box["text"], screen, win._instrument_font(11, bold=True),
                             r["field"].inflate(-s(12), 0), c["value"], hit_rect=r["field"], owner=box)
        _button(win, screen, r["ok"], "OK", hot=r["ok"].collidepoint(mouse))
        _button(win, screen, r["cancel"], "ESC", hot=r["cancel"].collidepoint(mouse))
    else:
        _button(win, screen, r["add"], "ADD BAR", enabled=button_enabled(win, "add"), hot=r["add"].collidepoint(mouse))

    area = r["list"]
    pygame.draw.rect(screen, c["canvas"], area)
    pygame.draw.rect(screen, c["steel"], area, 1)
    bars = list(rt.barset)
    row_h = s(BAR_ROW_H)
    win._qb_max_scroll = max(0, len(bars) * row_h - area.h)
    win._qb_scroll = max(0, min(win._qb_scroll, win._qb_max_scroll))
    previous = screen.get_clip()
    screen.set_clip(area.inflate(-2, -2).clip(previous))
    if not bars:
        message = "Log in to add bars." if not rt.account else "No extra bars yet"
        _blit_text(win, screen, message, 11, c["caption"], area.x + s(10), area.y + s(10))
        if rt.account:
            _blit_text(win, screen, "ADD BAR, then drag items from the inventory onto its slots.", 10, c["caption"],
                       area.x + s(10), area.y + s(28), max_w=area.w - s(16), bold=False)
    for index, bar in enumerate(bars):
        row = pygame.Rect(area.x + 1, area.y + 1 + index * row_h - win._qb_scroll, area.w - 2, row_h)
        if row.bottom < area.top or row.top > area.bottom:
            continue
        chosen = bar.id == win._qb_bar_selected
        hovered = row.collidepoint(mouse) and area.collidepoint(mouse)
        if chosen or hovered:
            pygame.draw.rect(screen, (18, 58, 94) if chosen else (14, 29, 43), row)
        if chosen:
            pygame.draw.rect(screen, c["accent"], (row.x, row.y, s(3), row.h))
        tag = "SHOWN" if bar.visible else "HIDDEN"
        tag_w = _blit_text(win, screen, tag, 9, c["ok"] if bar.visible else c["caption"], row.right - s(8), row.y + s(5),
                           right=True) + s(8)
        _blit_text(win, screen, bar.name, 11, c["value"] if chosen else c["text"], row.x + s(10), row.y + s(4),
                   max_w=row.w - s(20) - tag_w)
        caption = f"{mods_text(bar)}+1 ... 0  -  {bar.filled} of {bars_model.SLOTS} slots"
        _blit_text(win, screen, caption, 9, c["caption"], row.x + s(10), row.y + s(21), max_w=row.w - s(16), bold=False)
        hits["rows"].append((row.clip(area), bar.id))
    screen.set_clip(previous)
    if win._qb_max_scroll:
        _scrollbar(win, screen, area, win._qb_scroll, win._qb_max_scroll)

    info = r["info"]
    bar = selected_bar(win)
    if not rt.account:
        first, second = "Each character has its own bars: log in first.", ""
    elif len(bars) >= bars_model.MAX_BARS:
        first, second = f"{bars_model.MAX_BARS} bars is the most.", "Delete one to make room for another."
    elif bar is None:
        first, second = "Make a bar, then drag it by its edge to where you want it.", ""
    else:
        first = (f"{bar.name}: drag its edge or the gaps between slots to move it." if bar.visible
                 else f"{bar.name} is hidden: it is not drawn and its keys do nothing.")
        second = "Its keys are on the KEYS tab. Right-click a slot to clear it."
    _blit_text(win, screen, first, 10, c["text"], info.x, info.y, max_w=info.w)
    if second:
        _blit_text(win, screen, second, 10, c["caption"], info.x, info.y + s(15), max_w=info.w, bold=False)

    armed = win._qb_confirm_bar is not None and bar is not None and win._qb_confirm_bar[0] == bar.id
    _button(win, screen, r["bar_visible"], "HIDE" if (bar is not None and bar.visible) else "SHOW",
            enabled=bar is not None, hot=r["bar_visible"].collidepoint(mouse))
    _button(win, screen, r["bar_rename"], "RENAME", enabled=bar is not None, hot=r["bar_rename"].collidepoint(mouse))
    _button(win, screen, r["bar_place"], "RESET PLACE", enabled=bar is not None, hot=r["bar_place"].collidepoint(mouse))
    _button(win, screen, r["bar_delete"], "SURE?" if armed else "DELETE", enabled=bar is not None,
            hot=r["bar_delete"].collidepoint(mouse), armed=armed)


# ── input ────────────────────────────────────────────────────────────────────

def _handle_key(win, event) -> bool:
    """While a name is typed or a new key awaited every key press is the window's (so Esc, Enter and Backspace do not reach the
    game either)."""
    box = win._qb_input
    if box is not None:
        if event.type == pygame.KEYUP:
            return True
        key = event.key
        if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            commit_input(win)
        elif key == pygame.K_ESCAPE:
            cancel_input(win)
        else:
            # Typing, caret keys, selection and the clipboard are the shared TextField's (text_field_host.py).
            box["text"] = win._apply_text_edit(FIELD_KEY, box["text"], event, max_length=bars_model.MAX_NAME_LEN, owner=box).text
        return True
    if win._qb_capture is None:
        return False
    if event.type == pygame.KEYUP:
        return True
    key = event.key
    if key == pygame.K_ESCAPE:
        cancel_capture(win)
        _say(win, "Cancelled.", "info")
    elif key in (pygame.K_BACKSPACE, pygame.K_DELETE):
        bind(win, win._qb_capture, None)
    else:
        combo = kb.combo_from_event(key, getattr(event, "mod", 0))
        if combo is not None:                  # a modifier on its own: keep waiting
            bind(win, win._qb_capture, combo)
    return True


def _owns(win, pos, focus: bool = False) -> bool:
    manager = getattr(win, "_gui_windows", None)
    return manager is not None and manager.route_pointer(pos, focus=focus).owner == ID


def _click(win, pos) -> None:
    hits = win._qb_hits
    r = hits.get("rects")
    if not r:
        return
    if r["close"].collidepoint(pos):
        close_window(win)
        return
    box = win._qb_input
    if box is not None and hits.get("tab") == "bars":
        if r["ok"].collidepoint(pos):
            commit_input(win)
        elif r["cancel"].collidepoint(pos):
            cancel_input(win)
        elif r["field"].collidepoint(pos):
            win._press_text_field_at(FIELD_KEY, box["text"], pos, owner=box)       # caret to the press, a drag-select begins
        return
    for key, label in TABS:
        if r["tab_" + key].collidepoint(pos):
            set_tab(win, key)
            return
    if hits.get("tab") == "keys":
        _click_keys(win, pos, r, hits)
    else:
        _click_bars(win, pos, r, hits)


def _click_keys(win, pos, r, hits) -> None:
    for key, action in (("unbind", unbind_selected), ("reset", reset_selected), ("reset_all", reset_all)):
        if r[key].collidepoint(pos):
            if button_enabled(win, key):
                action(win)
            return
    for rect, action_id in hits.get("rows", ()):
        if rect.collidepoint(pos):
            begin_capture(win, action_id)
            return
    win._qb_capture = None                     # a click anywhere else in the window gives up waiting


def _click_bars(win, pos, r, hits) -> None:
    if r["add"].collidepoint(pos):
        if button_enabled(win, "add"):
            begin_input(win, "add")
        return
    for key, action in (("bar_visible", toggle_visible), ("bar_rename", lambda w: begin_input(w, "rename")),
                        ("bar_place", reset_place), ("bar_delete", delete_selected)):
        if r[key].collidepoint(pos):
            if selected_bar(win) is not None:
                action(win)
            return
    for rect, bar_id in hits.get("rows", ()):
        if rect.collidepoint(pos):
            select_bar(win, bar_id)
            return


def _begin_drag(win, pos) -> bool:
    r = win._qb_hits.get("rects")
    rect = win._qb_rect
    if not r or rect is None:
        return False
    edges = wg.resize_zone(rect, pos, win._s(6), win._s(16))
    if edges:
        kind = "resize"
    elif r["header"].collidepoint(pos) and not r["close"].collidepoint(pos):
        kind = "move"
    else:
        return False
    win._qb_drag = {"kind": kind, "edges": edges, "start": pygame.Rect(rect), "origin": tuple(pos)}
    return True


def _drag(win, pos) -> None:
    drag = win._qb_drag
    surface = pygame.display.get_surface()
    size = win.__dict__.get("_qb_size") or (surface.get_size() if surface is not None else (1280, 720))
    dx, dy = pos[0] - drag["origin"][0], pos[1] - drag["origin"][1]
    if drag["kind"] == "move":
        win._qb_rect = wg.moved_rect(drag["start"], dx, dy, *size)
    else:
        min_w, min_h = min_size(win)
        win._qb_rect = wg.resized_rect(drag["start"], drag["edges"], dx, dy, size[0], size[1], min_w, min_h)


def _scroll(win, pos, amount: int) -> bool:
    r = win._qb_hits.get("rects")
    if not r:
        return False
    if r["list"].collidepoint(pos):
        win._qb_scroll = max(0, min(win._qb_max_scroll, win._qb_scroll + win._s(36) * -amount))
    return True


def _handle(win, event) -> bool:
    kind = event.type
    drag = win._qb_drag
    if kind in (pygame.KEYDOWN, pygame.KEYUP):
        return _handle_key(win, event)
    if kind == pygame.MOUSEBUTTONDOWN:
        if not _owns(win, event.pos, focus=True):
            win._qb_capture = None             # a click on the game gives up waiting (and goes on to the game)
            return False
        if event.button == 1 and drag is None:
            if not _begin_drag(win, event.pos):
                _click(win, event.pos)
        return True
    if kind == pygame.MOUSEMOTION and drag is not None:
        if not event.buttons[0]:
            win._qb_drag = None
            return False
        _drag(win, event.pos)
        return True
    if kind == pygame.MOUSEBUTTONUP and event.button == 1 and drag is not None:
        _drag(win, event.pos)
        win._qb_drag = None
        return True
    if kind == pygame.MOUSEBUTTONUP:
        if win._qb_input is not None:
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
    """Called once per frame (Client.py) with the frame's events: takes the ones that belong to the window out of the list, and the
    mouse events of the extra bars, so the game's own handlers never see them. Total."""
    if not _enabled():
        return events
    try:
        _ensure(win)
        _manager_owner(win)
        window_open = win._qb_open
        kept = [event for event in events
                if not (window_open and _handle(win, event)) and not extra_bars.handle_event(win, event)]
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
        win._qb_size = tuple(screen.get_size())
        if not win._qb_open:
            return
        tick(win)
        win._gui_windows.queue_draw(
            ID, lambda: draw(win, screen), lambda: win._qb_rect, layer=GuiLayer.WINDOW, focusable=True,
            visible=lambda: bool(win._qb_open and _enabled()))
        _manager_owner(win)
        if win._qb_rect is None:
            window_rect(win, *screen.get_size())
    except Exception:
        _switch_off("queue()")


def draw_bars(win, frame) -> None:
    """Called once per frame (Client.py) right after the game's own bar: draws the extra bars (extra_bars.py). Total."""
    extra_bars.draw_bars(win, frame)


# ── the chat commands ────────────────────────────────────────────────────────

_HELP_KEYS = "/keys  (window)   |   /keys list   |   /keys reset"
_HELP_BARS = "/bars  (window)   |   /bars list   |   /bars add <name>   |   /bars show <name>   |   /bars hide <name>"
_INFO, _OK, _WARN = (100, 200, 255), (120, 220, 150), (255, 180, 60)


def _find_bar(win, text: str):
    """A bar by name: the exact name (any case), else the one name that starts with *text*."""
    wanted = bars_model.clean_name(text).lower()
    if not wanted:
        return None
    bars = bar_list(win)
    exact = [b for b in bars if b.name.lower() == wanted]
    if exact:
        return exact[0]
    starts = [b for b in bars if b.name.lower().startswith(wanted)]
    return starts[0] if len(starts) == 1 else None


def handle_command(win, text: str = "") -> None:
    """``/keys`` and ``/bars`` from the chat box: *text* is the whole line the player typed."""
    if not _enabled():
        win.chat_log("Quick bars are switched off.", _WARN)
        return
    try:
        _ensure(win)
        parts = (text or "").strip().split(None, 2)
        command = parts[0].lstrip("/").lower() if parts else "keys"
        verb = parts[1].lower() if len(parts) > 1 else ""
        rest = parts[2].strip() if len(parts) > 2 else ""
        if command in ("bars", "quickbars"):
            _bars_command(win, verb, rest)
        else:
            _keys_command(win, verb)
    except Exception:
        _switch_off("handle_command()")


def _keys_command(win, verb: str) -> None:
    if not verb:
        toggle(win, "keys")
    elif verb in ("list", "ls"):
        table = _table()
        changed = [a for a in table.catalog if table.is_changed(a.id)]
        if not changed:
            win.chat_log("Every key is the default.  " + _HELP_KEYS, _INFO)
        for action in changed:
            name = f"{action.group}: {action.label}" if action.kind == "barslot" else action.label
            win.chat_log(f"  {name}: {kb.format_combo(table.combo(action.id))}   (default {kb.format_combo(action.default)})", _INFO)
    elif verb == "reset":
        quick_keys.reset_all()
        win.chat_log("Every key is back to the default.", _OK)
    else:
        win.chat_log(_HELP_KEYS, _INFO)


def _bars_command(win, verb: str, rest: str) -> None:
    if not verb:
        toggle(win, "bars")
    elif verb in ("list", "ls"):
        bars = bar_list(win)
        if not bars:
            win.chat_log("No extra bars.  " + _HELP_BARS, _INFO)
        for bar in bars:
            state = "shown" if bar.visible else "hidden"
            win.chat_log(f"  {bar.name}   {mods_text(bar)}+1 ... 0   {bar.filled} of {bars_model.SLOTS} slots   {state}", _INFO)
    elif verb == "add":
        bar = extra_bars.add_bar(win, rest or None)
        if bar is None:
            win.chat_log(f"Could not add a bar (log in first; {bars_model.MAX_BARS} bars is the most).", _WARN)
        else:
            win.chat_log(f"Added '{bar.name}': {mods_text(bar)}+1 ... 0 fire its slots.", _OK)
    elif verb in ("show", "hide"):
        bar = _find_bar(win, rest)
        if bar is None:
            win.chat_log(f"No bar called '{rest}'. /bars list shows them." if rest else f"Usage: /bars {verb} <name>", _WARN)
        else:
            extra_bars.set_visible(win, bar.id, verb == "show")
            win.chat_log(f"'{bar.name}' is {'shown' if verb == 'show' else 'hidden'}.", _OK)
    else:
        win.chat_log(_HELP_BARS, _INFO)


# ── install ──────────────────────────────────────────────────────────────────

def install(window_cls, client_globals=None) -> bool:
    """Read the saved bindings and bars, hook the key handling's label cache key and the class-skill menu, take the palette from the
    game's render_mixin and, given Client.py's ``globals()``, wrap the flight-key text gate so a key pressed for a binding or a bar
    name never flies the ship. Idempotent; True if hooked."""
    global _colours
    if window_cls.__dict__.get("_quick_bars_installed"):
        return False
    _colours = _palette(sys.modules.get("render_mixin"))
    quick_keys.load()
    quick_keys.install(window_cls)
    extra_bars.install(window_cls)
    if client_globals is not None and "_flight_text_entry_active" in client_globals:
        gate = client_globals["_flight_text_entry_active"]

        def flight_text_entry_active(**kwargs):
            return bool(gate(**kwargs) or any_capturing())

        flight_text_entry_active.__wrapped__ = gate
        client_globals["_flight_text_entry_active"] = flight_text_entry_active
    window_cls._quick_bars_installed = True
    return True

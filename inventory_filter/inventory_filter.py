"""inventory_filter.py - filter and sort the ship inventories and the station Storage tab, with a side tab for the
advanced part (inventory_filter_model.py is the model; this is the state, the hooks and the tab).

Every ship inventory (yours, and each stored ship's window) and the player station's Storage tab gets a FILTER button
at the end of its search bar. It opens a TAB docked beside that window with:

  * SORT      - name / type / rarity / quantity (press again to turn it round, once more to switch it off);
  * SHOW      - everything, only what is fitted, or only what sits in the hold (Storage: equipped / in storage);
  * TYPE      - chips for the item categories that window actually holds (weapon, power, shield, plugin, ...);
  * RARITY    - chips for the tiers it holds, in the tier's colour;
  * LOADOUT   - pick a saved Ship Layout and the window lists only the parts of that setup, with what is here and
                what is missing; SELECT SHOWN then picks every listed hold row, ready to drag to another ship.

Right-click the FILTER button to clear that window's filters. The choices are kept per window while the client
runs (not saved to disk); the text search keeps working on top of them.

Hooked in from outside the vanilla code (``install`` is called once from Client.py): the ship inventory view function
every ship window shares is wrapped (the rows it gets are matched to their window by identity), and so are the two
search-bar footers that draw the button and the layer key that repaints the player's inventory. The Storage tab is
drawn inline by the game, so it has three small ``# MOD`` hooks of its own in render_mixin.py. The vanilla lines that
queue the tab's draw and feed it events (one filter call after the event pump) are marked ``# MOD`` in Client.py.
Every function the vanilla code calls is total: an error switches the feature off for the session and the game
carries on unfiltered. ``STAR_EMPIRE_NO_INVENTORY_FILTER=1`` turns it off.
"""

from __future__ import annotations

import logging
import os
import time
import weakref

import pygame

import inventory_filter_model as model
from gui_window_manager import GuiLayer
from inventory_filter_model import (FilterSpec, RARITIES, SOURCE_ALL, SOURCE_FITTED, SOURCE_HOLD, SORT_NAME,
                                    SORT_QUANTITY, SORT_RARITY, SORT_TYPE)
from inventory_sections import is_inventory_row_selectable

logger = logging.getLogger(__name__)

ID = "inventory_filter"
ENV_OFF = "STAR_EMPIRE_NO_INVENTORY_FILTER"
PLAYER = "inventory"            # the owner key of your own Ship Inventory panel
STORAGE = "storage"             # ... of the player station's Storage tab
TAB_W = 300
BUTTON_W = 78
STATUS_S = 5.0

_failed = False
_windows: list = []             # weak references to every window that has used the feature
_specs: dict = {}               # owner key -> FilterSpec (a ship window's key is its panel id)

_SORT_BUTTONS = ((SORT_NAME, "NAME"), (SORT_TYPE, "TYPE"), (SORT_RARITY, "RARITY"), (SORT_QUANTITY, "QTY"))


def _enabled() -> bool:
    return not _failed and not os.environ.get(ENV_OFF)


def _switch_off(where: str) -> None:
    global _failed
    if not _failed:
        logger.exception("inventory_filter: error in %s - the filters are switched off for this session", where)
    _failed = True


def reset_for_tests() -> None:
    global _failed
    _failed = False
    _specs.clear()
    _windows.clear()


def spec_for(owner: str) -> FilterSpec:
    return _specs.get(owner, model.NO_FILTER)


# ── window state ─────────────────────────────────────────────────────────────

def _ensure(win) -> None:
    if "_if_ready" in win.__dict__:
        return
    win._if_ready = True
    _windows.append(weakref.ref(win))
    win._if_owner = None            # the owner whose tab is open
    win._if_buttons = {}            # owner key -> the FILTER button's rect, as last drawn
    win._if_hits = []               # [(rect, action)] of the tab, as last drawn
    win._if_rect = None
    win._if_body = None
    win._if_scroll = 0
    win._if_max_scroll = 0
    win._if_status = None           # (text, monotonic deadline)
    win._if_storage = None          # (overlay rect, stamp) while the Storage tab is drawn
    win._if_wanted = (None, None)   # (layout id, Wanted) cache


def _live_windows() -> list:
    return [w for w in (ref() for ref in list(_windows)) if w is not None]


# ── owners: the windows that can be filtered ─────────────────────────────────

def _ship_windows(win) -> list:
    return list(getattr(win, "_ship_windows", {}).values())


def _ship_window(win, key):
    return next((sw for sw in _ship_windows(win) if sw.panel_id == key), None)


def _player_rect(win):
    panel = win._panels.get("inventory", {}) if hasattr(win, "_panels") else {}
    if panel.get("minimized") or panel.get("hidden") or win.__dict__.get("_inv_rows_cache") is None:
        return None
    return getattr(win, "_panel_full_rects", {}).get("inventory")


def _storage_rect(win):
    stamp = win.__dict__.get("_if_storage")
    if stamp is None or time.monotonic() - stamp[1] > 0.5:
        return None
    overlay = getattr(win, "_station_overlay", None)
    if not (isinstance(overlay, dict) and overlay.get("kind") == "player_station"
            and getattr(win, "_ps_tab", None) == "Storage"):
        return None
    return stamp[0]


def owner_rect(win, key):
    """The on-screen rect of the window that owns *key*, or None while it is hidden, folded or closed."""
    if key == PLAYER:
        return _player_rect(win)
    if key == STORAGE:
        return _storage_rect(win)
    sw = _ship_window(win, key)
    return win._hinv_panel_rect(sw) if sw is not None else None


def owner_route_id(win, key) -> str:
    if key == PLAYER:
        return "panel:inventory"
    if key == STORAGE:
        return "station_overlay"
    sw = _ship_window(win, key)
    return sw.window_id if sw is not None else "panel:" + str(key)


def owner_title(win, key) -> str:
    if key == PLAYER:
        return "SHIP INVENTORY"
    if key == STORAGE:
        return "STATION STORAGE"
    sw = _ship_window(win, key)
    if sw is None:
        return "INVENTORY"
    name = str(sw.data.get("display_name") or sw.data.get("ship_type") or "SHIP").upper()
    return name


def owner_rows(win, key):
    """The window's unfiltered rows (the dev's own row model)."""
    if key == PLAYER:
        return win.__dict__.get("_inv_rows_cache") or []
    if key == STORAGE:
        return win.__dict__.get("_ps_storage_rows_cache") or []
    sw = _ship_window(win, key)
    if sw is None:
        return []
    return (sw.model or win._hinv_model(sw))[1]


def _memo(win, slot, deps: tuple, build):
    """``build()``, remembered while every object in *deps* is the same one (by identity): the tab asks for these
    every frame, and the rows only change with a new server payload."""
    cache = win.__dict__.setdefault("_if_memo", {})
    entry = cache.get(slot)
    if entry is not None and len(entry[0]) == len(deps) and all(a is b for a, b in zip(entry[0], deps)):
        return entry[1]
    value = build()
    cache[slot] = (deps, value)
    return value


def owner_counts(win, key) -> tuple:
    """``(types, rarities)`` of the window's items, for the chips."""
    rows = owner_rows(win, key)
    build = model.storage_facet_counts if key == STORAGE else model.ship_facet_counts
    return _memo(win, ("counts", key), (rows,), lambda: build(rows))


def owner_items(win, key) -> list:
    rows = owner_rows(win, key)
    build = model.storage_row_items if key == STORAGE else model.ship_row_items
    return _memo(win, ("items", key), (rows,), lambda: build(rows))


def _owner_of_rows(rows):
    """``(window, owner key)`` whose unfiltered row list is *rows* (the view function is handed it)."""
    for win in _live_windows():
        if win.__dict__.get("_inv_rows_cache") is rows:
            return win, PLAYER
        for sw in _ship_windows(win):
            if sw.model is not None and sw.model[1] is rows:
                return win, sw.panel_id
    return None


def wanted_for(win, spec: FilterSpec):
    """The ``Wanted`` of the spec's saved layout (cached per layout object), or None."""
    if not spec.loadout:
        return None
    store = _layouts()
    if store is None:                        # the Ship Layouts mod is not installed: nothing to match against
        return None
    layout = store.get(spec.loadout)
    cached_layout, cached = win.__dict__.get("_if_wanted", (None, None))
    if layout is not None and cached_layout is layout:
        return cached
    wanted = model.wanted_from_layout(layout)
    win._if_wanted = (layout, wanted)
    return wanted


def _invalidate(win, key) -> None:
    """Make the window rebuild what it shows from its rows."""
    if key == PLAYER:
        win._inv_display_cache_key = None
        win._inv_display_cache_source = None
        win._panels["inventory"]["scroll"] = 0
        win._inv_popup = None
        win._inv_selected_keys.clear()
        win._inv_last_click_key = None
        win._inv_drag_pending = False
        win._inv_drag_active = False
    elif key == STORAGE:
        win._ps_storage_scroll = 0
        win._ps_cargo_popup = None
        getattr(win, "_psx_selected_keys", set()).clear()
    else:
        sw = _ship_window(win, key)
        if sw is not None:
            sw.view = None
            sw.selected_keys.clear()
            sw.range_anchor = None
            panel = win._panels.get(sw.panel_id)
            if panel is not None:
                panel["scroll"] = 0
            win._hinv_popup = None


def set_spec(win, key, spec: FilterSpec) -> None:
    if spec == spec_for(key):
        return
    if spec.active():
        _specs[key] = spec
    else:
        _specs.pop(key, None)
    win._if_scroll = win._if_scroll if key == win._if_owner else 0
    _invalidate(win, key)


# ── what the windows show ────────────────────────────────────────────────────

def filter_ship_rows(rows, spec: FilterSpec, wanted):
    return model.apply_to_ship_rows(rows, spec, wanted)


def storage_rows(win, rows):
    """The Storage tab's rows through its filter (the vanilla list is passed through here); cached per list."""
    if not _enabled():
        return rows
    try:
        spec = spec_for(STORAGE)
        if not spec.active():
            return rows
        wanted = wanted_for(win, spec)
        key = (id(rows), spec, wanted.layout_id if wanted else None, len(rows))
        cached = win.__dict__.get("_if_storage_cache")
        if cached is not None and cached[0] == key and cached[1] is rows:
            return cached[2]
        out = model.apply_to_storage_rows(rows, spec, wanted)
        win._if_storage_cache = (key, rows, out)
        return out
    except Exception:
        _switch_off("storage_rows()")
        return rows


def storage_filter_key(win) -> tuple:
    """What the Storage tab's own filter cache must also be keyed on (the vanilla cache knows the search text)."""
    return (spec_for(STORAGE),)


# ── the FILTER button ────────────────────────────────────────────────────────

def _palette(render_module) -> dict:
    def pick(name, default):
        return tuple(getattr(render_module, name, default))[:3]

    return {"plate": pick("_GM_PLATE_BG", (7, 11, 20)), "header": pick("_GM_HEADER_BG", (13, 21, 38)),
            "canvas": pick("_GM_CANVAS_BG", (5, 9, 18)), "steel": pick("_GM_STEEL", (56, 88, 138)),
            "steel_dim": pick("_GM_STEEL_DIM", (45, 80, 130)), "accent": pick("_GM_ACCENT", (95, 170, 255)),
            "text": pick("_GM_TEXT", (185, 215, 250)), "value": pick("_GM_VALUE", (228, 237, 250)),
            "caption": pick("_GM_CAPTION", (96, 128, 176)), "ok": pick("_GM_SUCCESS", (100, 230, 180)),
            "warn": pick("_GM_WARN", (255, 92, 72)), "amber": (255, 190, 70), "grid": pick("_GM_GRID", (28, 48, 80))}


_colours: dict = _palette(None)


def _text_surface(win, text: str, size: int, colour: tuple, bold: bool = True):
    cache = win.__dict__.setdefault("_if_text_cache", {})
    key = (text, size, colour, bold, win._uk)
    surface = cache.get(key)
    if surface is None:
        surface = win._instrument_font(size, bold=bold).render(text, True, colour)
        try:
            from gl_ui_renderer import mark_static_surface
            mark_static_surface(surface)
        except Exception:
            pass
        if len(cache) >= 800:
            cache.clear()
        cache[key] = surface
    return surface


def _fit(win, text: str, size: int, max_w: int, bold: bool = True) -> str:
    if max_w <= 0:
        return ""
    font = win._instrument_font(size, bold=bold)
    if font.size(text)[0] <= max_w:
        return text
    while text and font.size(text + "...")[0] > max_w:
        text = text[:-1]
    return (text + "...") if text else ""


def _blit_text(win, screen, text, size, colour, x, y, max_w=None, bold=True, right=False) -> int:
    if max_w is not None:
        text = _fit(win, text, size, max_w, bold)
    if not text:
        return 0
    surface = _text_surface(win, text, size, colour, bold)
    screen.blit(surface, (x - surface.get_width() if right else x, y))
    return surface.get_width()


def split_footer(win, rect: pygame.Rect) -> tuple:
    """A search-bar footer cut into the strip the search field keeps and the FILTER button's."""
    s = win._s
    width = min(s(BUTTON_W), max(s(30), rect.w // 3))
    field = pygame.Rect(rect.x, rect.y, rect.w - width, rect.h)
    button = pygame.Rect(field.right, rect.y + s(3), width - s(4), rect.h - s(5))
    return field, button


def _funnel(screen, rect: pygame.Rect, colour: tuple) -> None:
    x, y, w, h = rect
    pygame.draw.polygon(screen, colour, [(x, y), (x + w, y), (x + w * 0.62, y + h * 0.5), (x + w * 0.62, y + h),
                                         (x + w * 0.38, y + h * 0.82), (x + w * 0.38, y + h * 0.5)])


def _rarity_colour(tier):
    return model.RARITY_RGB.get(tier, (180, 180, 180))


def draw_button(win, screen, rect: pygame.Rect, owner: str, hover_zone, mouse) -> None:
    """The FILTER button, inside the footer strip *rect* ``split_footer`` gave. Remembers its rect for the clicks."""
    _ensure(win)
    c = _colours
    s = win._s
    spec = spec_for(owner)
    opened = win._if_owner == owner
    hover_zone(rect)
    hot = rect.collidepoint(mouse)
    lit = spec.active() or opened
    pygame.draw.rect(screen, (22, 44, 70) if (lit or hot) else c["canvas"], rect)
    pygame.draw.rect(screen, c["accent"] if (lit or hot) else c["steel_dim"], rect, 1)
    colour = c["value"] if lit else c["caption"]
    icon = pygame.Rect(rect.x + s(6), rect.centery - s(5), s(10), s(10))
    _funnel(screen, icon, colour)
    count = spec.count()
    label = f"FILTER {count}" if count else "FILTER"
    room = rect.right - icon.right - s(10)
    if win._instrument_font(10, bold=True).size(label)[0] <= room:
        _blit_text(win, screen, label, 10, colour, icon.right + s(5), rect.centery - s(6))
    elif count:
        _blit_text(win, screen, str(count), 10, colour, icon.right + s(4), rect.centery - s(6))
    win._if_buttons[owner] = pygame.Rect(rect)


def _wrap_footer(window_cls) -> None:
    """Make the two search-bar footer draws leave room for the button and draw it."""
    player_bar = window_cls._draw_inv_search_bar
    ship_bar = window_cls._draw_hinv_search_bar

    def draw_inv_search_bar(self, screen, rect, hover_zone, mouse):
        if not _enabled():
            return player_bar(self, screen, rect, hover_zone, mouse)
        try:
            _ensure(self)
            field, button = split_footer(self, rect)
            player_bar(self, screen, field, hover_zone, mouse)
            pygame.draw.line(screen, _colours["grid"], (field.right, rect.top), (rect.right - 2, rect.top))
            draw_button(self, screen, button, PLAYER, hover_zone, mouse)
        except Exception:
            _switch_off("draw_inv_search_bar()")
            return player_bar(self, screen, rect, hover_zone, mouse)

    def draw_hinv_search_bar(self, win, screen, rect, hover_zone, mouse):
        if not _enabled():
            return ship_bar(self, win, screen, rect, hover_zone, mouse)
        try:
            _ensure(self)
            field, button = split_footer(self, rect)
            ship_bar(self, win, screen, field, hover_zone, mouse)
            pygame.draw.line(screen, _colours["grid"], (field.right, rect.top), (rect.right - 2, rect.top))
            draw_button(self, screen, button, win.panel_id, hover_zone, mouse)
        except Exception:
            _switch_off("draw_hinv_search_bar()")
            return ship_bar(self, win, screen, rect, hover_zone, mouse)

    for wrapper, original in ((draw_inv_search_bar, player_bar), (draw_hinv_search_bar, ship_bar)):
        wrapper.__wrapped__ = original
        wrapper.__doc__ = original.__doc__
    window_cls._draw_inv_search_bar = draw_inv_search_bar
    window_cls._draw_hinv_search_bar = draw_hinv_search_bar


# ── the Storage tab's button (the game draws that tab inline: render_mixin.py calls these) ──

def storage_button_rect(win, search_rect: pygame.Rect) -> pygame.Rect:
    """The strip right of the Storage search box, which render_mixin.py cuts off the box's width."""
    s = win._s
    return pygame.Rect(search_rect.right + s(6), search_rect.y, s(BUTTON_W), search_rect.h)


def storage_button_width(win) -> int:
    """How much of the search box's width the button takes (0 when the feature is off)."""
    return win._s(BUTTON_W) + win._s(6) if _enabled() else 0


def draw_storage_button(win, screen, search_rect: pygame.Rect, overlay_rect: pygame.Rect) -> None:
    """Called by the Storage tab's draw, right after its search box."""
    if not _enabled():
        return
    try:
        _ensure(win)
        win._if_storage = (pygame.Rect(overlay_rect), time.monotonic())
        mouse = pygame.mouse.get_pos()
        draw_button(win, screen, storage_button_rect(win, search_rect), STORAGE, lambda _rect: None, mouse)
    except Exception:
        _switch_off("draw_storage_button()")


# ── the tab ──────────────────────────────────────────────────────────────────

def tab_rect(win, screen_w: int, screen_h: int):
    """The tab's rect beside its owner (right if there is room, else left), or None while the owner is gone."""
    owner = win._if_owner
    base = owner_rect(win, owner) if owner else None
    if base is None:
        return None
    s = win._s
    width = s(TAB_W)
    height = max(min(base.h, screen_h), min(s(420), screen_h))
    y = max(0, min(base.y, screen_h - height))
    x = base.right + s(2)
    if x + width > screen_w:
        x = base.left - width - s(2)
    if x < 0:
        x = max(0, screen_w - width)
    return pygame.Rect(x, y, width, height)


def is_open(win) -> bool:
    return bool(_enabled() and "_if_ready" in win.__dict__ and win._if_owner is not None)


def open_tab(win, owner: str) -> None:
    _ensure(win)
    win._if_owner = owner
    win._if_scroll = 0
    win._if_status = None
    try:
        import ship_layouts_window as slw
        slw.STORE.refresh()                  # another client may have saved a layout since
    except Exception:
        logger.debug("inventory_filter: could not refresh the layouts", exc_info=True)


def close_tab(win) -> None:
    _ensure(win)
    win._if_owner = None
    win._if_rect = None


def toggle_tab(win, owner: str) -> None:
    if win._if_owner == owner:
        close_tab(win)
    else:
        open_tab(win, owner)


def _say(win, text: str) -> None:
    win._if_status = (text, time.monotonic() + STATUS_S)


def _chip(win, screen, x, y, left, right, label, *, selected, colour, action, hits, mouse, dim=False):
    """One chip, wrapped to the next line if it would pass *right*. Returns the x and y for the next chip."""
    s = win._s
    surface = _text_surface(win, label, 10, colour if (selected or not dim) else _colours["caption"])
    width = surface.get_width() + s(14)
    height = s(20)
    if x + width > right and x > left:
        x, y = left, y + height + s(5)
    rect = pygame.Rect(x, y, width, height)
    hot = rect.collidepoint(mouse)
    if selected:
        pygame.draw.rect(screen, tuple(max(0, int(v * 0.30)) for v in colour), rect)
        pygame.draw.rect(screen, colour, rect, 1)
    else:
        pygame.draw.rect(screen, (20, 36, 58) if hot else _colours["canvas"], rect)
        pygame.draw.rect(screen, _colours["accent"] if hot else _colours["steel_dim"], rect, 1)
    screen.blit(surface, (rect.centerx - surface.get_width() // 2, rect.centery - surface.get_height() // 2))
    hits.append((rect, action, True))
    return x + width + s(5), y


def _button(win, screen, rect, label, *, enabled=True, hot=False, selected=False, arrow=None):
    """A flat button. ``arrow`` ("up" / "down") draws a small triangle after the label (the sort's direction)."""
    c = _colours
    if selected:
        fill, border = (22, 52, 84), c["accent"]
    else:
        fill = (14, 29, 43) if (hot and enabled) else c["plate"]
        border = c["accent"] if (hot and enabled) else c["steel"]
    pygame.draw.rect(screen, fill, rect)
    pygame.draw.rect(screen, border, rect, 1)
    colour = c["value"] if (enabled and (selected or hot)) else (c["text"] if enabled else c["caption"])
    surface = _text_surface(win, label, 10, colour)
    tri = win._s(4) if arrow else 0
    extra = tri * 2 + win._s(6) if arrow else 0
    x = rect.centerx - (surface.get_width() + extra) // 2
    screen.blit(surface, (x, rect.centery - surface.get_height() // 2))
    if arrow:
        cx = x + surface.get_width() + win._s(6) + tri
        cy = rect.centery
        if arrow == "up":
            points = [(cx - tri, cy + tri // 2 + 1), (cx + tri, cy + tri // 2 + 1), (cx, cy - tri // 2 - 1)]
        else:
            points = [(cx - tri, cy - tri // 2 - 1), (cx + tri, cy - tri // 2 - 1), (cx, cy + tri // 2 + 1)]
        pygame.draw.polygon(screen, colour, points)


def _section(win, screen, x, y, w, title, note=""):
    """A section's caption and rule; returns the y below them."""
    c = _colours
    s = win._s
    drawn = _blit_text(win, screen, title, 10, c["text"], x, y)
    if note:
        _blit_text(win, screen, note, 9, c["caption"], x + drawn + s(8), y + 1, max_w=w - drawn - s(8), bold=False)
    pygame.draw.line(screen, c["grid"], (x, y + s(15)), (x + w, y + s(15)))
    return y + s(21)


def _row_buttons(win, screen, x, y, w, entries, current, hits, mouse, kind, arrows=None):
    """A row of equal buttons: ``entries`` is ``[(value, label)]``; the one equal to *current* is lit."""
    s = win._s
    gap = s(5)
    count = len(entries)
    each = (w - gap * (count - 1)) // count
    for index, (value, label) in enumerate(entries):
        rect = pygame.Rect(x + index * (each + gap), y, each, s(22))
        selected = value == current
        arrow = (arrows or {}).get(value) if selected else None
        _button(win, screen, rect, label, hot=rect.collidepoint(mouse), selected=selected, arrow=arrow)
        hits.append((rect, (kind, value), True))
    return y + s(22) + s(10)


def _sort_arrow(spec: FilterSpec) -> dict:
    """Which way the lit sort button points: up for A-Z, down for biggest / rarest first, turned by the reverse."""
    natural_down = spec.sort in (SORT_RARITY, SORT_QUANTITY)
    return {spec.sort: "down" if natural_down != spec.reverse else "up"}


def _source_labels(owner: str) -> list:
    if owner == STORAGE:
        return [(SOURCE_ALL, "ALL"), (SOURCE_FITTED, "EQUIPPED"), (SOURCE_HOLD, "IN STORAGE")]
    return [(SOURCE_ALL, "ALL"), (SOURCE_FITTED, "FITTED"), (SOURCE_HOLD, "IN HOLD")]


def draw_tab(win, screen) -> None:
    """Draw the tab. Total: an error switches the feature off."""
    try:
        _draw_tab(win, screen)
    except Exception:
        _switch_off("draw_tab()")


def _layouts():
    """The Ship Layouts mod's store of saved layouts, or None when that mod is not installed (the LOADOUT section and
    loadout matching then simply do not appear)."""
    try:
        import ship_layouts_window as slw
    except ImportError:
        return None
    return slw.STORE


def _draw_tab(win, screen) -> None:
    c = _colours
    s = win._s
    owner = win._if_owner
    rect = tab_rect(win, *screen.get_size())
    win._if_rect = rect
    if owner is None or rect is None:
        win._if_hits = []
        return
    mouse = pygame.mouse.get_pos()
    spec = spec_for(owner)
    pad = s(10)
    header_h = s(30)
    footer_h = s(64)
    pygame.draw.rect(screen, c["plate"], rect)
    pygame.draw.rect(screen, c["steel"], rect, 1)
    header = pygame.Rect(rect.x, rect.y, rect.w, header_h)
    pygame.draw.rect(screen, c["header"], header)
    pygame.draw.line(screen, c["steel"], header.bottomleft, header.bottomright)
    _funnel(screen, pygame.Rect(rect.x + s(10), rect.y + header_h // 2 - s(5), s(10), s(10)), c["accent"])
    close = pygame.Rect(rect.right - pad - s(22), rect.y + (header_h - s(18)) // 2, s(22), s(18))
    _blit_text(win, screen, "FILTER  //  " + owner_title(win, owner), 10, c["text"], rect.x + s(28),
               rect.y + (header_h - s(12)) // 2, max_w=close.x - rect.x - s(36))
    hot = close.collidepoint(mouse)
    pygame.draw.rect(screen, (64, 22, 28) if hot else (12, 18, 30), close)
    pygame.draw.rect(screen, c["warn"] if hot else c["steel"], close, 1)
    mark = _text_surface(win, "X", 11, c["value"])
    screen.blit(mark, (close.centerx - mark.get_width() // 2, close.centery - mark.get_height() // 2))

    body = pygame.Rect(rect.x + pad, header.bottom + pad, rect.w - pad * 2, rect.h - header_h - footer_h - pad * 2)
    footer = pygame.Rect(rect.x + pad, rect.bottom - footer_h, rect.w - pad * 2, footer_h - pad)
    win._if_body = body
    hits: list = [(close, ("close",), False)]

    # ---- the body, scrolled
    previous_clip = screen.get_clip()
    screen.set_clip(body.clip(previous_clip) if previous_clip else body)
    left = body.x
    inner_w = body.w - s(8)
    top = body.y - win._if_scroll
    y = top
    types, rarities = owner_counts(win, owner)

    y = _section(win, screen, left, y, inner_w, "SORT", "press again to turn it round")
    arrows = _sort_arrow(spec) if spec.sort != model.SORT_DEFAULT else {}
    y = _row_buttons(win, screen, left, y, inner_w, list(_SORT_BUTTONS), spec.sort, hits, mouse, "sort", arrows)

    y = _section(win, screen, left, y, inner_w, "SHOW")
    y = _row_buttons(win, screen, left, y, inner_w, _source_labels(owner), spec.source, hits, mouse, "source")

    y = _section(win, screen, left, y, inner_w, "TYPE", "any of the ticked")
    counts = dict(types)
    shown_types = list(types) + [(t, 0) for t in sorted(spec.types) if t not in counts]
    if not shown_types:
        _blit_text(win, screen, "Nothing here to filter.", 10, c["caption"], left, y, bold=False)
        y += s(20)
    else:
        x, line_y = left, y
        for category, count in shown_types:
            x, line_y = _chip(win, screen, x, line_y, left, left + inner_w,
                              f"{model.category_label(category)}  {count}", selected=category in spec.types,
                              colour=c["accent"], action=("type", category), hits=hits, mouse=mouse,
                              dim=count == 0)
        y = line_y + s(20) + s(10)

    y = _section(win, screen, left, y, inner_w, "RARITY", "any of the ticked")
    tiers = [(tier, rarities.get(tier, 0)) for tier in reversed(RARITIES)
             if rarities.get(tier, 0) or tier in spec.rarities]
    if not tiers:
        _blit_text(win, screen, "Nothing here has a rarity.", 10, c["caption"], left, y, bold=False)
        y += s(20)
    else:
        x, line_y = left, y
        for tier, count in tiers:
            x, line_y = _chip(win, screen, x, line_y, left, left + inner_w, f"{tier.capitalize()}  {count}",
                              selected=tier in spec.rarities, colour=_rarity_colour(tier),
                              action=("rarity", tier), hits=hits, mouse=mouse, dim=count == 0)
        y = line_y + s(20) + s(10)

    y = _draw_loadout(win, screen, owner, spec, left, y, inner_w, hits, mouse)

    screen.set_clip(previous_clip)
    content_h = y - top
    win._if_max_scroll = max(0, content_h - body.h)
    win._if_scroll = max(0, min(win._if_max_scroll, win._if_scroll))
    if win._if_max_scroll:
        track = pygame.Rect(body.right - s(4), body.y, s(4), body.h)
        pygame.draw.rect(screen, c["canvas"], track)
        thumb_h = max(s(24), track.h * body.h // max(1, content_h))
        thumb_y = track.y + (track.h - thumb_h) * win._if_scroll // win._if_max_scroll
        pygame.draw.rect(screen, c["steel"], (track.x, thumb_y, track.w, thumb_h))

    # ---- the footer: a status line, SELECT SHOWN and CLEAR ALL
    pygame.draw.line(screen, c["grid"], (footer.x, footer.y - s(4)), (footer.right, footer.y - s(4)))
    status = win._if_status
    if status is not None and time.monotonic() > status[1]:
        win._if_status = status = None
    line = status[0] if status else _summary_line(win, owner, spec)
    _blit_text(win, screen, line, 10, c["amber"] if status else c["caption"], footer.x, footer.y,
               max_w=footer.w, bold=bool(status))
    half = (footer.w - s(6)) // 2
    select = pygame.Rect(footer.x, footer.bottom - s(26), half, s(26))
    clear = pygame.Rect(select.right + s(6), select.y, footer.right - select.right - s(6), s(26))
    _button(win, screen, select, "SELECT SHOWN", hot=select.collidepoint(mouse))
    _button(win, screen, clear, "CLEAR ALL", enabled=spec.active(), hot=clear.collidepoint(mouse))
    hits.append((select, ("select",), False))
    hits.append((clear, ("clear",), False))
    win._if_hits = hits


def _summary_line(win, owner, spec: FilterSpec) -> str:
    if not spec.active():
        return "Nothing filtered."
    bits = []
    if spec.loadout:
        wanted = wanted_for(win, spec)
        bits.append(f"loadout {wanted.name}" if wanted else "loadout (not found)")
    if spec.types:
        bits.append(f"{len(spec.types)} type{'s' if len(spec.types) != 1 else ''}")
    if spec.rarities:
        bits.append(f"{len(spec.rarities)} rarit{'ies' if len(spec.rarities) != 1 else 'y'}")
    if spec.source != SOURCE_ALL:
        bits.append("fitted only" if spec.source == SOURCE_FITTED else "hold only")
    if spec.sort != model.SORT_DEFAULT:
        bits.append("sorted by " + spec.sort)
    return "Showing: " + ", ".join(bits)


def _draw_loadout(win, screen, owner, spec, left, y, inner_w, hits, mouse) -> int:
    """The LOADOUT section: the saved layouts to pick from, then the parts of the picked one with what is here."""
    store = _layouts()
    if store is None:                        # the Ship Layouts mod is not installed: no section at all
        return y
    c = _colours
    s = win._s
    y = _section(win, screen, left, y, inner_w, "LOADOUT", "only the parts of a saved layout")
    layouts = list(store.layouts)
    if not layouts:
        text = "Loading saved layouts..." if not store.loaded else "No saved layouts yet."
        _blit_text(win, screen, text, 10, c["caption"], left, y, bold=False)
        y += s(20)
        if store.loaded:
            button = pygame.Rect(left, y, inner_w, s(24))
            _button(win, screen, button, "OPEN SHIP LAYOUTS", hot=button.collidepoint(mouse))
            hits.append((button, ("layouts",), True))
            y += s(24) + s(10)
        return y
    row_h = s(34)
    picked_layout = next((layout for layout in layouts if layout.get("id") == spec.loadout), None)
    for layout in ([picked_layout] if picked_layout is not None else layouts):
        row = pygame.Rect(left, y, inner_w, row_h - s(3))
        picked = layout.get("id") == spec.loadout
        hot = row.collidepoint(mouse)
        pygame.draw.rect(screen, (22, 52, 84) if picked else ((16, 30, 48) if hot else c["canvas"]), row)
        pygame.draw.rect(screen, c["accent"] if picked else c["steel_dim"], row, 1)
        centre = (row.x + s(12), row.centery)
        pygame.draw.circle(screen, c["accent"] if picked else c["steel"], centre, s(5), 0 if picked else 1)
        parts = model.wanted_from_layout(layout)
        ship = parts.ship if parts and parts.ship else "ship"
        total = f"{parts.total()} parts" if parts else ""
        _blit_text(win, screen, str(layout.get("name") or "?"), 11, c["value"], row.x + s(24), row.y + s(3),
                   max_w=row.w - s(30))
        detail = f"{ship}  -  {total}" + ("   (click to change)" if picked else "")
        _blit_text(win, screen, detail, 9, c["caption"], row.x + s(24), row.y + s(18),
                   max_w=row.w - s(30), bold=False)
        hits.append((row, ("loadout", layout.get("id")), True))
        y += row_h
    y += s(4)

    wanted = wanted_for(win, spec)
    if spec.loadout and wanted is None:
        _blit_text(win, screen, "That layout is gone.", 10, c["warn"], left, y, bold=False)
        return y + s(22)
    if wanted is None:
        return y + s(6)
    items = owner_items(win, owner)
    progress = _memo(win, ("progress", owner), (items, wanted), lambda: model.loadout_progress(items, wanted))
    total = wanted.total()
    here = sum(min(hold + fitted, need.count) for need, hold, fitted in progress)
    in_hold = sum(min(hold, need.count) for need, hold, _fitted in progress)
    y = _section(win, screen, left, y + s(4), inner_w, "PARTS", f"{here}/{total} here, {in_hold} in the hold")
    group = None
    for need, hold, fitted in progress:
        if need.group != group:
            group = need.group
            _blit_text(win, screen, model.GROUP_LABELS[group], 9, c["caption"], left, y + s(2))
            y += s(16)
        done = hold >= need.count
        some = hold + fitted > 0
        colour = c["ok"] if done else (c["amber"] if some else c["warn"])
        pygame.draw.circle(screen, colour, (left + s(6), y + s(8)), s(3))
        count_text = f"{hold}/{need.count}" + (f"  +{fitted} fitted" if fitted else "")
        width = _blit_text(win, screen, count_text, 10, colour, left + inner_w, y + s(2), right=True)
        _blit_text(win, screen, need.name, 10, c["text"] if some else c["caption"], left + s(16), y + s(2),
                   max_w=inner_w - s(20) - width, bold=some)
        y += s(18)
    return y + s(8)


# ── actions ──────────────────────────────────────────────────────────────────

def select_shown(win, owner: str) -> int:
    """Pick every shown row that sits in the hold / storage, so one drag moves them all. Returns how many."""
    if owner == PLAYER:
        shown = win.__dict__.get("_inv_display_click_map_cache") or {}
        keys = {key for key, item in shown.items()
                if isinstance(item, dict) and item.get("item_class") in ("cargo", "cargo_item")}
        win._inv_selected_keys.clear()
        win._inv_selected_keys.update(keys)
        win._inv_popup = None
        return len(keys)
    if owner == STORAGE:
        rows = getattr(win, "_psx_display_rows", None) or []
        picked = {win._psx_row_key(row) for row in rows
                  if isinstance(row, dict) and row.get("_kind") == "cargo" and not row.get("_display_equipped")
                  and int(row.get("_display_movable_qty", 1) or 0) > 0}
        win._psx_selected_keys.clear()
        win._psx_selected_keys.update(key for key in picked if key)
        return len(win._psx_selected_keys)
    sw = _ship_window(win, owner)
    if sw is None:
        return 0
    keys = {key for key, item in (sw.click_map or {}).items()
            if isinstance(item, dict) and item.get("item_class") in ("cargo", "cargo_item")}
    sw.selected_keys = set(keys)
    sw.range_anchor = None
    return len(keys)


def _act(win, action: tuple) -> None:
    owner = win._if_owner
    kind = action[0]
    if kind == "close":
        close_tab(win)
        return
    spec = spec_for(owner)
    if kind == "sort":
        set_spec(win, owner, spec.pick_sort(action[1]))
    elif kind == "source":
        set_spec(win, owner, spec.with_source(action[1]))
    elif kind == "type":
        set_spec(win, owner, spec.toggle_type(action[1]))
    elif kind == "rarity":
        set_spec(win, owner, spec.toggle_rarity(action[1]))
    elif kind == "loadout":
        set_spec(win, owner, spec.with_loadout(None if spec.loadout == action[1] else action[1]))
    elif kind == "clear":
        set_spec(win, owner, spec.cleared())
    elif kind == "layouts":
        try:
            import ship_layouts_window as slw
        except ImportError:
            return
        slw.open_window(win)
    elif kind == "select":
        count = select_shown(win, owner)
        _say(win, (f"Picked {count} row{'s' if count != 1 else ''} - drag one to move them all." if count
                   else "Nothing in the hold to pick."))


def _click(win, pos) -> None:
    body = win._if_body
    for rect, action, scrolls in win._if_hits:
        if rect.collidepoint(pos) and (not scrolls or (body is not None and body.collidepoint(pos))):
            _act(win, action)
            return


# ── events ───────────────────────────────────────────────────────────────────

def _route_allows(win, pos, window_id) -> bool:
    manager = getattr(win, "_gui_windows", None)
    return manager is not None and manager.route_pointer(pos).allows(window_id)


def _owns_tab(win, pos, focus=False) -> bool:
    manager = getattr(win, "_gui_windows", None)
    return manager is not None and manager.route_pointer(pos, focus=focus).owner == ID


def _button_hit(win, pos):
    """The owner key whose FILTER button is under *pos* (and not covered by another window), else None."""
    for owner, rect in list(win._if_buttons.items()):
        if not rect.collidepoint(pos):
            continue
        base = owner_rect(win, owner)
        if base is None or not base.contains(rect):
            continue                       # the window is folded, moved or closed since the button was drawn
        if _route_allows(win, pos, owner_route_id(win, owner)):
            return owner
    return None


def _handle(win, event) -> bool:
    kind = event.type
    if kind not in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEWHEEL):
        return False
    if kind == pygame.MOUSEWHEEL:
        pos = pygame.mouse.get_pos()
        if win._if_owner is not None and _owns_tab(win, pos):
            step = int(getattr(event, "y", 0)) * win._s(36)
            win._if_scroll = max(0, min(win._if_max_scroll, win._if_scroll - step))
            return True
        return False
    pos = event.pos
    if kind == pygame.MOUSEBUTTONUP:
        return win._if_owner is not None and _owns_tab(win, pos)
    owner = _button_hit(win, pos)
    if owner is not None:
        if event.button == 1:
            toggle_tab(win, owner)
        elif event.button == 3:
            set_spec(win, owner, spec_for(owner).cleared())
            if win._if_owner == owner:
                _say(win, "Filters cleared.")
        return True
    if win._if_owner is not None and _owns_tab(win, pos, focus=True):
        if event.button == 1:
            _click(win, pos)
        return True
    return False


def filter_events(win, events):
    """Called once per frame (Client.py) with the frame's events: takes out the ones that belong to a FILTER button
    or the tab, so the game's own handlers never see them. Total."""
    if not _enabled():
        return events
    try:
        _ensure(win)
        if win._if_owner is not None and owner_rect(win, win._if_owner) is None:
            close_tab(win)                 # its window was folded, hidden or closed
        kept = [event for event in events if not _handle(win, event)]
        return kept if len(kept) != len(events) else events
    except Exception:
        _switch_off("filter_events()")
        return events


# ── hooks ────────────────────────────────────────────────────────────────────

def queue(win, frame) -> None:
    """Called once per frame (Client.py) before the retained windows are flushed: queue the tab's draw."""
    if not _enabled():
        return
    try:
        _ensure(win)
        if win._if_owner is None:
            return
        screen = frame.screen
        win._gui_windows.queue_draw(
            ID, lambda: draw_tab(win, screen), lambda: tab_rect(win, *screen.get_size()),
            layer=GuiLayer.WINDOW, focusable=True,
            visible=lambda: bool(_enabled() and win._if_owner is not None))
    except Exception:
        _switch_off("queue()")


def _view_wrapper(original):
    def ship_inventory_view(rows, query, collapsed):
        if _enabled() and _specs:
            try:
                found = _owner_of_rows(rows)
                if found is not None:
                    win, owner = found
                    spec = spec_for(owner)
                    if spec.active():
                        rows = filter_ship_rows(rows, spec, wanted_for(win, spec))
            except Exception:
                _switch_off("ship_inventory_view()")
        return original(rows, query, collapsed)

    ship_inventory_view.__wrapped__ = original
    ship_inventory_view.__doc__ = original.__doc__
    return ship_inventory_view


def install(window_cls, render_module=None, client_globals=None) -> bool:
    """Wrap the shared ship inventory view, the two search-bar footers and the player inventory's layer key.
    Given Client.py's ``globals()`` it also replaces the view function that module imported. Idempotent."""
    global _colours
    if window_cls.__dict__.get("_inventory_filter_installed"):
        return False
    _colours = _palette(render_module)
    _wrap_footer(window_cls)

    layer_key = window_cls._inv_search_layer_key

    def inv_search_layer_key(self):
        key = layer_key(self)
        if not _enabled():
            return key
        return key + (spec_for(PLAYER), self.__dict__.get("_if_owner") == PLAYER)

    inv_search_layer_key.__wrapped__ = layer_key
    window_cls._inv_search_layer_key = inv_search_layer_key

    import hangar_inventory
    hangar_inventory.ship_inventory_view = _view_wrapper(hangar_inventory.ship_inventory_view)
    if client_globals is not None and "ship_inventory_view" in client_globals:
        client_globals["ship_inventory_view"] = _view_wrapper(client_globals["ship_inventory_view"])
    window_cls._inventory_filter_installed = True
    return True

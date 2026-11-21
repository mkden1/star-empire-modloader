"""extra_bars.py - the extra quick action bars in the running game.

Each account has its own set of extra bars (bars.py), loaded the first time they are drawn after login and saved to
``quick_bars.json`` whenever one changes. Everything a bar does is the game's own bar code run on the bar through
``bar_context.in_bar``:

  * DRAWING: once per frame, right after the game's own bar (one hook in Client.py calls ``draw_bars``): each shown bar is placed
    from its two fractions, drawn through the game's retained layer machinery under its own cache id, and registered with the
    window manager on the HUD layer, so a window above it gets the clicks first and the world never sees a click on a bar. The
    per-frame work the game does for its bar (lifting a held slot, a held key's server-side repeat) runs for every bar, shown or not.
  * THE MOUSE (``handle_event``, called from the window's event filter): a press on a slot is recorded and a release fires it, held
    it lifts the slot to move or swap it, a drop from the inventory binds it, a right-click clears a slot or opens the class-skill
    menu, exactly as on the game's bar. A press on the panel's padding or the gaps between slots drags the whole bar; where it lands
    is saved as fractions of the room the screen leaves it, so it keeps its relative place at any window size.
  * THE KEYS (``fire_slot``, called from quick_keys): the slot is fired with the game's ``_hotbar_activate_slot`` and a held key
    repeats server-side through ``_hotbar_key_hold_begin``; only ONE key is held at a time across all bars, as on the game's own.
  * THE CLASS-SKILL MENU, which the game opens from a right-click on an empty slot and answers later, outside any ``with`` block, is
    wrapped so that it draws and binds on the bar that opened it.

A hidden bar is not drawn and its keys do nothing. Every function the game's code calls here is total: an error switches the extra
bars off for the session (the game's own bar is never involved).
"""

from __future__ import annotations

import logging
import os

import pygame

import bar_context as bc
import bars as bars_model
import keybinds as kb
import loadouts as loadouts_model
import quick_keys
from gui_window_manager import GuiLayer

logger = logging.getLogger(__name__)

ENV_OFF = "STAR_EMPIRE_NO_EXTRA_BARS"
BAR_ID_PREFIX = "quickbar:"

_failed = False
_raw: dict = {}                       # the file's "bars" section: account name -> that account's saved set
_previous: dict = {}                  # the file's "previous" section: account name -> the setup a loadout load replaced (its undo)


def _switch_off(where: str) -> None:
    global _failed
    if not _failed:
        _failed = True
        logger.exception("extra_bars: switched off for this session (%s)", where)


def enabled() -> bool:
    return not _failed and not os.environ.get(ENV_OFF)


def load() -> None:
    """Read every account's saved bars and loadout undo (a small file, once, at start-up)."""
    global _raw, _previous
    data = kb.read_file(quick_keys.path())
    _raw = data.get("bars") if isinstance(data.get("bars"), dict) else {}
    _previous = data.get("previous") if isinstance(data.get("previous"), dict) else {}


def reset_for_tests() -> None:
    global _failed, _raw, _previous
    _failed, _raw, _previous = False, {}, {}


# ── the runtime of one window ────────────────────────────────────────────────

class Runtime:
    def __init__(self):
        self.account = None
        self.barset = bars_model.BarSet()
        self.views: dict = {}
        self.undo: "dict | None" = None          # the setup the last loadout load replaced (loadout_ops.undo puts it back)
        self.press_bar: "str | None" = None      # the bar a mouse press began on
        self.drag: "dict | None" = None          # a bar being moved by its padding
        self.size = (0, 0)                       # the screen, as of the last frame drawn
        self.status = None                       # the last thing worth telling the player


def _runtime_of(win) -> "Runtime | None":
    """The window's runtime if it has one (a window that never had extra bars, or no such window at all, has none)."""
    return getattr(win, "__dict__", {}).get("_qb_extra")


def runtime(win) -> Runtime:
    rt = _runtime_of(win)
    if rt is None:
        rt = win._qb_extra = Runtime()
    return rt


def _save(win) -> None:
    rt = runtime(win)
    if not rt.account:
        return
    submit = quick_keys.submitter()
    submit(_write, quick_keys.path(), rt.account, rt.barset.to_json())


def _write(path: str, account: str, snapshot: dict) -> None:
    try:
        kb.write_entry(path, "bars", account, snapshot)
    except Exception:
        logger.exception("extra_bars: could not save %s", path)


def ensure_loaded(win) -> Runtime:
    """The window's runtime, with the logged-in account's bars loaded (again, if the account changed)."""
    rt = runtime(win)
    account = getattr(win, "_username", None) or None
    if rt.account != account:
        rt.account = account
        rt.barset = bars_model.BarSet.from_json(_raw.get(account) if account else None)
        rt.views = {}
        rt.press_bar = rt.drag = None
        rt.undo = loadouts_model.clean_setup(_previous.get(account)) if account else None
        quick_keys.set_bars(list(rt.barset))
    return rt


def view_of(win, rt: Runtime, bar) -> bc.BarView:
    view = rt.views.get(bar.id)
    if view is None or view.slots is not bar.slots:
        view = rt.views[bar.id] = bc.BarView(bar.id, bar.slots, lambda: _save(win))
    return view


# ── changing the set (the window's BARS tab calls these) ─────────────────────

def _bars_changed(win, rt: Runtime) -> None:
    rt.views = {k: v for k, v in rt.views.items() if rt.barset.get(k) is not None}
    quick_keys.set_bars(list(rt.barset))
    _save(win)


def add_bar(win, name: "str | None" = None):
    rt = ensure_loaded(win)
    if not rt.account:
        return None
    bar = rt.barset.add(name)
    if bar is not None:
        _bars_changed(win, rt)
    return bar


def remove_bar(win, bar_id: str) -> bool:
    rt = ensure_loaded(win)
    bar = rt.barset.remove(bar_id)
    if bar is None:
        return False
    quick_keys.forget_bar(bar_id)
    _bars_changed(win, rt)
    return True


def rename_bar(win, bar_id: str, name: str) -> "str | None":
    rt = ensure_loaded(win)
    final = rt.barset.rename(bar_id, name)
    if final is not None:
        _bars_changed(win, rt)
    return final


def replace_set(win, bars_json: list, keys_json: dict) -> None:
    """A loadout was loaded: the account's extra bars are now exactly *bars_json*, with exactly the slot keys *keys_json* says. Saves.
    Raises ValueError when no account is logged in (the bars are kept per account)."""
    rt = ensure_loaded(win)
    if not rt.account:
        raise ValueError("no account")
    old_ids = [b.id for b in rt.barset]
    rt.barset = bars_model.BarSet.from_json({"bars": bars_json})
    rt.views = {}
    rt.press_bar = rt.drag = None
    quick_keys.replace_bar_keys(old_ids, list(rt.barset), keys_json)
    _save(win)


def save_undo(win, setup: dict) -> None:
    """Keep *setup* (what a loadout load is about to replace) as the account's undo, in memory and in the file."""
    global _previous
    rt = ensure_loaded(win)
    rt.undo = setup
    if rt.account:
        _previous = {**_previous, rt.account: setup}
        quick_keys.submitter()(_write_entry, quick_keys.path(), "previous", rt.account, setup)


def clear_undo(win) -> None:
    global _previous
    rt = ensure_loaded(win)
    rt.undo = None
    if rt.account:
        _previous = {k: v for k, v in _previous.items() if k != rt.account}
        quick_keys.submitter()(_delete_entry, quick_keys.path(), "previous", rt.account)


def _write_entry(path: str, section: str, key: str, value) -> None:
    try:
        kb.write_entry(path, section, key, value)
    except Exception:
        logger.exception("extra_bars: could not save %s", path)


def _delete_entry(path: str, section: str, key: str) -> None:
    try:
        kb.delete_entry(path, section, key)
    except Exception:
        logger.exception("extra_bars: could not save %s", path)


def set_visible(win, bar_id: str, visible: bool) -> None:
    rt = ensure_loaded(win)
    bar = rt.barset.get(bar_id)
    if bar is not None and bar.visible != bool(visible):
        bar.visible = bool(visible)
        rt.press_bar = None if rt.press_bar == bar_id else rt.press_bar
        rt.drag = None if rt.drag and rt.drag["bar"] == bar_id else rt.drag
        _save(win)


def reset_place(win, bar_id: str) -> None:
    rt = ensure_loaded(win)
    rt.barset.set_place(bar_id, None, None)
    _save(win)


# ── the UI Layouts mod's view of the bars ────────────────────────────────────

def layout_capture(win) -> "dict | None":
    """Where the extra bars are, for a layout of the UI Layouts mod (it looks this function up by name, when that mod is installed):
    each bar's id, its key set and its place. Only places: a layout moves windows, it does not change what is in them or whether
    they are shown. None when there are no bars (or none are loaded yet)."""
    rt = _runtime_of(win)
    if rt is None or not len(rt.barset):
        return None
    return {"bars": [{"id": b.id, "mods": list(b.mods), "fx": b.fx, "fy": b.fy} for b in rt.barset]}


def layout_apply(win, data) -> None:
    """Put the extra bars where a layout of the UI Layouts mod had them. A bar finds its place by its id and, failing that, by its key
    set (the Shift bar, the Ctrl bar ...), so a layout made with one character's bars also fits another's. A bar the layout does not
    know stays where it is. Fractions of the screen, so there is nothing to fit to another window size."""
    rt = _runtime_of(win)
    items = data.get("bars") if isinstance(data, dict) else None
    if rt is None or not isinstance(items, list):
        return
    by_id = {i["id"]: i for i in items if isinstance(i, dict) and isinstance(i.get("id"), str)}
    by_mods: dict = {}
    for item in items:
        mods = item.get("mods") if isinstance(item, dict) else None
        if isinstance(mods, list):
            by_mods.setdefault(tuple(mods), item)
    moved = False
    for bar in rt.barset:
        entry = by_id.get(bar.id) or by_mods.get(tuple(bar.mods))
        if entry is not None:
            rt.barset.set_place(bar.id, entry.get("fx"), entry.get("fy"))
            moved = True
    if moved:
        rt.drag = None                                    # a bar that was being dragged is where the layout says now
        _save(win)


# ── placing and drawing ──────────────────────────────────────────────────────

def place(win, rt: Runtime, bar, index: int, win_w: int, win_h: int) -> "tuple[int, int]":
    """Where *bar*'s panel goes: from its fractions of the room the screen leaves it, or, with no place of its own, stacked above
    the game's bar (bar 1 right above, bar 2 above that) with the slots lined up with the game's, wherever that bar is."""
    pw, ph = bc.panel_size(win._s)
    if bar.fx is not None and bar.fy is not None:
        return round(bar.fx * max(0, win_w - pw)), round(bar.fy * max(0, win_h - ph))
    step = ph + win._s(4)
    main = getattr(win, "_hotbar_panel_rect", None)
    if main is not None:
        return main.x, main.top - (index + 1) * step
    return (win_w - pw) // 2, win_h - ph - win._s(3) - (index + 1) * step


def _layer_key(win, win_w: int, win_h: int):
    """What the retained layer of the bar in the block is keyed on: the game's own recipe for its bar (Client.py, the hotbar's
    ``_hotbar_layer_key``), so an extra bar repaints exactly when the game's would."""
    mouse = pygame.mouse.get_pos()
    hover = next((i for i, r in enumerate(win._hotbar_slot_rects) if r is not None and r.collidepoint(mouse)), None)
    dragging = win._hotbar_drag_item is not None
    item = win._hotbar_drag_item or {}
    carrying = dragging or win._hotbar_move_src is not None
    return (win_w, win_h, win._uk, win._hotbar_shown_set, win._hotbar_pos_x, win._hotbar_pos_y,
            win._hotbar_page_arrow_under(mouse),
            win._hotbar_drawn_state(hover, tooltip=not carrying), hover, win._hotbar_move_src,
            ((item.get("item_class"), item.get("item_category"), item.get("item_type"), item.get("display_name"),
              tuple(item.get("color_rgb") or ())) if dragging else None),
            mouse if carrying else None)


def draw_bars(win, frame) -> None:
    """Called once per frame (Client.py) right after the game's own bar is drawn. Total."""
    if not enabled():
        return
    try:
        rt = ensure_loaded(win)
        win_w, win_h = frame.win_w, frame.win_h
        rt.size = (win_w, win_h)                          # (a loadout turns the game bar's place into fractions of it too)
        if not len(rt.barset):
            return
        try:
            pressed = pygame.mouse.get_pressed()[0]
        except Exception:
            pressed = False
        if not pressed:                                   # a release that never arrived (focus lost) ends the gesture
            rt.press_bar = None
            rt.drag = None
        manager = getattr(win, "_gui_windows", None)
        shown = 0
        for bar in rt.barset:
            view = view_of(win, rt, bar)
            if rt.drag and rt.drag["bar"] == bar.id:
                x, y = view.state["_hotbar_pos_x"], view.state["_hotbar_pos_y"]
            else:
                x, y = place(win, rt, bar, shown if bar.visible else 0, win_w, win_h)
            view.state["_hotbar_pos_x"], view.state["_hotbar_pos_y"] = x, y
            with bc.in_bar(win, view):
                win._hotbar_frame_tick()                  # the lift / held-key timers, as for the game's bar
                if bar.visible:
                    win._draw_retained_gpu_layer(
                        f"hud:quickbar:{bar.id}", frame, _layer_key(win, win_w, win_h),
                        lambda f: win._draw_hotbar(f, draw_animation=False, tick_press=False))
                    win._draw_hotbar_animation(frame)
            if bar.visible:
                shown += 1
                if manager is not None:
                    manager.register(BAR_ID_PREFIX + bar.id, view.panel_rect, layer=GuiLayer.HUD, focusable=False,
                                     rect_getter=lambda v=view: v.panel_rect)
    except Exception:
        _switch_off("draw_bars()")


# ── the mouse ────────────────────────────────────────────────────────────────

def bar_at(win, rt: Runtime, pos):
    """The shown bar that owns the pointer at *pos* (a window above a bar owns it instead), or None."""
    manager = getattr(win, "_gui_windows", None)
    if manager is not None:
        owner = manager.route_pointer(pos).owner
        if isinstance(owner, str) and owner.startswith(BAR_ID_PREFIX):
            bar = rt.barset.get(owner[len(BAR_ID_PREFIX):])
            return bar if bar is not None and bar.visible else None
        return None
    return next((b for b in rt.barset if b.visible and view_of(win, rt, b).panel_rect is not None
                 and view_of(win, rt, b).panel_rect.collidepoint(pos)), None)


def _clamp_place(win, rt: Runtime, x: int, y: int) -> "tuple[int, int, float, float]":
    pw, ph = bc.panel_size(win._s)
    room_w, room_h = max(0, rt.size[0] - pw), max(0, rt.size[1] - ph)
    x, y = max(0, min(room_w, x)), max(0, min(room_h, y))
    return x, y, (x / room_w if room_w else 0.0), (y / room_h if room_h else 0.0)


def handle_event(win, event) -> bool:
    """One mouse event: True when an extra bar used it up (the game never sees it). Total."""
    if not enabled():
        return False
    try:
        rt = _runtime_of(win)
        if rt is None or not len(rt.barset):
            return False
        kind = event.type
        if kind == pygame.MOUSEBUTTONDOWN:
            return _press(win, rt, event)
        if kind == pygame.MOUSEMOTION:
            return _motion(win, rt, event)
        if kind == pygame.MOUSEBUTTONUP and event.button == 1:
            return _release(win, rt, event)
        return False
    except Exception:
        _switch_off("handle_event()")
        return False


def _press(win, rt: Runtime, event) -> bool:
    bar = bar_at(win, rt, event.pos)
    if bar is None:
        return False
    view = view_of(win, rt, bar)
    if event.button == 1:
        with bc.in_bar(win, view):
            on_slot = bool(win._hotbar_on_mouse_down(event.pos))
        if on_slot:
            rt.press_bar = bar.id
        else:                                             # the padding or a gap: pick the whole bar up
            rect = view.panel_rect
            rt.drag = {"bar": bar.id, "dx": event.pos[0] - rect.x, "dy": event.pos[1] - rect.y}
        return True
    if event.button == 3:
        with bc.in_bar(win, view):
            before = getattr(win, "_class_skill_menu", None)
            if not getattr(type(win), "_extra_bars_skill_menu", False) and _slot_is_empty(win, event.pos):
                return True                               # no way to bind a class skill to this bar: swallow, do nothing
            used = bool(win._hotbar_on_right_click(event.pos))
            menu = getattr(win, "_class_skill_menu", None)
            if menu is not None and menu is not before:
                menu["qb_bar"] = bar.id                   # the menu answers later, outside this block: remember whose it is
        return used
    return False


def _slot_is_empty(win, pos) -> bool:
    slot = win._hotbar_slot_under(pos)
    return slot is not None and win._hotbar[slot] is None


def _motion(win, rt: Runtime, event) -> bool:
    drag = rt.drag
    if drag is None:
        return False
    if not event.buttons[0]:
        rt.drag = None
        return False
    bar = rt.barset.get(drag["bar"])
    if bar is None:
        rt.drag = None
        return False
    x, y, fx, fy = _clamp_place(win, rt, event.pos[0] - drag["dx"], event.pos[1] - drag["dy"])
    view = view_of(win, rt, bar)
    view.state["_hotbar_pos_x"], view.state["_hotbar_pos_y"] = x, y
    rt.barset.set_place(bar.id, fx, fy)
    return True


def _release(win, rt: Runtime, event) -> bool:
    if rt.drag is not None:
        rt.drag = None
        _save(win)
        return True
    if rt.press_bar is not None:
        bar = rt.barset.get(rt.press_bar)
        rt.press_bar = None
        if bar is not None:
            owned = bar_at(win, rt, event.pos) is bar
            with bc.in_bar(win, view_of(win, rt, bar)):
                win._hotbar_on_mouse_up(event.pos, owned)
        return False                                      # the game's own release handling still runs, as for its own bar
    if getattr(win, "_hotbar_drag_item", None) is not None:
        bar = bar_at(win, rt, event.pos)
        if bar is not None:                               # an item dragged out of the inventory, dropped on a bar
            with bc.in_bar(win, view_of(win, rt, bar)):
                win._hotbar_on_mouse_up(event.pos, True)
    return False


# ── the keys ─────────────────────────────────────────────────────────────────

def end_holds(win, keep: "str | None" = None) -> None:
    """End the held-key repeat of every bar but *keep* (a bar id, or "main"): the server repeats one held key at a time."""
    rt = _runtime_of(win)
    if rt is None:
        return
    if keep != "main" and hasattr(win, "_hotbar_key_hold_end"):
        win._hotbar_key_hold_end()
    for bar in rt.barset:
        if bar.id != keep:
            with bc.in_bar(win, view_of(win, rt, bar)):
                win._hotbar_key_hold_end()


def fire_slot(win, bar_id: str, index: int, event) -> bool:
    """A key bound to slot *index* of extra bar *bar_id* was pressed. True when the key press is used up (a hidden bar uses it up
    without doing anything); False when this window has no such bar or lacks the game's bar methods."""
    rt = _runtime_of(win)
    bar = rt.barset.get(bar_id) if rt is not None else None
    needed = ("_hotbar_activate_slot", "_hotbar_key_hold_begin", "_hotbar_key_hold_end")
    if bar is None or not all(hasattr(win, name) for name in needed):
        return False
    if not bar.visible:
        return True
    end_holds(win, keep=bar_id)
    with bc.in_bar(win, view_of(win, rt, bar)):
        win._hotbar_activate_slot(index, from_key=True)
        win._hotbar_key_hold_begin(index, event.key)
    return True


# ── the class-skill menu ─────────────────────────────────────────────────────

def _menu_view(win):
    menu = getattr(win, "_class_skill_menu", None)
    bar_id = menu.get("qb_bar") if isinstance(menu, dict) else None
    rt = _runtime_of(win)
    bar = rt.barset.get(bar_id) if (bar_id and rt is not None) else None
    return view_of(win, rt, bar) if bar is not None else None


def _wrap_in_menu_bar(original):
    def wrapper(self, *args, **kwargs):
        view = _menu_view(self)
        if view is None:
            return original(self, *args, **kwargs)
        with bc.in_bar(self, view):
            return original(self, *args, **kwargs)

    wrapper.__wrapped__ = original
    wrapper.__doc__ = original.__doc__
    return wrapper


def install(window_cls) -> bool:
    """Load the saved bars and wrap the two class-skill menu methods so the menu draws and binds on the bar that opened it. If a
    game update renames them the extra bars still work, only without that menu (a right-click on an empty slot does nothing
    there). Idempotent."""
    if window_cls.__dict__.get("_extra_bars_installed"):
        return False
    load()
    names = ("_hotbar_skill_menu_event", "_draw_class_skill_menu")
    wrapped = all(callable(getattr(window_cls, name, None)) for name in names)
    if wrapped:
        for name in names:
            setattr(window_cls, name, _wrap_in_menu_bar(getattr(window_cls, name)))
    else:
        logger.warning("extra_bars: the game has no class-skill menu methods %s; extra bars get no skill menu", names)
    window_cls._extra_bars_skill_menu = wrapped
    window_cls._extra_bars_installed = True
    return True

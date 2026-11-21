"""quick_keys.py - carries out the key bindings of keybinds.py inside the game's event loop.

The game's one-press hotkeys are ``elif event.key == pygame.K_x`` branches of a single chain in the event loop, after every text
field has had its first claim on the key and after the loop's repeat guard (``_keys_held``). One line of the mod's patch sits
right there and calls ``on_key_down``:

  * a key bound to a game HOTKEY comes back as a copy of the event with the action's own default key (a player who moved Dock
    to B presses B and the loop sees R), so none of the game's hotkey code is touched;
  * a key bound to a quick action SLOT or a TAB switch is carried out here and the event is used up: the slot is fired through the
    game's own ``_hotbar_activate_slot`` and ``_hotbar_key_hold_begin`` (the physical key is what the held-key repeat polls);
  * the old key of an action that moved away is used up too (nothing happens);
  * every other key goes on to the game unchanged.

With no binding changed the function does nothing at all, so the game plays exactly as it does without the mod. Every function
the game's code calls here is total: an error switches the mod's key handling off for the session.
"""

from __future__ import annotations

import logging
import os

import pygame

import keybinds as kb

logger = logging.getLogger(__name__)

ENV_OFF = "STAR_EMPIRE_NO_QUICK_KEYS"

_failed = False
_path: "str | None" = None
_raw: dict = {}                       # the file's "keys" section as saved; turned into a Table on first use (pygame is up)
_table: "kb.Table | None" = None
_bars: list = []                      # the extra quick action bars the logged-in account has (extra_bars.py): the table has their slots
_submit = None


def _switch_off(where: str) -> None:
    global _failed
    if not _failed:
        _failed = True
        logger.exception("quick_keys: switched off for this session (%s)", where)


def enabled() -> bool:
    return not _failed and not os.environ.get(ENV_OFF)


def path() -> str:
    return _path or kb.default_path()


def load(path_: "str | None" = None) -> None:
    """Read the saved bindings (a small file, once, at start-up). They become a table on first use."""
    global _path, _raw, _table
    if path_ is not None:
        _path = path_
    data = kb.read_file(path())
    _raw = data.get("keys") if isinstance(data.get("keys"), dict) else {}
    _table = None


def table() -> "kb.Table":
    global _table
    if _table is None:
        _table = kb.Table.from_json(_raw, _bars)
    return _table


def set_bars(bars) -> None:
    """The account's extra bars are these: their slots join the table (every change made so far stays)."""
    global _table, _bars
    changes = _table.to_json() if _table is not None else _raw
    _bars = list(bars)
    _table = kb.Table.from_json(changes, _bars)


def forget_bar(bar_id: str) -> None:
    """A bar was deleted: drop the keys that were changed for its slots too, and save."""
    global _table
    prefix = f"slot.{bar_id}."
    _table = kb.Table.from_json({k: v for k, v in table().to_json().items() if not k.startswith(prefix)}, _bars)
    _save()


def _is_bar_key(action_id: str, bar_ids) -> bool:
    return any(action_id.startswith(f"slot.{bar_id}.") for bar_id in bar_ids)


def bar_key_overrides(bar_ids) -> dict:
    """The keys changed for the slots of these bars, as they are saved (a loadout carries them with the bars)."""
    return {k: v for k, v in table().to_json().items() if _is_bar_key(k, bar_ids)}


def replace_bar_keys(old_ids, new_bars, keys_json: dict) -> None:
    """The account's extra bars are now *new_bars* (a loadout was loaded): the keys changed for the old bars' slots go (a loadout that
    had them has them saved), the new bars start with exactly the keys *keys_json* says, and everything else stays. Saves."""
    global _table, _bars
    gone = set(old_ids) | {b.id for b in new_bars}
    changes = {k: v for k, v in table().to_json().items() if not _is_bar_key(k, gone)}
    changes.update(keys_json or {})
    _bars = list(new_bars)
    _table = kb.Table.from_json(changes, _bars)
    _save()


def submitter():
    """What queues a file write on the background writer (``scan_writer.submit``; a test passes its own)."""
    if _submit is not None:
        return _submit
    from scan_writer import submit
    return submit


def _save() -> None:
    submitter()(_write, path(), table().to_json())


def _write(target: str, snapshot: dict) -> None:
    try:
        kb.write_section(target, "keys", snapshot)
    except Exception:
        logger.exception("quick_keys: could not save %s", target)


def configure_for_tests(path_: str, submit=None) -> None:
    global _submit, _failed
    _submit = submit
    _failed = False
    load(path_)


def reset_for_tests() -> None:
    global _path, _raw, _table, _bars, _submit, _failed
    _path, _raw, _table, _bars, _submit, _failed = None, {}, None, [], None, False


# ── changing the bindings (the window calls these) ───────────────────────────

def set_binding(action_id: str, combo: "kb.Combo | None") -> "list[kb.Action]":
    """Bind a combo (None: unbind) and save. Returns the actions that lost the combo."""
    displaced = table().set(action_id, combo)
    _save()
    return displaced


def reset_binding(action_id: str) -> None:
    table().reset(action_id)
    _save()


def reset_all() -> None:
    table().reset_all()
    _save()


# ── what the game's loop calls ───────────────────────────────────────────────

def _carry_out_slot(win, index: int, event) -> bool:
    """Fire slot *index* of the tab the bar shows, as the game's own number-key branch does. False when the game window has no
    such bar (then the key is left to the game)."""
    needed = ("_hotbar_shown_offset", "_hotbar_activate_slot", "_hotbar_key_hold_begin")
    if not all(hasattr(win, name) for name in needed):
        return False
    import extra_bars
    extra_bars.end_holds(win, keep="main")                 # one held key at a time, whichever bar it is on
    slot = win._hotbar_shown_offset() + index
    win._hotbar_activate_slot(slot, from_key=True)
    win._hotbar_key_hold_begin(slot, event.key)
    return True


def _hotkey_event(event, key: int):
    """A copy of the key press that says *key* was pressed (the modifiers, the character and the scan code stay)."""
    return pygame.event.Event(event.type, key=key, mod=getattr(event, "mod", 0), unicode=getattr(event, "unicode", ""),
                              scancode=getattr(event, "scancode", 0))


def on_key_down(win, event):
    """The loop's one-press hotkey hook: ``(event, consumed)``. Use ``event`` from here on; skip the rest of the loop body for
    this event when ``consumed``. Total."""
    if not enabled():
        return event, False
    try:
        current = table()
        if current.is_default and not current.has_bars:
            return event, False
        result = current.resolve(event.key, kb.mods_from_event(getattr(event, "mod", 0)))
        action = result.action
        if action is None:
            return event, result.swallow
        if action.kind == "hotkey":
            if event.key == action.default.key:
                return event, False
            return _hotkey_event(event, action.default.key), False
        if action.kind == "slot":
            return event, _carry_out_slot(win, action.arg, event)
        if action.kind == "barslot":
            import extra_bars
            return event, extra_bars.fire_slot(win, action.bar, action.arg, event)
        if action.kind == "loadout":
            import loadout_ops
            return event, loadout_ops.cycle(win, action.arg)
        if action.kind == "tab":
            if not hasattr(win, "_hotbar_page"):
                return event, False
            win._hotbar_page(action.arg)
            return event, True
        return event, False
    except Exception:
        _switch_off("on_key_down()")
        return event, False


def _labels(current, bar_id: "str | None") -> tuple:
    if bar_id:
        return tuple(kb.short_label(current.combo(kb.bar_slot_id(bar_id, i))) for i in range(10))
    return tuple(kb.short_label(current.combo(kb.slot_id(i))) for i in range(10))


def label_key(win, value, *args, **kwargs):
    """Runs after the game's ``_hotbar_drawn_state`` (what the retained bar layer is keyed on): adds the ten slot labels, so the
    bar repaints when a key changes. Nothing is added to the game's own bar while every binding is its own; an extra bar always
    gets its labels."""
    if not enabled():
        return None
    try:
        import bar_context
        current = table()
        bar_id = bar_context.current_bar_id()
        if current.is_default and not bar_id:
            return None
        return (*value, _labels(current, bar_id))
    except Exception:
        _switch_off("label_key()")
        return None


def install(window_cls) -> "list[str]":
    """Hook ``label_key`` on the game's bar state method. Never raises (see ``modloader.install_methods``)."""
    import modloader
    return modloader.install_methods(window_cls, None, None, (), result={"_hotbar_drawn_state": label_key})


# what test_mods_apply checks after every game update: (game module, game class, game method, the function that runs after it)
HOOKS = (("Client", "SolarSystemWindow", "_hotbar_drawn_state", "label_key"),)


def slot_label(win, index: int, default: str) -> str:
    """The text on slot *index*'s key tab: on an extra bar always the key its slot has; on the game's bar its own number while no
    binding is changed, else the slot's combo."""
    if not enabled():
        return default
    try:
        import bar_context
        current = table()
        bar_id = bar_context.current_bar_id()
        if bar_id:
            return kb.short_label(current.combo(kb.bar_slot_id(bar_id, index)))
        if current.is_default:
            return default
        return kb.short_label(current.combo(kb.slot_id(index)))
    except Exception:
        _switch_off("slot_label()")
        return default

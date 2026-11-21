"""loadout_ops.py - capture, load and undo loadouts (loadouts.py) on the running game.

CAPTURE reads the game's own bar through the game's own ``_hotbar_layout_out`` (the list it keeps in options.txt), the account's extra
bars (extra_bars.py) and the keys changed for their slots. LOAD puts a setup back: the game's bar through ``_hotbar_layout_in`` (padded
with empty slots to the bar's length, so slots the loadout did not fill are emptied, as the game's own loader would leave them),
the class-skill bindings refreshed and the bar saved to options.txt as the game does after any change, then the extra bars and their keys
as one step (``extra_bars.replace_set``). What a load replaces is kept first, as the account's UNDO (in memory and in the file), and a
load the game's loader chokes on is rolled back.

Which saved loadout is what you have now is not remembered but worked out: the one whose setup is exactly the captured one, so it is
right after a restart too, and stops being right the moment you change a slot.
"""

from __future__ import annotations

import logging

import extra_bars
import loadouts
import quick_keys

logger = logging.getLogger(__name__)

_OK, _WARN = (120, 220, 150), (255, 180, 60)

STORE = loadouts.LoadoutStore(quick_keys.path, quick_keys.submitter)


def reset_for_tests() -> None:
    global STORE
    STORE = loadouts.LoadoutStore(quick_keys.path, quick_keys.submitter)


def capture(win) -> dict:
    """What the game's bar and the account's extra bars hold now, and the keys changed for the extra bars' slots."""
    rt = extra_bars.ensure_loaded(win)
    layout_out = getattr(win, "_hotbar_layout_out", None)
    main = list(layout_out()) if callable(layout_out) else []
    ids = [b.id for b in rt.barset]
    return loadouts.clean_setup({"main": main, "bars": [b.to_json() for b in rt.barset],
                                 "keys": quick_keys.bar_key_overrides(ids)})


def _put(win, setup: dict) -> None:
    """Make *setup* what the game's bar and the extra bars hold. Raises when the game's loader does."""
    layout_in = getattr(win, "_hotbar_layout_in", None)
    if callable(layout_in) and hasattr(win, "_hotbar"):
        slots = list(setup["main"]) + [None] * max(0, len(win._hotbar) - len(setup["main"]))
        layout_in(slots)
        refresh = getattr(win, "_refresh_class_skill_bindings", None)
        if callable(refresh):
            refresh()
        save = getattr(win, "_hotbar_save", None)
        if callable(save):
            save()
    extra_bars.replace_set(win, setup["bars"], setup["keys"])


def apply(win, setup, remember: bool = True) -> "tuple[bool, str]":
    """Load *setup*. With *remember* what it replaces becomes the account's UNDO. Returns (ok, what to tell the player)."""
    rt = extra_bars.ensure_loaded(win)
    if not rt.account:
        return False, "Log in first: each character has its own bars."
    setup = loadouts.clean_setup(setup)
    if setup is None:
        return False, "That loadout cannot be read."
    before = capture(win)
    try:
        _put(win, setup)
    except Exception:
        logger.exception("loadout_ops: could not load a loadout")
        try:
            _put(win, before)                                   # best effort: leave things as they were
        except Exception:
            logger.exception("loadout_ops: could not put the bars back either")
        return False, "Could not load that loadout (the log says why); your bars were left as they were."
    if remember:
        extra_bars.save_undo(win, before)
    return True, ""


def load(win, loadout: dict) -> "tuple[bool, str]":
    ok, text = apply(win, loadouts.setup_of(loadout))
    return (True, f"Loaded '{loadout['name']}'. UNDO brings back what you had.") if ok else (False, text)


def undo(win) -> "tuple[bool, str]":
    rt = extra_bars.ensure_loaded(win)
    if rt.undo is None:
        return False, "Nothing to undo."
    ok, text = apply(win, rt.undo, remember=False)
    if not ok:
        return False, text
    extra_bars.clear_undo(win)
    return True, "Put your bars back as they were before the last load."


def save_current(win, name: str, target_id: "str | None" = None) -> "tuple[bool, str]":
    """Store what the game's bar and the extra bars hold now. With *target_id* that loadout is replaced (name and id kept)."""
    rt = extra_bars.ensure_loaded(win)
    if not rt.account:
        return False, "Log in first: each character has its own bars."
    if not STORE.loaded:
        return False, "Still loading the saved loadouts; try again in a moment."
    previous = STORE.get(target_id) if target_id else None
    item = loadouts.new_loadout(previous["name"] if previous else name, capture(win))
    if previous is not None:
        item["id"] = previous["id"]
    STORE.save(item)
    verb = "Updated" if previous is not None else "Saved"
    return True, f"{verb} '{item['name']}' ({loadouts.summary(item)})."


def match(win, items=None) -> "dict | None":
    """The saved loadout that is exactly what you have now, if there is one."""
    key = loadouts.setup_key(capture(win))
    for item in (STORE.items if items is None else items):
        if loadouts.setup_key(loadouts.setup_of(item)) == key:
            return item
    return None


def cycle(win, step: int) -> bool:
    """Load the next (step 1) or previous (-1) loadout in name order after the one you have now (the first / last when you have none).
    Says what happened in chat. Always True: the key press is used up."""
    chat = getattr(win, "chat_log", None)
    items = STORE.items
    if not items:
        text, ok = "No loadouts saved yet: arrange your bars, then save them in the QUICK BARS window.", False
    else:
        current = match(win, items)
        if current is None:
            target = items[0] if step > 0 else items[-1]
        else:
            target = items[(items.index(current) + step) % len(items)]
        ok, text = load(win, target)
    if callable(chat):
        chat(text, _OK if ok else _WARN)
    return True

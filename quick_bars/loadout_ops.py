"""loadout_ops.py - capture, load and undo loadouts (loadouts.py) on the running game.

CAPTURE reads the game's own bar through the game's own ``_hotbar_layout_out`` (the list it keeps in options.txt), where that bar sits
(two fractions of the room the screen leaves it), the account's extra bars (extra_bars.py) and the keys changed for their slots. LOAD puts a setup back: the game's bar through ``_hotbar_layout_in`` (padded
with empty slots to the bar's length, so slots the loadout did not fill are emptied, as the game's own loader would leave them),
the class-skill bindings refreshed, the bar put where the loadout had it and saved to options.txt as the game does after any change,
then the extra bars and their keys as one step (``extra_bars.replace_set``). What a load replaces is kept first, as the account's UNDO (in memory and in the file), and a
load the game's loader chokes on is rolled back.

Which saved loadout is what you have now is not remembered but worked out: the one whose setup is exactly the captured one, so it is
right after a restart too, and stops being right the moment you change a slot.
"""

from __future__ import annotations

import json
import logging

import extra_bars
import loadouts
import quick_keys

logger = logging.getLogger(__name__)

_OK, _WARN = (120, 220, 150), (255, 180, 60)

STORE = loadouts.LoadoutStore(quick_keys.path, quick_keys.submitter)
_UNKNOWN = object()


def reset_for_tests() -> None:
    global STORE
    STORE = loadouts.LoadoutStore(quick_keys.path, quick_keys.submitter)


def _room(win) -> "tuple[int, int] | None":
    """The room the screen leaves the game's bar (screen minus its panel), or None while that is not known (nothing drawn yet)."""
    rt = extra_bars.runtime(win)
    rect = getattr(win, "_hotbar_panel_rect", None)
    if rect is None or not rt.size[0] or not rt.size[1]:
        return None
    return max(0, rt.size[0] - rect.w), max(0, rt.size[1] - rect.h)


def _capture_place(win):
    """Where the game's bar sits, as fractions of its room: None for the default place, ``_UNKNOWN`` when it has been moved but the
    room is not known (then the loadout does not record a place at all)."""
    x, y = getattr(win, "_hotbar_pos_x", None), getattr(win, "_hotbar_pos_y", None)
    if x is None or y is None:
        return None
    room = _room(win)
    if room is None:
        return _UNKNOWN
    return {"fx": round(x / room[0], 6) if room[0] else 0.0, "fy": round(y / room[1], 6) if room[1] else 0.0}


def _place_pixels(win, place) -> "list | None":
    """A stored place as the pixel position it is at on this screen (None: the default place)."""
    if place is None:
        return None
    room = _room(win)
    if room is None:
        return None
    return [max(0, min(room[0], round(place["fx"] * room[0]))), max(0, min(room[1], round(place["fy"] * room[1])))]


def capture(win) -> dict:
    """What the game's bar and the account's extra bars hold now, where the game's bar sits, and the keys changed for the extra bars'
    slots."""
    rt = extra_bars.ensure_loaded(win)
    layout_out = getattr(win, "_hotbar_layout_out", None)
    main = list(layout_out()) if callable(layout_out) else []
    ids = [b.id for b in rt.barset]
    setup = {"main": main, "bars": [b.to_json() for b in rt.barset], "keys": quick_keys.bar_key_overrides(ids)}
    place = _capture_place(win)
    if place is not _UNKNOWN:
        setup["main_place"] = place
    return loadouts.clean_setup(setup)


def _put_place(win, setup: dict) -> None:
    """Put the game's bar where the setup had it. A setup with no place of its own (saved before places were kept, or captured before
    anything was drawn) leaves the bar where it is; None is the default place."""
    if "main_place" not in setup:
        return
    place = setup["main_place"]
    if place is None:
        win._hotbar_pos_x = win._hotbar_pos_y = None
        return
    pixels = _place_pixels(win, place)
    if pixels is not None:
        win._hotbar_pos_x, win._hotbar_pos_y = pixels


def _put(win, setup: dict) -> None:
    """Make *setup* what the game's bar and the extra bars hold. Raises when the game's loader does."""
    layout_in = getattr(win, "_hotbar_layout_in", None)
    if callable(layout_in) and hasattr(win, "_hotbar"):
        slots = list(setup["main"]) + [None] * max(0, len(win._hotbar) - len(setup["main"]))
        layout_in(slots)
        refresh = getattr(win, "_refresh_class_skill_bindings", None)
        if callable(refresh):
            refresh()
        _put_place(win, setup)
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


def _match_key(win, setup: dict, with_place: bool) -> str:
    """The comparison key of a setup. The game bar's place is compared as the pixel position it is at on this screen (a stored place
    is fractions, which come back a pixel off on another screen size); *with_place* False leaves it out (a loadout saved without one)."""
    cleaned = loadouts.clean_setup(setup)
    place = cleaned.pop("main_place", None)
    key = loadouts.setup_key(cleaned)
    return key + json.dumps(_place_pixels(win, place)) if with_place else key


def match(win, items=None) -> "dict | None":
    """The saved loadout that is exactly what you have now, if there is one."""
    captured = capture(win)
    keys = {}
    for item in (STORE.items if items is None else items):
        setup = loadouts.setup_of(item)
        with_place = "main_place" in setup
        if with_place not in keys:
            keys[with_place] = _match_key(win, captured, with_place)
        if _match_key(win, setup, with_place) == keys[with_place]:
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

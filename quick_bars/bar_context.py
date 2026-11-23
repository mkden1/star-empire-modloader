"""bar_context.py - run the game's own quick action bar code on one of the EXTRA bars.

The game keeps ONE bar in a handful of attributes of the window (``_hotbar``, where it is drawn, the rectangles it published for
the mouse, the press / lift / held-key state) and every piece of bar code reads them: drawing it, activating a slot, a click, a
drop from the inventory, a right-click. So an extra bar does not need its own copy of all that code. ``in_bar`` makes the bar
the game's bar for the length of a ``with`` block: it swaps the bar's state into those attributes, and swaps the game's own back
afterwards, whatever happens in between::

    with in_bar(window, view):
        window._draw_hotbar(frame, draw_animation=False, tick_press=False)   # draws the extra bar, publishes ITS rectangles

While the block runs the game's ``_hotbar_save`` (which writes the bar's contents into options.txt as the game's own hotbar) is
replaced by the bar's own save, so binding or clearing a slot of an extra bar can never overwrite the game's saved bar.

Everything is plain attribute swapping on the one thread the game's loop runs on; no game import.
"""

from __future__ import annotations

import contextlib

SLOTS = 10
# Everything the game's bar code keeps per bar. (Not per bar, and left alone: the item being dragged out of the inventory, the
# Queue Delay, the inventory tallies the slots' badges are drawn from, the drone / wing presses still waiting for the server's
# answer (``_hotbar_spent``, per item type: the charges are the player's, not a bar's).)
STATE_ATTRS = (
    "_hotbar", "_hotbar_shown_set", "_hotbar_pos_x", "_hotbar_pos_y", "_hotbar_slot_rects", "_hotbar_page_rects",
    "_hotbar_panel_rect", "_hotbar_layout_cache", "_hotbar_animation_geometry", "_hotbar_press_slot",
    "_hotbar_press_time", "_hotbar_press_pos", "_hotbar_move_src", "_hotbar_page_dwell", "_hotbar_key_hold")
_MISSING = object()
_current: "list[str]" = []


def panel_size(s) -> "tuple[int, int]":
    """The size of a one-tab bar's panel for the UI scale function *s* (``window._s``), the way ``_draw_hotbar`` adds it up: ten
    48 px slots, nine 4 px gaps and 7 px of padding each side, each term scaled (and rounded) on its own. A test checks it against
    the game's own draw at every UI scale."""
    return SLOTS * s(48) + (SLOTS - 1) * s(4) + 2 * s(7), 2 * s(7) + s(48)


def fresh_state(slots: list) -> dict:
    """The state of a bar nobody has drawn or touched yet."""
    return {"_hotbar": slots, "_hotbar_shown_set": 0, "_hotbar_pos_x": None, "_hotbar_pos_y": None,
            "_hotbar_slot_rects": [None] * SLOTS, "_hotbar_page_rects": (None, None), "_hotbar_panel_rect": None,
            "_hotbar_layout_cache": None, "_hotbar_animation_geometry": None, "_hotbar_press_slot": None,
            "_hotbar_press_time": 0.0, "_hotbar_press_pos": None, "_hotbar_move_src": None, "_hotbar_page_dwell": None,
            "_hotbar_key_hold": None}


class BarView:
    """One extra bar as the game's bar code sees it: its slots (the same list the bar object holds, edited in place), the rest
    of its state, and what to call when the game asks to save it."""

    def __init__(self, bar_id: str, slots: list, on_save=None):
        self.bar_id = bar_id
        self.slots = slots
        self.state = fresh_state(slots)
        self.on_save = on_save

    @property
    def panel_rect(self):
        return self.state["_hotbar_panel_rect"]


def current_bar_id() -> "str | None":
    """The extra bar whose state is swapped in right now, None when it is the game's own bar."""
    return _current[-1] if _current else None


@contextlib.contextmanager
def in_bar(win, view: BarView):
    """Make *view* the window's quick action bar until the block ends."""
    own = vars(win)
    saved = {name: own.get(name, _MISSING) for name in STATE_ATTRS}
    saved_save = own.get("_hotbar_save", _MISSING)
    for name in STATE_ATTRS:
        setattr(win, name, view.state[name])

    def hotbar_save() -> None:
        if view.on_save is not None:
            view.on_save()

    win._hotbar_save = hotbar_save
    _current.append(view.bar_id)
    try:
        yield view
    finally:
        _current.pop()
        for name in STATE_ATTRS:
            view.state[name] = own.get(name, view.state[name])
        game_list = view.state["_hotbar"]
        if game_list is not view.slots:                       # the game rebound the list: keep the bar's own object
            view.slots[:] = list(game_list)[:SLOTS] + [None] * max(0, SLOTS - len(game_list))
            view.state["_hotbar"] = view.slots
        for name, value in saved.items():
            if value is _MISSING:
                own.pop(name, None)
            else:
                setattr(win, name, value)
        if saved_save is _MISSING:
            own.pop("_hotbar_save", None)
        else:
            win._hotbar_save = saved_save

"""bars.py - the extra quick action bars: what a bar is, the set an account has, and how it is saved.

Pure Python: no pygame, no game imports.

An extra BAR is one more row of ten quick action slots, drawn as its own panel anywhere on screen. It holds what the game's own bar
holds in a slot (a binding by item type: ``item_class``, ``item_category``, ``item_type``, ``display_name``, ``color_rgb``), so
dragging an item onto a slot, firing it, moving slots about and clearing them all work through the game's own bar code (see
bar_context.py). Each bar also has:

  * a NAME, shown in the key bindings list;
  * its own KEY SET: the number keys 1-0 with the Ctrl / Shift / Alt it was given when it was made (the first bar Shift, the
    next Ctrl, then Alt, then pairs), so Shift+1 fires slot 1 of the first extra bar while a plain 1 stays the game's bar. They
    are only defaults; every slot's key can be changed in the key bindings list;
  * a PLACE as two fractions, how far across and down the room the screen leaves it (0 = the left / top edge, 1 = the right /
    bottom edge), so the bar keeps its relative place whatever the window size, and can never be off screen. None is the default
    place, stacked above the game's bar;
  * whether it is SHOWN. A hidden bar is not drawn and its keys do nothing.

Every account has its own set (the game keeps each character's bar apart too), saved in ``quick_bars.json`` under ``bars``.
"""

from __future__ import annotations

import copy
import re
import uuid
from dataclasses import dataclass, field

SLOTS = 10
MAX_BARS = 6
MAX_NAME_LEN = 24
# The key set a new bar gets: the first one no bar has.
MOD_SETS = (("shift",), ("ctrl",), ("alt",), ("ctrl", "shift"), ("ctrl", "alt"), ("shift", "alt"))
_ID = re.compile(r"^[A-Za-z0-9_-]{1,16}$")


def clean_name(name) -> str:
    return " ".join(str(name or "").split())[:MAX_NAME_LEN]


def clean_binding(value) -> "dict | None":
    """A slot's binding the way the game's own loader keeps it (``hotbar._hotbar_layout_in``), or None for an empty or unusable one.
    A copy, so nothing outside can change a slot."""
    if not isinstance(value, dict) or not value.get("item_type") or not value.get("item_category"):
        return None
    color = value.get("color_rgb") or [255, 180, 80]
    return {"item_class": str(value.get("item_class", "cargo_item")),
            "item_category": str(value.get("item_category", "")),
            "item_type": str(value.get("item_type", "")),
            "display_name": str(value.get("display_name", value.get("item_type", ""))),
            "color_rgb": list(color) if isinstance(color, (list, tuple)) else [255, 180, 80]}


def _fraction(value) -> "float | None":
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0.0, min(1.0, float(value)))


@dataclass
class Bar:
    id: str
    name: str
    mods: tuple
    fx: "float | None" = None
    fy: "float | None" = None
    visible: bool = True
    slots: list = field(default_factory=lambda: [None] * SLOTS)

    def to_json(self) -> dict:
        return {"id": self.id, "name": self.name, "mods": list(self.mods), "fx": self.fx, "fy": self.fy,
                "visible": self.visible, "slots": [copy.deepcopy(s) for s in self.slots]}

    @property
    def filled(self) -> int:
        return sum(1 for s in self.slots if s is not None)


def _new_id() -> str:
    return "b" + uuid.uuid4().hex[:6]


class BarSet:
    """The extra bars of one account, in the order they were made."""

    def __init__(self, bars=(), id_factory=_new_id):
        self.bars: "list[Bar]" = list(bars)
        self._id_factory = id_factory

    def __iter__(self):
        return iter(self.bars)

    def __len__(self) -> int:
        return len(self.bars)

    def get(self, bar_id) -> "Bar | None":
        return next((b for b in self.bars if b.id == bar_id), None)

    def free_mods(self) -> "tuple | None":
        taken = {tuple(b.mods) for b in self.bars}
        return next((m for m in MOD_SETS if m not in taken), None)

    def unique_name(self, name: str, ignoring: str = "") -> str:
        """*name*, or "name (2)", "name (3)" ... when another bar has it (any case)."""
        base = clean_name(name) or "Bar"
        taken = {b.name.lower() for b in self.bars if b.id != ignoring}
        if base.lower() not in taken:
            return base
        number = 2
        while True:
            suffix = f" ({number})"
            candidate = base[:MAX_NAME_LEN - len(suffix)] + suffix
            if candidate.lower() not in taken:
                return candidate
            number += 1

    def add(self, name: "str | None" = None) -> "Bar | None":
        """A new empty bar with the next free key set; None when the account already has MAX_BARS."""
        mods = self.free_mods()
        if len(self.bars) >= MAX_BARS or mods is None:
            return None
        used = {b.id for b in self.bars}
        bar_id = self._id_factory()
        while bar_id in used:
            bar_id = self._id_factory()
        bar = Bar(bar_id, self.unique_name(name or f"Bar {len(self.bars) + 2}"), mods)
        self.bars.append(bar)
        return bar

    def remove(self, bar_id) -> "Bar | None":
        bar = self.get(bar_id)
        if bar is not None:
            self.bars.remove(bar)
        return bar

    def rename(self, bar_id, name) -> "str | None":
        bar = self.get(bar_id)
        if bar is None or not clean_name(name):
            return None
        bar.name = self.unique_name(name, ignoring=bar_id)
        return bar.name

    def set_place(self, bar_id, fx, fy) -> None:
        bar = self.get(bar_id)
        if bar is not None:
            bar.fx, bar.fy = _fraction(fx), _fraction(fy)

    def to_json(self) -> dict:
        return {"version": 1, "bars": [b.to_json() for b in self.bars]}

    @classmethod
    def from_json(cls, data, id_factory=_new_id) -> "BarSet":
        """The set a saved entry stands for. Anything unusable is skipped (a bar with a bad id, a repeated id, a name nobody can
        read); a repeated or unknown key set is replaced by a free one; there are never more than MAX_BARS."""
        out = cls(id_factory=id_factory)
        items = data.get("bars") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return out
        for item in items:
            if len(out.bars) >= MAX_BARS or not isinstance(item, dict):
                continue
            bar_id = item.get("id")
            if not isinstance(bar_id, str) or not _ID.match(bar_id) or out.get(bar_id) is not None:
                continue
            mods = item.get("mods")
            mods = tuple(mods) if isinstance(mods, list) else ()
            if mods not in MOD_SETS or mods in {tuple(b.mods) for b in out.bars}:
                mods = out.free_mods()
                if mods is None:
                    continue
            slots = item.get("slots")
            slots = slots if isinstance(slots, list) else []
            bar = Bar(bar_id, out.unique_name(clean_name(item.get("name")) or "Bar"), mods,
                      _fraction(item.get("fx")), _fraction(item.get("fy")), item.get("visible") is not False,
                      [clean_binding(slots[i]) if i < len(slots) else None for i in range(SLOTS)])
            if bar.fx is None or bar.fy is None:
                bar.fx = bar.fy = None
            out.bars.append(bar)
        return out

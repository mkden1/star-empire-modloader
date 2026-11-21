"""keybinds.py - the key binding model of the quick_bars mod: which key (with which Ctrl / Shift / Alt) does what.

Pure Python: pygame is imported only for its key codes, nothing here opens a window or reads the game.

An ACTION is one thing a key can do. There are five kinds:

  * ``hotkey``  - one of the game's one-press hotkeys (dock, cycle weapon, galaxy map ...). The game reads them as
                  ``event.key == pygame.K_r``, so a rebound hotkey is carried out by handing the game that key (the
                  action's DEFAULT key) in place of the one that was pressed: nothing of the game's own code changes.
  * ``slot``    - slot N of the game's quick action bar (the tab it shows).
  * ``tab``     - the game's quick action bar's next / previous tab.
  * ``loadout`` - load the next / previous saved loadout (a whole setup of bars, see loadouts.py), by name order.
  * ``barslot`` - slot N of one of the EXTRA quick action bars the mod adds. Their actions exist only while the bar does, so
                  a ``Table`` is built for the bars that exist (``Table(bars=...)``).

A COMBO is a key plus the modifiers that must be held with it: ``Combo(K_r, {"ctrl"})`` is Ctrl+R.

MATCHING: a pressed key finds the binding whose modifiers are exactly the ones held; failing that, a binding with no
modifiers (the game's hotkeys have always ignored Shift and Ctrl, so a plain binding keeps doing that: Shift+Z still runs
Z backwards). The most specific binding wins, so Shift+1 can be one action while a plain 1 stays another. A key that some
action uses by default but that no binding matches now (you moved the action elsewhere, or unbound it) is SWALLOWED, so
the old key stops doing what it used to. Every other key is left to the game.

Only what you changed is saved (``overrides``): the file holds a combo, or null for "unbound", per changed action, so the
defaults can move with a game update for everyone who never touched them.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

import pygame

logger = logging.getLogger(__name__)

MODS = ("ctrl", "shift", "alt")            # the modifiers a binding can ask for, in display order
_MOD_BITS = (("ctrl", pygame.KMOD_CTRL), ("shift", pygame.KMOD_SHIFT), ("alt", pygame.KMOD_ALT))

# Keys that cannot be bound: the game uses them for flight, chat, menus, text editing and zoom, and a binding there would
# either never fire or break something. Modifier keys alone are not bindable either (see ``MODIFIER_KEYS``).
RESERVED_KEYS = frozenset({
    pygame.K_ESCAPE, pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB, pygame.K_SPACE, pygame.K_BACKSPACE,
    pygame.K_DELETE, pygame.K_w, pygame.K_a, pygame.K_s, pygame.K_d, pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT,
    pygame.K_RIGHT, pygame.K_SLASH, pygame.K_MINUS, pygame.K_KP_MINUS, pygame.K_EQUALS, pygame.K_PLUS,
    pygame.K_KP_PLUS, pygame.K_F8,
})
MODIFIER_KEYS = frozenset({
    pygame.K_LSHIFT, pygame.K_RSHIFT, pygame.K_LCTRL, pygame.K_RCTRL, pygame.K_LALT, pygame.K_RALT, pygame.K_LMETA,
    pygame.K_RMETA, pygame.K_LSUPER, pygame.K_RSUPER, pygame.K_MODE, pygame.K_CAPSLOCK, pygame.K_NUMLOCK,
    pygame.K_SCROLLOCK,
})


# ── combos ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Combo:
    key: int
    mods: frozenset = frozenset()

    def __post_init__(self):
        object.__setattr__(self, "mods", frozenset(self.mods))


def mods_from_event(mod_bits: int) -> frozenset:
    """The Ctrl / Shift / Alt held, from a pygame ``event.mod`` (the left and right keys count alike; Caps Lock, Num Lock and
    the Windows key are ignored)."""
    return frozenset(name for name, bit in _MOD_BITS if mod_bits & bit)


def combo_from_event(key: int, mod_bits: int) -> "Combo | None":
    """The combo a key press stands for, or None for a modifier key on its own."""
    if key in MODIFIER_KEYS or not key:
        return None
    return Combo(key, mods_from_event(mod_bits))


def key_name(key: int) -> str:
    return pygame.key.name(key)


_DISPLAY = {"return": "Enter", "escape": "Esc", "left": "Left", "right": "Right", "up": "Up", "down": "Down",
            "space": "Space", "backspace": "Backspace"}


def _key_title(key: int) -> str:
    name = key_name(key)
    if name in _DISPLAY:
        return _DISPLAY[name]
    if name.startswith("[") and name.endswith("]"):
        return "Keypad " + name[1:-1]
    return name.upper() if len(name) == 1 else name.title()


def format_combo(combo: "Combo | None") -> str:
    """``Ctrl+Shift+F5``; ``-`` for no binding."""
    if combo is None:
        return "-"
    return "+".join([m.title() for m in MODS if m in combo.mods] + [_key_title(combo.key)])


_SHORT_KEYS = {"left": "Lf", "right": "Rt", "up": "Up", "down": "Dn", "space": "Sp", "return": "En", "tab": "Tb",
               "backspace": "Bs", "delete": "Del", "insert": "Ins", "home": "Hm", "end": "End", "page up": "PU",
               "page down": "PD", "escape": "Esc"}


def short_label(combo: "Combo | None") -> str:
    """What fits on the small key tab of a quick action slot: ``Q``, ``F5``, and for a combo a lower-case prefix, ``c1``
    (Ctrl+1), ``s1`` (Shift+1), ``a1`` (Alt+1), ``cs1`` (Ctrl+Shift+1). Empty for no binding."""
    if combo is None:
        return ""
    name = key_name(combo.key)
    if name in _SHORT_KEYS:
        base = _SHORT_KEYS[name]
    elif name.startswith("[") and name.endswith("]"):
        base = name[1:-1]
    else:
        base = name.upper() if len(name) <= 3 else name[:3].title()
    return "".join(m[0] for m in MODS if m in combo.mods) + base


def combo_to_json(combo: "Combo | None"):
    if combo is None:
        return None
    return {"key": key_name(combo.key), "mods": [m for m in MODS if m in combo.mods]}


def combo_from_json(data) -> "Combo | None":
    """The combo a saved entry stands for; None when it is not a usable one (not a dict, an unknown key, a bad modifier, a
    reserved or modifier key)."""
    if not isinstance(data, dict) or not isinstance(data.get("key"), str):
        return None
    mods = data.get("mods", [])
    if not isinstance(mods, list) or not all(m in MODS for m in mods):
        return None
    try:
        key = pygame.key.key_code(data["key"])
    except (ValueError, KeyError):
        return None
    if key in RESERVED_KEYS or key in MODIFIER_KEYS or key_name(key) != data["key"]:
        return None
    return Combo(key, frozenset(mods))


# ── the actions ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Action:
    id: str
    label: str
    group: str
    kind: str                           # "hotkey" | "slot" | "tab" | "loadout" | "barslot"
    default: "Combo | None"
    arg: int = 0                        # a slot's index (0-9) or a tab's step (+1 / -1)
    bar: str = ""                       # for a "barslot": the id of the extra quick action bar it belongs to


GROUP_BAR = "Quick action bar"
GROUP_HOTKEYS = "Game hotkeys"
GROUP_LOADOUTS = "Loadouts"

# The game's one-press hotkeys: (id, label, default key). They are the ``elif event.key == pygame.K_x`` branches of the
# event loop (a test checks this list against the game's own code, so a game update that adds one says so).
HOTKEYS = (
    ("dock", "Dock / undock", pygame.K_r),
    ("cycle_weapon", "Cycle weapon", pygame.K_e),
    ("tractor", "Tractor beam on / off", pygame.K_g),
    ("galaxy_map", "Galaxy map", pygame.K_m),
    ("missions", "Missions", pygame.K_j),
    ("stats", "Stats (skills)", pygame.K_l),
    ("scoop", "Scoop cargo boxes", pygame.K_c),
    ("warp", "Enter / use warp gate", pygame.K_f),
    ("interact", "Interact menu", pygame.K_x),
    ("turrets", "All turrets on / off", pygame.K_o),
    ("cloak", "Cloak on / off", pygame.K_k),
    ("next_target", "Cycle target (same kind)", pygame.K_q),
    ("next_ship", "Cycle ship target", pygame.K_z),
    ("turret_overlay", "Turret aim overlay", pygame.K_p),
    ("focus_target", "Fighters: focus target", pygame.K_t),
    ("recall", "Fighters: recall", pygame.K_y),
    ("inventory", "Inventory panel", pygame.K_i),
    ("autopilot", "Autopilot to target", pygame.K_v),
)

_SLOT_KEYS = (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4, pygame.K_5, pygame.K_6, pygame.K_7, pygame.K_8,
              pygame.K_9, pygame.K_0)


def slot_id(index: int) -> str:
    return f"slot.main.{index + 1}"


def _build_catalog() -> "tuple[Action, ...]":
    actions = [Action(slot_id(i), f"Slot {i + 1 if i < 9 else 0}", GROUP_BAR, "slot", Combo(_SLOT_KEYS[i]), i)
               for i in range(10)]
    actions.append(Action("tab.next", "Next tab", GROUP_BAR, "tab", None, 1))
    actions.append(Action("tab.prev", "Previous tab", GROUP_BAR, "tab", None, -1))
    actions += [Action(f"hotkey.{name}", label, GROUP_HOTKEYS, "hotkey", Combo(key)) for name, label, key in HOTKEYS]
    actions.append(Action("loadout.next", "Next loadout", GROUP_LOADOUTS, "loadout", None, 1))
    actions.append(Action("loadout.prev", "Previous loadout", GROUP_LOADOUTS, "loadout", None, -1))
    return tuple(actions)


CATALOG = _build_catalog()                 # the actions every client has; extra bars add theirs (``catalog``)
BY_ID = {a.id: a for a in CATALOG}
# Every key an action uses by default: a press of one that no binding matches is swallowed (the action moved away).
DEFAULT_KEYS = frozenset(a.default.key for a in CATALOG if a.default is not None)


def bar_slot_id(bar_id: str, index: int) -> str:
    return f"slot.{bar_id}.{index + 1}"


def bar_actions(bars) -> "tuple[Action, ...]":
    """The actions of the extra quick action bars: ten slots each, whose default keys are the number keys with the bar's own
    modifiers (``bar.mods``, say Shift: Shift+1 ... Shift+0). *bars* are anything with ``id``, ``name`` and ``mods``."""
    actions = []
    for bar in bars:
        mods = frozenset(bar.mods)
        actions += [Action(bar_slot_id(bar.id, i), f"Slot {i + 1 if i < 9 else 0}", bar.name, "barslot",
                           Combo(_SLOT_KEYS[i], mods), i, bar.id) for i in range(10)]
    return tuple(actions)


def catalog(bars=()) -> "tuple[Action, ...]":
    """Every action there is: the base ones, then each extra bar's ten slots."""
    return CATALOG + bar_actions(bars)


# ── the table ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Resolution:
    action: "Action | None"      # what the key press does, if a binding matches
    swallow: bool = False        # no binding matches, but the key is one an action used by default: do nothing


class Table:
    """Every action's combo now: the defaults with the player's changes laid over them.

    The actions are the base ones plus the ten slots of each extra quick action bar passed as *bars* (anything with ``id``,
    ``name`` and ``mods``). A saved change for an action this table does not have (an extra bar of another account, a bar that
    is not loaded yet) is kept as it was saved and written back with the rest, so changing one binding never loses another
    account's."""

    def __init__(self, overrides: "dict | None" = None, bars=(), unknown: "dict | None" = None):
        self.catalog = catalog(bars)
        self.by_id = {a.id: a for a in self.catalog}
        self.default_keys = frozenset(a.default.key for a in self.catalog if a.default is not None)
        self.has_bars = any(a.kind == "barslot" for a in self.catalog)
        self.unknown: dict = dict(unknown or {})
        self.overrides: dict = {}
        self.bindings: dict = {a.id: a.default for a in self.catalog}
        self._index: dict = {}
        for action_id, combo in (overrides or {}).items():
            if action_id in self.by_id and (combo is None or isinstance(combo, Combo)):
                self.overrides[action_id] = combo
                self.bindings[action_id] = combo
        self._reindex()

    def _reindex(self) -> None:
        index: dict = {}
        for action_id, combo in self.bindings.items():
            if combo is not None:
                index.setdefault(combo.key, {})[combo.mods] = self.by_id[action_id]
        self._index = index
        self.is_default = all(self.bindings[a.id] == a.default for a in self.catalog)

    def combo(self, action_id: str) -> "Combo | None":
        return self.bindings.get(action_id)

    def is_changed(self, action_id: str) -> bool:
        return self.bindings.get(action_id) != self.by_id[action_id].default

    def holder(self, combo: Combo, ignoring: str = "") -> "Action | None":
        """The action that has exactly *combo* now (other than *ignoring*)."""
        action = self._index.get(combo.key, {}).get(combo.mods)
        return action if action is not None and action.id != ignoring else None

    def resolve(self, key: int, mods: frozenset) -> Resolution:
        """What a key press with *mods* held does: see the module docstring."""
        by_mods = self._index.get(key)
        if by_mods:
            action = by_mods.get(frozenset(mods)) or by_mods.get(frozenset())
            if action is not None:
                return Resolution(action)
        return Resolution(None, swallow=key in self.default_keys)

    def set(self, action_id: str, combo: "Combo | None") -> "list[Action]":
        """Bind *combo* (None: unbind) to an action; whatever else had exactly that combo loses it (unbound). Returns the
        actions that lost it. Raises ValueError for an unknown action or a combo that cannot be bound."""
        if action_id not in self.by_id:
            raise ValueError(f"unknown action {action_id!r}")
        if combo is not None and (combo.key in RESERVED_KEYS or combo.key in MODIFIER_KEYS):
            raise ValueError("that key is used by the game")
        displaced = []
        if combo is not None:
            other = self.holder(combo, ignoring=action_id)
            if other is not None:
                self._store(other.id, None)
                displaced.append(other)
        self._store(action_id, combo)
        self._reindex()
        return displaced

    def reset(self, action_id: str) -> None:
        """Back to the default combo. If something else holds it now, that one is unbound (a combo has one action)."""
        default = self.by_id[action_id].default
        if default is not None:
            other = self.holder(default, ignoring=action_id)
            if other is not None:
                self._store(other.id, None)
        self._store(action_id, default)
        self._reindex()

    def reset_all(self) -> None:
        """Every action this table has back to its default (what is saved for actions it does not have stays)."""
        self.overrides = {}
        self.bindings = {a.id: a.default for a in self.catalog}
        self._reindex()

    def _store(self, action_id: str, combo: "Combo | None") -> None:
        self.bindings[action_id] = combo
        if combo == self.by_id[action_id].default:
            self.overrides.pop(action_id, None)
        else:
            self.overrides[action_id] = combo

    def to_json(self) -> dict:
        out = dict(self.unknown)
        out.update({action_id: combo_to_json(combo) for action_id, combo in self.overrides.items()})
        return out

    def with_bars(self, bars) -> "Table":
        """The same changes, for a different set of extra bars."""
        return Table.from_json(self.to_json(), bars)

    @classmethod
    def from_json(cls, data, bars=()) -> "Table":
        """The table a saved ``keys`` section stands for. An entry that is not usable is skipped; two actions saved with the
        same combo (a hand edit) keep the first in catalog order, the other is unbound, so one combo never has two actions.
        An entry for an action there is no such action for (right now) is kept as it is, for ``to_json``."""
        actions = catalog(bars)
        known = {a.id for a in actions}
        overrides: dict = {}
        unknown: dict = {}
        if isinstance(data, dict):
            for action_id, entry in data.items():
                if not isinstance(action_id, str) or action_id in known:
                    continue
                if entry is None or isinstance(entry, dict):
                    unknown[action_id] = entry
            for action in actions:
                if action.id not in data:
                    continue
                entry = data[action.id]
                combo = None if entry is None else combo_from_json(entry)
                if entry is not None and combo is None:
                    continue
                if combo != action.default:
                    overrides[action.id] = combo
        table = cls(overrides, bars, unknown)
        seen: dict = {}
        for action in table.catalog:
            combo = table.bindings[action.id]
            if combo is None:
                continue
            if combo in seen:
                table._store(action.id, None)
            else:
                seen[combo] = action.id
        table._reindex()
        return table


# ── the file ─────────────────────────────────────────────────────────────────

def default_path() -> str:
    import modloader
    return os.path.join(str(modloader.REPO_DIR), "quick_bars.json")


def read_file(path: str) -> dict:
    """The whole file as a dict ({} when it is missing or unreadable)."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.warning("quick_bars: could not read %s", path)
        return {}
    return data if isinstance(data, dict) else {}


def _write_all(path: str, data: dict) -> None:
    """Temp file and atomic replace, so a reader (another client) never sees half a file."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1, sort_keys=True)
    os.replace(tmp, path)


def delete_entry(path: str, section: str, key: str) -> None:
    """Remove one entry of a section that holds several (a loadout of ``loadouts``, an account's undo of ``previous``); the rest stay."""
    data = read_file(path)
    inner = data.get(section)
    if not isinstance(inner, dict) or key not in inner:
        return
    del inner[key]
    data["version"] = 1
    _write_all(path, data)


def write_section(path: str, section: str, value) -> None:
    """Replace one section of the file and leave the others as they are. Read-modify-write, so two clients running from one
    folder do not wipe each other's other sections."""
    data = read_file(path)
    data["version"] = 1
    data[section] = value
    _write_all(path, data)


def write_entry(path: str, section: str, key: str, value) -> None:
    """Replace one entry of a section that holds several (the bars of one account in ``bars``) and leave the others: two
    clients on two accounts each keep their own."""
    data = read_file(path)
    data["version"] = 1
    inner = data.get(section) if isinstance(data.get(section), dict) else {}
    inner[key] = value
    data[section] = inner
    _write_all(path, data)

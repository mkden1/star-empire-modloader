"""ship_layouts.py - save a ship's whole fit under a name, and compare any ship's current fit against it.

A LAYOUT is what you would need to rebuild a setup, plus how it performs:

  * ship        - which hull it was saved on (type, name, classification);
  * weapons     - the main weapon slots, in slot order;
  * equipment   - every fitted module by slot (power, shield, engine, tractor, ... and the multi-fit families:
                  solar panels, fabricators, drones, which are counted by type);
  * plugins     - the installed ship plugins (counted by type: their slot order means nothing);
  * turrets     - each turret hardpoint's weapon and its controllers;
  * cargo       - the items (not bulk resources) sitting in the hold, counted by type;
  * stats       - the numbers that matter, as the server reported them at save time: DPS (best main weapon,
                  turrets, both), shield and energy banks / regen, the six resistances, speed, hull space,
                  visibility, mass, evasion. Hull space comes from the inventory's cargo summary (what the HULL
                  header shows), not from Ship Specs: while DOCKED the specs report a lighter, roomier ship (on a
                  Forest Warden: 1,453 units of gear and 2,664,514 effective mass docked, 3,637 and 3,051,514 in
                  space, with the inventory summary saying 3,637), so the layout also remembers which state its
                  specs were read in (``specs_state``) and a docked reading is never judged against an in-space
                  one for acceleration, turn rate and mass.

Everything is plain JSON so a layout file can be read, copied and backed up by hand. ``capture`` builds one
from the live data the client already holds (the ship inventory rows, SHIP_PLUGIN_DATA, TURRET_STATE and
SHIP_SPECS_DATA) - no new server traffic beyond asking for fresh Ship Specs. ``compare`` lines a saved layout up
against another (normally the ship you are flying right now) and says, per item, whether it is the same, was
swapped, is missing, or is extra, and per stat what changed and whether that is better or worse.

Layouts live one per file in ``scan_data/ship_layouts/`` next to the other shared client data, so every client
and account sees the same list. All disk access goes through ``scan_writer``'s background thread: the render
and message threads only ever read the in-memory list (``LayoutStore.layouts``).

This module has no pygame and no game imports beyond two small pure helpers.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field

import scan_paths
from damage_types import DAMAGE_TYPES, DAMAGE_TYPE_LABELS, RESISTANCE_FIELD
from inventory_sections import SHIP_GEAR_CLASSES, inventory_stack_quantity

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MAX_NAME_LEN = 40

# Fitted slots with exactly one row each, in the order the inventory panel lists them (power, shield, engine,
# tractor first, the rest A-Z by label), and the labels it uses.
SLOT_LABELS = {
    "engine": "Engine", "shield": "Shield", "energy": "Power", "scoop": "Scoop", "expander": "Hull Expander",
    "hangar": "Hangar", "launch_tube": "Launch Tube", "tractor": "Tractor Beam", "controller": "Fighter Controller",
    "slipstream": "Slipstream Drive", "warper": "Vector Jump Drive", "sensor": "Sensor", "cloak": "Cloak",
    "searchlight": "Spotlight", "shield_charger": "Shield Charger", "energy_capacitor": "Energy Capacitor",
    "shield_capacitor": "Shield Capacitor",
}
_PRIORITY = ("energy", "shield", "engine", "tractor")
SLOT_ORDER = _PRIORITY + tuple(sorted((k for k in SLOT_LABELS if k not in _PRIORITY),
                                      key=lambda k: SLOT_LABELS[k].lower()))
# Several rows of the same family can be fitted at once ("solar_panel_0", "solar_panel_1", ...): counted by type.
FAMILIES = (("solar_panel", "Solar Panel"), ("fabricator", "Fabricator"), ("drone", "Drone"))
FAMILY_LABELS = dict(FAMILIES)
FAMILY_ORDER = tuple(key for key, _label in FAMILIES)

STATUS_SAME, STATUS_CHANGED, STATUS_MISSING, STATUS_EXTRA, STATUS_NOTE = (
    "same", "changed", "missing", "extra", "note")


# ── reading the live data ────────────────────────────────────────────────────

def _text(value, default="") -> str:
    return default if value is None else str(value)


def _name_of(row: dict, fallback_key: str = "item_type") -> str:
    return _text(row.get("display_name") or row.get(fallback_key) or "?")


def _slot_index(slot) -> "int | None":
    match = re.search(r"(\d+)$", _text(slot))
    return int(match.group(1)) if match else None


def extract_weapons(inventory) -> list:
    """The fitted main weapons, in slot order: ``[{"slot": 0, "item_type": ..., "display_name": ...}]``."""
    out = []
    for row in inventory or ():
        slot = _text(row.get("slot"))
        if row.get("item_class") != "weapon" or not slot.startswith("weapon_") or not row.get("item_type"):
            continue
        index = _slot_index(slot)
        if index is None:
            continue
        out.append({"slot": index, "item_type": _text(row["item_type"]), "display_name": _name_of(row)})
    out.sort(key=lambda w: w["slot"])
    return out


def _family_of(slot: str) -> "str | None":
    for family, _label in FAMILIES:
        if slot.startswith(family + "_"):
            return family
    return None


def extract_equipment(inventory) -> list:
    """The fitted modules. One entry per single-fit slot (``{"slot": "engine", "item_type", "display_name"}``);
    one per type for the multi-fit families (``{"slot": "solar_panel", ..., "count": 3}``). Built-in hull
    fixtures are not part of a layout (the hull brings them)."""
    singles: dict = {}
    families: dict = {}
    for row in inventory or ():
        if row.get("built_in") or not row.get("item_type"):
            continue
        slot = _text(row.get("slot"))
        if not slot or slot.startswith("weapon_"):
            continue
        family = _family_of(slot)
        if family is not None:
            key = (family, _text(row["item_type"]))
            entry = families.setdefault(key, {"slot": family, "item_type": key[1],
                                              "display_name": _name_of(row), "count": 0})
            entry["count"] += 1
        elif slot in SLOT_LABELS and (row.get("item_class") in SHIP_GEAR_CLASSES or row.get("item_class") == slot):
            singles[slot] = {"slot": slot, "item_type": _text(row["item_type"]), "display_name": _name_of(row)}
    out = [singles[slot] for slot in SLOT_ORDER if slot in singles]
    for family in FAMILY_ORDER:
        out.extend(sorted((e for (f, _t), e in families.items() if f == family),
                          key=lambda e: e["display_name"].lower()))
    return out


def extract_plugins(inventory, plugin_data=None) -> list:
    """The installed ship plugins, ``[{"slot": 0, "plugin_id": ..., "display_name": ...}]``. The inventory's
    ``equipped_plugin`` rows are preferred; SHIP_PLUGIN_DATA is the fallback the inventory panel itself uses."""
    rows = [r for r in (inventory or ()) if r.get("item_class") == "equipped_plugin"]
    out = []
    if rows:
        for row in rows:
            if row.get("item_type"):
                out.append({"slot": int(row.get("slot_index") or 0), "plugin_id": _text(row["item_type"]),
                            "display_name": _name_of(row)})
    elif isinstance(plugin_data, dict):
        defs = plugin_data.get("plugin_defs") or {}
        installed = plugin_data.get("installed") or {}
        for slot, plugin_id in installed.items():
            if not plugin_id:
                continue
            try:
                index = int(slot)
            except (TypeError, ValueError):
                continue
            definition = defs.get(plugin_id) if isinstance(defs, dict) else None
            out.append({"slot": index, "plugin_id": _text(plugin_id),
                        "display_name": _text((definition or {}).get("display_name") or plugin_id)})
    out.sort(key=lambda p: p["slot"])
    return out


def extract_turrets(turret_state) -> tuple:
    """``(turrets, enabled)``: every hardpoint that has a weapon, with its controllers."""
    if not isinstance(turret_state, dict):
        return [], None
    turrets = []
    for hardpoint in turret_state.get("hardpoints") or ():
        if not isinstance(hardpoint, dict):
            continue
        weapon_type = hardpoint.get("weapon_type")
        controllers = []
        for upgrade in hardpoint.get("upgrades") or ():
            if isinstance(upgrade, dict) and upgrade.get("upgrade_id"):
                definition = upgrade.get("upgrade_def") or {}
                controllers.append({"id": _text(upgrade["upgrade_id"]),
                                    "name": _text(definition.get("display_name") or upgrade["upgrade_id"])})
        if not weapon_type and not controllers:
            continue
        definition = hardpoint.get("weapon_def") or {}
        turrets.append({"index": int(hardpoint.get("index", len(turrets))),
                        "kind": _text(hardpoint.get("kind"), "standard"),
                        "upgrade_slots": int(hardpoint.get("upgrade_slots", 0) or 0),
                        "weapon_type": _text(weapon_type),
                        "weapon_name": _text(definition.get("display_name") or weapon_type),
                        "controllers": controllers})
    turrets.sort(key=lambda t: t["index"])
    enabled = turret_state.get("turrets_enabled")
    return turrets, (bool(enabled) if enabled is not None else None)


def extract_cargo(inventory) -> list:
    """The items in the hold (``cargo_item`` rows: spare gear, kits, drones, ...), counted by type. Bulk
    resources and scoop cargo come and go with every trip and are not part of a layout."""
    counts: dict = {}
    for row in inventory or ():
        if row.get("item_class") != "cargo_item" or not row.get("item_type"):
            continue
        key = (_text(row.get("item_category")), _text(row["item_type"]))
        entry = counts.setdefault(key, {"item_category": key[0], "item_type": key[1],
                                        "display_name": _name_of(row), "qty": 0})
        entry["qty"] += inventory_stack_quantity(row)
    return sorted(counts.values(), key=lambda e: (e["display_name"].lower(), e["item_type"]))


def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def extract_ship(specs) -> dict:
    ship = (specs or {}).get("ship") if isinstance(specs, dict) else None
    if not isinstance(ship, dict):
        return {}
    return {"ship_type": _text(ship.get("ship_type")), "display_name": _text(ship.get("display_name")),
            "classification": _text(ship.get("classification"))}


def extract_cargo_summary(inventory) -> dict:
    """The inventory's ``cargo_summary`` row (the HULL header's numbers), or ``{}``."""
    for row in inventory or ():
        if isinstance(row, dict) and row.get("item_class") == "cargo_summary":
            return row
    return {}


def extract_stats(specs, turret_state=None, inventory=None) -> dict:
    """The numbers worth saving, flat and JSON-safe. A number the data does not carry is simply left out."""
    stats: dict = {}

    def put(key, value, scale=1.0):
        number = _num(value)
        if number is not None:
            stats[key] = number * scale

    if isinstance(specs, dict):
        ship = specs.get("ship") if isinstance(specs.get("ship"), dict) else {}
        engine = specs.get("engine") if isinstance(specs.get("engine"), dict) else {}
        shield = specs.get("shield") if isinstance(specs.get("shield"), dict) else {}
        energy = specs.get("energy") if isinstance(specs.get("energy"), dict) else {}
        weapons = [w for w in (specs.get("weapons") or ()) if isinstance(w, dict)]
        dps = [d for d in (_num(w.get("dps")) for w in weapons) if d is not None]
        if dps:
            stats["weapon_dps"] = max(dps)
        put("shield_bank", shield.get("max_shields"))
        put("shield_regen", shield.get("recharge_rate"))
        put("energy_bank", energy.get("max_energy"))
        put("energy_regen", energy.get("energy_output"))
        flat = ship.get("resistance")                      # the fallback the Ship Specs tab uses per type
        for damage_type in DAMAGE_TYPES:
            put("resist_" + damage_type, ship.get(RESISTANCE_FIELD[damage_type], flat))
        put("max_speed", engine.get("max_speed"))
        put("accel", engine.get("accel"))
        put("turn_rate", engine.get("turn_rate"))
        # Hull space: the specs' own figures are only trusted when read in space (docked they leave out part of
        # the gear); the inventory summary below replaces them in either state.
        put("hull_capacity", ship.get("cargo_capacity"))
        if specs.get("state") != "docked":
            put("hull_equipped", ship.get("equipped_cargo_used"))
            put("hull_hold", ship.get("hold_cargo_used"))
            capacity, used = _num(ship.get("cargo_capacity")), _num(ship.get("cargo_used"))
            if capacity is not None and used is not None:
                stats["hull_free"] = capacity - used
        put("visibility", ship.get("visibility"))
        put("mass", ship.get("mass"))
        put("effective_mass", ship.get("effective_mass"))
        put("evasion", ship.get("evasion"))
        put("flat_mitigation", ship.get("flat_damage_mitigation"))
    summary = extract_cargo_summary(inventory)
    if summary:
        capacity, used = _num(summary.get("cargo_capacity")), _num(summary.get("cargo_used"))
        equipped, hold = _num(summary.get("equipped_cargo_used")), _num(summary.get("hold_cargo_used"))
        if capacity is not None:
            stats["hull_capacity"] = capacity
        if equipped is not None:
            stats["hull_equipped"] = equipped
        if hold is None and used is not None and equipped is not None:
            hold = used - equipped
        if hold is not None:
            stats["hull_hold"] = hold
        if capacity is not None and used is not None:
            stats["hull_free"] = capacity - used
    if isinstance(turret_state, dict):
        turret_dps = sum(d for d in (_num(h.get("effective_dps")) for h in (turret_state.get("hardpoints") or ())
                                     if isinstance(h, dict) and h.get("weapon_type")) if d is not None)
        stats["turret_dps"] = turret_dps
        # A total needs the turret half: without a TURRET_STATE it would be the main weapons alone, which reads
        # as a (false) loss next to a layout that has turrets.
        stats["total_dps"] = stats.get("weapon_dps", 0.0) + turret_dps
    return stats


def capture(inventory, plugin_data, turret_state, specs, name="", *, now=None, username="",
            layout_id=None) -> dict:
    """A layout of the ship as the client sees it right now (see the module docstring for the parts)."""
    turrets, turrets_enabled = extract_turrets(turret_state)
    saved_at = time.time() if now is None else float(now)
    layout = {
        "version": SCHEMA_VERSION,
        "id": layout_id or make_id(name, saved_at),
        "name": clean_name(name),
        "saved_at": saved_at,
        "saved_by": _text(username),
        "ship": extract_ship(specs),
        "weapons": extract_weapons(inventory),
        "equipment": extract_equipment(inventory),
        "plugins": extract_plugins(inventory, plugin_data),
        "turrets": turrets,
        "turrets_enabled": turrets_enabled,
        "cargo": extract_cargo(inventory),
        "specs_state": _text(specs.get("state")) if isinstance(specs, dict) else "",
        "stats": extract_stats(specs, turret_state, inventory),
    }
    return layout


def clean_name(name) -> str:
    return " ".join(_text(name).split())[:MAX_NAME_LEN]


def make_id(name, when) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", clean_name(name).lower()).strip("-")[:30] or "layout"
    return f"{int(when * 1000)}-{slug}"


# ── comparing ────────────────────────────────────────────────────────────────

@dataclass
class Row:
    status: str
    label: str
    saved: str = ""
    current: str = ""


@dataclass
class Section:
    key: str
    title: str
    rows: list = field(default_factory=list)

    @property
    def differences(self) -> int:
        return sum(1 for row in self.rows if row.status in (STATUS_CHANGED, STATUS_MISSING, STATUS_EXTRA))

    @property
    def matching(self) -> int:
        return sum(1 for row in self.rows if row.status == STATUS_SAME)

    def visible_rows(self, show_matching: bool) -> list:
        return [row for row in self.rows if show_matching or row.status != STATUS_SAME]


# key, label, number format, which direction is better (None: just a number), group
STAT_DEFS = (
    ("total_dps", "Total DPS", "int", "up", "Damage"),
    ("weapon_dps", "Best weapon DPS", "int", "up", "Damage"),
    ("turret_dps", "Turret DPS", "int", "up", "Damage"),
    ("shield_bank", "Shield bank", "int", "up", "Shield & energy"),
    ("shield_regen", "Shield regen /s", "float1", "up", "Shield & energy"),
    ("energy_bank", "Energy bank", "int", "up", "Shield & energy"),
    ("energy_regen", "Energy regen /s", "float1", "up", "Shield & energy"),
) + tuple(("resist_" + dt, f"{DAMAGE_TYPE_LABELS[dt]} resist", "pct", "up", "Resistance")
          for dt in DAMAGE_TYPES) + (
    ("evasion", "Evasion", "pct", "up", "Resistance"),
    ("flat_mitigation", "Flat damage mitigation", "float1", "up", "Resistance"),
    ("max_speed", "Max speed", "float1", "up", "Speed"),
    ("accel", "Acceleration", "int", "up", "Speed"),
    ("turn_rate", "Turn rate", "float1", "up", "Speed"),
    ("mass", "Mass", "int", None, "Speed"),
    ("effective_mass", "Effective mass", "int", None, "Speed"),
    ("hull_capacity", "Hull space", "int", "up", "Hull & visibility"),
    ("hull_equipped", "Hull used by gear", "int", None, "Hull & visibility"),
    ("hull_hold", "Hull used in hold", "int", None, "Hull & visibility"),
    ("hull_free", "Hull space free", "int", "up", "Hull & visibility"),
    ("visibility", "Visibility", "int", "down", "Hull & visibility"),
)
STAT_KEYS = tuple(d[0] for d in STAT_DEFS)
# Readings that follow the ship's mass, which the server reports differently docked and in space.
MASS_DEPENDENT = ("accel", "turn_rate", "effective_mass")
STATE_TEXT = {"docked": "docked", "in_space": "in space"}


def format_stat(kind: str, value) -> str:
    number = _num(value)
    if number is None:
        return "-"
    if kind == "pct":
        return f"{number * 100:.1f}%"
    if kind == "float1":
        return f"{number:,.1f}"
    return f"{number:,.0f}"


def format_delta(kind: str, delta: float) -> str:
    if kind == "pct":
        return f"{delta * 100:+.1f}%"
    if kind == "float1":
        return f"{delta:+,.1f}"
    return f"{delta:+,.0f}"


@dataclass
class StatRow:
    key: str
    label: str
    kind: str
    better: "str | None"
    group: str
    saved: "float | None"
    current: "float | None"
    comparable: bool = True       # False: the two readings were taken in different states (see MASS_DEPENDENT)

    @property
    def delta(self) -> "float | None":
        if self.saved is None or self.current is None:
            return None
        return self.current - self.saved

    @property
    def verdict(self) -> str:
        """``same``, ``better``, ``worse``, ``changed`` (no preferred direction) or ``unknown``."""
        delta = self.delta
        if delta is None:
            return "unknown"
        scale = max(abs(self.saved), abs(self.current), 1.0 if self.kind != "pct" else 0.01)
        if abs(delta) <= 0.0005 * scale or abs(delta) < (0.0005 if self.kind == "pct" else 0.05):
            return "same"
        if self.better is None or not self.comparable:
            return "changed"
        return "better" if (delta > 0) == (self.better == "up") else "worse"

    @property
    def saved_text(self) -> str:
        return format_stat(self.kind, self.saved)

    @property
    def current_text(self) -> str:
        return format_stat(self.kind, self.current)

    @property
    def delta_text(self) -> str:
        delta = self.delta
        if delta is None or self.verdict == "same":
            return ""
        return format_delta(self.kind, delta)


@dataclass
class LayoutDiff:
    saved_ship: dict
    current_ship: dict
    same_ship: "bool | None"
    sections: list
    stats: list
    saved_state: str = ""
    current_state: str = ""

    @property
    def mixed_states(self) -> bool:
        return bool(self.saved_state and self.current_state and self.saved_state != self.current_state)

    @property
    def state_note(self) -> list:
        """Lines explaining why some speed readings are not judged (empty when they are comparable)."""
        if not any(not row.comparable and row.verdict != "same" for row in self.stats):    # only when mixed
            return []
        saved = STATE_TEXT.get(self.saved_state, self.saved_state)
        current = STATE_TEXT.get(self.current_state, self.current_state)
        # short lines: the pane is narrow in a small window and a note line is not wrapped
        return [f"Read {saved} when saved, {current} now.",
                "Docked, the server reports a lighter ship,",
                "so acceleration, turn rate and mass are not",
                "compared. Save in space for flight figures."]

    @property
    def differences(self) -> int:
        return sum(section.differences for section in self.sections)

    @property
    def stat_changes(self) -> int:
        """Stats that really differ (a reading that is not comparable is explained by ``state_note`` instead)."""
        return sum(1 for row in self.stats if row.comparable and row.verdict in ("better", "worse", "changed"))


def _by_key(items, key_fn) -> dict:
    return {key_fn(item): item for item in items or ()}


def _count(item) -> int:
    return int(item.get("count", item.get("qty", 1)) or 1)


def _times(name: str, count: int) -> str:
    return f"{name} x{count}" if count > 1 else name


def _counted_rows(saved_items, current_items, key_fn, label_fn, text_fn) -> list:
    saved, current = _by_key(saved_items, key_fn), _by_key(current_items, key_fn)
    order = list(saved) + [k for k in current if k not in saved]
    rows = []
    for key in order:
        s, c = saved.get(key), current.get(key)
        label = label_fn(s or c)
        if s is not None and c is not None:
            same = _count(s) == _count(c)
            rows.append(Row(STATUS_SAME if same else STATUS_CHANGED, label, text_fn(s), text_fn(c)))
        elif s is not None:
            rows.append(Row(STATUS_MISSING, label, text_fn(s), "(none)"))
        else:
            rows.append(Row(STATUS_EXTRA, label, "(none)", text_fn(c)))
    return rows


def _weapon_rows(saved, current) -> list:
    s, c = _by_key(saved, lambda w: w["slot"]), _by_key(current, lambda w: w["slot"])
    rows = []
    for slot in sorted(set(s) | set(c)):
        a, b = s.get(slot), c.get(slot)
        label = f"Weapon {slot + 1}"
        if a and b:
            rows.append(Row(STATUS_SAME if a["item_type"] == b["item_type"] else STATUS_CHANGED, label,
                            a["display_name"], b["display_name"]))
        elif a:
            rows.append(Row(STATUS_MISSING, label, a["display_name"], "(empty)"))
        else:
            rows.append(Row(STATUS_EXTRA, label, "(empty)", b["display_name"]))
    same_set = sorted(w["item_type"] for w in saved or ()) == sorted(w["item_type"] for w in current or ())
    if same_set and any(row.status == STATUS_CHANGED for row in rows):
        rows.append(Row(STATUS_NOTE, "Same weapons, different slots", "", ""))
    return rows


def _equipment_rows(saved, current) -> list:
    s = _by_key([e for e in saved or () if e["slot"] in SLOT_LABELS], lambda e: e["slot"])
    c = _by_key([e for e in current or () if e["slot"] in SLOT_LABELS], lambda e: e["slot"])
    rows = []
    for slot in SLOT_ORDER:
        a, b = s.get(slot), c.get(slot)
        label = SLOT_LABELS[slot]
        if a and b:
            rows.append(Row(STATUS_SAME if a["item_type"] == b["item_type"] else STATUS_CHANGED, label,
                            a["display_name"], b["display_name"]))
        elif a:
            rows.append(Row(STATUS_MISSING, label, a["display_name"], "(nothing fitted)"))
        elif b:
            rows.append(Row(STATUS_EXTRA, label, "(nothing fitted)", b["display_name"]))
    for family in FAMILY_ORDER:
        rows.extend(_counted_rows(
            [e for e in saved or () if e["slot"] == family], [e for e in current or () if e["slot"] == family],
            lambda e: e["item_type"], lambda e, f=family: FAMILY_LABELS[f],
            lambda e: _times(e["display_name"], _count(e))))
    return rows


def _plugin_rows(saved, current) -> list:
    def grouped(items):
        out: dict = {}
        for p in items or ():
            entry = out.setdefault(p["plugin_id"], {"plugin_id": p["plugin_id"], "display_name": p["display_name"],
                                                    "count": 0})
            entry["count"] += 1
        return list(out.values())

    return _counted_rows(grouped(saved), grouped(current), lambda e: e["plugin_id"], lambda e: "Plugin",
                         lambda e: _times(e["display_name"], _count(e)))


def short_controller_name(name: str) -> str:
    """"Broadside Turret Controller" -> "Broadside" (the row has little room and every one ends the same)."""
    text = _text(name)
    for suffix in (" Turret Controller", " Controller"):
        if text.lower().endswith(suffix.lower()) and len(text) > len(suffix):
            return text[:-len(suffix)]
    return text


def _turret_text(turret) -> str:
    counts: dict = {}
    for controller in turret.get("controllers") or ():
        short = short_controller_name(controller["name"])
        counts[short] = counts.get(short, 0) + 1
    controllers = ", ".join(_times(name, n) for name, n in counts.items())
    weapon = turret.get("weapon_name") or "(no weapon)"
    return f"{weapon}  [{controllers}]" if controllers else weapon


def _turret_signature(turret) -> tuple:
    return (turret.get("weapon_type") or "", tuple(sorted(c["id"] for c in turret.get("controllers") or ())))


def _turret_rows(saved, current) -> list:
    s, c = _by_key(saved, lambda t: t["index"]), _by_key(current, lambda t: t["index"])
    rows = []
    for index in sorted(set(s) | set(c)):
        a, b = s.get(index), c.get(index)
        label = f"Turret {index + 1}"
        if a and b:
            rows.append(Row(STATUS_SAME if _turret_signature(a) == _turret_signature(b) else STATUS_CHANGED,
                            label, _turret_text(a), _turret_text(b)))
        elif a:
            rows.append(Row(STATUS_MISSING, label, _turret_text(a), "(empty)"))
        else:
            rows.append(Row(STATUS_EXTRA, label, "(empty)", _turret_text(b)))
    return rows


def _cargo_rows(saved, current) -> list:
    return _counted_rows(saved, current, lambda e: (e["item_category"], e["item_type"]), lambda e: "Item",
                         lambda e: _times(e["display_name"], _count(e)))


def compare(saved: dict, current: dict) -> LayoutDiff:
    """Line *saved* up against *current* (both layout dicts, see ``capture``)."""
    saved_ship, current_ship = saved.get("ship") or {}, current.get("ship") or {}
    same_ship = None
    if saved_ship.get("ship_type") and current_ship.get("ship_type"):
        same_ship = saved_ship["ship_type"] == current_ship["ship_type"]
    sections = [
        Section("weapons", "Weapons", _weapon_rows(saved.get("weapons"), current.get("weapons"))),
        Section("equipment", "Equipment", _equipment_rows(saved.get("equipment"), current.get("equipment"))),
        Section("plugins", "Plugins", _plugin_rows(saved.get("plugins"), current.get("plugins"))),
        Section("turrets", "Turrets", _turret_rows(saved.get("turrets"), current.get("turrets"))),
        Section("cargo", "Cargo hold", _cargo_rows(saved.get("cargo"), current.get("cargo"))),
    ]
    saved_stats, current_stats = saved.get("stats") or {}, current.get("stats") or {}
    saved_state, current_state = _text(saved.get("specs_state")), _text(current.get("specs_state"))
    mixed = bool(saved_state and current_state and saved_state != current_state)
    stats = [StatRow(key, label, kind, better, group, _num(saved_stats.get(key)), _num(current_stats.get(key)),
                     comparable=not (mixed and key in MASS_DEPENDENT))
             for key, label, kind, better, group in STAT_DEFS
             if key in saved_stats or key in current_stats]
    return LayoutDiff(saved_ship, current_ship, same_ship, sections, stats, saved_state, current_state)


def summary_line(layout: dict) -> str:
    """One line for the layout list: ship and headline numbers."""
    ship = (layout.get("ship") or {}).get("display_name") or "Unknown ship"
    stats = layout.get("stats") or {}
    parts = [ship]
    if "total_dps" in stats:
        parts.append(f"{format_stat('int', stats['total_dps'])} dps")
    if "visibility" in stats:
        parts.append(f"vis {format_stat('int', stats['visibility'])}")
    return "  -  ".join(parts)


# ── storage ──────────────────────────────────────────────────────────────────

def default_directory() -> str:
    """``scan_data/ship_layouts`` (a pure path computation, safe on any thread)."""
    return scan_paths.path("ship_layouts")


def valid_layout(data) -> bool:
    return (isinstance(data, dict) and isinstance(data.get("id"), str) and data["id"]
            and isinstance(data.get("name"), str) and isinstance(data.get("saved_at"), (int, float)))


def migrate(layout: dict) -> dict:
    """A layout saved before ``specs_state`` existed: infer the state its specs were read in. The server sends
    the gear / hold split of the hull only while docked, so a layout that has it was read docked and one that
    has a hull capacity without it was read in space. The file itself is left as it is."""
    if "specs_state" in layout:
        return layout
    stats = layout.get("stats") or {}
    if "hull_equipped" in stats or "hull_hold" in stats:
        state = "docked"
    elif "hull_capacity" in stats:
        state = "in_space"
    else:
        state = ""
    return {**layout, "specs_state": state}


def read_all(directory: str) -> list:
    """Every readable layout in *directory*, newest first. Unreadable or foreign files are skipped."""
    layouts = []
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    for filename in names:
        if not filename.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, filename), encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            logger.warning("ship_layouts: could not read %s", filename)
            continue
        if valid_layout(data):
            layouts.append(migrate(data))
    layouts.sort(key=lambda d: d["saved_at"], reverse=True)
    return layouts


def _file_for(directory: str, layout_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", layout_id)
    return os.path.join(directory, safe + ".json")


def write_layout(directory: str, layout: dict) -> None:
    """Writer-thread side: temp file + atomic replace, so a reader (another client) never sees half a file."""
    os.makedirs(directory, exist_ok=True)
    path = _file_for(directory, layout["id"])
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(layout, handle, indent=1, sort_keys=True)
    os.replace(tmp, path)


def delete_layout(directory: str, layout_id: str) -> None:
    try:
        os.remove(_file_for(directory, layout_id))
    except FileNotFoundError:
        pass


class LayoutStore:
    """The layouts on disk, read and written off the render thread.

    ``layouts`` is an immutable tuple that is replaced whole when a scan finishes; reading it never blocks.
    ``refresh``, ``save``, ``delete`` queue their disk work on the shared writer thread (so they run in order:
    a save is on disk before the refresh that follows it reads the folder).

    A save or delete is visible at once and stays visible: a scan that was already queued ahead of it (the
    periodic refresh, say) reads the folder before the change is on disk, so every scan puts the changes that
    are not on disk yet back on top of what it read. Without that the new layout would blink out of the list
    and a deleted one would blink back."""

    def __init__(self, directory=None, submit=None):
        self._directory = directory
        self._submit = submit
        self.layouts: tuple = ()
        self.loaded = False
        self.error: "str | None" = None
        self._lock = threading.Lock()
        self._unsaved: dict = {}          # id -> layout accepted by save() but not yet written
        self._deleted: set = set()        # ids accepted by delete() but not yet removed

    @property
    def directory(self) -> str:
        return self._directory or default_directory()

    def _run(self, fn, *args) -> None:
        submit = self._submit
        if submit is None:
            from scan_writer import submit
        submit(fn, *args)

    def refresh(self) -> None:
        self._run(self._scan)

    def _publish(self, on_disk) -> None:
        """What is on disk, with the changes that are not on disk yet put back on top, newest first."""
        with self._lock:
            by_id = {d["id"]: d for d in on_disk}
            by_id.update(self._unsaved)
            for layout_id in self._deleted:
                by_id.pop(layout_id, None)
        self.layouts = tuple(sorted(by_id.values(), key=lambda d: d["saved_at"], reverse=True))

    def _scan(self, keep_error: bool = False) -> None:
        try:
            self._publish(read_all(self.directory))
            if not keep_error:
                self.error = None
        except Exception as exc:        # a scan must never take the writer thread's loop down
            logger.exception("ship_layouts: could not scan %s", self.directory)
            self.error = str(exc)
        self.loaded = True

    def save(self, layout: dict) -> None:
        """Remember *layout* at once (so the list shows it this frame) and queue the file write."""
        with self._lock:
            self._unsaved[layout["id"]] = layout
            self._deleted.discard(layout["id"])
        self._publish(self.layouts)
        self._run(self._write, layout)

    def _write(self, layout: dict) -> None:
        failed = False
        try:
            write_layout(self.directory, layout)
            with self._lock:
                if self._unsaved.get(layout["id"]) is layout:      # a newer save of it stays pending
                    del self._unsaved[layout["id"]]
        except Exception as exc:
            logger.exception("ship_layouts: could not save %s", layout.get("id"))
            self.error = f"could not save: {exc}"
            failed = True
        self._scan(keep_error=failed)

    def delete(self, layout_id: str) -> None:
        with self._lock:
            self._deleted.add(layout_id)
            self._unsaved.pop(layout_id, None)
        self._publish(self.layouts)
        self._run(self._delete, layout_id)

    def _delete(self, layout_id: str) -> None:
        failed = False
        try:
            delete_layout(self.directory, layout_id)
            with self._lock:
                self._deleted.discard(layout_id)
        except Exception as exc:
            logger.exception("ship_layouts: could not delete %s", layout_id)
            self.error = f"could not delete: {exc}"
            failed = True
        self._scan(keep_error=failed)

    def get(self, layout_id) -> "dict | None":
        return next((d for d in self.layouts if d["id"] == layout_id), None)

    def unique_name(self, name: str) -> str:
        """*name*, or "name (2)", "name (3)", ... when a layout already has it (case-insensitive)."""
        base = clean_name(name)
        taken = {d["name"].lower() for d in self.layouts}
        if base.lower() not in taken:
            return base
        number = 2
        while True:
            suffix = f" ({number})"
            candidate = base[:MAX_NAME_LEN - len(suffix)] + suffix
            if candidate.lower() not in taken:
                return candidate
            number += 1

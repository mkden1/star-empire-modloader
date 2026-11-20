"""inventory_filter_model.py - the pure model behind the inventory filters.

Every ship inventory window (your own and each stored ship's) and the player station's Storage tab can be
narrowed and reordered beyond the dev's text search:

  * TYPE     - the item's category (weapon, energy, shield, engine, ship plugin, resource, ...);
  * RARITY   - common ... ancient (the eight tiers the server colours items by);
  * SHOW     - what is fitted on the ship / station, or what only sits in the hold / storage;
  * LOADOUT  - only the items a saved Ship Layout (ship_layouts.py) is made of, so a storage ship opens on the
               one list of things a setup needs, ready to be picked up and moved;
  * SORT     - by name, type, rarity or quantity (the hold; fitted gear keeps its slot order).

The choices combine: within TYPE and within RARITY any ticked value passes, between the groups every one must.
The text search still applies on top (the dev's own ``ship_inventory_view`` runs it on what is left here).

Pure: no pygame, no window state, no disk. A ship inventory row is the dev's
``(row_key, text, colour, is_bar, bar_val, bar_max, bar_fill, item)`` tuple (``ship_inventory_rows``); a Storage row
is the dict ``_build_ps_storage_rows`` makes. Rows are returned as they came (same objects), never copied.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from inventory_search_model import is_searchable_inventory_row
from inventory_sections import (gear_slots, inventory_stack_quantity,
                                is_inventory_section_key)
from item_search_words import inventory_item_category, storage_item_category

# The server's eight rarity tiers, worst first, and the colours the inventory tints them with.
RARITIES = ("common", "uncommon", "rare", "epic", "legendary", "mythic", "relic", "ancient")
RARITY_RANK = {name: rank for rank, name in enumerate(RARITIES)}
RARITY_RGB = {
    "common": (180, 180, 180), "uncommon": (100, 220, 100), "rare": (80, 140, 255), "epic": (180, 80, 255),
    "legendary": (255, 220, 60), "mythic": (255, 140, 40), "relic": (255, 70, 70), "ancient": (80, 230, 255),
}

SOURCE_ALL, SOURCE_FITTED, SOURCE_HOLD = "all", "fitted", "hold"
SOURCES = (SOURCE_ALL, SOURCE_FITTED, SOURCE_HOLD)

SORT_DEFAULT, SORT_NAME, SORT_TYPE, SORT_RARITY, SORT_QUANTITY = "default", "name", "type", "rarity", "quantity"
SORTS = (SORT_DEFAULT, SORT_NAME, SORT_TYPE, SORT_RARITY, SORT_QUANTITY)

# Words for the categories whose name alone reads badly; the rest use the server's slot label or their own name.
_CATEGORY_LABELS = {
    "ship_plugin": "Plugin", "station_plugin": "Station Plugin", "turret_upgrade": "Turret Controller",
    "controller": "Fighter Controller", "resource": "Resource", "lootbox": "Crate", "trade_bot": "Trade Bot",
    "launch_tube": "Launch Tube", "solar_panel": "Solar Panel", "energy": "Power", "warper": "Jump Drive",
    "fabricator": "Fabricator", "searchlight": "Spotlight", "token": "Commodity",
}


# ── the filter spec ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FilterSpec:
    """What one inventory is narrowed and ordered by. Immutable, so it can key a cache."""
    types: frozenset = frozenset()
    rarities: frozenset = frozenset()
    source: str = SOURCE_ALL
    loadout: "str | None" = None          # a saved layout's id
    sort: str = SORT_DEFAULT
    reverse: bool = False                 # the sort's other direction

    def filtering(self) -> bool:
        """Does it hide any item?"""
        return bool(self.types or self.rarities or self.source != SOURCE_ALL or self.loadout)

    def active(self) -> bool:
        return self.filtering() or self.sort != SORT_DEFAULT

    def count(self) -> int:
        """How many controls are set (the funnel button's badge)."""
        return (bool(self.types) + bool(self.rarities) + (self.source != SOURCE_ALL) + bool(self.loadout)
                + (self.sort != SORT_DEFAULT))

    def toggle_type(self, category: str) -> "FilterSpec":
        return replace(self, types=self.types ^ {category})

    def toggle_rarity(self, rarity: str) -> "FilterSpec":
        return replace(self, rarities=self.rarities ^ {rarity})

    def with_source(self, source: str) -> "FilterSpec":
        return replace(self, source=source if source in SOURCES else SOURCE_ALL)

    def with_loadout(self, layout_id: "str | None") -> "FilterSpec":
        return replace(self, loadout=layout_id or None)

    def pick_sort(self, sort: str) -> "FilterSpec":
        """Choose a sort. Choosing the one already set turns it around, and once more switches it off, so one
        button steps through its direction and back to the game's own order."""
        if sort not in SORTS or sort == SORT_DEFAULT:
            return replace(self, sort=SORT_DEFAULT, reverse=False)
        if sort != self.sort:
            return replace(self, sort=sort, reverse=False)
        if not self.reverse:
            return replace(self, reverse=True)
        return replace(self, sort=SORT_DEFAULT, reverse=False)

    def cleared(self) -> "FilterSpec":
        return FilterSpec()


NO_FILTER = FilterSpec()


# ── facets of one item ───────────────────────────────────────────────────────

def category_label(category) -> str:
    """The words a category is shown by: "energy" is Power, "ship_plugin" is Plugin."""
    key = str(category or "")
    if key in _CATEGORY_LABELS:
        return _CATEGORY_LABELS[key]
    for slot in gear_slots():
        if slot.get("item_class") == key and slot.get("label"):
            return str(slot["label"])
    return key.replace("_", " ").title() or "Other"


def item_rarity(item) -> "str | None":
    """The rarity tier of an item dict: the server's tier name, else the nearest tier to its colour; None for
    things off the ladder (bulk resources)."""
    if not isinstance(item, dict):
        return None
    name = item.get("rarity_color")
    if isinstance(name, str) and name.strip().lower() in RARITY_RANK:
        return name.strip().lower()
    rgb = item.get("color_rgb")
    if isinstance(rgb, (list, tuple)) and len(rgb) >= 3 and item.get("item_class") != "cargo":
        try:
            colour = tuple(int(c) for c in rgb[:3])
        except (TypeError, ValueError):
            return None
        for tier, tier_rgb in RARITY_RGB.items():
            if colour == tier_rgb:
                return tier
    return None


def item_type_of(item) -> "str | None":
    """The item's own id ("blaze_cannon"): what a saved layout names its parts by."""
    if not isinstance(item, dict):
        return None
    value = item.get("item_type") or item.get("plugin_id")
    if not value:
        key = str(item.get("item_key") or "")
        if key.startswith("item:") and key.count(":") >= 2:
            value = key.split(":", 2)[2]
    return str(value) if value else None


def _item_name(item, row_text: str = "") -> str:
    if isinstance(item, dict):
        name = item.get("display_name") or item.get("item_type")
        if name:
            return str(name)
    return row_text.strip()


# ── ship inventory rows ──────────────────────────────────────────────────────

def ship_row_item(row):
    return row[-1] if row else None


def ship_row_source(row) -> "str | None":
    """Fitted, or in the hold (carried cargo, scoop cargo); None for rows that are no item."""
    if not is_searchable_inventory_row(row):
        return None
    key, item = row[0], ship_row_item(row)
    if isinstance(key, str) and key.startswith("scoop_cargo_"):
        return SOURCE_HOLD
    if isinstance(item, dict) and item.get("item_class") in ("cargo", "cargo_item"):
        return SOURCE_HOLD
    return SOURCE_FITTED


def ship_row_facets(row) -> dict:
    """``{"category", "rarity", "source", "type", "name", "quantity", "pinned"}`` of an item row."""
    item = ship_row_item(row)
    text = str(row[1]).replace("●", " ").replace("○", " ")
    quantity = 1
    pinned = False
    if isinstance(item, dict):
        pinned = bool(item.get("pinned"))
        quantity = (max(0, int(item.get("amount") or 0)) if item.get("item_class") == "cargo"
                    else inventory_stack_quantity(item))
    return {"category": inventory_item_category(item), "rarity": item_rarity(item),
            "source": ship_row_source(row), "type": item_type_of(item),
            "name": _item_name(item, text), "quantity": quantity, "pinned": pinned}


def _passes(facets: dict, spec: FilterSpec, wanted: "Wanted | None") -> bool:
    if spec.types and facets["category"] not in spec.types:
        return False
    if spec.rarities and facets["rarity"] not in spec.rarities:
        return False
    if spec.source != SOURCE_ALL and facets["source"] != spec.source:
        return False
    if spec.loadout and (wanted is None or facets["type"] not in wanted.needs):
        return False
    return True


def _sort_key(facets: dict, sort: str):
    name = str(facets["name"]).casefold()
    if sort == SORT_NAME:
        return (name,)
    if sort == SORT_TYPE:
        return (category_label(facets["category"]).casefold(), name)
    if sort == SORT_RARITY:
        # Rarest first; what is off the ladder (resources) after the commons.
        return (-RARITY_RANK.get(facets["rarity"], -1), name)
    if sort == SORT_QUANTITY:
        return (-int(facets["quantity"]), name)
    return (0,)


def _sorted_block(rows, sort: str, reverse: bool) -> list:
    """The item rows of one hold section in the sort's order; pinned stacks stay first, as the dev's order has them."""
    keyed = [(ship_row_facets(row), row) for row in rows]
    pinned = [pair for pair in keyed if pair[0]["pinned"]]
    loose = [pair for pair in keyed if not pair[0]["pinned"]]
    out = []
    for group in (pinned, loose):
        group.sort(key=lambda pair: _sort_key(pair[0], sort), reverse=reverse)
        out.extend(row for _facets, row in group)
    return out


def _sections(rows):
    """``[(header row | None, [body rows])]``: a header owns every row to the next header."""
    sections: list = []
    for row in rows:
        if not row:
            continue
        if is_inventory_section_key(row[0]):
            sections.append((row, []))
        else:
            if not sections:
                sections.append((None, []))
            sections[-1][1].append(row)
    return sections


def apply_to_ship_rows(rows, spec: FilterSpec, wanted: "Wanted | None" = None) -> list:
    """*rows* narrowed and ordered by *spec*.

    A filter that hides anything also hides the rows that are no item (empty slots, "(empty hold)") and the
    section headers left with nothing under them, so a loadout opens on just its items. A sort alone keeps every
    row and reorders only the sections that hold carried cargo.
    """
    if spec is None or not spec.active():
        return rows
    filtering = spec.filtering()
    out: list = []
    for header, body in _sections(rows):
        items = [row for row in body if is_searchable_inventory_row(row)]
        if filtering:
            body = [row for row in items if _passes(ship_row_facets(row), spec, wanted)]
            if not body:
                continue
        if spec.sort != SORT_DEFAULT and body:
            hold = [row for row in body if ship_row_source(row) == SOURCE_HOLD]
            if hold and len(hold) == len([row for row in body if is_searchable_inventory_row(row)]):
                hold_ids = {id(row) for row in hold}
                body = _sorted_block(hold, spec.sort, spec.reverse) + [
                    row for row in body if id(row) not in hold_ids]
        if header is not None:
            out.append(header)
        out.extend(body)
    return out


def ship_facet_counts(rows) -> tuple:
    """``(types, rarities)`` over every item row: ``types`` is ``[(category, count)]`` biggest first, ``rarities``
    ``{tier: count}``. What the filter tab offers (a type nothing here has is not listed)."""
    types: dict = {}
    rarities: dict = {}
    for row in rows or ():
        if not row or not is_searchable_inventory_row(row):
            continue
        facets = ship_row_facets(row)
        if facets["category"]:
            types[facets["category"]] = types.get(facets["category"], 0) + 1
        if facets["rarity"]:
            rarities[facets["rarity"]] = rarities.get(facets["rarity"], 0) + 1
    ordered = sorted(types.items(), key=lambda kv: (category_label(kv[0]).casefold()))
    return ordered, rarities


def ship_row_items(rows) -> list:
    """``[(item dict, source)]`` for every item row with a dict: what the loadout progress counts."""
    out = []
    for row in rows or ():
        if row and is_searchable_inventory_row(row) and isinstance(ship_row_item(row), dict):
            out.append((ship_row_item(row), ship_row_source(row)))
    return out


# ── station Storage rows ─────────────────────────────────────────────────────

def storage_row_facets(row: dict) -> "dict | None":
    """The same facets for a Storage row; None for the rows that are no item (header, placeholder)."""
    kind = row.get("_kind") if isinstance(row, dict) else None
    if kind == "cargo":
        quantity = int(row.get("_display_qty", row.get("_qty", 1)) or 0)
        category = storage_item_category(row.get("item_key"))
        return {"category": category, "rarity": item_rarity(row),
                "source": SOURCE_FITTED if row.get("_display_equipped") else SOURCE_HOLD,
                "type": item_type_of(row),
                "name": str(row.get("display_name") or row.get("item_type") or row.get("item_key") or "")
                .replace("_", " "),
                "quantity": quantity, "pinned": False}
    if kind == "plugin_slot":
        slot = row.get("_slot") or {}
        if not slot.get("plugin_id"):
            return None
        definition = slot.get("def") or {}
        return {"category": "station_plugin", "rarity": item_rarity(definition) or item_rarity(slot),
                "source": SOURCE_FITTED, "type": str(slot["plugin_id"]),
                "name": str(slot.get("display_name") or definition.get("display_name") or slot["plugin_id"])
                .replace("_", " "), "quantity": 1, "pinned": False}
    return None


def apply_to_storage_rows(rows, spec: FilterSpec, wanted: "Wanted | None" = None) -> list:
    """The Storage tab's rows narrowed and ordered by *spec*. The cargo segment is sorted in place (an equipped
    sub-row stays directly above its in-storage sub-row, they sort as one name); the plugin segment keeps its
    slot order. Nothing left reads as the dev's "nothing matches" row."""
    if spec is None or not spec.active():
        return list(rows or ())
    source = [row for row in (rows or ()) if isinstance(row, dict)]
    filtering = spec.filtering()
    kept: list = []
    header = None
    header_added = False
    for row in source:
        kind = row.get("_kind")
        if kind == "plugin_header":
            header = row
            continue
        facets = storage_row_facets(row)
        if filtering and (facets is None or not _passes(facets, spec, wanted)):
            continue
        if kind == "plugin_slot" and header is not None and not header_added:
            kept.append(header)          # the plugin segment's divider, once, above its first shown slot
            header_added = True
        kept.append(row)
    if spec.sort != SORT_DEFAULT:
        cargo = [row for row in kept if row.get("_kind") == "cargo"]
        rest = [row for row in kept if row.get("_kind") != "cargo"]
        kept = _sorted_storage_units(cargo, spec) + rest
    if not kept and filtering:
        return [{"_kind": "search_empty"}]
    return kept


def _sorted_storage_units(cargo: list, spec: FilterSpec) -> list:
    """The cargo rows in the sort's order. One item can show two rows (the equipped ones, then the ones in
    storage): they sort as one unit, by their shared name and rarity and their summed quantity, and keep their
    order inside it in either direction."""
    units: list = []
    for row in cargo:
        if units and units[-1][0].get("item_key") == row.get("item_key"):
            units[-1].append(row)
        else:
            units.append([row])
    keyed = []
    for unit in units:
        facets = dict(storage_row_facets(unit[0]))
        facets["quantity"] = sum(storage_row_facets(row)["quantity"] for row in unit)
        keyed.append((facets, unit))
    keyed.sort(key=lambda pair: _sort_key(pair[0], spec.sort), reverse=spec.reverse)
    return [row for _facets, unit in keyed for row in unit]


def storage_facet_counts(rows) -> tuple:
    """``(types, rarities)`` like ``ship_facet_counts`` for Storage rows."""
    types: dict = {}
    rarities: dict = {}
    for row in rows or ():
        facets = storage_row_facets(row)
        if facets is None:
            continue
        if facets["category"]:
            types[facets["category"]] = types.get(facets["category"], 0) + 1
        if facets["rarity"]:
            rarities[facets["rarity"]] = rarities.get(facets["rarity"], 0) + 1
    return sorted(types.items(), key=lambda kv: category_label(kv[0]).casefold()), rarities


def storage_row_items(rows) -> list:
    """``[(row dict, source)]`` for every Storage item row: quantities come from ``_display_qty``."""
    out = []
    for row in rows or ():
        facets = storage_row_facets(row)
        if facets is not None and facets["category"] != "station_plugin":
            out.append((dict(row, stack_quantity=facets["quantity"], _qty=facets["quantity"]), facets["source"]))
    return out


# ── loadouts ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Need:
    item_type: str
    name: str
    group: str          # weapons | equipment | plugins | turrets | spares
    count: int


GROUP_ORDER = ("weapons", "equipment", "plugins", "turrets", "spares")
GROUP_LABELS = {"weapons": "WEAPONS", "equipment": "EQUIPMENT", "plugins": "PLUGINS", "turrets": "TURRETS",
                "spares": "SPARES IN THE HOLD"}


@dataclass(frozen=True)
class Wanted:
    """The parts of a saved layout, by item id."""
    layout_id: str
    name: str
    ship: str
    needs: dict = field(default_factory=dict)       # item_type -> Need

    def ordered(self) -> list:
        return sorted(self.needs.values(),
                      key=lambda need: (GROUP_ORDER.index(need.group), need.name.casefold(), need.item_type))

    def total(self) -> int:
        return sum(need.count for need in self.needs.values())


def wanted_from_layout(layout) -> "Wanted | None":
    """The item ids a saved layout needs, counted: every fitted weapon, module, plugin, turret weapon and turret
    controller, plus the spares it carried in the hold. The same part in two places is one entry with the sum."""
    if not isinstance(layout, dict):
        return None
    needs: dict = {}

    def add(item_type, name, group, count=1):
        item_type = str(item_type or "")
        if not item_type:
            return
        try:
            count = max(1, int(count))
        except (TypeError, ValueError):
            count = 1
        old = needs.get(item_type)
        if old is None:
            needs[item_type] = Need(item_type, str(name or item_type), group, count)
        else:
            needs[item_type] = replace(old, count=old.count + count)

    for weapon in layout.get("weapons") or ():
        add(weapon.get("item_type"), weapon.get("display_name"), "weapons")
    for part in layout.get("equipment") or ():
        add(part.get("item_type"), part.get("display_name"), "equipment", part.get("count", 1))
    for plugin in layout.get("plugins") or ():
        add(plugin.get("plugin_id"), plugin.get("display_name"), "plugins")
    for turret in layout.get("turrets") or ():
        add(turret.get("weapon_type"), turret.get("weapon_name"), "turrets")
        for controller in turret.get("controllers") or ():
            add(controller.get("id"), controller.get("name"), "turrets")
    for spare in layout.get("cargo") or ():
        add(spare.get("item_type"), spare.get("display_name"), "spares", spare.get("qty", 1))
    ship = layout.get("ship") if isinstance(layout.get("ship"), dict) else {}
    return Wanted(str(layout.get("id") or ""), str(layout.get("name") or "?"),
                  str(ship.get("display_name") or ship.get("classification") or ""), needs)


def loadout_progress(items, wanted: "Wanted | None") -> list:
    """For each part of the loadout, in display order: ``(need, in_hold, fitted)`` counted over *items*
    (``[(item dict, source)]``), so the tab can say what is here and what still has to be found."""
    if wanted is None:
        return []
    hold: dict = {}
    fitted: dict = {}
    for item, source in items or ():
        item_type = item_type_of(item)
        if item_type not in wanted.needs:
            continue
        if source == SOURCE_HOLD:
            hold[item_type] = hold.get(item_type, 0) + inventory_stack_quantity(item)
        elif source == SOURCE_FITTED:
            fitted[item_type] = fitted.get(item_type, 0) + inventory_stack_quantity(item)
    return [(need, hold.get(need.item_type, 0), fitted.get(need.item_type, 0)) for need in wanted.ordered()]

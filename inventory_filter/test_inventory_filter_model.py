"""The pure model behind the inventory filters (inventory_filter_model.py), on rows the real row builders make."""
import unittest

import inventory_filter_model as m
from inventory_filter_model import FilterSpec
from inventory_sections import set_gear_slots
from ship_inventory_rows import build_ship_inventory_rows

# The server's slot list (a small stand-in: the real table lives in the server, which is not shipped).
set_gear_slots([
    {"item_class": "energy", "label": "Power", "always_shown": True},
    {"item_class": "shield", "label": "Shield", "always_shown": True},
    {"item_class": "engine", "label": "Engine", "always_shown": True},
    {"item_class": "tractor", "label": "Tractor Beam", "always_shown": True},
    {"item_class": "sensor", "label": "Sensor", "always_shown": False},
    {"item_class": "shield_charger", "label": "Shield Charger", "always_shown": False},
])

DIM = (90, 90, 90)


def cargo(category, item_type, name, rarity="common", qty=1, **extra):
    item = {"item_class": "cargo_item", "item_category": category, "item_type": item_type, "display_name": name,
            "cargo_key": f"item:{category}:{item_type}", "rarity_color": rarity, "stack_quantity": qty,
            "color_rgb": list(m.RARITY_RGB[rarity])}
    item.update(extra)
    return item


ITEMS = [
    {"item_class": "cargo_summary", "cargo_capacity": 900, "cargo_used": 300, "max_weapon_slots": 2},
    {"item_class": "weapon", "item_type": "blaze_cannon", "slot": "weapon_0", "slot_index": 0,
     "display_name": "Blaze Cannon", "rarity_color": "epic", "can_unequip": True},
    {"item_class": "energy", "item_type": "transcendence_core", "slot": "energy", "display_name": "Transcendence Core",
     "rarity_color": "legendary"},
    {"item_class": "shield", "item_type": "aegis_bank_mk1", "slot": "shield", "display_name": "Aegis Bank Mk I",
     "rarity_color": "rare"},
    {"item_class": "cargo", "resource": "metal", "amount": 1200},
    cargo("ship_plugin", "plugin_speed", "Speed Plugin", "uncommon"),
    cargo("shield", "aegis_bank_mk1", "Aegis Bank Mk I", "rare", qty=2),
    cargo("weapon", "blaze_cannon", "Blaze Cannon", "epic"),
    cargo("weapon", "wisplight_laser", "Wisplight Laser", "common", qty=4),
    cargo("energy", "star_heart", "Star Heart", "legendary"),
    cargo("token", "adept_token", "Adept Token", "common", pinned=True),
    {"item_class": "equipped_plugin", "item_type": "plugin_firing", "slot_index": 0, "display_name": "Firing Plugin",
     "rarity_color": "rare"},
    {"item_class": "equipped_plugin", "item_type": None, "slot_index": 1, "display_name": "Plugin Slot 2  (empty)"},
]


def rows():
    return build_ship_inventory_rows(ITEMS, dim=DIM, cargo_used=300, cargo_capacity=900)[0]


def names(shown):
    return [row[1].replace("●", "").replace("○", "").strip() for row in shown if not row[0].startswith("hdr_")]


def headers(shown):
    return [row[0] for row in shown if row[0].startswith("hdr_")]


LAYOUT = {
    "id": "123-hunter", "name": "hunter", "ship": {"display_name": "Oakheart", "classification": "Heavy Fighter"},
    "weapons": [{"slot": 0, "item_type": "blaze_cannon", "display_name": "Blaze Cannon"}],
    "equipment": [{"slot": "energy", "item_type": "star_heart", "display_name": "Star Heart"},
                  {"slot": "drone", "item_type": "wasp", "display_name": "Wasp", "count": 3}],
    "plugins": [{"slot": 0, "plugin_id": "plugin_firing", "display_name": "Firing Plugin"},
                {"slot": 1, "plugin_id": "plugin_speed", "display_name": "Speed Plugin"}],
    "turrets": [{"index": 0, "weapon_type": "wisplight_laser", "weapon_name": "Wisplight Laser",
                 "controllers": [{"id": "ctl_range", "name": "Range Controller"}]}],
    "cargo": [{"item_category": "weapon", "item_type": "blaze_cannon", "display_name": "Blaze Cannon", "qty": 1}],
}


class SpecTests(unittest.TestCase):
    def test_a_fresh_spec_hides_nothing_and_counts_nothing(self):
        spec = FilterSpec()
        self.assertFalse(spec.filtering())
        self.assertFalse(spec.active())
        self.assertEqual(spec.count(), 0)

    def test_toggles_add_and_remove(self):
        spec = FilterSpec().toggle_type("weapon").toggle_type("shield").toggle_rarity("epic")
        self.assertEqual(spec.types, {"weapon", "shield"})
        self.assertEqual(spec.count(), 2)
        spec = spec.toggle_type("weapon")
        self.assertEqual(spec.types, {"shield"})
        self.assertTrue(spec.filtering())
        spec = spec.with_source(m.SOURCE_HOLD).with_loadout("x")
        self.assertEqual(spec.count(), 4)
        self.assertEqual(spec.with_source("nonsense").source, m.SOURCE_ALL)
        self.assertEqual(spec.cleared(), FilterSpec())

    def test_picking_a_sort_turns_it_round_then_off_and_default_resets(self):
        spec = FilterSpec().pick_sort(m.SORT_RARITY)
        self.assertEqual((spec.sort, spec.reverse), (m.SORT_RARITY, False))
        spec = spec.pick_sort(m.SORT_RARITY)
        self.assertEqual((spec.sort, spec.reverse), (m.SORT_RARITY, True))
        self.assertEqual(spec.pick_sort(m.SORT_RARITY), FilterSpec())          # the third press switches it off
        spec = spec.pick_sort(m.SORT_NAME)
        self.assertEqual((spec.sort, spec.reverse), (m.SORT_NAME, False))
        self.assertEqual(spec.pick_sort(m.SORT_DEFAULT), FilterSpec())
        self.assertEqual(spec.pick_sort("nonsense"), FilterSpec())
        self.assertTrue(FilterSpec().pick_sort(m.SORT_NAME).active())
        self.assertFalse(FilterSpec().pick_sort(m.SORT_NAME).filtering())

    def test_specs_are_hashable_so_they_can_key_a_cache(self):
        self.assertEqual(hash(FilterSpec().toggle_type("a")), hash(FilterSpec().toggle_type("a")))
        self.assertEqual(len({FilterSpec(), FilterSpec(), FilterSpec().toggle_type("a")}), 2)


class FacetTests(unittest.TestCase):
    def test_rarity_comes_from_the_tier_name_then_the_colour(self):
        self.assertEqual(m.item_rarity({"rarity_color": "Epic"}), "epic")
        self.assertEqual(m.item_rarity({"color_rgb": [255, 70, 70]}), "relic")
        self.assertIsNone(m.item_rarity({"color_rgb": [1, 2, 3]}))
        self.assertIsNone(m.item_rarity({"item_class": "cargo", "color_rgb": [180, 180, 180]}))
        self.assertIsNone(m.item_rarity(None))
        self.assertIsNone(m.item_rarity({"rarity_color": "weird"}))

    def test_type_labels_read_as_words(self):
        self.assertEqual(m.category_label("energy"), "Power")
        self.assertEqual(m.category_label("ship_plugin"), "Plugin")
        self.assertEqual(m.category_label("turret_upgrade"), "Turret Controller")
        self.assertEqual(m.category_label("shield_charger"), "Shield Charger")
        self.assertEqual(m.category_label("brand_new_thing"), "Brand New Thing")
        self.assertEqual(m.category_label(None), "Other")

    def test_the_item_id_comes_from_the_row_or_its_key(self):
        self.assertEqual(m.item_type_of({"item_type": "a"}), "a")
        self.assertEqual(m.item_type_of({"plugin_id": "p"}), "p")
        self.assertEqual(m.item_type_of({"item_key": "item:shield:aegis_bank_mk1"}), "aegis_bank_mk1")
        self.assertIsNone(m.item_type_of({"item_key": "metal"}))
        self.assertIsNone(m.item_type_of(None))


class ShipRowTests(unittest.TestCase):
    def test_no_filter_and_no_sort_returns_the_rows_untouched(self):
        shown = rows()
        self.assertIs(m.apply_to_ship_rows(shown, FilterSpec()), shown)
        self.assertIs(m.apply_to_ship_rows(shown, None), shown)

    def test_a_type_filter_keeps_that_type_everywhere_and_drops_the_rest(self):
        shown = m.apply_to_ship_rows(rows(), FilterSpec().toggle_type("weapon"))
        self.assertEqual(names(shown), ["Blaze Cannon", "Blaze Cannon", "Wisplight Laser x4"])
        # The sections with no weapon left, and the empty slots, are gone with their headers.
        self.assertEqual(headers(shown), ["hdr_wpn", "hdr_cargo"])
        self.assertNotIn("wpn_empty_1", [row[0] for row in shown])

    def test_several_types_pass_any_of_them(self):
        shown = m.apply_to_ship_rows(rows(), FilterSpec().toggle_type("energy").toggle_type("shield"))
        self.assertEqual(sorted(names(shown)), sorted(
            ["Power: Transcendence Core", "Shield: Aegis Bank Mk I", "Aegis Bank Mk I x2", "Star Heart"]))

    def test_resources_are_a_type_of_their_own(self):
        shown = m.apply_to_ship_rows(rows(), FilterSpec().toggle_type("resource"))
        self.assertEqual(names(shown), ["Metal x1,200"])

    def test_a_rarity_filter_passes_fitted_and_carried_items_of_that_tier(self):
        shown = m.apply_to_ship_rows(rows(), FilterSpec().toggle_rarity("rare"))
        self.assertEqual(sorted(names(shown)), sorted(
            ["Shield: Aegis Bank Mk I", "Aegis Bank Mk I x2", "Firing Plugin"]))

    def test_rarities_combine_with_types_as_and(self):
        spec = FilterSpec().toggle_type("weapon").toggle_rarity("epic")
        self.assertEqual(names(m.apply_to_ship_rows(rows(), spec)), ["Blaze Cannon", "Blaze Cannon"])
        spec = spec.toggle_rarity("common")
        self.assertEqual(len(names(m.apply_to_ship_rows(rows(), spec))), 3)

    def test_a_rarity_filter_hides_what_is_off_the_ladder(self):
        shown = m.apply_to_ship_rows(rows(), FilterSpec().toggle_rarity("common"))
        self.assertNotIn("Metal x1,200", names(shown))

    def test_show_fitted_and_show_in_hold(self):
        fitted = m.apply_to_ship_rows(rows(), FilterSpec().with_source(m.SOURCE_FITTED))
        self.assertEqual(headers(fitted), ["hdr_wpn", "hdr_eq", "hdr_plugins"])
        self.assertTrue(all(row[0] != "cargo_0" and not row[0].startswith("cargo_item") for row in fitted))
        hold = m.apply_to_ship_rows(rows(), FilterSpec().with_source(m.SOURCE_HOLD))
        self.assertEqual(headers(hold), ["hdr_cargo"])
        self.assertEqual(len(names(hold)), 7)

    def test_a_loadout_keeps_only_its_parts_whether_fitted_or_carried(self):
        wanted = m.wanted_from_layout(LAYOUT)
        spec = FilterSpec().with_loadout("123-hunter")
        shown = m.apply_to_ship_rows(rows(), spec, wanted)
        self.assertEqual(sorted(names(shown)), sorted([
            "Blaze Cannon", "Blaze Cannon", "Wisplight Laser x4", "Star Heart", "Speed Plugin", "Firing Plugin"]))
        # The aegis shield, the metal and the token are not in it.
        self.assertTrue(all(key not in [row[0] for row in shown] for key in ("cargo_0",)))

    def test_a_loadout_that_cannot_be_read_hides_everything_rather_than_nothing(self):
        shown = m.apply_to_ship_rows(rows(), FilterSpec().with_loadout("gone"), None)
        self.assertEqual(shown, [])

    def test_sort_alone_keeps_every_row_and_reorders_only_the_hold(self):
        base = rows()
        shown = m.apply_to_ship_rows(base, FilterSpec().pick_sort(m.SORT_NAME))
        self.assertEqual(len(shown), len(base))
        self.assertEqual([row[0] for row in shown if not row[0].startswith("cargo")],
                         [row[0] for row in base if not row[0].startswith("cargo")])
        hold = [row for row in shown if row[0].startswith("cargo_")]
        # Pinned stays first; the rest A-Z.
        self.assertEqual(names(hold), ["Adept Token  [PINNED]", "Aegis Bank Mk I x2", "Blaze Cannon", "Metal x1,200",
                                       "Speed Plugin", "Star Heart", "Wisplight Laser x4"])

    def test_sort_by_rarity_puts_the_rarest_first_and_reverse_flips_it(self):
        spec = FilterSpec().pick_sort(m.SORT_RARITY)
        hold = [row for row in m.apply_to_ship_rows(rows(), spec) if row[0].startswith("cargo_")]
        order = [m.ship_row_facets(row)["rarity"] for row in hold]
        ranked = [m.RARITY_RANK.get(r, -1) for r in order[1:]]          # [0] is the pinned token
        self.assertEqual(ranked, sorted(ranked, reverse=True))
        flipped = [row for row in m.apply_to_ship_rows(rows(), spec.pick_sort(m.SORT_RARITY))
                   if row[0].startswith("cargo_")]
        ranks = [m.RARITY_RANK.get(m.ship_row_facets(row)["rarity"], -1) for row in flipped]
        self.assertEqual(ranks[1:], sorted(ranks[1:]))

    def test_sort_by_quantity_puts_the_biggest_stack_first(self):
        spec = FilterSpec().pick_sort(m.SORT_QUANTITY)
        hold = [row for row in m.apply_to_ship_rows(rows(), spec) if row[0].startswith("cargo_")]
        self.assertEqual(names(hold)[1], "Metal x1,200")
        self.assertEqual(names(hold)[2], "Wisplight Laser x4")

    def test_sort_by_type_groups_by_the_type_label(self):
        spec = FilterSpec().pick_sort(m.SORT_TYPE)
        hold = [row for row in m.apply_to_ship_rows(rows(), spec) if row[0].startswith("cargo_")]
        labels = [m.category_label(m.ship_row_facets(row)["category"]) for row in hold[1:]]
        self.assertEqual(labels, sorted(labels, key=str.casefold))

    def test_a_sort_with_a_filter_sorts_what_is_left(self):
        spec = FilterSpec().toggle_type("weapon").pick_sort(m.SORT_NAME)
        shown = m.apply_to_ship_rows(rows(), spec)
        hold = [row for row in shown if row[0].startswith("cargo_")]
        self.assertEqual(names(hold), ["Blaze Cannon", "Wisplight Laser x4"])

    def test_the_input_rows_are_never_changed(self):
        base = rows()
        before = list(base)
        m.apply_to_ship_rows(base, FilterSpec().toggle_type("weapon").pick_sort(m.SORT_NAME))
        self.assertEqual(base, before)

    def test_what_the_tab_offers_comes_from_the_items_here(self):
        types, rarities = m.ship_facet_counts(rows())
        kinds = dict(types)
        self.assertEqual(kinds["weapon"], 3)
        self.assertEqual(kinds["resource"], 1)
        self.assertNotIn("sensor", kinds)
        self.assertEqual([label for label in (m.category_label(k) for k, _ in types)],
                         sorted((m.category_label(k) for k, _ in types), key=str.casefold))
        self.assertEqual(rarities["legendary"], 2)
        self.assertNotIn("mythic", rarities)


def storage_row(category, item_type, name, rarity="common", qty=1, equipped=False, unequipped=False, **extra):
    row = {"_kind": "cargo", "item_key": f"item:{category}:{item_type}", "item_type": item_type, "display_name": name,
           "rarity_color": rarity, "_qty": qty, "_display_qty": qty, "_display_equipped": equipped,
           "_display_unequipped": unequipped}
    row.update(extra)
    return row


STORAGE = [
    storage_row("weapon", "blaze_cannon", "Blaze Cannon", "epic"),
    storage_row("shield", "aegis_bank_mk1", "Aegis Bank Mk I", "rare", qty=1, equipped=True),
    storage_row("shield", "aegis_bank_mk1", "Aegis Bank Mk I", "rare", qty=3, unequipped=True),
    storage_row("energy", "star_heart", "Star Heart", "legendary"),
    {"_kind": "cargo", "item_key": "metal", "item_type": "metal", "display_name": "metal", "_qty": 900,
     "_display_qty": 900, "_display_equipped": False},
    {"_kind": "plugin_header", "_filled": 1, "_slots": 2},
    {"_kind": "plugin_slot", "_slot_index": 0, "_slot": {"plugin_id": "p_mining", "display_name": "Mining Plugin",
                                                         "def": {"rarity_color": "epic", "bonuses": []}}},
    {"_kind": "plugin_slot", "_slot_index": 1, "_slot": {"slot_index": 1, "plugin_id": ""}},
]


class StorageRowTests(unittest.TestCase):
    def kinds(self, shown):
        return [(row.get("_kind"), row.get("item_type") or (row.get("_slot") or {}).get("plugin_id"))
                for row in shown]

    def test_nothing_set_returns_a_copy_in_the_same_order(self):
        shown = m.apply_to_storage_rows(STORAGE, FilterSpec())
        self.assertEqual(shown, STORAGE)

    def test_type_and_rarity_filter_storage_rows(self):
        shown = m.apply_to_storage_rows(STORAGE, FilterSpec().toggle_type("shield"))
        self.assertEqual(self.kinds(shown), [("cargo", "aegis_bank_mk1")] * 2)
        shown = m.apply_to_storage_rows(STORAGE, FilterSpec().toggle_rarity("epic"))
        self.assertEqual(self.kinds(shown), [("cargo", "blaze_cannon"), ("plugin_header", None),
                                             ("plugin_slot", "p_mining")])

    def test_station_plugins_are_a_type_and_keep_their_header(self):
        shown = m.apply_to_storage_rows(STORAGE, FilterSpec().toggle_type("station_plugin"))
        self.assertEqual(self.kinds(shown), [("plugin_header", None), ("plugin_slot", "p_mining")])

    def test_show_fitted_means_the_equipped_sub_rows_and_plugins(self):
        shown = m.apply_to_storage_rows(STORAGE, FilterSpec().with_source(m.SOURCE_FITTED))
        self.assertEqual(self.kinds(shown), [("cargo", "aegis_bank_mk1"), ("plugin_header", None),
                                             ("plugin_slot", "p_mining")])
        shown = m.apply_to_storage_rows(STORAGE, FilterSpec().with_source(m.SOURCE_HOLD))
        self.assertEqual([r["_display_qty"] for r in shown], [1, 3, 1, 900])

    def test_a_loadout_keeps_its_parts_in_storage(self):
        wanted = m.wanted_from_layout(LAYOUT)
        shown = m.apply_to_storage_rows(STORAGE, FilterSpec().with_loadout("123-hunter"), wanted)
        self.assertEqual(self.kinds(shown), [("cargo", "blaze_cannon"), ("cargo", "star_heart")])

    def test_nothing_left_reads_as_nothing_matches(self):
        shown = m.apply_to_storage_rows(STORAGE, FilterSpec().toggle_type("sensor"))
        self.assertEqual(shown, [{"_kind": "search_empty"}])

    def test_sorting_orders_the_cargo_and_leaves_the_plugin_segment_last(self):
        shown = m.apply_to_storage_rows(STORAGE, FilterSpec().pick_sort(m.SORT_NAME))
        self.assertEqual([r.get("display_name") for r in shown if r["_kind"] == "cargo"],
                         ["Aegis Bank Mk I", "Aegis Bank Mk I", "Blaze Cannon", "metal", "Star Heart"])
        self.assertEqual([r["_kind"] for r in shown[-3:]], ["plugin_header", "plugin_slot", "plugin_slot"])

    def test_an_item_keeps_its_equipped_row_above_its_stored_row_either_way(self):
        for sort in (m.SORT_NAME, m.SORT_QUANTITY, m.SORT_RARITY):
            for flips in (0, 1):
                spec = FilterSpec().pick_sort(sort)
                if flips:
                    spec = spec.pick_sort(sort)
                shown = [r for r in m.apply_to_storage_rows(STORAGE, spec) if r["_kind"] == "cargo"
                         and r["item_type"] == "aegis_bank_mk1"]
                self.assertEqual([r["_display_equipped"] for r in shown], [True, False], (sort, flips))

    def test_sort_by_quantity_puts_the_biggest_first(self):
        shown = m.apply_to_storage_rows(STORAGE, FilterSpec().pick_sort(m.SORT_QUANTITY))
        self.assertEqual(shown[0]["item_type"], "metal")

    def test_facet_counts_and_items_for_the_tab(self):
        types, rarities = m.storage_facet_counts(STORAGE)
        self.assertEqual(dict(types)["shield"], 2)
        self.assertEqual(dict(types)["station_plugin"], 1)
        self.assertEqual(rarities["epic"], 2)
        items = m.storage_row_items(STORAGE)
        self.assertNotIn("p_mining", [m.item_type_of(item) for item, _ in items])


class LoadoutTests(unittest.TestCase):
    def test_every_part_of_a_layout_is_a_need_with_its_count(self):
        wanted = m.wanted_from_layout(LAYOUT)
        self.assertEqual((wanted.name, wanted.ship, wanted.layout_id), ("hunter", "Oakheart", "123-hunter"))
        needs = wanted.needs
        self.assertEqual(needs["blaze_cannon"].count, 2)               # fitted weapon + a spare in the hold
        self.assertEqual(needs["wasp"].count, 3)                       # a drone family counted by type
        self.assertEqual(needs["wisplight_laser"].group, "turrets")
        self.assertEqual(needs["ctl_range"].name, "Range Controller")
        self.assertEqual(needs["plugin_speed"].group, "plugins")
        self.assertEqual(wanted.total(), 2 + 1 + 3 + 1 + 1 + 1 + 1)

    def test_the_list_is_ordered_by_group_then_name(self):
        wanted = m.wanted_from_layout(LAYOUT)
        self.assertEqual([need.group for need in wanted.ordered()],
                         ["weapons", "equipment", "equipment", "plugins", "plugins", "turrets", "turrets"])
        self.assertEqual([n.name for n in wanted.ordered() if n.group == "equipment"], ["Star Heart", "Wasp"])

    def test_a_broken_layout_gives_nothing(self):
        self.assertIsNone(m.wanted_from_layout(None))
        self.assertIsNone(m.wanted_from_layout("x"))
        empty = m.wanted_from_layout({"id": "e", "name": "empty", "weapons": [{"item_type": ""}], "cargo": [None or {}]})
        self.assertEqual(empty.needs, {})

    def test_progress_counts_the_hold_and_the_fitted_separately(self):
        wanted = m.wanted_from_layout(LAYOUT)
        progress = {need.item_type: (have_hold, have_fitted)
                    for need, have_hold, have_fitted in m.loadout_progress(m.ship_row_items(rows()), wanted)}
        self.assertEqual(progress["blaze_cannon"], (1, 1))
        self.assertEqual(progress["wisplight_laser"], (4, 0))
        self.assertEqual(progress["plugin_firing"], (0, 1))
        self.assertEqual(progress["wasp"], (0, 0))
        self.assertEqual(m.loadout_progress([], None), [])

    def test_progress_over_storage_uses_the_row_quantities(self):
        wanted = m.wanted_from_layout(LAYOUT)
        progress = {need.item_type: (h, f) for need, h, f in m.loadout_progress(m.storage_row_items(STORAGE), wanted)}
        self.assertEqual(progress["star_heart"], (1, 0))
        self.assertEqual(progress["blaze_cannon"], (1, 0))


if __name__ == "__main__":
    unittest.main()

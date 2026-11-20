"""Tests for ship_layouts.py - capturing a ship's fit, comparing two fits, and the layout files."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

import scan_paths
import ship_layouts as sl


def weapon(slot, item_type="patriot_beam", name="Patriot Beam"):
    return {"item_class": "weapon", "slot": f"weapon_{slot}", "item_type": item_type, "display_name": name,
            "can_unequip": True}


def gear(slot, item_type, name, item_class=None):
    return {"item_class": item_class or slot, "slot": slot, "item_type": item_type, "display_name": name}


def plugin(index, plugin_id="plugin_speed_refined", name="Refined Speed Plugin"):
    return {"item_class": "equipped_plugin", "slot_index": index, "item_type": plugin_id, "display_name": name}


def cargo_item(item_type, name, qty=1, category="weapon"):
    return {"item_class": "cargo_item", "item_category": category, "item_type": item_type, "display_name": name,
            "stack_quantity": qty}


def hardpoint(index, weapon_type=None, name=None, controllers=(), slots=2, kind="standard", dps=0.0):
    return {"index": index, "kind": kind, "upgrade_slots": slots, "weapon_type": weapon_type,
            "weapon_def": {"display_name": name} if weapon_type else None, "effective_dps": dps,
            "upgrades": [{"slot_index": i, "upgrade_id": cid, "upgrade_def": {"display_name": cname} if cid else None}
                         for i, (cid, cname) in enumerate(controllers)]}


def specs(ship_type="panstar", name="Panstar", dps=(16848.9, 100.0), visibility=10000.0, cap=667, used=463,
          kinetic=0.14, flat=0.0, state=None, effective_mass=285700.0, accel=19285.6, turn=418.0,
          equipped=331, hold=132):
    data = {
        "ship": {"ship_type": ship_type, "display_name": name, "classification": "Light Fighter",
                 "resistance": 0.14, "kinetic_resistance": kinetic, "laser_resistance": 0.14,
                 "thermal_resistance": 0.14, "biogenic_resistance": 0.14, "mining_resistance": 0.14,
                 "energy_resistance": 0.14, "cargo_capacity": cap, "cargo_used": used,
                 "equipped_cargo_used": equipped, "hold_cargo_used": hold, "visibility": visibility, "mass": 73000.0,
                 "effective_mass": effective_mass, "evasion": 0.0, "flat_damage_mitigation": flat,
                 "plugin_slots": 5},
        "engine": {"max_speed": 4643.5, "accel": accel, "turn_rate": turn},
        "shield": {"max_shields": 20629.2, "recharge_rate": 356.15},
        "energy": {"max_energy": 6240.0, "energy_output": 2312.43},
        "weapons": [{"dps": d, "slot": i} for i, d in enumerate(dps)],
    }
    if state:
        data["state"] = state
    if state == "in_space":                       # the server only sends the split while docked
        del data["ship"]["equipped_cargo_used"], data["ship"]["hold_cargo_used"]
    return data


def cargo_summary(cap=6810, used=3650, equipped=3637, hold=13):
    """The inventory's cargo_summary row (real numbers from a Forest Warden)."""
    return {"item_class": "cargo_summary", "item_type": "cargo_hold", "cargo_capacity": cap, "cargo_used": used,
            "equipped_cargo_used": equipped, "hold_cargo_used": hold, "max_weapon_slots": 3}


def fit(**overrides):
    """A complete layout, built through ``capture`` the way the client builds one."""
    inventory = overrides.pop("inventory", None)
    if inventory is None:
        inventory = [weapon(0), weapon(1), gear("energy", "glorious_power", "Glorious Power"),
                     gear("shield", "stormsail", "Stormsail"), gear("engine", "glorious_booster", "Glorious Booster"),
                     plugin(0), plugin(1), plugin(2, None, "Plugin Slot 3 (empty)")]
    turret = overrides.pop("turret_state", {"turrets_enabled": True, "hardpoints": [
        hardpoint(0, "kings_thumper", "King's Thumper", [("c_broad", "Broadside Controller")] * 2, dps=21000.0),
        hardpoint(1)]})
    layout = sl.capture(inventory, overrides.pop("plugin_data", None), turret,
                        overrides.pop("specs", specs()), overrides.pop("name", "hunter stealth"),
                        now=overrides.pop("now", 1000.0), username=overrides.pop("username", "mk"))
    assert not overrides, overrides
    return layout


class ExtractionTests(unittest.TestCase):
    def test_weapons_come_in_numeric_slot_order_and_skip_everything_else(self):
        rows = [weapon(10, "c", "C"), weapon(2, "b", "B"), weapon(0, "a", "A"),
                {"item_class": "weapon", "slot": "weapon_3", "item_type": None, "display_name": "x"},
                {"item_class": "cargo_item", "slot": "weapon_4", "item_type": "z"},
                gear("engine", "ion", "Ion Drive")]
        self.assertEqual([w["slot"] for w in sl.extract_weapons(rows)], [0, 2, 10])
        self.assertEqual([w["item_type"] for w in sl.extract_weapons(rows)], ["a", "b", "c"])
        self.assertEqual(sl.extract_weapons(None), [])

    def test_a_weapon_class_row_in_another_slot_is_not_a_main_weapon(self):
        rows = [{"item_class": "weapon", "slot": "hangar_2", "item_type": "x", "display_name": "X"},
                {"item_class": "weapon", "slot": None, "item_type": "y", "display_name": "Y"}, weapon(1)]
        self.assertEqual([w["slot"] for w in sl.extract_weapons(rows)], [1])

    def test_families_are_listed_by_name(self):
        rows = [gear("solar_panel_0", "z", "Zeta Panel", "solar_panel"), gear("solar_panel_1", "a", "Alpha Panel", "solar_panel"),
                gear("solar_panel_2", "m", "Mid Panel", "solar_panel")]
        self.assertEqual([e["display_name"] for e in sl.extract_equipment(rows)],
                         ["Alpha Panel", "Mid Panel", "Zeta Panel"])

    def test_turrets_are_listed_by_mount_number(self):
        state = {"hardpoints": [hardpoint(2, "c", "C"), hardpoint(0, "a", "A"), hardpoint(1, "b", "B")]}
        self.assertEqual([t["index"] for t in sl.extract_turrets(state)[0]], [0, 1, 2])

    def test_equipment_is_in_the_inventory_panels_order(self):
        rows = [gear("sensor", "s1", "Sensor 1"), gear("engine", "e1", "Engine 1"), gear("cloak", "c1", "Cloak 1"),
                gear("energy", "p1", "Power 1"), gear("shield", "sh1", "Shield 1"), gear("tractor", "t1", "Tractor 1")]
        order = [e["slot"] for e in sl.extract_equipment(rows)]
        self.assertEqual(order[:4], ["energy", "shield", "engine", "tractor"])
        self.assertEqual(order[4:], ["cloak", "sensor"])                       # then A-Z by label

    def test_built_in_empty_weapon_and_unknown_rows_are_not_equipment(self):
        rows = [{**gear("hangar", "perch", "Hangar Expansion"), "built_in": True}, gear("engine", None, "none"),
                weapon(0), {"item_class": "cargo_item", "item_type": "x", "slot": None},
                {"item_class": "mystery", "slot": "mystery", "item_type": "m"}]
        self.assertEqual(sl.extract_equipment(rows), [])

    def test_multi_fit_families_are_counted_by_type(self):
        rows = [gear("solar_panel_0", "sp1", "Solar 1", "solar_panel"), gear("solar_panel_1", "sp1", "Solar 1", "solar_panel"),
                gear("solar_panel_2", "sp2", "Solar 2", "solar_panel"), gear("drone_0", "d1", "Drone 1", "drone"),
                gear("fabricator_0", "f1", "Fab 1", "fabricator"), gear("engine", "e1", "E", "engine")]
        equipment = sl.extract_equipment(rows)
        self.assertEqual([(e["slot"], e["item_type"], e.get("count")) for e in equipment],
                         [("engine", "e1", None), ("solar_panel", "sp1", 2), ("solar_panel", "sp2", 1),
                          ("fabricator", "f1", 1), ("drone", "d1", 1)])

    def test_plugins_from_inventory_rows_skip_empty_slots(self):
        rows = [plugin(2, "b", "B"), plugin(0, "a", "A"), plugin(1, None, "empty")]
        self.assertEqual([(p["slot"], p["plugin_id"]) for p in sl.extract_plugins(rows)], [(0, "a"), (2, "b")])

    def test_plugins_fall_back_on_ship_plugin_data(self):
        data = {"installed": {"0": "plugin_a", "1": None, "3": "plugin_b", "x": "bad"},
                "plugin_defs": {"plugin_a": {"display_name": "Plugin A"}}}
        self.assertEqual(sl.extract_plugins([], data), [
            {"slot": 0, "plugin_id": "plugin_a", "display_name": "Plugin A"},
            {"slot": 3, "plugin_id": "plugin_b", "display_name": "plugin_b"}])
        self.assertEqual(sl.extract_plugins([], None), [])
        self.assertEqual(sl.extract_plugins([plugin(5, None, "e")], data), [])        # rows win, even all-empty

    def test_turrets_carry_weapon_and_controllers_and_skip_empty_mounts(self):
        state = {"turrets_enabled": True, "hardpoints": [
            hardpoint(1, "wisp", "Wisplight", [("c1", "Solstice"), (None, None)]), hardpoint(0),
            hardpoint(2, None, None, [("c2", "Lonely")]), "junk"]}
        turrets, enabled = sl.extract_turrets(state)
        self.assertTrue(enabled)
        self.assertEqual([(t["index"], t["weapon_name"], [c["name"] for c in t["controllers"]]) for t in turrets],
                         [(1, "Wisplight", ["Solstice"]), (2, "", ["Lonely"])])
        self.assertEqual(sl.extract_turrets(None), ([], None))
        self.assertEqual(sl.extract_turrets({"hardpoints": []}), ([], None))

    def test_cargo_counts_items_by_type_and_leaves_resources_out(self):
        rows = [cargo_item("kt", "King's Thumper", 1), cargo_item("kt", "King's Thumper", 2),
                cargo_item("pb", "Patriot Beam"), cargo_item("kt", "Other Category Kt", 1, category="plugin"),
                {"item_class": "cargo", "resource": "metal", "amount": 9000},
                {"item_class": "scoop_cargo_item", "item_type": "scooped"}]
        self.assertEqual([(e["item_category"], e["item_type"], e["qty"]) for e in sl.extract_cargo(rows)],
                         [("weapon", "kt", 3), ("plugin", "kt", 1), ("weapon", "pb", 1)])

    def test_ship_comes_from_the_specs(self):
        self.assertEqual(sl.extract_ship(specs()), {"ship_type": "panstar", "display_name": "Panstar",
                                                    "classification": "Light Fighter"})
        self.assertEqual(sl.extract_ship(None), {})
        self.assertEqual(sl.extract_ship({"ship": "junk"}), {})


class StatsTests(unittest.TestCase):
    def test_every_number_is_picked_up(self):
        stats = sl.extract_stats(specs(), {"hardpoints": [hardpoint(0, "w", "W", dps=2000.0),
                                                          hardpoint(1, "w", "W", dps=500.0), hardpoint(2, dps=999.0)]})
        self.assertEqual(stats["weapon_dps"], 16848.9)               # the best single weapon, not the sum
        self.assertEqual(stats["turret_dps"], 2500.0)                # mounts without a weapon count nothing
        self.assertAlmostEqual(stats["total_dps"], 16848.9 + 2500.0)
        self.assertEqual((stats["shield_bank"], stats["shield_regen"]), (20629.2, 356.15))
        self.assertEqual((stats["energy_bank"], stats["energy_regen"]), (6240.0, 2312.43))
        self.assertEqual((stats["max_speed"], stats["accel"], stats["turn_rate"]), (4643.5, 19285.6, 418.0))
        self.assertEqual((stats["hull_capacity"], stats["hull_equipped"], stats["hull_hold"], stats["hull_free"]),
                         (667.0, 331.0, 132.0, 204.0))
        self.assertEqual((stats["visibility"], stats["mass"], stats["evasion"]), (10000.0, 73000.0, 0.0))

    def test_all_six_resistances_with_the_flat_value_as_the_fallback(self):
        data = specs(kinetic=0.5)
        del data["ship"]["laser_resistance"]
        stats = sl.extract_stats(data)
        self.assertEqual(stats["resist_kinetic"], 0.5)
        self.assertEqual(stats["resist_laser"], 0.14)                # the flat resistance fills the gap
        self.assertEqual(sorted(k for k in stats if k.startswith("resist_")),
                         ["resist_biogenic", "resist_energy", "resist_kinetic", "resist_laser", "resist_mining",
                          "resist_thermal"])

    def test_missing_and_broken_numbers_are_left_out(self):
        data = specs()
        data["ship"]["visibility"] = "n/a"
        data["engine"]["accel"] = float("nan")
        data["shield"]["max_shields"] = float("inf")
        data["weapons"] = [{"dps": None}, "junk"]
        stats = sl.extract_stats(data)
        for key in ("visibility", "accel", "shield_bank", "weapon_dps", "total_dps"):
            self.assertNotIn(key, stats)
        self.assertEqual(stats["max_speed"], 4643.5)
        self.assertEqual(sl.extract_stats(None), {})
        self.assertEqual(sl.extract_stats({}), {})

    def test_numbers_given_as_text_are_read(self):
        data = specs()
        data["ship"]["visibility"] = "250.5"
        self.assertEqual(sl.extract_stats(data)["visibility"], 250.5)

    def test_total_dps_needs_the_turret_state_so_it_is_never_a_half_truth(self):
        self.assertNotIn("total_dps", sl.extract_stats(specs(), None))
        self.assertNotIn("turret_dps", sl.extract_stats(specs(), None))
        stats = sl.extract_stats(specs(), {"hardpoints": []})
        self.assertEqual((stats["turret_dps"], stats["total_dps"]), (0.0, 16848.9))

    def test_turrets_alone_still_give_a_total(self):
        stats = sl.extract_stats(None, {"hardpoints": [hardpoint(0, "w", "W", dps=700.0)]})
        self.assertEqual((stats["turret_dps"], stats["total_dps"]), (700.0, 700.0))

    def test_hull_numbers_come_from_the_inventory_summary_docked_or_in_space(self):
        # what the user saw: docked, the specs said 1,453 gear / 13 hold, the ship's own summary says 3,637 / 13
        for state in ("docked", "in_space", None):
            data = specs(state=state, cap=6810, used=1466, equipped=1453, hold=13)
            stats = sl.extract_stats(data, None, [cargo_summary()])
            self.assertEqual((stats["hull_capacity"], stats["hull_equipped"], stats["hull_hold"], stats["hull_free"]),
                             (6810.0, 3637.0, 13.0, 3160.0), state)

    def test_docked_specs_hull_numbers_are_not_trusted_without_a_summary(self):
        stats = sl.extract_stats(specs(state="docked", used=1466, equipped=1453, hold=13))
        self.assertEqual(stats["hull_capacity"], 667.0)
        for key in ("hull_equipped", "hull_hold", "hull_free"):
            self.assertNotIn(key, stats)

    def test_in_space_specs_give_the_free_hull_space_without_a_summary(self):
        stats = sl.extract_stats(specs(state="in_space", cap=6810, used=3650))
        self.assertEqual((stats["hull_capacity"], stats["hull_free"]), (6810.0, 3160.0))
        self.assertNotIn("hull_equipped", stats)

    def test_specs_without_a_state_are_trusted_as_before(self):
        stats = sl.extract_stats(specs())
        self.assertEqual((stats["hull_equipped"], stats["hull_hold"], stats["hull_free"]), (331.0, 132.0, 204.0))

    def test_the_hold_is_worked_out_when_the_summary_does_not_give_it(self):
        row = cargo_summary()
        del row["hold_cargo_used"]
        stats = sl.extract_stats(None, None, [row])
        self.assertEqual((stats["hull_hold"], stats["hull_equipped"], stats["hull_free"]), (13.0, 3637.0, 3160.0))

    def test_a_partial_summary_gives_only_what_it_has(self):
        stats = sl.extract_stats(None, None, [{"item_class": "cargo_summary", "cargo_capacity": 100}])
        self.assertEqual(stats, {"hull_capacity": 100.0})
        self.assertEqual(sl.extract_stats(None, None, [{"item_class": "cargo_item"}]), {})
        self.assertEqual(sl.extract_cargo_summary(None), {})
        self.assertEqual(sl.extract_cargo_summary([{"item_class": "cargo_summary", "cargo_used": 5}])["cargo_used"], 5)

    def test_effective_mass_is_a_stat(self):
        self.assertEqual(sl.extract_stats(specs(effective_mass=2664514.0))["effective_mass"], 2664514.0)

    def test_hull_free_needs_both_numbers(self):
        data = specs()
        del data["ship"]["cargo_used"]
        self.assertNotIn("hull_free", sl.extract_stats(data))


class CaptureTests(unittest.TestCase):
    def test_a_layout_has_every_part_and_is_plain_json(self):
        layout = fit()
        self.assertEqual(layout["version"], sl.SCHEMA_VERSION)
        self.assertEqual((layout["name"], layout["saved_at"], layout["saved_by"]), ("hunter stealth", 1000.0, "mk"))
        self.assertEqual(layout["ship"]["ship_type"], "panstar")
        self.assertEqual(len(layout["weapons"]), 2)
        self.assertEqual([e["slot"] for e in layout["equipment"]], ["energy", "shield", "engine"])
        self.assertEqual(len(layout["plugins"]), 2)
        self.assertEqual(layout["turrets"][0]["weapon_name"], "King's Thumper")
        self.assertTrue(layout["turrets_enabled"])
        self.assertEqual(json.loads(json.dumps(layout)), layout)

    def test_the_state_the_specs_were_read_in_is_remembered(self):
        self.assertEqual(sl.capture([], None, None, specs(state="docked"), "x")["specs_state"], "docked")
        self.assertEqual(sl.capture([], None, None, specs(state="in_space"), "x")["specs_state"], "in_space")
        self.assertEqual(sl.capture([], None, None, specs(), "x")["specs_state"], "")
        self.assertEqual(sl.capture([], None, None, None, "x")["specs_state"], "")

    def test_the_id_and_name(self):
        layout = fit(name="  Hunter   STEALTH!! ")
        self.assertEqual(layout["name"], "Hunter STEALTH!!")
        self.assertEqual(layout["id"], "1000000-hunter-stealth")
        self.assertEqual(sl.make_id("???", 5.0), "5000-layout")
        self.assertEqual(sl.capture([], None, None, None, "x", layout_id="mine")["id"], "mine")

    def test_names_are_cleaned_and_cut(self):
        self.assertEqual(sl.clean_name(" a \t b\n c "), "a b c")
        self.assertEqual(len(sl.clean_name("x" * 100)), sl.MAX_NAME_LEN)
        self.assertEqual(sl.clean_name(None), "")

    def test_a_ship_with_nothing_known_still_captures(self):
        layout = sl.capture(None, None, None, None, "empty", now=1.0)
        self.assertEqual((layout["weapons"], layout["equipment"], layout["plugins"], layout["turrets"],
                          layout["cargo"], layout["stats"], layout["ship"]), ([], [], [], [], [], {}, {}))


class CompareTests(unittest.TestCase):
    def sections(self, diff):
        return {s.key: s for s in diff.sections}

    def test_identical_fits_have_no_differences(self):
        diff = sl.compare(fit(), fit(name="now"))
        self.assertEqual(diff.differences, 0)
        self.assertEqual(diff.stat_changes, 0)
        self.assertTrue(diff.same_ship)
        self.assertTrue(all(row.status == sl.STATUS_SAME for s in diff.sections for row in s.rows))

    def test_a_swapped_weapon_a_missing_one_and_an_extra_one(self):
        saved = fit(inventory=[weapon(0), weapon(1), weapon(2, "kt", "King's Thumper")])
        current = fit(inventory=[weapon(0, "x", "Xenon Gun"), weapon(1), weapon(3, "y", "Why Gun")])
        rows = {r.label: r for r in self.sections(sl.compare(saved, current))["weapons"].rows}
        self.assertEqual(rows["Weapon 1"].status, sl.STATUS_CHANGED)
        self.assertEqual((rows["Weapon 1"].saved, rows["Weapon 1"].current), ("Patriot Beam", "Xenon Gun"))
        self.assertEqual(rows["Weapon 2"].status, sl.STATUS_SAME)
        self.assertEqual((rows["Weapon 3"].status, rows["Weapon 3"].current), (sl.STATUS_MISSING, "(empty)"))
        self.assertEqual((rows["Weapon 4"].status, rows["Weapon 4"].saved), (sl.STATUS_EXTRA, "(empty)"))

    def test_the_same_weapons_in_another_order_say_so(self):
        saved = fit(inventory=[weapon(0, "a", "A"), weapon(1, "b", "B")])
        current = fit(inventory=[weapon(0, "b", "B"), weapon(1, "a", "A")])
        section = self.sections(sl.compare(saved, current))["weapons"]
        self.assertEqual(section.differences, 2)
        self.assertEqual(section.rows[-1].status, sl.STATUS_NOTE)
        self.assertIn("different slots", section.rows[-1].label)
        other = fit(inventory=[weapon(0, "b", "B"), weapon(1, "c", "C")])
        self.assertNotIn(sl.STATUS_NOTE, [r.status for r in self.sections(sl.compare(saved, other))["weapons"].rows])

    def test_equipment_changed_missing_and_extra(self):
        saved = fit(inventory=[gear("engine", "e1", "Old Engine"), gear("sensor", "s1", "Sensor"),
                               gear("shield", "sh", "Shield")])
        current = fit(inventory=[gear("engine", "e2", "New Engine"), gear("cloak", "c1", "Cloak"),
                                 gear("shield", "sh", "Shield")])
        rows = {r.label: r for r in self.sections(sl.compare(saved, current))["equipment"].rows}
        self.assertEqual((rows["Engine"].status, rows["Engine"].saved, rows["Engine"].current),
                         (sl.STATUS_CHANGED, "Old Engine", "New Engine"))
        self.assertEqual((rows["Sensor"].status, rows["Sensor"].current), (sl.STATUS_MISSING, "(nothing fitted)"))
        self.assertEqual((rows["Cloak"].status, rows["Cloak"].saved), (sl.STATUS_EXTRA, "(nothing fitted)"))
        self.assertEqual(rows["Shield"].status, sl.STATUS_SAME)

    def test_multi_fit_families_compare_by_type_and_count(self):
        saved = fit(inventory=[gear("solar_panel_0", "a", "Panel A", "solar_panel"),
                               gear("solar_panel_1", "a", "Panel A", "solar_panel"),
                               gear("solar_panel_2", "b", "Panel B", "solar_panel")])
        current = fit(inventory=[gear("solar_panel_0", "a", "Panel A", "solar_panel"),
                                 gear("solar_panel_1", "c", "Panel C", "solar_panel")])
        rows = {(r.status, r.saved, r.current) for r in self.sections(sl.compare(saved, current))["equipment"].rows}
        self.assertEqual(rows, {(sl.STATUS_CHANGED, "Panel A x2", "Panel A"),
                                (sl.STATUS_MISSING, "Panel B", "(none)"),
                                (sl.STATUS_EXTRA, "(none)", "Panel C")})

    def test_plugins_compare_by_type_ignoring_slot_order(self):
        saved = fit(inventory=[plugin(0, "a", "A"), plugin(1, "b", "B"), plugin(2, "a", "A")])
        same_other_order = fit(inventory=[plugin(0, "b", "B"), plugin(1, "a", "A"), plugin(2, "a", "A")])
        self.assertEqual(sl.compare(saved, same_other_order).differences, 0)
        current = fit(inventory=[plugin(0, "a", "A"), plugin(1, "c", "C")])
        rows = {(r.status, r.saved, r.current) for r in self.sections(sl.compare(saved, current))["plugins"].rows}
        self.assertEqual(rows, {(sl.STATUS_CHANGED, "A x2", "A"), (sl.STATUS_MISSING, "B", "(none)"),
                                (sl.STATUS_EXTRA, "(none)", "C")})

    def turrets(self, *mounts):
        return {"turrets_enabled": True, "hardpoints": list(mounts)}

    def test_turrets_compare_weapon_and_controllers_per_mount(self):
        broad, bomb = ("c_broad", "Broadside"), ("c_bomb", "Bombard")
        saved = fit(turret_state=self.turrets(hardpoint(0, "kt", "King's Thumper", [broad, broad]),
                                              hardpoint(1, "pb", "Patriot Beam", [bomb]),
                                              hardpoint(2, "pb", "Patriot Beam", [broad, bomb]),
                                              hardpoint(3, "pb", "Patriot Beam")))
        current = fit(turret_state=self.turrets(hardpoint(0, "kt", "King's Thumper", [broad, broad]),
                                                hardpoint(1, "pb", "Patriot Beam", [broad]),
                                                hardpoint(2, "pb", "Patriot Beam", [bomb, broad]),
                                                hardpoint(4, "wl", "Wisplight")))
        rows = {r.label: r for r in self.sections(sl.compare(saved, current))["turrets"].rows}
        self.assertEqual(rows["Turret 1"].status, sl.STATUS_SAME)
        self.assertEqual((rows["Turret 2"].status, rows["Turret 2"].saved, rows["Turret 2"].current),
                         (sl.STATUS_CHANGED, "Patriot Beam  [Bombard]", "Patriot Beam  [Broadside]"))
        self.assertEqual(rows["Turret 3"].status, sl.STATUS_SAME)             # controller order means nothing
        self.assertEqual((rows["Turret 4"].status, rows["Turret 4"].current), (sl.STATUS_MISSING, "(empty)"))
        self.assertEqual((rows["Turret 5"].status, rows["Turret 5"].saved), (sl.STATUS_EXTRA, "(empty)"))
        self.assertEqual(rows["Turret 1"].saved, "King's Thumper  [Broadside x2]")

    def test_controller_names_are_shortened_in_the_row_but_compared_by_id(self):
        full = ("c_broad", "Broadside Turret Controller")
        other = ("c_broad2", "Broadside Turret Controller")                # another id with the same shown name
        saved = fit(turret_state=self.turrets(hardpoint(0, "kt", "King's Thumper", [full, full])))
        current = fit(turret_state=self.turrets(hardpoint(0, "kt", "King's Thumper", [full, other])))
        row = self.sections(sl.compare(saved, current))["turrets"].rows[0]
        self.assertEqual(row.saved, "King's Thumper  [Broadside x2]")
        self.assertEqual(row.status, sl.STATUS_CHANGED)

    def test_short_controller_names(self):
        self.assertEqual(sl.short_controller_name("Broadside Turret Controller"), "Broadside")
        self.assertEqual(sl.short_controller_name("Solstice controller"), "Solstice")
        self.assertEqual(sl.short_controller_name("Controller"), "Controller")
        self.assertEqual(sl.short_controller_name("Plain"), "Plain")
        self.assertEqual(sl.short_controller_name(None), "")

    def test_a_turret_with_a_different_weapon_but_the_same_controllers_differs(self):
        broad = ("c_broad", "Broadside")
        saved = fit(turret_state=self.turrets(hardpoint(0, "kt", "King's Thumper", [broad])))
        current = fit(turret_state=self.turrets(hardpoint(0, "pb", "Patriot Beam", [broad])))
        self.assertEqual(self.sections(sl.compare(saved, current))["turrets"].differences, 1)

    def test_cargo_of_the_same_type_in_two_categories_is_two_items(self):
        saved = fit(inventory=[cargo_item("kt", "KT weapon", 1, "weapon"), cargo_item("kt", "KT plugin", 1, "plugin")])
        current = fit(inventory=[cargo_item("kt", "KT weapon", 1, "weapon")])
        rows = self.sections(sl.compare(saved, current))["cargo"].rows
        self.assertEqual(sorted((r.status, r.saved) for r in rows),
                         [(sl.STATUS_MISSING, "KT plugin"), (sl.STATUS_SAME, "KT weapon")])

    def test_cargo_compares_quantities(self):
        saved = fit(inventory=[cargo_item("a", "Item A", 3), cargo_item("b", "Item B", 1), cargo_item("c", "Item C")])
        current = fit(inventory=[cargo_item("a", "Item A", 1), cargo_item("b", "Item B", 1), cargo_item("d", "Item D")])
        rows = {(r.status, r.saved, r.current) for r in self.sections(sl.compare(saved, current))["cargo"].rows}
        self.assertEqual(rows, {(sl.STATUS_CHANGED, "Item A x3", "Item A"), (sl.STATUS_SAME, "Item B", "Item B"),
                                (sl.STATUS_MISSING, "Item C", "(none)"), (sl.STATUS_EXTRA, "(none)", "Item D")})

    def test_same_ship_different_ship_and_unknown(self):
        saved = fit()
        self.assertTrue(sl.compare(saved, fit()).same_ship)
        other = fit(specs=specs("oakheart", "Oakheart"))
        diff = sl.compare(saved, other)
        self.assertFalse(diff.same_ship)
        self.assertEqual((diff.saved_ship["display_name"], diff.current_ship["display_name"]), ("Panstar", "Oakheart"))
        self.assertIsNone(sl.compare(saved, fit(specs=None)).same_ship)

    def test_section_helpers(self):
        section = sl.Section("x", "X", [sl.Row(sl.STATUS_SAME, "a"), sl.Row(sl.STATUS_CHANGED, "b"),
                                        sl.Row(sl.STATUS_NOTE, "n"), sl.Row(sl.STATUS_MISSING, "c"),
                                        sl.Row(sl.STATUS_EXTRA, "d")])
        self.assertEqual((section.differences, section.matching), (3, 1))
        self.assertEqual([r.label for r in section.visible_rows(False)], ["b", "n", "c", "d"])
        self.assertEqual(len(section.visible_rows(True)), 5)

    def test_differences_add_up_across_sections(self):
        saved = fit()
        current = fit(inventory=[weapon(0, "x", "X"), plugin(0, "q", "Q")])
        diff = sl.compare(saved, current)
        self.assertEqual(diff.differences, sum(s.differences for s in diff.sections))
        self.assertGreater(diff.differences, 3)


class StatRowTests(unittest.TestCase):
    def row(self, key, saved, current):
        definition = next(d for d in sl.STAT_DEFS if d[0] == key)
        return sl.StatRow(key, definition[1], definition[2], definition[3], definition[4], saved, current)

    def test_higher_is_better_for_damage_lower_for_visibility(self):
        self.assertEqual(self.row("total_dps", 1000.0, 1500.0).verdict, "better")
        self.assertEqual(self.row("total_dps", 1000.0, 900.0).verdict, "worse")
        self.assertEqual(self.row("visibility", 5000.0, 400.0).verdict, "better")
        self.assertEqual(self.row("visibility", 400.0, 5000.0).verdict, "worse")

    def test_a_number_without_a_preferred_direction_is_just_changed(self):
        self.assertEqual(self.row("mass", 73000.0, 80000.0).verdict, "changed")
        self.assertEqual(self.row("hull_hold", 10.0, 20.0).verdict, "changed")

    def test_a_tiny_absolute_change_is_noise_even_when_it_is_large_next_to_the_number(self):
        self.assertEqual(self.row("resist_kinetic", 0.14, 0.1403).verdict, "same")
        self.assertEqual(self.row("max_speed", 1.0, 1.04).verdict, "same")
        self.assertEqual(self.row("max_speed", 1.0, 1.1).verdict, "better")

    def test_more_free_hull_space_is_better(self):
        self.assertEqual(self.row("hull_free", 100.0, 200.0).verdict, "better")
        self.assertEqual(self.row("hull_free", 200.0, 100.0).verdict, "worse")
        self.assertEqual(self.row("hull_capacity", 600.0, 700.0).verdict, "better")

    def test_noise_is_not_a_change(self):
        self.assertEqual(self.row("total_dps", 16848.9, 16850.0).verdict, "same")
        self.assertEqual(self.row("max_speed", 4643.5, 4643.52).verdict, "same")
        self.assertEqual(self.row("resist_kinetic", 0.14, 0.14).verdict, "same")
        self.assertEqual(self.row("resist_kinetic", 0.14, 0.15).verdict, "better")
        self.assertEqual(self.row("visibility", 82.2, 117.9).verdict, "worse")

    def test_a_missing_side_is_unknown(self):
        row = self.row("total_dps", 1000.0, None)
        self.assertEqual((row.verdict, row.delta, row.delta_text, row.current_text), ("unknown", None, "", "-"))
        self.assertEqual(self.row("total_dps", None, None).verdict, "unknown")

    def test_texts(self):
        row = self.row("total_dps", 131668.4, 16848.9)
        self.assertEqual((row.saved_text, row.current_text, row.delta_text), ("131,668", "16,849", "-114,820"))
        row = self.row("resist_kinetic", 0.14, 0.205)
        self.assertEqual((row.saved_text, row.current_text, row.delta_text), ("14.0%", "20.5%", "+6.5%"))
        row = self.row("max_speed", 4643.5, 4700.25)
        self.assertEqual((row.saved_text, row.current_text, row.delta_text), ("4,643.5", "4,700.2", "+56.8"))
        self.assertEqual(self.row("total_dps", 5.0, 5.0).delta_text, "")

    def test_the_stats_list_has_only_what_one_side_knows_in_the_groups_order(self):
        saved = fit(specs=specs(visibility=100.0))
        current = fit(specs=None, turret_state=None)
        keys = [r.key for r in sl.compare(saved, current).stats]
        self.assertIn("visibility", keys)
        self.assertEqual(keys, [k for k in sl.STAT_KEYS if k in keys])
        groups = []
        for row in sl.compare(saved, fit()).stats:
            if not groups or groups[-1] != row.group:
                groups.append(row.group)
        self.assertEqual(groups, ["Damage", "Shield & energy", "Resistance", "Speed", "Hull & visibility"])

    def test_every_stat_key_is_unique_and_formatted(self):
        self.assertEqual(len(set(sl.STAT_KEYS)), len(sl.STAT_KEYS))
        for _key, _label, kind, better, _group in sl.STAT_DEFS:
            self.assertIn(kind, ("int", "float1", "pct"))
            self.assertIn(better, ("up", "down", None))

    def test_the_stat_the_user_asked_for_are_there(self):
        for key in ("total_dps", "turret_dps", "resist_kinetic", "resist_energy", "max_speed", "hull_capacity",
                    "hull_free", "visibility"):
            self.assertIn(key, sl.STAT_KEYS)


class StateTests(unittest.TestCase):
    """The user's real case: a Forest Warden saved in space and looked at docked."""

    def layouts(self, saved_state="in_space", current_state="docked"):
        summary = cargo_summary()
        saved = fit(inventory=[summary], specs=specs("ForestWarden", "Forest Warden", state=saved_state,
                                                     cap=6810, used=3650, effective_mass=3051514.0, accel=413.8,
                                                     turn=128.5))
        current = fit(inventory=[summary], specs=specs("ForestWarden", "Forest Warden", state=current_state,
                                                       cap=6810, used=1466, equipped=1453, hold=13,
                                                       effective_mass=2664514.0, accel=474.0, turn=147.1))
        return saved, current

    def rows(self, diff):
        return {row.key: row for row in diff.stats}

    def test_the_hull_rows_agree_in_both_states(self):
        diff = sl.compare(*self.layouts())
        rows = self.rows(diff)
        for key in ("hull_capacity", "hull_equipped", "hull_hold", "hull_free"):
            self.assertEqual(rows[key].verdict, "same", key)
        self.assertEqual((rows["hull_equipped"].saved_text, rows["hull_hold"].saved_text,
                          rows["hull_free"].current_text), ("3,637", "13", "3,160"))

    def test_the_speed_readings_of_two_states_are_not_judged(self):
        diff = sl.compare(*self.layouts())
        rows = self.rows(diff)
        for key in ("accel", "turn_rate", "effective_mass"):
            self.assertFalse(rows[key].comparable, key)
            self.assertEqual(rows[key].verdict, "changed", key)          # a plain change, never green or red
        self.assertTrue(rows["max_speed"].comparable)
        self.assertEqual(rows["accel"].delta_text, "+60")

    def test_mixed_states_alone_make_no_stat_changes_and_no_differences(self):
        diff = sl.compare(*self.layouts())
        self.assertEqual((diff.differences, diff.stat_changes), (0, 0))
        self.assertTrue(diff.mixed_states)
        self.assertEqual(len(diff.state_note), 4)
        self.assertIn("in space when saved", diff.state_note[0])
        self.assertIn("docked now", diff.state_note[0])
        self.assertTrue(all(len(line) <= 46 for line in diff.state_note))      # one short line each, never wrapped

    def test_a_real_difference_still_counts_next_to_mixed_states(self):
        saved, current = self.layouts()
        current["stats"]["shield_bank"] = saved["stats"]["shield_bank"] - 1000
        diff = sl.compare(saved, current)
        self.assertEqual(diff.stat_changes, 1)
        self.assertEqual(self.rows(diff)["shield_bank"].verdict, "worse")

    def test_the_same_state_compares_the_speed_normally(self):
        for state in ("docked", "in_space"):
            diff = sl.compare(*self.layouts(state, state))
            self.assertFalse(diff.mixed_states)
            self.assertEqual(diff.state_note, [])
            rows = self.rows(diff)
            self.assertTrue(rows["accel"].comparable)
            self.assertEqual(rows["accel"].verdict, "better")
            self.assertEqual(rows["effective_mass"].verdict, "changed")        # neutral by nature
            self.assertEqual(diff.stat_changes, 3)

    def test_an_unknown_state_is_not_treated_as_a_mismatch(self):
        saved, current = self.layouts("", "docked")
        diff = sl.compare(saved, current)
        self.assertFalse(diff.mixed_states)
        self.assertTrue(all(row.comparable for row in diff.stats))
        saved, current = self.layouts("in_space", "")
        self.assertFalse(sl.compare(saved, current).mixed_states)

    def test_no_note_when_nothing_that_depends_on_mass_differs(self):
        saved, current = self.layouts()
        current["stats"].update(accel=saved["stats"]["accel"], turn_rate=saved["stats"]["turn_rate"],
                                effective_mass=saved["stats"]["effective_mass"])
        diff = sl.compare(saved, current)
        self.assertTrue(diff.mixed_states)
        self.assertEqual(diff.state_note, [])

    def test_a_non_comparable_reading_that_is_missing_stays_unknown_and_equal_stays_same(self):
        row = sl.StatRow("accel", "Acceleration", "int", "up", "Speed", None, 5.0, comparable=False)
        self.assertEqual(row.verdict, "unknown")
        row = sl.StatRow("accel", "Acceleration", "int", "up", "Speed", 5.0, 5.0, comparable=False)
        self.assertEqual(row.verdict, "same")

    def test_the_states_have_readable_names(self):
        self.assertEqual(sl.STATE_TEXT, {"docked": "docked", "in_space": "in space"})
        self.assertEqual(sl.MASS_DEPENDENT, ("accel", "turn_rate", "effective_mass"))


class SummaryTests(unittest.TestCase):
    def test_the_list_line(self):
        self.assertEqual(sl.summary_line(fit()), "Panstar  -  37,849 dps  -  vis 10,000")
        self.assertEqual(sl.summary_line({}), "Unknown ship")
        self.assertEqual(sl.summary_line({"ship": {"display_name": "Oakheart"}, "stats": {"visibility": 82.2}}),
                         "Oakheart  -  vis 82")

    def test_format_helpers(self):
        self.assertEqual(sl.format_stat("int", 1234.6), "1,235")
        self.assertEqual(sl.format_stat("float1", 12.25), "12.2")
        self.assertEqual(sl.format_stat("pct", 0.256), "25.6%")
        self.assertEqual(sl.format_stat("int", None), "-")
        self.assertEqual(sl.format_stat("int", "x"), "-")
        self.assertEqual(sl.format_delta("int", 5), "+5")
        self.assertEqual(sl.format_delta("pct", -0.05), "-5.0%")


class FilesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self._tmp.name, "ship_layouts")
        self.addCleanup(self._tmp.cleanup)

    def test_a_written_layout_reads_back_and_is_found_by_its_id(self):
        layout = fit()
        sl.write_layout(self.dir, layout)
        self.assertEqual(sl.read_all(self.dir), [layout])
        self.assertEqual([f for f in os.listdir(self.dir)], ["1000000-hunter-stealth.json"])

    def test_newest_first(self):
        for when, name in ((1.0, "old"), (3.0, "newest"), (2.0, "middle")):
            sl.write_layout(self.dir, fit(name=name, now=when))
        self.assertEqual([d["name"] for d in sl.read_all(self.dir)], ["newest", "middle", "old"])

    def test_junk_in_the_folder_is_skipped(self):
        sl.write_layout(self.dir, fit())
        for name, text in (("broken.json", "{not json"), ("list.json", "[1, 2]"), ("noid.json", '{"name": "x"}'),
                           ("notes.txt", "hello"), ("odd.json", '{"id": 5, "name": "x", "saved_at": 1}')):
            with open(os.path.join(self.dir, name), "w", encoding="utf-8") as handle:
                handle.write(text)
        with self.assertLogs("ship_layouts", level="WARNING"):
            layouts = sl.read_all(self.dir)
        self.assertEqual([d["name"] for d in layouts], ["hunter stealth"])

    def test_only_json_files_are_layouts(self):
        sl.write_layout(self.dir, fit(name="real"))
        good = json.dumps(fit(name="in the wrong file"))
        for name in ("copy.txt", "1000000-real.json.4242.tmp", "layout.json.bak"):
            with open(os.path.join(self.dir, name), "w", encoding="utf-8") as handle:
                handle.write(good)
        self.assertEqual([d["name"] for d in sl.read_all(self.dir)], ["real"])

    def test_layouts_saved_before_the_state_was_kept_get_it_worked_out_when_read(self):
        old_in_space = {**fit(name="old flying"), "stats": {"hull_capacity": 6810.0, "hull_free": 3160.0}}
        old_docked = {**fit(name="old docked", now=2000.0),
                      "stats": {"hull_capacity": 6810.0, "hull_equipped": 1453.0, "hull_hold": 13.0}}
        old_unknown = {**fit(name="old unknown", now=3000.0), "stats": {"total_dps": 5.0}}
        for layout in (old_in_space, old_docked, old_unknown):
            del layout["specs_state"]
            sl.write_layout(self.dir, layout)
        read = {d["name"]: d for d in sl.read_all(self.dir)}
        self.assertEqual((read["old flying"]["specs_state"], read["old docked"]["specs_state"],
                          read["old unknown"]["specs_state"]), ("in_space", "docked", ""))
        with open(sl._file_for(self.dir, old_in_space["id"]), encoding="utf-8") as handle:
            self.assertNotIn("specs_state", json.load(handle))              # the file itself is untouched

    def test_a_hold_figure_alone_marks_an_old_layout_as_docked(self):
        old = {**fit(), "stats": {"hull_hold": 13.0}}
        del old["specs_state"]
        self.assertEqual(sl.migrate(old)["specs_state"], "docked")

    def test_a_layout_that_has_its_state_is_not_second_guessed(self):
        layout = {**fit(), "specs_state": "docked", "stats": {"hull_capacity": 1.0}}
        self.assertIs(sl.migrate(layout), layout)
        self.assertEqual(sl.migrate({**layout, "specs_state": ""})["specs_state"], "")

    def test_a_missing_folder_is_an_empty_list(self):
        self.assertEqual(sl.read_all(os.path.join(self.dir, "nope")), [])

    def test_no_temp_file_is_left_behind_and_a_rewrite_replaces(self):
        sl.write_layout(self.dir, fit(name="one"))
        sl.write_layout(self.dir, {**fit(name="one"), "name": "renamed"})
        self.assertEqual(os.listdir(self.dir), ["1000000-one.json"])
        self.assertEqual([d["name"] for d in sl.read_all(self.dir)], ["renamed"])

    def test_ids_are_safe_file_names(self):
        path = sl._file_for(self.dir, "../../evil/../name")
        self.assertEqual(os.path.dirname(path), self.dir)
        self.assertNotIn("/", os.path.basename(path))

    def test_the_default_folder_is_under_the_shared_scan_data(self):
        scan_paths.set_root_for_tests(self._tmp.name)
        self.addCleanup(scan_paths.set_root_for_tests, None)
        self.assertEqual(sl.default_directory(), os.path.join(self._tmp.name, "ship_layouts"))

    def test_delete_of_a_missing_file_is_fine(self):
        sl.delete_layout(self.dir, "nothing-here")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self._tmp.name, "layouts")
        self.addCleanup(self._tmp.cleanup)
        self.queued = []
        self.store = sl.LayoutStore(self.dir, submit=lambda fn, *args: self.queued.append((fn, args)))

    def drain(self):
        while self.queued:
            fn, args = self.queued.pop(0)
            fn(*args)

    def test_nothing_touches_the_disk_until_the_writer_runs(self):
        self.store.refresh()
        self.store.save(fit())
        self.assertFalse(os.path.exists(self.dir))
        self.assertEqual(len(self.queued), 2)
        self.drain()
        self.assertEqual(len(os.listdir(self.dir)), 1)

    def test_a_save_is_in_the_list_at_once_and_on_disk_later(self):
        layout = fit()
        self.store.save(layout)
        self.assertEqual(self.store.layouts, (layout,))
        self.drain()
        self.assertEqual(sl.read_all(self.dir), [layout])
        self.assertTrue(self.store.loaded)

    def test_saving_the_same_id_replaces_it(self):
        self.store.save(fit(name="a"))
        self.store.save({**fit(name="a"), "stats": {"total_dps": 1.0}})
        self.assertEqual(len(self.store.layouts), 1)
        self.assertEqual(self.store.layouts[0]["stats"], {"total_dps": 1.0})

    def test_the_list_is_newest_first(self):
        self.store.save(fit(name="a", now=1.0))
        self.store.save(fit(name="b", now=5.0))
        self.store.save(fit(name="c", now=3.0))
        self.assertEqual([d["name"] for d in self.store.layouts], ["b", "c", "a"])

    def test_refresh_picks_up_what_another_client_saved(self):
        sl.write_layout(self.dir, fit(name="from elsewhere"))
        self.assertEqual(self.store.layouts, ())
        self.store.refresh()
        self.drain()
        self.assertEqual([d["name"] for d in self.store.layouts], ["from elsewhere"])

    def test_delete_removes_it_from_the_list_and_the_folder(self):
        layout = fit()
        self.store.save(layout)
        self.drain()
        self.store.delete(layout["id"])
        self.assertEqual(self.store.layouts, ())
        self.drain()
        self.assertEqual(os.listdir(self.dir), [])
        self.assertIsNone(self.store.get(layout["id"]))

    def test_a_failed_write_is_reported_not_raised(self):
        with mock.patch.object(sl, "write_layout", side_effect=OSError("disk full")):
            self.store.save(fit())
            with self.assertLogs("ship_layouts", level="ERROR"):
                self.drain()
        self.assertIn("disk full", self.store.error)

    def test_a_failed_delete_is_reported_not_raised(self):
        with mock.patch.object(sl, "delete_layout", side_effect=OSError("locked")):
            self.store.delete("x")
            with self.assertLogs("ship_layouts", level="ERROR"):
                self.drain()
        self.assertIn("locked", self.store.error)

    def test_a_scan_that_fails_still_marks_the_store_loaded(self):
        with mock.patch.object(sl, "read_all", side_effect=RuntimeError("boom")):
            self.store.refresh()
            with self.assertLogs("ship_layouts", level="ERROR"):
                self.drain()
        self.assertTrue(self.store.loaded)
        self.assertIn("boom", self.store.error)

    def test_a_good_scan_clears_the_error(self):
        self.store.error = "old"
        self.store.refresh()
        self.drain()
        self.assertIsNone(self.store.error)

    def test_unique_names(self):
        self.store.save(fit(name="Hunter", now=1.0))
        self.store.save(fit(name="Hunter (2)", now=2.0))
        self.assertEqual(self.store.unique_name("hunter"), "hunter (3)")
        self.assertEqual(self.store.unique_name("Other"), "Other")
        self.assertEqual(self.store.unique_name("  Hunter  "), "Hunter (3)")

    def test_a_long_name_keeps_its_suffix_within_the_limit(self):
        long_name = "x" * sl.MAX_NAME_LEN
        self.store.save(fit(name=long_name))
        unique = self.store.unique_name(long_name)
        self.assertEqual(len(unique), sl.MAX_NAME_LEN)
        self.assertTrue(unique.endswith(" (2)"))

    def step(self):
        """Run exactly one queued job on the 'writer thread'."""
        fn, args = self.queued.pop(0)
        fn(*args)

    def test_a_scan_queued_before_a_save_does_not_blink_the_new_layout_out(self):
        layout = fit()
        self.store.refresh()                      # the periodic refresh, already waiting for the writer
        self.store.save(layout)
        self.step()                               # it reads the folder before the file exists ...
        self.assertEqual(self.store.layouts, (layout,))      # ... and still the layout is in the list
        self.step()                               # the write
        self.assertEqual(self.store.layouts, (layout,))
        self.assertEqual(sl.read_all(self.dir), [layout])
        self.assertEqual(self.store._unsaved, {})

    def test_a_scan_queued_before_a_delete_does_not_bring_the_layout_back(self):
        layout = fit()
        self.store.save(layout)
        self.drain()
        self.store.refresh()
        self.store.delete(layout["id"])
        self.step()                               # a scan that still finds the file
        self.assertEqual(self.store.layouts, ())
        self.step()                               # the delete itself
        self.assertEqual(self.store.layouts, ())
        self.assertEqual(self.store._deleted, set())
        self.assertEqual(os.listdir(self.dir), [])

    def test_a_layout_that_could_not_be_written_stays_in_the_list(self):
        layout = fit()
        with mock.patch.object(sl, "write_layout", side_effect=OSError("disk full")):
            self.store.save(layout)
            with self.assertLogs("ship_layouts", level="ERROR"):
                self.drain()
        self.assertEqual(self.store.layouts, (layout,))
        self.assertIn(layout["id"], self.store._unsaved)
        self.assertIn("disk full", self.store.error)

    def test_a_layout_that_could_not_be_deleted_stays_gone_from_the_list(self):
        layout = fit()
        self.store.save(layout)
        self.drain()
        with mock.patch.object(sl, "delete_layout", side_effect=OSError("locked")):
            self.store.delete(layout["id"])
            with self.assertLogs("ship_layouts", level="ERROR"):
                self.drain()
        self.assertEqual(self.store.layouts, ())

    def test_saving_again_before_the_first_write_lands_keeps_the_newer_one(self):
        first = fit(name="a")
        second = {**first, "name": "a, edited"}
        self.store.save(first)
        self.store.save(second)
        self.step()                               # the first write
        self.assertEqual(self.store.layouts[0]["name"], "a, edited")
        self.step()                               # the second write
        self.assertEqual(self.store.layouts[0]["name"], "a, edited")
        self.assertEqual([d["name"] for d in sl.read_all(self.dir)], ["a, edited"])
        self.assertEqual(self.store._unsaved, {})

    def test_saving_a_layout_that_was_just_deleted_brings_it_back(self):
        layout = fit()
        self.store.delete(layout["id"])
        self.store.save(layout)
        self.assertEqual(self.store.layouts, (layout,))                 # at once, not only when the writer is done
        self.drain()
        self.assertEqual(self.store.layouts, (layout,))

    def test_deleting_a_layout_that_was_never_written_forgets_it(self):
        layout = fit()
        self.store.save(layout)
        self.store.delete(layout["id"])
        self.drain()
        self.assertEqual(self.store.layouts, ())
        self.assertEqual(os.listdir(self.dir), [])

    def test_the_default_store_writes_through_the_shared_writer_thread(self):
        import scan_writer
        store = sl.LayoutStore(self.dir)
        store.save(fit())
        scan_writer.flush()
        self.assertEqual(len(os.listdir(self.dir)), 1)


if __name__ == "__main__":
    unittest.main()

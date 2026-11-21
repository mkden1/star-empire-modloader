"""Tests for bars.py (the extra bars and the set an account has) and the extra bars' part of keybinds.py."""

from __future__ import annotations
import modloader

import json
import os
import tempfile
import unittest

import pygame

import bars
import keybinds as kb
from keybinds import Combo

pygame.init()

FIGHTER = {"item_class": "cargo_item", "item_category": "fighter", "item_type": "interceptor",
           "display_name": "Interceptor", "color_rgb": [120, 200, 255]}


def ids():
    counter = iter(range(1, 100))
    return lambda: f"b{next(counter)}"


def make_set(count=0):
    barset = bars.BarSet(id_factory=ids())
    for _ in range(count):
        barset.add()
    return barset


class BarSetTests(unittest.TestCase):
    def test_a_new_bar_is_empty_shown_and_has_the_first_free_key_set(self):
        barset = make_set()
        bar = barset.add()
        self.assertEqual(bar.slots, [None] * 10)
        self.assertTrue(bar.visible)
        self.assertEqual(bar.mods, ("shift",))
        self.assertEqual((bar.fx, bar.fy), (None, None))
        self.assertEqual(barset.add().mods, ("ctrl",))
        self.assertEqual(barset.add().mods, ("alt",))
        self.assertEqual(barset.add().mods, ("ctrl", "shift"))

    def test_bars_are_named_from_two_and_names_are_unique(self):
        barset = make_set()
        self.assertEqual(barset.add().name, "Bar 2")
        self.assertEqual(barset.add().name, "Bar 3")
        self.assertEqual(barset.add("Combat").name, "Combat")
        self.assertEqual(barset.add("combat").name, "combat (2)")

    def test_the_number_of_bars_is_capped(self):
        barset = make_set(bars.MAX_BARS)
        self.assertEqual(len(barset), 6)
        self.assertIsNone(barset.add())
        self.assertEqual(len({tuple(b.mods) for b in barset}), 6)                # every key set different

    def test_a_removed_bars_key_set_is_the_next_bar_s(self):
        barset = make_set(3)
        barset.remove("b2")
        self.assertEqual(barset.add().mods, ("ctrl",))
        self.assertEqual([b.mods for b in barset], [("shift",), ("alt",), ("ctrl",)])      # the others keep theirs

    def test_ids_never_repeat_within_a_set(self):
        counter = iter(["b1", "b1", "b1", "b2"])
        barset = bars.BarSet(id_factory=lambda: next(counter))
        self.assertEqual(barset.add().id, "b1")
        self.assertEqual(barset.add().id, "b2")

    def test_real_ids_are_short_and_file_safe(self):
        barset = bars.BarSet()
        bar = barset.add()
        self.assertRegex(bar.id, r"^b[0-9a-f]{6}$")
        self.assertNotEqual(bar.id, barset.add().id)

    def test_rename(self):
        barset = make_set(2)
        self.assertEqual(barset.rename("b1", "  Combat   bar "), "Combat bar")
        self.assertEqual(barset.rename("b2", "combat bar"), "combat bar (2)")
        self.assertEqual(barset.rename("b1", "Combat bar"), "Combat bar")           # its own name is not a clash
        self.assertIsNone(barset.rename("b1", "   "))
        self.assertIsNone(barset.rename("nope", "x"))
        self.assertEqual(len(barset.get("b1").name), len("Combat bar"))

    def test_names_are_capped(self):
        self.assertEqual(len(make_set().add("x" * 80).name), bars.MAX_NAME_LEN)

    def test_remove(self):
        barset = make_set(2)
        self.assertEqual(barset.remove("b1").id, "b1")
        self.assertIsNone(barset.remove("b1"))
        self.assertEqual([b.id for b in barset], ["b2"])

    def test_places_are_fractions_kept_between_zero_and_one(self):
        barset = make_set(1)
        barset.set_place("b1", 0.25, 0.5)
        self.assertEqual((barset.get("b1").fx, barset.get("b1").fy), (0.25, 0.5))
        barset.set_place("b1", -3, 9)
        self.assertEqual((barset.get("b1").fx, barset.get("b1").fy), (0.0, 1.0))
        barset.set_place("b1", None, None)
        self.assertEqual((barset.get("b1").fx, barset.get("b1").fy), (None, None))
        barset.set_place("b1", "left", True)
        self.assertEqual((barset.get("b1").fx, barset.get("b1").fy), (None, None))
        barset.set_place("b1", 0.4, None)                                          # half a place is no place
        self.assertEqual((barset.get("b1").fx, barset.get("b1").fy), (None, None))
        barset.set_place("b1", None, 0.4)
        self.assertEqual((barset.get("b1").fx, barset.get("b1").fy), (None, None))


class BindingTests(unittest.TestCase):
    def test_a_binding_is_kept_the_way_the_games_own_loader_keeps_it(self):
        self.assertEqual(bars.clean_binding(FIGHTER), FIGHTER)
        self.assertEqual(bars.clean_binding({"item_type": "x", "item_category": "y"}),
                         {"item_class": "cargo_item", "item_category": "y", "item_type": "x", "display_name": "x",
                          "color_rgb": [255, 180, 80]})

    def test_it_is_a_copy(self):
        binding = dict(FIGHTER, color_rgb=[1, 2, 3])
        cleaned = bars.clean_binding(binding)
        binding["color_rgb"].append(4)
        self.assertEqual(cleaned["color_rgb"], [1, 2, 3])

    def test_unusable_bindings_are_empty_slots(self):
        for junk in (None, "x", 5, [], {}, {"item_type": "x"}, {"item_category": "y"}, {"item_type": "", "item_category": "y"}):
            self.assertIsNone(bars.clean_binding(junk), junk)

    def test_a_bad_colour_falls_back(self):
        self.assertEqual(bars.clean_binding(dict(FIGHTER, color_rgb="red"))["color_rgb"], [255, 180, 80])


class JsonTests(unittest.TestCase):
    def test_round_trip(self):
        barset = make_set(2)
        barset.get("b1").slots[3] = dict(FIGHTER)
        barset.get("b1").visible = False
        barset.set_place("b2", 0.1, 0.9)
        again = bars.BarSet.from_json(json.loads(json.dumps(barset.to_json())), ids())
        self.assertEqual(again.to_json(), barset.to_json())
        self.assertEqual(again.get("b1").slots[3], FIGHTER)
        self.assertFalse(again.get("b1").visible)
        self.assertEqual((again.get("b2").fx, again.get("b2").fy), (0.1, 0.9))

    def test_it_is_not_a_view_of_the_saved_data(self):
        barset = make_set(1)
        barset.get("b1").slots[0] = dict(FIGHTER)
        data = barset.to_json()
        barset.get("b1").slots[0]["item_type"] = "changed"
        self.assertEqual(data["bars"][0]["slots"][0]["item_type"], "interceptor")

    def test_junk_gives_an_empty_set(self):
        for junk in (None, [], "x", 5, {}, {"bars": "no"}, {"bars": [1, "x", None]}):
            self.assertEqual(len(bars.BarSet.from_json(junk)), 0, junk)

    def test_a_bar_with_a_bad_or_repeated_id_is_skipped(self):
        data = {"bars": [{"id": "ok1"}, {"id": "bad id!"}, {"id": 5}, {"id": "ok1"}, {"id": ""}, {"id": "x" * 40}, {"id": "ok2"}]}
        self.assertEqual([b.id for b in bars.BarSet.from_json(data)], ["ok1", "ok2"])

    def test_missing_fields_get_defaults(self):
        (bar,) = bars.BarSet.from_json({"bars": [{"id": "b1"}]})
        self.assertEqual((bar.name, bar.mods, bar.fx, bar.fy, bar.visible), ("Bar", ("shift",), None, None, True))
        self.assertEqual(bar.slots, [None] * 10)

    def test_a_repeated_or_unknown_key_set_is_replaced_by_a_free_one(self):
        data = {"bars": [{"id": "a", "mods": ["shift"]}, {"id": "b", "mods": ["shift"]}, {"id": "c", "mods": ["hyper"]},
                         {"id": "d", "mods": "ctrl"}]}
        got = bars.BarSet.from_json(data)
        self.assertEqual([b.mods for b in got], [("shift",), ("ctrl",), ("alt",), ("ctrl", "shift")])

    def test_a_half_a_place_is_no_place(self):
        (bar,) = bars.BarSet.from_json({"bars": [{"id": "a", "fx": 0.5}]})
        self.assertEqual((bar.fx, bar.fy), (None, None))
        (bar,) = bars.BarSet.from_json({"bars": [{"id": "a", "fx": 7, "fy": -1}]})
        self.assertEqual((bar.fx, bar.fy), (1.0, 0.0))

    def test_visible_is_only_ever_turned_off_explicitly(self):
        got = bars.BarSet.from_json({"bars": [{"id": "a", "visible": False}, {"id": "b", "visible": "no"}, {"id": "c"}]})
        self.assertEqual([b.visible for b in got], [False, True, True])

    def test_the_slots_are_ten_whatever_the_file_says(self):
        data = {"bars": [{"id": "a", "slots": [dict(FIGHTER)] * 14}, {"id": "b", "slots": [dict(FIGHTER)]}, {"id": "c", "slots": 5}]}
        got = bars.BarSet.from_json(data)
        self.assertEqual([len(b.slots) for b in got], [10, 10, 10])
        self.assertEqual([b.filled for b in got], [10, 1, 0])

    def test_too_many_bars_are_cut_off(self):
        data = {"bars": [{"id": f"b{i}"} for i in range(20)]}
        self.assertEqual(len(bars.BarSet.from_json(data)), bars.MAX_BARS)

    def test_names_are_made_unique_and_readable(self):
        got = bars.BarSet.from_json({"bars": [{"id": "a", "name": "Same"}, {"id": "b", "name": "same"}, {"id": "c", "name": ""},
                                              {"id": "d", "name": 5}]})
        self.assertEqual([b.name for b in got], ["Same", "same (2)", "Bar", "5"])


class BarKeyTests(unittest.TestCase):
    def table(self, count=2, overrides=None):
        barset = make_set(count)
        return kb.Table.from_json(overrides or {}, list(barset)), barset

    def test_each_bar_adds_ten_slot_actions_with_its_own_default_keys(self):
        table, barset = self.table(2)
        self.assertEqual(len(table.catalog), len(kb.CATALOG) + 20)
        self.assertEqual(table.combo("slot.b1.1"), Combo(pygame.K_1, {"shift"}))
        self.assertEqual(table.combo("slot.b1.10"), Combo(pygame.K_0, {"shift"}))
        self.assertEqual(table.combo("slot.b2.3"), Combo(pygame.K_3, {"ctrl"}))
        action = table.by_id["slot.b2.3"]
        self.assertEqual((action.kind, action.bar, action.arg, action.group, action.label), ("barslot", "b2", 2, "Bar 3", "Slot 3"))
        self.assertTrue(table.has_bars)
        self.assertTrue(table.is_default)

    def test_a_table_without_bars_is_the_base_table(self):
        table = kb.Table()
        self.assertEqual(table.catalog, kb.CATALOG)
        self.assertFalse(table.has_bars)

    def test_the_games_bar_and_the_extra_bars_do_not_clash_by_default(self):
        table, _ = self.table(6)
        defaults = [a.default for a in table.catalog if a.default is not None]
        self.assertEqual(len(defaults), len(set(defaults)))

    def test_a_key_press_finds_the_right_bar_by_its_modifiers(self):
        table, _ = self.table(3)
        self.assertEqual(table.resolve(pygame.K_1, frozenset()).action.id, "slot.main.1")
        self.assertEqual(table.resolve(pygame.K_1, {"shift"}).action.id, "slot.b1.1")
        self.assertEqual(table.resolve(pygame.K_1, {"ctrl"}).action.id, "slot.b2.1")
        self.assertEqual(table.resolve(pygame.K_1, {"alt"}).action.id, "slot.b3.1")
        self.assertEqual(table.resolve(pygame.K_1, {"ctrl", "shift"}).action.id, "slot.main.1")       # no such bar: the plain key

    def test_a_slot_key_can_be_changed_and_saved_with_the_rest(self):
        table, barset = self.table(1)
        table.set("slot.b1.1", Combo(pygame.K_F1))
        self.assertEqual(table.to_json(), {"slot.b1.1": {"key": "f1", "mods": []}})
        again = kb.Table.from_json(table.to_json(), list(barset))
        self.assertEqual(again.combo("slot.b1.1"), Combo(pygame.K_F1))

    def test_a_saved_change_for_a_bar_that_is_not_there_is_kept_for_the_file(self):
        table = kb.Table.from_json({"slot.b9.1": {"key": "f1", "mods": []}, "hotkey.dock": {"key": "b"}})
        self.assertEqual(table.combo("hotkey.dock"), Combo(pygame.K_b))
        self.assertNotIn("slot.b9.1", table.by_id)
        self.assertEqual(table.to_json(), {"slot.b9.1": {"key": "f1", "mods": []}, "hotkey.dock": {"key": "b", "mods": []}})
        table.set("hotkey.dock", None)
        self.assertIn("slot.b9.1", table.to_json())

    def test_the_bar_comes_back_with_its_saved_keys(self):
        table = kb.Table.from_json({"slot.b1.1": {"key": "f1", "mods": []}})
        with_bar = table.with_bars([make_set(1).get("b1")])
        self.assertEqual(with_bar.combo("slot.b1.1"), Combo(pygame.K_F1))
        self.assertEqual(with_bar.to_json(), {"slot.b1.1": {"key": "f1", "mods": []}})

    def test_the_bar_going_away_leaves_its_keys_in_the_saved_data_until_they_are_removed(self):
        table, barset = self.table(1, {"slot.b1.1": {"key": "f1", "mods": []}})
        gone = table.with_bars([])
        self.assertNotIn("slot.b1.1", gone.by_id)
        self.assertIn("slot.b1.1", gone.to_json())

    def test_reset_all_resets_the_bars_keys_too(self):
        table, barset = self.table(1, {"slot.b1.1": {"key": "f1", "mods": []}, "hotkey.dock": {"key": "b"}})
        table.reset_all()
        self.assertTrue(table.is_default)
        self.assertEqual(table.combo("slot.b1.1"), Combo(pygame.K_1, {"shift"}))

    def test_the_old_key_of_an_extra_bar_slot_that_moved_falls_back_to_the_games_bar(self):
        """Shift+1 is nobody's any more, and the plain 1 has always ignored Shift: it is the game's slot 1 again."""
        table, _ = self.table(1)
        table.set("slot.b1.1", Combo(pygame.K_F1))
        got = table.resolve(pygame.K_1, {"shift"})
        self.assertEqual(got.action.id, "slot.main.1")
        self.assertEqual(table.resolve(pygame.K_F1, frozenset()).action.id, "slot.b1.1")

    def test_a_combo_taken_from_another_bar_unbinds_it_there(self):
        table, _ = self.table(2)
        displaced = table.set("slot.b2.1", Combo(pygame.K_1, {"shift"}))
        self.assertEqual([a.id for a in displaced], ["slot.b1.1"])
        self.assertIsNone(table.combo("slot.b1.1"))


class FileEntryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "sub", "quick_bars.json")

    def test_an_entry_is_replaced_and_the_others_kept(self):
        kb.write_entry(self.path, "bars", "Alice", {"bars": [1]})
        kb.write_entry(self.path, "bars", "Bob", {"bars": [2]})
        kb.write_entry(self.path, "bars", "Alice", {"bars": [3]})
        self.assertEqual(kb.read_file(self.path)["bars"], {"Alice": {"bars": [3]}, "Bob": {"bars": [2]}})

    def test_other_sections_survive_an_entry_write(self):
        kb.write_section(self.path, "keys", {"hotkey.dock": None})
        kb.write_entry(self.path, "bars", "Alice", {"bars": []})
        data = kb.read_file(self.path)
        self.assertEqual(data["keys"], {"hotkey.dock": None})
        self.assertEqual(data["version"], 1)

    def test_a_section_that_is_not_a_dict_is_replaced(self):
        kb.write_section(self.path, "bars", ["junk"])
        kb.write_entry(self.path, "bars", "Alice", {"bars": []})
        self.assertEqual(kb.read_file(self.path)["bars"], {"Alice": {"bars": []}})

    def test_no_temp_file_is_left(self):
        kb.write_entry(self.path, "bars", "Alice", {})
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["quick_bars.json"])


if __name__ == "__main__":
    unittest.main()

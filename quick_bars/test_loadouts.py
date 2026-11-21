"""Tests for loadouts.py: what a loadout holds, how a setup is cleaned and compared, the saved shape, and the file store."""

from __future__ import annotations
import modloader

import copy
import json
import os
import tempfile
import unittest
from unittest import mock

import pygame

import bars
import keybinds as kb
import loadouts as lo

pygame.init()

FIGHTER = {"item_class": "cargo_item", "item_category": "fighter", "item_type": "interceptor", "display_name": "Interceptor",
           "color_rgb": [120, 200, 255]}
MISSILE = {"item_class": "cargo_item", "item_category": "missile", "item_type": "kinetic_missile", "display_name": "Kinetic Missile",
           "color_rgb": [255, 180, 80]}


def bar(bar_id, name, mods, slots=()):
    filled = [None] * 10
    for index, binding in slots:
        filled[index] = dict(binding)
    return {"id": bar_id, "name": name, "mods": list(mods), "fx": 0.25, "fy": 0.5, "visible": True, "slots": filled}


def setup(main=None, bar_list=None, keys=None):
    main = [None] * 20 if main is None else main
    return {"main": main, "bars": bar_list if bar_list is not None else [bar("b1", "Combat", ["shift"], [(0, FIGHTER)])],
            "keys": keys if keys is not None else {}}


def sync_submit(fn, *args):
    fn(*args)


class CleanSetupTests(unittest.TestCase):
    def test_a_clean_setup_comes_out_as_it_went_in(self):
        data = setup([dict(FIGHTER)] + [None] * 19, [bar("b1", "Combat", ["shift"], [(0, FIGHTER)])], {"slot.b1.1": {"key": "f1", "mods": []}})
        self.assertEqual(lo.clean_setup(data), data)

    def test_it_is_a_copy(self):
        data = setup(keys={"slot.b1.1": {"key": "f1", "mods": ["ctrl"]}})
        cleaned = lo.clean_setup(data)
        data["bars"][0]["slots"][0]["item_type"] = "changed"
        data["keys"]["slot.b1.1"]["mods"].append("alt")
        data["main"].append("junk")
        self.assertEqual(cleaned["bars"][0]["slots"][0]["item_type"], "interceptor")
        self.assertEqual(cleaned["keys"]["slot.b1.1"], {"key": "f1", "mods": ["ctrl"]})
        self.assertEqual(len(cleaned["main"]), 20)

    def test_the_game_bar_is_kept_as_the_game_keeps_it(self):
        cleaned = lo.clean_setup({"main": [{"item_type": "x", "item_category": "y"}, "junk", None, 5], "bars": [], "keys": {}})
        self.assertEqual(cleaned["main"][0], {"item_class": "cargo_item", "item_category": "y", "item_type": "x", "display_name": "x",
                                              "color_rgb": [255, 180, 80]})
        self.assertEqual(cleaned["main"][1:], [None, None, None])

    def test_a_game_bar_longer_than_any_game_has_is_cut(self):
        self.assertEqual(len(lo.clean_setup({"main": [dict(FIGHTER)] * 200})["main"]), lo.MAX_MAIN)

    def test_the_bars_are_cleaned_as_bars_are_loaded(self):
        cleaned = lo.clean_setup({"bars": [{"id": "b1", "name": "A"}, {"id": "bad id"}, 5, {"id": "b1"}], "main": [], "keys": {}})
        self.assertEqual([b["id"] for b in cleaned["bars"]], ["b1"])
        self.assertEqual(len(cleaned["bars"][0]["slots"]), 10)

    def test_only_keys_for_slots_of_the_setups_own_bars_are_kept(self):
        keys = {"slot.b1.3": {"key": "f3", "mods": []}, "slot.b1.10": None, "slot.b1.11": None, "slot.b1.0": None,
                "slot.b9.1": None, "slot.main.1": None, "hotkey.dock": {"key": "b", "mods": []}, "junk": None, 5: None,
                "slot.b1.2": "text", "slot.b1.4": {"mods": []}}
        cleaned = lo.clean_setup({"bars": [bar("b1", "A", ["shift"])], "keys": keys})
        self.assertEqual(sorted(cleaned["keys"]), ["slot.b1.10", "slot.b1.3"])

    def test_not_a_dict_is_no_setup(self):
        for junk in (None, [], "x", 5):
            self.assertIsNone(lo.clean_setup(junk))
        self.assertEqual(lo.clean_setup({}), {"main": [], "bars": [], "keys": {}})

    def test_two_setups_are_the_same_exactly_when_their_keys_are(self):
        a = setup()
        self.assertEqual(lo.setup_key(a), lo.setup_key(copy.deepcopy(a)))
        for change in (lambda s: s["main"].__setitem__(3, dict(MISSILE)),
                       lambda s: s["bars"][0]["slots"].__setitem__(1, dict(MISSILE)),
                       lambda s: s["bars"][0].__setitem__("fx", 0.9),
                       lambda s: s["bars"][0].__setitem__("visible", False),
                       lambda s: s["bars"][0].__setitem__("name", "Other"),
                       lambda s: s["keys"].__setitem__("slot.b1.1", {"key": "f1", "mods": []}),
                       lambda s: s["bars"].append(bar("b2", "Two", ["ctrl"]))):
            b = copy.deepcopy(a)
            change(b)
            self.assertNotEqual(lo.setup_key(a), lo.setup_key(b))

    def test_empty_slots_at_the_end_of_the_game_bar_do_not_make_a_difference(self):
        short = setup([dict(FIGHTER)])
        full = setup([dict(FIGHTER)] + [None] * 19)
        self.assertEqual(lo.setup_key(short), lo.setup_key(full))
        self.assertEqual(lo.setup_key(setup([])), lo.setup_key(setup([None] * 20)))
        self.assertNotEqual(lo.setup_key(setup([None, dict(FIGHTER)])), lo.setup_key(setup([dict(FIGHTER)])))     # a gap in front does
        self.assertNotEqual(lo.setup_key(short), lo.setup_key(setup([dict(FIGHTER)] + [None] * 18 + [dict(MISSILE)])))

    def test_the_key_does_not_depend_on_the_order_things_were_written_in(self):
        a = setup(keys={"slot.b1.1": {"key": "f1", "mods": []}, "slot.b1.2": None})
        b = setup(keys={"slot.b1.2": None, "slot.b1.1": {"mods": [], "key": "f1"}})
        self.assertEqual(lo.setup_key(a), lo.setup_key(b))


class LoadoutTests(unittest.TestCase):
    def test_a_new_loadout_has_an_id_a_name_a_time_and_the_setup(self):
        item = lo.new_loadout("  Warden   build ", setup(), when=1700000000.5)
        self.assertEqual(item["name"], "Warden build")
        self.assertTrue(item["id"].startswith("1700000000500-warden-build"))
        self.assertEqual(item["saved_at"], 1700000000.5)
        self.assertEqual(item["version"], 1)
        self.assertEqual(lo.setup_of(item), lo.clean_setup(setup()))

    def test_it_survives_json(self):
        item = lo.new_loadout("Warden", setup(keys={"slot.b1.1": None}), when=5.0)
        self.assertEqual(lo.normalize(json.loads(json.dumps(item))), item)

    def test_a_loadout_that_is_not_one_is_refused(self):
        good = lo.new_loadout("Warden", setup(), when=5.0)
        for mutate in (lambda d: d.pop("id"), lambda d: d.update(id=""), lambda d: d.update(id=5), lambda d: d.update(name="  "),
                       lambda d: d.update(name=7), lambda d: d.update(saved_at="now"), lambda d: d.update(saved_at=True),
                       lambda d: d.update(version=99), lambda d: d.pop("version"), lambda d: d.update(version=True)):
            item = copy.deepcopy(good)
            mutate(item)
            self.assertIsNone(lo.normalize(item), item)
        for junk in (None, "x", 5, []):
            self.assertIsNone(lo.normalize(junk))

    def test_a_loadout_with_damaged_parts_loads_what_it_can(self):
        item = lo.new_loadout("Warden", setup(), when=5.0)
        item["main"] = "broken"
        item["bars"] = [{"id": "ok"}, 5]
        got = lo.normalize(item)
        self.assertEqual(got["main"], [])
        self.assertEqual([b["id"] for b in got["bars"]], ["ok"])

    def test_names_are_cleaned_and_capped(self):
        self.assertEqual(lo.clean_name("  A   B "), "A B")
        self.assertEqual(len(lo.clean_name("x" * 90)), lo.MAX_NAME_LEN)
        self.assertEqual(lo.clean_name(None), "")

    def test_the_summary(self):
        item = lo.new_loadout("W", setup([dict(FIGHTER), dict(MISSILE)] + [None] * 18,
                                         [bar("b1", "A", ["shift"], [(0, FIGHTER), (1, MISSILE)]), bar("b2", "B", ["ctrl"], [(5, FIGHTER)])]))
        self.assertEqual(lo.summary(item), "main 2  -  2 bars, 3 slots")
        self.assertEqual(lo.summary(lo.new_loadout("W", setup(bar_list=[bar("b1", "A", ["shift"])]))), "main 0  -  1 bar, 0 slots")
        self.assertEqual(lo.summary(lo.new_loadout("W", setup(bar_list=[bar("b1", "A", ["shift"], [(0, FIGHTER)])]))), "main 0  -  1 bar, 1 slot")
        self.assertEqual(lo.summary(lo.new_loadout("W", setup(bar_list=[]))), "main 0  -  0 bars, 0 slots")

    def test_ages(self):
        self.assertEqual(lo.format_age(5), "just now")
        self.assertEqual(lo.format_age(600), "10 min ago")
        self.assertEqual(lo.format_age(7200), "2 h ago")
        self.assertEqual(lo.format_age(86400), "1 day ago")
        self.assertEqual(lo.format_age(3 * 86400), "3 days ago")

    def test_the_age_changes_word_at_the_edges(self):
        for seconds, text in ((0, "just now"), (-50, "just now"), (89, "just now"), (90, "1 min ago"), (3599, "59 min ago"),
                              (3600, "1 h ago"), (86399, "23 h ago"), (86400, "1 day ago"), (2 * 86400 - 1, "1 day ago"),
                              (2 * 86400, "2 days ago")):
            self.assertEqual(lo.format_age(seconds), text, seconds)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "sub", "quick_bars.json")
        self.store = lo.LoadoutStore(lambda: self.path, lambda: sync_submit)

    def item(self, name, when=1000.0, **kwargs):
        return lo.new_loadout(name, setup(**kwargs), when=when)

    def test_a_save_is_listed_at_once_and_is_in_the_file_under_loadouts_by_id(self):
        a = self.item("Warden")
        self.store.save(a)
        self.assertEqual([d["name"] for d in self.store.items], ["Warden"])
        self.assertEqual(kb.read_file(self.path)["loadouts"], {a["id"]: a})
        self.assertTrue(self.store.loaded)
        self.assertIsNone(self.store.error)

    def test_the_list_is_by_name_ignoring_case(self):
        for name, when in (("laptop", 1.0), ("Desk", 2.0), ("alpha", 3.0)):
            self.store.save(self.item(name, when))
        self.assertEqual([d["name"] for d in self.store.items], ["alpha", "Desk", "laptop"])

    def test_saving_again_replaces_that_loadout_only(self):
        a, b = self.item("A", 1.0), self.item("B", 2.0)
        self.store.save(a)
        self.store.save(b)
        a2 = {**a, "main": [dict(FIGHTER)] + [None] * 19}
        self.store.save(a2)
        self.assertEqual(len(self.store.items), 2)
        self.assertEqual(self.store.get(a["id"])["main"][0]["item_type"], "interceptor")
        self.assertEqual(sorted(kb.read_file(self.path)["loadouts"]), sorted([a["id"], b["id"]]))

    def test_delete(self):
        a, b = self.item("A", 1.0), self.item("B", 2.0)
        self.store.save(a)
        self.store.save(b)
        self.store.delete(a["id"])
        self.assertEqual([d["name"] for d in self.store.items], ["B"])
        self.assertEqual(list(kb.read_file(self.path)["loadouts"]), [b["id"]])
        self.store.delete("nothing-like-this")
        self.assertEqual(len(self.store.items), 1)

    def test_the_other_sections_of_the_file_survive(self):
        kb.write_section(self.path, "keys", {"hotkey.dock": None})
        kb.write_entry(self.path, "bars", "alice", {"bars": []})
        self.store.save(self.item("A"))
        self.store.delete(self.store.items[0]["id"])
        data = kb.read_file(self.path)
        self.assertEqual(data["keys"], {"hotkey.dock": None})
        self.assertEqual(data["bars"], {"alice": {"bars": []}})

    def test_two_clients_do_not_wipe_each_other(self):
        other = lo.LoadoutStore(lambda: self.path, lambda: sync_submit)
        a, b = self.item("From first", 1.0), self.item("From second", 2.0)
        self.store.save(a)
        other.save(b)
        self.assertEqual(len(kb.read_file(self.path)["loadouts"]), 2)
        self.assertEqual({d["name"] for d in self.store.items}, {"From first"})          # until it looks
        self.store.refresh()
        self.assertEqual({d["name"] for d in self.store.items}, {"From first", "From second"})

    def test_a_delete_does_not_remove_what_another_client_saved(self):
        other = lo.LoadoutStore(lambda: self.path, lambda: sync_submit)
        a, b = self.item("A", 1.0), self.item("B", 2.0)
        self.store.save(a)
        other.save(b)
        self.store.delete(a["id"])
        self.assertEqual(list(kb.read_file(self.path)["loadouts"]), [b["id"]])

    def test_a_scan_queued_before_a_save_does_not_make_it_blink_out(self):
        queued: list = []
        store = lo.LoadoutStore(lambda: self.path, lambda: lambda fn, *args: queued.append((fn, args)))
        store.refresh()
        a = self.item("A")
        store.save(a)
        self.assertEqual([d["name"] for d in store.items], ["A"])
        fn, args = queued[0]
        fn(*args)
        self.assertEqual([d["name"] for d in store.items], ["A"])
        for fn, args in queued[1:]:
            fn(*args)
        self.assertEqual(list(kb.read_file(self.path)["loadouts"]), [a["id"]])

    def test_a_delete_queued_behind_a_scan_does_not_blink_back(self):
        a = self.item("A")
        self.store.save(a)
        queued: list = []
        store = lo.LoadoutStore(lambda: self.path, lambda: lambda fn, *args: queued.append((fn, args)))
        store.refresh()
        fn, args = queued.pop()
        fn(*args)
        store.delete(a["id"])
        self.assertEqual(store.items, ())
        store.refresh()
        for fn, args in queued:
            fn(*args)
        self.assertEqual(store.items, ())
        self.assertEqual(kb.read_file(self.path)["loadouts"], {})

    def test_saving_again_something_deleted_shows_it_again(self):
        a = self.item("A")
        self.store.save(a)
        queued: list = []
        store = lo.LoadoutStore(lambda: self.path, lambda: lambda fn, *args: queued.append((fn, args)))
        store.refresh()
        fn, args = queued.pop(0)
        fn(*args)                                         # it has read the file: A is listed
        store.delete(a["id"])
        self.assertEqual(store.items, ())
        store.save(a)                                     # the same loadout again, before the delete has even run
        self.assertEqual([d["name"] for d in store.items], ["A"])
        for fn, args in queued:
            fn(*args)
        self.assertEqual([d["name"] for d in store.items], ["A"])

    def test_two_quick_saves_of_one_loadout_never_show_the_older_one_again(self):
        queued: list = []
        store = lo.LoadoutStore(lambda: self.path, lambda: lambda fn, *args: queued.append((fn, args)))
        first = self.item("Same", 1.0)
        second = {**first, "main": [dict(FIGHTER)] + [None] * 19}
        store.save(first)
        store.save(second)
        fn, args = queued[0]
        fn(*args)                                         # the first write ran and the scan after it read the file: only the first is there
        self.assertEqual(store.get(first["id"])["main"][0], dict(FIGHTER))        # still the newer one on top
        for fn, args in queued[1:]:
            fn(*args)
        self.assertEqual(store.get(first["id"])["main"][0], dict(FIGHTER))
        self.assertEqual(kb.read_file(self.path)["loadouts"][first["id"]]["main"][0]["item_type"], "interceptor")

    def test_a_failed_write_is_reported_and_the_loadout_stays_listed(self):
        blocker = os.path.join(self._tmp.name, "blocker")
        with open(blocker, "w", encoding="utf-8") as handle:
            handle.write("a file where a folder is needed")
        store = lo.LoadoutStore(lambda: os.path.join(blocker, "quick_bars.json"), lambda: sync_submit)
        store.save(self.item("A"))
        self.assertIn("could not save", store.error)
        self.assertEqual([d["name"] for d in store.items], ["A"])

    def test_a_failed_delete_is_reported_and_the_loadout_stays_hidden(self):
        a = self.item("A")
        self.store.save(a)
        with mock.patch.object(kb, "delete_entry", side_effect=OSError("disk full")), self.assertLogs("loadouts", level="ERROR"):
            self.store.delete(a["id"])
        self.assertIn("could not delete", self.store.error)
        self.assertEqual(self.store.items, ())

    def test_damaged_entries_in_the_file_are_skipped(self):
        good = self.item("Good")
        kb.write_section(self.path, "loadouts", {good["id"]: good, "bad1": {"id": "bad1"}, "bad2": "text", "bad3": {**good, "version": 99}})
        self.store.refresh()
        self.assertEqual([d["name"] for d in self.store.items], ["Good"])

    def test_a_section_that_is_not_a_dict_reads_as_nothing(self):
        kb.write_section(self.path, "loadouts", ["junk"])
        self.store.refresh()
        self.assertEqual(self.store.items, ())
        self.assertTrue(self.store.loaded)

    def test_find_by_name(self):
        for name, when in (("Warden", 1.0), ("Warden alt", 2.0), ("Hauler", 3.0), ("Hunter", 4.0)):
            self.store.save(self.item(name, when))
        self.assertEqual(self.store.find("warden")["name"], "Warden")                  # the exact name wins over a prefix
        self.assertEqual(self.store.find("  HAUL ")["name"], "Hauler")
        self.assertIsNone(self.store.find("h"))                                         # two prefix matches: not guessed
        self.assertIsNone(self.store.find("tv"))
        self.assertIsNone(self.store.find(""))

    def test_unique_names(self):
        self.store.save(self.item("Warden", 1.0))
        self.assertEqual(self.store.unique_name("Hauler"), "Hauler")
        self.assertEqual(self.store.unique_name("warden"), "warden (2)")
        self.store.save(self.item("Warden (2)", 2.0))
        self.assertEqual(self.store.unique_name("Warden"), "Warden (3)")
        self.assertEqual(self.store.unique_name(""), "Loadout")
        long = "x" * lo.MAX_NAME_LEN
        self.store.save(self.item(long, 3.0))
        self.assertLessEqual(len(self.store.unique_name(long)), lo.MAX_NAME_LEN)


class DeleteEntryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "quick_bars.json")

    def test_one_entry_goes_and_the_rest_stay(self):
        kb.write_entry(self.path, "previous", "alice", {"a": 1})
        kb.write_entry(self.path, "previous", "bob", {"b": 2})
        kb.write_section(self.path, "keys", {"x": None})
        kb.delete_entry(self.path, "previous", "alice")
        data = kb.read_file(self.path)
        self.assertEqual(data["previous"], {"bob": {"b": 2}})
        self.assertEqual(data["keys"], {"x": None})

    def test_deleting_what_is_not_there_changes_nothing(self):
        kb.write_entry(self.path, "previous", "alice", {"a": 1})
        before = open(self.path, encoding="utf-8").read()
        kb.delete_entry(self.path, "previous", "nobody")
        kb.delete_entry(self.path, "nothing", "alice")
        kb.delete_entry(os.path.join(self._tmp.name, "missing.json"), "previous", "alice")
        self.assertEqual(open(self.path, encoding="utf-8").read(), before)
        self.assertFalse(os.path.exists(os.path.join(self._tmp.name, "missing.json")))

    def test_the_loadout_key_actions_exist_and_have_no_default_key(self):
        self.assertEqual(kb.BY_ID["loadout.next"].arg, 1)
        self.assertEqual(kb.BY_ID["loadout.prev"].arg, -1)
        self.assertIsNone(kb.BY_ID["loadout.next"].default)
        self.assertEqual(kb.BY_ID["loadout.next"].kind, "loadout")


if __name__ == "__main__":
    unittest.main()

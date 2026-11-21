"""Tests for ui_layouts.py: what a layout holds, merging it over the game's layout dict, fitting it to another window size,
and the file it is kept in."""

from __future__ import annotations
import modloader

import copy
import json
import os
import tempfile
import unittest

import ui_layouts as ul


def sync_submit(fn, *args):
    fn(*args)


# What the game's ``_panel_layout_dict()`` returns (Client.py), with a mod's section (combat_log) on the end.
FULL = {
    "panels": {
        "inventory": {"x": 10, "y": 120, "minimized": False, "max_h": 480, "max_w": None,
                      "collapsed_sections": ["cargo"]},
        "turrets": {"x": 700, "y": 0, "minimized": True, "max_h": None, "max_w": None,
                    "layout_turrets_collapsed": True},
        "target": {"x": 1600, "y": 0, "minimized": False, "max_h": None, "max_w": 360},
    },
    "chat_panel": {"x": 0, "y": 880, "w": 580, "h": 198, "minimized": False,
                   "tabs": [{"id": "t1", "name": "Log", "sources": ["log"], "send": "log", "font": 1.0}],
                   "colours": {"global": [1, 2, 3]}, "settings_pos": [300, 40]},
    "hotbar": [None, {"item_class": "cargo_item", "item_type": "booster"}],
    "hotbar_pos": {"x": 690, "y": 1018},
    "hangar_order": {"7": [1, 2]},
    "stats": {"x": None, "y": None},
    "squad": {"x": 5, "y": 6},
    "minimap": {"zoom": 3, "resize": 1.5, "visible": True, "x": 1638, "y": 0},
    "coalition": {"visible": False, "x": 1, "y": 2, "width": 400, "height": 500},
    "friends": {"visible": True, "x": 3, "y": 4},
    "mission_detail": {"x": None, "y": None, "width": None, "height": None},
    "bot_haul": {"x": None, "y": None},
    "galaxy_favorites": ["Sol"],
    "combat_log": {"visible": True, "x": 9, "y": 8, "width": 420, "height": 320},
}


class PlacesTests(unittest.TestCase):
    def test_a_layout_keeps_the_places_of_every_section(self):
        got = ul.places(FULL)
        self.assertEqual(got["panels"]["inventory"], {"x": 10, "y": 120, "max_h": 480, "max_w": None})
        self.assertEqual(got["panels"]["target"], {"x": 1600, "y": 0, "max_h": None, "max_w": 360})
        self.assertEqual(got["chat_panel"], {"x": 0, "y": 880, "w": 580, "h": 198})
        self.assertEqual(got["hotbar_pos"], {"x": 690, "y": 1018})
        self.assertEqual(got["minimap"], {"resize": 1.5, "x": 1638, "y": 0})
        self.assertEqual(got["coalition"], {"x": 1, "y": 2, "width": 400, "height": 500})
        self.assertEqual(got["combat_log"], {"x": 9, "y": 8, "width": 420, "height": 320})

    def test_a_window_at_its_default_place_is_kept_as_null(self):
        got = ul.places(FULL)
        self.assertEqual(got["stats"], {"x": None, "y": None})
        self.assertEqual(got["bot_haul"], {"x": None, "y": None})
        self.assertEqual(got["mission_detail"], {"x": None, "y": None, "width": None, "height": None})

    def test_nothing_that_is_not_a_place_is_kept(self):
        text = json.dumps(ul.places(FULL))
        for left_out in ("hotbar\"", "item_type", "hangar_order", "galaxy_favorites", "Sol", "tabs", "colours", "settings_pos",
                         "minimized", "visible", "zoom", "collapsed_sections", "layout_turrets_collapsed"):
            self.assertNotIn(left_out, text)

    def test_the_input_is_not_changed(self):
        before = copy.deepcopy(FULL)
        ul.places(FULL)
        self.assertEqual(FULL, before)

    def test_odd_values_are_dropped_not_copied(self):
        got = ul.places({"panels": {"a": {"x": "left", "y": True, "max_h": 5}, "b": "text", "c": {}},
                         "weird": {"x": [1, 2]}, "list": [1, 2], "empty": {}})
        self.assertEqual(got, {"panels": {"a": {"max_h": 5}}})

    def test_a_section_a_later_mod_adds_comes_along(self):
        got = ul.places({"my_window": {"visible": True, "x": 4, "y": 5, "width": 30, "height": 40}})
        self.assertEqual(got, {"my_window": {"x": 4, "y": 5, "width": 30, "height": 40}})

    def test_count_and_summary(self):
        geometry = ul.places(FULL)
        # inventory, turrets, target, chat, hotbar, squad, minimap, coalition, friends, combat_log have a position
        self.assertEqual(ul.count(geometry), 10)
        self.assertEqual(ul.count(geometry, {"galaxy_map": [1, 2, 3, 4]}), 11)
        layout = ul.new_layout("Desk", (2560, 1440), geometry, {}, when=1000.0)
        self.assertEqual(ul.summary(layout), "2560 x 1440  -  10 windows")
        one = ul.new_layout("One", (800, 600), {"squad": {"x": 1, "y": 2}}, {}, when=1.0)
        self.assertEqual(ul.summary(one), "800 x 600  -  1 window")


class MergeTests(unittest.TestCase):
    def test_the_saved_places_are_laid_over_the_current_dict(self):
        saved = ul.places(FULL)
        current = copy.deepcopy(FULL)
        current["panels"]["inventory"].update(x=999, y=998, max_h=111)
        current["chat_panel"].update(x=500, w=100)
        current["minimap"].update(x=77, resize=1.0)
        merged = ul.merge(current, saved)
        self.assertEqual(merged["panels"]["inventory"]["x"], 10)
        self.assertEqual(merged["panels"]["inventory"]["max_h"], 480)
        self.assertEqual(merged["chat_panel"]["x"], 0)
        self.assertEqual(merged["chat_panel"]["w"], 580)
        self.assertEqual(merged["minimap"]["x"], 1638)
        self.assertEqual(merged["minimap"]["resize"], 1.5)

    def test_everything_that_is_not_a_place_stays_as_the_game_has_it_now(self):
        saved = ul.places(FULL)
        current = copy.deepcopy(FULL)
        current["hotbar"] = [{"item_type": "other"}]
        current["galaxy_favorites"] = ["Vega"]
        current["hangar_order"] = {"9": [3]}
        current["chat_panel"]["colours"] = {"squad": [9, 9, 9]}
        current["chat_panel"]["tabs"] = []
        current["panels"]["inventory"]["minimized"] = True
        current["minimap"]["zoom"] = 8
        current["coalition"]["visible"] = True
        merged = ul.merge(current, saved)
        for key in ("hotbar", "galaxy_favorites", "hangar_order"):
            self.assertEqual(merged[key], current[key])
        self.assertEqual(merged["chat_panel"]["colours"], {"squad": [9, 9, 9]})
        self.assertEqual(merged["chat_panel"]["tabs"], [])
        self.assertTrue(merged["panels"]["inventory"]["minimized"])
        self.assertEqual(merged["minimap"]["zoom"], 8)
        self.assertTrue(merged["coalition"]["visible"])

    def test_a_panel_the_game_creates_late_is_added(self):
        current = copy.deepcopy(FULL)
        del current["panels"]["turrets"]
        merged = ul.merge(current, ul.places(FULL))
        self.assertEqual(merged["panels"]["turrets"]["x"], 700)
        self.assertEqual(merged["panels"]["turrets"]["y"], 0)

    def test_a_section_the_game_does_not_have_now_is_left_out(self):
        current = copy.deepcopy(FULL)
        del current["combat_log"]
        merged = ul.merge(current, ul.places(FULL))
        self.assertNotIn("combat_log", merged)

    def test_a_null_position_is_never_laid_over(self):
        """The game's loader would choke on int(None) for Chat, and ignores it elsewhere: the window resets those itself."""
        saved = {"chat_panel": {"x": None, "y": None, "w": 300}, "panels": {"inventory": {"x": None, "y": 5}},
                 "stats": {"x": None, "y": None}}
        current = copy.deepcopy(FULL)
        current["stats"] = {"x": 40, "y": 50}
        merged = ul.merge(current, saved)
        self.assertEqual((merged["chat_panel"]["x"], merged["chat_panel"]["y"], merged["chat_panel"]["w"]), (0, 880, 300))
        self.assertEqual((merged["panels"]["inventory"]["x"], merged["panels"]["inventory"]["y"]), (10, 5))
        self.assertEqual(merged["stats"], {"x": 40, "y": 50})

    def test_a_null_size_is_laid_over(self):
        """max_h None is "the default height": a layout saved that way must reset a panel that was resized since."""
        merged = ul.merge(copy.deepcopy(FULL), {"panels": {"inventory": {"max_h": None}}})
        self.assertIsNone(merged["panels"]["inventory"]["max_h"])

    def test_only_places_are_taken_from_a_layout_file(self):
        """A hand-edited file cannot slip a binding into the game's dict."""
        merged = ul.merge(copy.deepcopy(FULL), {"hotbar_pos": {"x": 1, "y": 2, "item_type": "x"},
                                                "panels": {"target": {"x": 3, "minimized": True}}})
        self.assertNotIn("item_type", merged["hotbar_pos"])
        self.assertFalse(merged["panels"]["target"]["minimized"])
        self.assertEqual(merged["panels"]["target"]["x"], 3)

    def test_the_inputs_are_not_changed(self):
        current, saved = copy.deepcopy(FULL), ul.places(FULL)
        saved["squad"] = {"x": 99, "y": 98}
        before_current, before_saved = copy.deepcopy(current), copy.deepcopy(saved)
        ul.merge(current, saved)
        self.assertEqual(current, before_current)
        self.assertEqual(saved, before_saved)

    def test_malformed_saved_sections_are_skipped(self):
        merged = ul.merge(copy.deepcopy(FULL), {"panels": "x", "squad": [1, 2], "friends": {"x": 7, "y": 8}})
        self.assertEqual(merged["panels"], FULL["panels"])
        self.assertEqual(merged["squad"], FULL["squad"])
        self.assertEqual((merged["friends"]["x"], merged["friends"]["y"]), (7, 8))

    def test_default_sections_are_the_ones_saved_with_a_null_position(self):
        got = ul.default_sections(ul.places(FULL))
        self.assertEqual(sorted(got), ["bot_haul", "mission_detail", "stats"])

    def test_a_panel_is_not_a_default_section(self):
        self.assertEqual(ul.default_sections({"panels": {"inventory": {"x": None, "y": None}}}), [])

    def test_null_sizes_are_the_sizes_left_at_the_default(self):
        self.assertEqual(sorted(ul.null_sizes(ul.places(FULL))), [("mission_detail", "height"), ("mission_detail", "width")])

    def test_a_panels_null_size_is_not_one_the_window_has_to_reset(self):
        """The game's loader does read a null max_h / max_w (it means the default height / width)."""
        self.assertEqual(ul.null_sizes({"panels": {"inventory": {"max_h": None, "max_w": None}}}), [])

    def test_a_null_position_is_not_a_null_size(self):
        self.assertEqual(ul.null_sizes({"stats": {"x": None, "y": None}}), [])


class FitTests(unittest.TestCase):
    OLD, NEW = (2560, 1440), (1920, 1080)

    def test_a_window_keeps_its_distance_from_the_nearest_corner(self):
        # near the left / top: unchanged
        self.assertEqual(ul.fit_point(100, 50, self.OLD, self.NEW), (100, 50))
        # right half: the distance from the right edge stays (2560 - 2400 = 160)
        self.assertEqual(ul.fit_point(2400, 50, self.OLD, self.NEW), (1760, 50))
        # bottom half: the distance from the bottom edge stays (1440 - 1300 = 140)
        self.assertEqual(ul.fit_point(100, 1300, self.OLD, self.NEW), (100, 940))
        # both
        self.assertEqual(ul.fit_point(2000, 1400, self.OLD, self.NEW), (1360, 1040))

    def test_it_is_the_games_own_rule(self):
        """The game's frame loop: anchor_right = px > old_w / 2 (the middle itself counts as left)."""
        self.assertEqual(ul.fit_point(1280, 720, self.OLD, self.NEW), (1280, 720))
        self.assertEqual(ul.fit_point(1281, 721, self.OLD, self.NEW), (1281 - 640, 721 - 360))

    def test_the_chat_at_the_bottom_edge_stays_there(self):
        """The default chat y is the window height (its draw clamps it flush with the bottom)."""
        self.assertEqual(ul.fit_point(0, 1440, self.OLD, self.NEW), (0, 1080))

    def test_a_bigger_window_moves_right_and_bottom_windows_out(self):
        self.assertEqual(ul.fit_point(1700, 900, (1920, 1080), (2560, 1440)), (2340, 1260))

    def test_only_positions_move_and_sizes_stay(self):
        geometry = {"panels": {"target": {"x": 2400, "y": 10, "max_h": 300, "max_w": 360}},
                    "chat_panel": {"x": 10, "y": 1400, "w": 580, "h": 198},
                    "stats": {"x": None, "y": None}, "minimap": {"x": 2300, "y": 0, "resize": 1.5}}
        got = ul.fit_geometry(geometry, self.OLD, self.NEW)
        self.assertEqual(got["panels"]["target"], {"x": 1760, "y": 10, "max_h": 300, "max_w": 360})
        self.assertEqual(got["chat_panel"], {"x": 10, "y": 1040, "w": 580, "h": 198})
        self.assertEqual(got["stats"], {"x": None, "y": None})        # the default place follows the window by itself
        self.assertEqual(got["minimap"], {"x": 1660, "y": 0, "resize": 1.5})

    def test_the_input_is_not_changed(self):
        geometry = {"squad": {"x": 2000, "y": 1000}}
        ul.fit_geometry(geometry, self.OLD, self.NEW)
        self.assertEqual(geometry, {"squad": {"x": 2000, "y": 1000}})

    def test_the_mods_windows_move_by_their_top_left_corner(self):
        got = ul.fit_windows({"galaxy_map": [2000, 1000, 600, 400], "ship_layouts": [10, 20, 480, 380]}, self.OLD, self.NEW)
        self.assertEqual(got, {"galaxy_map": [1360, 640, 600, 400], "ship_layouts": [10, 20, 480, 380]})

    def test_fitting_to_the_same_size_changes_nothing(self):
        geometry = ul.places(FULL)
        self.assertEqual(ul.fit_geometry(geometry, self.OLD, self.OLD), geometry)


class LayoutTests(unittest.TestCase):
    def good(self):
        return ul.new_layout("Desktop", (2560, 1440), ul.places(FULL), {"galaxy_map": [1, 2, 3, 4]}, when=1700000000.5)

    def test_a_new_layout_is_valid_and_survives_json(self):
        layout = self.good()
        self.assertTrue(ul.valid_layout(layout))
        self.assertEqual(ul.valid_layout(json.loads(json.dumps(layout))), True)
        self.assertEqual(layout["size"], [2560, 1440])
        self.assertTrue(layout["id"].startswith("1700000000500-desktop"))

    def test_it_holds_a_copy_of_the_places(self):
        geometry = {"squad": {"x": 1, "y": 2}}
        layout = ul.new_layout("A", (800, 600), geometry)
        geometry["squad"]["x"] = 99
        self.assertEqual(layout["geometry"]["squad"]["x"], 1)

    def test_bad_layouts_are_rejected(self):
        for mutate in (lambda d: d.pop("id"), lambda d: d.update(id=""), lambda d: d.update(name="   "),
                       lambda d: d.update(name=5), lambda d: d.update(size=[0, 100]), lambda d: d.update(size=[100]),
                       lambda d: d.update(size=[True, 5]), lambda d: d.update(size="big"),
                       lambda d: d.update(saved_at="now"), lambda d: d.update(geometry=[]),
                       lambda d: d.update(windows=[]), lambda d: d.update(windows={"a": [1, 2, 3]}),
                       lambda d: d.update(windows={"a": [1, 2, 3, "4"]}), lambda d: d.update(version=99),
                       lambda d: d.pop("version")):
            layout = self.good()
            mutate(layout)
            self.assertFalse(ul.valid_layout(layout), layout)
        self.assertFalse(ul.valid_layout("text"))
        self.assertFalse(ul.valid_layout(None))

    def test_a_layout_keeps_the_places_other_mods_gave_it(self):
        extras = {"quick_bars": {"bars": [{"id": "b1", "mods": ["shift"], "fx": 0.5, "fy": None}]}}
        layout = ul.new_layout("Desk", (2560, 1440), {}, {}, extras=extras, when=1.0)
        self.assertEqual(layout["extras"], extras)
        self.assertTrue(ul.valid_layout(layout))
        self.assertEqual(json.loads(json.dumps(layout))["extras"], extras)

    def test_the_extras_are_a_copy(self):
        extras = {"quick_bars": {"bars": []}}
        layout = ul.new_layout("Desk", (800, 600), {}, extras=extras)
        extras["quick_bars"]["bars"].append(1)
        self.assertEqual(layout["extras"], {"quick_bars": {"bars": []}})

    def test_no_extras_is_an_empty_dict(self):
        self.assertEqual(ul.new_layout("Desk", (800, 600), {})["extras"], {})

    def test_a_layout_saved_before_there_were_extras_is_still_valid(self):
        layout = self.good()
        del layout["extras"]
        self.assertTrue(ul.valid_layout(layout))

    def test_bad_extras_are_rejected(self):
        for bad in ("text", [], 5, None, {"a": 5}, {"a": []}, {5: {}}, {"a": "x"}):
            layout = self.good()
            layout["extras"] = bad
            self.assertFalse(ul.valid_layout(layout), bad)

    def test_names_are_cleaned(self):
        self.assertEqual(ul.clean_name("  Big   desk \n screen "), "Big desk screen")
        self.assertEqual(len(ul.clean_name("x" * 100)), ul.MAX_NAME_LEN)
        self.assertEqual(ul.clean_name(None), "")

    def test_ids_are_unique_per_moment_and_file_safe(self):
        a, b = ul.make_id("Same", 1.0), ul.make_id("Same", 1.5)
        self.assertNotEqual(a, b)
        self.assertRegex(ul.make_id("A / B: C?", 1.0), r"^[0-9]+-[a-z0-9-]+$")
        self.assertTrue(ul.make_id("???", 1.0).endswith("-layout"))

    def test_ages(self):
        self.assertEqual(ul.format_age(5), "just now")
        self.assertEqual(ul.format_age(600), "10 min ago")
        self.assertEqual(ul.format_age(7200), "2 h ago")
        self.assertEqual(ul.format_age(86400), "1 day ago")
        self.assertEqual(ul.format_age(3 * 86400), "3 days ago")
        self.assertEqual(ul.format_age(-5), "just now")


class FileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "ui_layouts.json")

    def layout(self, name, when=1000.0, size=(1920, 1080)):
        return ul.new_layout(name, size, {"squad": {"x": 1, "y": 2}}, {}, when=when)

    def test_a_missing_file_is_an_empty_list(self):
        self.assertEqual(ul.read_file(self.path), [])

    def test_a_broken_file_is_an_empty_list(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(ul.read_file(self.path), [])
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write('["a list"]')
        self.assertEqual(ul.read_file(self.path), [])

    def test_a_write_reads_back_and_leaves_no_temp_file(self):
        layouts = [self.layout("A"), self.layout("B", 2000.0)]
        ul.write_file(self.path, layouts)
        self.assertEqual(ul.read_file(self.path), layouts)
        self.assertEqual(os.listdir(self._tmp.name), ["ui_layouts.json"])

    def test_invalid_entries_are_skipped_and_the_rest_kept(self):
        good = self.layout("Good")
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "layouts": [good, {"id": "x"}, "nonsense"]}, handle)
        self.assertEqual(ul.read_file(self.path), [good])

    def test_the_default_path_is_next_to_the_client(self):
        self.assertEqual(os.path.basename(ul.default_path()), "ui_layouts.json")
        self.assertEqual(os.path.dirname(ul.default_path()), str(modloader.REPO_DIR))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "sub", "ui_layouts.json")
        self.store = ul.LayoutStore(self.path, submit=sync_submit)

    def layout(self, name, when=1000.0, size=(1920, 1080)):
        return ul.new_layout(name, size, {"squad": {"x": 1, "y": 2}}, {}, when=when)

    def test_a_save_is_listed_at_once_and_is_on_disk(self):
        a = self.layout("Desk")
        self.store.save(a)
        self.assertEqual([d["name"] for d in self.store.layouts], ["Desk"])
        self.assertEqual(ul.read_file(self.path), [a])
        self.assertTrue(self.store.loaded)
        self.assertIsNone(self.store.error)

    def test_the_list_is_by_name_ignoring_case(self):
        for name, when in (("laptop", 1.0), ("Desk", 2.0), ("alpha", 3.0)):
            self.store.save(self.layout(name, when))
        self.assertEqual([d["name"] for d in self.store.layouts], ["alpha", "Desk", "laptop"])

    def test_saving_again_replaces_that_layout_only(self):
        a, b = self.layout("A", 1.0), self.layout("B", 2.0)
        self.store.save(a)
        self.store.save(b)
        a2 = {**a, "size": [800, 600]}
        self.store.save(a2)
        self.assertEqual(len(self.store.layouts), 2)
        self.assertEqual(self.store.get(a["id"])["size"], [800, 600])
        self.assertEqual(ul.read_file(self.path), [a2, b])

    def test_delete_removes_it_from_the_list_and_the_file(self):
        a, b = self.layout("A", 1.0), self.layout("B", 2.0)
        self.store.save(a)
        self.store.save(b)
        self.store.delete(a["id"])
        self.assertEqual([d["name"] for d in self.store.layouts], ["B"])
        self.assertEqual(ul.read_file(self.path), [b])
        self.store.delete("nothing-like-this")
        self.assertEqual(len(self.store.layouts), 1)

    def test_two_clients_in_one_folder_do_not_wipe_each_other(self):
        """Each client has its own in-memory list; every write re-reads the file and changes only its own layout."""
        other = ul.LayoutStore(self.path, submit=sync_submit)
        a, b = self.layout("From first", 1.0), self.layout("From second", 2.0)
        self.store.save(a)                 # the second client has not looked at the file yet
        other.save(b)
        self.assertEqual({d["name"] for d in ul.read_file(self.path)}, {"From first", "From second"})
        self.assertEqual({d["name"] for d in other.layouts}, {"From first", "From second"})
        self.assertEqual({d["name"] for d in self.store.layouts}, {"From first"})     # until it looks
        self.store.refresh()
        self.assertEqual({d["name"] for d in self.store.layouts}, {"From first", "From second"})

    def test_a_delete_does_not_remove_what_another_client_saved(self):
        other = ul.LayoutStore(self.path, submit=sync_submit)
        a, b = self.layout("A", 1.0), self.layout("B", 2.0)
        self.store.save(a)
        other.save(b)
        self.store.delete(a["id"])
        self.assertEqual([d["name"] for d in ul.read_file(self.path)], ["B"])

    def test_a_scan_queued_before_a_save_does_not_make_it_blink_out(self):
        """The writer is one thread, so a scan can be queued ahead of a save: it reads the file without the layout."""
        queued: list = []
        store = ul.LayoutStore(self.path, submit=lambda fn, *args: queued.append((fn, args)))
        store.refresh()                    # queued: a scan
        a = self.layout("A")
        store.save(a)                      # queued: the write
        self.assertEqual([d["name"] for d in store.layouts], ["A"])
        fn, args = queued[0]
        fn(*args)                          # the scan runs first and sees an empty folder
        self.assertEqual([d["name"] for d in store.layouts], ["A"])
        for fn, args in queued[1:]:
            fn(*args)
        self.assertEqual([d["name"] for d in store.layouts], ["A"])
        self.assertEqual(ul.read_file(self.path), [a])

    def test_a_delete_queued_behind_a_scan_does_not_blink_back(self):
        a = self.layout("A")
        self.store.save(a)
        queued: list = []
        store = ul.LayoutStore(self.path, submit=lambda fn, *args: queued.append((fn, args)))
        store.refresh()
        fn, args = queued.pop()
        fn(*args)
        self.assertEqual([d["name"] for d in store.layouts], ["A"])
        store.delete(a["id"])
        self.assertEqual(store.layouts, ())
        store.refresh()
        for fn, args in queued:
            fn(*args)
        self.assertEqual(store.layouts, ())
        self.assertEqual(ul.read_file(self.path), [])

    def test_a_failed_write_is_reported_and_the_layout_stays_in_the_list(self):
        blocker = os.path.join(self._tmp.name, "blocker")
        with open(blocker, "w", encoding="utf-8") as handle:
            handle.write("a file where a folder is needed")
        store = ul.LayoutStore(os.path.join(blocker, "ui_layouts.json"), submit=sync_submit)
        store.save(self.layout("A"))
        self.assertIsNotNone(store.error)
        self.assertIn("could not save", store.error)
        self.assertEqual([d["name"] for d in store.layouts], ["A"])

    def test_find_by_name(self):
        for name, when in (("Desk", 1.0), ("Desktop wide", 2.0), ("Laptop", 3.0), ("Lapel", 4.0)):
            self.store.save(self.layout(name, when))
        self.assertEqual(self.store.find("desk")["name"], "Desk")             # the exact name wins over a prefix
        self.assertEqual(self.store.find("  LAPT ")["name"], "Laptop")        # one prefix match
        self.assertIsNone(self.store.find("lap"))                              # two prefix matches: not guessed
        self.assertIsNone(self.store.find("tv"))
        self.assertIsNone(self.store.find(""))

    def test_unique_names(self):
        self.store.save(self.layout("Desk", 1.0))
        self.assertEqual(self.store.unique_name("Laptop"), "Laptop")
        self.assertEqual(self.store.unique_name("desk"), "desk (2)")
        self.store.save(self.layout("Desk (2)", 2.0))
        self.assertEqual(self.store.unique_name("Desk"), "Desk (3)")
        long = "x" * ul.MAX_NAME_LEN
        self.store.save(self.layout(long, 3.0))
        self.assertLessEqual(len(self.store.unique_name(long)), ul.MAX_NAME_LEN)


if __name__ == "__main__":
    unittest.main()

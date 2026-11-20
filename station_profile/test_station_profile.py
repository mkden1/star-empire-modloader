"""station_profile: a per-station snapshot of what the client held while you were docked, written when you leave."""
import modloader
import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import capture_settings
import scan_writer
import station_profile as sp

CARGO = [
    {"item_key": "metal_ore", "item_category": "resource", "cargo_size": 5000, "quantity": None},
    {"item_key": "workers", "item_category": "resource", "cargo_size": 595},
    {"item_key": "item:resource:crate", "item_category": "resource", "quantity": 3, "cargo_size": 10},
    {"item_key": "item:station:industrial_metal_drill", "display_name": "Industrial Metal Drill", "quantity": 10,
     "cargo_size": 5, "stats": {"Cycle Output": "3 Metal Ore", "Cycle Time": "10 seconds", "Tech": 9}},
    {"item_key": "item:station:station_shield_mk3", "display_name": "Station Shield Mk III", "quantity": 1,
     "stats": {"Shield": 5}},
    "junk", {"item_category": "resource"},
]


def overlay_data(**extra):
    data = {"attached_station_id": 2105, "station_name": "cryldorwen - 1", "owner_name": "ExamplePlayer", "is_owner": True,
            "management_access": True, "public_dock": False, "dock_min_rank": "officer", "management_min_rank": "",
            "motd": "Welcome", "planet_id": 32627, "has_colony": True, "trade_enabled": True,
            "construction_enabled": True, "tech": 9, "faction": "Free", "greeting": "x" * 500,
            "construction_projects": [{"display_name": "Willowbarge", "build_state": "building", "manhours_total": 100,
                                       "manhours_done": 40, "eta_seconds": 3600, "resource_costs": [{"a": 1}]}],
            "blueprints": [{}, {}], "ps_trade_inventory": [{}, {}, {}], "rows": [{"big": 1}]}
    data.update(extra)
    return data


class Fake:
    def __init__(self, overlay="player_station", **extra):
        self._lock = threading.RLock()
        self._station_overlay = {"kind": overlay, "data": overlay_data(**extra)} if overlay else None
        self._ps_station_cargo = list(CARGO)
        self._ps_resource_caps = {"metal_ore": 15000}
        self._ps_resource_reserves = {"metal_ore": 0}
        self._ps_station_credits = 1234.5
        self._ps_workers_used = 12
        self._ps_hull_used, self._ps_hull_space = 5000, 115000
        self._ps_trade_mgr = {"station_id": 2105, "config": {
            ("Resources", "metal"): {"buy_price": 10, "sell_price": 12, "min_qty": 0, "max_qty": 500}}}
        self._layout = {"system_name": "Zordoil Frontier", "planets": [
            {"id": 32627, "name": "Cryldorwen", "is_moon": False},
            {"id": 5, "name": "Feshi", "is_moon": True, "parent_planet_id": 4},
            {"id": 4, "name": "Zelelashlyn", "is_moon": False}]}
        self._my_stations_data = []
        self._username = "ExamplePlayer"
        self.trade_requests = 0
        self.events = []

    def request_ps_trade_manager(self):
        self.trade_requests += 1


class CargoTests(unittest.TestCase):
    def test_resources_and_modules_are_told_apart(self):
        out = sp.compact_cargo(CARGO)
        self.assertEqual(out["resources"], {"metal_ore": 5000, "workers": 595, "item:resource:crate": 3})
        names = [m["name"] for m in out["modules"]]
        self.assertEqual(names, ["Industrial Metal Drill", "Station Shield Mk III"])
        drill = out["modules"][0]
        self.assertEqual((drill["quantity"], drill["stats"]),
                         (10, {"Cycle Output": "3 Metal Ore", "Cycle Time": "10 seconds"}))   # only the stats we use

    def test_bad_input_is_skipped(self):
        self.assertEqual(sp.compact_cargo(None), {"resources": {}, "modules": []})
        self.assertEqual(sp.compact_cargo([{"item_key": "x", "item_category": "resource", "cargo_size": "n/a"}]),
                         {"resources": {}, "modules": []})

    def test_policies(self):
        self.assertEqual(sp.policies({"public_dock": True}), {"docking": "public", "management": "owner"})
        self.assertEqual(sp.policies({"dock_min_rank": " Officer ", "management_min_rank": "Leader"}),
                         {"docking": "officer", "management": "leader"})


class BuildTests(unittest.TestCase):
    def setUp(self):
        sp.reset_for_tests()

    def test_a_docked_station_becomes_a_full_profile(self):
        profile = sp.build_profile(Fake(), "undock")
        self.assertEqual((profile["station_id"], profile["event"], profile["version"]), ("2105", "undock", 1))
        self.assertEqual((profile["station_name"], profile["owner_name"], profile["is_owner"]),
                         ("cryldorwen - 1", "ExamplePlayer", True))
        self.assertEqual((profile["docking"], profile["management"]), ("officer", "owner"))
        self.assertEqual(profile["system_name"], "Zordoil Frontier")
        self.assertEqual(profile["taken_by"], "ExamplePlayer")
        self.assertEqual(profile["motd"], "Welcome")
        self.assertEqual(profile["flags"], {"trade_enabled": True, "construction_enabled": True})
        self.assertEqual(profile["resources"]["metal_ore"], 5000)
        self.assertEqual(profile["resource_caps"], {"metal_ore": 15000})
        self.assertEqual((profile["credits"], profile["workers_used"]), (1234.5, 12))
        self.assertEqual((profile["hull_used"], profile["hull_space"]), (5000, 115000))
        self.assertEqual(profile["trade_listed"], 3)
        self.assertLess(abs(profile["seen_at"] - time.time()), 5)
        json.dumps(profile)                                              # it has to be writable as it stands

    def test_the_overlay_part_keeps_only_plain_unnamed_fields(self):
        overlay = sp.build_profile(Fake(), "undock")["overlay"]
        self.assertEqual(overlay, {"tech": 9, "faction": "Free"})        # no big strings, lists or named fields

    def test_the_trade_list_is_taken_only_when_it_is_this_stations(self):
        profile = sp.build_profile(Fake(), "undock")
        self.assertEqual(profile["trade_entries"], [{"category": "Resources", "item_type": "metal", "buy_price": 10,
                                                     "sell_price": 12, "min_qty": 0, "max_qty": 500}])
        other = Fake()
        other._ps_trade_mgr["station_id"] = 7
        self.assertIsNone(sp.build_profile(other, "undock")["trade_entries"])
        empty = Fake()
        empty._ps_trade_mgr = {"station_id": 2105, "config": {}}
        self.assertEqual(sp.build_profile(empty, "undock")["trade_entries"], [])      # known and empty

    def test_the_construction_queue_is_summarised(self):
        construction = sp.build_profile(Fake(), "undock")["construction"]
        self.assertEqual(construction["blueprints"], 2)
        self.assertEqual(construction["projects"], [{"name": "Willowbarge", "build_state": "building",
                                                     "manhours_total": 100, "manhours_done": 40, "eta_seconds": 3600}])
        self.assertIsNone(sp.build_profile(Fake(construction_projects=None), "undock")["construction"])

    def test_not_a_managed_player_station_means_no_profile(self):
        self.assertIsNone(sp.build_profile(Fake(overlay=None), "undock"))
        self.assertIsNone(sp.build_profile(Fake(overlay="npc"), "undock"))
        self.assertIsNone(sp.build_profile(Fake(jump_gate_key="gate_1"), "undock"))
        self.assertIsNone(sp.build_profile(Fake(attached_station_id=None), "undock"))

    def test_ownership_falls_back_to_the_owner_name(self):
        fake = Fake()
        del fake._station_overlay["data"]["is_owner"]
        self.assertTrue(sp.build_profile(fake, "undock")["is_owner"])
        fake._station_overlay["data"]["owner_name"] = "someone"
        self.assertFalse(sp.build_profile(fake, "undock")["is_owner"])


class BodyTests(unittest.TestCase):
    def test_the_layout_names_the_planet_the_station_is_on(self):
        profile = sp.build_profile(Fake(), "undock")
        self.assertEqual((profile["body_name"], profile["body_kind"], profile["body_parent"]),
                         ("Cryldorwen", "planet", None))

    def test_a_moon_knows_its_parent(self):
        profile = sp.build_profile(Fake(planet_id=5), "undock")
        self.assertEqual((profile["body_name"], profile["body_kind"], profile["body_parent"]),
                         ("Feshi", "moon", "Zelelashlyn"))

    def test_the_possessions_list_fills_in_when_the_layout_has_no_such_body(self):
        fake = Fake(planet_id=999)
        fake._my_stations_data = [{"station_id": 2105, "body_name": "Cryldorwen", "body_kind": "Planet"}]
        self.assertEqual(sp.build_profile(fake, "undock")["body_name"], "Cryldorwen")
        fake._my_stations_data = [{"station_id": 7, "body_name": "Other"}]
        self.assertIsNone(sp.build_profile(fake, "undock")["body_name"])

    def test_an_unattached_station_has_no_body(self):
        profile = sp.build_profile(Fake(planet_id=None), "undock")
        self.assertEqual((profile["body_name"], profile["body_kind"], profile["planet_id"]), (None, None, None))

    def test_a_missing_layout_does_not_break_the_snapshot(self):
        fake = Fake()
        fake._layout = None
        self.assertIsNone(sp.build_profile(fake, "undock")["body_name"])


class WriteTests(unittest.TestCase):
    def setUp(self):
        sp.reset_for_tests()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = os.path.join(self._tmp.name, "station_profiles")

    def test_a_profile_is_written_one_file_per_station_and_reads_back(self):
        self.assertTrue(sp.capture(Fake(), "undock", self.dir))
        scan_writer.flush()
        self.assertEqual(os.listdir(self.dir), ["2105.json"])           # no temp file left behind
        loaded = sp.load_profiles(self.dir)
        self.assertEqual(list(loaded), ["2105"])
        self.assertEqual(loaded["2105"]["resources"]["metal_ore"], 5000)

    def test_the_newest_visit_replaces_the_old_one(self):
        sp.capture(Fake(), "undock", self.dir)
        later = Fake()
        later._ps_station_cargo = [{"item_key": "metal_ore", "item_category": "resource", "cargo_size": 1}]
        sp.capture(later, "undock", self.dir)
        scan_writer.flush()
        self.assertEqual(sp.load_profiles(self.dir)["2105"]["resources"], {"metal_ore": 1})

    def test_unreadable_files_are_skipped_and_a_missing_folder_is_empty(self):
        os.makedirs(self.dir)
        with open(os.path.join(self.dir, "bad.json"), "w") as handle:
            handle.write("{not json")
        with open(os.path.join(self.dir, "notes.txt"), "w") as handle:
            handle.write("x")
        sp.capture(Fake(), "undock", self.dir)
        scan_writer.flush()
        self.assertEqual(list(sp.load_profiles(self.dir)), ["2105"])
        self.assertEqual(sp.load_profiles(os.path.join(self._tmp.name, "nope")), {})

    def test_the_file_name_is_made_safe(self):
        self.assertEqual(sp._file_name("2105"), "2105.json")
        self.assertEqual(sp._file_name("a/b\\c:d"), "a_b_c_d.json")

    def test_nothing_is_written_when_not_docked_or_for_an_empty_cargo_push(self):
        self.assertFalse(sp.capture(Fake(overlay=None), "undock", self.dir))
        empty = Fake()
        empty._ps_station_cargo = []
        self.assertFalse(sp.capture(empty, "cargo", self.dir))
        self.assertTrue(sp.capture(empty, "undock", self.dir))           # leaving always records what there is
        scan_writer.flush()

    def test_the_real_file_is_gated_by_the_capture_switch_but_an_explicit_folder_is_not(self):
        with mock.patch.object(capture_settings, "enabled", return_value=False):
            self.assertFalse(sp.capture(Fake(), "undock"))
            self.assertTrue(sp.capture(Fake(), "undock", self.dir))
        scan_writer.flush()


class DebounceTests(unittest.TestCase):
    def setUp(self):
        sp.reset_for_tests()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.object(sp, "record", lambda profile, out_dir=None: self.written.append(profile))
        self.written = []
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(capture_settings, "enabled", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_cargo_pushes_write_at_most_once_a_minute_per_station(self):
        fake = Fake()
        base = time.monotonic()
        self.assertTrue(sp.on_cargo(fake, base))
        self.assertFalse(sp.on_cargo(fake, base + 10))
        self.assertFalse(sp.on_cargo(fake, base + sp.PROFILE_EVERY_S - 1))
        self.assertTrue(sp.on_cargo(fake, base + sp.PROFILE_EVERY_S + 1))
        self.assertEqual(len(self.written), 2)
        self.assertEqual(self.written[0]["event"], "cargo")

    def test_another_station_has_its_own_clock(self):
        base = time.monotonic()
        self.assertTrue(sp.on_cargo(Fake(), base))
        self.assertTrue(sp.on_cargo(Fake(attached_station_id=2106), base + 1))

    def test_no_overlay_no_write(self):
        self.assertFalse(sp.on_cargo(Fake(overlay=None)))


def make_class():
    class Window(Fake):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.calls = []

        def show_station_overlay(self, data, kind):
            self.calls.append(("show", kind))
            self._station_overlay = {"kind": kind, "data": data}

        def hide_station_overlay(self):
            self.calls.append(("hide", self._station_overlay is not None))
            self._station_overlay = None

        def set_ps_station_cargo(self, data):
            self.calls.append(("cargo", data))
    return Window


class HookTests(unittest.TestCase):
    def setUp(self):
        sp.reset_for_tests()
        self.cls = make_class()
        self.assertTrue(sp.install(self.cls))
        self.written = []
        for patcher in (mock.patch.object(sp, "record", lambda profile, out_dir=None: self.written.append(profile)),
                        mock.patch.object(capture_settings, "enabled", return_value=True)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_install_is_idempotent_and_refuses_a_class_without_the_methods(self):
        self.assertFalse(sp.install(self.cls))
        with self.assertLogs(sp.logger, "WARNING"):
            self.assertFalse(sp.install(type("Bare", (), {})))

    def test_leaving_snapshots_before_the_game_clears_the_overlay(self):
        window = self.cls()
        window.hide_station_overlay()
        self.assertEqual(len(self.written), 1)
        self.assertEqual(self.written[0]["event"], "undock")
        self.assertEqual(window.calls, [("hide", True)])
        self.assertIsNone(window._station_overlay)

    def test_docking_at_your_own_station_asks_for_the_trade_list_once(self):
        window = self.cls(overlay=None)
        window.show_station_overlay(overlay_data(), "player_station")
        self.assertEqual(window.trade_requests, 1)
        self.assertEqual(window.calls, [("show", "player_station")])

    def test_it_does_not_ask_for_stations_that_are_not_yours_to_manage(self):
        window = self.cls(overlay=None)
        window.show_station_overlay(overlay_data(is_owner=False), "player_station")
        window.show_station_overlay(overlay_data(management_access=False), "player_station")
        window.show_station_overlay(overlay_data(jump_gate_key="g"), "player_station")
        window.show_station_overlay(overlay_data(), "npc")
        self.assertEqual(window.trade_requests, 0)

    def test_a_cargo_push_while_docked_snapshots_once_a_minute(self):
        window = self.cls()
        window.set_ps_station_cargo({"cargo": []})
        window.set_ps_station_cargo({"cargo": []})
        self.assertEqual(len(self.written), 1)
        self.assertEqual(window.calls, [("cargo", {"cargo": []}), ("cargo", {"cargo": []})])

    def test_a_failure_switches_it_off_and_the_game_still_runs(self):
        window = self.cls()
        with mock.patch.object(sp, "capture", side_effect=RuntimeError("boom")), self.assertLogs(sp.logger, "ERROR"):
            window.hide_station_overlay()
        self.assertEqual(window.calls, [("hide", True)])
        self.assertFalse(sp.enabled())
        window = self.cls()
        window.hide_station_overlay()
        self.assertEqual(self.written, [])
        self.assertEqual(window.calls, [("hide", True)])


class ClientHookTests(unittest.TestCase):
    def test_client_py_installs_it(self):
        from pathlib import Path
        source = modloader.game_source("Client.py")
        self.assertIn("\nstation_profile.install(SolarSystemWindow)\n", source)

    def test_the_real_window_is_hooked_with_the_methods_it_wraps(self):
        from Client import SolarSystemWindow
        self.assertTrue(SolarSystemWindow.__dict__.get("_station_profile_installed"))
        for name in ("show_station_overlay", "hide_station_overlay", "set_ps_station_cargo"):
            self.assertTrue(hasattr(getattr(SolarSystemWindow, name), "__wrapped__"), name)


if __name__ == "__main__":
    unittest.main()

"""The scan loggers must never wait on the disk on the thread that calls them.

Why: the freeze watchdog showed the client's message thread stuck for 1-3 s inside filesystem
calls made by these loggers (a folder check that normally takes 0.08 ms, a first-time read of a
13 MB file, a per-push append) whenever the shared drive hiccuped -- about two thirds of every
stall in a session. These tests run every production record_* function (``out_path=None``, the
path the game takes) with the filesystem entry points instrumented, and assert that none of
them is reached from the calling thread; the writers' own threads may touch the disk freely.
"""
import builtins
import os
import tempfile
import threading
import unittest
from unittest import mock

import bot_fleet_scan_logger
import capture_settings
import colony_scan_logger
import combat_scan_logger
import scan_logger
import scan_paths
import scan_writer
import shuttle_route_capture
import station_id_logger
import station_log_capture
import trade_bot_slots_capture


class _DiskWatch:
    """Records every filesystem call made from ``thread_id`` while active."""

    PATCHED = (("builtins", "open"), ("os", "makedirs"), ("os", "mkdir"), ("os", "stat"),
               ("os", "listdir"), ("os", "scandir"), ("os", "replace"), ("os", "remove"),
               ("os.path", "exists"), ("os.path", "isdir"), ("os.path", "isfile"),
               ("os.path", "getsize"))

    def __init__(self, thread_id):
        self.thread_id = thread_id
        self.calls = []
        self._patches = []

    def __enter__(self):
        import importlib
        for module_name, attr in self.PATCHED:
            module = importlib.import_module(module_name)
            real = getattr(module, attr)
            self._patches.append(mock.patch.object(module, attr, self._wrap(real, f"{module_name}.{attr}")))
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()

    def _wrap(self, real, name):
        def wrapper(*args, **kwargs):
            if threading.get_ident() == self.thread_id:
                self.calls.append(name)
            return real(*args, **kwargs)
        return wrapper


class ProductionRecordersNeverTouchTheDiskOnTheCallerThread(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(lambda: (scan_writer.flush(), combat_scan_logger.flush()))   # before the dir goes
        self.root = os.path.join(self._tmp.name, "scan_data")      # deliberately not created
        scan_paths.set_root_for_tests(self.root)
        self.addCleanup(scan_paths.set_root_for_tests, None)
        # production-mode recorders obey capture_settings: use an empty settings file (all on)
        # so a developer's own capture_settings.json can't change what this test sees
        capture_settings.configure_for_tests(os.path.join(self._tmp.name, "capture_settings.json"))
        self.addCleanup(capture_settings.configure_for_tests, None)
        bot_fleet_scan_logger._last_seen.clear()
        bot_fleet_scan_logger._last_system.clear()
        combat_scan_logger._last_seen.clear()
        combat_scan_logger._rows_cache.clear()
        colony_scan_logger._last_seen.clear()
        station_log_capture._reset_cache_for_tests()
        # Start both writer threads now, so thread start-up is not counted against the caller.
        scan_writer.submit(lambda: None)
        combat_scan_logger._ensure_write_worker()
        scan_writer.flush()
        combat_scan_logger.flush()

    def flush_all(self):
        scan_writer.flush()
        combat_scan_logger.flush()

    def path(self, name):
        return os.path.join(self.root, name)

    def test_every_recorder_leaves_the_disk_to_its_writer_thread(self):
        me = threading.get_ident()
        with _DiskWatch(me) as watch:
            bot_fleet_scan_logger.record_bot_state(
                701, {"bot_pid": 701, "state": "hauling", "system_name": "Alpha"},
                {"character": "tester"})
            bot_fleet_scan_logger.record_bot_state(      # a system change -> leg transition
                701, {"bot_pid": 701, "state": "hauling", "system_name": "Beta"},
                {"character": "tester"})
            combat_scan_logger.record_inventory_items(
                [{"item_type": "laser_mk1", "category": "weapon", "stats": {"dps": 5}}])
            combat_scan_logger.record_ship_specs(
                {"ship": {"display_name": "Viper"}, "weapons": [{"weapon_type": "laser_mk1"}],
                 "plugins": [], "energy": {"current_energy": 90, "max_energy": 100}})
            combat_scan_logger.record_turret_state({"hardpoints": [1, 2, 3]})
            combat_scan_logger.record_ship_plugin_data({"slots": ["a", "b"]})
            combat_scan_logger.record_skills_data([{"skill_id": "gunnery", "level": 3}],
                                                   [{"level": 1}])
            station_log_capture.record_station_log_rows(
                "st-1", [{"id": "r1", "time": "10:00", "text": "Ver-A4 docked."}])
            scan_logger.record_scan("Sys", {"planet_name": "Cryldorwen", "planet_type": "Terra"})
            station_id_logger.record_stations("Sys", [{"station_id": "42", "name": "Hub"}])
            shuttle_route_capture.record_shuttle_routes(
                "hub-1", [{"target_station_id": 9, "resource_name": "iron", "direction": "in"}])
            trade_bot_slots_capture.record_trade_bot_slots("tester", 5, 59)
            colony_scan_logger.record_colony_data(
                {"station_id": 21, "colony": {"population": 9, "income_history": [{"space_corn": 5}]}},
                {"system_name": "Sys"})
            colony_scan_logger.record_station_setup(
                21, {"modules": {"extractor": 2}, "cargo": [], "station_credits": 5})
            colony_scan_logger.record_shuttle_network(
                21, {"routes": [{"id": 1, "resource_key": "iron", "source_qty": 3,
                                 "destination_qty": 4}]})
        self.assertEqual(watch.calls, [],
                         "a recorder touched the filesystem on the calling thread")

        self.flush_all()                  # ...and the writer threads really did the work:
        self.assertEqual(
            sorted(f for f in os.listdir(self.root) if not f.endswith(".lock")),
            ["bot_fleet_scan.jsonl", "bot_leg_times.jsonl", "colony_scan.jsonl", "combat_scan.jsonl",
             "planet_scans.csv", "shuttle_routes.csv", "station_ids.csv",
             "station_logs.csv", "trade_bot_slots.csv"])
        sources = {(r["source"], r["key"]) for r in combat_scan_logger.load_combat_scan(
            self.path("combat_scan.jsonl"))}
        self.assertIn(("inventory_item", "laser_mk1"), sources)
        self.assertIn(("skill", "gunnery"), sources)
        self.assertIn(("turret_state", None), sources)
        self.assertIn(("ship_plugin_data", None), sources)
        self.assertIn(("energy_sample", None), sources)
        self.assertIn(("mastery_tree", None), sources)
        legs = bot_fleet_scan_logger.load_bot_leg_times(self.path("bot_leg_times.jsonl"))
        self.assertEqual([(l["from_system"], l["to_system"]) for l in legs], [("Alpha", "Beta")])
        colony_rows = {(r["source"], r["key"]) for r in colony_scan_logger.load_colony_scan(
            self.path("colony_scan.jsonl"))}
        self.assertEqual(colony_rows, {("colony_data", "21"), ("colony_tick", "21"),
                                       ("station_setup", "21"), ("shuttle_network", "21"),
                                       ("shuttle_levels", "21")})

    def test_a_payload_the_caller_mutates_afterwards_is_logged_as_it_was(self):
        item = {"item_type": "shield_mk2", "category": "shield", "stats": {"bank": 10}}
        combat_scan_logger.record_inventory_items([item])
        item["stats"]["bank"] = 999            # the UI reuses and edits the live payload
        self.flush_all()
        rows = combat_scan_logger.load_combat_scan(self.path("combat_scan.jsonl"))
        self.assertEqual(rows[0]["data"]["stats"]["bank"], 10)

    def test_a_colony_payload_the_caller_mutates_afterwards_is_logged_as_it_was(self):
        payload = {"station_id": 31, "colony": {"population": 100}, "prices": {"wood": {"price": 12}}}
        colony_scan_logger.record_colony_data(payload)
        payload["prices"]["wood"]["price"] = 999         # the UI reuses and edits the live payload
        payload["colony"]["population"] = 1
        self.flush_all()
        row = [r for r in colony_scan_logger.load_colony_scan(self.path("colony_scan.jsonl"))
               if r["source"] == "colony_data"][0]
        self.assertEqual(row["data"]["prices"]["wood"]["price"], 12)
        self.assertEqual(row["data"]["colony"]["population"], 100)

    def test_a_slow_drive_does_not_slow_the_caller(self):
        import time
        real_open = builtins.open
        slow = threading.Event()

        def sluggish_open(*args, **kwargs):
            if slow.is_set():
                time.sleep(0.4)                 # what the E: drive was doing
            return real_open(*args, **kwargs)

        slow.set()
        with mock.patch.object(builtins, "open", sluggish_open):
            started = time.perf_counter()
            for i in range(20):
                bot_fleet_scan_logger.record_bot_state(
                    800 + i, {"bot_pid": 800 + i, "state": "s%d" % i, "system_name": "A"})
                combat_scan_logger.record_turret_state({"hardpoints": [i]})
                station_log_capture.record_station_log_rows(
                    "st-2", [{"id": "slow%d" % i, "time": "t", "text": "x"}])
            elapsed = time.perf_counter() - started
            slow.clear()                        # let the writers finish quickly
            self.flush_all()
        self.assertLess(elapsed, 0.5, "the caller waited on the (simulated) slow drive")


class AppendedRowsSurviveTheNextRewrite(unittest.TestCase):
    """Found while moving the append off the message thread: _append wrote the file but not the
    cached copy that every rewrite is made from, so the next upsert in the same session deleted
    every turret_state / energy_sample / plugin / mastery row appended since the file was read."""

    def setUp(self):
        combat_scan_logger._last_seen.clear()
        combat_scan_logger._rows_cache.clear()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "combat.jsonl")

    def sources(self):
        return [(r["source"], r["key"]) for r in combat_scan_logger.load_combat_scan(self.path)]

    def test_an_appended_row_is_still_there_after_a_later_upsert(self):
        combat_scan_logger.record_skills_data([{"skill_id": "a", "level": 1}], out_path=self.path)
        combat_scan_logger.record_turret_state({"hardpoints": [1, 2, 3]}, out_path=self.path)
        combat_scan_logger.record_skills_data([{"skill_id": "b", "level": 1}], out_path=self.path)
        self.assertEqual(sorted(self.sources(), key=str),
                         sorted([("skill", "a"), ("skill", "b"), ("turret_state", None)], key=str))

    def test_several_appends_between_upserts_all_survive_in_order(self):
        combat_scan_logger.record_skills_data([{"skill_id": "a", "level": 1}], out_path=self.path)
        for n in range(3):
            combat_scan_logger.record_turret_state({"hardpoints": [n]}, out_path=self.path)
        combat_scan_logger.record_skills_data([{"skill_id": "a", "level": 2}], out_path=self.path)
        turret = [r["data"]["hardpoints"] for r in combat_scan_logger.load_combat_scan(self.path)
                  if r["source"] == "turret_state"]
        self.assertEqual(turret, [[0], [1], [2]])


if __name__ == "__main__":
    unittest.main()

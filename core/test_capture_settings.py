"""capture_settings.py: the switches, their file, and every logger honouring its switch."""
import json
import os
import tempfile
import unittest
from pathlib import Path
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


class _SettingsFixture(unittest.TestCase):
    """Every test gets its own settings file and its own scan_data folder."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.flush)         # runs first: queued saves must finish before the dir goes
        self.settings_file = os.path.join(self._tmp.name, "capture_settings.json")
        self.scan_root = os.path.join(self._tmp.name, "scan_data")
        capture_settings.configure_for_tests(self.settings_file)
        self.addCleanup(capture_settings.configure_for_tests, None)
        scan_paths.set_root_for_tests(self.scan_root)
        self.addCleanup(scan_paths.set_root_for_tests, None)
        bot_fleet_scan_logger._last_seen.clear()
        bot_fleet_scan_logger._last_system.clear()
        combat_scan_logger._last_seen.clear()
        combat_scan_logger._rows_cache.clear()
        colony_scan_logger._last_seen.clear()
        station_log_capture._reset_cache_for_tests()

    def flush(self):
        scan_writer.flush()
        combat_scan_logger.flush()

    def exists(self, name):
        return os.path.exists(os.path.join(self.scan_root, name))


class SettingsFileTests(_SettingsFixture):
    def test_everything_is_on_when_there_is_no_file(self):
        for key in capture_settings.ALL_KEYS:
            self.assertTrue(capture_settings.enabled(key), key)

    def test_the_master_switch_turns_off_every_data_logger_but_not_the_watchdog(self):
        capture_settings.set_enabled(capture_settings.MASTER, False)
        for key in capture_settings.DATA_KEYS:
            self.assertFalse(capture_settings.enabled(key), key)
        self.assertTrue(capture_settings.enabled(capture_settings.WATCHDOG))
        self.assertTrue(capture_settings.snapshot()["bot_fleet"])     # stored value is kept
        capture_settings.set_enabled(capture_settings.MASTER, True)
        self.assertTrue(capture_settings.enabled("bot_fleet"))

    def test_a_switch_is_saved_in_the_background_and_read_back(self):
        capture_settings.set_enabled("combat", False)
        scan_writer.flush()
        stored = json.loads(Path(self.settings_file).read_text(encoding="utf-8"))
        self.assertIs(stored["combat"], False)
        self.assertIs(stored["bot_fleet"], True)
        capture_settings.configure_for_tests(self.settings_file)         # "restart"
        self.assertFalse(capture_settings.enabled("combat"))
        self.assertTrue(capture_settings.enabled("bot_fleet"))

    def test_setting_a_switch_does_not_touch_the_disk_on_the_calling_thread(self):
        import builtins
        import threading
        me, touched = threading.get_ident(), []
        real_open = builtins.open

        def watching_open(*args, **kwargs):
            if threading.get_ident() == me:
                touched.append(args[0])
            return real_open(*args, **kwargs)

        with mock.patch.object(builtins, "open", watching_open):
            capture_settings.set_enabled("station_ids", False)
            capture_settings.toggle("station_ids")
            capture_settings.enabled("station_ids")
        scan_writer.flush()
        self.assertEqual(touched, [])

    def test_a_malformed_or_odd_file_means_defaults_not_a_crash(self):
        for content in ("{not json", "[1, 2]", '{"combat": "no", "bot_fleet": 0, "nonsense": false}'):
            with self.subTest(content=content):
                Path(self.settings_file).write_text(content, encoding="utf-8")
                capture_settings.configure_for_tests(self.settings_file)
                for key in capture_settings.ALL_KEYS:
                    self.assertTrue(capture_settings.enabled(key), key)

    def test_an_unknown_switch_is_refused(self):
        with self.assertRaises(KeyError):
            capture_settings.set_enabled("not_a_logger", False)

    def test_the_watchdog_switch_starts_and_stops_it_live(self):
        with mock.patch("freeze_watchdog.start") as start, mock.patch("freeze_watchdog.stop") as stop:
            capture_settings.set_enabled(capture_settings.WATCHDOG, False)
            stop.assert_called_once()
            capture_settings.set_enabled(capture_settings.WATCHDOG, True)
            start.assert_called_once()

    def test_the_page_lists_every_switch_the_module_knows(self):
        import capture_settings_panel
        listed = [key for key, _label in capture_settings_panel._rows()]
        self.assertEqual(listed, list(capture_settings.ALL_KEYS))


class LoggersHonourTheirSwitch(_SettingsFixture):
    """Each production recorder (out_path=None) writes nothing while its switch is off, and
    starts again when it is back on. A recorder given its own out_path (a test) ignores them."""

    def test_trade_bot_states_and_leg_times_are_separate_switches(self):
        capture_settings.set_enabled("bot_fleet", False)
        bot_fleet_scan_logger.record_bot_state(1, {"bot_pid": 1, "system_name": "A"})
        bot_fleet_scan_logger.record_bot_state(1, {"bot_pid": 1, "system_name": "B"})
        self.flush()
        self.assertFalse(self.exists("bot_fleet_scan.jsonl"))
        self.assertTrue(self.exists("bot_leg_times.jsonl"))              # legs are still on
        capture_settings.set_enabled("bot_fleet", True)
        capture_settings.set_enabled("bot_legs", False)
        bot_fleet_scan_logger.record_bot_state(1, {"bot_pid": 1, "system_name": "B"})
        bot_fleet_scan_logger.record_bot_state(1, {"bot_pid": 1, "system_name": "C"})
        self.flush()
        self.assertEqual([r["key"] for r in bot_fleet_scan_logger.load_bot_fleet_scan(
            os.path.join(self.scan_root, "bot_fleet_scan.jsonl"))], ["1"])
        legs = bot_fleet_scan_logger.load_bot_leg_times(
            os.path.join(self.scan_root, "bot_leg_times.jsonl"))
        self.assertEqual([(l["from_system"], l["to_system"]) for l in legs], [("A", "B")])

    def test_leg_times_do_not_invent_a_leg_across_the_time_they_were_off(self):
        bot_fleet_scan_logger.record_bot_state(2, {"bot_pid": 2, "system_name": "A"})
        capture_settings.set_enabled("bot_legs", False)
        bot_fleet_scan_logger.record_bot_state(2, {"bot_pid": 2, "system_name": "B"})
        bot_fleet_scan_logger.record_bot_state(2, {"bot_pid": 2, "system_name": "C"})
        capture_settings.set_enabled("bot_legs", True)
        bot_fleet_scan_logger.record_bot_state(2, {"bot_pid": 2, "system_name": "C"})
        bot_fleet_scan_logger.record_bot_state(2, {"bot_pid": 2, "system_name": "D"})
        self.flush()
        legs = bot_fleet_scan_logger.load_bot_leg_times(
            os.path.join(self.scan_root, "bot_leg_times.jsonl"))
        self.assertEqual([(l["from_system"], l["to_system"]) for l in legs], [("C", "D")])

    def test_switching_a_logger_back_on_records_the_current_state_not_a_repeat(self):
        state = {"hardpoints": [1, 2, 3]}
        capture_settings.set_enabled("combat", False)
        combat_scan_logger.record_turret_state(state)
        capture_settings.set_enabled("combat", True)
        combat_scan_logger.record_turret_state(state)           # identical payload, never recorded
        self.flush()
        rows = combat_scan_logger.load_combat_scan(os.path.join(self.scan_root, "combat_scan.jsonl"))
        self.assertEqual([r["source"] for r in rows], ["turret_state"])

    def test_switching_colony_capture_back_on_records_the_current_state_not_a_repeat(self):
        payload = {"station_id": 7, "colony": {"population": 5}}
        capture_settings.set_enabled("colony_scan", False)
        colony_scan_logger.record_colony_data(payload)
        capture_settings.set_enabled("colony_scan", True)
        colony_scan_logger.record_colony_data(payload)          # identical payload, never recorded
        self.flush()
        rows = colony_scan_logger.load_colony_scan(os.path.join(self.scan_root, "colony_scan.jsonl"))
        self.assertEqual(sorted(r["source"] for r in rows), ["colony_data", "colony_tick"])

    def test_every_other_logger_has_its_own_switch(self):
        recorders = {
            "combat": ("combat_scan.jsonl", lambda: (
                combat_scan_logger.record_inventory_items([{"item_type": "x", "category": "weapon"}]),
                combat_scan_logger.record_ship_specs({"ship": {"display_name": "V"}}),
                combat_scan_logger.record_ship_plugin_data({"a": 1}),
                combat_scan_logger.record_skills_data([{"skill_id": "s"}]))),
            "station_logs": ("station_logs.csv", lambda: station_log_capture.record_station_log_rows(
                "st", [{"id": "1", "time": "t", "text": "x"}])),
            "planet_scans": ("planet_scans.csv", lambda: scan_logger.record_scan(
                "Sys", {"planet_name": "P", "planet_type": "Terra"})),
            "station_ids": ("station_ids.csv", lambda: station_id_logger.record_stations(
                "Sys", [{"station_id": "7", "name": "Hub"}])),
            "shuttle_routes": ("shuttle_routes.csv", lambda: shuttle_route_capture.record_shuttle_routes(
                "hub", [{"target_station_id": 2, "resource_name": "iron"}])),
            "trade_bot_slots": ("trade_bot_slots.csv", lambda: trade_bot_slots_capture.record_trade_bot_slots(
                "tester", 1, 59)),
            "colony_scan": ("colony_scan.jsonl", lambda: (
                colony_scan_logger.record_colony_data({"station_id": 7, "colony": {"population": 5}}),
                colony_scan_logger.record_station_setup(7, {"modules": {}}),
                colony_scan_logger.record_shuttle_network(7, {"routes": []}))),
        }
        for key, (filename, record) in recorders.items():
            with self.subTest(switch=key):
                capture_settings.set_enabled(key, False)
                record()
                self.flush()
                self.assertFalse(self.exists(filename), "wrote while switched off")
                capture_settings.set_enabled(key, True)
                record()
                self.flush()
                self.assertTrue(self.exists(filename), "did not write once switched back on")

    def test_the_master_switch_silences_all_of_them(self):
        capture_settings.set_enabled(capture_settings.MASTER, False)
        bot_fleet_scan_logger.record_bot_state(3, {"bot_pid": 3, "system_name": "A"})
        combat_scan_logger.record_turret_state({"hardpoints": [9]})
        station_log_capture.record_station_log_rows("st", [{"id": "1", "time": "t", "text": "x"}])
        scan_logger.record_scan("Sys", {"planet_name": "P"})
        trade_bot_slots_capture.record_trade_bot_slots("tester", 1, 59)
        colony_scan_logger.record_colony_data({"station_id": 7, "colony": {"population": 5}})
        self.flush()
        self.assertFalse(os.path.exists(self.scan_root) and os.listdir(self.scan_root))

    def test_a_logger_given_its_own_path_ignores_the_switches(self):
        capture_settings.set_enabled(capture_settings.MASTER, False)
        capture_settings.set_enabled("combat", False)
        path = os.path.join(self._tmp.name, "mine.jsonl")
        combat_scan_logger.record_turret_state({"hardpoints": [1]}, out_path=path)
        self.assertEqual(len(combat_scan_logger.load_combat_scan(path)), 1)
        csv_path = os.path.join(self._tmp.name, "slots.csv")
        trade_bot_slots_capture.record_trade_bot_slots("tester", 2, 59, out_path=csv_path)
        self.assertTrue(os.path.exists(csv_path))
        colony_scan_logger.record_station_setup(7, {"modules": {}}, out_path=os.path.join(self._tmp.name, "c.jsonl"))
        self.assertTrue(os.path.exists(os.path.join(self._tmp.name, "c.jsonl")))


class BotStateIsLoggedAsItWas(_SettingsFixture):
    def test_a_bot_dict_the_client_updates_in_place_is_written_as_it_was_pushed(self):
        live = {"bot_pid": 5, "state": "hauling", "haul_orders": [{"system": "A"}]}
        bot_fleet_scan_logger.record_bot_state(5, live)
        live["haul_orders"][0]["system"] = "CHANGED"        # the next push updates it in place
        live["state"] = "docked"
        self.flush()
        row = bot_fleet_scan_logger.load_bot_fleet_scan(
            os.path.join(self.scan_root, "bot_fleet_scan.jsonl"))[0]
        self.assertEqual(row["data"]["state"], "hauling")
        self.assertEqual(row["data"]["haul_orders"][0]["system"], "A")


if __name__ == "__main__":
    unittest.main()

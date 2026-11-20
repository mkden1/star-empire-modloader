import tempfile
import unittest
from pathlib import Path

import bot_fleet_scan_logger
import scan_writer
from bot_fleet_scan_logger import (load_bot_fleet_scan, load_bot_leg_times,
                                    record_bot_state)


class _ResetsDedupCache(unittest.TestCase):
    def setUp(self):
        bot_fleet_scan_logger._last_seen.clear()
        bot_fleet_scan_logger._last_system.clear()


class RecordBotStateTests(_ResetsDedupCache):
    def test_upserts_keyed_by_bot_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "bot_fleet_scan.jsonl")
            record_bot_state(
                501, {"bot_pid": 501, "ship_type": "heavy_lift_trade_bot",
                      "haul_orders": [{"type": "warp", "system": "Shaula Sound"}]},
                out_path=path)
            scan_writer.flush()
            rows = load_bot_fleet_scan(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["key"], "501")
            self.assertEqual(rows[0]["data"]["ship_type"], "heavy_lift_trade_bot")
            self.assertEqual(rows[0]["data"]["haul_orders"][0]["system"], "Shaula Sound")

    def test_reseeing_the_same_bot_overwrites_rather_than_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "bot_fleet_scan.jsonl")
            record_bot_state(501, {"bot_pid": 501, "state": "following"}, out_path=path)
            record_bot_state(501, {"bot_pid": 501, "state": "hauling"}, out_path=path)
            scan_writer.flush()
            rows = load_bot_fleet_scan(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["data"]["state"], "hauling")

    def test_reseeing_byte_identical_data_does_not_rewrite_the_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "bot_fleet_scan.jsonl")
            record_bot_state(501, {"bot_pid": 501, "state": "following"}, out_path=path)
            scan_writer.flush()
            first_captured_at = load_bot_fleet_scan(path)[0]["captured_at"]
            record_bot_state(501, {"bot_pid": 501, "state": "following"}, out_path=path)
            scan_writer.flush()
            rows = load_bot_fleet_scan(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["captured_at"], first_captured_at)

    def test_missing_bot_pid_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "bot_fleet_scan.jsonl")
            record_bot_state(None, {"state": "following"}, out_path=path)
            scan_writer.flush()
            self.assertEqual(load_bot_fleet_scan(path), [])

    def test_context_carries_the_owning_character(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "bot_fleet_scan.jsonl")
            record_bot_state(501, {"bot_pid": 501}, context={"username": "ExampleAlt2"},
                              out_path=path)
            scan_writer.flush()
            rows = load_bot_fleet_scan(path)
            self.assertEqual(rows[0]["context"]["username"], "ExampleAlt2")

    def test_different_bots_get_separate_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "bot_fleet_scan.jsonl")
            record_bot_state(501, {"bot_pid": 501}, out_path=path)
            record_bot_state(502, {"bot_pid": 502}, out_path=path)
            scan_writer.flush()
            rows = load_bot_fleet_scan(path)
            self.assertEqual({r["key"] for r in rows}, {"501", "502"})

    def test_cargo_and_credits_ticking_alone_does_not_rewrite_the_row(self):
        # bot_cargo_used/credits change almost continuously while a bot is actively hauling, even
        # when nothing about its route/gear/state has changed -- the exact lag bug this dedup
        # exists to avoid, mirroring combat_scan_logger's own current_energy fix.
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "bot_fleet_scan.jsonl")
            record_bot_state(
                501, {"bot_pid": 501, "state": "hauling", "bot_cargo_used": 100, "credits": 50},
                out_path=path)
            scan_writer.flush()
            first_captured_at = load_bot_fleet_scan(path)[0]["captured_at"]
            record_bot_state(
                501, {"bot_pid": 501, "state": "hauling", "bot_cargo_used": 4200, "credits": 900},
                out_path=path)
            scan_writer.flush()
            rows = load_bot_fleet_scan(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["captured_at"], first_captured_at)

    def test_a_real_change_alongside_cargo_still_rewrites_the_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "bot_fleet_scan.jsonl")
            record_bot_state(
                501, {"bot_pid": 501, "state": "hauling", "bot_cargo_used": 100}, out_path=path)
            record_bot_state(
                501, {"bot_pid": 501, "state": "warping", "bot_cargo_used": 4200}, out_path=path)
            scan_writer.flush()
            rows = load_bot_fleet_scan(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["data"]["state"], "warping")
            self.assertEqual(rows[0]["data"]["bot_cargo_used"], 4200)


class LegTimeTests(_ResetsDedupCache):
    def test_first_sighting_of_a_bot_logs_no_leg(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap_path = str(Path(tmp) / "bot_fleet_scan.jsonl")
            leg_path = str(Path(tmp) / "bot_leg_times.jsonl")
            record_bot_state(501, {"bot_pid": 501, "system_name": "Zordoil Frontier"},
                              out_path=snap_path, leg_path=leg_path)
            scan_writer.flush()
            self.assertEqual(load_bot_leg_times(leg_path), [])

    def test_a_real_system_change_logs_one_leg(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap_path = str(Path(tmp) / "bot_fleet_scan.jsonl")
            leg_path = str(Path(tmp) / "bot_leg_times.jsonl")
            record_bot_state(501, {"bot_pid": 501, "system_name": "Zordoil Frontier"},
                              out_path=snap_path, leg_path=leg_path)
            record_bot_state(501, {"bot_pid": 501, "system_name": "Veritate Basin"},
                              out_path=snap_path, leg_path=leg_path)
            scan_writer.flush()
            legs = load_bot_leg_times(leg_path)
            self.assertEqual(len(legs), 1)
            self.assertEqual(legs[0]["from_system"], "Zordoil Frontier")
            self.assertEqual(legs[0]["to_system"], "Veritate Basin")
            self.assertEqual(legs[0]["bot_pid"], "501")

    def test_repeated_pushes_in_the_same_system_log_no_extra_legs(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap_path = str(Path(tmp) / "bot_fleet_scan.jsonl")
            leg_path = str(Path(tmp) / "bot_leg_times.jsonl")
            record_bot_state(501, {"bot_pid": 501, "system_name": "Zordoil Frontier",
                                    "state": "hauling"}, out_path=snap_path, leg_path=leg_path)
            record_bot_state(501, {"bot_pid": 501, "system_name": "Zordoil Frontier",
                                    "state": "warping"}, out_path=snap_path, leg_path=leg_path)
            scan_writer.flush()
            self.assertEqual(load_bot_leg_times(leg_path), [])

    def test_different_bots_track_independent_system_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap_path = str(Path(tmp) / "bot_fleet_scan.jsonl")
            leg_path = str(Path(tmp) / "bot_leg_times.jsonl")
            record_bot_state(501, {"bot_pid": 501, "system_name": "Zordoil Frontier"},
                              out_path=snap_path, leg_path=leg_path)
            record_bot_state(502, {"bot_pid": 502, "system_name": "Veritate Basin"},
                              out_path=snap_path, leg_path=leg_path)
            record_bot_state(501, {"bot_pid": 501, "system_name": "Veritate Basin"},
                              out_path=snap_path, leg_path=leg_path)
            scan_writer.flush()
            legs = load_bot_leg_times(leg_path)
            self.assertEqual(len(legs), 1)
            self.assertEqual(legs[0]["bot_pid"], "501")

    def test_context_is_carried_on_the_leg_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap_path = str(Path(tmp) / "bot_fleet_scan.jsonl")
            leg_path = str(Path(tmp) / "bot_leg_times.jsonl")
            record_bot_state(501, {"bot_pid": 501, "system_name": "Zordoil Frontier"},
                              context={"username": "ExamplePlayer"}, out_path=snap_path, leg_path=leg_path)
            record_bot_state(501, {"bot_pid": 501, "system_name": "Veritate Basin"},
                              context={"username": "ExamplePlayer"}, out_path=snap_path, leg_path=leg_path)
            scan_writer.flush()
            legs = load_bot_leg_times(leg_path)
            self.assertEqual(legs[0]["context"]["username"], "ExamplePlayer")


if __name__ == "__main__":
    unittest.main()

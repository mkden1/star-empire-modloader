import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import capture_settings
import colony_scan_logger
import scan_paths
import scan_writer
from colony_scan_logger import (load_colony_scan, record_colony_data,
                                 record_shuttle_network, record_station_setup)


class _ResetsDedupCache(unittest.TestCase):
    """See combat_scan_logger's own test base for why this matters: _last_seen is a
    module-level, per-process cache not scoped to a single call or file."""

    def setUp(self):
        colony_scan_logger._last_seen.clear()


def _colony_data_rows(path):
    scan_writer.flush()
    return [r for r in load_colony_scan(path) if r["source"] == "colony_data"]


class RecordColonyDataTests(_ResetsDedupCache):
    def test_upserts_keyed_by_station_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "colony_scan.jsonl")
            record_colony_data(
                {"station_id": 2105, "colony": {"population": 1000}},
                out_path=path)
            rows = _colony_data_rows(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["key"], "2105")
            self.assertEqual(rows[0]["data"]["colony"]["population"], 1000)

    def test_reseeing_the_same_station_overwrites_rather_than_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "colony_scan.jsonl")
            record_colony_data(
                {"station_id": 2105, "colony": {"population": 1000}}, out_path=path)
            record_colony_data(
                {"station_id": 2105, "colony": {"population": 1200}}, out_path=path)
            rows = _colony_data_rows(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["data"]["colony"]["population"], 1200)

    def test_reseeing_byte_identical_data_does_not_rewrite_the_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "colony_scan.jsonl")
            record_colony_data(
                {"station_id": 2105, "colony": {"population": 1000}}, out_path=path)
            first_captured_at = _colony_data_rows(path)[0]["captured_at"]
            record_colony_data(
                {"station_id": 2105, "colony": {"population": 1000}}, out_path=path)
            rows = _colony_data_rows(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["captured_at"], first_captured_at)

    def test_missing_station_id_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "colony_scan.jsonl")
            record_colony_data({"colony": {"population": 1000}}, out_path=path)
            scan_writer.flush()
            self.assertEqual(load_colony_scan(path), [])

    def test_context_is_merged_into_the_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "colony_scan.jsonl")
            record_colony_data(
                {"station_id": 2105, "colony": {"population": 1000}},
                context={"system_name": "Shaula Sound", "planet_id": 42},
                out_path=path)
            rows = _colony_data_rows(path)
            self.assertEqual(rows[0]["context"]["system_name"], "Shaula Sound")
            self.assertEqual(rows[0]["context"]["planet_id"], 42)

    def test_different_stations_get_separate_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "colony_scan.jsonl")
            record_colony_data({"station_id": 2105, "colony": {}}, out_path=path)
            record_colony_data({"station_id": 4795, "colony": {}}, out_path=path)
            rows = _colony_data_rows(path)
            self.assertEqual({r["key"] for r in rows}, {"2105", "4795"})

    def test_also_appends_a_colony_tick_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "colony_scan.jsonl")
            record_colony_data(
                {"station_id": 2105,
                 "colony": {"population": 1000, "prev_population": 900,
                            "income_history": [{"items": [
                                {"resource": "rations", "qty": 50, "value": 1750}]}]}},
                out_path=path)
            scan_writer.flush()
            rows = load_colony_scan(path)
            ticks = [r for r in rows if r["source"] == "colony_tick"]
            self.assertEqual(len(ticks), 1)
            self.assertEqual(ticks[0]["key"], "2105")
            self.assertEqual(ticks[0]["data"]["population"], 1000)
            self.assertEqual(ticks[0]["data"]["prev_population"], 900)
            self.assertEqual(
                ticks[0]["data"]["tick_income"]["items"][0]["resource"], "rations")

    def test_two_distinct_ticks_both_append_rather_than_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "colony_scan.jsonl")
            record_colony_data(
                {"station_id": 2105, "colony": {"population": 1000, "prev_population": 900}},
                out_path=path)
            record_colony_data(
                {"station_id": 2105, "colony": {"population": 1050, "prev_population": 1000}},
                out_path=path)
            scan_writer.flush()
            rows = load_colony_scan(path)
            ticks = [r for r in rows if r["source"] == "colony_tick"]
            self.assertEqual(len(ticks), 2)
            self.assertEqual([t["data"]["population"] for t in ticks], [1000, 1050])

    def test_reopening_with_no_tick_change_does_not_append_a_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "colony_scan.jsonl")
            record_colony_data(
                {"station_id": 2105, "colony": {"population": 1000, "prev_population": 900}},
                out_path=path)
            record_colony_data(
                {"station_id": 2105, "colony": {"population": 1000, "prev_population": 900}},
                out_path=path)
            scan_writer.flush()
            rows = load_colony_scan(path)
            ticks = [r for r in rows if r["source"] == "colony_tick"]
            self.assertEqual(len(ticks), 1)


class RecordStationSetupTests(_ResetsDedupCache):
    def test_upserts_keyed_by_station_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "colony_scan.jsonl")
            record_station_setup(
                2105, {"equipped_module_counts": {"colony_hab": 3}, "workers_used": 12},
                out_path=path)
            scan_writer.flush()
            rows = load_colony_scan(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["source"], "station_setup")
            self.assertEqual(rows[0]["data"]["workers_used"], 12)

    def test_missing_station_id_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "colony_scan.jsonl")
            record_station_setup(None, {"workers_used": 12}, out_path=path)
            scan_writer.flush()
            self.assertEqual(load_colony_scan(path), [])

    def test_colony_data_and_station_setup_coexist_for_the_same_station(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "colony_scan.jsonl")
            record_colony_data({"station_id": 2105, "colony": {}}, out_path=path)
            record_station_setup(2105, {"workers_used": 12}, out_path=path)
            scan_writer.flush()
            rows = load_colony_scan(path)
            self.assertEqual({r["source"] for r in rows},
                              {"colony_data", "colony_tick", "station_setup"})

    def test_a_pure_cargo_change_does_not_rewrite_the_row(self):
        # A station's live cargo stock ticking up/down (nothing else about its modules/caps/
        # reserves/workers changed) must NOT trigger a rewrite -- the exact lag bug this dedup
        # exists to avoid, mirroring combat_scan_logger's own current_energy fix.
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "colony_scan.jsonl")
            record_station_setup(
                2105, {"workers_used": 12, "cargo": [{"item_type": "wood", "quantity": 100}]},
                out_path=path)
            scan_writer.flush()
            rows = load_colony_scan(path)
            first_captured_at = rows[0]["captured_at"]
            record_station_setup(
                2105, {"workers_used": 12, "cargo": [{"item_type": "wood", "quantity": 250}]},
                out_path=path)
            scan_writer.flush()
            rows = load_colony_scan(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["captured_at"], first_captured_at)

    def test_a_real_change_alongside_cargo_still_rewrites_the_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "colony_scan.jsonl")
            record_station_setup(
                2105, {"workers_used": 12, "cargo": [{"item_type": "wood", "quantity": 100}]},
                out_path=path)
            record_station_setup(
                2105, {"workers_used": 45, "cargo": [{"item_type": "wood", "quantity": 250}]},
                out_path=path)
            scan_writer.flush()
            rows = load_colony_scan(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["data"]["workers_used"], 45)
            self.assertEqual(rows[0]["data"]["cargo"][0]["quantity"], 250)

    def test_a_pure_credits_change_does_not_rewrite_the_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "colony_scan.jsonl")
            record_station_setup(
                2105, {"workers_used": 12, "station_credits": 1000.0}, out_path=path)
            scan_writer.flush()
            first_captured_at = load_colony_scan(path)[0]["captured_at"]
            record_station_setup(
                2105, {"workers_used": 12, "station_credits": 58200.0}, out_path=path)
            scan_writer.flush()
            rows = load_colony_scan(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["captured_at"], first_captured_at)


def _shuttle_network_rows(path):
    scan_writer.flush()
    return [r for r in load_colony_scan(path) if r["source"] == "shuttle_network"]


def _shuttle_levels_rows(path):
    scan_writer.flush()
    return [r for r in load_colony_scan(path) if r["source"] == "shuttle_levels"]


class RecordShuttleNetworkTests(_ResetsDedupCache):
    def test_upserts_keyed_by_hub_station_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "colony_scan.jsonl")
            record_shuttle_network(
                4795,
                {"routes": [{"id": 1, "resource_id": "wood", "direction": "import"}],
                 "transfer_limit": 250, "transfer_interval_seconds": 10.0},
                out_path=path)
            rows = _shuttle_network_rows(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["key"], "4795")
            self.assertEqual(rows[0]["data"]["transfer_limit"], 250)

    def test_missing_station_id_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "colony_scan.jsonl")
            record_shuttle_network(None, {"routes": []}, out_path=path)
            scan_writer.flush()
            self.assertEqual(load_colony_scan(path), [])

    def test_a_pure_levels_change_does_not_rewrite_the_config_row(self):
        # A route's live source_qty/destination_qty changing on its own (route config otherwise
        # identical) must NOT trigger a rewrite of the (larger) config snapshot -- that's the
        # exact lag bug this dedup exists to avoid. The quantities themselves are still captured,
        # just via the separate shuttle_levels append (see the class below).
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "colony_scan.jsonl")
            record_shuttle_network(
                4795, {"routes": [{"id": 1, "resource_id": "wood"}]}, out_path=path)
            first_captured_at = _shuttle_network_rows(path)[0]["captured_at"]
            record_shuttle_network(
                4795,
                {"routes": [{"id": 1, "resource_id": "wood",
                             "source_qty": 900, "destination_qty": 12}]},
                out_path=path)
            rows = _shuttle_network_rows(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["captured_at"], first_captured_at)
            self.assertNotIn("source_qty", rows[0]["data"]["routes"][0])

    def test_a_real_config_change_still_rewrites_the_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "colony_scan.jsonl")
            record_shuttle_network(
                4795, {"routes": [{"id": 1, "resource_id": "wood"}]}, out_path=path)
            record_shuttle_network(
                4795, {"routes": [{"id": 1, "resource_id": "wood"},
                                   {"id": 2, "resource_id": "silicon"}]}, out_path=path)
            rows = _shuttle_network_rows(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(len(rows[0]["data"]["routes"]), 2)

    def test_reseeing_byte_identical_levels_does_not_rewrite_the_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "colony_scan.jsonl")
            record_shuttle_network(4795, {"routes": [{"id": 1}]}, out_path=path)
            first_captured_at = _shuttle_network_rows(path)[0]["captured_at"]
            record_shuttle_network(4795, {"routes": [{"id": 1}]}, out_path=path)
            rows = _shuttle_network_rows(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["captured_at"], first_captured_at)


class RecordShuttleLevelsSampleTests(_ResetsDedupCache):
    def test_appends_one_sample_per_real_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "colony_scan.jsonl")
            record_shuttle_network(
                4795, {"routes": [{"id": 1, "resource_key": "wood",
                                    "source_qty": 900, "destination_qty": 12}]}, out_path=path)
            record_shuttle_network(
                4795, {"routes": [{"id": 1, "resource_key": "wood",
                                    "source_qty": 850, "destination_qty": 40}]}, out_path=path)
            levels = _shuttle_levels_rows(path)
            self.assertEqual(len(levels), 2)
            self.assertEqual(levels[0]["data"][0]["source_qty"], 900)
            self.assertEqual(levels[1]["data"][0]["source_qty"], 850)

    def test_reseeing_identical_levels_does_not_append_a_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "colony_scan.jsonl")
            record_shuttle_network(
                4795, {"routes": [{"id": 1, "source_qty": 900, "destination_qty": 12}]},
                out_path=path)
            record_shuttle_network(
                4795, {"routes": [{"id": 1, "source_qty": 900, "destination_qty": 12}]},
                out_path=path)
            self.assertEqual(len(_shuttle_levels_rows(path)), 1)


if __name__ == "__main__":
    unittest.main()


class _FakeWindow:
    """Just what the capture_* hooks read off the SolarSystemWindow."""

    def __init__(self, layout="absent", overlay_data=None):
        if layout != "absent":
            self._layout = layout
        self._station_overlay = None if overlay_data is None else {"data": overlay_data}


class _HookFixture(unittest.TestCase):
    def setUp(self):
        colony_scan_logger._last_seen.clear()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # an empty settings file = everything on, whatever this machine's own settings say
        capture_settings.configure_for_tests(os.path.join(self._tmp.name, "capture_settings.json"))
        self.addCleanup(capture_settings.configure_for_tests, None)
        # production-mode calls resolve their file under scan_paths: keep it inside the temp dir
        self.scan_root = os.path.join(self._tmp.name, "scan_data")
        scan_paths.set_root_for_tests(self.scan_root)
        self.addCleanup(scan_paths.set_root_for_tests, None)
        self.addCleanup(scan_writer.flush)          # queued writes finish before the dir goes


class CaptureColonyDataTests(_HookFixture):
    def test_the_context_names_the_open_station_when_the_payload_is_for_it(self):
        win = _FakeWindow({"system_name": "Shaula Sound"},
                          {"attached_station_id": 2105, "station_name": "Cryldorwen - 1 - col",
                           "planet_id": 42})
        data = {"station_id": 2105, "colony": {}}
        with mock.patch.object(colony_scan_logger, "record_colony_data") as record:
            colony_scan_logger.capture_colony_data(win, data)
        record.assert_called_once_with(
            data, {"system_name": "Shaula Sound", "station_name": "Cryldorwen - 1 - col",
                   "planet_id": 42})

    def test_a_payload_for_another_station_gets_only_the_system(self):
        win = _FakeWindow({"system_name": "Shaula Sound"},
                          {"attached_station_id": 1, "station_name": "Other", "planet_id": 9})
        data = {"station_id": 2105, "colony": {}}
        with mock.patch.object(colony_scan_logger, "record_colony_data") as record:
            colony_scan_logger.capture_colony_data(win, data)
        record.assert_called_once_with(data, {"system_name": "Shaula Sound"})

    def test_it_still_records_with_no_overlay_and_no_layout(self):
        data = {"station_id": 2105, "colony": {}}
        with mock.patch.object(colony_scan_logger, "record_colony_data") as record:
            colony_scan_logger.capture_colony_data(_FakeWindow(), data)
        record.assert_called_once_with(data, {"system_name": ""})

    def test_it_does_nothing_while_the_switch_is_off(self):
        capture_settings.set_enabled("colony_scan", False)
        with mock.patch.object(colony_scan_logger, "record_colony_data") as record:
            colony_scan_logger.capture_colony_data(_FakeWindow({"system_name": "S"}), {"station_id": 1})
        record.assert_not_called()


class CaptureStationSetupTests(_HookFixture):
    def test_the_station_id_and_context_come_from_the_overlay(self):
        win = _FakeWindow({"system_name": "Shaula Sound"})
        overlay = {"attached_station_id": 2105, "station_name": "Hub", "planet_id": 42}
        data = {"modules": {}}
        with mock.patch.object(colony_scan_logger, "record_station_setup") as record:
            colony_scan_logger.capture_station_setup(win, overlay, data)
        record.assert_called_once_with(
            2105, data,
            {"system_name": "Shaula Sound", "station_name": "Hub", "planet_id": 42})

    def test_a_jump_gate_is_not_recorded_as_a_station(self):
        overlay = {"attached_station_id": "gate-key", "jump_gate_key": "gate-key"}
        with mock.patch.object(colony_scan_logger, "record_station_setup") as record:
            colony_scan_logger.capture_station_setup(_FakeWindow({"system_name": "S"}), overlay,
                                                     {"cargo": [{"item_key": "fuel"}]})
        record.assert_not_called()

    def test_a_window_with_no_layout_yet_does_not_raise(self):
        overlay = {"attached_station_id": 7, "station_name": "Hub"}
        with mock.patch.object(colony_scan_logger, "record_station_setup") as record:
            colony_scan_logger.capture_station_setup(_FakeWindow(), overlay, {"modules": {}})
        self.assertEqual(record.call_args.args[2]["system_name"], "")

    def test_it_does_nothing_while_the_switch_is_off(self):
        capture_settings.set_enabled("colony_scan", False)
        with mock.patch.object(colony_scan_logger, "record_station_setup") as record:
            colony_scan_logger.capture_station_setup(
                _FakeWindow({"system_name": "S"}), {"attached_station_id": 7}, {"modules": {}})
        record.assert_not_called()


class CaptureShuttleNetworkTests(_HookFixture):
    def test_the_hub_and_system_are_passed_through(self):
        dialog = {"routes": []}
        with mock.patch.object(colony_scan_logger, "record_shuttle_network") as record:
            colony_scan_logger.capture_shuttle_network(
                _FakeWindow({"system_name": "Shaula Sound"}), 2105, dialog)
        record.assert_called_once_with(2105, dialog, {"system_name": "Shaula Sound"})

    def test_it_does_nothing_while_the_switch_is_off(self):
        capture_settings.set_enabled("colony_scan", False)
        with mock.patch.object(colony_scan_logger, "record_shuttle_network") as record:
            colony_scan_logger.capture_shuttle_network(_FakeWindow(), 2105, {"routes": []})
        record.assert_not_called()


class SwitchAndIsolationTests(_HookFixture):
    def test_nothing_is_written_while_the_switch_is_off(self):
        capture_settings.set_enabled("colony_scan", False)
        colony_scan_logger.record_colony_data({"station_id": 5, "colony": {"population": 1}})
        colony_scan_logger.record_station_setup(5, {"modules": {}})
        colony_scan_logger.record_shuttle_network(5, {"routes": []})
        scan_writer.flush()
        self.assertFalse(os.path.exists(os.path.join(self.scan_root, "colony_scan.jsonl")))

    def test_a_call_with_its_own_path_ignores_the_switch(self):
        capture_settings.set_enabled("colony_scan", False)
        path = str(Path(self._tmp.name) / "mine.jsonl")
        record_colony_data({"station_id": 5, "colony": {"population": 1}}, out_path=path)
        self.assertEqual(len(load_colony_scan(path)), 2)         # colony_data + colony_tick

    def test_updating_the_live_payload_in_place_is_noticed_as_a_change(self):
        """The dedup must remember what was written, not the caller's live dict -- else editing
        that dict in place afterwards would make the next push look like a repeat."""
        live = {"modules": {"extractor": 1}, "workers_used": 1}
        colony_scan_logger.record_station_setup(5, live)
        live["modules"]["extractor"] = 2                          # the client edits it in place
        colony_scan_logger.record_station_setup(5, live)          # a real change vs what was seen
        scan_writer.flush()
        rows = [r for r in load_colony_scan(os.path.join(self.scan_root, "colony_scan.jsonl"))
                if r["source"] == "station_setup"]
        self.assertEqual(rows[0]["data"]["modules"]["extractor"], 2)

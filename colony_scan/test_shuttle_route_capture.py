import csv
import os
import tempfile
import unittest

import shuttle_route_capture as src


class ShuttleRouteCaptureTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        os.remove(self.path)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))

    def _rows(self):
        with open(self.path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def test_records_a_route_with_target_station_id(self):
        src.record_shuttle_routes("7", [
            {"target_station_id": 9, "target_station_name": "Londdalen - 2",
             "resource_name": "Wood", "direction": "export", "enabled": True},
        ], out_path=self.path)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["hub_station_id"], "7")
        self.assertEqual(r["target_station_id"], "9")
        self.assertEqual(r["resource"], "Wood")
        self.assertEqual(r["enabled"], "1")

    def test_falls_back_to_lunar_station_id_and_name(self):
        src.record_shuttle_routes("7", [
            {"lunar_station_id": 9, "lunar_station_name": "Londdalen - 2",
             "resource_key": "wood", "direction": "export"},
        ], out_path=self.path)
        r = self._rows()[0]
        self.assertEqual(r["target_station_id"], "9")
        self.assertEqual(r["target_station_name"], "Londdalen - 2")

    def test_route_missing_target_id_is_skipped(self):
        src.record_shuttle_routes("7", [{"resource_name": "Wood"}], out_path=self.path)
        self.assertFalse(os.path.exists(self.path))

    def test_disabled_route_recorded_as_zero(self):
        src.record_shuttle_routes("7", [
            {"target_station_id": 9, "resource_name": "Wood", "enabled": False},
        ], out_path=self.path)
        self.assertEqual(self._rows()[0]["enabled"], "0")

    def test_repeat_route_upserts_rather_than_duplicating(self):
        route = {"target_station_id": 9, "resource_name": "Wood", "enabled": True}
        src.record_shuttle_routes("7", [route], out_path=self.path)
        src.record_shuttle_routes("7", [route], out_path=self.path)
        self.assertEqual(len(self._rows()), 1)

    def test_load_shuttle_target_station_ids(self):
        src.record_shuttle_routes("7", [
            {"target_station_id": 9, "resource_name": "Wood"},
            {"target_station_id": 10, "resource_name": "Silicon"},
        ], out_path=self.path)
        self.assertEqual(src.load_shuttle_target_station_ids(self.path), {"9", "10"})

    def test_load_shuttle_target_station_ids_missing_file_returns_empty_set(self):
        self.assertEqual(src.load_shuttle_target_station_ids("does-not-exist.csv"), set())

    def test_shuttle_data_has_been_captured_reflects_file_existence(self):
        self.assertFalse(src.shuttle_data_has_been_captured(self.path))
        src.record_shuttle_routes("7", [
            {"target_station_id": 9, "resource_name": "Wood"}], out_path=self.path)
        self.assertTrue(src.shuttle_data_has_been_captured(self.path))


if __name__ == "__main__":
    unittest.main()

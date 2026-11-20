import csv
import os
import tempfile
import unittest

import station_log_capture as slc


class StationLogCaptureTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        os.remove(self.path)   # record_station_log_rows must create it fresh
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))
        slc._reset_cache_for_tests()

    def _rows(self, path=None):
        with open(path or self.path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def test_creates_file_with_header_on_first_write(self):
        slc.record_station_log_rows(
            "7", [{"id": "1", "time": 1789272000, "text": "Ver-A4 docked."}],
            out_path=self.path)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], {"station_id": "7", "id": "1",
                                    "time": "1789272000", "text": "Ver-A4 docked."})

    def test_appends_rather_than_rewriting(self):
        slc.record_station_log_rows("7", [{"id": "1", "time": 1, "text": "a"}],
                                     out_path=self.path)
        slc.record_station_log_rows("7", [{"id": "2", "time": 2, "text": "b"}],
                                     out_path=self.path)
        self.assertEqual([r["id"] for r in self._rows()], ["1", "2"])

    def test_repeat_sighting_of_the_same_row_is_not_duplicated(self):
        row = {"id": "1", "time": 1, "text": "Ver-A4 docked."}
        slc.record_station_log_rows("7", [row], out_path=self.path)
        slc.record_station_log_rows("7", [row], out_path=self.path)
        self.assertEqual(len(self._rows()), 1)

    def test_same_id_at_different_stations_is_not_a_collision(self):
        slc.record_station_log_rows("7", [{"id": "1", "time": 1, "text": "a"}],
                                     out_path=self.path)
        slc.record_station_log_rows("9", [{"id": "1", "time": 1, "text": "b"}],
                                     out_path=self.path)
        self.assertEqual(len(self._rows()), 2)

    def test_row_missing_id_is_skipped(self):
        slc.record_station_log_rows("7", [{"time": 1, "text": "no id"}],
                                     out_path=self.path)
        self.assertFalse(os.path.exists(self.path))

    def test_no_write_at_all_when_nothing_new(self):
        slc.record_station_log_rows("7", [{"id": "1", "time": 1, "text": "a"}],
                                     out_path=self.path)
        mtime = os.path.getmtime(self.path)
        slc.record_station_log_rows("7", [{"id": "1", "time": 1, "text": "a"}],
                                     out_path=self.path)
        self.assertEqual(os.path.getmtime(self.path), mtime)

    def test_seen_cache_reloads_when_out_path_changes(self):
        slc.record_station_log_rows("7", [{"id": "1", "time": 1, "text": "a"}],
                                     out_path=self.path)
        fd, other = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        os.remove(other)
        try:
            slc.record_station_log_rows("7", [{"id": "1", "time": 1, "text": "a"}],
                                         out_path=other)
            self.assertEqual(len(self._rows(other)), 1)
        finally:
            if os.path.exists(other):
                os.remove(other)

    def test_load_station_log_rows_round_trips(self):
        slc.record_station_log_rows(
            "7", [{"id": "1", "time": 1, "text": "Ver-A4 docked."}],
            out_path=self.path)
        loaded = slc.load_station_log_rows(self.path)
        self.assertEqual(loaded, [{"station_id": "7", "id": "1",
                                    "time": "1", "text": "Ver-A4 docked."}])

    def test_load_missing_file_returns_empty_list(self):
        self.assertEqual(slc.load_station_log_rows("does-not-exist.csv"), [])


if __name__ == "__main__":
    unittest.main()

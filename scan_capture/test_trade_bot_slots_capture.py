import csv
import os
import tempfile
import unittest

import trade_bot_slots_capture as tbsc


class TradeBotSlotsCaptureTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        os.remove(self.path)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))

    def _rows(self):
        with open(self.path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def test_creates_file_and_records_values(self):
        tbsc.record_trade_bot_slots("ExampleAlt3", 41, 59, out_path=self.path)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["username"], "ExampleAlt3")
        self.assertEqual(rows[0]["slots_used"], "41")
        self.assertEqual(rows[0]["slots_total"], "59")

    def test_repeat_call_upserts_in_place(self):
        tbsc.record_trade_bot_slots("ExampleAlt3", 41, 59, out_path=self.path)
        tbsc.record_trade_bot_slots("ExampleAlt3", 45, 59, out_path=self.path)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["slots_used"], "45")

    def test_multiple_accounts_each_get_their_own_row(self):
        tbsc.record_trade_bot_slots("ExampleAlt3", 41, 59, out_path=self.path)
        tbsc.record_trade_bot_slots("ExamplePlayer", 6, 59, out_path=self.path)
        self.assertEqual({r["username"] for r in self._rows()}, {"ExampleAlt3", "ExamplePlayer"})

    def test_blank_username_is_skipped(self):
        tbsc.record_trade_bot_slots("", 41, 59, out_path=self.path)
        self.assertFalse(os.path.exists(self.path))

    def test_load_round_trips(self):
        tbsc.record_trade_bot_slots("ExampleAlt3", 41, 59, out_path=self.path)
        loaded = tbsc.load_trade_bot_slots(self.path)
        self.assertEqual(loaded["ExampleAlt3"]["slots_used"], 41)
        self.assertEqual(loaded["ExampleAlt3"]["slots_total"], 59)

    def test_load_missing_file_returns_empty_dict(self):
        self.assertEqual(tbsc.load_trade_bot_slots("does-not-exist.csv"), {})


if __name__ == "__main__":
    unittest.main()

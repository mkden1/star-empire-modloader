"""Station Logs: every reply is persisted through station_log_capture, and opening the tab pages through the whole history on its own (tests we added to the dev's test_station_logs_client.py)."""
import unittest
from unittest.mock import patch

import test_station_logs_client as dev


@patch('station_logs_client.record_station_log_rows')
class StationLogsBackfillTests(unittest.TestCase):
    """set_station_logs hands every reply to station_log_capture; the auto-backfill pages on its own."""

    setUp = dev.StationLogsClientTests.setUp

    def reply(self, count=100, more=True, before=None):
        window = self.window
        window.set_station_logs({
            'station_id': 1, 'request_id': window._station_logs['request'],
            'query': window._station_logs['query'],
            'rows': [{'id': str(2000-i), 'time': 1789272000,
                      'text': "Ruski sold 25 A King's Siege Cannon for 5,000 credits."}
                     for i in range(count)],
            'before': before if before is not None else str(2000-count+1),
            'has_more': more})

    def test_reply_is_persisted_via_station_log_capture(self, record):
        w = self.window
        w._station_logs_tick()
        self.reply(count=5)
        record.assert_called_once()
        args = record.call_args.args
        self.assertEqual(args[0], 1)
        self.assertEqual(len(args[1]), 5)

    def test_auto_backfill_pages_through_full_history_without_scrolling(self, _record):
        # The whole point of this session's change: opening a station's Logs
        # tab should capture its FULL history on its own, not just whatever a
        # manual scroll happens to reach.
        w = self.window
        w._station_logs_tick()                        # page 1 request
        self.reply(count=100, more=True, before='1901')
        w._station_logs['sent'] = 0
        w._station_logs_last_sent = 0                  # clear the 0.3s cooldown
        w._station_logs_tick()                         # auto-backfill: page 2
        self.assertEqual(w._send_json.call_count, 2)
        self.assertEqual(w._send_json.call_args.args[1]['before'], '1901')
        self.reply(count=100, more=False, before='1801')
        w._station_logs['sent'] = 0
        w._station_logs_last_sent = 0
        w._station_logs_tick()                         # more=False -- no page 3
        self.assertEqual(w._send_json.call_count, 2)

    def test_auto_backfill_rate_limited_retry_does_not_lose_pagination_progress(self, _record):
        # Regression guard: a due-triggered retry used to always pass
        # reset=True, which would wipe `before`/`rows` if the retry was
        # really just a continuation page deferred by the send cooldown.
        w = self.window
        w._station_logs_tick()
        self.reply(count=100, more=True, before='1901')
        # Auto-backfill fires immediately (within the 0.3s cooldown) -> rate
        # limited -> schedules a due-retry instead of sending.
        w._station_logs_tick()
        self.assertEqual(w._send_json.call_count, 1)
        self.assertEqual(w._station_logs['before'], '1901')
        self.assertTrue(w._station_logs['due'])
        self.assertFalse(w._station_logs['due_reset'])
        # Once the cooldown clears, the due-retry must resume from '1901',
        # not restart from the top.
        with patch('station_logs_client.time.monotonic',
                   return_value=w._station_logs_last_sent + 1):
            w._station_logs_tick()
        self.assertEqual(w._send_json.call_count, 2)
        self.assertEqual(w._send_json.call_args.args[1]['before'], '1901')

    def test_auto_backfill_stops_at_the_page_cap(self, _record):
        w = self.window
        w._station_logs_tick()
        self.reply(count=1, more=True, before='x')
        w._station_logs['auto_pages'] = 200   # already at _AUTO_BACKFILL_PAGE_CAP
        w._station_logs['sent'] = 0
        w._station_logs_last_sent = 0
        w._station_logs_tick()
        self.assertEqual(w._send_json.call_count, 1)   # no further auto page


if __name__ == "__main__":
    unittest.main()

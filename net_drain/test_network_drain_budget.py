"""App._poll_network keeps our wall-clock drain budget (MOD) next to the dev's handler helpers.

The dev's version of the method starts its timer at 0.0 whenever the profiler is off and caps a drain at
512 items; ours bounds a drain by time instead (a backlog is spread over many short calls so the render
thread is not starved), and so needs a REAL start time whether or not the profiler runs. A merge that took
the dev's timer would break every drain at once: nothing would ever be read.  These tests pin that."""
import queue
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import Client


def _app(items, handler=None, profiler=None):
    app = Client.App.__new__(Client.App)
    app._recv_q = queue.Queue()
    for item in items:
        app._recv_q.put(item)
    app._solar_window = SimpleNamespace(_profiler=profiler) if profiler is not None else None
    app.handled = []

    def handle(event, data, t_arrival=None):
        app.handled.append(data)
        if handler is not None:
            handler()

    app._handle_event = handle
    app._arena_network_event = lambda item: False
    app._arena_standby_poll = lambda: None
    return app


class _Profiler:
    def __init__(self, running=True):
        self.is_running = running
        self.records = []

    def record_external(self, name, value):
        self.records.append(name)


class DrainBudgetTests(unittest.TestCase):
    def test_a_short_backlog_is_drained_in_one_call_with_the_profiler_off(self):
        app = _app([("line", n, time.monotonic()) for n in range(50)])
        app._poll_network()
        self.assertEqual(app.handled, list(range(50)))

    def test_a_backlog_longer_than_the_old_item_cap_is_not_cut_at_512(self):
        app = _app([("line", n, time.monotonic()) for n in range(700)])
        app._poll_network()
        self.assertEqual(app.handled, list(range(700)))

    def test_a_slow_backlog_is_spread_over_calls_in_order_and_nothing_is_lost(self):
        app = _app([("line", n, time.monotonic()) for n in range(40)], handler=lambda: time.sleep(0.004))
        with patch.object(Client.App, "_NETWORK_DRAIN_BUDGET_S", 0.02):
            app._poll_network()
            first = len(app.handled)
            self.assertGreater(first, 0)
            self.assertLess(first, 40)
            self.assertEqual(app.handled, list(range(first)))
            for _ in range(60):
                if len(app.handled) == 40:
                    break
                app._poll_network()
        self.assertEqual(app.handled, list(range(40)))

    def test_it_drains_with_the_profiler_running_and_reports_the_drain(self):
        profiler = _Profiler(running=True)
        app = _app([("line", n, time.monotonic()) for n in range(5)], profiler=profiler)
        app._poll_network()
        self.assertEqual(app.handled, list(range(5)))
        self.assertEqual(profiler.records.count("network_queue_wait"), 5)
        self.assertEqual(profiler.records.count("network_drain"), 1)

    def test_a_stopped_profiler_records_nothing_but_still_drains(self):
        profiler = _Profiler(running=False)
        app = _app([("line", n, time.monotonic()) for n in range(5)], profiler=profiler)
        app._poll_network()
        self.assertEqual(app.handled, list(range(5)))
        self.assertEqual(profiler.records, [])

    def test_every_poll_ticks_the_freeze_watchdog_heartbeat(self):
        app = _app([])
        with patch.object(Client, "_watchdog_tick") as tick:
            app._poll_network()
        tick.assert_called_once_with("app")

    def test_control_events_are_skipped_not_counted_as_handled(self):
        app = _app([("line", 1, time.monotonic()), ("arena", 2, time.monotonic()), ("line", 3, time.monotonic())])
        app._arena_network_event = lambda item: item[0] == "arena"
        app._poll_network()
        self.assertEqual(app.handled, [1, 3])


if __name__ == "__main__":
    unittest.main()

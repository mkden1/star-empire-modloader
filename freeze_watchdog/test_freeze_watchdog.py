import tempfile
import threading
import time
import unittest
from unittest import mock

import freeze_watchdog as wd


class FreezeWatchdogTests(unittest.TestCase):
    def setUp(self):
        wd._reset()
        self.addCleanup(wd._reset)
        self.log = tempfile.TemporaryFile("w+")
        self.addCleanup(self.log.close)

    def started(self):
        self.assertTrue(wd.start(self.log))

    def test_tick_is_a_no_op_until_started(self):
        with mock.patch.object(wd.faulthandler, "dump_traceback_later") as arm:
            wd.tick("app", now=1.0)
        arm.assert_not_called()
        self.assertIsNone(wd._last["app"])

    def test_late_tick_is_reported_with_its_length_and_normal_ticks_are_not(self):
        self.started()
        with mock.patch.object(wd.faulthandler, "dump_traceback_later"):
            wd.tick("render", now=10.0)
            with self.assertNoLogs(wd.logger, "WARNING"):
                wd.tick("render", now=10.017)
            with self.assertLogs(wd.logger, "WARNING") as logged:
                wd.tick("render", now=12.5)
        self.assertIn("render thread was stalled for 2.48s", logged.output[0])

    def test_rearming_is_rate_limited(self):
        self.started()
        with mock.patch.object(wd.faulthandler, "dump_traceback_later") as arm:
            for t in (0.0, 0.03, 0.06, 0.12, 0.15, 0.25):
                wd.tick("app", now=t)
        self.assertEqual(arm.call_count, 3)  # 0.0, 0.12, 0.25

    def test_a_stuck_thread_stops_the_other_thread_from_hiding_it(self):
        self.started()
        with mock.patch.object(wd.faulthandler, "dump_traceback_later") as arm:
            wd.tick("app", now=100.0)
            wd.tick("render", now=100.0)
            arm.reset_mock()
            # the render thread goes quiet; the app thread keeps ticking
            wd.tick("app", now=100.4)
            self.assertEqual(arm.call_count, 1)    # render only 0.4s quiet: still healthy
            arm.reset_mock()
            wd.tick("app", now=100.9)
            wd.tick("app", now=101.2)
            arm.assert_not_called()                # render silent >= STALL_S: timer left to fire

    def test_a_thread_that_has_exited_is_not_treated_as_stuck(self):
        self.started()
        with mock.patch.object(wd.faulthandler, "dump_traceback_later") as arm:
            wd.tick("render", now=1.0)
            arm.reset_mock()
            wd.tick("app", now=1.05 + wd.GONE_S)
        self.assertEqual(arm.call_count, 1)

    def test_switching_the_watchdog_off_and_on_does_not_report_the_gap_as_a_stall(self):
        self.started()
        with mock.patch.object(wd.faulthandler, "dump_traceback_later"), \
             mock.patch.object(wd.faulthandler, "cancel_dump_traceback_later"):
            wd.tick("app", now=100.0)
            wd.stop()                                   # the Data Capture page switches it off...
            self.assertTrue(wd.start(self.log))         # ...and on again an hour later
            with self.assertNoLogs(wd.logger, "WARNING"):
                wd.tick("app", now=3700.0)

    def test_start_without_any_file_is_refused(self):
        with mock.patch.object(wd.sys, "stderr", None), \
             mock.patch.dict("sys.modules", {"crash_log": None}):
            self.assertFalse(wd.start())

    def test_real_stall_dumps_every_threads_stack_into_the_file(self):
        # Uses the real faulthandler: a stuck helper thread must appear by name.
        release = threading.Event()

        def deliberately_stuck_here():
            release.wait(5)

        worker = threading.Thread(target=deliberately_stuck_here, daemon=True)
        worker.start()
        self.addCleanup(release.set)
        with mock.patch.object(wd, "STALL_S", 0.2):
            self.started()
            wd.tick("app")          # arms the 0.2 s timer, then we simply stop ticking
            time.sleep(0.7)
        self.log.seek(0)
        dump = self.log.read()
        self.assertIn("Timeout", dump)
        self.assertIn("deliberately_stuck_here", dump)


if __name__ == "__main__":
    unittest.main()

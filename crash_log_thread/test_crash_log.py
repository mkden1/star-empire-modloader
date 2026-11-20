import atexit
import logging
import os
import tempfile
import threading
import time
import unittest
import unittest.mock

import crash_log
import crash_log_thread
import modloader


class _SlowFileHandler(logging.FileHandler):
    """A file handler on a drive that has stalled."""

    def __init__(self, path, delay_s):
        super().__init__(path, encoding="utf-8")
        self.delay_s = delay_s

    def emit(self, record):
        time.sleep(self.delay_s)
        super().emit(record)


class LoggingRunsOffTheCallingThread(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "client.log")
        self.log = logging.getLogger("crash_log_test.%s" % id(self))
        self.log.setLevel(logging.INFO)
        self.log.propagate = False
        self.saved = (crash_log_thread._log_queue, crash_log_thread._log_listener, crash_log_thread._real_handlers)

        def cleanup():
            listener = crash_log_thread._log_listener
            if listener is not None:
                listener.stop()
            atexit.unregister(crash_log_thread.stop_listener)
            routed_handlers = list(crash_log_thread._real_handlers)
            crash_log_thread._log_queue, crash_log_thread._log_listener, crash_log_thread._real_handlers = self.saved
            for h in list(self.log.handlers) + routed_handlers:
                self.log.removeHandler(h)
                h.close()                   # Windows will not delete a log file still open
            self._tmp.cleanup()
        self.addCleanup(cleanup)

    def routed(self, handler):
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        self.assertIsNotNone(crash_log_thread.route_through_queue(self.log, [handler]))

    def lines(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read().splitlines()

    def test_a_stalled_drive_does_not_stall_the_thread_that_logs(self):
        self.routed(_SlowFileHandler(self.path, delay_s=0.3))
        started = time.perf_counter()
        for n in range(5):
            self.log.warning("line %d", n)
        self.assertLess(time.perf_counter() - started, 0.25)    # direct writes would take 1.5 s
        crash_log_thread.drain(timeout=5)
        self.assertEqual(self.lines(), ["WARNING line %d" % n for n in range(5)])

    def test_drain_waits_for_what_is_queued_and_gives_up_on_a_dead_drive(self):
        self.routed(_SlowFileHandler(self.path, delay_s=0.2))
        self.log.error("queued")
        started = time.perf_counter()
        crash_log_thread.drain(timeout=0.05)
        self.assertLess(time.perf_counter() - started, 0.15)    # bounded, a hung disk cannot hang a crash hook
        crash_log_thread.drain(timeout=5)
        self.assertEqual(self.lines(), ["ERROR queued"])

    def test_the_traceback_survives_the_trip_through_the_queue(self):
        self.routed(logging.FileHandler(self.path, encoding="utf-8"))
        try:
            raise ValueError("boom")
        except ValueError:
            self.log.exception("it broke")
        crash_log_thread.drain(timeout=5)
        text = "\n".join(self.lines())
        self.assertIn("ERROR it broke", text)
        self.assertIn("Traceback (most recent call last)", text)
        self.assertIn("ValueError: boom", text)

    def test_records_below_the_handler_level_are_still_filtered(self):
        handler = logging.FileHandler(self.path, encoding="utf-8")
        handler.setLevel(logging.WARNING)
        self.routed(handler)
        self.log.info("quiet")
        self.log.warning("loud")
        crash_log_thread.drain(timeout=5)
        self.assertEqual(self.lines(), ["WARNING loud"])

    def test_stop_at_exit_writes_everything_still_queued(self):
        self.routed(_SlowFileHandler(self.path, delay_s=0.05))
        for n in range(4):
            self.log.warning("late %d", n)
        crash_log_thread.stop_listener()
        self.assertEqual(len(self.lines()), 4)

    def test_nothing_to_drive_means_no_queue(self):
        self.assertIsNone(crash_log_thread.route_through_queue(self.log, []))


class TheDevsCrashLogUsesIt(unittest.TestCase):
    """crash_log.py is the dev's; its patch is a few call sites into crash_log_thread.py."""

    def setUp(self):
        self.addCleanup(setattr, crash_log_thread, "_real_handlers", crash_log_thread._real_handlers)

    def test_a_handler_set_that_cannot_be_routed_falls_back_to_the_direct_handlers(self):
        root = logging.getLogger("crash_log_test.fallback")
        root.propagate = False
        handler = logging.NullHandler()
        with unittest.mock.patch.object(crash_log_thread, "route_through_queue", side_effect=OSError("no thread")):
            crash_log_thread.add_handlers(root, [handler])
        self.addCleanup(root.removeHandler, handler)
        self.assertEqual(root.handlers, [handler])

    def test_add_handlers_hands_them_to_the_background_thread_and_keeps_them_off_the_root(self):
        root = logging.getLogger("crash_log_test.routed")
        root.propagate = False
        saved = (crash_log_thread._log_queue, crash_log_thread._log_listener)
        handler = logging.NullHandler()
        crash_log_thread.add_handlers(root, [handler])

        def cleanup():
            crash_log_thread.stop_listener()
            atexit.unregister(crash_log_thread.stop_listener)
            crash_log_thread._log_queue, crash_log_thread._log_listener = saved
            for h in list(root.handlers):
                root.removeHandler(h)
        self.addCleanup(cleanup)
        self.assertEqual([type(h).__name__ for h in root.handlers], ["QueueHandler"])
        self.assertEqual(crash_log_thread.real_handlers(), [handler])

    def test_the_patched_crash_log_calls_the_module_at_each_of_its_sites(self):
        source = modloader.game_source("crash_log.py")
        self.assertIn("crash_log_thread.add_handlers(root, real_handlers)", source)
        self.assertIn("crash_log_thread.drain()", source)
        self.assertIn("crash_log_thread.real_handlers()", source)
        self.assertIs(crash_log.crash_log_thread, crash_log_thread)
        self.assertNotIn("QueueListener", source)          # the writer lives in the mod's module now


if __name__ == "__main__":
    unittest.main()

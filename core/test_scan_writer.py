import threading
import time
import unittest

import scan_writer


class SubmitAndFlushTests(unittest.TestCase):
    def test_submit_runs_the_function_on_a_background_thread(self):
        result = {}
        done = threading.Event()
        main_thread = threading.current_thread()

        def record():
            result["thread"] = threading.current_thread()
            done.set()

        scan_writer.submit(record)
        self.assertTrue(done.wait(timeout=2.0), "background write never ran")
        self.assertIsNot(result["thread"], main_thread)

    def test_submit_does_not_block_the_caller(self):
        def slow():
            time.sleep(0.3)

        started = time.monotonic()
        scan_writer.submit(slow)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.2, "submit() blocked waiting for the write to finish")

    def test_flush_waits_for_all_pending_writes(self):
        calls = []

        def append(n):
            time.sleep(0.05)
            calls.append(n)

        for i in range(5):
            scan_writer.submit(append, i)
        scan_writer.flush()
        self.assertEqual(sorted(calls), [0, 1, 2, 3, 4])

    def test_writes_execute_in_submission_order(self):
        # Real callers rely on this: a station's later state must not be able to land in the
        # file before an earlier one it was submitted after.
        order = []
        for i in range(10):
            scan_writer.submit(order.append, i)
        scan_writer.flush()
        self.assertEqual(order, list(range(10)))

    def test_an_exception_in_one_write_does_not_stop_later_writes(self):
        calls = []

        def boom():
            raise ValueError("simulated failure")

        scan_writer.submit(boom)
        scan_writer.submit(calls.append, "still ran")
        scan_writer.flush()
        self.assertEqual(calls, ["still ran"])

    def test_flush_with_nothing_submitted_returns_immediately(self):
        # Exercises the lazily-started-thread path in a fresh-ish state; mainly a "doesn't hang"
        # check since other tests in this process will already have started the worker thread.
        scan_writer.flush()


if __name__ == "__main__":
    unittest.main()

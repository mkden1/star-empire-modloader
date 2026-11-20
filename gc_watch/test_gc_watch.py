"""gc_watch: the callback that times collections must be gone before the interpreter tears its modules down.

At exit a big heap still gets a collection while modules are being emptied; the callback then ran with its
module globals set to None and printed "Exception ignored while calling GC callback ... 'NoneType' object has
no attribute 'perf_counter'" (the end of the 2026-11-10 update's test run). These run in a child process, so
the exit handlers they fire never touch the test process.
"""
import pathlib
import subprocess
import sys
import textwrap
import unittest

INTERNAL = pathlib.Path(__file__).parent


def run_child(body):
    script = "import sys\nsys.path.insert(0, %r)\n" % str(INTERNAL) + textwrap.dedent(body)
    done = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    return done


class ExitTests(unittest.TestCase):
    def test_the_callback_is_removed_when_the_exit_handlers_run(self):
        done = run_child("""
            import atexit, gc, gc_watch
            gc_watch.install()
            before = gc_watch._callback in gc.callbacks
            atexit._run_exitfuncs()
            print(before, gc_watch._callback in gc.callbacks)
        """)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.split(), ["True", "False"])

    def test_installing_twice_registers_one_callback_and_one_exit_handler(self):
        done = run_child("""
            import atexit, gc, gc_watch
            handlers_before = atexit._ncallbacks()
            gc_watch.install()
            gc_watch.install()
            print(gc.callbacks.count(gc_watch._callback), atexit._ncallbacks() - handlers_before)
            atexit._run_exitfuncs()
            print(gc.callbacks.count(gc_watch._callback))
        """)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.split(), ["1", "1", "0"])

    def test_a_collection_after_the_exit_handlers_is_not_timed_and_prints_nothing(self):
        done = run_child("""
            import atexit, gc, gc_watch
            gc_watch.install()
            atexit._run_exitfuncs()
            gc_watch.time = None          # what module teardown does to the module's globals
            gc.collect()
            print("count", gc_watch.stats()["collections"])
        """)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.split(), ["count", "0"])
        self.assertNotIn("Exception ignored", done.stderr)

    def test_a_collection_is_still_timed_while_the_process_runs(self):
        done = run_child("""
            import gc, gc_watch
            gc_watch.install()
            gc.collect()
            print(gc_watch.stats()["collections"] >= 1)
        """)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.split(), ["True"])


if __name__ == "__main__":
    unittest.main()

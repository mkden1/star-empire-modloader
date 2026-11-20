import tempfile
import unittest
from pathlib import Path

import scan_file_lock
from scan_file_lock import locked


class LockedTests(unittest.TestCase):
    def test_basic_usage_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "some_file.jsonl")
            with locked(path):
                pass  # just needs to not raise

    def test_sequential_acquisitions_both_succeed(self):
        # The lock must be released on exit so a later, separate acquisition still works --
        # otherwise every caller after the first would hang/timeout forever.
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "some_file.jsonl")
            with locked(path):
                pass
            with locked(path, timeout=0.5):
                pass

    def test_creates_a_sidecar_lock_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "some_file.jsonl")
            with locked(path):
                pass
            self.assertTrue(Path(path + ".lock").exists())

    def test_falls_back_to_a_noop_when_msvcrt_unavailable(self):
        original = scan_file_lock.msvcrt
        scan_file_lock.msvcrt = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = str(Path(tmp) / "some_file.jsonl")
                with locked(path):
                    pass  # must not raise even with no locking backend at all
        finally:
            scan_file_lock.msvcrt = original

    def test_body_exception_still_releases_the_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "some_file.jsonl")
            with self.assertRaises(ValueError):
                with locked(path):
                    raise ValueError("boom")
            # A later acquisition must still succeed -- the lock wasn't left held.
            with locked(path, timeout=0.5):
                pass


if __name__ == "__main__":
    unittest.main()

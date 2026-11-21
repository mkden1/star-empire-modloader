"""sound_prefetch.py: the weapon sound files are read ahead on a background thread, and slow sound
loads are logged.

Background: the first-ever weapon sound is read from disk on the message thread while it holds the
window's lock, and one cold read froze the whole game for 5.4 s (2026-10-22). These tests pin that the
read-ahead happens off the calling thread, has no switch (it is an optimisation: the mod being installed is the
switch), never breaks the audio set-up it hooks, and that the real Audio module is hooked."""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import sound_prefetch


def _join_prefetch_threads(timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        live = [t for t in threading.enumerate() if t.name == "sound-prefetch"]
        if not live:
            return True
        for t in live:
            t.join(0.05)
    return False


class _Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        sound_prefetch.reset_for_tests()
        self.addCleanup(sound_prefetch.reset_for_tests)
        self.folder = Path(self._tmp.name) / "Weapons"
        self.folder.mkdir()
        for name, size in (("a.wav", 100), ("B.WAV", 3000), ("notes.txt", 10), ("c.wav", 2_500_000)):
            (self.folder / name).write_bytes(b"x" * size)

    def spy_reads(self):
        """Patch the file read to record (name, thread) and still do it."""
        calls = []
        real = sound_prefetch._read_through

        def spy(path):
            calls.append((os.path.basename(path), threading.current_thread().name))
            return real(path)

        patcher = mock.patch.object(sound_prefetch, "_read_through", spy)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls


class StartTests(_Fixture):
    def test_every_wav_is_read_on_a_background_thread_and_nothing_else(self):
        calls = self.spy_reads()
        self.assertTrue(sound_prefetch.start(self.folder))
        self.assertTrue(_join_prefetch_threads())
        self.assertEqual(sorted(name for name, _thread in calls), ["B.WAV", "a.wav", "c.wav"])
        self.assertEqual({thread for _name, thread in calls}, {"sound-prefetch"})
        self.assertNotIn("notes.txt", [name for name, _thread in calls])

    def test_start_returns_at_once_while_the_files_are_still_being_read(self):
        gate = threading.Event()
        real = sound_prefetch._read_through
        with mock.patch.object(sound_prefetch, "_read_through", lambda path: (gate.wait(5), real(path))[1]):
            began = time.perf_counter()
            self.assertTrue(sound_prefetch.start(self.folder))
            self.assertLess(time.perf_counter() - began, 0.5)
            self.assertTrue(any(t.name == "sound-prefetch" for t in threading.enumerate()))
            gate.set()
            self.assertTrue(_join_prefetch_threads())

    def test_a_folder_is_read_once_per_session(self):
        calls = self.spy_reads()
        self.assertTrue(sound_prefetch.start(self.folder))
        self.assertTrue(_join_prefetch_threads())
        self.assertFalse(sound_prefetch.start(self.folder))
        self.assertFalse(sound_prefetch.start())
        self.assertTrue(_join_prefetch_threads())
        self.assertEqual(len(calls), 3)

    def test_the_summary_is_logged_and_a_slow_drive_is_called_out(self):
        with self.assertLogs(sound_prefetch.logger, "INFO") as logs:
            sound_prefetch.start(self.folder)
            self.assertTrue(_join_prefetch_threads())
        self.assertTrue(any("read 3 weapon sound(s)" in line and "INFO" in line for line in logs.output),
                        logs.output)
        sound_prefetch.reset_for_tests()
        with mock.patch.object(sound_prefetch, "SLOW_READ_S", -1.0):
            with self.assertLogs(sound_prefetch.logger, "WARNING") as logs:
                sound_prefetch.start(self.folder)
                self.assertTrue(_join_prefetch_threads())
        self.assertTrue(any("the drive was slow" in line for line in logs.output), logs.output)

    def test_a_missing_folder_is_silent_and_harmless(self):
        with self.assertNoLogs(sound_prefetch.logger, "WARNING"):
            self.assertTrue(sound_prefetch.start(Path(self._tmp.name) / "nowhere"))
            self.assertTrue(_join_prefetch_threads())

    def test_one_unreadable_file_does_not_stop_the_others(self):
        calls = []
        real = sound_prefetch._read_through

        def flaky(path):
            calls.append(os.path.basename(path))
            if path.endswith("a.wav"):
                raise PermissionError("locked")
            return real(path)

        with mock.patch.object(sound_prefetch, "_read_through", flaky):
            sound_prefetch.start(self.folder)
            self.assertTrue(_join_prefetch_threads())
        self.assertEqual(sorted(calls), ["B.WAV", "a.wav", "c.wav"])
        self.assertNotIn(str(self.folder / "a.wav"), sound_prefetch._warmed_at)
        self.assertIn(str(self.folder / "c.wav"), sound_prefetch._warmed_at)


class TimedLoadTests(_Fixture):
    class FakeSound:
        def __init__(self, path, delay=0.0, boom=None):
            self._path, self.delay, self.boom = path, delay, boom

        def _load(self):
            time.sleep(self.delay)
            if self.boom:
                raise self.boom
            return "loaded"

    def wrapped(self):
        return sound_prefetch.timed_load(self.FakeSound._load)

    def test_a_fast_load_is_silent_and_returns_what_the_original_returned(self):
        load = self.wrapped()
        with self.assertNoLogs(sound_prefetch.logger, "WARNING"):
            self.assertEqual(load(self.FakeSound(str(self.folder / "a.wav"))), "loaded")

    def test_a_slow_load_is_logged_with_file_size_thread_and_that_it_was_not_read_ahead(self):
        load = self.wrapped()
        with mock.patch.object(sound_prefetch, "SLOW_LOAD_S", 0.01):
            with self.assertLogs(sound_prefetch.logger, "WARNING") as logs:
                load(self.FakeSound(str(self.folder / "B.WAV"), delay=0.03))
        line = logs.output[0]
        self.assertIn("B.WAV", line)
        self.assertIn("3 KB", line)
        self.assertIn(threading.current_thread().name, line)
        self.assertIn("had not been read ahead", line)

    def test_a_slow_load_of_a_file_that_was_read_ahead_says_when(self):
        sound_prefetch._warmed_at[str(self.folder / "a.wav")] = time.monotonic() - 12
        load = self.wrapped()
        with mock.patch.object(sound_prefetch, "SLOW_LOAD_S", 0.01):
            with self.assertLogs(sound_prefetch.logger, "WARNING") as logs:
                load(self.FakeSound(str(self.folder / "a.wav"), delay=0.03))
        self.assertIn("had been read ahead 12s earlier", logs.output[0])

    def test_a_failing_load_still_raises_and_is_still_timed(self):
        load = self.wrapped()
        with mock.patch.object(sound_prefetch, "SLOW_LOAD_S", 0.01):
            with self.assertLogs(sound_prefetch.logger, "WARNING"):
                with self.assertRaises(RuntimeError):
                    load(self.FakeSound(str(self.folder / "a.wav"), delay=0.03, boom=RuntimeError("bad wav")))

    def test_the_description_cannot_break_a_load(self):
        load = self.wrapped()
        with mock.patch.object(sound_prefetch, "SLOW_LOAD_S", 0.0), \
                mock.patch.object(sound_prefetch.os.path, "getsize", side_effect=RuntimeError("x")), \
                self.assertLogs(sound_prefetch.logger, "ERROR"):
            self.assertEqual(load(self.FakeSound(None)), "loaded")


class InstallTests(_Fixture):
    def make_module(self):
        order = []

        class Manager:
            def __init__(self, sounds_dir, extra=None):
                order.append(("init", sounds_dir, extra))

        class Sound:
            def _load(self):
                return 1

        return mock.Mock(AudioManager=Manager, _Sound=Sound), order

    def test_the_read_ahead_starts_before_the_managers_own_set_up_with_the_weapons_folder(self):
        module, order = self.make_module()
        with mock.patch.object(sound_prefetch, "start", lambda d=None: order.append(("start", d))):
            self.assertTrue(sound_prefetch.install(module))
            module.AudioManager(Path("Sounds"), extra=5)
        self.assertEqual(order, [("start", Path("Sounds") / "Weapons"), ("init", Path("Sounds"), 5)])

    def test_the_sounds_dir_may_be_a_keyword_and_a_string(self):
        module, order = self.make_module()
        with mock.patch.object(sound_prefetch, "start", lambda d=None: order.append(("start", d))):
            sound_prefetch.install(module)
            module.AudioManager(sounds_dir="Sounds")
        self.assertEqual(order[0], ("start", Path("Sounds") / "Weapons"))
        self.assertEqual(order[1][0], "init")

    def test_a_failing_read_ahead_cannot_stop_the_audio_starting(self):
        module, order = self.make_module()
        with self.assertLogs(sound_prefetch.logger, "ERROR"):
            with mock.patch.object(sound_prefetch, "_directory", None), \
                    mock.patch.object(sound_prefetch.threading, "Thread", side_effect=RuntimeError("x")):
                sound_prefetch.install(module)
                module.AudioManager(Path("Sounds"))
        self.assertEqual(order, [("init", Path("Sounds"), None)])

    def test_install_hooks_both_methods_once(self):
        module, _order = self.make_module()
        self.assertTrue(sound_prefetch.install(module))
        first = (module.AudioManager.__init__, module._Sound._load)
        self.assertFalse(sound_prefetch.install(module))
        self.assertIs(module.AudioManager.__init__, first[0])
        self.assertIs(module._Sound._load, first[1])
        self.assertTrue(hasattr(first[0], "__wrapped__") and hasattr(first[1], "__wrapped__"))

    def test_the_real_audio_module_is_hooked_by_importing_the_client(self):
        import Audio
        import Client  # noqa: F401  (the install block at the bottom of Client.py runs on import)
        self.assertTrue(hasattr(Audio.AudioManager.__init__, "__wrapped__"))
        self.assertTrue(hasattr(Audio._Sound._load, "__wrapped__"))

    def test_the_real_weapons_folder_has_wavs_to_read(self):
        folder = Path(__file__).resolve().parent / "Sounds" / "Weapons"
        if not folder.is_dir():
            self.skipTest("no Sounds/Weapons in this install")
        calls = self.spy_reads()
        self.assertTrue(sound_prefetch.start(folder))
        self.assertTrue(_join_prefetch_threads())
        self.assertGreater(len(calls), 0)
        self.assertEqual({thread for _name, thread in calls}, {"sound-prefetch"})


if __name__ == "__main__":
    unittest.main()

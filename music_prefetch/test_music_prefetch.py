import modloader
import ast
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import pygame

import music_prefetch


class WarmTests(unittest.TestCase):
    def setUp(self):
        music_prefetch._reset()
        self.addCleanup(music_prefetch._reset)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.track = Path(self._tmp.name) / "Some Track.mp3"
        self.track.write_bytes(os.urandom(3 * (1 << 20) + 17))

    def wait_until_warm(self):
        deadline = time.monotonic() + 5
        while str(self.track) not in music_prefetch._warmed_at and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIn(str(self.track), music_prefetch._warmed_at)

    def test_warm_reads_the_whole_file_on_another_thread_and_returns_at_once(self):
        reads, started_on = [], []
        real_open = open

        def spy_open(path, *args, **kwargs):
            handle = real_open(path, *args, **kwargs)
            if str(path) == str(self.track):
                started_on.append(threading.current_thread().name)
                inner = handle.read

                def read(n=-1):
                    data = inner(n)
                    reads.append(len(data))
                    return data
                handle.read = read
            return handle

        with mock.patch("builtins.open", spy_open):
            t0 = time.perf_counter()
            music_prefetch.warm(self.track)
            self.assertLess(time.perf_counter() - t0, 0.1)
            self.wait_until_warm()
        self.assertEqual(started_on, ["music-prefetch"])
        self.assertEqual(sum(reads), self.track.stat().st_size)

    def test_a_second_warm_while_one_is_running_does_not_start_another(self):
        gate, opened = threading.Event(), []
        real_open = open

        def slow_open(path, *args, **kwargs):
            if str(path) == str(self.track):
                opened.append(path)
                gate.wait(2)
            return real_open(path, *args, **kwargs)

        with mock.patch("builtins.open", slow_open):
            music_prefetch.warm(self.track)
            music_prefetch.warm(self.track)
            time.sleep(0.1)
            gate.set()
            self.wait_until_warm()
        self.assertEqual(len(opened), 1)

    def test_warming_a_missing_file_is_silent(self):
        with self.assertNoLogs(music_prefetch.logger, "WARNING"):
            music_prefetch.warm(Path(self._tmp.name) / "nope.mp3")
            time.sleep(0.1)
        self.assertEqual(music_prefetch._in_flight, set())


class TimedLoadTests(WarmTests):
    def slow(self, seconds=0.05):
        return lambda path: time.sleep(seconds)

    def test_a_slow_load_is_logged_with_its_size_and_whether_it_was_read_ahead(self):
        music_prefetch.warm(self.track)
        self.wait_until_warm()
        with mock.patch.object(music_prefetch, "SLOW_LOAD_S", 0.02), \
             self.assertLogs(music_prefetch.logger, "WARNING") as logged:
            music_prefetch.timed_load(self.slow())(str(self.track))
        self.assertIn("Some Track.mp3", logged.output[0])
        self.assertIn("3.0 MB", logged.output[0])
        self.assertIn("had been read", logged.output[0])

    def test_a_slow_load_of_a_track_never_read_ahead_says_so(self):
        with mock.patch.object(music_prefetch, "SLOW_LOAD_S", 0.02), \
             self.assertLogs(music_prefetch.logger, "WARNING") as logged:
            music_prefetch.timed_load(self.slow())(str(self.track))
        self.assertIn("had not been read ahead", logged.output[0])

    def test_a_fast_load_is_silent_and_passes_its_arguments_through(self):
        seen = []
        wrapped = music_prefetch.timed_load(lambda *a, **k: seen.append((a, k)) or "result")
        with self.assertNoLogs(music_prefetch.logger, "WARNING"):
            self.assertEqual(wrapped(str(self.track), namehint="mp3"), "result")
        self.assertEqual(seen, [((str(self.track),), {"namehint": "mp3"})])

    def test_load_errors_still_reach_the_players_own_handler(self):
        with self.assertRaisesRegex(RuntimeError, "no audio output"):
            music_prefetch.timed_load(
                mock.Mock(side_effect=RuntimeError("no audio output")))(str(self.track))

    def test_a_slow_load_given_its_path_by_keyword_is_still_described(self):
        with mock.patch.object(music_prefetch, "SLOW_LOAD_S", 0.02), \
             self.assertLogs(music_prefetch.logger, "WARNING") as logged:
            music_prefetch.timed_load(lambda path: time.sleep(0.05))(path=str(self.track))
        self.assertIn("Some Track.mp3", logged.output[0])


class FakePlayer:
    """The shape of Client.py's MusicPlayer that install() touches."""
    _playlist = ()
    _track_index = 0
    calls = []

    @classmethod
    def set_gameplay(cls, layout):
        cls.calls.append("set_gameplay")
        cls._playlist = ("a.mp3", "b.mp3", "c.mp3")

    @classmethod
    def _start_next_track(cls):
        cls.calls.append("start")
        cls._track_index = (cls._track_index + 1) % len(cls._playlist)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.player = type("Player", (FakePlayer,), {"calls": []})
        # install() looks the methods up in the class's own __dict__, as on the real MusicPlayer
        self.player.set_gameplay = FakePlayer.__dict__["set_gameplay"]
        self.player._start_next_track = FakePlayer.__dict__["_start_next_track"]
        self.warmed = []
        patcher = mock.patch.object(music_prefetch, "warm", self.warmed.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        # the dev's Audio.music_output, the object the real player loads through
        self.music = mock.Mock()
        self.music.load = mock.Mock()

    def test_the_next_track_is_read_ahead_after_each_start_and_the_original_still_runs(self):
        music_prefetch.install(self.player, self.music)
        self.player.set_gameplay({"system_id": 1})        # picks the playlist, index 0
        self.assertEqual(self.warmed[-1], "a.mp3")
        self.player._start_next_track()                   # index 1 now
        self.assertEqual(self.warmed[-1], "b.mp3")
        self.player._start_next_track()
        self.assertEqual(self.warmed[-1], "c.mp3")
        self.player._start_next_track()                   # wraps round
        self.assertEqual(self.warmed[-1], "a.mp3")
        self.assertEqual(self.player.calls, ["set_gameplay", "start", "start", "start"])

    def test_nothing_is_warmed_without_a_playlist(self):
        music_prefetch.install(self.player, self.music)
        self.player._playlist = ()
        music_prefetch._warm_upcoming(self.player)
        self.assertEqual(self.warmed, [])

    def test_install_is_idempotent_and_wraps_the_music_outputs_loader_once(self):
        original = self.music.load
        music_prefetch.install(self.player, self.music)
        music_prefetch.install(self.player, self.music)
        self.assertIs(self.music.load.__wrapped__, original)
        self.player.set_gameplay({})
        self.assertEqual(len(self.warmed), 1)             # not hooked twice

    def test_the_default_target_is_the_object_the_player_loads_through(self):
        from Audio import music_output
        player = type("Player", (FakePlayer,), {"calls": []})
        player.set_gameplay = FakePlayer.__dict__["set_gameplay"]
        player._start_next_track = FakePlayer.__dict__["_start_next_track"]
        # Importing Client.py already hooked the real object; start from the unhooked one.
        hooked = vars(music_output).pop("load", None)

        def restore():
            vars(music_output).pop("load", None)
            if hooked is not None:
                vars(music_output)["load"] = hooked
        self.addCleanup(restore)
        original = music_output.load                      # a bound method: compare, not identity
        music_prefetch.install(player)
        self.assertEqual(music_output.load.__wrapped__, original)
        self.assertIn("load", vars(music_output))         # the hook sits on the object the player calls

    def test_a_load_through_the_hook_reaches_the_music_output_with_its_path(self):
        music_prefetch.install(self.player, self.music)
        self.music.load("Music/Track.mp3")
        self.music.load.__wrapped__.assert_called_once_with("Music/Track.mp3")


class ClientHookTests(unittest.TestCase):
    """The vanilla MusicPlayer source must stay exactly as shipped (vanilla's own tests exec it
    in a bare namespace); the hooks live outside the class."""

    def test_the_hook_is_one_statement_after_the_class(self):
        source = modloader.game_source("Client.py")
        self.assertIn("music_prefetch.install(MusicPlayer)", source)
        tree = ast.parse(source)
        player = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MusicPlayer")
        self.assertNotIn("music_prefetch", ast.get_source_segment(source, player))
        # the call the hook times: the dev's music output (v0.5.63; pygame.mixer.music before)
        self.assertIn("music_output.load(str(path))", ast.get_source_segment(source, player))


if __name__ == "__main__":
    unittest.main()

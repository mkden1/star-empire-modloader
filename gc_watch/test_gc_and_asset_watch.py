import modloader
import gc
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import asset_watch
import gc_watch


class GcWatchTests(unittest.TestCase):
    def setUp(self):
        gc_watch._reset()
        self.addCleanup(gc_watch._reset)

    def test_a_slow_collection_is_logged_with_its_generation_and_session_totals(self):
        with mock.patch.object(gc_watch, "SLOW_S", 0.01), \
             self.assertLogs(gc_watch.logger, "WARNING") as logged:
            gc_watch._callback("start", {"generation": 2})
            time.sleep(0.03)
            gc_watch._callback("stop", {"generation": 2, "collected": 123, "uncollectable": 0})
        line = logged.output[0]
        self.assertIn("generation-2", line)
        self.assertIn("freed 123", line)
        self.assertIn("1 such pause(s)", line)
        self.assertEqual(gc_watch.stats()["slow"], 1)
        self.assertGreaterEqual(gc_watch.stats()["longest_s"], 0.03)

    def test_a_fast_collection_is_counted_but_not_logged(self):
        with self.assertNoLogs(gc_watch.logger, "WARNING"):
            gc_watch._callback("start", {"generation": 0})
            gc_watch._callback("stop", {"generation": 0, "collected": 0, "uncollectable": 0})
        self.assertEqual(gc_watch.stats()["collections"], 1)
        self.assertEqual(gc_watch.stats()["slow"], 0)

    def test_a_stop_without_a_start_is_ignored(self):
        gc_watch._callback("stop", {"generation": 1})
        self.assertEqual(gc_watch.stats()["collections"], 0)

    def test_installed_it_really_sees_the_interpreters_collections(self):
        before = len(gc.callbacks)
        gc_watch._installed = False
        gc_watch.install()
        gc_watch.install()                          # idempotent
        self.addCleanup(lambda: gc.callbacks.remove(gc_watch._callback))
        self.assertEqual(len(gc.callbacks), before + 1)
        gc.collect()
        self.assertGreaterEqual(gc_watch.stats()["collections"], 1)


class GcFreezeTests(unittest.TestCase):
    def setUp(self):
        gc_watch._reset()
        self.addCleanup(gc_watch._reset)
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("STAR_EMPIRE_NO_GC_FREEZE", None)

    def test_the_freeze_is_scheduled_once_per_process(self):
        with mock.patch.object(gc_watch.threading, "Timer") as timer:
            gc_watch.schedule_freeze()
            gc_watch.schedule_freeze()
        timer.assert_called_once_with(gc_watch.FREEZE_AFTER_S, gc_watch._freeze_now)
        timer.return_value.start.assert_called_once()
        self.assertTrue(timer.return_value.daemon)

    def test_the_environment_switch_turns_it_off(self):
        os.environ["STAR_EMPIRE_NO_GC_FREEZE"] = "1"
        with mock.patch.object(gc_watch.threading, "Timer") as timer:
            gc_watch.schedule_freeze()
        timer.assert_not_called()

    def test_freezing_collects_first_then_freezes_and_says_what_it_did(self):
        order = []
        with mock.patch.object(gc_watch.gc, "collect", lambda *a: order.append("collect") or 0), \
             mock.patch.object(gc_watch.gc, "freeze", lambda: order.append("freeze")), \
             mock.patch.object(gc_watch.gc, "get_freeze_count", return_value=2_500_000), \
             self.assertLogs(gc_watch.logger, "INFO") as logged:
            gc_watch._freeze_now()
        self.assertEqual(order, ["collect", "freeze"])
        self.assertIn("froze 2.50 million objects", logged.output[0])
        self.assertTrue(gc_watch._frozen)
        with mock.patch.object(gc_watch.threading, "Timer") as timer:    # nothing more to schedule
            gc_watch.schedule_freeze()
        timer.assert_not_called()

    def test_a_failing_freeze_is_logged_not_raised(self):
        with mock.patch.object(gc_watch.gc, "collect", side_effect=RuntimeError("boom")), \
             self.assertLogs(gc_watch.logger, "ERROR"):
            gc_watch._freeze_now()
        self.assertFalse(gc_watch._frozen)

    def test_the_countdown_starts_when_the_window_runs_and_the_original_still_runs(self):
        ran = []

        class Window:
            def run(self):
                """doc"""
                ran.append("run")
                return "result"

        gc_watch.install_window(Window)
        gc_watch.install_window(Window)                         # idempotent
        with mock.patch.object(gc_watch, "schedule_freeze") as schedule:
            self.assertEqual(Window().run(), "result")
        schedule.assert_called_once_with()
        self.assertEqual(ran, ["run"])
        self.assertEqual(Window.run.__doc__, "doc")


class FakeWindow:
    def __init__(self, folder):
        self._dir = folder

    def _script_dir(self):
        return self._dir

    def _load_raw_image(self, folder, filename, cache, cache_key=None, warn_missing=False):
        """doc of the real one"""
        key = cache_key if cache_key is not None else filename
        if key in cache:
            return cache[key]
        time.sleep(self.delay)
        cache[key] = ("surface", folder, filename, warn_missing)
        return cache[key]

    delay = 0.0


class AssetWatchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        ships = Path(self._tmp.name) / "Ships"
        ships.mkdir()
        (ships / "viper.png").write_bytes(os.urandom(2 * (1 << 20)))
        self.cls = type("Window", (FakeWindow,), {})
        self.cls._load_raw_image = FakeWindow._load_raw_image
        asset_watch.install(self.cls)
        self.window = self.cls(self._tmp.name)

    def test_a_slow_first_load_names_the_file_its_size_and_the_thread(self):
        self.window.delay = 0.05
        with mock.patch.object(asset_watch, "SLOW_S", 0.02), \
             self.assertLogs(asset_watch.logger, "WARNING") as logged:
            result = self.window._load_raw_image("Ships", "viper.png", {})
        self.assertEqual(result[:3], ("surface", "Ships", "viper.png"))
        self.assertIn("Ships/viper.png", logged.output[0])
        self.assertIn("2.0 MB", logged.output[0])
        self.assertIn("MainThread", logged.output[0])

    def test_a_cache_hit_and_a_fast_load_are_silent(self):
        cache = {}
        with self.assertNoLogs(asset_watch.logger, "WARNING"):
            self.window._load_raw_image("Ships", "viper.png", cache)
            self.window.delay = 5                       # would be slow, but it is cached
            self.window._load_raw_image("Ships", "viper.png", cache)

    def test_keyword_arguments_and_cache_keys_pass_through(self):
        cache = {}
        result = self.window._load_raw_image("Ships", "viper.png", cache,
                                             cache_key="slug", warn_missing=True)
        self.assertEqual(result[3], True)
        self.assertIn("slug", cache)

    def test_install_is_idempotent_and_the_original_is_reachable(self):
        wrapped = self.cls._load_raw_image
        asset_watch.install(self.cls)
        self.assertIs(self.cls._load_raw_image, wrapped)
        self.assertEqual(wrapped.__doc__, "doc of the real one")


class ClientHookTests(unittest.TestCase):
    def test_client_installs_both_after_the_vanilla_code_it_wraps(self):
        source = modloader.game_source("Client.py")
        self.assertIn("gc_watch.install()", source)
        self.assertIn("gc_watch.install_window(SolarSystemWindow)", source)
        self.assertIn("asset_watch.install(SolarSystemWindow)", source)
        self.assertGreater(source.index("asset_watch.install(SolarSystemWindow)"),
                           source.index("class SolarSystemWindow("))


if __name__ == "__main__":
    unittest.main()

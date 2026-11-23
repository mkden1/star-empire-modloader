import modloader
import os
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import pygame

import pump_watch


class PumpWatchTests(unittest.TestCase):
    def setUp(self):
        pump_watch._reset()
        self.addCleanup(pump_watch._reset)

    def fake_pump(self, events, delay_s):
        def pump():
            time.sleep(delay_s)
            return events
        return mock.patch.object(pygame.event, "get", pump)

    def test_a_fast_pump_returns_the_events_and_logs_nothing(self):
        events = [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a)]
        with self.fake_pump(events, 0.0), self.assertNoLogs(pump_watch.logger, "WARNING"):
            self.assertIs(pump_watch.get_events(), events)
        self.assertEqual(pump_watch.stats(), (0, 0.0))

    def test_a_slow_pump_is_logged_with_what_it_returned_and_still_returns_it(self):
        events = [pygame.event.Event(pygame.JOYDEVICEADDED, device_index=0),
                  pygame.event.Event(pygame.WINDOWFOCUSLOST),
                  pygame.event.Event(pygame.WINDOWFOCUSLOST)]
        with mock.patch.object(pump_watch, "SLOW_S", 0.05), \
             mock.patch.object(pump_watch, "_foreground_program", return_value="opera.exe"), \
             self.fake_pump(events, 0.08), \
             self.assertLogs(pump_watch.logger, "WARNING") as logged:
            self.assertIs(pump_watch.get_events(), events)
        line = logged.output[0]
        self.assertIn("pygame.event.get() blocked for 0.0", line)
        self.assertIn("JoyDeviceAdded", line)
        self.assertIn("WindowFocusLost x2", line)
        self.assertIn("opera.exe", line)
        self.assertIn("1 such stall(s)", line)
        self.assertEqual(pump_watch.stats()[0], 1)

    def test_click_diag_is_told_about_the_events_when_that_mod_is_there(self):
        import click_diag
        events = [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a)]
        pump_watch._click_diag = None                       # looked up again
        self.addCleanup(setattr, pump_watch, "_click_diag", None)
        with self.fake_pump(events, 0.0), mock.patch.object(click_diag, "note_events") as note:
            self.assertIs(pump_watch.get_events(), events)
        note.assert_called_once_with(events)

    def test_without_click_diag_the_pump_still_returns_the_events(self):
        """click_diag is optional (pump_watch needs no other mod): not installed, the lookup fails once and is remembered."""
        import builtins
        real_import = builtins.__import__

        def refuse(name, *args, **kwargs):
            if name == "click_diag":
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        events = [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a)]
        pump_watch._click_diag = None
        self.addCleanup(setattr, pump_watch, "_click_diag", None)
        with self.fake_pump(events, 0.0), mock.patch.object(builtins, "__import__", refuse):
            self.assertIs(pump_watch.get_events(), events)
            self.assertIs(pump_watch._click_diag, False)
            self.assertIs(pump_watch.get_events(), events)          # remembered: no second lookup, no error

    def test_the_session_counter_keeps_counting(self):
        with mock.patch.object(pump_watch, "SLOW_S", 0.02), \
             mock.patch.object(pump_watch, "_foreground_program", return_value="?"), \
             self.fake_pump([], 0.03), self.assertLogs(pump_watch.logger, "WARNING") as logged:
            pump_watch.get_events()
            pump_watch.get_events()
        self.assertIn("2 such stall(s)", logged.output[1])

    def test_a_failure_while_describing_a_stall_never_reaches_the_render_loop(self):
        events = [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a)]
        with mock.patch.object(pump_watch, "SLOW_S", 0.0), \
             mock.patch.object(pygame.event, "event_name", side_effect=RuntimeError("boom")), \
             self.fake_pump(events, 0.0), self.assertLogs(pump_watch.logger, "ERROR"):
            self.assertIs(pump_watch.get_events(), events)

    def test_the_foreground_program_is_a_bare_exe_name_or_a_placeholder(self):
        name = pump_watch._foreground_program()
        self.assertIsInstance(name, str)
        self.assertNotIn("\\", name)          # never a path, never a window title


class JoystickTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    def test_disable_joysticks_shuts_the_subsystem_down(self):
        pygame.joystick.init()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STAR_EMPIRE_KEEP_JOYSTICK", None)
            self.assertTrue(pump_watch.disable_joysticks())
        self.assertFalse(pygame.joystick.get_init())

    def test_the_escape_hatch_leaves_it_running(self):
        pygame.joystick.init()
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_KEEP_JOYSTICK": "1"}):
            self.assertFalse(pump_watch.disable_joysticks())
        self.assertTrue(pygame.joystick.get_init())
        pygame.joystick.quit()


class HookTests(unittest.TestCase):
    def test_the_render_loop_uses_the_timed_pump(self):
        from pathlib import Path
        source = modloader.game_source("Client.py")
        self.assertIn("_frame_events = pump_watch.get_events()", source)
        self.assertNotIn("pygame.event.get()", source.split("class SolarSystemWindow(")[1])

    def test_the_init_hook_is_installed_at_import_and_start_is_left_as_shipped(self):
        from pathlib import Path
        source = modloader.game_source("Client.py")
        self.assertIn("\npump_watch.install_init_hook()\n", source)
        # start() still calls plain pygame.init() (the hook wraps pygame.init itself) and nothing of ours is
        # injected there. The dev edits around it (v0.5.58 sets an SDL hint first, v0.5.60 changed the comment
        # after it), so pin the call within start()'s head, not its neighbours.
        # (v0.5.88 gave it a return annotation, `def start(self) -> bool:`, so only the name and `self` are pinned.)
        import re
        found = re.search(r"    def start\(self\)[^\n]*:\n", source)
        self.assertIsNotNone(found, "App.start() moved or was renamed")
        head = source[found.end():].split("pygame.display.set_mode(", 1)[0]
        self.assertIn("        pygame.init()\n", head)
        self.assertNotIn("pump_watch", head)


class InitHookTests(unittest.TestCase):
    def test_pygame_init_shuts_the_joystick_down_after_the_real_init_and_returns_its_result(self):
        calls = []

        def fake_init():                      # a plain function: a Mock would fake the hook marker
            calls.append("init")
            return (5, 0)

        with mock.patch.object(pygame, "init", fake_init), \
             mock.patch.object(pump_watch, "disable_joysticks",
                               side_effect=lambda: calls.append("disable") or True):
            pump_watch.install_init_hook()
            pump_watch.install_init_hook()               # idempotent: wraps once
            self.assertEqual(pygame.init(), (5, 0))
        self.assertEqual(calls, ["init", "disable"])

    def test_a_real_init_leaves_no_joystick_subsystem(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("STAR_EMPIRE_KEEP_JOYSTICK", None)
            pump_watch.install_init_hook()
            pygame.joystick.init()                       # as a plain pygame.init() would have
            pygame.init()
            self.assertFalse(pygame.joystick.get_init())
            self.assertTrue(pygame.display.get_init())   # everything else is still up


if __name__ == "__main__":
    unittest.main()

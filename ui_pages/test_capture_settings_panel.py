"""The ESC menu's "Data Capture" page: drawn by capture_settings_panel, reached from the ESC menu."""
import modloader
import ast
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame

import capture_settings
import capture_settings_panel
import scan_writer
from render_mixin import RenderMixin

SCREEN = (1280, 720)


def make_window(**flags):
    window = RenderMixin()
    window._s = round
    window._F = lambda size, bold=False: pygame.font.SysFont("consolas", size, bold=bold)
    window._cached_label = lambda text, color, font=None: (
        font or window._F(13)).render(text, True, color)
    window._esc_btn_rects = {}
    window._opt_rects = {}
    window._esc_menu_open = False
    window._options_open = False
    window._ui_panels_settings_open = False
    window._capture_settings_open = False
    window._mods_page_open = False
    for name, value in flags.items():
        setattr(window, name, value)
    return window


class _Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.font.init()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        capture_settings.configure_for_tests(os.path.join(self._tmp.name, "capture_settings.json"))
        self.addCleanup(capture_settings.configure_for_tests, None)
        self.addCleanup(scan_writer.flush)                 # settings saves finish before cleanup
        self.screen = pygame.Surface(SCREEN, pygame.SRCALPHA)

    def draw_menu(self, window):
        window._draw_esc_menu(SimpleNamespace(screen=self.screen))


class EscMenuEntryTests(_Fixture):
    def test_the_esc_menu_has_a_data_capture_button_that_fits_the_default_screen(self):
        window = make_window(_esc_menu_open=True)
        self.draw_menu(window)
        rects = window._esc_btn_rects
        self.assertEqual({"panel", "options", "capture", "mods", "redeem", "exit"}, set(rects))
        for key, rect in rects.items():
            with self.subTest(button=key):
                self.assertTrue(self.screen.get_rect().contains(rect))
                self.assertTrue(rects["panel"].contains(rect))
        order = sorted((r.y, k) for k, r in rects.items() if k != "panel")
        self.assertEqual([k for _y, k in order], ["options", "capture", "mods", "redeem", "exit"])


class PageTests(_Fixture):
    def open_page(self):
        window = make_window(_ui_panels_settings_open=True, _capture_settings_open=True)
        self.draw_menu(window)
        return window

    def test_the_page_draws_inside_the_screen_with_a_button_per_switch(self):
        window = self.open_page()
        rects = window._capture_rects
        rows = {name[4:] for name in rects if name.startswith("row:")}
        self.assertEqual(rows, set(capture_settings.ALL_KEYS))
        for name, rect in rects.items():
            with self.subTest(control=name):
                self.assertTrue(self.screen.get_rect().contains(rect))
        self.assertTrue(self.screen.get_bounding_rect().width > 0)

    def test_the_page_fits_at_the_largest_ui_scale(self):
        window = make_window(_ui_panels_settings_open=True, _capture_settings_open=True)
        window._s = lambda px: round(px * 1.5)
        self.draw_menu(window)
        panel = window._capture_rects["panel"]
        self.assertGreaterEqual(panel.top, 0)
        self.assertLessEqual(panel.bottom, SCREEN[1] + 80)    # 1.5x grows the panel; it stays on screen
        for name, rect in window._capture_rects.items():
            self.assertTrue(panel.contains(rect), name)

    def test_clicking_a_row_flips_that_switch_and_only_that_one(self):
        window = self.open_page()
        capture_settings_panel.click(window, window._capture_rects["row:combat"].center)
        self.assertFalse(capture_settings.enabled("combat"))
        self.assertTrue(capture_settings.enabled("bot_fleet"))
        self.assertTrue(window._capture_settings_open)           # the page stays open
        capture_settings_panel.click(window, window._capture_rects["row:combat"].center)
        self.assertTrue(capture_settings.enabled("combat"))

    def test_a_row_reads_on_off_and_flags_the_master_override(self):
        window = self.open_page()
        capture_settings.set_enabled("combat", False)
        capture_settings.set_enabled(capture_settings.MASTER, False)
        before = self.screen.copy()
        self.draw_menu(window)
        self.assertNotEqual(pygame.image.tobytes(before, "RGBA"),
                            pygame.image.tobytes(self.screen, "RGBA"))

    def test_close_button_click_outside_and_escape_state_all_leave_both_flags_clear(self):
        for where in ("close", "outside"):
            with self.subTest(where=where):
                window = self.open_page()
                if where == "close":
                    pos = window._capture_rects["close"].center
                else:
                    pos = (2, 2)
                capture_settings_panel.click(window, pos)
                self.assertFalse(window._capture_settings_open)
                self.assertFalse(window._ui_panels_settings_open)

    def test_a_click_on_blank_panel_space_does_nothing(self):
        window = self.open_page()
        panel = window._capture_rects["panel"]
        capture_settings_panel.click(window, (panel.centerx, panel.top + 3))
        self.assertTrue(window._capture_settings_open)
        self.assertEqual(capture_settings.snapshot(),
                         {k: True for k in capture_settings.ALL_KEYS})

    def test_the_ui_panels_page_still_draws_when_the_capture_flag_is_off(self):
        window = make_window(_ui_panels_settings_open=True)
        window._panels = {}
        window._chat_panel = {}
        window._mm_open = window._coalition_open = window._friends_open = False
        window._combat_log_open = False
        self.draw_menu(window)
        self.assertIn("uip_close", window._opt_rects)
        self.assertFalse(hasattr(window, "_capture_rects"))


class OnlyInstalledModsTests(_Fixture):
    """A player who did not pick a recorder is not shown its options."""

    def rows_with(self, mods):
        with mock.patch.object(capture_settings, "installed_mods", return_value=mods):
            return [key for key, _label in capture_settings_panel._rows()]

    def test_only_the_switches_of_mods_in_effect_are_listed(self):
        self.assertEqual(self.rows_with({"click_diag"}), ["click_diag"])                 # no master: nothing records data
        self.assertEqual(self.rows_with({"scan_capture", "freeze_watchdog"}),
                         [capture_settings.MASTER, "bot_fleet", "bot_legs", "combat", "planet_scans", "station_ids",
                          "trade_bot_slots", "freeze_watchdog"])
        self.assertEqual(self.rows_with({"station_profile", "sound_prefetch"}),
                         [capture_settings.MASTER, "colony_scan", "sound_prefetch"])

    def test_everything_is_listed_when_no_mod_loader_is_running_or_every_mod_is_in_effect(self):
        self.assertEqual(self.rows_with(None), list(capture_settings.ALL_KEYS))
        every = {owner for owners in capture_settings.OWNERS.values() for owner in owners}
        self.assertEqual(self.rows_with(every), list(capture_settings.ALL_KEYS))

    def test_a_footnote_is_shown_only_with_its_row(self):
        with mock.patch.object(capture_settings, "installed_mods", return_value={"scan_capture"}):
            self.assertTrue(any("galaxy map" in line for line in capture_settings_panel._note_lines()))
        with mock.patch.object(capture_settings, "installed_mods", return_value={"station_logs"}):
            self.assertEqual(capture_settings_panel._note_lines(), [])

    def test_with_none_of_those_mods_the_page_says_so_and_still_draws_and_closes(self):
        with mock.patch.object(capture_settings, "installed_mods", return_value=set()):
            self.assertEqual(capture_settings_panel._rows(), [])
            self.assertIn("No mod that records data", capture_settings_panel._note_lines()[0])
            window = make_window(_ui_panels_settings_open=True, _capture_settings_open=True)
            self.draw_menu(window)
            rects = window._capture_rects
            self.assertEqual([name for name in rects if name.startswith("row:")], [])
            self.assertTrue(self.screen.get_rect().contains(rects["panel"]))
            capture_settings_panel.click(window, rects["close"].center)
            self.assertFalse(window._capture_settings_open)

    def test_the_page_only_has_buttons_for_the_listed_switches(self):
        with mock.patch.object(capture_settings, "installed_mods", return_value={"station_logs", "click_diag"}):
            window = make_window(_ui_panels_settings_open=True, _capture_settings_open=True)
            self.draw_menu(window)
            rows = {name[4:] for name in window._capture_rects if name.startswith("row:")}
        self.assertEqual(rows, {capture_settings.MASTER, "station_logs", "click_diag"})


class HookTests(unittest.TestCase):
    """Client.py's run loop cannot be stepped from a test, so check the hooks are where the page
    needs them: the ESC key, the ESC-menu button and the click routing all know the page."""

    @classmethod
    def setUpClass(cls):
        cls.source = modloader.game_source("Client.py")
        ast.parse(cls.source)

    def test_the_window_initialises_the_flag(self):
        self.assertIn("self._capture_settings_open: bool = False", self.source)

    def test_the_esc_key_closes_the_page_with_the_ui_panels_flag(self):
        self.assertRegex(self.source, r"self\._ui_panels_settings_open = False\n\s+"
                                      r"self\._capture_settings_open = False")

    def test_the_esc_menu_button_opens_it_and_clicks_are_routed_to_the_module(self):
        self.assertIn("ui_panels_page.esc_click(self, _ep)", self.source)         # the loop's hook for the ESC menu button
        self.assertIn("ui_panels_page.click(self, event.pos)", self.source)     # the loop's one hook for every ESC page
        import inspect
        import ui_panels_page
        self.assertIn('rects.get("capture")', inspect.getsource(ui_panels_page.esc_click))
        self.assertIn("self._capture_settings_open = True", inspect.getsource(ui_panels_page.esc_click))
        self.assertIn("capture_settings_panel.click(self, _op)", inspect.getsource(ui_panels_page.click))

    def test_the_watchdog_is_started_only_if_its_switch_is_on(self):
        self.assertIn('if capture_settings.enabled("freeze_watchdog"):', self.source)


if __name__ == "__main__":
    unittest.main()

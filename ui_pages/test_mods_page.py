"""mods_page.py: the ESC menu's Mods page - what it lists, how it draws, what a click does, the hooks that reach it."""
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame

import modloader
import mods_page
from modloader import BuildResult, Hunk, Installed, Mod
from test_capture_settings_panel import make_window

SCREEN = (1280, 720)


def hunk(file="Client.py"):
    return Hunk("m", file, "x", ("a",), (), ("b",), (), "mods/m/x", 0)


def fake_installed(count=0, *, failed=None, skipped=(), libraries=(), env_off=False):
    """An Installed with ``count`` ordinary mods (mod_00 ...), plus named failed / skipped / library ones."""
    mods, applied = [], []
    for i in range(count):
        mod = Mod(f"mod_{i:02d}", Path("m"), description=f"does thing number {i}")
        mod.hunks = [hunk(), hunk("render_mixin.py")]
        mods.append(mod)
        applied.append(mod.name)
    for name, reason in (failed or {}).items():
        mod = Mod(name, Path("m"), description="a mod whose anchor moved", requires=("mod_00",))
        mod.hunks = [hunk()]
        mods.append(mod)
    for name in skipped:
        mod = Mod(name, Path("m"), description="turned off")
        mod.hunks = [hunk()]
        mods.append(mod)
    for name in libraries:
        mods.append(Mod(name, Path("m"), description="shared code"))
        applied.append(name)
    result = BuildResult(applied=applied, failed=dict({n: [r] for n, r in (failed or {}).items()}),
                         skipped=list(skipped))
    return Installed(result, Path("."), None, None, mods, env_off)


class EntriesTests(unittest.TestCase):
    def test_each_mod_gets_the_status_the_loader_gave_it(self):
        info = fake_installed(2, failed={"broken": "Client.py: anchor not found for mods/broken/Client.py.patch:7"},
                              skipped=("quiet",), libraries=("shared",))
        by = {e.name: e for e in mods_page.entries(info)}
        self.assertEqual({n: e.status for n, e in by.items()},
                         {"mod_00": "on", "mod_01": "on", "broken": "failed", "quiet": "skipped", "shared": "library"})
        self.assertIn("anchor not found", by["broken"].reasons[0])
        self.assertEqual(by["broken"].requires, ("mod_00",))
        self.assertEqual(by["mod_00"].patches, "2 hunks in Client, render_mixin")
        self.assertIn("code for other mods", by["shared"].patches)

    def test_a_mod_whose_code_could_not_be_attached_is_shown_as_switched_off_with_the_reason(self):
        info = fake_installed(3)
        with mock.patch.dict(modloader.ATTACH_FAILURES, {"mod_01": "RenderMethods could not be attached to RenderMixin: "
                                                                    "render_mixin no longer defines _GM_STEEL"}):
            by = {e.name: e for e in mods_page.entries(info)}
        self.assertEqual((by["mod_00"].status, by["mod_01"].status), ("on", "failed"))
        self.assertIn("no longer defines _GM_STEEL", by["mod_01"].reasons[0])

    def test_with_the_loader_switched_off_every_mod_says_so(self):
        info = fake_installed(0, skipped=(), libraries=())
        info.mods = [Mod("a", Path("m"), description="d")]
        info.mods[0].hunks = [hunk()]
        info.result = BuildResult()
        (entry,) = mods_page.entries(info)
        self.assertEqual(entry.status, "skipped")
        self.assertIn("STAR_EMPIRE_NO_MODS", entry.reasons[0])

    def test_without_a_loader_there_is_nothing_to_list(self):
        with mock.patch.object(modloader, "installed", return_value=None):
            self.assertEqual(mods_page.entries(), [])
        self.assertIn("did not run", mods_page.summary([]))

    def test_the_summary_counts_and_calls_out_what_was_switched_off(self):
        info = fake_installed(3, failed={"broken": "x"}, skipped=("quiet",), libraries=("shared",))
        text = mods_page.summary(mods_page.entries(info))
        self.assertEqual(text, "4 loaded - 1 SWITCHED OFF - 1 off on purpose")
        self.assertEqual(mods_page.summary(mods_page.entries(fake_installed(2))), "2 loaded")

    def test_the_real_mods_are_all_listed_and_in_effect(self):
        entries = mods_page.entries()
        self.assertEqual([e.name for e in entries], [m.name for m in modloader.load_mods()])
        if os.environ.get(modloader.ENV_OFF) or os.environ.get(modloader.ENV_DISABLE):
            self.skipTest("mods are switched off in this environment")
        self.assertEqual({e.status for e in entries} - {"on", "library"}, set(), [e for e in entries if e.status == "failed"])
        self.assertEqual(next(e for e in entries if e.name == "scan_archive").status, "library")


class DrawTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.font.init()

    def setUp(self):
        self.screen = pygame.Surface(SCREEN, pygame.SRCALPHA)
        self.mouse = (0, 0)
        patcher = mock.patch("pygame.mouse.get_pos", side_effect=lambda: self.mouse)
        patcher.start()
        self.addCleanup(patcher.stop)

    def open_page(self, info, scale=1.0):
        patcher = mock.patch.object(modloader, "installed", return_value=info)
        patcher.start()
        self.addCleanup(patcher.stop)
        window = make_window(_ui_panels_settings_open=True, _mods_page_open=True)
        window._s = lambda px: round(px * scale)
        window._draw_esc_menu(SimpleNamespace(screen=self.screen))
        return window

    def test_every_mod_has_a_cell_inside_the_panel_and_the_screen(self):
        info = fake_installed(39, failed={"broken": "Client.py: anchor not found"}, libraries=("shared",))
        window = self.open_page(info)
        rects = window._mods_page_rects
        cells = {n[5:] for n in rects if n.startswith("cell:")}
        self.assertEqual(cells, {e.name for e in mods_page.entries(info)})
        for name, rect in rects.items():
            with self.subTest(control=name):
                self.assertTrue(self.screen.get_rect().contains(rect))
                if name != "panel":
                    self.assertTrue(rects["panel"].contains(rect))
        self.assertGreater(self.screen.get_bounding_rect().width, 0)

    def test_the_page_fits_at_the_largest_ui_scale_with_the_real_number_of_mods(self):
        info = fake_installed(40)
        window = self.open_page(info, scale=1.5)
        rects = window._mods_page_rects
        self.assertTrue(self.screen.get_rect().contains(rects["panel"]), rects["panel"])
        for name, rect in rects.items():
            with self.subTest(control=name):
                self.assertTrue(rects["panel"].contains(rect))

    def test_far_too_many_mods_for_the_window_still_draw_without_an_error(self):
        window = self.open_page(fake_installed(300), scale=1.5)
        self.assertEqual(len([n for n in window._mods_page_rects if n.startswith("cell:")]), 300)

    def test_the_cells_run_down_each_column(self):
        window = self.open_page(fake_installed(12))
        r = window._mods_page_rects
        self.assertEqual(r["cell:mod_00"].x, r["cell:mod_01"].x)
        self.assertLess(r["cell:mod_00"].y, r["cell:mod_01"].y)
        self.assertGreater(r["cell:mod_11"].x, r["cell:mod_00"].x)

    def test_hovering_a_mod_changes_the_detail_box(self):
        info = fake_installed(5, failed={"broken": "Client.py: anchor not found for mods/broken/Client.py.patch:7"})
        window = self.open_page(info)
        idle = pygame.image.tobytes(self.screen, "RGBA")
        self.mouse = window._mods_page_rects["cell:broken"].center
        self.screen.fill((0, 0, 0, 0))
        window._draw_esc_menu(SimpleNamespace(screen=self.screen))
        self.assertNotEqual(idle, pygame.image.tobytes(self.screen, "RGBA"))

    def test_clicking_a_mod_selects_it_and_it_stays_shown_when_the_pointer_leaves(self):
        window = self.open_page(fake_installed(5))
        mods_page.click(window, window._mods_page_rects["cell:mod_03"].center)
        self.assertEqual(window._mods_page_selected, "mod_03")
        self.assertTrue(window._mods_page_open)
        self.screen.fill((0, 0, 0, 0))
        window._draw_esc_menu(SimpleNamespace(screen=self.screen))
        with_selection = pygame.image.tobytes(self.screen, "RGBA")
        window._mods_page_selected = None
        self.screen.fill((0, 0, 0, 0))
        window._draw_esc_menu(SimpleNamespace(screen=self.screen))
        self.assertNotEqual(with_selection, pygame.image.tobytes(self.screen, "RGBA"))

    def test_close_and_a_click_outside_close_the_page_and_clear_both_flags(self):
        for where in ("close", "outside"):
            with self.subTest(where=where):
                window = self.open_page(fake_installed(3))
                window._mods_page_selected = "mod_01"
                pos = window._mods_page_rects["close"].center if where == "close" else (2, 2)
                mods_page.click(window, pos)
                self.assertFalse(window._mods_page_open)
                self.assertFalse(window._ui_panels_settings_open)
                self.assertIsNone(window._mods_page_selected)

    def test_a_click_inside_the_panel_but_off_the_cells_does_nothing(self):
        window = self.open_page(fake_installed(3))
        panel = window._mods_page_rects["panel"]
        mods_page.click(window, (panel.centerx, panel.bottom - 2))          # inside the panel, below the Close button
        self.assertTrue(window._mods_page_open)

    def test_a_long_reason_and_description_are_wrapped_not_overflowing(self):
        info = fake_installed(0, failed={"broken": "Client.py: anchor not found for mods/broken/Client.py.patch:7 " * 12})
        info.mods[0].description = "a very long description " * 30
        window = self.open_page(info)
        self.mouse = window._mods_page_rects["cell:broken"].center
        self.screen.fill((0, 0, 0, 0))
        window._draw_esc_menu(SimpleNamespace(screen=self.screen))
        panel = window._mods_page_rects["panel"]
        self.assertTrue(self.screen.get_rect().contains(panel))

    def test_with_no_loader_the_page_still_draws(self):
        window = self.open_page(None)
        self.assertEqual({n for n in window._mods_page_rects if n.startswith("cell:")}, set())
        self.assertIn("close", window._mods_page_rects)


class HelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.font.init()

    def test_wrap_keeps_to_the_width_and_ends_a_truncated_text_with_dots(self):
        font = pygame.font.SysFont("consolas", 11)
        lines = mods_page._wrap(font, "word " * 80, 120, 3)
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[-1].endswith("..."))
        self.assertTrue(all(font.size(l)[0] <= 120 + font.size("...")[0] for l in lines))
        self.assertEqual(mods_page._wrap(font, "", 120, 3), [])

    def test_fit_shortens_a_long_name_with_dots(self):
        font = pygame.font.SysFont("consolas", 13)
        self.assertEqual(mods_page._fit(font, "short", 200), "short")
        shortened = mods_page._fit(font, "a_really_long_mod_name_that_cannot_fit", 80)
        self.assertTrue(shortened.endswith("..."))
        self.assertLessEqual(font.size(shortened)[0], 80)


class HookTests(unittest.TestCase):
    """Client.py's loop cannot be stepped from a test: check the hooks are where the page needs them."""

    @classmethod
    def setUpClass(cls):
        cls.client = modloader.game_source("Client.py")
        cls.render = modloader.game_source("render_mixin.py")

    def test_the_window_initialises_the_flag_and_esc_closes_the_page(self):
        self.assertIn("self._mods_page_open: bool = False", self.client)
        self.assertRegex(self.client, r"self\._capture_settings_open = False   # MOD\n\s+"
                                      r"self\._mods_page_open = False   # MOD")

    def test_the_esc_menu_button_opens_it_and_clicks_are_routed_to_the_module(self):
        self.assertIn("ui_panels_page.esc_click(self, _ep)", self.client)
        self.assertIn("ui_panels_page.click(self, event.pos)", self.client)
        import inspect
        import ui_panels_page
        self.assertIn('rects.get("mods")', inspect.getsource(ui_panels_page.esc_click))
        self.assertIn("self._mods_page_open = True", inspect.getsource(ui_panels_page.esc_click))
        self.assertIn("mods_page.click(self, _op)", inspect.getsource(ui_panels_page.click))

    def test_the_esc_menu_has_the_row_and_draws_the_page(self):
        self.assertIn('("mods", "  Mods")', self.render)
        self.assertIn("mods_page.draw(self, screen)", self.render)
        self.assertIn("_BH * 6 + _PAD * 7", self.render)       # the game's 4 buttons (v0.5.88: with Logout) + ours


if __name__ == "__main__":
    unittest.main()

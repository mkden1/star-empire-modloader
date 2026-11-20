"""galaxy_map_render.py / galaxy_map_scans.py: the Survey view's drawing and the VIEW SCANS action are modules of the mod,
attached to the game's classes by one-line hooks (see modloader.attach_methods)."""
import unittest
from unittest import mock

import modloader
import galaxy_map_render
import galaxy_map_scans


def non_dunder(cls):
    return {n: v for n, v in vars(cls).items() if not (n.startswith("__") and n.endswith("__"))}


class RenderMethodsTests(unittest.TestCase):
    def test_render_mixin_has_the_survey_drawing_the_moment_it_is_imported(self):
        import render_mixin
        names = non_dunder(galaxy_map_render.RenderMethods)
        self.assertEqual(set(names), {"_gm_resource_totals", "_gm_survey_selection_surface",
                                      "_gm_draw_survey_selection", "_gm_draw_view_scans_button"})
        for name, value in names.items():
            with self.subTest(method=name):
                self.assertIs(vars(render_mixin.RenderMixin).get(name), value)

    def test_the_drawing_borrows_the_games_palette(self):
        import render_mixin
        for name in galaxy_map_render._BORROWED:
            with self.subTest(name=name):
                self.assertIs(getattr(galaxy_map_render, name), getattr(render_mixin, name))
                self.assertIsNotNone(getattr(galaxy_map_render, name))

    def test_nothing_of_it_is_inlined_in_the_patched_render_mixin_any_more(self):
        source = modloader.game_source("render_mixin.py")
        self.assertNotIn("def _gm_survey_selection_surface", source)
        self.assertNotIn("def _gm_draw_view_scans_button", source)
        self.assertIn("galaxy_map_render.install(RenderMixin)", source)

    def test_the_module_attaches_cleanly(self):
        import render_mixin  # noqa: F401
        self.assertEqual(modloader.attachment_problems(galaxy_map_render), [])


class WindowMethodsTests(unittest.TestCase):
    def test_the_game_window_has_the_scan_dashboard_action(self):
        import Client
        self.assertIs(vars(Client.SolarSystemWindow).get("_gm_open_scan_dashboard"),
                      galaxy_map_scans.WindowMethods._gm_open_scan_dashboard)
        self.assertIn("galaxy_map_scans.install(SolarSystemWindow)", modloader.game_source("Client.py"))
        self.assertNotIn("def _gm_open_scan_dashboard", modloader.game_source("Client.py"))

    def test_the_module_attaches_cleanly_and_imports_what_it_calls(self):
        import Client  # noqa: F401
        self.assertEqual(modloader.attachment_problems(galaxy_map_scans), [])
        archive = galaxy_map_scans.scan_archive()
        self.assertTrue(archive and all(callable(f) for f in archive))        # (export_data, generate_and_open)


class OptionalScanArchiveTests(unittest.TestCase):
    """VIEW SCANS is the only part of the map that needs the scan_archive mod; without it the button is left out."""

    class Win:
        _gm_scan_archive_available = galaxy_map_scans.WindowMethods._gm_scan_archive_available
        _gm_open_scan_dashboard = galaxy_map_scans.WindowMethods._gm_open_scan_dashboard
        _visited_systems = {}
        _galaxy_map_info_system = None

        def _server_elapsed(self):
            return 0.0

    def setUp(self):
        self._saved = galaxy_map_scans._archive

    def tearDown(self):
        galaxy_map_scans._archive = self._saved

    def test_the_archive_is_found_when_the_mod_is_there(self):
        galaxy_map_scans._archive = None
        self.assertTrue(self.Win()._gm_scan_archive_available())

    def test_without_the_mod_the_button_is_off_and_a_click_does_nothing(self):
        import builtins
        galaxy_map_scans._archive = None
        real_import = builtins.__import__

        def refuse(name, *args, **kwargs):
            if name in ("galaxy_export", "scan_dashboard"):
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        with mock.patch.object(builtins, "__import__", refuse):
            win = self.Win()
            self.assertFalse(win._gm_scan_archive_available())
            self.assertIsNone(win._gm_open_scan_dashboard())                  # no error, nothing opened
        galaxy_map_scans._archive = None

    def test_a_click_runs_the_export_before_the_dashboard(self):
        calls = []
        galaxy_map_scans._archive = (lambda *a, **k: calls.append("export"), lambda **k: calls.append("open"))
        self.Win()._gm_open_scan_dashboard()
        self.assertEqual(calls, ["export", "open"])                           # the order the stale-export bug needed

    def test_the_header_layout_asks_whether_the_button_exists(self):
        source = modloader.game_source("render_mixin.py")
        self.assertIn("self._gm_scan_archive_available()", source)
        self.assertIn("if not _windowed and _scans_w:", source)


if __name__ == "__main__":
    unittest.main()

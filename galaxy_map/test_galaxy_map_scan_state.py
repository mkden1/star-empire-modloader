"""The galaxy map's scan state and the Survey view's Ctrl/Cmd-click multi-select (galaxy_map_scans.py): what used to be
blocks of patch text inside Client.py's ``_init_galaxy_map`` and its mouse handling."""
import unittest
from unittest import mock

import pygame

import galaxy_map_scans as gms
import modloader
from Client import SolarSystemWindow


class Window:
    pass


class InitStateTests(unittest.TestCase):
    def test_the_scan_indexes_are_seeded_from_the_scan_files_and_the_survey_state_starts_empty(self):
        sentinels = [object(), object(), object()]
        with mock.patch.object(gms, "load_scanned_planets_by_type", return_value=sentinels[0]), \
                mock.patch.object(gms, "load_scanned_moons_by_system", return_value=sentinels[1]), \
                mock.patch.object(gms, "load_scanned_resources_by_system", return_value=sentinels[2]):
            w = Window()
            gms.init_state(w)
        self.assertIs(w._scanned_by_system, sentinels[0])
        self.assertIs(w._scanned_moons_by_system, sentinels[1])
        self.assertIs(w._scanned_resources_by_system, sentinels[2])
        self.assertEqual(w._gm_survey_selected, set())
        self.assertEqual((w._gm_survey_selection_rect, w._gm_survey_selection_clear_rect, w._gm_view_scans_btn_rect),
                         (None, None, None))

    def test_the_game_window_runs_it_after_its_own_map_init(self):
        self.assertIn(gms.init_state, [fn for _kind, fn in modloader.hooks_on(SolarSystemWindow, "_init_galaxy_map")])
        window = object.__new__(SolarSystemWindow)
        window._layout = {}
        with mock.patch.object(gms, "load_scanned_planets_by_type", return_value={"Sol": {}}), \
                mock.patch.object(gms, "load_scanned_moons_by_system", return_value={}), \
                mock.patch.object(gms, "load_scanned_resources_by_system", return_value={}):
            window._init_galaxy_map()
        self.assertEqual(window._scanned_by_system, {"Sol": {}})
        self.assertTrue(hasattr(window, "_galaxy_map_view_tab_rects"), "the game's own map state is still set up")


class SurveyClickTests(unittest.TestCase):
    def window(self, view="survey", selected=()):
        w = Window()
        w._galaxy_map_view = view
        w._gm_survey_selected = set(selected)
        w._gm_survey_selection_rect = pygame.Rect(10, 10, 100, 50)
        w._gm_survey_selection_clear_rect = pygame.Rect(12, 12, 20, 10)
        w._galaxy_map_node_rects = {"Sol": pygame.Rect(200, 200, 30, 30), "Vega": pygame.Rect(300, 300, 30, 30),
                                    "Unvisited": pygame.Rect(400, 400, 30, 30)}
        w._visited_systems = {"Sol": {}, "Vega": {}}
        w._galaxy_map_dragging = True
        return w

    def test_clear_all_empties_the_selection(self):
        w = self.window(selected=("Sol", "Vega"))
        self.assertTrue(gms.survey_clear_click(w, (15, 15)))
        self.assertEqual(w._gm_survey_selected, set())

    def test_a_click_elsewhere_on_the_overlay_is_absorbed_but_changes_nothing(self):
        w = self.window(selected=("Sol",))
        self.assertTrue(gms.survey_clear_click(w, (80, 40)))
        self.assertEqual(w._gm_survey_selected, {"Sol"})

    def test_a_click_off_the_overlay_or_with_no_overlay_is_not_taken(self):
        w = self.window(selected=("Sol",))
        self.assertFalse(gms.survey_clear_click(w, (500, 500)))
        w._gm_survey_selection_rect = w._gm_survey_selection_clear_rect = None
        self.assertFalse(gms.survey_clear_click(w, (15, 15)))
        self.assertEqual(w._gm_survey_selected, {"Sol"})

    def ctrl_click(self, w, pos, mods=pygame.KMOD_CTRL):
        with mock.patch("pygame.key.get_mods", return_value=mods):
            return gms.survey_ctrl_click(w, pos)

    def test_ctrl_click_toggles_a_visited_system_and_ends_the_map_drag(self):
        w = self.window()
        self.assertTrue(self.ctrl_click(w, (210, 210)))
        self.assertEqual(w._gm_survey_selected, {"Sol"})
        self.assertFalse(w._galaxy_map_dragging)
        self.assertTrue(self.ctrl_click(w, (310, 310), pygame.KMOD_META))        # Cmd on a Mac counts
        self.assertEqual(w._gm_survey_selected, {"Sol", "Vega"})
        self.assertTrue(self.ctrl_click(w, (210, 210)))
        self.assertEqual(w._gm_survey_selected, {"Vega"})

    def test_ctrl_click_needs_the_survey_view_a_modifier_and_a_visited_system(self):
        for label, w, pos, mods in (("another view", self.window("csw"), (210, 210), pygame.KMOD_CTRL),
                                    ("no modifier", self.window(), (210, 210), 0),
                                    ("shift is not ctrl", self.window(), (210, 210), pygame.KMOD_SHIFT),
                                    ("not visited", self.window(), (410, 410), pygame.KMOD_CTRL),
                                    ("empty space", self.window(), (900, 900), pygame.KMOD_CTRL)):
            with self.subTest(label):
                self.assertFalse(self.ctrl_click(w, pos, mods))
                self.assertEqual(w._gm_survey_selected, set())
                self.assertTrue(w._galaxy_map_dragging, "the map drag goes on: the click was not ours")


class WiringTests(unittest.TestCase):
    def test_the_loop_calls_the_modules_functions_and_the_old_blocks_are_gone(self):
        source = modloader.game_source("Client.py")
        for call in ("galaxy_map_scans.survey_clear_click(self, event.pos)", "galaxy_map_scans.survey_ctrl_click(self, event.pos)"):
            with self.subTest(call):
                self.assertEqual(source.count(call), 1)
        for gone in ("_gm_sel_name", "load_scanned_planets_by_type", "self._scanned_by_system: dict"):
            with self.subTest(gone):
                self.assertNotIn(gone, source)

    def test_the_game_file_has_the_module_to_call_it_on(self):
        import Client
        self.assertIs(Client.galaxy_map_scans, gms)


if __name__ == "__main__":
    unittest.main()

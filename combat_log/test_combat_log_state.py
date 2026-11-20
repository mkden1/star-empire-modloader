"""The Combat Log's window state: set up after the game's own init, restored from and saved into options.txt's panel layout.

These used to be blocks of patch text inside Client.py; they are now functions of combat_log.py that modloader hooks
onto the game's methods by name (``HOOKS`` in combat_log.py), so no anchor in the dev's code is involved.
"""
import unittest

import combat_log
import modloader
from Client import SolarSystemWindow


class Bare:
    """What a vanilla test's stand-in window is: an object with nothing set."""


class InitStateTests(unittest.TestCase):
    def test_every_piece_of_state_starts_at_its_default(self):
        w = Bare()
        combat_log.init_state(w)
        self.assertEqual(w._combat_log, [])
        self.assertEqual(w._combat_log_totals, {"dealt": 0.0, "taken": 0.0, "dealt_hits": 0, "taken_hits": 0})
        self.assertIsNone(w._combat_log_session_started_at)
        self.assertTrue(w._combat_log_open)
        self.assertEqual((w._hitsplats, w._combat_log_pending, w._combat_log_pending_seq), ([], {}, 0))
        self.assertEqual((w._combat_log_width, w._combat_log_height), (420, 320))
        self.assertEqual((w._combat_log_pos_x, w._combat_log_pos_y), (None, None))
        self.assertEqual((w._combat_log_dragging, w._combat_log_resizing), (False, False))
        self.assertEqual((w._combat_log_drag_dx, w._combat_log_drag_dy, w._combat_log_scroll), (0, 0, 0))
        for rect in ("panel", "header", "grip", "close", "reset"):
            self.assertIsNone(getattr(w, f"_combat_log_{rect}_rect"), rect)

    def test_two_windows_do_not_share_their_lists(self):
        a, b = Bare(), Bare()
        combat_log.init_state(a)
        combat_log.init_state(b)
        a._combat_log.append({})
        a._combat_log_totals["dealt"] = 5.0
        a._hitsplats.append({})
        a._combat_log_pending[1] = {}
        self.assertEqual((b._combat_log, b._hitsplats, b._combat_log_pending), ([], [], {}))
        self.assertEqual(b._combat_log_totals["dealt"], 0.0)

    def test_the_game_window_runs_it_after_its_own_init_step(self):
        window = object.__new__(SolarSystemWindow)
        self.assertFalse(hasattr(window, "_combat_log"))
        window._init_trails_beams_flashes()
        self.assertEqual(window._combat_log, [])
        self.assertTrue(window._combat_log_open)
        self.assertTrue(hasattr(window, "_hit_flashes"), "the game's own state is still set up")


class LayoutTests(unittest.TestCase):
    def window(self):
        w = Bare()
        combat_log.init_state(w)
        return w

    def test_a_saved_layout_restores_visibility_position_and_size(self):
        w = self.window()
        combat_log.load_layout(w, {"combat_log": {"visible": False, "x": 30, "y": 40, "width": 500, "height": 400}})
        self.assertEqual((w._combat_log_open, w._combat_log_pos_x, w._combat_log_pos_y), (False, 30, 40))
        self.assertEqual((w._combat_log_width, w._combat_log_height), (500, 400))

    def test_size_is_clamped_and_numbers_may_arrive_as_text(self):
        w = self.window()
        combat_log.load_layout(w, {"combat_log": {"x": "7", "y": "8", "width": 5000, "height": 10}})
        self.assertEqual((w._combat_log_pos_x, w._combat_log_pos_y), (7, 8))
        self.assertEqual((w._combat_log_width, w._combat_log_height), (900, 220))
        combat_log.load_layout(w, {"combat_log": {"width": 1, "height": 5000}})
        self.assertEqual((w._combat_log_width, w._combat_log_height), (320, 800))

    def test_older_options_files_and_junk_leave_the_window_as_it_was(self):
        for layout in ({}, {"combat_log": None}, {"combat_log": 5}, {"combat_log": "x"}):
            with self.subTest(layout=layout):
                w = self.window()
                combat_log.load_layout(w, layout)
                self.assertEqual((w._combat_log_open, w._combat_log_width, w._combat_log_pos_x), (True, 420, None))

    def test_a_position_needs_both_coordinates_and_a_bad_one_resets_it(self):
        w = self.window()
        combat_log.load_layout(w, {"combat_log": {"x": 5, "y": None}})
        self.assertEqual((w._combat_log_pos_x, w._combat_log_pos_y), (None, None))
        combat_log.load_layout(w, {"combat_log": {"x": 5, "y": 6}})
        combat_log.load_layout(w, {"combat_log": {"x": "left", "y": 6}})
        self.assertEqual((w._combat_log_pos_x, w._combat_log_pos_y), (None, None))
        combat_log.load_layout(w, {"combat_log": {"width": "wide", "height": 300}})
        self.assertEqual(w._combat_log_width, 420, "a size that is not a number keeps the old one")

    def test_a_bare_window_with_no_state_still_loads_and_saves(self):
        """The dev's stand-in windows (test_reset_interface_layout, test_bot_window_layout) never ran the init."""
        w = Bare()
        combat_log.load_layout(w, {"combat_log": {"visible": True}})
        self.assertEqual(w._combat_log_open, True)
        layout = {}
        combat_log.save_layout(Bare(), layout)
        self.assertEqual(layout, {"combat_log": {"visible": True, "x": None, "y": None, "width": 420, "height": 320}})

    def test_what_is_saved_is_what_loads_back(self):
        w = self.window()
        w._combat_log_open, w._combat_log_pos_x, w._combat_log_pos_y = False, 12, 34
        w._combat_log_width, w._combat_log_height = 640, 410
        layout = {}
        combat_log.save_layout(w, layout)
        other = self.window()
        combat_log.load_layout(other, layout)
        self.assertEqual({k: v for k, v in vars(other).items() if k.startswith("_combat_log_") and "rect" not in k},
                         {k: v for k, v in vars(w).items() if k.startswith("_combat_log_") and "rect" not in k})

    def test_the_game_saves_and_restores_it_through_its_own_layout_methods(self):
        saved = SolarSystemWindow._panel_layout_dict.__wrapped__
        self.assertIsNotNone(saved)
        hooked = {name: [fn for _kind, fn in modloader.hooks_on(SolarSystemWindow, name)]
                  for name in ("apply_panel_layout", "_panel_layout_dict", "_init_trails_beams_flashes")}
        self.assertIn(combat_log.load_layout, hooked["apply_panel_layout"])
        self.assertIn(combat_log.save_layout, hooked["_panel_layout_dict"])
        self.assertIn(combat_log.init_state, hooked["_init_trails_beams_flashes"])


if __name__ == "__main__":
    unittest.main()

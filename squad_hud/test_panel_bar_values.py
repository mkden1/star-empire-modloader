"""Our compact-roster mode of Panel drawing (hide_bar_values), kept working through vanilla's
rewrites of the same function (v0.5.45 added footers, a live-label cache and a changed
height clamp that all had to be merged into it by hand)."""
import unittest
from unittest import mock

import pygame

from gl_ui_renderer import install_draw_dispatch
from test_gl_ui_canvas import _FakeRenderer
from test_profiler_review_fixes import make_frame, make_owner


class HideBarValuesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        install_draw_dispatch()

    def setUp(self):
        self.canvas = _FakeRenderer((900, 700)).canvas
        self.frame = make_frame(self.canvas)
        self.owner = make_owner()
        # blank-label bar rows, as the squad / fighter rosters pass them: (text, colour, is_bar,
        # value, maximum, fill colour)
        self.lines = [("", (200, 210, 230), True, 40 + index, 100, (60, 200, 90))
                      for index in range(3)]
        self.live_labels = []
        real = self.owner._cached_live_label
        self.owner._cached_live_label = lambda text, *a, **k: (
            self.live_labels.append(text) or real(text, *a, **k))
        self.owner._panels["roster"] = {
            "x": 100, "y": 100, "minimized": False, "max_h": 200, "max_w": None, "scroll": 0}

    def draw(self, mouse, hide):
        self.live_labels.clear()
        with mock.patch("pygame.mouse.get_pos", return_value=mouse):
            self.owner._draw_panel(self.frame, "roster", "ROSTER", self.lines,
                                   click_map={}, rects_target={},
                                   hide_bar_values=hide)
        return list(self.live_labels)

    def test_without_the_flag_every_bar_shows_its_numbers(self):
        self.assertEqual(sorted(self.draw((5, 5), hide=False)), ["40/100", "41/100", "42/100"])

    def test_with_it_the_numbers_stay_hidden_until_the_pointer_is_on_that_row(self):
        self.assertEqual(self.draw((5, 5), hide=True), [])
        panel = self.owner._panel_full_rects["roster"]
        shown = {}
        for y in range(panel.top, panel.bottom):
            labels = self.draw((panel.left + 20, y), hide=True)
            self.assertLessEqual(len(labels), 1, "more than the hovered row showed its value")
            for label in labels:
                shown.setdefault(label, y)
        self.assertEqual(sorted(shown), ["40/100", "41/100", "42/100"])
        self.assertEqual(sorted(shown.values()), list(shown.values()))     # top to bottom

    def test_a_blank_label_row_draws_no_label_at_all_and_a_named_one_does(self):
        asked = []
        real = self.owner._cached_label
        self.owner._cached_label = lambda text, *a, **k: asked.append(text) or real(text, *a, **k)
        self.draw((5, 5), hide=True)
        self.assertNotIn("", asked)                          # no empty-string label surfaces
        asked.clear()
        self.lines = [("Name", (200, 210, 230), True, 40 + i, 100, (60, 200, 90)) for i in range(3)]
        self.draw((5, 5), hide=True)
        self.assertIn("Name", asked)

    def test_vanillas_footer_and_our_drag_guard_still_work_together(self):
        footers = []
        with mock.patch("pygame.mouse.get_pos", return_value=(5, 5)):
            self.owner._draw_panel(
                self.frame, "roster", "ROSTER", self.lines,
                click_map={}, rects_target={}, hide_bar_values=True, footer_h=24,
                footer_draw=lambda screen, rect, zone, mouse: footers.append(rect.copy()))
        self.assertEqual(len(footers), 1)
        # a panel being dragged is not pulled back by the resize clamp, but is once released
        self.owner._panels["roster"].update(y=690, dragging=True)
        with mock.patch("pygame.mouse.get_pos", return_value=(5, 5)):
            self.owner._draw_panel(self.frame, "roster", "ROSTER", self.lines,
                                   click_map={}, rects_target={},
                                   hide_bar_values=True)
        self.assertEqual(self.owner._panels["roster"]["y"], 690)
        self.owner._panels["roster"]["dragging"] = False
        with mock.patch("pygame.mouse.get_pos", return_value=(5, 5)):
            self.owner._draw_panel(self.frame, "roster", "ROSTER", self.lines,
                                   click_map={}, rects_target={},
                                   hide_bar_values=True)
        self.assertLess(self.owner._panels["roster"]["y"], 690)


if __name__ == "__main__":
    unittest.main()

"""The Combat Log's mouse handling: what used to be four blocks of patch text inside the game's event loop.

Each function answers "did I take this event?" (True where the loop did ``continue``). The checks here pin what the
blocks did; the loop itself is only a call and a ``continue`` (checked against the patched source below).
"""
import unittest
from unittest import mock

import pygame

import combat_log
import modloader

PANEL = pygame.Rect(100, 100, 300, 200)


def allow_all(name):
    return name == "combat_log"


def deny_all(name):
    return False


class Window:
    def __init__(self, **kw):
        self._combat_log_open = True
        self._combat_log_scroll = 4
        self._combat_log_panel_rect = PANEL
        self._combat_log_close_rect = pygame.Rect(370, 102, 20, 20)
        self._combat_log_reset_rect = pygame.Rect(340, 102, 20, 20)
        self._combat_log_grip_rect = pygame.Rect(100, 100, 12, 12)
        self._combat_log_header_rect = pygame.Rect(100, 100, 300, 24)
        self._combat_log_dragging = self._combat_log_resizing = False
        self._combat_log_drag_dx = self._combat_log_drag_dy = 0
        self.calls = []
        self.saved = []
        self.__dict__.update(kw)

    def _close_combat_log_panel(self):
        self.calls.append("close")

    def _reset_combat_log(self):
        self.calls.append("reset")

    def _combat_log_apply_resize(self, pos, w, h):
        self.calls.append(("resize", pos, w, h))

    def _combat_log_apply_drag(self, pos, w, h):
        self.calls.append(("drag", pos, w, h))

    def _save_options_fn(self, panels=None):
        self.saved.append(panels)

    def _panel_layout_dict(self):
        return {"layout": 1}


class WheelTests(unittest.TestCase):
    def wheel(self, window, y, mouse=(200, 200), route=allow_all):
        with mock.patch("pygame.mouse.get_pos", return_value=mouse):
            return combat_log.wheel(window, mock.Mock(y=y), route)

    def test_scrolling_over_the_panel_moves_the_history_and_is_taken(self):
        w = Window()
        self.assertTrue(self.wheel(w, 1))
        self.assertEqual(w._combat_log_scroll, 3)
        self.assertTrue(self.wheel(w, -2))
        self.assertEqual(w._combat_log_scroll, 5)

    def test_it_never_scrolls_above_the_newest_entry(self):
        w = Window(_combat_log_scroll=1)
        self.assertTrue(self.wheel(w, 5))
        self.assertEqual(w._combat_log_scroll, 0)

    def test_scrolling_elsewhere_is_left_to_the_camera_zoom(self):
        """The live bug this guards: the panel is open by default and used to swallow EVERY wheel event."""
        for label, window, mouse, route in (
                ("cursor outside the panel", Window(), (700, 500), allow_all),
                ("panel closed", Window(_combat_log_open=False), (200, 200), allow_all),
                ("a window above it", Window(), (200, 200), deny_all),
                ("not drawn yet", Window(_combat_log_panel_rect=None), (200, 200), allow_all)):
            with self.subTest(label):
                self.assertFalse(self.wheel(window, 1, mouse, route))
                self.assertEqual(window._combat_log_scroll, 4)


class ClickTests(unittest.TestCase):
    def test_the_panel_is_hit_only_when_open_unblocked_and_under_the_pointer(self):
        self.assertTrue(combat_log.panel_hit(Window(), (200, 200), allow_all))
        self.assertFalse(combat_log.panel_hit(Window(), (700, 500), allow_all))
        self.assertFalse(combat_log.panel_hit(Window(_combat_log_open=False), (200, 200), allow_all))
        self.assertFalse(combat_log.panel_hit(Window(), (200, 200), deny_all))
        self.assertFalse(combat_log.panel_hit(Window(_combat_log_panel_rect=None), (200, 200), allow_all))

    def test_close_and_reset_buttons(self):
        w = Window()
        self.assertTrue(combat_log.click(w, (375, 110)))
        self.assertTrue(combat_log.click(w, (345, 110)))
        self.assertEqual(w.calls, ["close", "reset"])

    def test_the_grip_starts_a_resize_and_the_title_bar_starts_a_drag_from_where_it_was_grabbed(self):
        w = Window()
        self.assertTrue(combat_log.click(w, (105, 105)))
        self.assertEqual((w._combat_log_resizing, w._combat_log_dragging), (True, False))
        w = Window()
        self.assertTrue(combat_log.click(w, (250, 110)))
        self.assertEqual((w._combat_log_dragging, w._combat_log_drag_dx, w._combat_log_drag_dy), (True, 150, 10))
        self.assertFalse(w._combat_log_resizing)

    def test_a_click_on_the_history_table_is_not_a_control_but_is_still_absorbed_by_the_panel(self):
        w = Window()
        self.assertTrue(combat_log.panel_hit(w, (250, 250), allow_all))
        self.assertFalse(combat_log.click(w, (250, 250)))      # False: the loop does not `continue`, falls on
        self.assertEqual(w.calls, [])
        self.assertEqual((w._combat_log_dragging, w._combat_log_resizing), (False, False))

    def test_controls_are_checked_in_the_order_close_reset_grip_title(self):
        w = Window(_combat_log_close_rect=pygame.Rect(0, 0, 500, 500))     # covers everything: close wins
        combat_log.click(w, (105, 105))
        self.assertEqual(w.calls, ["close"])


class DragTests(unittest.TestCase):
    def test_releasing_ends_the_drag_or_resize_and_saves_the_layout(self):
        for field in ("_combat_log_resizing", "_combat_log_dragging"):
            with self.subTest(field):
                w = Window(**{field: True})
                self.assertTrue(combat_log.release(w))
                self.assertFalse(getattr(w, field))
                self.assertEqual(w.saved, [{"layout": 1}])

    def test_releasing_with_nothing_going_does_nothing(self):
        w = Window()
        self.assertFalse(combat_log.release(w))
        self.assertEqual(w.saved, [])

    def test_moving_follows_the_pointer_for_a_resize_or_a_drag(self):
        w = Window(_combat_log_resizing=True)
        self.assertTrue(combat_log.motion(w, (300, 300), 1280, 720, allow_all))
        self.assertEqual(w.calls, [("resize", (300, 300), 1280, 720)])
        w = Window(_combat_log_dragging=True)
        self.assertTrue(combat_log.motion(w, (310, 320), 1280, 720, allow_all))
        self.assertEqual(w.calls, [("drag", (310, 320), 1280, 720)])

    def test_moving_does_nothing_when_nothing_is_held_or_a_window_above_owns_the_pointer(self):
        self.assertFalse(combat_log.motion(Window(), (1, 1), 1280, 720, allow_all))
        w = Window(_combat_log_resizing=True)
        self.assertFalse(combat_log.motion(w, (1, 1), 1280, 720, deny_all))
        self.assertEqual(w.calls, [])


class LoopHooksTests(unittest.TestCase):
    """What the patched Client.py's event loop says: a call, and a ``continue`` where the old block had one."""

    @classmethod
    def setUpClass(cls):
        cls.source = modloader.game_source("Client.py")

    def test_the_loop_calls_the_modules_functions(self):
        for call in ("combat_log.wheel(self, event, _gui_route_allows)",
                     "combat_log.panel_hit(self, event.pos, _gui_route_allows)",
                     "combat_log.click(self, event.pos)",
                     "combat_log.release(self)",
                     "combat_log.motion(self, event.pos, win_w, win_h, _gui_route_allows)"):
            with self.subTest(call):
                self.assertEqual(self.source.count(call), 1)

    def test_the_game_file_has_the_module_to_call_them_on(self):
        import Client
        self.assertIs(Client.combat_log, combat_log)

    def test_the_old_blocks_are_gone_from_the_patched_source(self):
        for gone in ("self._combat_log_scroll = max(", "self._combat_log_resizing = True", "_combat_log_apply_resize("):
            with self.subTest(gone):
                self.assertNotIn(gone, self.source)


if __name__ == "__main__":
    unittest.main()

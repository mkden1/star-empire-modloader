"""Tests for bar_context.py: the game's own quick action bar code, drawing, clicking, dropping, lifting and holding, run on an
extra bar through ``in_bar`` while the game's own bar stays exactly as it was. The window is the dev's own bar test window (the
real HotbarMixin and the real _draw_hotbar)."""

from __future__ import annotations
import modloader

import json
import os
import types
import unittest
from unittest.mock import patch

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

import bar_context as bc
import test_hotbar_tabs as dev

FIGHTER = {"item_class": "cargo_item", "item_category": "fighter", "item_type": "interceptor",
           "display_name": "Interceptor", "color_rgb": [120, 200, 255]}
FIGHTER_ROW = dict(FIGHTER, cargo_key="item:fighter:interceptor")
MISSILE = {"item_class": "cargo_item", "item_category": "missile", "item_type": "kinetic_missile",
           "display_name": "Kinetic Missile", "color_rgb": [255, 180, 80]}
SCREEN = (1280, 720)


class Win(dev.DrawnBarTests.Window):
    """The dev's bar test window with what firing a slot needs: an inventory, the server, the chat."""

    def __init__(self):
        super().__init__()
        self._stasis_until = 0.0
        self._inventory = [FIGHTER_ROW]
        self.sent: list = []
        self.logs: list = []
        self.saves = 0

    def _send_fn(self, message):
        self.sent.append(message)

    def _send_json(self, name, payload):
        self.sent.append((name, payload))

    def chat_log(self, text, colour=None):
        self.logs.append(text)

    def _hotbar_save(self):
        self.saves += 1                       # the game's own save: writes the game's bar into options.txt


def make_view(bar_id="b1", on_save=None):
    return bc.BarView(bar_id, [None] * bc.SLOTS, on_save)


def draw(window, mouse=(0, 0)):
    screen = pygame.Surface(SCREEN)
    screen.fill((20, 60, 30))
    frame = types.SimpleNamespace(screen=screen, win_w=SCREEN[0], win_h=SCREEN[1])
    with patch("pygame.mouse.get_pos", return_value=mouse):
        window._draw_hotbar(frame, draw_animation=False, tick_press=False)
    return screen


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.font.init()
        pygame.display.init()

    def setUp(self):
        self.win = Win()
        self.saved = []
        self.view = make_view(on_save=lambda: self.saved.append(1))
        self.view.state["_hotbar_pos_x"], self.view.state["_hotbar_pos_y"] = 100, 50

    def draw_extra(self, mouse=(0, 0)):
        with bc.in_bar(self.win, self.view):
            return draw(self.win, mouse)

    def snapshot(self):
        return {name: getattr(self.win, name, None) for name in bc.STATE_ATTRS}


class SwapTests(Base):
    def test_the_bar_is_the_games_bar_inside_the_block_and_the_games_own_outside(self):
        main = self.win._hotbar
        with bc.in_bar(self.win, self.view):
            self.assertIs(self.win._hotbar, self.view.slots)
            self.assertEqual(bc.current_bar_id(), "b1")
            self.assertEqual((self.win._hotbar_pos_x, self.win._hotbar_pos_y), (100, 50))
        self.assertIs(self.win._hotbar, main)
        self.assertIsNone(bc.current_bar_id())
        self.assertIsNone(self.win._hotbar_pos_x)

    def test_every_attribute_is_back_as_it_was(self):
        self.win._hotbar_move_src = 7
        self.win._hotbar_press_slot = 3
        self.win._hotbar_key_hold = {"slot": 1}
        before = self.snapshot()
        with bc.in_bar(self.win, self.view):
            self.win._hotbar_move_src = 2
            self.win._hotbar_press_slot = 5
            self.win._hotbar_shown_set = 1
        after = self.snapshot()
        for name in bc.STATE_ATTRS:
            self.assertEqual(after[name], before[name], name)
        self.assertEqual(self.view.state["_hotbar_move_src"], 2)             # and the bar kept what happened to it
        self.assertEqual(self.view.state["_hotbar_press_slot"], 5)

    def test_an_error_inside_the_block_still_puts_everything_back(self):
        before = self.snapshot()
        with self.assertRaises(RuntimeError):
            with bc.in_bar(self.win, self.view):
                self.win._hotbar_move_src = 4
                raise RuntimeError("boom")
        self.assertEqual(self.snapshot(), before)
        self.assertIsNone(bc.current_bar_id())
        self.assertNotIn("_hotbar_save", vars(self.win))

    def test_two_bars_nest_and_unwind(self):
        other = make_view("b2")
        before = self.snapshot()
        with bc.in_bar(self.win, self.view):
            with bc.in_bar(self.win, other):
                self.assertIs(self.win._hotbar, other.slots)
                self.assertEqual(bc.current_bar_id(), "b2")
            self.assertIs(self.win._hotbar, self.view.slots)
            self.assertEqual(bc.current_bar_id(), "b1")
        self.assertEqual(self.snapshot(), before)

    def test_the_games_save_is_replaced_by_the_bars_own_while_inside(self):
        with bc.in_bar(self.win, self.view):
            self.win._hotbar_save()
            self.win._hotbar_save()
        self.assertEqual(self.saved, [1, 1])
        self.assertEqual(self.win.saves, 0)                                  # the game's bar was never written
        self.win._hotbar_save()
        self.assertEqual(self.win.saves, 1)                                  # and its save is back
        self.assertNotIn("_hotbar_save", vars(self.win))

    def test_a_save_the_window_had_set_on_itself_is_restored(self):
        mine = lambda: None
        self.win._hotbar_save = mine
        with bc.in_bar(self.win, self.view):
            self.assertIsNot(self.win._hotbar_save, mine)
        self.assertIs(self.win._hotbar_save, mine)

    def test_a_bar_without_a_save_hook_is_fine(self):
        view = make_view()
        with bc.in_bar(self.win, view):
            self.win._hotbar_save()
        self.assertEqual(self.win.saves, 0)

    def test_a_list_the_game_rebinds_keeps_the_bars_own_object(self):
        original = self.view.slots
        with bc.in_bar(self.win, self.view):
            self.win._hotbar = [dict(FIGHTER)] + [None] * 4
        self.assertIs(self.view.slots, original)
        self.assertIs(self.view.state["_hotbar"], original)
        self.assertEqual(len(original), bc.SLOTS)
        self.assertEqual(original[0]["item_type"], "interceptor")

    def test_the_games_own_bar_is_not_touched_by_what_happens_to_the_extra_one(self):
        self.win._hotbar[3] = dict(MISSILE)
        main = list(self.win._hotbar)
        with bc.in_bar(self.win, self.view):
            self.win._hotbar_bind_slot(0, FIGHTER_ROW)
            self.win._hotbar_clear_slot(0)
            self.win._hotbar_bind_slot(1, FIGHTER_ROW)
        self.assertEqual(self.win._hotbar, main)
        self.assertEqual(self.view.slots[1]["item_type"], "interceptor")
        self.assertEqual(self.win.saves, 0)
        self.assertEqual(len(self.saved), 3)


class DrawTests(Base):
    def test_the_extra_bar_is_drawn_where_it_was_put_and_publishes_its_own_rectangles(self):
        self.draw_extra()
        panel = self.view.panel_rect
        self.assertEqual((panel.x, panel.y, panel.w, panel.h), (100, 50, 530, 62))
        rects = self.view.state["_hotbar_slot_rects"]
        self.assertEqual(len(rects), 10)
        self.assertTrue(all(panel.contains(r) for r in rects))
        self.assertEqual(self.view.state["_hotbar_page_rects"], (None, None))        # one tab: no arrows
        self.assertIsNone(self.win._hotbar_panel_rect)                               # nothing of it leaked to the game's bar
        self.assertEqual(self.win._hotbar_slot_rects, [None] * 10)

    def test_the_games_bar_and_the_extra_bar_draw_side_by_side(self):
        self.draw(self.win)
        main_panel = self.win._hotbar_panel_rect.copy()
        main_rects = [r.copy() for r in self.win._hotbar_slot_rects]
        self.draw_extra()
        self.assertEqual(self.win._hotbar_panel_rect, main_panel)
        self.assertEqual(self.win._hotbar_slot_rects, main_rects)
        self.assertNotEqual(self.view.panel_rect, main_panel)
        self.assertAlmostEqual(main_panel.centerx, SCREEN[0] / 2, delta=1)           # still bottom-centre

    def draw(self, window):
        return draw(window)

    def test_a_bound_slot_draws_differently_from_an_empty_one(self):
        empty = pygame.image.tobytes(self.draw_extra(), "RGB")
        self.view.slots[2] = dict(FIGHTER)
        bound = pygame.image.tobytes(self.draw_extra(), "RGB")
        self.assertNotEqual(empty, bound)

    def test_the_panel_size_is_what_the_game_draws_at_every_ui_scale(self):
        """``panel_size`` is what places a bar by fractions of the room it has: it must be the size the game draws."""
        for scale in (1.0, 1.1, 1.25, 1.5, 1.75, 2.0):
            self.win._uk = scale
            view = make_view()
            with bc.in_bar(self.win, view):
                draw(self.win)
            self.assertEqual((view.panel_rect.w, view.panel_rect.h), bc.panel_size(self.win._s), scale)
        self.win._uk = 1.0
        self.assertEqual(bc.panel_size(self.win._s), (530, 62))

    def test_the_bar_is_kept_on_screen_by_the_games_own_draw(self):
        self.view.state["_hotbar_pos_x"], self.view.state["_hotbar_pos_y"] = 5000, -300
        self.draw_extra()
        self.assertTrue(pygame.Rect(0, 0, *SCREEN).contains(self.view.panel_rect))

    def test_drawing_twice_gives_the_same_picture(self):
        self.view.slots[0] = dict(FIGHTER)
        first = pygame.image.tobytes(self.draw_extra(), "RGB")
        self.assertEqual(first, pygame.image.tobytes(self.draw_extra(), "RGB"))


class ClickTests(Base):
    def setUp(self):
        super().setUp()
        self.view.slots[2] = dict(FIGHTER)
        self.draw_extra()
        self.slot = self.view.state["_hotbar_slot_rects"][2].center
        self.empty = self.view.state["_hotbar_slot_rects"][5].center

    def test_a_click_fires_the_slot_of_the_extra_bar_and_only_that(self):
        with bc.in_bar(self.win, self.view):
            self.assertTrue(self.win._hotbar_on_mouse_down(self.slot))
            self.assertEqual(self.win._hotbar_press_slot, 2)
        self.assertIsNone(self.win._hotbar_press_slot)                       # the game's bar saw no press
        with bc.in_bar(self.win, self.view):
            self.win._hotbar_on_mouse_up(self.slot, True)
        self.assertEqual(self.win.sent, ["LAUNCH_FIGHTER item:fighter:interceptor"])

    def test_a_press_on_the_panel_but_not_on_a_slot_is_not_a_slot_press(self):
        panel = self.view.panel_rect
        with bc.in_bar(self.win, self.view):
            self.assertFalse(self.win._hotbar_on_mouse_down((panel.x + 2, panel.y + 2)))
            self.assertIsNone(self.win._hotbar_press_slot)

    def test_a_click_on_an_empty_slot_fires_nothing(self):
        with bc.in_bar(self.win, self.view):
            self.win._hotbar_on_mouse_down(self.empty)
            self.win._hotbar_on_mouse_up(self.empty, True)
        self.assertEqual(self.win.sent, [])

    def test_a_press_released_over_a_surface_above_the_bar_cancels(self):
        with bc.in_bar(self.win, self.view):
            self.win._hotbar_on_mouse_down(self.slot)
            self.win._hotbar_on_mouse_up(self.slot, False)
        self.assertEqual(self.win.sent, [])
        self.assertIsNone(self.view.state["_hotbar_press_slot"])

    def test_right_click_clears_a_bound_slot_and_saves_the_extra_bar_not_the_games(self):
        with bc.in_bar(self.win, self.view):
            self.assertTrue(self.win._hotbar_on_right_click(self.slot))
        self.assertIsNone(self.view.slots[2])
        self.assertEqual(self.saved, [1])
        self.assertEqual(self.win.saves, 0)

    def test_right_click_on_an_empty_slot_opens_the_class_skill_menu_for_that_slot(self):
        opened = []
        self.win._hotbar_open_skill_menu = lambda slot: opened.append((slot, bc.current_bar_id()))
        with bc.in_bar(self.win, self.view):
            self.assertTrue(self.win._hotbar_on_right_click(self.empty))
        self.assertEqual(opened, [(5, "b1")])

    def test_a_press_held_long_enough_lifts_the_slot_and_a_release_over_another_swaps_them(self):
        clock = [100.0]
        with patch("time.monotonic", lambda: clock[0]), patch("pygame.mouse.get_pressed", return_value=(True, False, False)):
            with bc.in_bar(self.win, self.view):
                self.win._hotbar_on_mouse_down(self.slot)
            clock[0] += 0.6
            with bc.in_bar(self.win, self.view):
                self.win._hotbar_tick_press()
                self.assertEqual(self.win._hotbar_move_src, 2)
            self.assertIsNone(self.win._hotbar_move_src)
            with bc.in_bar(self.win, self.view):
                self.win._hotbar_on_mouse_up(self.empty, True)
        self.assertIsNone(self.view.slots[2])
        self.assertEqual(self.view.slots[5]["item_type"], "interceptor")
        self.assertEqual(self.win.sent, [])                                  # moved, never fired
        self.assertEqual(self.saved, [1])

    def test_an_item_dragged_from_the_inventory_binds_where_it_is_dropped(self):
        self.win._hotbar_drag_item = dict(MISSILE)
        with bc.in_bar(self.win, self.view):
            self.win._hotbar_on_mouse_up(self.empty, True)
        self.assertEqual(self.view.slots[5]["item_type"], "kinetic_missile")
        self.assertIsNone(self.win._hotbar_drag_item)
        self.assertEqual(self.saved, [1])
        self.assertEqual(self.win._hotbar, [None] * 20)

    def test_a_drop_cancelled_by_a_surface_above_binds_nothing_and_lets_go_of_the_item(self):
        self.win._hotbar_drag_item = dict(MISSILE)
        with bc.in_bar(self.win, self.view):
            self.win._hotbar_on_mouse_up(self.empty, False)
        self.assertIsNone(self.view.slots[5])
        self.assertIsNone(self.win._hotbar_drag_item)

    def test_a_slot_key_fires_through_the_games_own_methods(self):
        with bc.in_bar(self.win, self.view):
            self.win._hotbar_activate_slot(2, from_key=True)
        self.assertEqual(self.win.sent, ["LAUNCH_FIGHTER item:fighter:interceptor"])

    def test_a_held_slot_key_repeats_from_the_extra_bar_and_ends_with_the_key(self):
        clock, down = [100.0], {pygame.K_F3: True}

        class Pressed:
            def __getitem__(self, key):
                return down.get(key, False)

        with patch("time.monotonic", lambda: clock[0]), patch("pygame.key.get_pressed", return_value=Pressed()):
            with bc.in_bar(self.win, self.view):
                self.win._hotbar_key_hold_begin(2, pygame.K_F3)
            self.assertIsNone(self.win._hotbar_key_hold)                     # the game's bar holds nothing
            clock[0] += 0.4
            with bc.in_bar(self.win, self.view):
                self.win._hotbar_tick_key_hold()
            started = [m for m in self.win.sent if isinstance(m, tuple) and m[0] == "HOTBAR_HOLD"]
            self.assertEqual(started[0][1]["action"], "start")
            self.assertEqual(started[0][1]["kind"], "fighter")
            down[pygame.K_F3] = False
            with bc.in_bar(self.win, self.view):
                self.win._hotbar_tick_key_hold()
            stopped = [m for m in self.win.sent if isinstance(m, tuple) and m[0] == "HOTBAR_HOLD"]
            self.assertEqual(stopped[-1][1], {"action": "stop"})
            self.assertIsNone(self.view.state["_hotbar_key_hold"])


class StasisTests(Base):
    def test_no_action_while_control_is_locked_out_on_an_extra_bar_either(self):
        self.view.slots[0] = dict(FIGHTER)
        self.win._stasis_until = 10 ** 9
        with bc.in_bar(self.win, self.view):
            self.win._hotbar_activate_slot(0, from_key=True)
        self.assertEqual(self.win.sent, [])


class RealGameTests(unittest.TestCase):
    """The game's own code, as the context swap relies on it: if a game update adds state to the bar, this says so."""

    # What the bar's code writes that is NOT per bar (one drag at a time, one Queue Delay, the inventory tallies, the key map).
    SHARED = {"_hotbar_drag_item", "_hotbar_queue_delay", "_hotbar_inventory_cache_version", "_hotbar_inventory_counts_cache",
              "_hotbar_fitted_types_cache", "_hotbar_key_to_slot"}

    def assigned(self, source: str) -> set:
        import re
        return set(re.findall(r"self\.(_hotbar\w*)\s*(?::[^=\n]+)?=(?!=)", source))

    def test_every_bar_attribute_the_game_writes_is_swapped_or_known_to_be_shared(self):
        found = self.assigned(modloader.game_source("hotbar.py")) | self.assigned(modloader.game_source("render_mixin.py"))
        unknown = found - set(bc.STATE_ATTRS) - self.SHARED
        self.assertEqual(unknown, set(), "the game's bar has new state: add it to bar_context.STATE_ATTRS (or SHARED here)")

    def test_every_swapped_attribute_is_one_the_game_really_has(self):
        found = self.assigned(modloader.game_source("hotbar.py")) | self.assigned(modloader.game_source("render_mixin.py"))
        self.assertEqual(set(bc.STATE_ATTRS) - found, set(), "an attribute the game no longer writes: drop it from STATE_ATTRS")

    def test_the_fresh_state_covers_exactly_the_swapped_attributes(self):
        self.assertEqual(set(bc.fresh_state([None] * 10)), set(bc.STATE_ATTRS))

    def test_the_games_window_has_every_method_the_runtime_calls(self):
        import Client
        for name in ("_draw_hotbar", "_draw_hotbar_animation", "_hotbar_frame_tick", "_hotbar_on_mouse_down",
                     "_hotbar_on_mouse_up", "_hotbar_on_right_click", "_hotbar_slot_under", "_hotbar_page_arrow_under",
                     "_hotbar_drawn_state", "_hotbar_activate_slot", "_hotbar_key_hold_begin", "_hotbar_key_hold_end",
                     "_draw_retained_gpu_layer", "_hotbar_skill_menu_event", "_draw_class_skill_menu", "_hotbar_open_skill_menu"):
            self.assertTrue(callable(getattr(Client.SolarSystemWindow, name, None)), name)

    def test_the_draw_hook_is_inside_the_branch_that_draws_the_bar_and_right_after_it(self):
        import ast
        tree = ast.parse(modloader.game_source("Client.py"))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                 and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == "draw_bars"]
        self.assertEqual(len(calls), 1)
        hook = calls[0]
        body = parents[hook].body
        before = body[body.index(hook) - 1]
        self.assertEqual(ast.unparse(before), "self._draw_hotbar_animation(_frame)")
        self.assertIn("self._ui_editor is None", ast.unparse(parents[hook].test))      # not while the game's UI editor is open


if __name__ == "__main__":
    unittest.main()

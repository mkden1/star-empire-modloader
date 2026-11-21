"""Tests for extra_bars.py: the extra quick action bars in the running game, on the game's real bar renderer and real pointer manager
(the dev's bar test window): loading per account, where bars sit, drawing and who owns the pointer, the mouse, the keys, saving,
and the class-skill menu."""

from __future__ import annotations
import modloader

import os
import tempfile
import types
import unittest
from unittest.mock import patch

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

import bar_context as bc
import bars
import extra_bars as eb
import keybinds as kb
import quick_keys as qk
from gui_window_manager import GuiLayer, GuiWindowManager
from keybinds import Combo
from test_bar_context import FIGHTER, FIGHTER_ROW, MISSILE, Win as BaseWin

SCREEN = (1280, 720)


def sync_submit(fn, *args):
    fn(*args)


class XWin(BaseWin):
    """The dev's bar window with the account, the pointer manager and a stand-in class-skill menu."""

    def __init__(self, username="alice"):
        super().__init__()
        self._username = username
        self._gui_windows = GuiWindowManager()
        self._class_skill_menu = None
        self.menu_log: list = []
        self._targeted_pid = self._targeted_npc_id = self._targeted_player_station_id = None      # a missile slot reads them

    def _hotbar_open_skill_menu(self, slot):
        self._class_skill_menu = {"slot": slot, "rect": None, "rows": []}

    def _hotbar_skill_menu_event(self, event):
        self.menu_log.append(("event", bc.current_bar_id(), self._hotbar_slot_rects[0]))
        return True

    def _draw_class_skill_menu(self, ctx):
        self.menu_log.append(("draw", bc.current_bar_id(), self._hotbar_slot_rects[0]))


class PlainWin(BaseWin):
    """A window class with no class-skill menu methods at all."""


def saved_set(*names, hidden=()):
    """What a saved account entry looks like for bars named b1, b2 ..."""
    return {"version": 1, "bars": [{"id": f"b{i + 1}", "name": name, "mods": list(bars.MOD_SETS[i]), "fx": None, "fy": None,
                                    "visible": name not in hidden, "slots": [None] * 10} for i, name in enumerate(names)]}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.font.init()
        pygame.display.init()
        cls._boot = tempfile.TemporaryDirectory()
        qk.reset_for_tests()
        qk.configure_for_tests(os.path.join(cls._boot.name, "boot.json"), sync_submit)
        eb.install(XWin)
        eb.install(PlainWin)

    @classmethod
    def tearDownClass(cls):
        qk.reset_for_tests()
        cls._boot.cleanup()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "quick_bars.json")
        qk.reset_for_tests()
        qk.configure_for_tests(self.path, sync_submit)
        eb.reset_for_tests()
        self.addCleanup(self._cleanup)
        self.win = XWin()

    def _cleanup(self):
        qk.reset_for_tests()
        eb.reset_for_tests()
        self._tmp.cleanup()

    def given(self, *names, account="alice", hidden=()):
        """The account has saved bars with these names (b1, b2 ...)."""
        eb._raw = {account: saved_set(*names, hidden=hidden)}

    def file_bars(self, account="alice"):
        return kb.read_file(self.path).get("bars", {}).get(account)

    def draw(self, win=None, pressed=False, mouse=(0, 0), size=SCREEN):
        win = win or self.win
        screen = pygame.Surface(size)
        screen.fill((20, 60, 30))
        frame = types.SimpleNamespace(screen=screen, win_w=size[0], win_h=size[1])
        manager = win._gui_windows
        manager.begin_frame()
        with patch("pygame.mouse.get_pos", return_value=mouse), \
                patch("pygame.mouse.get_pressed", return_value=(pressed, False, False)):
            win._draw_hotbar(frame, draw_animation=False, tick_press=False)
            manager.register("hotbar", win._hotbar_panel_rect, layer=GuiLayer.HUD)
            eb.draw_bars(win, frame)
        manager.end_frame()
        return screen

    def rt(self):
        return eb.runtime(self.win)

    def bar(self, bar_id="b1"):
        return self.rt().barset.get(bar_id)

    def view(self, bar_id="b1"):
        return self.rt().views[bar_id]

    def slot_center(self, index, bar_id="b1"):
        return self.view(bar_id).state["_hotbar_slot_rects"][index].center

    def down(self, pos, button=1, win=None):
        return eb.handle_event(win or self.win, pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=button))

    def up(self, pos, button=1, win=None):
        return eb.handle_event(win or self.win, pygame.event.Event(pygame.MOUSEBUTTONUP, pos=pos, button=button))

    def move(self, pos, win=None, held=True):
        return eb.handle_event(win or self.win, pygame.event.Event(pygame.MOUSEMOTION, pos=pos, rel=(0, 0),
                                                                    buttons=(1 if held else 0, 0, 0)))

    def padding(self, bar_id="b1"):
        rect = self.view(bar_id).panel_rect
        return rect.x + 2, rect.y + 2


# ── loading ──────────────────────────────────────────────────────────────────

class LoadTests(Base):
    def test_the_logged_in_accounts_bars_are_loaded_and_nobody_elses(self):
        eb._raw = {"alice": saved_set("Combat", "Mining"), "bob": saved_set("Bob's bar")}
        self.draw()
        self.assertEqual([b.name for b in self.rt().barset], ["Combat", "Mining"])
        self.assertEqual(self.rt().account, "alice")

    def test_the_key_table_learns_of_the_bars(self):
        self.given("Combat", "Mining")
        self.draw()
        table = qk.table()
        self.assertTrue(table.has_bars)
        self.assertEqual(table.combo("slot.b1.1"), Combo(pygame.K_1, {"shift"}))
        self.assertEqual(table.combo("slot.b2.1"), Combo(pygame.K_1, {"ctrl"}))

    def test_without_a_known_account_there_are_no_bars(self):
        self.given("Combat")
        win = XWin(username=None)
        self.draw(win)
        self.assertEqual(len(eb.runtime(win).barset), 0)
        self.assertIsNone(eb.add_bar(win))

    def test_a_new_account_gets_its_own_bars(self):
        eb._raw = {"alice": saved_set("A1"), "bob": saved_set("B1", "B2")}
        self.draw()
        self.win._username = "bob"
        self.draw()
        self.assertEqual([b.name for b in self.rt().barset], ["B1", "B2"])
        self.win._username = "carol"
        self.draw()
        self.assertEqual(len(self.rt().barset), 0)

    def test_nothing_is_drawn_or_changed_without_bars(self):
        before = pygame.image.tobytes(self.draw(), "RGB")
        main = {name: getattr(self.win, name) for name in bc.STATE_ATTRS}
        self.draw()
        self.assertEqual({name: getattr(self.win, name) for name in bc.STATE_ATTRS}, main)
        self.assertEqual(before, pygame.image.tobytes(self.draw(), "RGB"))

    def test_a_saved_set_with_junk_still_loads_what_it_can(self):
        eb._raw = {"alice": {"bars": [{"id": "b1", "name": "Ok"}, {"id": "bad id"}, 5]}}
        self.draw()
        self.assertEqual([b.id for b in self.rt().barset], ["b1"])

    def test_the_file_is_read_at_install_and_only_once(self):
        kb.write_entry(self.path, "bars", "alice", saved_set("Combat"))
        eb.load()
        self.assertEqual(sorted(eb._raw), ["alice"])
        kb.write_entry(self.path, "bars", "bob", saved_set("Other"))
        self.draw()
        self.assertEqual(sorted(eb._raw), ["alice"])


# ── drawing and placing ──────────────────────────────────────────────────────

class DrawTests(Base):
    def test_a_shown_bar_is_drawn_registered_and_owns_the_pointer_over_it(self):
        self.given("Combat")
        self.draw()
        rect = self.view().panel_rect
        self.assertIsNotNone(rect)
        owner = self.win._gui_windows.route_pointer(rect.center).owner
        self.assertEqual(owner, "quickbar:b1")
        main = self.win._hotbar_panel_rect
        self.assertEqual(self.win._gui_windows.route_pointer(main.center).owner, "hotbar")

    def test_a_hidden_bar_is_not_drawn_or_registered(self):
        self.given("Combat", "Mining", hidden=("Mining",))
        self.draw()
        self.assertIsNotNone(self.view("b1").panel_rect)
        self.assertIsNone(self.view("b2").panel_rect)
        self.assertIsNone(self.win._gui_windows.route_pointer((5000, 5000)).owner)
        self.assertNotIn("quickbar:b2", self.win._gui_windows._windows)

    def test_the_games_own_bar_is_not_changed_by_drawing_the_extra_ones(self):
        self.given("Combat", "Mining")
        self.draw()
        game = {name: getattr(self.win, name) for name in bc.STATE_ATTRS}
        self.assertIs(self.win._hotbar, game["_hotbar"])
        self.assertEqual(len(self.win._hotbar), 20)
        self.assertAlmostEqual(self.win._hotbar_panel_rect.centerx, SCREEN[0] / 2, delta=1)
        self.assertIsNone(self.win._hotbar_pos_x)

    def test_with_no_place_the_bars_stack_above_the_games_bar(self):
        self.given("One", "Two", "Three")
        self.draw()
        main = self.win._hotbar_panel_rect
        gap = self.win._s(4)
        one, two, three = (self.view(f"b{i}").panel_rect for i in (1, 2, 3))
        self.assertEqual(main.top - one.bottom, gap)
        self.assertEqual(one.top - two.bottom, gap)
        self.assertEqual(two.top - three.bottom, gap)
        self.assertEqual({one.x, two.x, three.x}, {main.x})                     # the slots line up with the games own
        self.assertEqual(one.x + self.win._s(7), self.view("b1").state["_hotbar_slot_rects"][0].x)

    def test_the_stack_follows_the_games_bar_when_it_is_moved(self):
        self.given("One", "Two")
        self.win._hotbar_pos_x, self.win._hotbar_pos_y = 100, 400
        self.draw()
        main = self.win._hotbar_panel_rect
        self.assertEqual((main.x, main.y), (100, 400))
        self.assertEqual(self.view("b1").panel_rect.topleft, (100, 400 - (main.h + self.win._s(4))))
        self.assertEqual(self.view("b2").panel_rect.x, 100)

    def test_without_a_game_bar_drawn_yet_the_stack_starts_bottom_centre(self):
        self.given("One")
        win = self.win
        win._hotbar_panel_rect = None
        frame = types.SimpleNamespace(screen=pygame.Surface(SCREEN), win_w=SCREEN[0], win_h=SCREEN[1])
        with patch("pygame.mouse.get_pos", return_value=(0, 0)), patch("pygame.mouse.get_pressed", return_value=(0, 0, 0)):
            eb.draw_bars(win, frame)
        pw, ph = bc.panel_size(win._s)
        rect = self.view().panel_rect
        self.assertEqual(rect.x, (1280 - pw) // 2)
        self.assertEqual(rect.y, 720 - ph - win._s(3) - (ph + win._s(4)))      # one step above where the games bar would be

    def test_a_hidden_bar_does_not_leave_a_hole_in_the_stack(self):
        self.given("One", "Two", "Three", hidden=("Two",))
        self.draw()
        gap = self.win._s(4)
        self.assertEqual(self.view("b1").panel_rect.top - self.view("b3").panel_rect.bottom, gap)

    def test_a_bar_with_a_place_sits_at_its_fractions_of_the_room_left_over(self):
        self.given("Combat")
        self.draw()
        self.rt().barset.set_place("b1", 0.5, 0.25)
        self.draw()
        pw, ph = bc.panel_size(self.win._s)
        rect = self.view().panel_rect
        self.assertEqual((rect.x, rect.y), (round(0.5 * (1280 - pw)), round(0.25 * (720 - ph))))
        self.rt().barset.set_place("b1", 0.0, 0.0)
        self.draw()
        self.assertEqual(self.view().panel_rect.topleft, (0, 0))
        self.rt().barset.set_place("b1", 1.0, 1.0)
        self.draw()
        self.assertEqual(self.view().panel_rect.bottomright, SCREEN)

    def test_the_place_follows_the_window_size(self):
        self.given("Combat")
        self.draw()
        self.rt().barset.set_place("b1", 1.0, 0.0)
        self.draw(size=(1920, 1080))
        self.assertEqual(self.view().panel_rect.topright, (1920, 0))
        self.draw(size=(900, 600))
        self.assertEqual(self.view().panel_rect.topright, (900, 0))
        self.assertTrue(pygame.Rect(0, 0, 900, 600).contains(self.view().panel_rect))

    def test_a_window_too_narrow_for_a_bar_keeps_it_at_the_left_edge_and_still_places_it_vertically(self):
        self.given("Combat")
        self.draw()
        self.rt().barset.set_place("b1", 0.7, 0.7)
        self.draw(size=(300, 200))
        pw, ph = bc.panel_size(self.win._s)
        self.assertEqual(self.view().panel_rect.topleft, (0, round(0.7 * (200 - ph))))
        self.draw(size=(300, 40))                                             # too short as well
        self.assertEqual(self.view().panel_rect.topleft, (0, 0))

    def test_a_bound_slot_shows_and_an_empty_one_does_not(self):
        self.given("Combat")
        empty = pygame.image.tobytes(self.draw(), "RGB")
        self.bar().slots[3] = dict(FIGHTER)
        self.assertNotEqual(empty, pygame.image.tobytes(self.draw(), "RGB"))

    def test_each_bar_has_its_own_retained_layer(self):
        self.given("One", "Two")
        self.draw()
        cache = self.win.__dict__.get("_retained_gpu_ui_layers", {})
        self.assertTrue(cache == {} or all(k.startswith("hud:") for k in cache))   # (a plain surface draws straight through)

    def test_the_slots_show_the_key_each_has(self):
        self.given("Combat")
        self.draw()
        qk.set_binding("slot.b1.2", Combo(pygame.K_F2))
        labels = []
        with bc.in_bar(self.win, self.view()):
            labels = [qk.slot_label(self.win, i, str(i + 1)) for i in range(3)]
        self.assertEqual(labels, ["s1", "F2", "s3"])
        self.assertEqual(qk.slot_label(self.win, 0, "1"), "1")                   # the games bar: its own number

    def test_the_bars_cache_key_changes_when_a_key_changes(self):
        self.given("Combat")
        qk.install(XWin)
        self.draw()
        with bc.in_bar(self.win, self.view()):
            before = eb._layer_key(self.win, *SCREEN)
        qk.set_binding("slot.b1.1", Combo(pygame.K_F1))
        with bc.in_bar(self.win, self.view()):
            after = eb._layer_key(self.win, *SCREEN)
        self.assertNotEqual(before, after)
        qk.set_binding("slot.b1.1", Combo(pygame.K_F2))                        # a different key again: it is that bar's labels
        with bc.in_bar(self.win, self.view()):
            again = eb._layer_key(self.win, *SCREEN)
        self.assertNotEqual(after, again)

    def test_the_games_own_bar_is_not_keyed_on_an_extra_bars_labels(self):
        self.given("Combat")
        qk.install(XWin)
        self.draw()
        qk.set_binding("slot.b1.1", Combo(pygame.K_F1))
        before = self.win._hotbar_drawn_state(None)
        qk.set_binding("slot.b1.1", Combo(pygame.K_F2))
        self.assertEqual(self.win._hotbar_drawn_state(None), before)           # the games bar shows the same: it is not repainted
        self.assertEqual(before[2], tuple("1234567890"))                       # (its own labels, which are the numbers)

    def test_an_error_while_drawing_switches_the_extra_bars_off_not_the_game(self):
        self.given("Combat")
        self.draw()
        with patch.object(eb, "view_of", side_effect=RuntimeError("boom")), self.assertLogs(eb.logger, "ERROR"):
            self.draw()
        self.assertFalse(eb.enabled())
        self.assertIsNotNone(self.win._hotbar_panel_rect)
        self.assertFalse(eb.handle_event(self.win, pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(1, 1), button=1)))

    def test_the_env_switch_turns_them_off(self):
        self.given("Combat")
        with patch.dict(os.environ, {eb.ENV_OFF: "1"}):
            self.draw()
            self.assertNotIn("_qb_extra", vars(self.win))


# ── the mouse ────────────────────────────────────────────────────────────────

class MouseTests(Base):
    def setUp(self):
        super().setUp()
        self.given("Combat", "Mining")
        self.draw()
        self.bar().slots[2] = dict(FIGHTER)
        self.draw()

    def test_a_press_on_a_slot_is_used_up_and_remembered_on_that_bar(self):
        self.assertTrue(self.down(self.slot_center(2)))
        self.assertEqual(self.rt().press_bar, "b1")
        self.assertEqual(self.view().state["_hotbar_press_slot"], 2)
        self.assertIsNone(self.win._hotbar_press_slot)
        self.assertEqual(self.win.sent, [])

    def test_the_release_fires_the_slot_and_goes_on_to_the_game(self):
        self.down(self.slot_center(2))
        self.assertFalse(self.up(self.slot_center(2)))
        self.assertEqual(self.win.sent, ["LAUNCH_FIGHTER item:fighter:interceptor"])
        self.assertIsNone(self.rt().press_bar)

    def test_a_press_released_somewhere_else_fires_nothing(self):
        self.down(self.slot_center(2))
        self.up(self.slot_center(5))
        self.assertEqual(self.win.sent, [])

    def test_a_press_released_over_a_window_above_the_bar_is_cancelled(self):
        self.down(self.slot_center(2))
        rect = self.view().panel_rect.inflate(40, 40)
        self.win._gui_windows.register("some_window", rect, layer=GuiLayer.WINDOW)
        self.up(self.slot_center(2))
        self.assertEqual(self.win.sent, [])

    def test_a_window_above_a_bar_gets_the_click_not_the_bar(self):
        rect = self.view().panel_rect.inflate(40, 40)
        self.win._gui_windows.register("some_window", rect, layer=GuiLayer.WINDOW)
        self.assertFalse(self.down(self.slot_center(2)))
        self.assertIsNone(self.rt().press_bar)

    def test_the_game_bars_own_slots_are_not_the_extra_bars_business(self):
        main = self.win._hotbar_slot_rects[3].center
        self.assertFalse(self.down(main))
        self.assertFalse(self.up(main))

    def test_a_click_in_the_world_is_left_alone(self):
        self.assertFalse(self.down((5, 5)))
        self.assertFalse(self.up((5, 5)))
        self.assertFalse(self.move((5, 5)))
        self.assertTrue(eb.enabled())                                         # (nothing went wrong on the way: it is just not theirs)
        self.assertFalse(self.down(self.win._hotbar_slot_rects[3].center))
        self.assertTrue(eb.enabled())

    def test_other_buttons_and_wheel_are_left_to_the_game(self):
        self.assertFalse(self.down(self.slot_center(2), button=2))
        self.assertFalse(eb.handle_event(self.win, pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=1)))
        self.assertFalse(self.up(self.slot_center(2), button=3))

    def test_a_press_held_long_enough_lifts_the_slot_and_a_release_over_another_slot_moves_it(self):
        clock = [100.0]
        with patch("time.monotonic", lambda: clock[0]):
            self.down(self.slot_center(2))
            clock[0] += 0.6
            self.draw(pressed=True)                                             # the frame tick lifts it
            self.assertEqual(self.view().state["_hotbar_move_src"], 2)
            self.up(self.slot_center(6))
        self.assertIsNone(self.bar().slots[2])
        self.assertEqual(self.bar().slots[6]["item_type"], "interceptor")
        self.assertEqual(self.win.sent, [])
        self.assertEqual(self.file_bars()["bars"][0]["slots"][6]["item_type"], "interceptor")

    def test_a_lifted_slot_dropped_on_the_other_bar_is_put_back(self):
        clock = [100.0]
        with patch("time.monotonic", lambda: clock[0]):
            self.down(self.slot_center(2))
            clock[0] += 0.6
            self.draw(pressed=True)
            self.up(self.slot_center(2, "b2"))
        self.assertEqual(self.bar().slots[2]["item_type"], "interceptor")
        self.assertEqual(self.bar("b2").filled, 0)

    def test_right_click_clears_a_bound_slot_and_saves(self):
        self.assertTrue(self.down(self.slot_center(2), button=3))
        self.assertIsNone(self.bar().slots[2])
        self.assertIsNone(self.file_bars()["bars"][0]["slots"][2])
        self.assertEqual(self.win.saves, 0)                                      # the games own bar was never saved

    def test_right_click_on_an_empty_slot_opens_the_class_skill_menu_for_that_bar(self):
        self.assertTrue(self.down(self.slot_center(5), button=3))
        self.assertEqual(self.win._class_skill_menu["slot"], 5)
        self.assertEqual(self.win._class_skill_menu["qb_bar"], "b1")

    def test_the_menu_of_a_second_bar_is_that_bars(self):
        self.down(self.slot_center(5, "b2"), button=3)
        self.assertEqual(self.win._class_skill_menu["qb_bar"], "b2")

    def test_right_click_on_an_empty_slot_does_nothing_where_there_is_no_class_skill_menu(self):
        win = PlainWin()
        win._username = "alice"
        win._gui_windows = GuiWindowManager()
        win._class_skill_menu = None
        win._hotbar_open_skill_menu = lambda slot: setattr(win, "opened", slot)
        self.draw(win)
        rt = eb.runtime(win)
        pos = rt.views["b1"].state["_hotbar_slot_rects"][5].center
        self.assertTrue(self.down(pos, button=3, win=win))
        self.assertFalse(hasattr(win, "opened"))

    def test_a_drop_from_the_inventory_binds_the_slot_it_lands_on_and_goes_on_to_the_game(self):
        self.win._hotbar_drag_item = dict(MISSILE)
        self.assertFalse(self.up(self.slot_center(5)))
        self.assertEqual(self.bar().slots[5]["item_type"], "kinetic_missile")
        self.assertIsNone(self.win._hotbar_drag_item)
        self.assertEqual(self.file_bars()["bars"][0]["slots"][5]["item_type"], "kinetic_missile")
        self.assertEqual(self.win._hotbar, [None] * 20)

    def test_a_drop_in_the_world_binds_nothing(self):
        self.win._hotbar_drag_item = dict(MISSILE)
        self.up((5, 5))
        self.assertEqual(self.bar().filled, 1)
        self.assertEqual(self.win._hotbar_drag_item["item_type"], "kinetic_missile")           # the game's own handler lets go of it

    def test_a_drop_on_the_games_own_bar_is_the_games(self):
        self.win._hotbar_drag_item = dict(MISSILE)
        self.up(self.win._hotbar_slot_rects[3].center)
        self.assertEqual(self.bar().filled, 1)
        self.assertEqual(self.bar("b2").filled, 0)

    def test_a_hidden_bar_cannot_be_clicked(self):
        at = self.view().panel_rect.center
        eb.set_visible(self.win, "b1", False)
        eb.set_visible(self.win, "b2", False)
        self.draw()
        self.assertFalse(self.down(at))
        self.assertFalse(self.down(self.slot_center(2)))

    def test_when_a_bar_is_hidden_the_ones_after_it_close_up(self):
        at = self.view("b1").panel_rect.center
        eb.set_visible(self.win, "b1", False)
        self.draw()
        self.assertTrue(self.down(at))
        self.assertEqual(self.win._gui_windows.route_pointer(at).owner, "quickbar:b2")


class DragTests(Base):
    def setUp(self):
        super().setUp()
        self.given("Combat")
        self.draw()

    def test_a_press_on_the_padding_picks_the_whole_bar_up(self):
        self.assertTrue(self.down(self.padding()))
        self.assertIsNotNone(self.rt().drag)
        self.assertIsNone(self.rt().press_bar)
        self.assertEqual(self.win.sent, [])

    def test_moving_follows_the_pointer_and_keeps_where_it_was_grabbed(self):
        start = self.padding()
        rect = self.view().panel_rect.copy()
        self.down(start)
        self.assertTrue(self.move((start[0] + 100, start[1] - 150)))
        self.assertEqual(self.view().state["_hotbar_pos_x"], rect.x + 100)
        self.assertEqual(self.view().state["_hotbar_pos_y"], rect.y - 150)
        self.draw(pressed=True)
        self.assertEqual(self.view().panel_rect.topleft, (rect.x + 100, rect.y - 150))

    def test_the_place_is_kept_as_fractions_and_saved_on_release(self):
        start = self.padding()
        self.down(start)
        self.move((start[0] + 100, start[1] - 150))
        self.draw(pressed=True)
        self.assertTrue(self.up((start[0] + 100, start[1] - 150)))
        pw, ph = bc.panel_size(self.win._s)
        rect = self.view().panel_rect
        saved = self.file_bars()["bars"][0]
        self.assertAlmostEqual(saved["fx"], rect.x / (1280 - pw), places=6)
        self.assertAlmostEqual(saved["fy"], rect.y / (720 - ph), places=6)
        self.assertIsNone(self.rt().drag)

    def test_a_bar_cannot_be_dragged_off_screen(self):
        start = self.padding()
        self.down(start)
        self.move((start[0] - 5000, start[1] - 5000))
        self.assertEqual((self.view().state["_hotbar_pos_x"], self.view().state["_hotbar_pos_y"]), (0, 0))
        self.move((start[0] + 9000, start[1] + 9000))
        pw, ph = bc.panel_size(self.win._s)
        self.assertEqual((self.view().state["_hotbar_pos_x"], self.view().state["_hotbar_pos_y"]), (1280 - pw, 720 - ph))
        self.assertEqual((self.bar().fx, self.bar().fy), (1.0, 1.0))

    def test_the_motion_after_the_button_came_up_ends_the_drag(self):
        start = self.padding()
        self.down(start)
        self.assertFalse(self.move((start[0] + 10, start[1]), held=False))
        self.assertIsNone(self.rt().drag)

    def test_a_drag_that_lost_its_release_ends_at_the_next_frame(self):
        self.down(self.padding())
        self.draw(pressed=False)
        self.assertIsNone(self.rt().drag)

    def test_a_gap_between_slots_picks_the_bar_up_too(self):
        rects = self.view().state["_hotbar_slot_rects"]
        gap = ((rects[0].right + rects[1].left) // 2, rects[0].centery)
        self.assertTrue(self.down(gap))
        self.assertIsNotNone(self.rt().drag)

    def test_hiding_a_bar_while_it_is_held_drops_it(self):
        self.down(self.padding())
        eb.set_visible(self.win, "b1", False)
        self.assertIsNone(self.rt().drag)

    def test_reset_place_puts_it_back_in_the_stack(self):
        self.rt().barset.set_place("b1", 0.0, 0.0)
        self.draw()
        eb.reset_place(self.win, "b1")
        self.draw()
        self.assertEqual(self.win._hotbar_panel_rect.top - self.view().panel_rect.bottom, self.win._s(4))
        self.assertEqual((self.file_bars()["bars"][0]["fx"], self.file_bars()["bars"][0]["fy"]), (None, None))


# ── the keys ─────────────────────────────────────────────────────────────────

def key_event(key, mod=0):
    return pygame.event.Event(pygame.KEYDOWN, key=key, mod=mod, unicode="", scancode=0)


class KeyTests(Base):
    def setUp(self):
        super().setUp()
        self.given("Combat", "Mining")
        self.draw()
        self.bar().slots[2] = dict(FIGHTER)
        self.bar("b2").slots[0] = dict(MISSILE)
        self.win._inventory = [FIGHTER_ROW, dict(MISSILE, cargo_key="item:missile:kinetic_missile")]

    def test_the_default_key_of_a_bar_slot_fires_it(self):
        event, consumed = qk.on_key_down(self.win, key_event(pygame.K_3, pygame.KMOD_LSHIFT))
        self.assertTrue(consumed)
        self.assertEqual(self.win.sent, ["LAUNCH_FIGHTER item:fighter:interceptor"])
        self.assertEqual(self.win._hotbar, [None] * 20)

    def test_each_bar_has_its_own_modifier(self):
        qk.on_key_down(self.win, key_event(pygame.K_1, pygame.KMOD_LCTRL))
        self.assertEqual(self.win.sent[0][0], "LAUNCH_MISSILE")
        self.win.sent.clear()
        qk.on_key_down(self.win, key_event(pygame.K_1, pygame.KMOD_LSHIFT))
        self.assertEqual(self.win.sent, [])                                        # slot 1 of the first bar is empty

    def test_the_games_bar_keeps_its_plain_number_keys(self):
        self.win._hotbar[0] = dict(FIGHTER)
        event, consumed = qk.on_key_down(self.win, key_event(pygame.K_1))
        self.assertTrue(consumed)
        self.assertEqual(self.win.sent, ["LAUNCH_FIGHTER item:fighter:interceptor"])

    def test_a_changed_key_of_a_bar_slot_works_and_the_old_one_does_not_fire_it(self):
        qk.set_binding("slot.b1.3", Combo(pygame.K_F3))
        self.assertTrue(qk.on_key_down(self.win, key_event(pygame.K_F3))[1])
        self.assertEqual(self.win.sent, ["LAUNCH_FIGHTER item:fighter:interceptor"])
        self.win.sent.clear()
        qk.on_key_down(self.win, key_event(pygame.K_3, pygame.KMOD_LSHIFT))
        self.assertEqual(self.win.sent, [])                                        # Shift+3 is the games own slot 3 again (empty)

    def test_the_key_a_hidden_bar_has_does_nothing_at_all(self):
        eb.set_visible(self.win, "b1", False)
        event, consumed = qk.on_key_down(self.win, key_event(pygame.K_3, pygame.KMOD_LSHIFT))
        self.assertTrue(consumed)
        self.assertEqual(self.win.sent, [])

    def test_a_bar_that_is_gone_leaves_its_key_to_the_game(self):
        self.assertFalse(eb.fire_slot(self.win, "nope", 0, key_event(pygame.K_1)))

    def test_a_window_without_the_bar_methods_is_left_alone(self):
        self.assertFalse(eb.fire_slot(object(), "b1", 0, key_event(pygame.K_1)))

    def test_only_one_key_is_held_at_a_time_across_the_bars(self):
        clock, down = [100.0], {pygame.K_1: True}

        class Pressed:
            def __getitem__(self, key):
                return down.get(key, False)

        with patch("time.monotonic", lambda: clock[0]), patch("pygame.key.get_pressed", return_value=Pressed()):
            self.win._hotbar[0] = dict(FIGHTER)
            qk.on_key_down(self.win, key_event(pygame.K_1))                       # the games bar, slot 1: a hold begins
            self.assertIsNotNone(self.win._hotbar_key_hold)
            clock[0] += 0.4
            self.win._hotbar_tick_key_hold()
            qk.on_key_down(self.win, key_event(pygame.K_3, pygame.KMOD_LSHIFT))   # an extra bar: the games hold ends
            self.assertIsNone(self.win._hotbar_key_hold)
            self.assertIsNotNone(self.view().state["_hotbar_key_hold"])
            stops = [m for m in self.win.sent if isinstance(m, tuple) and m[1].get("action") == "stop"]
            self.assertEqual(len(stops), 1)

    def test_pressing_a_slot_of_the_games_bar_ends_the_hold_of_an_extra_bar(self):
        clock, down = [100.0], {pygame.K_3: True, pygame.K_1: True}

        class Pressed:
            def __getitem__(self, key):
                return down.get(key, False)

        with patch("time.monotonic", lambda: clock[0]), patch("pygame.key.get_pressed", return_value=Pressed()):
            qk.on_key_down(self.win, key_event(pygame.K_3, pygame.KMOD_LSHIFT))
            clock[0] += 0.4
            self.draw()                                                          # the hold starts on the extra bar
            self.assertIsNotNone(self.view().state["_hotbar_key_hold"])
            self.win._hotbar[0] = dict(FIGHTER)
            qk.on_key_down(self.win, key_event(pygame.K_1))                      # now a slot of the games own bar
            self.assertIsNone(self.view().state["_hotbar_key_hold"])
            self.assertIsNotNone(self.win._hotbar_key_hold)
            stops = [m for m in self.win.sent if isinstance(m, tuple) and m[1].get("action") == "stop"]
            self.assertEqual(len(stops), 1)

    def test_a_held_extra_bar_key_ends_when_it_is_let_go_even_with_the_bar_hidden(self):
        clock, down = [100.0], {pygame.K_3: True}

        class Pressed:
            def __getitem__(self, key):
                return down.get(key, False)

        with patch("time.monotonic", lambda: clock[0]), patch("pygame.key.get_pressed", return_value=Pressed()):
            qk.on_key_down(self.win, key_event(pygame.K_3, pygame.KMOD_LSHIFT))
            clock[0] += 0.4
            self.draw()
            started = [m for m in self.win.sent if isinstance(m, tuple) and m[1].get("action") == "start"]
            self.assertEqual(len(started), 1)
            down[pygame.K_3] = False
            eb.set_visible(self.win, "b1", False)
            self.draw()
            self.assertIsNone(self.view().state["_hotbar_key_hold"])
            self.assertEqual(self.win.sent[-1][1], {"action": "stop"})

    def test_the_stage_one_hook_does_nothing_for_a_plain_default_table_without_bars(self):
        qk.set_bars([])
        event = key_event(pygame.K_3, pygame.KMOD_LSHIFT)
        self.assertEqual(qk.on_key_down(self.win, event), (event, False))


# ── changing the set ─────────────────────────────────────────────────────────

class SetTests(Base):
    def test_adding_a_bar_saves_it_and_gives_it_keys(self):
        bar = eb.add_bar(self.win)
        self.assertEqual(bar.name, "Bar 2")
        self.assertEqual(self.file_bars()["bars"][0]["id"], bar.id)
        self.assertEqual(qk.table().combo(f"slot.{bar.id}.1"), Combo(pygame.K_1, {"shift"}))
        self.draw()
        self.assertIsNotNone(self.view(bar.id).panel_rect)

    def test_adding_stops_at_the_limit(self):
        for _ in range(bars.MAX_BARS):
            self.assertIsNotNone(eb.add_bar(self.win))
        self.assertIsNone(eb.add_bar(self.win))
        self.assertEqual(len(self.file_bars()["bars"]), bars.MAX_BARS)

    def test_removing_a_bar_forgets_its_keys_and_saves(self):
        self.given("Combat", "Mining")
        self.draw()
        qk.set_binding("slot.b1.1", Combo(pygame.K_F1))
        self.assertIn("slot.b1.1", kb.read_file(self.path)["keys"])
        self.assertTrue(eb.remove_bar(self.win, "b1"))
        self.assertEqual([b["id"] for b in self.file_bars()["bars"]], ["b2"])
        self.assertNotIn("slot.b1.1", kb.read_file(self.path)["keys"])
        self.assertNotIn("slot.b1.1", qk.table().by_id)
        self.assertFalse(eb.remove_bar(self.win, "b1"))
        self.assertNotIn("b1", self.rt().views)

    def test_removing_a_bar_leaves_the_other_bars_keys_alone(self):
        self.given("Combat", "Mining")
        self.draw()
        qk.set_binding("slot.b2.1", Combo(pygame.K_F1))
        eb.remove_bar(self.win, "b1")
        self.assertEqual(qk.table().combo("slot.b2.1"), Combo(pygame.K_F1))

    def test_rename(self):
        self.given("Combat", "Mining")
        self.draw()
        self.assertEqual(eb.rename_bar(self.win, "b2", "combat"), "combat (2)")
        self.assertEqual(self.file_bars()["bars"][1]["name"], "combat (2)")
        self.assertEqual(qk.table().by_id["slot.b2.1"].group, "combat (2)")
        self.assertIsNone(eb.rename_bar(self.win, "b2", "  "))

    def test_hiding_and_showing(self):
        self.given("Combat")
        self.draw()
        eb.set_visible(self.win, "b1", False)
        self.assertFalse(self.file_bars()["bars"][0]["visible"])
        eb.set_visible(self.win, "b1", True)
        self.assertTrue(self.file_bars()["bars"][0]["visible"])

    def test_two_accounts_keep_their_own_bars_in_one_file(self):
        eb._raw = {"bob": saved_set("Bob's")}
        kb.write_entry(self.path, "bars", "bob", saved_set("Bob's"))
        eb.add_bar(self.win)
        data = kb.read_file(self.path)["bars"]
        self.assertEqual(sorted(data), ["alice", "bob"])
        self.assertEqual(data["bob"]["bars"][0]["name"], "Bob's")

    def test_changing_a_slot_key_keeps_another_accounts_bar_keys(self):
        kb.write_section(self.path, "keys", {"slot.bbbb.1": {"key": "f9", "mods": []}})
        qk.load(self.path)
        eb.add_bar(self.win)
        qk.set_binding("hotkey.dock", Combo(pygame.K_b))
        self.assertIn("slot.bbbb.1", kb.read_file(self.path)["keys"])

    def test_the_changes_need_an_account(self):
        win = XWin(username=None)
        self.assertIsNone(eb.add_bar(win))
        self.assertFalse(eb.remove_bar(win, "x"))
        self.assertIsNone(eb.rename_bar(win, "x", "y"))


# ── the UI Layouts mod's view of the bars ────────────────────────────────────

class LayoutProviderTests(Base):
    def setUp(self):
        super().setUp()
        self.given("Combat", "Mining")
        self.draw()
        self.rt().barset.set_place("b1", 0.25, 0.5)

    def test_it_says_where_every_bar_is_and_only_that(self):
        got = eb.layout_capture(self.win)
        self.assertEqual(got, {"bars": [{"id": "b1", "mods": ["shift"], "fx": 0.25, "fy": 0.5},
                                        {"id": "b2", "mods": ["ctrl"], "fx": None, "fy": None}]})
        self.assertNotIn("slots", repr(got))                                    # a layout moves bars, it does not change their contents
        self.assertNotIn("visible", repr(got))

    def test_hidden_bars_have_places_too(self):
        eb.set_visible(self.win, "b2", False)
        self.assertEqual([b["id"] for b in eb.layout_capture(self.win)["bars"]], ["b1", "b2"])

    def test_nothing_to_say_without_bars_or_before_they_are_loaded(self):
        self.assertIsNone(eb.layout_capture(XWin()))                           # no runtime yet
        self.given()
        win = XWin()
        self.draw(win)
        self.assertIsNone(eb.layout_capture(win))                              # a runtime, no bars

    def test_asking_changes_nothing(self):
        win = XWin()
        eb.layout_capture(win)
        self.assertNotIn("_qb_extra", vars(win))
        before = self.file_bars()
        eb.layout_capture(self.win)
        self.assertEqual(self.file_bars(), before)

    def test_a_layout_puts_a_bar_back_by_its_id(self):
        data = eb.layout_capture(self.win)
        self.rt().barset.set_place("b1", 0.9, 0.9)
        self.rt().barset.set_place("b2", 0.1, 0.1)
        eb.layout_apply(self.win, data)
        self.assertEqual((self.bar("b1").fx, self.bar("b1").fy), (0.25, 0.5))
        self.assertEqual((self.bar("b2").fx, self.bar("b2").fy), (None, None))     # the default place is a place too
        saved = self.file_bars()["bars"]
        self.assertEqual((saved[0]["fx"], saved[0]["fy"]), (0.25, 0.5))

    def test_a_bar_with_another_id_is_found_by_its_key_set(self):
        """A layout made with another character's bars (other ids) still places the Shift bar and the Ctrl bar."""
        data = {"bars": [{"id": "zzz1", "mods": ["shift"], "fx": 0.1, "fy": 0.2}, {"id": "zzz2", "mods": ["ctrl"], "fx": 0.7, "fy": 0.8}]}
        eb.layout_apply(self.win, data)
        self.assertEqual((self.bar("b1").fx, self.bar("b1").fy), (0.1, 0.2))
        self.assertEqual((self.bar("b2").fx, self.bar("b2").fy), (0.7, 0.8))

    def test_the_id_wins_over_the_key_set(self):
        data = {"bars": [{"id": "b1", "mods": ["ctrl"], "fx": 0.1, "fy": 0.1}, {"id": "other", "mods": ["shift"], "fx": 0.9, "fy": 0.9}]}
        eb.layout_apply(self.win, data)
        self.assertEqual((self.bar("b1").fx, self.bar("b1").fy), (0.1, 0.1))

    def test_a_bar_the_layout_does_not_know_stays_where_it_is(self):
        eb.layout_apply(self.win, {"bars": [{"id": "b9", "mods": ["alt"], "fx": 0.3, "fy": 0.3}]})
        self.assertEqual((self.bar("b1").fx, self.bar("b1").fy), (0.25, 0.5))

    def test_a_layout_of_nothing_changes_nothing(self):
        before = self.file_bars()
        for junk in (None, {}, [], "x", {"bars": "no"}, {"bars": [5, None, "x", {}]}, {"bars": [{"mods": "shift"}]}):
            eb.layout_apply(self.win, junk)
        self.assertEqual(self.file_bars(), before)
        self.assertEqual((self.bar("b1").fx, self.bar("b1").fy), (0.25, 0.5))

    def test_half_a_place_is_the_default_place(self):
        eb.layout_apply(self.win, {"bars": [{"id": "b1", "mods": ["shift"], "fx": 0.4, "fy": None}]})
        self.assertEqual((self.bar("b1").fx, self.bar("b1").fy), (None, None))

    def test_places_out_of_range_or_odd_are_made_safe(self):
        eb.layout_apply(self.win, {"bars": [{"id": "b1", "fx": 7, "fy": -2}, {"id": "b2", "fx": "left", "fy": True}]})
        self.assertEqual((self.bar("b1").fx, self.bar("b1").fy), (1.0, 0.0))
        self.assertEqual((self.bar("b2").fx, self.bar("b2").fy), (None, None))

    def test_a_bar_that_is_being_dragged_is_dropped_where_the_layout_says(self):
        self.draw(pressed=True)
        self.down(self.padding())
        self.assertIsNotNone(self.rt().drag)
        eb.layout_apply(self.win, {"bars": [{"id": "b1", "fx": 0.5, "fy": 0.5}]})
        self.assertIsNone(self.rt().drag)
        self.draw()
        pw, ph = bc.panel_size(self.win._s)
        self.assertEqual(self.view().panel_rect.topleft, (round(0.5 * (1280 - pw)), round(0.5 * (720 - ph))))

    def test_without_bars_a_layout_is_ignored(self):
        win = XWin()
        eb.layout_apply(win, {"bars": [{"id": "b1", "fx": 0.5, "fy": 0.5}]})
        self.assertNotIn("_qb_extra", vars(win))

    def test_the_ui_layouts_mod_finds_it_by_name(self):
        """The two mods know each other only by this module name and these two function names."""
        try:
            import ui_layouts_window as ulw
        except ImportError:
            self.skipTest("the UI Layouts mod is not installed next to this one")
        self.assertIn(("quick_bars", "extra_bars", "layout_capture", "layout_apply"), ulw.PROVIDERS)
        import sys
        self.assertIs(sys.modules["extra_bars"], eb)
        self.assertEqual(ulw.capture_extras(self.win), {"quick_bars": eb.layout_capture(self.win)})

    def test_a_layout_load_and_undo_through_the_ui_layouts_mod_move_the_bars(self):
        """The real UI Layouts load, on a window that has these bars: it saves the places, a load puts them back, UNDO takes it back."""
        try:
            import ui_layouts_window as ulw
        except ImportError:
            self.skipTest("the UI Layouts mod is not installed next to this one")

        class LayoutWin(XWin):
            def _panel_layout_dict(self):
                return {}

            def apply_panel_layout(self, layout):
                pass

            def _save_options_fn(self, **kwargs):
                pass

        win = LayoutWin()
        self.win = win
        self.draw(win)
        eb.runtime(win).barset.set_place("b1", 0.25, 0.5)
        layout = ulw.ui_layouts.new_layout("Desk", (1280, 720), {}, {}, extras=ulw.capture_extras(win))
        self.assertEqual(layout["extras"]["quick_bars"]["bars"][0]["fx"], 0.25)
        eb.runtime(win).barset.set_place("b1", 0.9, 0.9)
        ok, text = ulw.apply_layout(win, layout)
        self.assertTrue(ok, text)
        self.assertEqual((self.bar("b1").fx, self.bar("b1").fy), (0.25, 0.5))
        self.assertEqual(win._ul_undo["extras"]["quick_bars"]["bars"][0]["fx"], 0.9)        # what it replaced
        ulw.undo(win)
        self.assertEqual((self.bar("b1").fx, self.bar("b1").fy), (0.9, 0.9))


# ── the class-skill menu ─────────────────────────────────────────────────────

class SkillMenuTests(Base):
    def setUp(self):
        super().setUp()
        self.given("Combat", "Mining")
        self.draw()

    def test_the_menu_of_an_extra_bar_draws_and_answers_in_that_bars_context(self):
        self.down(self.slot_center(5, "b2"), button=3)
        self.win._draw_class_skill_menu(None)
        self.win._hotbar_skill_menu_event(pygame.event.Event(pygame.MOUSEMOTION, pos=(0, 0)))
        (drawn, answered) = self.win.menu_log
        self.assertEqual(drawn[:2], ("draw", "b2"))
        self.assertEqual(answered[:2], ("event", "b2"))
        self.assertEqual(drawn[2], self.view("b2").state["_hotbar_slot_rects"][0])      # that bar's rectangles, not the games

    def test_the_games_own_menu_runs_as_it_always_did(self):
        self.win._class_skill_menu = {"slot": 3, "rect": None, "rows": []}
        self.win._draw_class_skill_menu(None)
        self.assertEqual(self.win.menu_log, [("draw", None, self.win._hotbar_slot_rects[0])])

    def test_a_menu_for_a_bar_that_was_deleted_falls_back_to_the_games(self):
        self.down(self.slot_center(5, "b2"), button=3)
        eb.remove_bar(self.win, "b2")
        self.win._draw_class_skill_menu(None)
        self.assertIsNone(self.win.menu_log[0][1])

    def test_the_wrappers_say_what_they_wrap(self):
        self.assertEqual(XWin._hotbar_skill_menu_event.__wrapped__.__qualname__, "XWin._hotbar_skill_menu_event")
        self.assertEqual(XWin._draw_class_skill_menu.__wrapped__.__qualname__, "XWin._draw_class_skill_menu")

    def test_installing_twice_wraps_once(self):
        self.assertFalse(eb.install(XWin))
        self.assertFalse(hasattr(XWin._hotbar_skill_menu_event.__wrapped__, "__wrapped__"))

    def test_a_window_class_without_the_menu_is_marked_so(self):
        self.assertTrue(XWin._extra_bars_skill_menu)
        self.assertFalse(PlainWin._extra_bars_skill_menu)


if __name__ == "__main__":
    unittest.main()

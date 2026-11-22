"""Tests for log_out.py: where the button sits under the game's real ESC menu, when it is there, what a press and a release on it do, and
the starting of the new client (relaunch.py is stubbed here; it is tested for real in core)."""

from __future__ import annotations
import modloader

import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

import log_out as lo
from render_mixin import RenderMixin

pygame.init()
pygame.font.init()
SCREEN = (1280, 720)


class Hooked(RenderMixin):
    """The game's real render mixin (so ``_draw_esc_menu`` is the game's own, with every other mod's changes) with the hook on."""


lo.install(Hooked)


def make_window(scale: float = 1.0):
    win = Hooked()
    win._uk = scale
    win._s = lambda px: round(px * scale)
    win._F = lambda size, bold=False: pygame.font.SysFont("consolas", max(6, round(size * scale)), bold=bold)
    win._cached_label = lambda text, color, font=None: (font or win._F(13)).render(text, True, color)
    win._esc_menu_open = True
    win._options_open = False
    win._esc_btn_rects = {}
    win._opt_rects = {}
    win._ui_panels_settings_open = False
    win._capture_settings_open = False
    win._mods_page_open = False
    win.chat = []
    win.chat_log = lambda text, colour=None: win.chat.append(text)
    return win


def event(kind, pos, button=1):
    return pygame.event.Event(kind, pos=pos, button=button)


def down(pos, button=1):
    return event(pygame.MOUSEBUTTONDOWN, pos, button)


def up(pos, button=1):
    return event(pygame.MOUSEBUTTONUP, pos, button)


class Base(unittest.TestCase):
    def setUp(self):
        lo.reset_for_tests()
        self.addCleanup(lo.reset_for_tests)
        self.win = make_window()
        self.restarts: list = []
        self.quits: list = []
        self.restart_ok = True
        for patcher in (mock.patch.object(lo.relaunch, "restart_after_exit", lambda **kw: self.restarts.append(kw) or self.restart_ok),
                        mock.patch.object(lo.pygame.event, "post", lambda e: self.quits.append(e))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def draw(self, win=None, size=SCREEN):
        win = win or self.win
        screen = pygame.Surface(size, pygame.SRCALPHA)
        screen.fill((20, 60, 30, 255))
        win._draw_esc_menu(SimpleNamespace(screen=screen))
        return screen

    def button(self):
        return lo.button_of(self.win)


class GeometryTests(unittest.TestCase):
    def test_the_strip_is_as_wide_as_the_panel_and_just_below_it(self):
        panel = pygame.Rect(500, 200, 288, 360)
        rect = lo.strip_rect(panel, round, 720)
        self.assertEqual((rect.x, rect.w), (panel.x, panel.w))
        self.assertEqual(rect.top, panel.bottom + 6)

    def test_with_no_room_below_it_goes_above(self):
        panel = pygame.Rect(500, 360, 288, 340)
        rect = lo.strip_rect(panel, round, 720)
        self.assertEqual(rect.bottom, panel.top - 6)

    def test_it_never_leaves_the_top_of_the_screen(self):
        rect = lo.strip_rect(pygame.Rect(0, 10, 288, 700), round, 720)
        self.assertGreaterEqual(rect.top, 0)

    def test_the_button_sits_inside_its_panel_with_the_menus_padding(self):
        for scale in (1.0, 1.5, 2.0):
            s = lambda px, k=scale: round(px * k)
            strip = lo.strip_rect(pygame.Rect(400, 100, s(288), s(300)), s, 1080)
            button = lo._button_rect(strip, s)
            self.assertTrue(strip.contains(button))
            self.assertEqual(button.h, s(48))
            self.assertEqual(button.x - strip.x, s(14))


class DrawTests(Base):
    def test_the_button_is_below_the_menu_panel_on_the_screen_and_clear_of_every_menu_button(self):
        self.draw()
        panel = self.win._esc_btn_rects["panel"]
        button = self.button()
        self.assertGreater(button.top, panel.bottom)
        self.assertTrue(pygame.Rect(0, 0, *SCREEN).contains(button))
        for key, rect in self.win._esc_btn_rects.items():
            self.assertFalse(rect.colliderect(button), key)

    def test_the_games_own_button_table_is_left_exactly_as_the_game_made_it(self):
        self.draw()
        self.assertEqual(set(self.win._esc_btn_rects), {"panel", "options", "capture", "mods", "redeem", "exit"})

    def test_it_is_drawn_amber_so_it_is_not_taken_for_exit_game(self):
        screen = self.draw()
        button = self.button()
        self.assertEqual(screen.get_at((button.right - 8, button.centery))[:3], (60, 44, 12))
        exit_rect = self.win._esc_btn_rects["exit"]
        self.assertNotEqual(screen.get_at((exit_rect.right - 8, exit_rect.centery))[:3], (60, 44, 12))

    def test_the_button_has_a_label(self):
        screen = self.draw()
        button = self.button()
        label_area = pygame.Rect(button.x + 10, button.y + 4, 120, button.h - 8)
        colours = {tuple(screen.get_at((x, y))[:3]) for x in range(label_area.left, label_area.right)
                   for y in range(label_area.top, label_area.bottom)}
        self.assertGreater(len(colours), 3)                                    # anti-aliased text on the fill

    def test_the_game_menu_is_drawn_as_before(self):
        plain = make_window()
        plain.__class__ = RenderMixin                                         # no hook at all
        before = self.draw(plain)
        after = self.draw()
        panel = self.win._esc_btn_rects["panel"]
        self.assertEqual(pygame.image.tobytes(before.subsurface(panel), "RGBA"), pygame.image.tobytes(after.subsurface(panel), "RGBA"))

    def test_it_follows_the_panel_at_other_screen_and_ui_sizes(self):
        for scale, size in ((1.0, (1920, 1080)), (1.5, (1920, 1080)), (2.0, (2560, 1440)), (1.0, (1024, 600))):
            win = make_window(scale)
            self.draw(win, size)
            button = lo.button_of(win)
            self.assertTrue(pygame.Rect(0, 0, *size).contains(button), (scale, size))
            self.assertFalse(win._esc_btn_rects["panel"].colliderect(button), (scale, size))

    def test_there_is_no_button_when_the_menu_is_closed(self):
        self.draw()
        self.win._esc_menu_open = False
        self.draw()
        self.assertIsNone(lo.button_of(self.win))

    def test_there_is_no_button_on_options_or_the_pages_that_replace_the_menu(self):
        for flag in ("_options_open", "_ui_panels_settings_open", "_capture_settings_open", "_mods_page_open"):
            win = make_window()
            self.draw(win)
            self.assertIsNotNone(lo.button_of(win), flag)
            setattr(win, flag, True)                                           # (the game closes the menu itself when it opens one)
            lo.draw_strip(win, SimpleNamespace(screen=pygame.Surface(SCREEN)))
            self.assertIsNone(lo.button_of(win), flag)
            self.assertFalse(lo.menu_showing(win), flag)

    def test_a_flag_that_is_set_while_the_menu_is_still_open_hides_it_too(self):
        self.draw()
        self.win._mods_page_open = True
        lo.draw_strip(self.win, SimpleNamespace(screen=pygame.Surface(SCREEN)))
        self.assertIsNone(lo.button_of(self.win))


class FilterTests(Base):
    def setUp(self):
        super().setUp()
        self.draw()
        self.at = self.button().center
        self.away = (5, 5)

    def test_a_press_on_the_button_is_used_up_and_arms_it(self):
        self.assertEqual(lo.filter_events(self.win, [down(self.at)]), [])
        self.assertTrue(self.win._lo_armed)
        self.assertEqual(self.restarts, [])

    def test_the_release_on_the_button_logs_out_and_is_used_up(self):
        got = lo.filter_events(self.win, [down(self.at), up(self.at)])
        self.assertEqual(got, [])
        self.assertEqual(self.restarts, [{}])
        self.assertEqual([e.type for e in self.quits], [pygame.QUIT])

    def test_the_press_and_the_release_may_come_in_different_frames(self):
        lo.filter_events(self.win, [down(self.at)])
        self.assertEqual(lo.filter_events(self.win, [up(self.at)]), [])
        self.assertEqual(len(self.quits), 1)

    def test_a_release_somewhere_else_cancels_it(self):
        got = lo.filter_events(self.win, [down(self.at), up(self.away)])
        self.assertEqual(got, [])                                              # (the release belongs to the press that was used up)
        self.assertEqual(self.restarts, [])
        self.assertEqual(self.quits, [])
        self.assertFalse(self.win._lo_armed)

    def test_a_release_with_no_press_on_the_button_is_the_games(self):
        release = up(self.at)
        self.assertEqual(lo.filter_events(self.win, [release]), [release])
        self.assertEqual(self.quits, [])

    def test_other_events_go_on_to_the_game_in_order(self):
        events = [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a, mod=0, unicode="a"), down(self.away), down(self.at, button=3),
                  pygame.event.Event(pygame.MOUSEMOTION, pos=self.at, rel=(0, 0), buttons=(0, 0, 0)), up(self.away), up(self.at, button=3)]
        self.assertEqual(lo.filter_events(self.win, list(events)), events)
        self.assertEqual(self.quits, [])

    def test_the_button_is_taken_out_of_a_longer_list_and_the_rest_is_kept(self):
        key = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a, mod=0, unicode="a")
        other = down(self.away)
        got = lo.filter_events(self.win, [key, down(self.at), other, up(self.at)])
        self.assertEqual(got, [key, other])
        self.assertEqual(len(self.quits), 1)

    def test_with_the_menu_closed_nothing_is_taken_and_an_armed_press_is_dropped(self):
        lo.filter_events(self.win, [down(self.at)])
        self.win._esc_menu_open = False
        events = [down(self.at), up(self.at)]
        self.assertEqual(lo.filter_events(self.win, list(events)), events)
        self.assertFalse(self.win._lo_armed)
        self.assertEqual(self.quits, [])

    def test_before_the_button_has_been_drawn_nothing_is_taken(self):
        win = make_window()
        events = [down((600, 400)), up((600, 400))]
        self.assertEqual(lo.filter_events(win, list(events)), events)

    def test_a_click_inside_the_menu_panel_is_still_the_games(self):
        panel = self.win._esc_btn_rects["panel"]
        events = [down(panel.center), up(panel.center)]
        self.assertEqual(lo.filter_events(self.win, list(events)), events)


class BeginTests(Base):
    def test_it_starts_the_new_client_and_closes_this_one_the_way_exit_game_does(self):
        self.assertTrue(lo.begin(self.win))
        self.assertEqual(self.restarts, [{}])
        self.assertEqual([e.type for e in self.quits], [pygame.QUIT])

    def test_it_is_done_once_however_often_it_is_clicked(self):
        lo.begin(self.win)
        self.assertTrue(lo.begin(self.win))
        self.assertEqual(len(self.restarts), 1)
        self.assertEqual(len(self.quits), 1)

    def test_when_the_restart_cannot_be_set_up_the_game_stays_open_and_says_why(self):
        self.restart_ok = False
        self.assertFalse(lo.begin(self.win))
        self.assertEqual(self.quits, [])
        self.assertEqual(len(self.win.chat), 1)
        self.assertIn("Exit Game", self.win.chat[0])
        self.restart_ok = True
        self.assertTrue(lo.begin(self.win))                                   # a second try is possible
        self.assertEqual(len(self.quits), 1)

    def test_a_window_without_a_chat_does_not_make_it_fail(self):
        self.restart_ok = False
        del self.win.chat_log
        self.assertFalse(lo.begin(self.win))


class SwitchTests(Base):
    def test_an_error_in_the_draw_switches_the_button_off_and_the_menu_still_draws(self):
        with mock.patch.object(lo, "draw_strip", side_effect=RuntimeError("boom")), self.assertLogs("log_out", level="ERROR"):
            self.draw()
        self.assertFalse(lo.enabled())
        self.assertIn("exit", self.win._esc_btn_rects)

    def test_an_error_in_the_filter_switches_it_off_and_lets_every_event_through(self):
        self.draw()
        events = [down(self.button().center)]
        with mock.patch.object(lo, "menu_showing", side_effect=RuntimeError("boom")), self.assertLogs("log_out", level="ERROR"):
            self.assertEqual(lo.filter_events(self.win, list(events)), events)
        self.assertEqual(lo.filter_events(self.win, list(events)), events)

    def test_the_environment_switch_turns_it_off(self):
        with mock.patch.dict(os.environ, {lo.ENV_OFF: "1"}):
            self.draw()
            events = [down((10, 10))]
            self.assertEqual(lo.filter_events(self.win, list(events)), events)
        self.assertIsNone(lo.button_of(self.win))

    def test_installing_is_idempotent_and_reports_no_problem(self):
        class Another(RenderMixin):
            pass

        self.assertEqual(lo.install(Another), [])
        self.assertEqual(lo.install(Another), [])
        self.assertTrue(Another.__dict__["_log_out_installed"])


class GamePatchTests(unittest.TestCase):
    """What the mod assumes about the game, pinned so an update that changes it says so."""

    def setUp(self):
        self.client = modloader.game_source("Client.py")

    def test_the_patched_client_installs_the_hook_and_filters_the_frames_events(self):
        self.assertIn("log_out.install(RenderMixin)", self.client)
        self.assertIn("_frame_events = log_out.filter_events(self, _frame_events)", self.client)

    def test_exit_game_closes_by_posting_quit_on_the_release_as_this_does(self):
        self.assertIn("pygame.event.post(pygame.event.Event(pygame.QUIT))", self.client)
        self.assertIn("if self._esc_exit_armed:", self.client)

    def test_a_press_outside_the_menu_panel_closes_the_menu_which_is_why_the_press_is_taken_first(self):
        self.assertIn("# Click-outside closes esc menu", self.client)

    def test_the_hooked_method_takes_the_frame_context(self):
        import inspect
        self.assertEqual(list(inspect.signature(RenderMixin._draw_esc_menu).parameters), ["self", "ctx"])

    def test_the_escape_menu_is_a_modal_the_pointer_router_gives_every_click(self):
        self.assertRegex(self.client, r'_queue_modal\(\s*"escape_menu",')


if __name__ == "__main__":
    unittest.main()

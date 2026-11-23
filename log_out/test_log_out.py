"""Tests for log_out.py (Switch User): where the button and the list of characters sit under the game's real ESC menu, what presses and
releases do, the restart that logs in as the chosen character, and the login screen's side of it (the real LoginScreen). relaunch.py is
stubbed here; it is tested for real in core."""

from __future__ import annotations
import modloader

import importlib.abc
import importlib.util
import os
import sys
import tempfile
import unittest
import weakref
from pathlib import Path
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


class FakeLogin:
    """The login screen as far as the mod asks of it: the saved accounts."""

    def __init__(self, *names):
        self.names = list(names)

    def _saved_account_names(self):
        return list(self.names)


def make_window(scale: float = 1.0, username: "str | None" = "alice"):
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
    win._username = username
    win.chat = []
    win.chat_log = lambda text, colour=None: win.chat.append(text)
    return win


def event(kind, pos, button=1):
    return pygame.event.Event(kind, pos=pos, button=button)


def down(pos, button=1):
    return event(pygame.MOUSEBUTTONDOWN, pos, button)


def up(pos, button=1):
    return event(pygame.MOUSEBUTTONUP, pos, button)


def wheel(y):
    return pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=y)


class Base(unittest.TestCase):
    def setUp(self):
        lo.reset_for_tests()
        self.addCleanup(lo.reset_for_tests)
        self.login = FakeLogin("alice", "bob", "carol")
        lo._login_ref = weakref.ref(self.login)
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

    def state(self):
        return self.win._lo_state

    def open_list(self):
        """Draw, click the button, draw again: the list of characters is up and its rows are known."""
        self.draw()
        lo.filter_events(self.win, [down(self.button().center), up(self.button().center)])
        self.draw()
        self.assertTrue(self.state()["open"])

    def row(self, account):
        for rect, name, enabled in self.state()["rows"]:
            if name == account:
                return rect
        raise AssertionError(f"no row for {account!r}")


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

    def test_the_list_is_centred_and_has_a_row_per_account_and_a_last_one_for_the_login_screen(self):
        panel, rows, max_scroll, scroll = lo.chooser_layout(["alice", "bob"], "alice", SCREEN, round)
        self.assertEqual(panel.centerx, SCREEN[0] // 2)
        self.assertEqual([(account, enabled) for _r, account, enabled in rows], [("alice", False), ("bob", True), (None, True)])
        self.assertEqual((max_scroll, scroll), (0, 0))

    def test_the_rows_are_inside_the_panel_in_order_and_do_not_touch(self):
        for scale in (1.0, 1.5, 2.0):
            s = lambda px, k=scale: round(px * k)
            panel, rows, _m, _s = lo.chooser_layout(["a", "b", "c", "d"], "b", (1920, 1080), s)
            self.assertTrue(pygame.Rect(0, 0, 1920, 1080).contains(panel))
            for i, (rect, _a, _e) in enumerate(rows):
                self.assertTrue(panel.contains(rect), (scale, i))
                self.assertEqual(rect.h, s(40))
                if i:
                    self.assertGreater(rect.top, rows[i - 1][0].bottom)

    def test_no_saved_account_still_has_the_login_screen_row(self):
        _p, rows, _m, _s = lo.chooser_layout([], None, SCREEN, round)
        self.assertEqual([(a, e) for _r, a, e in rows], [(None, True)])

    def test_the_character_being_played_is_not_clickable(self):
        _p, rows, _m, _s = lo.chooser_layout(["alice", "bob"], "bob", SCREEN, round)
        self.assertEqual({a: e for _r, a, e in rows}, {"alice": True, "bob": False, None: True})

    def test_a_long_list_shows_what_fits_and_scrolls_to_the_end(self):
        names = [f"pilot{i:02d}" for i in range(40)]
        panel, rows, max_scroll, scroll = lo.chooser_layout(names, None, SCREEN, round)
        self.assertLess(len(rows), 41)
        self.assertGreater(max_scroll, 0)
        self.assertLessEqual(panel.height, int(SCREEN[1] * 0.9))
        self.assertEqual(rows[0][1], "pilot00")
        _p, rows, _m, scroll = lo.chooser_layout(names, None, SCREEN, round, scroll=10 ** 6)
        self.assertEqual(scroll, max_scroll)
        self.assertIsNone(rows[-1][1])                                          # the end of the list is the login screen entry
        _p, rows, _m, scroll = lo.chooser_layout(names, None, SCREEN, round, scroll=-5)
        self.assertEqual((scroll, rows[0][1]), (0, "pilot00"))

    def test_a_narrow_screen_gets_a_narrower_panel(self):
        panel, rows, _m, _s = lo.chooser_layout(["a"], None, (260, 720), round)
        self.assertLessEqual(panel.width, 240)
        self.assertTrue(panel.contains(rows[0][0]))


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
        self.open_list()
        # (the game's own buttons: options, redeem, logout - new in v0.5.88 - and exit, plus the UI Panels mod's two)
        self.assertEqual(set(self.win._esc_btn_rects),
                         {"panel", "options", "capture", "mods", "redeem", "logout", "exit"})

    def test_it_is_drawn_amber_so_it_is_not_taken_for_exit_game(self):
        screen = self.draw()
        button = self.button()
        self.assertEqual(screen.get_at((button.right - 8, button.centery))[:3], (60, 44, 12))
        exit_rect = self.win._esc_btn_rects["exit"]
        self.assertNotEqual(screen.get_at((exit_rect.right - 8, exit_rect.centery))[:3], (60, 44, 12))

    def test_the_button_has_a_label(self):
        screen = self.draw()
        button = self.button()
        area = pygame.Rect(button.x + 10, button.y + 4, 120, button.h - 8)
        colours = {tuple(screen.get_at((x, y))[:3]) for x in range(area.left, area.right) for y in range(area.top, area.bottom)}
        self.assertGreater(len(colours), 3)

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

    def test_the_list_is_only_drawn_once_it_is_opened(self):
        self.draw()
        self.assertIsNone(self.state()["panel"])
        self.assertEqual(self.state()["rows"], [])
        self.open_list()
        self.assertIsNotNone(self.state()["panel"])
        self.assertEqual([account for _r, account, _e in self.state()["rows"]], ["alice", "bob", "carol", None])

    def test_the_list_colours_say_which_is_which(self):
        self.open_list()
        screen = self.draw()
        at = lambda rect: tuple(screen.get_at((rect.right - 8, rect.centery))[:3])
        self.assertEqual(at(self.row("bob")), (60, 44, 12))                    # a character: amber
        self.assertEqual(at(self.row("alice")), (22, 24, 38))                  # the one being played: dim
        self.assertEqual(at(self.row(None)), (20, 40, 90))                     # the login screen: blue

    def test_the_list_is_opaque_and_dims_the_menu_behind_it(self):
        closed = self.draw()
        self.open_list()
        opened = self.draw()
        panel = self.state()["panel"]
        gap = (panel.x + 3, panel.centery)                                      # between the panel's edge and its rows
        self.assertEqual(opened.get_at(gap)[:3], (10, 12, 28))                  # not blended with what is behind it
        corner = (5, 5)
        self.assertLess(sum(opened.get_at(corner)[:3]), sum(closed.get_at(corner)[:3]))        # the screen outside is darker
        options = self.win._esc_btn_rects["options"]
        outside = (options.right - 4, options.centery)                          # a menu button the list does not cover
        if not panel.collidepoint(outside):
            self.assertLess(sum(opened.get_at(outside)[:3]), sum(closed.get_at(outside)[:3]))

    def test_there_is_no_button_when_the_menu_is_closed_and_the_list_closes_with_it(self):
        self.open_list()
        self.win._esc_menu_open = False
        self.draw()
        self.assertIsNone(self.button())
        self.assertFalse(self.state()["open"])
        self.assertEqual(self.state()["rows"], [])

    def test_there_is_no_button_on_options_or_the_pages_that_replace_the_menu(self):
        for flag in ("_options_open", "_ui_panels_settings_open", "_capture_settings_open", "_mods_page_open"):
            win = make_window()
            self.draw(win)
            self.assertIsNotNone(lo.button_of(win), flag)
            setattr(win, flag, True)                                           # (the game closes the menu itself when it opens one)
            lo.draw_strip(win, SimpleNamespace(screen=pygame.Surface(SCREEN)))
            self.assertIsNone(lo.button_of(win), flag)
            self.assertFalse(lo.menu_showing(win), flag)

    def test_a_scrolled_list_is_clamped_when_drawn(self):
        self.login.names = [f"p{i:02d}" for i in range(40)]
        self.draw()
        lo.filter_events(self.win, [down(self.button().center), up(self.button().center)])
        self.state()["scroll"] = 10 ** 6
        self.draw()
        self.assertLess(self.state()["scroll"], 10 ** 6)
        self.assertIsNone(self.state()["rows"][-1][1])


class ButtonTests(Base):
    def setUp(self):
        super().setUp()
        self.draw()
        self.at = self.button().center
        self.away = (5, 5)

    def test_a_press_on_the_button_is_used_up_and_arms_it(self):
        self.assertEqual(lo.filter_events(self.win, [down(self.at)]), [])
        self.assertTrue(self.state()["button_armed"])
        self.assertFalse(self.state()["open"])

    def test_the_release_on_the_button_opens_the_list_and_is_used_up(self):
        self.assertEqual(lo.filter_events(self.win, [down(self.at), up(self.at)]), [])
        self.assertTrue(self.state()["open"])
        self.assertEqual(self.restarts, [])                                    # opening is not switching
        self.assertEqual(self.quits, [])

    def test_the_press_and_the_release_may_come_in_different_frames(self):
        lo.filter_events(self.win, [down(self.at)])
        self.assertEqual(lo.filter_events(self.win, [up(self.at)]), [])
        self.assertTrue(self.state()["open"])

    def test_a_release_somewhere_else_cancels_it(self):
        self.assertEqual(lo.filter_events(self.win, [down(self.at), up(self.away)]), [])
        self.assertFalse(self.state()["open"])
        self.assertFalse(self.state()["button_armed"])

    def test_a_release_with_no_press_on_the_button_is_the_games(self):
        release = up(self.at)
        self.assertEqual(lo.filter_events(self.win, [release]), [release])
        self.assertFalse(self.state()["open"])

    def test_other_events_go_on_to_the_game_in_order(self):
        events = [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a, mod=0, unicode="a"), down(self.away), down(self.at, button=3),
                  pygame.event.Event(pygame.MOUSEMOTION, pos=self.at, rel=(0, 0), buttons=(0, 0, 0)), up(self.away), up(self.at, button=3)]
        self.assertEqual(lo.filter_events(self.win, list(events)), events)
        self.assertFalse(self.state()["open"])

    def test_the_button_is_taken_out_of_a_longer_list_and_the_rest_is_kept(self):
        key = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a, mod=0, unicode="a")
        other = down(self.away)
        self.assertEqual(lo.filter_events(self.win, [key, down(self.at), other, up(self.at)]), [key, other])
        self.assertTrue(self.state()["open"])

    def test_with_the_menu_closed_nothing_is_taken_and_an_armed_press_is_dropped(self):
        lo.filter_events(self.win, [down(self.at)])
        self.win._esc_menu_open = False
        events = [down(self.at), up(self.at)]
        self.assertEqual(lo.filter_events(self.win, list(events)), events)
        self.assertFalse(self.state()["button_armed"])

    def test_before_the_button_has_been_drawn_nothing_is_taken(self):
        win = make_window()
        events = [down((600, 400)), up((600, 400))]
        self.assertEqual(lo.filter_events(win, list(events)), events)

    def test_a_click_inside_the_menu_panel_is_still_the_games(self):
        panel = self.win._esc_btn_rects["panel"]
        events = [down(panel.center), up(panel.center)]
        self.assertEqual(lo.filter_events(self.win, list(events)), events)


class ListTests(Base):
    def setUp(self):
        super().setUp()
        self.open_list()

    def pick(self, account):
        centre = self.row(account).center
        return lo.filter_events(self.win, [down(centre), up(centre)])

    def test_picking_a_character_switches_to_them(self):
        self.assertEqual(self.pick("bob"), [])
        self.assertEqual(self.restarts, [{"env": {lo.ENV_AUTO_LOGIN: "bob"}}])
        self.assertEqual([e.type for e in self.quits], [pygame.QUIT])

    def test_picking_the_login_screen_restarts_without_a_character(self):
        self.pick(None)
        self.assertEqual(self.restarts, [{"env": {lo.ENV_AUTO_LOGIN: ""}}])
        self.assertEqual(len(self.quits), 1)

    def test_the_press_and_the_release_may_come_in_different_frames(self):
        centre = self.row("carol").center
        lo.filter_events(self.win, [down(centre)])
        self.assertEqual(self.restarts, [])
        lo.filter_events(self.win, [up(centre)])
        self.assertEqual(self.restarts, [{"env": {lo.ENV_AUTO_LOGIN: "carol"}}])

    def test_pressing_on_one_row_and_releasing_on_another_does_nothing(self):
        lo.filter_events(self.win, [down(self.row("bob").center), up(self.row("carol").center)])
        self.assertEqual(self.restarts, [])
        self.assertTrue(self.state()["open"])

    def test_pressing_on_a_row_and_releasing_off_it_does_nothing(self):
        lo.filter_events(self.win, [down(self.row("bob").center), up((5, 5))])
        self.assertEqual(self.restarts, [])
        self.assertTrue(self.state()["open"])

    def test_the_character_being_played_cannot_be_picked(self):
        self.assertEqual(self.pick("alice"), [])
        self.assertEqual(self.restarts, [])
        self.assertTrue(self.state()["open"])

    def test_the_title_and_the_gaps_do_nothing_and_leave_the_list_open(self):
        panel = self.state()["panel"]
        for pos in ((panel.centerx, panel.y + 6), (panel.x + 3, panel.centery)):
            self.assertEqual(lo.filter_events(self.win, [down(pos), up(pos)]), [])
        self.assertTrue(self.state()["open"])
        self.assertEqual(self.restarts, [])

    def test_a_press_outside_closes_the_list_and_neither_it_nor_its_release_reach_the_game(self):
        outside = (5, 5)
        self.assertEqual(lo.filter_events(self.win, [down(outside)]), [])
        self.assertFalse(self.state()["open"])
        self.assertEqual(lo.filter_events(self.win, [up(outside)]), [])                # (the release of the press that closed it)
        later = up(outside)
        self.assertEqual(lo.filter_events(self.win, [later]), [later])                 # but only that one
        self.assertEqual(self.restarts, [])

    def test_a_right_click_closes_it_too(self):
        self.assertEqual(lo.filter_events(self.win, [down(self.row("bob").center, button=3)]), [])
        self.assertFalse(self.state()["open"])
        self.assertEqual(self.restarts, [])

    def test_every_mouse_event_is_used_up_while_it_is_open_and_the_rest_go_on(self):
        key = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a, mod=0, unicode="a")
        motion = pygame.event.Event(pygame.MOUSEMOTION, pos=(5, 5), rel=(0, 0), buttons=(0, 0, 0))
        got = lo.filter_events(self.win, [key, wheel(1), motion, up((5, 5), button=3)])
        self.assertEqual(got, [key, motion])

    def test_the_wheel_moves_a_long_list(self):
        self.login.names = [f"p{i:02d}" for i in range(40)]
        self.draw()
        before = self.state()["rows"][0][1]
        lo.filter_events(self.win, [wheel(-3)])
        self.draw()
        self.assertNotEqual(self.state()["rows"][0][1], before)
        lo.filter_events(self.win, [wheel(10)])
        self.draw()
        self.assertEqual(self.state()["rows"][0][1], "p00")                          # (and never above the top)

    def test_the_menu_closing_closes_the_list(self):
        self.win._esc_menu_open = False
        events = [down((5, 5))]
        self.assertEqual(lo.filter_events(self.win, list(events)), events)
        self.assertFalse(self.state()["open"])

    def test_opening_the_list_again_starts_at_the_top(self):
        self.state()["scroll"] = 5
        lo.filter_events(self.win, [down((5, 5)), up((5, 5))])
        self.draw()
        lo.filter_events(self.win, [down(self.button().center), up(self.button().center)])
        self.assertEqual(self.state()["scroll"], 0)

    def test_a_character_is_picked_once_however_often_it_is_clicked(self):
        centre = self.row("bob").center
        lo.filter_events(self.win, [down(centre), up(centre), down(centre), up(centre)])
        self.assertEqual(len(self.restarts), 1)


class BeginTests(Base):
    def test_it_starts_the_new_client_for_the_character_and_closes_this_one_the_way_exit_game_does(self):
        self.assertTrue(lo.begin(self.win, "bob"))
        self.assertEqual(self.restarts, [{"env": {lo.ENV_AUTO_LOGIN: "bob"}}])
        self.assertEqual([e.type for e in self.quits], [pygame.QUIT])

    def test_it_is_done_once_however_often_it_is_asked(self):
        lo.begin(self.win, "bob")
        self.assertTrue(lo.begin(self.win, "carol"))
        self.assertEqual(len(self.restarts), 1)
        self.assertEqual(len(self.quits), 1)

    def test_when_the_restart_cannot_be_set_up_the_game_stays_open_and_says_why(self):
        self.restart_ok = False
        self.assertFalse(lo.begin(self.win, "bob"))
        self.assertEqual(self.quits, [])
        self.assertEqual(len(self.win.chat), 1)
        self.assertIn("Exit Game", self.win.chat[0])
        self.restart_ok = True
        self.assertTrue(lo.begin(self.win, "bob"))                                  # a second try is possible
        self.assertEqual(len(self.quits), 1)

    def test_a_window_without_a_chat_does_not_make_it_fail(self):
        self.restart_ok = False
        del self.win.chat_log
        self.assertFalse(lo.begin(self.win, "bob"))


class SavedAccountsTests(Base):
    def test_the_names_come_from_the_login_screen(self):
        self.assertEqual(lo.saved_accounts(), ["alice", "bob", "carol"])

    def test_without_a_login_screen_there_are_none(self):
        lo._login_ref = None
        self.assertEqual(lo.saved_accounts(), [])
        self.assertIsNone(lo.login_screen())

    def test_a_login_screen_that_is_gone_is_none(self):
        lo._login_ref = weakref.ref(FakeLogin())
        self.assertEqual(lo.saved_accounts(), [])

    def test_a_login_screen_that_errors_gives_none(self):
        class Broken:
            def _saved_account_names(self):
                raise RuntimeError("no")

        broken = Broken()
        lo._login_ref = weakref.ref(broken)
        self.assertEqual(lo.saved_accounts(), [])

    def test_the_hook_keeps_the_login_screen_weakly(self):
        other = FakeLogin("x")
        lo.remember_login_screen(other)
        self.assertIs(lo.login_screen(), other)
        del other
        self.assertIsNone(lo.login_screen())

    def test_without_the_login_screen_the_list_is_just_the_login_screen_row(self):
        lo._login_ref = None
        self.draw()
        lo.filter_events(self.win, [down(self.button().center), up(self.button().center)])
        self.draw()
        self.assertEqual([account for _r, account, _e in self.state()["rows"]], [None])

    def test_the_current_user_may_be_unknown(self):
        win = make_window(username=None)
        self.draw(win)
        lo.filter_events(win, [down(lo.button_of(win).center), up(lo.button_of(win).center)])
        self.draw(win)
        self.assertTrue(all(enabled for _r, _a, enabled in win._lo_state["rows"]))


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


class LoginScreenTests(unittest.TestCase):
    """On the game's real login screen: what the next client does with the account the previous one asked for."""

    @classmethod
    def setUpClass(cls):
        import login

        class Hooked(login.LoginScreen):
            def load_options(self):                       # (the real one reads the player's options.txt)
                return 0.5, False

        cls.Hooked = Hooked
        cls.problems = lo.install_login(Hooked)

    def setUp(self):
        lo.reset_for_tests()
        self.addCleanup(lo.reset_for_tests)
        self.screen_obj = self.Hooked(lambda _line: None, lambda _line: None)
        self.screen_obj._saved_accounts = {"alice": "pw-a", "bob": "pw-b"}
        self.surface = pygame.Surface((1200, 720))
        self.fonts = (pygame.font.Font(None, 28), pygame.font.Font(None, 18), pygame.font.Font(None, 14))
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(lo.ENV_AUTO_LOGIN, None)

    def draw(self, screen_obj=None):
        with mock.patch.object(self.Hooked, "_draw_background", lambda self_, screen: None):
            (screen_obj or self.screen_obj).draw(self.surface, self.fonts)

    def test_installing_reports_no_problem_and_is_idempotent(self):
        self.assertEqual(self.problems, [])
        self.assertEqual(lo.install_login(self.Hooked), [])

    def test_loading_the_options_keeps_the_login_screen_for_the_menu(self):
        self.assertIsNone(lo.login_screen())
        self.screen_obj.load_options()
        self.assertIs(lo.login_screen(), self.screen_obj)
        self.assertEqual(lo.saved_accounts(), ["alice", "bob"])

    def test_the_account_the_previous_client_asked_for_is_selected_and_connected_once(self):
        os.environ[lo.ENV_AUTO_LOGIN] = "bob"
        self.draw()
        obj = self.screen_obj
        self.assertEqual((obj._user_text, obj._pass_text), ("bob", "pw-b"))
        self.assertEqual((obj._pending_user, obj._pending_pass, obj._pending_mode), ("bob", "pw-b", "LOGIN"))
        self.assertEqual(obj._login_status, "Authenticating...")
        self.assertFalse(obj._login_err)

    def test_the_variable_is_used_up_so_a_later_restart_does_not_log_in_again(self):
        os.environ[lo.ENV_AUTO_LOGIN] = "bob"
        self.draw()
        self.assertNotIn(lo.ENV_AUTO_LOGIN, os.environ)

    def test_it_happens_on_the_first_frame_only(self):
        os.environ[lo.ENV_AUTO_LOGIN] = "bob"
        self.draw()
        self.screen_obj._pending_user = "changed"
        os.environ[lo.ENV_AUTO_LOGIN] = "alice"
        self.draw()
        self.assertEqual(self.screen_obj._pending_user, "changed")
        self.assertEqual(os.environ[lo.ENV_AUTO_LOGIN], "alice")                    # (left alone: this screen has had its one go)

    def test_with_no_variable_or_an_empty_one_nothing_happens(self):
        self.draw()
        self.assertFalse(getattr(self.screen_obj, "_pending_user", ""))
        other = self.Hooked(lambda _line: None, lambda _line: None)
        other._saved_accounts = {"alice": "pw-a"}
        os.environ[lo.ENV_AUTO_LOGIN] = ""
        self.draw(other)
        self.assertFalse(getattr(other, "_pending_user", ""))
        self.assertNotIn(lo.ENV_AUTO_LOGIN, os.environ)

    def test_an_account_that_is_not_saved_says_so_and_connects_nothing(self):
        os.environ[lo.ENV_AUTO_LOGIN] = "mallory"
        self.draw()
        self.assertEqual(self.screen_obj._login_status, "'mallory' is not a saved account: type it in.")
        self.assertTrue(self.screen_obj._login_err)
        self.assertFalse(getattr(self.screen_obj, "_pending_user", ""))

    def test_the_login_goes_to_the_server_when_it_is_ready(self):
        sent = []
        screen = self.Hooked(sent.append, lambda _line: None)
        screen._saved_accounts = {"bob": "pw-b"}
        screen._device_id_future = screen._vm_future = None
        os.environ[lo.ENV_AUTO_LOGIN] = "bob"
        self.draw(screen)
        self.assertEqual(sent, [])                                                  # not connected yet: it waits for READY
        screen.handle_line("READY")
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0].startswith("LOGIN "))
        self.assertIn('"username":"bob"', sent[0])

    def test_the_environment_switch_turns_the_login_off(self):
        os.environ[lo.ENV_AUTO_LOGIN] = "bob"
        with mock.patch.dict(os.environ, {lo.ENV_OFF: "1"}):
            self.draw()
        self.assertFalse(getattr(self.screen_obj, "_pending_user", ""))

    def test_an_error_switches_the_mod_off_and_the_login_screen_goes_on(self):
        os.environ[lo.ENV_AUTO_LOGIN] = "bob"
        with mock.patch.object(self.screen_obj, "_select_saved_account", side_effect=RuntimeError("boom")), \
                self.assertLogs("log_out", level="ERROR"):
            self.draw()
        self.assertFalse(lo.enabled())


class GamePatchTests(unittest.TestCase):
    """What the mod assumes about the game, pinned so an update that changes it says so."""

    def setUp(self):
        self.client = modloader.game_source("Client.py")

    def test_the_patched_client_installs_the_hooks_and_filters_the_frames_events(self):
        self.assertIn("log_out.install(RenderMixin)", self.client)
        self.assertIn("log_out.install_login(LoginScreen)", self.client)
        self.assertIn("_frame_events = log_out.filter_events(self, _frame_events)", self.client)

    def test_the_login_screen_is_imported_before_the_hooks_are_installed(self):
        self.assertLess(self.client.index("from login import LoginScreen"), self.client.index("log_out.install_login(LoginScreen)"))

    def test_exit_game_closes_by_posting_quit_on_the_release_as_this_does(self):
        self.assertIn("pygame.event.post(pygame.event.Event(pygame.QUIT))", self.client)
        self.assertIn("if self._esc_exit_armed:", self.client)

    def test_a_press_outside_the_menu_panel_closes_the_menu_which_is_why_the_press_is_taken_first(self):
        self.assertIn("# Click-outside closes esc menu", self.client)

    def test_the_hooked_methods_take_what_the_hooks_expect(self):
        import inspect
        import login
        self.assertEqual(list(inspect.signature(RenderMixin._draw_esc_menu).parameters), ["self", "ctx"])
        self.assertEqual(list(inspect.signature(login.LoginScreen.load_options).parameters), ["self"])
        self.assertEqual(list(inspect.signature(login.LoginScreen.draw).parameters), ["self", "screen", "fonts"])
        for name in ("_saved_account_names", "_select_saved_account", "_submit", "handle_line"):
            self.assertTrue(callable(getattr(login.LoginScreen, name)), name)

    def test_the_escape_menu_is_a_modal_the_pointer_router_gives_every_click(self):
        self.assertRegex(self.client, r'_queue_modal\(\s*"escape_menu",')

    def test_the_games_own_account_picker_selects_then_submit_connects(self):
        import login
        screen = login.LoginScreen(lambda _l: None, lambda _l: None)
        screen._saved_accounts = {"bob": "pw-b"}
        self.assertTrue(screen._select_saved_account("bob"))
        self.assertFalse(screen._select_saved_account("nobody"))
        screen._submit()
        self.assertEqual((screen._pending_user, screen._pending_pass), ("bob", "pw-b"))


# ── a core mod that is older than this one ───────────────────────────────────

class BlockImports(importlib.abc.MetaPathFinder):
    """Makes ``import <name>`` fail, as it does for a core folder that lacks the file."""

    def __init__(self, *names):
        self.names = set(names)

    def find_spec(self, fullname, path=None, target=None):
        if fullname in self.names:
            raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
        return None


def load_without(path: Path, name: str, *blocked: str):
    """Import the module at *path* afresh, with the *blocked* modules (and any copy already imported) not importable."""
    finder = BlockImports(*blocked)
    saved = {n: sys.modules.pop(n) for n in blocked if n in sys.modules}
    sys.meta_path.insert(0, finder)
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.meta_path.remove(finder)
        sys.modules.pop(name, None)
        sys.modules.update(saved)


class FakeRenderClass:
    pass


class FakeLoginClass:
    pass


class OlderCoreTests(unittest.TestCase):
    PATH = Path(lo.__file__)

    def setUp(self):
        patcher = mock.patch.dict(modloader.ATTACH_FAILURES)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_with_the_helper_present_there_is_no_problem(self):
        self.assertIsNone(lo.core_problem())

    def test_the_module_still_imports_without_relaunch_and_says_what_is_missing(self):
        module = load_without(self.PATH, "log_out_nocore", "relaunch")
        self.assertIsNone(module.relaunch)
        self.assertIn("relaunch.py", module.core_problem())
        self.assertIn("older", module.core_problem())

    def test_a_relaunch_that_lacks_the_function_is_a_problem_too(self):
        with mock.patch.object(lo, "relaunch", SimpleNamespace()):
            self.assertIn("relaunch.restart_after_exit", lo.core_problem())

    def test_installing_with_an_older_core_hooks_nothing_and_reports_on_the_mods_page(self):
        module = load_without(self.PATH, "log_out_nocore2", "relaunch")
        with mock.patch.object(modloader, "install_methods") as install_methods:
            esc = module.install(FakeRenderClass)
            login = module.install_login(FakeLoginClass)
        install_methods.assert_not_called()
        self.assertEqual((len(esc), len(login)), (1, 1))
        self.assertEqual(modloader.ATTACH_FAILURES["log_out"], esc[0])
        self.assertFalse(FakeRenderClass.__dict__.get("_log_out_installed"))
        self.assertFalse(FakeLoginClass.__dict__.get("_log_out_installed"))

    def test_installing_with_a_current_core_still_hooks_both(self):
        with mock.patch.object(modloader, "install_methods", return_value=[]) as install_methods:
            self.assertEqual(lo.install(type("R", (), {})), [])
            self.assertEqual(lo.install_login(type("L", (), {})), [])
        self.assertEqual(install_methods.call_count, 2)
        self.assertNotIn("log_out", modloader.ATTACH_FAILURES)


if __name__ == "__main__":
    unittest.main()

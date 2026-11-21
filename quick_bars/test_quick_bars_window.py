"""Tests for quick_bars_window.py - the QUICK BARS window (the KEYS tab): capture, unbind, reset, the list, the mouse, the chat command,
drawing, and the vanilla lines that open / draw / feed it."""

from __future__ import annotations
import modloader

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

import keybinds as kb
import quick_bars_window as qw
import quick_keys as qk
from gui_window_manager import GuiWindowManager
from keybinds import Combo

SCREEN = (1280, 720)
BACKDROP = (20, 60, 30, 255)
CTRL, SHIFT, ALT = pygame.KMOD_CTRL, pygame.KMOD_SHIFT, pygame.KMOD_ALT


def sync_submit(fn, *args):
    fn(*args)


class FakeWindow:
    """The parts of SolarSystemWindow the window touches."""

    def __init__(self, scale=1.0):
        self._uk = scale
        self._gui_windows = GuiWindowManager()
        self.chat: list = []
        self._fonts: dict = {}

    def _s(self, px):
        return px if self._uk == 1.0 else round(px * self._uk)

    def _instrument_font(self, size, bold=False):
        key = (size, bold)
        if key not in self._fonts:
            self._fonts[key] = pygame.font.SysFont("consolas", max(6, round(size * self._uk)), bold=bold)
        return self._fonts[key]

    def chat_log(self, text, colour=None):
        self.chat.append(text)


class Win(FakeWindow):
    """A class of its own, so ``install`` can wrap it without touching the shared FakeWindow."""

    def _hotbar_drawn_state(self, hover_slot, tooltip=True):
        return ("slots", None)


GATE_CALLS: list = []


def _original_gate(**kwargs):
    GATE_CALLS.append(kwargs)
    return bool(kwargs.get("chat_focused"))


CLIENT_GLOBALS = {"_flight_text_entry_active": _original_gate}
pygame.init()


class Clock:
    def __init__(self, start=1000.0):
        self.now = start

    def monotonic(self):
        return self.now


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode(SCREEN)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "quick_bars.json")
        qk.reset_for_tests()
        qk.configure_for_tests(self.path, sync_submit)
        qw.reset_for_tests()
        self.addCleanup(self._cleanup)
        self.win = self.make_window()

    def _cleanup(self):
        qw.reset_for_tests()
        qk.reset_for_tests()
        self._tmp.cleanup()

    def make_window(self, scale=1.0):
        win = Win(scale)
        qw._ensure(win)
        return win

    size = SCREEN

    def render(self, win=None, size=None, mouse=(5, 5)):
        win = win or self.win
        size = self.size = size or self.size
        screen = pygame.Surface(size, pygame.SRCALPHA)
        screen.fill(BACKDROP)
        manager = win._gui_windows
        with mock.patch("pygame.mouse.get_pos", return_value=mouse):
            manager.begin_frame()
            qw.queue(win, SimpleNamespace(screen=screen))
            manager.flush_draw_queue()
            manager.end_frame()
        return screen

    def open(self, win=None):
        win = win or self.win
        qw.open_window(win)
        self.render(win)
        return win

    def rects(self):
        return self.win._qb_hits["rects"]

    def click(self, pos, win=None, button=1):
        win = win or self.win
        events = [pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=button),
                  pygame.event.Event(pygame.MOUSEBUTTONUP, pos=pos, button=button)]
        with mock.patch("pygame.mouse.get_pos", return_value=pos):
            return qw.filter_events(win, events)

    def press(self, name):
        remaining = self.click(self.rects()[name].center)
        self.render()
        return remaining

    def row(self, action_id):
        """The rectangle of an action's row, scrolling the list until it is drawn."""
        for _ in range(60):
            for rect, found in self.win._qb_hits["rows"]:
                if found == action_id:
                    return rect
            self.win._qb_scroll += self.win._s(40)
            self.render()
        raise AssertionError(f"{action_id} is never drawn")

    def start_capture(self, action_id):
        self.win._qb_scroll = 0
        self.render()
        self.click(self.row(action_id).center)
        self.render()
        self.assertEqual(self.win._qb_capture, action_id)

    def key(self, key, mod=0, win=None, kind=pygame.KEYDOWN):
        win = win or self.win
        event = pygame.event.Event(kind, key=key, mod=mod, unicode="", scancode=0)
        return qw.filter_events(win, [event])

    def combo(self, action_id):
        return qk.table().combo(action_id)


# ── geometry ─────────────────────────────────────────────────────────────────

class GeometryTests(unittest.TestCase):
    def rects(self, scale, size=None):
        s = (lambda px: px) if scale == 1.0 else (lambda px: round(px * scale))
        width, height = size or (s(qw._MIN_W), s(qw._MIN_H))
        window = pygame.Rect(40, 30, width, height)
        return window, qw.layout_rects(window, s)

    def test_everything_is_inside_the_window_at_every_size_and_scale(self):
        for scale in (1.0, 1.25, 1.5, 2.0):
            for grow in (0, 150):
                window, _r = self.rects(scale)
                window = window.inflate(grow, grow)
                r = qw.layout_rects(window, (lambda px: round(px * scale)))
                for name, rect in r.items():
                    self.assertTrue(window.contains(rect), (scale, grow, name, rect, window))
                    self.assertGreater(rect.w, 0)
                    self.assertGreater(rect.h, 0)

    def test_the_parts_do_not_overlap(self):
        for scale in (1.0, 1.5, 2.0):
            _window, r = self.rects(scale)
            names = ["list", "info", "unbind", "reset", "reset_all"]
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    self.assertFalse(r[a].colliderect(r[b]), (scale, a, b))
            self.assertFalse(r["body"].colliderect(r["header"]))
            self.assertFalse(r["body"].colliderect(r["status"]))

    def test_there_is_room_for_a_list_at_the_smallest_size(self):
        _window, r = self.rects(1.0)
        self.assertGreaterEqual(r["list"].h, 100)

    def test_the_window_stays_on_screen_and_above_its_minimum(self):
        win = Win(1.0)
        qw._ensure(win)
        win._qb_rect = pygame.Rect(-50, 9000, 10, 10)
        rect = qw.window_rect(win, 1280, 720)
        self.assertTrue(pygame.Rect(0, 0, 1280, 720).contains(rect))
        self.assertGreaterEqual((rect.w, rect.h), (qw._MIN_W, qw._MIN_H))
        win._qb_rect = None
        self.assertTrue(pygame.Rect(0, 0, 300, 200).contains(qw.window_rect(win, 300, 200)))


class ViewTests(unittest.TestCase):
    def test_the_list_is_a_group_title_then_its_actions_for_every_action(self):
        rows = qw.view_rows()
        self.assertEqual([d for k, d in rows if k == "group"], [kb.GROUP_BAR, kb.GROUP_HOTKEYS, kb.GROUP_LOADOUTS])
        self.assertEqual([d.id for k, d in rows if k == "action"], [a.id for a in kb.CATALOG])
        self.assertEqual(rows[0], ("group", kb.GROUP_BAR))

    def test_the_height_adds_up(self):
        s = lambda px: px
        self.assertEqual(qw.view_height(s), 3 * qw.GROUP_H + len(kb.CATALOG) * qw.ROW_H)


# ── opening and drawing ──────────────────────────────────────────────────────

class OpenTests(Base):
    def test_it_starts_closed_and_toggle_opens_and_closes(self):
        self.assertFalse(qw.is_open(self.win))
        qw.toggle(self.win)
        self.assertTrue(qw.is_open(self.win))
        self.assertEqual(self.win._qb_selected, kb.CATALOG[0].id)
        qw.toggle(self.win)
        self.assertFalse(qw.is_open(self.win))

    def test_closing_stops_waiting_for_a_key(self):
        self.open()
        self.start_capture("hotkey.dock")
        qw.close_window(self.win)
        self.assertIsNone(self.win._qb_capture)

    def test_it_draws_at_every_ui_scale_changed_and_waiting(self):
        qk.set_binding("hotkey.dock", Combo(pygame.K_b, {"ctrl"}))
        qk.set_binding("hotkey.missions", None)
        for scale in (1.0, 1.5, 2.0):
            win = self.make_window(scale)
            qw.open_window(win)
            self.render(win)
            self.assertTrue(win._qb_hits["rects"])
            win._qb_capture = "hotkey.dock"
            self.render(win)
            self.assertTrue(win._qb_hits["rows"])

    def test_the_env_switch_turns_it_off(self):
        qw.open_window(self.win)
        events = [object()]
        with mock.patch.dict(os.environ, {qw.ENV_OFF: "1"}):
            self.assertFalse(qw.is_open(self.win))
            self.assertIs(qw.filter_events(self.win, events), events)
            self.win._qb_hits = {}
            self.render()
            self.assertEqual(self.win._qb_hits, {})
        self.assertTrue(qw.is_open(self.win))

    def test_an_error_while_drawing_switches_it_off_for_the_session(self):
        self.open()
        with mock.patch.object(qw, "_draw", side_effect=RuntimeError("boom")), self.assertLogs(qw.logger, "ERROR"):
            self.render()
        self.assertFalse(qw.is_open(self.win))

    def test_an_error_in_the_event_filter_switches_it_off_and_returns_the_events(self):
        self.open()
        events = [object()]
        with mock.patch.object(qw, "_handle", side_effect=RuntimeError("boom")), self.assertLogs(qw.logger, "ERROR"):
            self.assertIs(qw.filter_events(self.win, events), events)
        self.assertFalse(qw._enabled())

    def test_rows_that_are_scrolled_out_of_view_are_not_hit_targets(self):
        self.open()
        self.assertLess(len(self.win._qb_hits["rows"]), len(kb.CATALOG))
        self.assertGreater(len(self.win._qb_hits["rows"]), 5)
        self.assertGreater(self.win._qb_max_scroll, 0)


# ── capturing a key ──────────────────────────────────────────────────────────

class CaptureTests(Base):
    def setUp(self):
        super().setUp()
        self.open()

    def test_clicking_a_row_selects_it_and_waits_for_a_key(self):
        self.start_capture("hotkey.dock")
        self.assertEqual(self.win._qb_selected, "hotkey.dock")

    def test_the_next_key_is_bound_and_does_not_reach_the_game(self):
        self.start_capture("hotkey.dock")
        self.assertEqual(self.key(pygame.K_b), [])
        self.assertEqual(self.combo("hotkey.dock"), Combo(pygame.K_b))
        self.assertIsNone(self.win._qb_capture)
        self.assertEqual(self.win._qb_status[0], "Dock / undock: B.")
        self.assertEqual(self.win._qb_status[1], "ok")

    def test_modifiers_held_with_the_key_are_part_of_the_binding(self):
        self.start_capture("hotkey.dock")
        self.key(pygame.K_b, mod=CTRL | pygame.KMOD_LSHIFT)
        self.assertEqual(self.combo("hotkey.dock"), Combo(pygame.K_b, {"ctrl", "shift"}))
        self.assertEqual(self.win._qb_status[0], "Dock / undock: Ctrl+Shift+B.")

    def test_a_modifier_key_on_its_own_keeps_waiting(self):
        self.start_capture("hotkey.dock")
        for key in (pygame.K_LCTRL, pygame.K_RSHIFT, pygame.K_LALT):
            self.assertEqual(self.key(key), [])
        self.assertEqual(self.win._qb_capture, "hotkey.dock")
        self.assertEqual(self.combo("hotkey.dock"), Combo(pygame.K_r))

    def test_escape_cancels_and_does_not_open_the_games_menu(self):
        self.start_capture("hotkey.dock")
        self.assertEqual(self.key(pygame.K_ESCAPE), [])
        self.assertIsNone(self.win._qb_capture)
        self.assertEqual(self.combo("hotkey.dock"), Combo(pygame.K_r))
        self.assertEqual(self.win._qb_status[0], "Cancelled.")

    def test_backspace_and_delete_unbind(self):
        self.start_capture("hotkey.dock")
        self.assertEqual(self.key(pygame.K_BACKSPACE), [])
        self.assertIsNone(self.combo("hotkey.dock"))
        self.assertEqual(self.win._qb_status[0], "Dock / undock: unbound.")
        self.start_capture("hotkey.missions")
        self.key(pygame.K_DELETE)
        self.assertIsNone(self.combo("hotkey.missions"))

    def test_a_key_the_game_needs_is_refused_and_it_keeps_waiting(self):
        self.start_capture("hotkey.dock")
        for key in (pygame.K_w, pygame.K_SPACE, pygame.K_TAB, pygame.K_RETURN, pygame.K_LEFT):
            self.assertEqual(self.key(key), [])
            self.assertEqual(self.win._qb_capture, "hotkey.dock")
            self.assertEqual(self.win._qb_status[1], "warn")
            self.assertIn("used by the game", self.win._qb_status[0])
        self.assertEqual(self.combo("hotkey.dock"), Combo(pygame.K_r))

    def test_taking_a_key_another_action_has_unbinds_that_action_and_says_so(self):
        self.start_capture("hotkey.dock")
        self.key(pygame.K_e)
        self.assertEqual(self.combo("hotkey.dock"), Combo(pygame.K_e))
        self.assertIsNone(self.combo("hotkey.cycle_weapon"))
        self.assertEqual(self.win._qb_status[1], "warn")
        self.assertIn("Cycle weapon had it and is now unbound", self.win._qb_status[0])

    def test_the_release_of_the_key_is_swallowed_while_waiting(self):
        self.start_capture("hotkey.dock")
        self.assertEqual(self.key(pygame.K_b, kind=pygame.KEYUP), [])

    def test_keys_go_to_the_game_when_nothing_is_awaited(self):
        event = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_r, mod=0, unicode="r", scancode=0)
        self.assertEqual(qw.filter_events(self.win, [event]), [event])

    def test_a_change_is_saved(self):
        self.start_capture("hotkey.dock")
        self.key(pygame.K_b)
        self.assertEqual(kb.read_file(self.path)["keys"], {"hotkey.dock": {"key": "b", "mods": []}})

    def test_the_list_shows_what_was_bound(self):
        self.start_capture("hotkey.dock")
        self.key(pygame.K_b, mod=ALT)
        self.render()
        self.assertEqual(kb.format_combo(self.combo("hotkey.dock")), "Alt+B")

    def test_clicking_another_row_moves_the_wait_to_it(self):
        self.start_capture("hotkey.dock")
        self.click(self.row("hotkey.missions").center)
        self.assertEqual(self.win._qb_capture, "hotkey.missions")
        self.assertEqual(self.win._qb_selected, "hotkey.missions")

    def test_clicking_elsewhere_in_the_window_gives_up_waiting(self):
        self.start_capture("hotkey.dock")
        self.click(self.rects()["info"].center)
        self.assertIsNone(self.win._qb_capture)
        self.assertEqual(self.combo("hotkey.dock"), Combo(pygame.K_r))

    def test_clicking_the_game_gives_up_waiting_and_the_click_goes_on_to_the_game(self):
        self.start_capture("hotkey.dock")
        outside = (self.win._qb_rect.right + 20, self.win._qb_rect.bottom + 20)
        self.assertEqual(len(self.click(outside)), 2)
        self.assertIsNone(self.win._qb_capture)

    def test_the_flight_keys_are_quiet_while_a_key_is_awaited(self):
        qw.install(Win, CLIENT_GLOBALS)
        gate = CLIENT_GLOBALS["_flight_text_entry_active"]
        self.assertFalse(gate(chat_focused=False))
        self.start_capture("hotkey.dock")
        self.assertTrue(gate(chat_focused=False))
        self.key(pygame.K_b)
        self.assertFalse(gate(chat_focused=False))
        self.assertTrue(gate(chat_focused=True))                    # the game's own answer still counts
        self.assertIs(gate.__wrapped__, _original_gate)
        self.assertFalse(qw.install(Win, CLIENT_GLOBALS))           # idempotent


# ── unbind, reset, reset all ─────────────────────────────────────────────────

class ButtonTests(Base):
    def setUp(self):
        super().setUp()
        self.open()
        self.win._qb_selected = "hotkey.dock"
        self.render()

    def test_unbind_clears_the_selected_action(self):
        self.press("unbind")
        self.assertIsNone(self.combo("hotkey.dock"))
        self.assertEqual(self.win._qb_status[0], "Dock / undock: unbound.")

    def test_reset_puts_the_selected_actions_default_back(self):
        qk.set_binding("hotkey.dock", Combo(pygame.K_b))
        self.press("reset")
        self.assertEqual(self.combo("hotkey.dock"), Combo(pygame.K_r))
        self.assertEqual(self.win._qb_status[0], "Dock / undock: back to R.")

    def test_reset_takes_the_default_key_back_from_whoever_has_it_now(self):
        qk.set_binding("hotkey.dock", Combo(pygame.K_b))
        qk.set_binding("hotkey.missions", Combo(pygame.K_r))
        self.press("reset")
        self.assertEqual(self.combo("hotkey.dock"), Combo(pygame.K_r))
        self.assertIsNone(self.combo("hotkey.missions"))

    def test_reset_all_needs_a_second_click(self):
        qk.set_binding("hotkey.dock", Combo(pygame.K_b))
        qk.set_binding("tab.next", Combo(pygame.K_F5))
        self.press("reset_all")
        self.assertFalse(qk.table().is_default)
        self.assertTrue(self.win._qb_confirm_all)
        self.press("reset_all")
        self.assertTrue(qk.table().is_default)
        self.assertEqual(self.win._qb_status[0], "Every key is back to the default.")
        self.assertEqual(kb.read_file(self.path)["keys"], {})

    def test_an_armed_reset_all_expires(self):
        qk.set_binding("hotkey.dock", Combo(pygame.K_b))
        clock = Clock()
        with mock.patch.object(qw.time, "monotonic", clock.monotonic):
            self.press("reset_all")
            clock.now += qw.CONFIRM_S + 1
            self.render()
            self.assertEqual(self.win._qb_confirm_all, 0.0)
            self.press("reset_all")                                 # arms again instead of resetting
        self.assertFalse(qk.table().is_default)

    def test_the_buttons_that_have_nothing_to_do_do_nothing(self):
        before = dict(qk.table().bindings)
        self.press("reset")                                         # nothing changed on the selected action
        self.press("reset_all")                                     # nothing changed anywhere
        self.assertEqual(qk.table().bindings, before)
        self.assertIsNone(self.win._qb_status)
        self.assertEqual(self.win._qb_confirm_all, 0.0)             # a greyed-out RESET ALL does not even arm

    def test_unbind_on_an_unbound_action_changes_nothing(self):
        qk.set_binding("tab.next", None)
        self.win._qb_selected = "tab.next"
        self.render()
        self.press("unbind")
        self.assertIsNone(self.combo("tab.next"))


# ── the window's mouse ───────────────────────────────────────────────────────

class MouseTests(Base):
    def setUp(self):
        super().setUp()
        self.open()

    def test_clicks_inside_the_window_never_reach_the_game_and_clicks_outside_do(self):
        inside = self.win._qb_rect.center
        outside = (self.win._qb_rect.right + 20, self.win._qb_rect.bottom + 20)
        self.assertEqual(self.click(inside), [])
        self.assertEqual(len(self.click(outside)), 2)

    def test_the_close_button_closes(self):
        self.press("close")
        self.assertFalse(self.win._qb_open)

    def test_dragging_the_header_moves_the_window_and_it_stays_on_screen(self):
        rect = pygame.Rect(self.win._qb_rect)
        start = (rect.x + 60, rect.y + 8)
        end = (start[0] - 5000, start[1] + 10)
        events = [pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=start, button=1),
                  pygame.event.Event(pygame.MOUSEMOTION, pos=end, buttons=(1, 0, 0), rel=(0, 0)),
                  pygame.event.Event(pygame.MOUSEBUTTONUP, pos=end, button=1)]
        with mock.patch("pygame.mouse.get_pos", return_value=start):
            self.assertEqual(qw.filter_events(self.win, events), [])
        self.assertEqual(self.win._qb_rect.x, 0)
        self.assertEqual(self.win._qb_rect.y, rect.y + 10)
        self.assertEqual(self.win._qb_rect.size, rect.size)
        self.assertIsNone(self.win._qb_drag)

    def test_dragging_a_corner_resizes_it_but_not_below_the_minimum(self):
        rect = pygame.Rect(self.win._qb_rect)
        start = (rect.right - 2, rect.bottom - 2)
        end = (start[0] - 900, start[1] - 900)
        events = [pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=start, button=1),
                  pygame.event.Event(pygame.MOUSEMOTION, pos=end, buttons=(1, 0, 0), rel=(0, 0)),
                  pygame.event.Event(pygame.MOUSEBUTTONUP, pos=end, button=1)]
        with mock.patch("pygame.mouse.get_pos", return_value=start):
            qw.filter_events(self.win, events)
        self.assertEqual(self.win._qb_rect.size, (qw._MIN_W, qw._MIN_H))

    def test_the_wheel_scrolls_the_list_and_is_kept_from_the_game_over_the_window(self):
        area = self.rects()["list"]
        with mock.patch("pygame.mouse.get_pos", return_value=area.center):
            kept = qw.filter_events(self.win, [pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-3)])
        self.assertEqual(kept, [])
        self.assertGreater(self.win._qb_scroll, 0)
        with mock.patch("pygame.mouse.get_pos", return_value=(1, 1)):
            outside = qw.filter_events(self.win, [pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=1)])
        self.assertEqual(len(outside), 1)

    def test_scrolling_stops_at_both_ends(self):
        area = self.rects()["list"]
        with mock.patch("pygame.mouse.get_pos", return_value=area.center):
            qw.filter_events(self.win, [pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-500)])
            self.assertEqual(self.win._qb_scroll, self.win._qb_max_scroll)
            qw.filter_events(self.win, [pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=500)])
            self.assertEqual(self.win._qb_scroll, 0)

    def test_every_action_can_be_reached_by_scrolling(self):
        for action in kb.CATALOG:
            self.assertIsNotNone(self.row(action.id))
        self.win._qb_scroll = 0

    def test_the_last_row_is_fully_visible_at_the_bottom_of_the_scroll(self):
        self.win._qb_scroll = 10 ** 6
        self.render()
        rect = self.row(kb.CATALOG[-1].id)
        self.assertTrue(self.rects()["list"].contains(rect))


# ── the chat command ─────────────────────────────────────────────────────────

class CommandTests(Base):
    def say(self, text):
        self.win.chat.clear()
        qw.handle_command(self.win, text)
        return list(self.win.chat)

    def test_the_bare_command_toggles_the_window(self):
        self.say("/keys")
        self.assertTrue(self.win._qb_open)
        self.say("/keybinds")
        self.assertFalse(self.win._qb_open)

    def test_list_says_when_nothing_is_changed(self):
        self.assertIn("Every key is the default", self.say("/keys list")[0])

    def test_list_shows_what_was_changed_with_its_default(self):
        qk.set_binding("hotkey.dock", Combo(pygame.K_b, {"ctrl"}))
        qk.set_binding("tab.next", Combo(pygame.K_F5))
        qk.set_binding("hotkey.missions", None)
        lines = self.say("/keys list")
        self.assertIn("  Dock / undock: Ctrl+B   (default R)", lines)
        self.assertIn("  Next tab: F5   (default -)", lines)
        self.assertIn("  Missions: -   (default J)", lines)
        self.assertEqual(len(lines), 3)                                  # only what was changed

    def test_reset_puts_every_default_back(self):
        qk.set_binding("hotkey.dock", Combo(pygame.K_b))
        self.assertEqual(self.say("/keys reset"), ["Every key is back to the default."])
        self.assertTrue(qk.table().is_default)

    def test_an_unknown_word_shows_the_help(self):
        self.assertIn("/keys list", self.say("/keys frobnicate")[0])

    def test_switched_off_it_says_so(self):
        with mock.patch.dict(os.environ, {qw.ENV_OFF: "1"}):
            self.assertEqual(self.say("/keys list"), ["Quick bars are switched off."])

    def test_an_error_switches_it_off(self):
        with mock.patch.object(qk, "table", side_effect=RuntimeError("boom")), self.assertLogs(qw.logger, "ERROR"):
            qw.handle_command(self.win, "/keys list")
        self.assertFalse(qw._enabled())


# ── the vanilla lines the patch adds ─────────────────────────────────────────

class PatchedGameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = modloader.game_source("Client.py")

    def test_every_hook_is_in_the_patched_client(self):
        for line in ("import quick_bars_window  # MOD", "import quick_keys  # MOD",
                     'quick_bars_window.is_open(self), "", False),',
                     "self._menu_quick_bars_rect = item_rects[\"quick_bars\"]",
                     "_frame_events = quick_bars_window.filter_events(self, _frame_events)",
                     'elif _cmd in ("keys", "keybinds", "bars", "quickbars", "loadout", "loadouts"):',
                     "quick_bars_window.draw_bars(self, _frame)",
                     "quick_bars_window.handle_command(self, _msg)",
                     "quick_bars_window.toggle(self)",
                     "quick_bars_window.queue(self, _frame)",
                     "quick_bars_window.install(SolarSystemWindow, globals())"):
            self.assertEqual(self.client.count(line), 1, line)

    def test_the_menu_row_and_its_rect_use_the_same_key(self):
        self.assertIn('("quick_bars", "QUICK BARS"', self.client)
        self.assertIn('item_rects["quick_bars"]', self.client)

    def test_the_click_hook_comes_before_the_menus_catch_all(self):
        mine = self.client.index("quick_bars_window.toggle(self)")
        catch_all = self.client.index('and not (self._menu_popup_rect and', mine)
        self.assertLess(mine, catch_all)

    def test_the_command_names_are_taken_by_no_other_branch(self):
        import re
        branches = re.findall(r'_cmd (?:==|in) [^\n]*"(?:keys|keybinds)"', self.client)
        self.assertEqual(len(branches), 1, branches)


if __name__ == "__main__":
    unittest.main()

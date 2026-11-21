"""Tests for ui_layouts_window.py - the UI LAYOUTS window: save / load / undo / update / rename / delete, the chat command, the
mouse and typing, drawing, and (against the game's own window class) that loading a layout moves windows and nothing else."""

from __future__ import annotations
import modloader

import copy
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

import ui_layouts as ul
import ui_layouts_window as slw
from gui_window_manager import GuiWindowManager
from test_ui_layouts import FULL
from text_field_host import TextFieldHostMixin

SCREEN = (1280, 720)
BACKDROP = (20, 60, 30, 255)


def sync_submit(fn, *args):
    fn(*args)


class FakeWindow(TextFieldHostMixin):
    """The parts of SolarSystemWindow the window touches. ``layout`` is what the game's ``_panel_layout_dict`` returns, and what
    ``apply_panel_layout`` is given becomes the new one (a game that stores exactly what it is told)."""

    def __init__(self, scale=1.0):
        self.clipboard = ""
        self._uk = scale
        self._lock = threading.RLock()
        self._gui_windows = GuiWindowManager()
        self.chat: list = []
        self.saves: list = []
        self.applied: list = []
        self._fonts: dict = {}
        self.layout = copy.deepcopy(FULL)
        self._stats_pos_x, self._stats_pos_y = 55, 66          # a window that was dragged
        self._ui_editor = None

    def _panel_layout_dict(self):
        return copy.deepcopy(self.layout)

    def apply_panel_layout(self, layout):
        self.applied.append(copy.deepcopy(layout))
        self.layout = copy.deepcopy(layout)

    def _save_options_fn(self, **kwargs):
        self.saves.append(kwargs)

    def _get_clipboard_text(self):
        return self.clipboard

    def _set_clipboard_text(self, text):
        self.clipboard = text

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


GATE_CALLS: list = []


def _original_gate(**kwargs):
    GATE_CALLS.append(kwargs)
    return bool(kwargs.get("chat_focused"))


CLIENT_GLOBALS = {"_flight_text_entry_active": _original_gate}
slw.install(Win, CLIENT_GLOBALS)


def moved(layout: dict) -> dict:
    """*layout* (a game dict) with every place changed."""
    out = copy.deepcopy(layout)
    out["panels"]["inventory"].update(x=300, y=310, max_h=222)
    out["panels"]["target"].update(x=5, y=6)
    out["chat_panel"].update(x=400, y=100, w=300, h=150)
    out["hotbar_pos"] = {"x": 11, "y": 12}
    out["minimap"].update(x=13, y=14, resize=2.0)
    out["coalition"].update(x=15, y=16, width=450, height=520)
    return out


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode(SCREEN)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        slw.reset_for_tests()
        self.path = os.path.join(self._tmp.name, "ui_layouts.json")
        slw.configure_for_tests(self.path, sync_submit)
        self.addCleanup(self._cleanup)
        self.win = self.make_window()

    def _cleanup(self):
        slw.reset_for_tests()
        self._tmp.cleanup()

    def make_window(self, scale=1.0):
        win = Win(scale)
        slw._ensure(win)
        return win

    size = SCREEN                       # the screen the next frame is drawn at, until a test draws at another one

    def render(self, win=None, size=None, mouse=(5, 5)):
        win = win or self.win
        size = self.size = size or self.size
        screen = pygame.Surface(size, pygame.SRCALPHA)
        screen.fill(BACKDROP)
        manager = win._gui_windows
        with mock.patch("pygame.mouse.get_pos", return_value=mouse):
            manager.begin_frame()
            slw.queue(win, SimpleNamespace(screen=screen))
            manager.flush_draw_queue()
            manager.end_frame()
        return screen

    def open(self, win=None):
        win = win or self.win
        slw.open_window(win)
        self.render(win)
        return win

    def rects(self):
        return self.win._ul_hits["rects"]

    def click(self, pos, win=None, button=1):
        win = win or self.win
        events = [pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=button),
                  pygame.event.Event(pygame.MOUSEBUTTONUP, pos=pos, button=button)]
        with mock.patch("pygame.mouse.get_pos", return_value=pos):
            return slw.filter_events(win, events)

    def press(self, name):
        """Click the middle of a button; draw again afterwards, as the game would."""
        remaining = self.click(self.rects()[name].center)
        self.render()
        return remaining

    def type_text(self, text, win=None):
        win = win or self.win
        events = [pygame.event.Event(pygame.KEYDOWN, key=ord(ch.lower()) if ch.isalnum() else pygame.K_SPACE,
                                     mod=0, unicode=ch) for ch in text]
        return slw.filter_events(win, events)

    def key(self, key, win=None, mod=0, unicode=""):
        win = win or self.win
        return slw.filter_events(win, [pygame.event.Event(pygame.KEYDOWN, key=key, mod=mod, unicode=unicode)])

    def name_it(self, name):
        """With the name box open: replace its text and press Enter."""
        self.key(pygame.K_a, mod=pygame.KMOD_CTRL)
        self.type_text(name)
        self.key(pygame.K_RETURN)

    def save_as(self, name, layout=None):
        """Save what the game has now (or *layout*) under *name*, through the window's own buttons."""
        if layout is not None:
            self.win.layout = copy.deepcopy(layout)
        self.press("save")
        self.name_it(name)
        self.render()
        return self.win._ul_store.find(name)

    def names(self):
        return [d["name"] for d in self.win._ul_store.layouts]


class Clock:
    def __init__(self, start=1000.0):
        self.now = start

    def monotonic(self):
        return self.now


# ── geometry ─────────────────────────────────────────────────────────────────

class GeometryTests(unittest.TestCase):
    def rects(self, scale, size=None):
        s = (lambda px: px) if scale == 1.0 else (lambda px: round(px * scale))
        width, height = size or (s(slw._MIN_W), s(slw._MIN_H))
        window = pygame.Rect(40, 30, width, height)
        return window, slw.layout_rects(window, s)

    def test_everything_is_inside_the_window_at_every_size_and_scale(self):
        for scale in (1.0, 1.25, 1.5, 2.0):
            for grow in (0, 150):
                window, _r = self.rects(scale)
                window = window.inflate(grow, grow)
                r = slw.layout_rects(window, (lambda px: round(px * scale)))
                for name, rect in r.items():
                    self.assertTrue(window.contains(rect), (scale, grow, name, rect, window))
                    self.assertGreater(rect.w, 0, (scale, name))
                    self.assertGreater(rect.h, 0, (scale, name))

    def test_the_parts_do_not_overlap(self):
        for scale in (1.0, 1.5, 2.0):
            _window, r = self.rects(scale)
            names = ["save", "list", "info", "load", "undo", "update", "rename", "delete"]
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    self.assertFalse(r[a].colliderect(r[b]), (scale, a, b, r[a], r[b]))
            self.assertFalse(r["body"].colliderect(r["header"]))
            self.assertFalse(r["body"].colliderect(r["status"]))
            self.assertFalse(r["field"].colliderect(r["ok"]))
            self.assertFalse(r["ok"].colliderect(r["cancel"]))
            self.assertTrue(r["save"].contains(r["field"]) and r["save"].contains(r["ok"]) and r["save"].contains(r["cancel"]))

    def test_the_five_buttons_are_one_row_below_the_list(self):
        _window, r = self.rects(1.0)
        row = [r[k] for k in ("load", "undo", "update", "rename", "delete")]
        self.assertEqual({rect.y for rect in row}, {row[0].y})
        for left, right in zip(row, row[1:]):
            self.assertLess(left.right, right.left)
        self.assertGreater(r["list"].top, r["save"].bottom)
        self.assertGreater(row[0].top, r["info"].bottom)
        self.assertGreater(r["info"].top, r["list"].bottom)

    def test_there_is_room_for_a_list_at_the_smallest_size(self):
        _window, r = self.rects(1.0)
        self.assertGreaterEqual(r["list"].h, 80)
        self.assertGreaterEqual(r["list"].w, 380)

    def test_the_window_stays_on_screen_and_above_its_minimum(self):
        win = Win(1.0)
        slw._ensure(win)
        win._ul_rect = pygame.Rect(-50, 9000, 10, 10)
        rect = slw.window_rect(win, 1280, 720)
        self.assertTrue(pygame.Rect(0, 0, 1280, 720).contains(rect))
        self.assertGreaterEqual(rect.w, slw._MIN_W)
        self.assertGreaterEqual(rect.h, slw._MIN_H)
        win._ul_rect = None
        self.assertTrue(pygame.Rect(0, 0, 300, 200).contains(slw.window_rect(win, 300, 200)))      # a tiny screen


# ── opening, the size, drawing ───────────────────────────────────────────────

class OpenTests(Base):
    def test_it_starts_closed_and_toggle_opens_and_closes(self):
        self.assertFalse(slw.is_open(self.win))
        slw.toggle(self.win)
        self.assertTrue(slw.is_open(self.win))
        slw.toggle(self.win)
        self.assertFalse(slw.is_open(self.win))

    def test_closing_drops_a_half_typed_name_and_an_armed_button(self):
        self.open()
        self.press("save")
        self.assertIsNotNone(self.win._ul_input)
        slw.close_window(self.win)
        self.assertIsNone(self.win._ul_input)
        self.assertIsNone(self.win._ul_confirm)

    def test_the_screen_size_is_noted_every_frame_even_when_closed(self):
        self.render(size=(1600, 900))
        self.assertEqual(slw.window_size(self.win), (1600, 900))
        self.render(size=(1024, 768))
        self.assertEqual(slw.window_size(self.win), (1024, 768))

    def test_the_size_falls_back_to_the_display_before_the_first_frame(self):
        self.assertEqual(slw.window_size(self.win), SCREEN)         # pygame.display.set_mode(SCREEN) in setUpClass

    def test_it_draws_at_every_ui_scale_with_and_without_layouts(self):
        for scale in (1.0, 1.5, 2.0):
            win = self.make_window(scale)
            slw.open_window(win)
            self.render(win)
            self.assertTrue(win._ul_hits["rects"])
            slw.save_current(win, f"A{scale}")
            self.render(win)
            self.assertEqual(len(win._ul_hits["rows"]), len(win._ul_store.layouts))
            self.assertGreaterEqual(len(win._ul_hits["rows"]), 1)

    def test_the_env_switch_turns_it_off(self):
        slw.open_window(self.win)
        events = [object()]
        with mock.patch.dict(os.environ, {slw.ENV_OFF: "1"}):
            self.assertFalse(slw.is_open(self.win))
            self.assertIs(slw.filter_events(self.win, events), events)
            self.win._ul_hits = {}
            self.render()                                         # draws nothing
            self.assertEqual(self.win._ul_hits, {})
        self.assertTrue(slw.is_open(self.win))

    def test_an_error_while_drawing_switches_it_off_for_the_session(self):
        self.open()
        with mock.patch.object(slw, "_draw", side_effect=RuntimeError("boom")), self.assertLogs(slw.logger, "ERROR"):
            self.render()
        self.assertFalse(slw.is_open(self.win))

    def test_an_error_in_the_event_filter_switches_it_off_and_returns_the_events(self):
        self.open()
        events = [object()]
        with mock.patch.object(slw, "_handle", side_effect=RuntimeError("boom")), self.assertLogs(slw.logger, "ERROR"):
            self.assertIs(slw.filter_events(self.win, events), events)
        self.assertFalse(slw._enabled())


# ── saving ───────────────────────────────────────────────────────────────────

class SaveTests(Base):
    def test_save_current_stores_the_places_the_size_and_the_mods_windows(self):
        self.render(size=(1600, 900))
        self.win._gm_win_rect = pygame.Rect(30, 40, 500, 400)
        self.win._sl_rect = None                                   # a window that is not placed: left out
        ok, text = slw.save_current(self.win, "Desk")
        self.assertTrue(ok, text)
        layout = self.win._ul_store.layouts[0]
        self.assertEqual(layout["name"], "Desk")
        self.assertEqual(layout["size"], [1600, 900])
        self.assertEqual(layout["geometry"], ul.places(FULL))
        self.assertEqual(layout["windows"], {"galaxy_map": [30, 40, 500, 400]})
        self.assertEqual(self.win._ul_selected, layout["id"])
        self.assertTrue(ul.valid_layout(layout))
        self.assertEqual(ul.read_file(self.path), [layout])

    def test_nothing_but_places_is_saved(self):
        slw.save_current(self.win, "Desk")
        text = repr(self.win._ul_store.layouts[0])
        for left_out in ("booster", "Sol", "colours", "minimized", "visible"):
            self.assertNotIn(left_out, text)

    def test_the_save_button_opens_a_name_box_with_the_window_size_as_the_name(self):
        self.open()
        self.press("save")
        self.assertEqual(self.win._ul_input["text"], "1280 x 720")
        self.assertEqual(self.win._ul_input["mode"], "save")

    def test_typing_a_name_and_pressing_enter_saves_it(self):
        self.open()
        self.press("save")
        self.name_it("Big desk")
        self.assertIsNone(self.win._ul_input)
        self.assertEqual(self.names(), ["Big desk"])
        self.assertEqual(self.win._ul_selected, self.win._ul_store.layouts[0]["id"])
        self.assertIn("Saved 'Big desk'", self.win._ul_status[0])

    def test_the_ok_button_saves_and_esc_cancels(self):
        self.open()
        self.press("save")
        self.press("ok")
        self.assertEqual(self.names(), ["1280 x 720"])
        self.press("save")
        self.assertIsNotNone(self.win._ul_input)
        self.press("cancel")
        self.assertIsNone(self.win._ul_input)
        self.assertEqual(len(self.names()), 1)
        self.press("save")
        self.key(pygame.K_ESCAPE)
        self.assertIsNone(self.win._ul_input)

    def test_an_empty_name_is_refused_and_the_box_stays_open(self):
        self.open()
        self.press("save")
        self.key(pygame.K_a, mod=pygame.KMOD_CTRL)
        self.key(pygame.K_BACKSPACE)
        self.key(pygame.K_RETURN)
        self.assertIsNotNone(self.win._ul_input)
        self.assertEqual(self.names(), [])
        self.assertEqual(self.win._ul_status[1], "warn")

    def test_a_name_that_is_taken_gets_a_number(self):
        self.open()
        self.save_as("Desk")
        self.save_as("desk")
        self.assertEqual(self.names(), ["Desk", "desk (2)"])

    def test_the_name_is_capped(self):
        self.open()
        self.press("save")
        self.name_it("x" * 80)
        self.assertEqual(len(self.win._ul_store.layouts[0]["name"]), ul.MAX_NAME_LEN)

    def test_the_flight_keys_are_quiet_while_a_name_is_typed(self):
        self.assertFalse(CLIENT_GLOBALS["_flight_text_entry_active"](chat_focused=False))
        self.open()
        self.press("save")
        self.assertTrue(CLIENT_GLOBALS["_flight_text_entry_active"](chat_focused=False))
        self.key(pygame.K_ESCAPE)
        self.assertFalse(CLIENT_GLOBALS["_flight_text_entry_active"](chat_focused=False))

    def test_the_gate_still_passes_the_games_own_answer_through(self):
        self.assertTrue(CLIENT_GLOBALS["_flight_text_entry_active"](chat_focused=True))
        self.assertIs(CLIENT_GLOBALS["_flight_text_entry_active"].__wrapped__, _original_gate)

    def test_install_is_idempotent(self):
        self.assertFalse(slw.install(Win, CLIENT_GLOBALS))

    def test_a_save_before_the_list_is_loaded_waits(self):
        queued: list = []
        slw.configure_for_tests(self.path, lambda fn, *args: queued.append((fn, args)))
        win = self.make_window()
        ok, text = slw.save_current(win, "Desk")
        self.assertFalse(ok)
        self.assertIn("Still loading", text)

    def test_the_window_opens_on_the_layout_made_for_this_size(self):
        for name, size in (("Alpha", (1920, 1080)), ("Zed", (1280, 720)), ("Beta", (2560, 1440))):
            with mock.patch.object(slw, "window_size", return_value=size):
                slw.save_current(self.win, name)
        self.win._ul_selected = None                      # (a save selects what it saved)
        slw.open_window(self.win)
        self.assertEqual(self.names(), ["Alpha", "Beta", "Zed"])
        self.assertEqual(self.win._ul_store.get(self.win._ul_selected)["name"], "Zed")      # not the first in the list

    def test_without_a_layout_for_this_size_the_first_is_selected(self):
        with mock.patch.object(slw, "window_size", return_value=(800, 600)):
            slw.save_current(self.win, "Zed")
            slw.save_current(self.win, "Alpha")
        self.win._ul_selected = None
        slw.open_window(self.win)
        self.assertEqual(self.win._ul_store.get(self.win._ul_selected)["name"], "Alpha")


# ── loading, undo ────────────────────────────────────────────────────────────

class LoadTests(Base):
    def saved_and_moved(self):
        """Save FULL's places, then change every place on the game side. Returns the layout."""
        self.open()
        layout = self.save_as("Desk")
        self.win.layout = moved(FULL)
        self.win.applied.clear()
        self.win.saves.clear()
        return layout

    def places_now(self):
        return ul.places(self.win.layout)

    def test_load_puts_every_place_back(self):
        layout = self.saved_and_moved()
        self.press("load")
        self.assertEqual(self.places_now(), layout["geometry"])
        self.assertEqual(self.win._ul_status[1], "ok")
        self.assertEqual(self.win._ul_status[0], "Loaded 'Desk'.")

    def test_load_changes_nothing_but_places(self):
        self.saved_and_moved()
        before = copy.deepcopy(self.win.layout)
        before["hotbar"] = [{"item_type": "other"}]
        before["galaxy_favorites"] = ["Vega"]
        before["chat_panel"]["colours"] = {"squad": [9, 9, 9]}
        before["panels"]["inventory"]["minimized"] = True
        before["minimap"]["zoom"] = 9
        self.win.layout = copy.deepcopy(before)
        self.press("load")
        after = self.win.layout
        for key in ("hotbar", "galaxy_favorites", "hangar_order"):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(after["chat_panel"]["colours"], {"squad": [9, 9, 9]})
        self.assertTrue(after["panels"]["inventory"]["minimized"])
        self.assertEqual(after["minimap"]["zoom"], 9)

    def test_load_hands_the_game_one_dict_and_saves_options_once(self):
        self.saved_and_moved()
        self.press("load")
        self.assertEqual(len(self.win.applied), 1)
        self.assertEqual(len(self.win.saves), 1)
        self.assertEqual(self.win.saves[0], {"panels": self.win.layout})

    def test_double_clicking_a_row_loads_it_and_one_click_only_selects(self):
        layout = self.saved_and_moved()
        row = self.win._ul_hits["rows"][0][0]
        self.click(row.center)
        self.assertEqual(self.win.applied, [])
        self.click(row.center)
        self.assertEqual(self.places_now(), layout["geometry"])

    def test_two_clicks_too_far_apart_are_not_a_double_click(self):
        self.saved_and_moved()
        row = self.win._ul_hits["rows"][0][0]
        clock = Clock()
        with mock.patch.object(slw.time, "monotonic", clock.monotonic):
            self.click(row.center)
            clock.now += slw.DOUBLE_CLICK_S + 0.2
            self.click(row.center)
        self.assertEqual(self.win.applied, [])

    def test_undo_takes_the_last_load_back(self):
        self.saved_and_moved()
        moved_places = self.places_now()
        self.press("load")
        self.assertIsNotNone(self.win._ul_undo)
        self.press("undo")
        self.assertEqual(self.places_now(), moved_places)
        self.assertIsNone(self.win._ul_undo)
        self.assertIn("back where they were", self.win._ul_status[0])

    def test_undo_does_nothing_before_a_load(self):
        self.open()
        self.save_as("Desk")
        self.win.applied.clear()
        self.press("undo")
        self.assertEqual(self.win.applied, [])

    def test_a_layout_made_for_another_size_is_fitted_and_the_player_is_told(self):
        self.render(size=(2560, 1440))
        slw.save_current(self.win, "Big")
        self.render(size=(1920, 1080))
        self.win.layout = moved(FULL)
        slw.open_window(self.win)
        self.render(size=(1920, 1080))
        self.press("load")
        fitted = ul.fit_geometry(ul.places(FULL), (2560, 1440), (1920, 1080))
        self.assertEqual(self.places_now(), fitted)
        self.assertEqual(self.win._ul_status[0], "Loaded 'Big', fitted from 2560 x 1440 to 1920 x 1080.")
        self.assertNotEqual(fitted, ul.places(FULL))

    def test_a_layout_made_for_this_size_is_not_fitted(self):
        self.saved_and_moved()
        self.press("load")
        self.assertNotIn("fitted", self.win._ul_status[0])
        self.assertEqual(self.places_now(), ul.places(FULL))

    def test_a_window_saved_at_its_default_place_goes_back_to_it(self):
        """FULL has Stats at its default place (null); the game's loader ignores a null, so the window resets it."""
        self.saved_and_moved()
        self.assertEqual((self.win._stats_pos_x, self.win._stats_pos_y), (55, 66))
        self.press("load")
        self.assertEqual((self.win._stats_pos_x, self.win._stats_pos_y), (None, None))

    def test_a_window_saved_at_its_default_size_goes_back_to_it(self):
        """FULL saves Mission Detail without a size; the game's loader ignores a null size, so the window resets it."""
        self.saved_and_moved()
        self.win._mission_detail_w, self.win._mission_detail_h = 640, 480
        self.press("load")
        self.assertEqual((self.win._mission_detail_w, self.win._mission_detail_h), (None, None))

    def test_an_attribute_the_window_does_not_have_is_not_invented(self):
        self.saved_and_moved()
        self.press("load")
        for attr in ("_rc_haul_panel_pos", "_rc_haul_pos_custom", "_mission_detail_pos_x", "_squad_pos_x"):
            self.assertFalse(hasattr(self.win, attr), attr)       # FULL saves these at their default place

    def test_the_mods_windows_are_put_back_and_their_own_save_runs(self):
        saver = mock.Mock()
        fake = ModuleType("galaxy_map_window")
        fake._save = saver
        self.win._gm_win_rect = pygame.Rect(30, 40, 500, 400)
        self.open()
        self.save_as("Desk")
        self.win._gm_win_rect = pygame.Rect(900, 10, 200, 200)
        with mock.patch.dict(sys.modules, {"galaxy_map_window": fake}):
            self.press("load")
        self.assertEqual(self.win._gm_win_rect, pygame.Rect(30, 40, 500, 400))
        saver.assert_called_once_with(self.win)

    def test_a_mods_window_that_was_never_opened_is_left_alone(self):
        self.win._gm_win_rect = pygame.Rect(30, 40, 500, 400)
        self.open()
        self.save_as("Desk")
        del self.win._gm_win_rect                       # a window this session never opened has no attribute
        self.press("load")
        self.assertFalse(hasattr(self.win, "_gm_win_rect"))

    def test_a_failing_game_loader_leaves_the_windows_as_they_were(self):
        self.saved_and_moved()
        before = copy.deepcopy(self.win.layout)
        real = self.win.apply_panel_layout
        calls = []

        def flaky(layout):
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("bad value")
            real(layout)

        self.win.apply_panel_layout = flaky
        with self.assertLogs(slw.logger, "ERROR"):
            self.press("load")
        self.assertEqual(len(calls), 2)                 # the load, then the put-back
        self.assertEqual(self.win.layout, before)
        self.assertEqual(self.win._ul_status[1], "warn")
        self.assertIn("left as they were", self.win._ul_status[0])
        self.assertIsNone(self.win._ul_undo)
        self.assertEqual(self.win.saves, [])

    def test_the_games_ui_editor_blocks_a_load(self):
        self.saved_and_moved()
        self.win._ui_editor = object()
        self.press("load")
        self.assertEqual(self.win.applied, [])
        self.assertEqual(self.win._ul_status[1], "warn")

    def test_load_is_unavailable_without_a_selection(self):
        self.open()
        self.press("load")
        self.assertEqual(self.win.applied, [])

    def test_an_unreadable_options_file_does_not_undo_the_load(self):
        self.saved_and_moved()
        self.win._save_options_fn = mock.Mock(side_effect=OSError("disk"))
        self.press("load")
        self.assertEqual(self.places_now(), ul.places(FULL))
        self.assertEqual(self.win._ul_status[1], "ok")


# ── the places other mods keep (PROVIDERS) ───────────────────────────────────

class ProviderTests(Base):
    """A mod whose windows are not rectangles on the game window (the Quick Bars mod's extra bars) offers its places to a layout
    through a module-level pair of functions, found by module name, so this mod never depends on it."""

    DATA = {"bars": [{"id": "b1", "mods": ["shift"], "fx": 0.25, "fy": 0.5}]}

    def places_now(self):
        return ul.places(self.win.layout)

    def provider(self, capture=None, apply=None):
        fake = ModuleType("extra_bars")
        fake.layout_capture = capture or mock.Mock(return_value=copy.deepcopy(self.DATA))
        fake.layout_apply = apply or mock.Mock()
        return fake

    def saved_and_changed(self, fake):
        with mock.patch.dict(sys.modules, {"extra_bars": fake}):
            self.open()
            layout = self.save_as("Desk")
            self.win.layout = moved(FULL)
            self.win.applied.clear()
        return layout

    def test_a_layout_keeps_what_the_provider_says(self):
        layout = self.saved_and_changed(self.provider())
        self.assertEqual(layout["extras"], {"quick_bars": self.DATA})
        self.assertTrue(ul.valid_layout(layout))
        self.assertEqual(ul.read_file(self.path)[0]["extras"], {"quick_bars": self.DATA})

    def test_a_load_hands_the_data_back_to_the_provider(self):
        fake = self.provider()
        self.saved_and_changed(fake)
        fake.layout_apply.reset_mock()
        with mock.patch.dict(sys.modules, {"extra_bars": fake}):
            self.press("load")
        fake.layout_apply.assert_called_once_with(self.win, self.DATA)
        self.assertEqual(self.win._ul_status[1], "ok")

    def test_the_data_is_not_fitted_to_another_window_size(self):
        """The provider's places are relative (fractions of the screen): whatever size the layout was made for, it gets them as saved."""
        fake = self.provider()
        with mock.patch.dict(sys.modules, {"extra_bars": fake}):
            self.render(size=(2560, 1440))
            slw.save_current(self.win, "Big")
            self.render(size=(1920, 1080))
            self.win.layout = moved(FULL)
            slw.open_window(self.win)
            self.render(size=(1920, 1080))
            fake.layout_apply.reset_mock()
            self.press("load")
        fake.layout_apply.assert_called_once_with(self.win, self.DATA)
        self.assertIn("fitted", self.win._ul_status[0])

    def test_without_the_provider_a_layout_has_no_extras_and_loads_all_the_same(self):
        self.open()
        layout = self.save_as("Desk")
        self.assertEqual(layout["extras"], {})
        self.win.layout = moved(FULL)
        self.press("load")
        self.assertEqual(self.places_now(), ul.places(FULL))

    def test_a_layout_saved_with_a_provider_loads_when_that_mod_is_gone(self):
        fake = self.provider()
        self.saved_and_changed(fake)
        self.press("load")                                                      # the module is not in sys.modules any more
        self.assertEqual(self.places_now(), ul.places(FULL))
        self.assertEqual(self.win._ul_status[1], "ok")

    def test_a_provider_with_nothing_to_say_adds_nothing(self):
        for nothing in (None, {}, "text", 5, []):
            with self.subTest(nothing=nothing):
                fake = self.provider(capture=mock.Mock(return_value=nothing))
                with mock.patch.dict(sys.modules, {"extra_bars": fake}):
                    self.assertEqual(slw.capture_extras(self.win), {})

    def test_a_provider_that_raises_is_skipped_and_the_layout_still_saves(self):
        fake = self.provider(capture=mock.Mock(side_effect=RuntimeError("boom")))
        with mock.patch.dict(sys.modules, {"extra_bars": fake}):
            self.open()
            layout = self.save_as("Desk")
        self.assertEqual(layout["extras"], {})
        self.assertIsNotNone(layout)

    def test_a_provider_that_raises_on_apply_does_not_stop_the_load(self):
        fake = self.provider(apply=mock.Mock(side_effect=RuntimeError("boom")))
        self.saved_and_changed(fake)
        with mock.patch.dict(sys.modules, {"extra_bars": fake}):
            self.press("load")
        self.assertEqual(self.places_now(), ul.places(FULL))
        self.assertEqual(self.win._ul_status[1], "ok")

    def test_undo_gives_the_provider_back_what_it_had_before_the_load(self):
        current = {"bars": [{"id": "b1", "mods": ["shift"], "fx": 0.9, "fy": 0.1}]}
        fake = self.provider()
        self.saved_and_changed(fake)
        fake.layout_capture.return_value = copy.deepcopy(current)               # the bars were moved since the save
        with mock.patch.dict(sys.modules, {"extra_bars": fake}):
            self.press("load")
            fake.layout_apply.reset_mock()
            self.press("undo")
        fake.layout_apply.assert_called_once_with(self.win, current)

    def test_a_failed_load_puts_the_providers_places_back_too(self):
        fake = self.provider()
        self.saved_and_changed(fake)
        real = self.win.apply_panel_layout
        calls = []

        def flaky(layout):
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("bad value")
            real(layout)

        self.win.apply_panel_layout = flaky
        fake.layout_capture.return_value = {"bars": []}
        fake.layout_apply.reset_mock()
        with mock.patch.dict(sys.modules, {"extra_bars": fake}), self.assertLogs(slw.logger, "ERROR"):
            self.press("load")
        # the load failed in the game's loader before it reached the provider; the put-back hands it what it had before
        self.assertEqual(fake.layout_apply.call_args_list, [mock.call(self.win, {"bars": []})])
        self.assertEqual(self.win._ul_status[1], "warn")

    def test_update_takes_the_providers_places_as_they_are_now(self):
        fake = self.provider()
        layout = self.saved_and_changed(fake)
        newer = {"bars": [{"id": "b1", "mods": ["shift"], "fx": 0.8, "fy": 0.2}]}
        fake.layout_capture.return_value = newer
        with mock.patch.dict(sys.modules, {"extra_bars": fake}):
            self.press("update")
            self.press("update")
        self.assertEqual(self.win._ul_store.get(layout["id"])["extras"], {"quick_bars": newer})

    def test_a_layout_saved_before_extras_existed_loads_and_calls_no_provider(self):
        fake = self.provider()
        self.open()
        layout = self.save_as("Desk")
        old = dict(layout)
        del old["extras"]
        self.assertTrue(ul.valid_layout(old))
        self.win._ul_store.save(old)
        self.win.layout = moved(FULL)
        with mock.patch.dict(sys.modules, {"extra_bars": fake}):
            self.press("load")
        fake.layout_apply.assert_not_called()
        self.assertEqual(self.places_now(), ul.places(FULL))

    def test_the_provider_table_names_the_quick_bars_mod(self):
        self.assertEqual(slw.PROVIDERS, (("quick_bars", "extra_bars", "layout_capture", "layout_apply"),))


# ── update, rename, delete ───────────────────────────────────────────────────

class EditTests(Base):
    def setUp(self):
        super().setUp()
        self.open()
        self.layout = self.save_as("Desk")

    def test_update_needs_a_second_click_and_keeps_name_and_id(self):
        self.win.layout = moved(FULL)
        self.press("update")
        self.assertEqual(self.win._ul_store.get(self.layout["id"])["geometry"], ul.places(FULL))   # armed only
        self.assertEqual(self.win._ul_confirm[0], "update")
        self.press("update")
        updated = self.win._ul_store.get(self.layout["id"])
        self.assertEqual(updated["geometry"], ul.places(moved(FULL)))
        self.assertEqual(updated["name"], "Desk")
        self.assertEqual(len(self.win._ul_store.layouts), 1)
        self.assertIn("Updated 'Desk'", self.win._ul_status[0])

    def test_update_takes_the_size_the_window_has_now(self):
        self.render(size=(1600, 900))
        self.press("update")
        self.press("update")
        self.assertEqual(self.win._ul_store.get(self.layout["id"])["size"], [1600, 900])

    def test_an_armed_button_expires(self):
        clock = Clock()
        with mock.patch.object(slw.time, "monotonic", clock.monotonic):
            self.win._ul_last_refresh = clock.now
            self.press("update")
            clock.now += slw.CONFIRM_S + 1
            self.render()
            self.assertIsNone(self.win._ul_confirm)
            self.win.layout = moved(FULL)
            self.press("update")                         # arms again instead of updating
        self.assertEqual(self.win._ul_store.get(self.layout["id"])["geometry"], ul.places(FULL))

    def test_arming_one_button_does_not_arm_the_other(self):
        self.win.layout = moved(FULL)
        self.press("update")
        self.press("delete")
        self.assertEqual(len(self.win._ul_store.layouts), 1)
        self.assertEqual(self.win._ul_confirm[0], "delete")
        self.assertEqual(self.win._ul_store.get(self.layout["id"])["geometry"], ul.places(FULL))

    def test_delete_needs_a_second_click(self):
        self.press("delete")
        self.assertEqual(self.names(), ["Desk"])
        self.press("delete")
        self.assertEqual(self.names(), [])
        self.assertIsNone(self.win._ul_selected)
        self.assertEqual(ul.read_file(self.path), [])
        self.assertIn("Deleted 'Desk'", self.win._ul_status[0])

    def test_after_a_delete_the_next_layout_is_selected(self):
        other = self.save_as("Other")
        self.win._ul_selected = self.layout["id"]
        self.press("delete")
        self.press("delete")
        self.assertEqual(self.win._ul_selected, other["id"])

    def test_selecting_another_layout_disarms(self):
        self.save_as("Other")
        self.render()
        self.press("delete")
        rows = {layout_id: rect for rect, layout_id in self.win._ul_hits["rows"]}
        self.click(rows[self.layout["id"]].center)
        self.assertIsNone(self.win._ul_confirm)
        self.assertEqual(len(self.win._ul_store.layouts), 2)

    def test_rename(self):
        self.press("rename")
        self.assertEqual(self.win._ul_input["text"], "Desk")
        self.name_it("Laptop")
        self.assertEqual(self.names(), ["Laptop"])
        self.assertEqual(self.win._ul_store.layouts[0]["id"], self.layout["id"])
        self.assertEqual(self.win._ul_store.layouts[0]["geometry"], self.layout["geometry"])

    def test_renaming_to_a_name_another_layout_has_adds_a_number(self):
        self.save_as("Other")
        self.win._ul_selected = self.layout["id"]
        self.press("rename")
        self.name_it("other")
        self.assertEqual(sorted(self.names()), ["Other", "other (2)"])

    def test_the_buttons_do_nothing_without_a_layout_selected(self):
        self.win._ul_store.delete(self.layout["id"])
        self.win._ul_selected = None
        self.render()
        for name in ("update", "rename", "delete"):
            self.press(name)
        self.assertIsNone(self.win._ul_input)
        self.assertIsNone(self.win._ul_confirm)


# ── the window's mouse and keys ──────────────────────────────────────────────

class InputTests(Base):
    def setUp(self):
        super().setUp()
        self.open()

    def test_clicks_inside_the_window_never_reach_the_game_and_clicks_outside_do(self):
        inside = self.win._ul_rect.center
        outside = (self.win._ul_rect.right + 20, self.win._ul_rect.bottom + 20)
        self.assertEqual(self.click(inside), [])
        self.assertEqual(len(self.click(outside)), 2)

    def test_the_close_button_closes(self):
        self.press("close")
        self.assertFalse(self.win._ul_open)

    def test_dragging_the_header_moves_the_window_and_it_stays_on_screen(self):
        rect = pygame.Rect(self.win._ul_rect)
        start = (rect.x + 60, rect.y + 8)
        events = [pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=start, button=1),
                  pygame.event.Event(pygame.MOUSEMOTION, pos=(start[0] - 5000, start[1] + 40), buttons=(1, 0, 0), rel=(0, 0)),
                  pygame.event.Event(pygame.MOUSEBUTTONUP, pos=(start[0] - 5000, start[1] + 40), button=1)]
        with mock.patch("pygame.mouse.get_pos", return_value=start):
            self.assertEqual(slw.filter_events(self.win, events), [])
        self.assertEqual(self.win._ul_rect.x, 0)
        self.assertEqual(self.win._ul_rect.y, rect.y + 40)
        self.assertEqual(self.win._ul_rect.size, rect.size)
        self.assertIsNone(self.win._ul_drag)

    def test_dragging_a_corner_resizes_it_but_not_below_the_minimum(self):
        rect = pygame.Rect(self.win._ul_rect)
        start = (rect.right - 2, rect.bottom - 2)
        events = [pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=start, button=1),
                  pygame.event.Event(pygame.MOUSEMOTION, pos=(start[0] - 900, start[1] - 900), buttons=(1, 0, 0), rel=(0, 0)),
                  pygame.event.Event(pygame.MOUSEBUTTONUP, pos=(start[0] - 900, start[1] - 900), button=1)]
        with mock.patch("pygame.mouse.get_pos", return_value=start):
            slw.filter_events(self.win, events)
        self.assertEqual(self.win._ul_rect.size, (slw._MIN_W, slw._MIN_H))
        self.assertEqual(self.win._ul_rect.topleft, rect.topleft)

    def test_the_wheel_scrolls_a_long_list_and_is_kept_from_the_game_over_the_window(self):
        for n in range(12):
            slw.save_current(self.win, f"Layout {n:02d}")
        self.render()
        area = self.rects()["list"]
        self.assertGreater(self.win._ul_max_scroll, 0)
        with mock.patch("pygame.mouse.get_pos", return_value=area.center):
            kept = slw.filter_events(self.win, [pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-3)])
        self.assertEqual(kept, [])
        self.assertGreater(self.win._ul_scroll, 0)
        with mock.patch("pygame.mouse.get_pos", return_value=(1, 1)):
            outside = slw.filter_events(self.win, [pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=1)])
        self.assertEqual(len(outside), 1)

    def test_scrolling_up_stops_at_the_top(self):
        for n in range(12):
            slw.save_current(self.win, f"Layout {n:02d}")
        self.render()
        with mock.patch("pygame.mouse.get_pos", return_value=self.rects()["list"].center):
            slw.filter_events(self.win, [pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=50)])
        self.assertEqual(self.win._ul_scroll, 0)

    def test_keys_go_to_the_game_unless_a_name_is_being_typed(self):
        events = [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, mod=0, unicode="")]
        self.assertEqual(slw.filter_events(self.win, events), events)
        self.press("save")
        self.assertEqual(slw.filter_events(self.win, events), [])

    def test_typing_edits_the_name_box(self):
        self.press("save")
        self.key(pygame.K_a, mod=pygame.KMOD_CTRL)
        self.type_text("Abc")
        self.assertEqual(self.win._ul_input["text"], "Abc")
        self.key(pygame.K_BACKSPACE)
        self.assertEqual(self.win._ul_input["text"], "Ab")

    def test_a_released_key_is_swallowed_while_typing(self):
        self.press("save")
        up = pygame.event.Event(pygame.KEYUP, key=pygame.K_a, mod=0)
        self.assertEqual(slw.filter_events(self.win, [up]), [])


# ── the chat command ─────────────────────────────────────────────────────────

class CommandTests(Base):
    def say(self, text):
        self.win.chat.clear()
        slw.handle_command(self.win, text)
        return list(self.win.chat)

    def test_the_bare_command_toggles_the_window(self):
        self.say("/uilayout")
        self.assertTrue(self.win._ul_open)
        self.say("/uilayouts")
        self.assertFalse(self.win._ul_open)

    def test_list_when_there_are_none(self):
        self.assertIn("No UI layouts saved yet", self.say("/uilayout list")[0])

    def test_save_then_list_shows_the_size_and_marks_this_size(self):
        self.render(size=(1600, 900))
        self.assertEqual(self.say("/uilayout save Desk"), ["Saved 'Desk' (1600 x 900)."])
        with mock.patch.object(slw, "window_size", return_value=(800, 600)):
            slw.save_current(self.win, "Small")
        lines = self.say("/uilayout list")
        self.assertEqual(lines, ["  Desk   1600 x 900   <- this window size", "  Small   800 x 600"])

    def test_save_with_a_name_that_exists_updates_it(self):
        self.say("/uilayout save Desk")
        first = self.win._ul_store.layouts[0]
        self.win.layout = moved(FULL)
        self.assertEqual(self.say("/uilayout save desk")[0][:7], "Updated")
        self.assertEqual(len(self.win._ul_store.layouts), 1)
        self.assertEqual(self.win._ul_store.layouts[0]["id"], first["id"])
        self.assertEqual(self.win._ul_store.layouts[0]["geometry"], ul.places(moved(FULL)))

    def test_save_with_a_name_that_only_starts_like_another_makes_a_new_one(self):
        self.say("/uilayout save Desk")
        self.say("/uilayout save Des")
        self.assertEqual(self.names(), ["Des", "Desk"])

    def test_save_without_a_name_uses_the_window_size_and_again_updates_it(self):
        self.render(size=(1600, 900))
        self.assertEqual(self.say("/uilayout save"), ["Saved '1600 x 900' (1600 x 900)."])
        self.assertEqual(self.say("/uilayout save")[0][:7], "Updated")
        self.assertEqual(len(self.win._ul_store.layouts), 1)

    def test_names_with_spaces(self):
        self.say("/uilayout save Big  wide   screen")
        self.assertEqual(self.names(), ["Big wide screen"])
        self.win.layout = moved(FULL)
        self.assertEqual(self.say("/uilayout load big wide SCREEN"), ["Loaded 'Big wide screen'."])

    def test_load_by_name_or_unique_prefix(self):
        self.say("/uilayout save Desktop")
        self.say("/uilayout save Laptop")
        self.win.layout = moved(FULL)
        self.assertEqual(self.say("/uilayout load lap"), ["Loaded 'Laptop'."])
        self.assertEqual(ul.places(self.win.layout), ul.places(FULL))
        self.assertEqual(self.win._ul_selected, self.win._ul_store.find("laptop")["id"])

    def test_load_is_not_guessed_when_two_names_match_or_none_does(self):
        self.say("/uilayout save Laptop")
        self.say("/uilayout save Lapel")
        self.assertIn("No UI layout called 'lap'", self.say("/uilayout load lap")[0])
        self.assertIn("No UI layout called 'tv'", self.say("/uilayout load tv")[0])
        self.assertEqual(self.say("/uilayout load"), ["Usage: /uilayout load <name>"])
        self.assertEqual(self.win.applied, [])

    def test_use_is_a_synonym_for_load(self):
        self.say("/uilayout save Desk")
        self.assertEqual(self.say("/uilayout use desk"), ["Loaded 'Desk'."])

    def test_an_unknown_word_shows_the_help(self):
        self.assertIn("/uilayout list", self.say("/uilayout frobnicate")[0])

    def test_it_says_so_when_the_list_is_not_loaded_yet(self):
        queued: list = []
        slw.configure_for_tests(self.path, lambda fn, *args: queued.append((fn, args)))
        win = self.make_window()
        slw.handle_command(win, "/uilayout list")
        slw.handle_command(win, "/uilayout save Desk")
        self.assertEqual(win.chat, ["Still loading the saved layouts; try again in a moment."] * 2)

    def test_switched_off_it_says_so(self):
        with mock.patch.dict(os.environ, {slw.ENV_OFF: "1"}):
            self.assertEqual(self.say("/uilayout list"), ["UI layouts are switched off."])

    def test_an_error_switches_it_off(self):
        self.say("/uilayout save Desk")
        with mock.patch.object(slw, "apply_layout", side_effect=RuntimeError("boom")), self.assertLogs(slw.logger, "ERROR"):
            slw.handle_command(self.win, "/uilayout load Desk")
        self.assertFalse(slw._enabled())


# ── the vanilla lines the patch adds ─────────────────────────────────────────

class PatchedGameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = modloader.game_source("Client.py")

    def test_every_hook_is_in_the_patched_client(self):
        for line in ("import ui_layouts_window  # MOD",
                     'ui_layouts_window.is_open(self), "", False),',
                     "self._menu_ui_layouts_rect = item_rects[\"ui_layouts\"]",
                     "_frame_events = ui_layouts_window.filter_events(self, _frame_events)",
                     'elif _cmd in ("uilayout", "uilayouts"):',
                     "ui_layouts_window.handle_command(self, _msg)",
                     "ui_layouts_window.toggle(self)",
                     "ui_layouts_window.queue(self, _frame)",
                     "ui_layouts_window.install(SolarSystemWindow, globals())"):
            self.assertEqual(self.client.count(line), 1, line)

    def test_the_menu_row_and_its_rect_use_the_same_key(self):
        self.assertIn('("ui_layouts", "UI LAYOUTS"', self.client)
        self.assertIn('item_rects["ui_layouts"]', self.client)

    def test_the_click_hook_comes_before_the_menus_catch_all(self):
        """The menu's own handler closes the menu on any click that is not on a row it knows; mine must see the click first."""
        mine = self.client.index("ui_layouts_window.toggle(self)")
        catch_all = self.client.index('and not (self._menu_popup_rect and', mine)
        self.assertLess(mine, catch_all)

    def test_the_command_name_is_the_games_only_once(self):
        self.assertEqual(self.client.count('"uilayout"'), 1)


# ── against the game's own window class ──────────────────────────────────────

import test_reset_interface_layout as dev          # the dev's fixtures: a real SolarSystemWindow with what the layout reads


def default_window():
    window = dev._window([])
    window.apply_panel_layout({})
    window._place_default_panels(1920, 1080)
    return window


def prepared(window, size=(1920, 1080)):
    slw._ensure(window)
    window._ul_size = size
    return window


class RealGameTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        slw.reset_for_tests()
        slw.configure_for_tests(os.path.join(self._tmp.name, "ui_layouts.json"), sync_submit)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        slw.reset_for_tests()
        self._tmp.cleanup()

    def source_window(self):
        window = dev._moved_window([])
        window._hotbar_pos_x, window._hotbar_pos_y = 50, 60
        return prepared(window)

    def test_a_layout_carries_every_place_to_a_window_that_has_other_places(self):
        source = self.source_window()
        geometry, windows = slw.capture(source)
        target = prepared(default_window())
        self.assertNotEqual(ul.places(target._panel_layout_dict()), ul.places(source._panel_layout_dict()))
        slw._put(target, geometry, windows)
        self.assertEqual(ul.places(target._panel_layout_dict()), ul.places(source._panel_layout_dict()))

    def test_the_games_attributes_hold_the_places_afterwards(self):
        source = self.source_window()
        geometry, windows = slw.capture(source)
        target = prepared(default_window())
        slw._put(target, geometry, windows)
        self.assertEqual((target._panels["inventory"]["x"], target._panels["inventory"]["y"],
                          target._panels["inventory"]["max_h"]), (400, 300, 260))
        self.assertEqual(tuple(target._chat_panel[k] for k in ("x", "y", "w", "h")), (700, 200, 800, 400))
        self.assertEqual((target._hotbar_pos_x, target._hotbar_pos_y), (50, 60))
        self.assertEqual((target._mm_pos_x, target._mm_pos_y), (15, 16))
        self.assertAlmostEqual(target._mm_resize, 1.8)
        self.assertEqual((target._coalition_pos_x, target._coalition_pos_y, target._coalition_width,
                          target._coalition_height), (17, 18, 500, 600))
        self.assertEqual((target._mission_detail_w, target._mission_detail_h), (640, 480))

    def test_loading_changes_nothing_that_is_not_a_place(self):
        source = self.source_window()
        geometry, windows = slw.capture(source)
        target = prepared(default_window())
        binding = {"item_class": "cargo_item", "item_category": "c", "item_type": "booster", "display_name": "Booster",
                   "color_rgb": [1, 2, 3]}
        target._hotbar = [dict(binding), None]
        target._gm_favorites = ["Sol", "Vega"]
        target._hangar_order = {"7": [3, 1, 2]}
        target._chat_colours = {"system": (9, 8, 7)}
        target._chat_tabs = [dict(tab) for tab in target._chat_tabs]
        tabs_before = copy.deepcopy(target._chat_tabs)
        target._panels["inventory"]["minimized"] = True
        target._mm_zoom_level, target._mm_open = 6, True
        target._coalition_open = True
        slw._put(target, geometry, windows)
        self.assertEqual(target._hotbar, [binding, None])
        self.assertEqual(target._gm_favorites, ["Sol", "Vega"])
        self.assertEqual(target._hangar_order, {"7": [3, 1, 2]})
        self.assertEqual(target._chat_colours, {"system": (9, 8, 7)})
        self.assertEqual(target._chat_tabs, tabs_before)
        self.assertTrue(target._panels["inventory"]["minimized"])
        self.assertEqual(target._mm_zoom_level, 6)
        self.assertTrue(target._mm_open)
        self.assertTrue(target._coalition_open)

    def test_a_window_saved_at_its_default_place_resets_a_dragged_one(self):
        source = default_window()                          # nothing dragged: every optional window at its default place
        geometry, windows = slw.capture(prepared(source))
        self.assertEqual(geometry["stats"], {"x": None, "y": None})
        target = prepared(dev._moved_window([]))
        self.assertEqual((target._stats_pos_x, target._mm_pos_x), (11, 15))
        slw._put(target, geometry, windows)
        for attr in ("_stats_pos_x", "_stats_pos_y", "_squad_pos_x", "_mm_pos_x", "_mm_pos_y", "_friends_pos_x",
                     "_coalition_pos_x", "_hotbar_pos_x", "_hotbar_pos_y", "_run_summary_pos", "_rc_dock_pos"):
            self.assertIsNone(getattr(target, attr), attr)
        self.assertFalse(target._rc_haul_pos_custom)
        self.assertIsNone(target._rc_haul_panel_pos)

    def test_every_section_the_game_can_save_at_a_default_place_is_one_the_window_resets(self):
        """If the game gains a window whose place can be null and a layout cannot reset it, this says so."""
        geometry = ul.places(default_window()._panel_layout_dict())
        nullable = ul.default_sections(geometry)
        self.assertTrue(nullable)
        self.assertLessEqual(set(nullable), set(slw.DEFAULT_PLACE), "a section the window does not know how to reset")

    def test_a_window_saved_at_its_default_size_resets_one_that_was_resized(self):
        geometry, windows = slw.capture(prepared(default_window()))
        self.assertIsNone(geometry["mission_detail"]["width"])
        target = prepared(dev._moved_window([]))
        self.assertEqual((target._mission_detail_w, target._mission_detail_h), (640, 480))
        slw._put(target, geometry, windows)
        self.assertEqual((target._mission_detail_w, target._mission_detail_h), (None, None))

    def test_every_size_the_game_can_save_as_default_is_one_the_window_resets(self):
        """A panel's max_h / max_w are read by the game even when null; any other null size would be ignored by it."""
        nullable = ul.null_sizes(ul.places(default_window()._panel_layout_dict()))
        self.assertTrue(nullable)
        self.assertLessEqual(set(nullable), set(slw.DEFAULT_SIZE), "a size the window does not know how to reset")

    def test_the_attributes_the_window_resets_are_the_games_own(self):
        source = modloader.game_source("Client.py") + modloader.game_source("hotbar.py")
        for section, attrs in slw.DEFAULT_PLACE.items():
            if section == "combat_log":                    # another mod's window (combat_log.py)
                continue
            for attr, _default in attrs:
                self.assertIn("self." + attr, source, f"{section}: the game has no {attr}")
        for (section, leaf), attr in slw.DEFAULT_SIZE.items():
            self.assertIn("self." + attr, source, f"{section}.{leaf}: the game has no {attr}")

    def test_a_layout_for_another_size_lands_where_the_game_would_have_moved_it(self):
        source = self.source_window()
        layout = ul.new_layout("Big", (1920, 1080), *slw.capture(source))
        target = prepared(default_window(), size=(1280, 720))
        ok, text = slw.apply_layout(target, layout)
        self.assertTrue(ok, text)
        self.assertEqual(text, "Loaded 'Big', fitted from 1920 x 1080 to 1280 x 720.")
        self.assertEqual(ul.fit_point(1500, 800, (1920, 1080), (1280, 720)), (860, 440))      # (a panel that really moves)
        self.assertEqual((target._panels["fighter_control"]["x"], target._panels["fighter_control"]["y"]), (860, 440))
        self.assertEqual(target._panels["inventory"]["max_h"], 260)            # sizes stay

    def test_load_then_undo_restores_the_exact_dict_the_game_had(self):
        source = self.source_window()
        layout = ul.new_layout("Big", (1920, 1080), *slw.capture(source))
        target = prepared(default_window())
        before = target._panel_layout_dict()
        slw.apply_layout(target, layout)
        self.assertNotEqual(target._panel_layout_dict(), before)
        slw.undo(target)
        self.assertEqual(target._panel_layout_dict(), before)

    def test_load_persists_the_layout_through_the_games_options_save(self):
        saves: list = []
        source = self.source_window()
        layout = ul.new_layout("Big", (1920, 1080), *slw.capture(source))
        target = prepared(default_window())
        target._save_options_fn = lambda **kw: saves.append(kw)
        slw.apply_layout(target, layout)
        self.assertEqual(len(saves), 1)
        self.assertEqual(saves[0]["panels"], target._panel_layout_dict())


class SiblingModTests(unittest.TestCase):
    """The windows of other mods that a layout carries: their attribute and save function must exist where this mod says."""

    def read(self, folder, name):
        path = Path(__file__).resolve().parent.parent / folder / name
        if not path.is_file():
            self.skipTest(f"{folder} is not installed next to this mod")
        return path.read_text(encoding="utf-8")

    def test_the_extra_windows_are_where_the_table_says(self):
        for key, attr, module, save in slw.EXTRA_WINDOWS:
            folder = {"galaxy_map_window": "galaxy_map", "ship_layouts_window": "ship_layouts"}[module]
            text = self.read(folder, module + ".py")
            self.assertIn(f"win.{attr} = ", text, key)
            self.assertIn(f"def {save}(win", text, key)


if __name__ == "__main__":
    unittest.main()

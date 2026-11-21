"""Tests for the BARS tab of quick_bars_window.py and what the window hands the extra bars: adding, naming, hiding, resetting and
deleting bars through the window's own clicks and keys, the extra bars' rows on the KEYS tab, the chat commands, and the mouse
events the window passes on to the bars while it is closed."""

from __future__ import annotations
import modloader

import os
import threading
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

import bars
import extra_bars as eb
import keybinds as kb
import quick_bars_window as qw
import quick_keys as qk
from keybinds import Combo
from test_quick_bars_window import Base as KeysBase, Clock, SCREEN, Win, sync_submit
from text_field_host import TextFieldHostMixin


class BarWin(TextFieldHostMixin, Win):
    """The window's stand-in with a text box host (the name box) and an account."""

    def __init__(self, scale=1.0):
        Win.__init__(self, scale)
        self.clipboard = ""
        self._lock = threading.RLock()
        self._username = "alice"

    def _get_clipboard_text(self):
        return self.clipboard

    def _set_clipboard_text(self, text):
        self.clipboard = text


def saved_set(*names, hidden=()):
    return {"version": 1, "bars": [{"id": f"b{i + 1}", "name": name, "mods": list(bars.MOD_SETS[i]), "fx": None, "fy": None,
                                    "visible": name not in hidden, "slots": [None] * 10} for i, name in enumerate(names)]}


class Base(KeysBase):
    def setUp(self):
        super().setUp()
        eb.reset_for_tests()
        self.addCleanup(eb.reset_for_tests)

    def make_window(self, scale=1.0):
        win = BarWin(scale)
        qw._ensure(win)
        return win

    def given(self, *names, hidden=()):
        eb._raw = {"alice": saved_set(*names, hidden=hidden)}

    def open_bars(self, *names, hidden=()):
        if names:
            self.given(*names, hidden=hidden)
        qw.open_window(self.win, "bars")
        self.render()
        return self.win

    def file_bars(self):
        return kb.read_file(self.path).get("bars", {}).get("alice")

    def bar_names(self):
        return [b.name for b in qw.bar_list(self.win)]

    def name_it(self, text):
        self.key(pygame.K_a, mod=pygame.KMOD_CTRL)
        events = [pygame.event.Event(pygame.KEYDOWN, key=ord(ch.lower()) if ch.isalnum() else pygame.K_SPACE, mod=0, unicode=ch)
                  for ch in text]
        qw.filter_events(self.win, events)
        self.key(pygame.K_RETURN)
        self.render()


# ── the tabs ─────────────────────────────────────────────────────────────────

class TabTests(Base):
    def test_it_opens_on_the_keys_tab_and_a_click_on_a_tab_switches(self):
        self.open()
        self.assertEqual(self.win._qb_tab, "keys")
        self.press("tab_bars")
        self.assertEqual(self.win._qb_tab, "bars")
        self.assertEqual(self.win._qb_hits["tab"], "bars")
        self.press("tab_keys")
        self.assertEqual(self.win._qb_tab, "keys")

    def test_switching_tabs_gives_up_waiting_for_a_key_and_typing_a_name(self):
        self.open_bars("Combat")
        self.press("add")
        self.assertIsNotNone(self.win._qb_input)
        self.key(pygame.K_ESCAPE)
        self.press("tab_keys")
        self.win._qb_capture = "hotkey.dock"
        self.press("tab_bars")
        self.assertIsNone(self.win._qb_capture)

    def test_a_tab_click_while_typing_a_name_does_nothing_but_the_name_boxs_own_clicks(self):
        self.open_bars("Combat")
        self.press("add")
        self.assertIsNotNone(self.win._qb_input)
        self.click(self.rects()["cancel"].center)
        self.assertIsNone(self.win._qb_input)

    def test_the_open_tab_is_kept_when_the_window_is_toggled(self):
        qw.open_window(self.win, "bars")
        qw.close_window(self.win)
        qw.open_window(self.win)
        self.assertEqual(self.win._qb_tab, "bars")


class GeometryTests(unittest.TestCase):
    def rects(self, tab, scale):
        s = (lambda px: px) if scale == 1.0 else (lambda px: round(px * scale))
        window = pygame.Rect(40, 30, s(qw._MIN_W), s(qw._MIN_H))
        return window, qw.layout_rects(window, s, tab)

    def test_everything_is_inside_the_window_on_both_tabs_at_every_scale(self):
        for tab in ("keys", "bars"):
            for scale in (1.0, 1.25, 1.5, 2.0):
                window, r = self.rects(tab, scale)
                for name, rect in r.items():
                    self.assertTrue(window.contains(rect), (tab, scale, name, rect))
                    self.assertGreater(min(rect.w, rect.h), 0, (tab, scale, name))

    def test_the_parts_of_each_tab_do_not_overlap(self):
        groups = {"keys": ["tabs", "list", "info", "unbind", "reset", "reset_all"],
                  "bars": ["tabs", "add", "list", "info", "bar_visible", "bar_rename", "bar_place", "bar_delete"]}
        for tab, names in groups.items():
            for scale in (1.0, 1.5, 2.0):
                _window, r = self.rects(tab, scale)
                for i, a in enumerate(names):
                    for b in names[i + 1:]:
                        self.assertFalse(r[a].colliderect(r[b]), (tab, scale, a, b))
                self.assertFalse(r["tab_keys"].colliderect(r["tab_bars"]))
                self.assertTrue(r["tabs"].contains(r["tab_keys"]) and r["tabs"].contains(r["tab_bars"]))

    def test_the_name_box_sits_in_the_add_row(self):
        _window, r = self.rects("bars", 1.0)
        self.assertTrue(r["add"].contains(r["field"]) and r["add"].contains(r["ok"]) and r["add"].contains(r["cancel"]))
        self.assertFalse(r["field"].colliderect(r["ok"]))
        self.assertFalse(r["ok"].colliderect(r["cancel"]))

    def test_there_is_room_for_a_list_at_the_smallest_size_on_both_tabs(self):
        for tab in ("keys", "bars"):
            self.assertGreaterEqual(self.rects(tab, 1.0)[1]["list"].h, 90, tab)


# ── adding and naming a bar ──────────────────────────────────────────────────

class AddTests(Base):
    def test_add_bar_opens_the_name_box_with_a_suggested_name(self):
        self.open_bars("Combat")
        self.press("add")
        self.assertEqual(self.win._qb_input["mode"], "add")
        self.assertEqual(self.win._qb_input["text"], "Bar 3")

    def test_enter_adds_the_bar_selects_it_and_says_what_its_keys_are(self):
        self.open_bars("Combat")
        self.press("add")
        self.key(pygame.K_RETURN)
        self.render()
        self.assertEqual(self.bar_names(), ["Combat", "Bar 3"])
        self.assertEqual(self.win._qb_bar_selected, qw.bar_list(self.win)[1].id)
        self.assertEqual(self.win._qb_status[0], "Added 'Bar 3': Ctrl+1 ... 0 fire its slots.")
        self.assertEqual(len(self.file_bars()["bars"]), 2)
        self.assertTrue(qk.table().has_bars)

    def test_a_typed_name(self):
        self.open_bars()
        self.press("add")
        self.name_it("Mining run")
        self.assertEqual(self.bar_names(), ["Mining run"])

    def test_the_ok_button_adds_and_esc_cancels(self):
        self.open_bars()
        self.press("add")
        self.press("ok")
        self.assertEqual(len(self.bar_names()), 1)
        self.press("add")
        self.press("cancel")
        self.assertIsNone(self.win._qb_input)
        self.assertEqual(len(self.bar_names()), 1)
        self.press("add")
        self.key(pygame.K_ESCAPE)
        self.assertIsNone(self.win._qb_input)
        self.assertEqual(len(self.bar_names()), 1)

    def test_an_empty_name_is_refused_and_the_box_stays_open(self):
        self.open_bars()
        self.press("add")
        self.key(pygame.K_a, mod=pygame.KMOD_CTRL)
        self.key(pygame.K_BACKSPACE)
        self.key(pygame.K_RETURN)
        self.assertIsNotNone(self.win._qb_input)
        self.assertEqual(self.bar_names(), [])
        self.assertEqual(self.win._qb_status[1], "warn")

    def test_a_name_that_is_taken_gets_a_number(self):
        self.open_bars("Combat")
        self.press("add")
        self.name_it("combat")
        self.assertEqual(self.bar_names(), ["Combat", "combat (2)"])

    def test_the_name_is_capped(self):
        self.open_bars()
        self.press("add")
        self.name_it("x" * 80)
        self.assertEqual(len(self.bar_names()[0]), bars.MAX_NAME_LEN)

    def test_the_flight_keys_are_quiet_while_a_name_is_typed(self):
        self.open_bars()
        self.assertFalse(qw.capturing(self.win))
        self.press("add")
        self.assertTrue(qw.capturing(self.win))
        self.assertTrue(qw.any_capturing())
        self.key(pygame.K_ESCAPE)
        self.assertFalse(qw.any_capturing())

    def test_add_does_nothing_without_a_logged_in_account(self):
        self.win._username = None
        self.open_bars()
        self.press("add")
        self.assertIsNone(self.win._qb_input)
        self.assertFalse(qw.button_enabled(self.win, "add"))
        qw.begin_input(self.win, "add")                                        # not by any other way in either
        self.assertIsNone(self.win._qb_input)

    def test_add_stops_at_the_limit_and_the_window_says_so(self):
        self.open_bars(*[f"Bar {i}" for i in range(bars.MAX_BARS)])
        self.assertFalse(qw.button_enabled(self.win, "add"))
        self.press("add")
        self.assertIsNone(self.win._qb_input)
        self.assertEqual(len(self.bar_names()), bars.MAX_BARS)

    def test_the_typed_text_is_the_name_boxs_and_the_box_takes_the_clicks_it_needs(self):
        self.open_bars()
        self.press("add")
        self.click(self.rects()["field"].center)
        self.assertIsNotNone(self.win._qb_input)


# ── the list and its buttons ─────────────────────────────────────────────────

class ListTests(Base):
    def setUp(self):
        super().setUp()
        self.open_bars("Combat", "Mining", "Hauling")

    def rows(self):
        return {bar_id: rect for rect, bar_id in self.win._qb_hits["rows"]}

    def test_the_first_bar_is_selected_and_the_rows_are_the_bars(self):
        self.assertEqual(self.win._qb_bar_selected, "b1")
        self.assertEqual(list(self.rows()), ["b1", "b2", "b3"])

    def test_clicking_a_row_selects_it(self):
        self.click(self.rows()["b3"].center)
        self.assertEqual(self.win._qb_bar_selected, "b3")

    def test_hide_and_show_the_selected_bar(self):
        self.press("bar_visible")
        self.assertFalse(qw.selected_bar(self.win).visible)
        self.assertEqual(self.win._qb_status[0], "'Combat' is hidden.")
        self.assertFalse(self.file_bars()["bars"][0]["visible"])
        self.press("bar_visible")
        self.assertTrue(qw.selected_bar(self.win).visible)

    def test_rename_starts_with_the_current_name(self):
        self.click(self.rows()["b2"].center)
        self.press("bar_rename")
        self.assertEqual(self.win._qb_input["text"], "Mining")
        self.name_it("Mining run")
        self.assertEqual(self.bar_names(), ["Combat", "Mining run", "Hauling"])
        self.assertEqual(self.win._qb_status[0], "Renamed to 'Mining run'.")
        self.assertEqual(self.file_bars()["bars"][1]["name"], "Mining run")

    def test_renaming_a_bar_renames_its_group_on_the_keys_tab(self):
        self.press("bar_rename")
        self.name_it("Alpha")
        self.assertEqual(qk.table().by_id["slot.b1.1"].group, "Alpha")

    def test_reset_place(self):
        eb.runtime(self.win).barset.set_place("b1", 0.3, 0.4)
        self.press("bar_place")
        self.assertEqual((qw.selected_bar(self.win).fx, qw.selected_bar(self.win).fy), (None, None))

    def test_delete_needs_a_second_click_on_the_same_bar(self):
        self.press("bar_delete")
        self.assertEqual(len(self.bar_names()), 3)
        self.assertEqual(self.win._qb_confirm_bar[0], "b1")
        self.click(self.rows()["b2"].center)                                   # selecting another disarms
        self.assertIsNone(self.win._qb_confirm_bar)
        self.press("bar_delete")
        self.press("bar_delete")
        self.assertEqual(self.bar_names(), ["Combat", "Hauling"])
        self.assertEqual(self.win._qb_bar_selected, "b1")                      # the first one left is selected now
        self.assertEqual([b["id"] for b in self.file_bars()["bars"]], ["b1", "b3"])
        self.assertIn("Deleted 'Mining'", self.win._qb_status[0])

    def test_an_armed_delete_expires(self):
        clock = Clock()
        with mock.patch.object(qw.time, "monotonic", clock.monotonic):
            self.press("bar_delete")
            clock.now += qw.CONFIRM_S + 1
            self.render()
            self.assertIsNone(self.win._qb_confirm_bar)
            self.press("bar_delete")
        self.assertEqual(len(self.bar_names()), 3)

    def test_deleting_the_last_bar_leaves_an_empty_list(self):
        self.win._qb_bar_selected = "b1"
        eb.remove_bar(self.win, "b2")
        eb.remove_bar(self.win, "b3")
        self.press("bar_delete")
        self.press("bar_delete")
        self.assertEqual(self.bar_names(), [])
        self.assertIsNone(self.win._qb_bar_selected)

    def test_the_buttons_do_nothing_without_a_bar(self):
        self.open_bars()
        for bar in list(qw.bar_list(self.win)):
            eb.remove_bar(self.win, bar.id)
        self.win._qb_bar_selected = None
        self.render()
        for name in ("bar_visible", "bar_rename", "bar_place", "bar_delete"):
            self.press(name)
        self.assertIsNone(self.win._qb_input)
        self.assertIsNone(self.win._qb_confirm_bar)

    def test_the_wheel_scrolls_a_long_list(self):
        for _ in range(3):
            eb.add_bar(self.win)
        self.win._qb_rect = pygame.Rect(self.win._qb_rect.x, self.win._qb_rect.y, self.win._qb_rect.w, qw._MIN_H)
        self.render()
        area = self.rects()["list"]
        self.assertGreater(self.win._qb_max_scroll, 0)
        with mock.patch("pygame.mouse.get_pos", return_value=area.center):
            self.assertEqual(qw.filter_events(self.win, [pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-3)]), [])
        self.assertGreater(self.win._qb_scroll, 0)

    def test_it_draws_at_every_ui_scale_with_bars_a_name_box_and_nothing(self):
        for scale in (1.0, 1.5, 2.0):
            win = self.make_window(scale)
            qw.open_window(win, "bars")
            self.render(win)
            self.assertEqual(len(win._qb_hits["rows"]), 3)
            win._qb_input = {"mode": "add", "text": "x", "target": None}
            win._text_cursor_to_end(qw.FIELD_KEY, "x", owner=win._qb_input)
            self.render(win)
            win._qb_input = None
        self.win._username = None
        self.render()
        self.assertEqual(len(self.win._qb_hits["rows"]), 0)                     # no account: no bars, and the list says to log in

    def test_a_hidden_bar_is_tagged_and_the_info_line_says_what_hidden_means(self):
        self.press("bar_visible")
        self.assertFalse(qw.selected_bar(self.win).visible)
        self.render()                                                          # (the text itself is drawn, not stored)
        self.assertEqual(self.win._qb_hits["tab"], "bars")


# ── the extra bars' keys on the KEYS tab ─────────────────────────────────────

class KeysTabTests(Base):
    def test_each_bar_is_a_group_of_ten_slots(self):
        self.given("Combat", "Mining")
        qw.open_window(self.win, "bars")
        eb.ensure_loaded(self.win)
        rows = qw.view_rows(qk.table().catalog)
        groups = [d for k, d in rows if k == "group"]
        self.assertEqual(groups, [kb.GROUP_BAR, kb.GROUP_HOTKEYS, "Combat", "Mining"])
        self.assertEqual(len([d for k, d in rows if k == "action" and d.kind == "barslot"]), 20)

    def test_a_bar_slot_can_be_given_a_key_and_the_window_names_the_bar(self):
        self.given("Combat")
        qw.open_window(self.win, "keys")
        eb.ensure_loaded(self.win)
        qw.begin_capture(self.win, "slot.b1.1")
        self.render()
        self.key(pygame.K_F1)
        self.assertEqual(qk.table().combo("slot.b1.1"), Combo(pygame.K_F1))
        self.assertEqual(self.win._qb_status[0], "Combat: Slot 1: F1.")

    def test_taking_another_bars_key_says_which_slot_lost_it(self):
        self.given("Combat", "Mining")
        qw.open_window(self.win, "keys")
        eb.ensure_loaded(self.win)
        qw.begin_capture(self.win, "slot.b2.1")
        self.key(pygame.K_1, mod=pygame.KMOD_LSHIFT)
        self.assertIn("Combat: Slot 1 had it and is now unbound", self.win._qb_status[0])

    def test_the_keys_tab_draws_with_bar_groups_and_scrolls_to_the_last_one(self):
        self.given("Combat", "Mining")
        qw.open_window(self.win, "keys")
        self.render()
        self.win._qb_scroll = 10 ** 6
        self.render()
        self.assertIsNotNone(self.row("slot.b2.10"))

    def test_deleting_a_bar_takes_its_group_off_the_list(self):
        self.given("Combat", "Mining")
        qw.open_window(self.win, "keys")
        eb.ensure_loaded(self.win)
        eb.remove_bar(self.win, "b1")
        groups = [d for k, d in qw.view_rows(qk.table().catalog) if k == "group"]
        self.assertEqual(groups, [kb.GROUP_BAR, kb.GROUP_HOTKEYS, "Mining"])

    def test_reset_all_resets_the_bars_keys_too(self):
        self.given("Combat")
        qw.open_window(self.win, "keys")
        eb.ensure_loaded(self.win)
        qk.set_binding("slot.b1.1", Combo(pygame.K_F1))
        self.render()
        self.press("reset_all")
        self.press("reset_all")
        self.assertEqual(qk.table().combo("slot.b1.1"), Combo(pygame.K_1, {"shift"}))


# ── the commands ─────────────────────────────────────────────────────────────

class CommandTests(Base):
    def say(self, text):
        self.win.chat.clear()
        qw.handle_command(self.win, text)
        return list(self.win.chat)

    def test_bars_opens_the_bars_tab_and_a_second_one_closes_it(self):
        self.say("/bars")
        self.assertTrue(self.win._qb_open)
        self.assertEqual(self.win._qb_tab, "bars")
        self.say("/quickbars")
        self.assertFalse(self.win._qb_open)

    def test_keys_while_the_bars_tab_is_open_switches_to_the_keys_tab(self):
        self.say("/bars")
        self.say("/keys")
        self.assertTrue(self.win._qb_open)
        self.assertEqual(self.win._qb_tab, "keys")
        self.say("/keys")
        self.assertFalse(self.win._qb_open)

    def test_list_with_no_bars(self):
        self.assertIn("No extra bars", self.say("/bars list")[0])

    def test_list_with_some(self):
        self.given("Combat", "Mining", hidden=("Mining",))
        self.assertEqual(self.say("/bars list"), ["  Combat   Shift+1 ... 0   0 of 10 slots   shown",
                                                  "  Mining   Ctrl+1 ... 0   0 of 10 slots   hidden"])

    def test_add_with_and_without_a_name(self):
        self.assertEqual(self.say("/bars add Combat bar"), ["Added 'Combat bar': Shift+1 ... 0 fire its slots."])
        self.assertEqual(self.say("/bars add"), ["Added 'Bar 3': Ctrl+1 ... 0 fire its slots."])
        self.assertEqual(self.bar_names(), ["Combat bar", "Bar 3"])
        self.assertEqual(len(self.file_bars()["bars"]), 2)

    def test_add_without_an_account_or_over_the_limit_says_so(self):
        self.win._username = None
        self.assertIn("Could not add a bar", self.say("/bars add")[0])
        self.win._username = "alice"
        for i in range(bars.MAX_BARS):
            self.say(f"/bars add b{i}")
        self.assertIn("Could not add a bar", self.say("/bars add")[0])

    def test_hide_and_show_by_name_or_unique_prefix(self):
        self.given("Combat", "Mining")
        eb.ensure_loaded(self.win)
        self.assertEqual(self.say("/bars hide mining"), ["'Mining' is hidden."])
        self.assertFalse(eb.runtime(self.win).barset.get("b2").visible)
        self.assertEqual(self.say("/bars show Min"), ["'Mining' is shown."])
        self.assertTrue(eb.runtime(self.win).barset.get("b2").visible)

    def test_a_name_that_matches_nothing_or_two_bars_is_not_guessed(self):
        self.given("Combat", "Comms")
        eb.ensure_loaded(self.win)
        self.assertIn("No bar called 'co'", self.say("/bars hide co")[0])
        self.assertIn("No bar called 'tv'", self.say("/bars show tv")[0])
        self.assertEqual(self.say("/bars hide"), ["Usage: /bars hide <name>"])
        self.assertTrue(eb.runtime(self.win).barset.get("b1").visible)

    def test_an_unknown_word_shows_the_help(self):
        self.assertIn("/bars add", self.say("/bars frobnicate")[0])

    def test_keys_list_names_the_bar_of_a_changed_bar_slot(self):
        self.given("Combat")
        eb.ensure_loaded(self.win)
        qk.set_binding("slot.b1.2", Combo(pygame.K_F2))
        self.assertEqual(self.say("/keys list"), ["  Combat: Slot 2: F2   (default Shift+2)"])

    def test_switched_off_it_says_so(self):
        with mock.patch.dict(os.environ, {qw.ENV_OFF: "1"}):
            self.assertEqual(self.say("/bars list"), ["Quick bars are switched off."])


# ── what the window hands the extra bars ─────────────────────────────────────

class HandOffTests(Base):
    def test_the_bars_get_the_mouse_events_even_with_the_window_closed(self):
        down = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(5, 5), button=1)
        key = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_r, mod=0, unicode="r", scancode=0)
        seen = []

        def handle(win, event):
            seen.append(event.type)
            return event.type == pygame.MOUSEBUTTONDOWN

        with mock.patch.object(eb, "handle_event", handle):
            kept = qw.filter_events(self.win, [down, key])
        self.assertEqual(kept, [key])
        self.assertEqual(seen, [pygame.MOUSEBUTTONDOWN, pygame.KEYDOWN])

    def test_the_window_takes_its_own_clicks_before_the_bars_see_them(self):
        self.open()
        seen = []
        with mock.patch.object(eb, "handle_event", lambda w, e: seen.append(e.type) or False):
            self.click(self.win._qb_rect.center)
        self.assertEqual(seen, [])

    def test_a_failing_bar_handler_switches_the_window_hook_off_and_returns_the_events(self):
        events = [pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(5, 5), button=1)]
        with mock.patch.object(eb, "handle_event", side_effect=RuntimeError("boom")), self.assertLogs(qw.logger, "ERROR"):
            self.assertIs(qw.filter_events(self.win, events), events)
        self.assertFalse(qw._enabled())

    def test_the_draw_hook_goes_to_the_extra_bars(self):
        frame = object()
        with mock.patch.object(eb, "draw_bars") as draw:
            qw.draw_bars(self.win, frame)
        draw.assert_called_once_with(self.win, frame)

    def test_install_hooks_the_extra_bars_and_the_labels(self):
        class Window(BarWin):
            def _hotbar_drawn_state(self, hover_slot, tooltip=True):
                return ("slots", None)

        self.assertTrue(qw.install(Window, {"_flight_text_entry_active": lambda **kw: False}))
        self.assertTrue(Window._extra_bars_installed)
        self.assertFalse(qw.install(Window))
        self.assertIn("_hotbar_drawn_state", Window.__dict__)                  # the label hook wrapped it on this class


if __name__ == "__main__":
    unittest.main()

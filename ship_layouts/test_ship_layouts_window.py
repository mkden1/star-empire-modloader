"""Tests for ship_layouts_window.py - the SHIP LAYOUTS window: geometry, the save / update / rename / delete flow,
typing, mouse handling, the hooks, drawing, and the vanilla lines that open / draw / feed it."""

from __future__ import annotations
import modloader

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

import premium_shop
import scan_writer
import ship_layouts as sl
import ship_layouts_window as slw
from run_summary_gui import RunSummaryState
from gui_window_manager import GuiLayer, GuiWindowManager
from test_ship_layouts import cargo_item, cargo_summary, fit, gear, hardpoint, plugin, specs, weapon
from text_field_host import TextFieldHostMixin

SCREEN = (1280, 720)
BACKDROP = (20, 60, 30, 255)


def sync_submit(fn, *args):
    fn(*args)


class FakeWindow(TextFieldHostMixin):
    """The parts of SolarSystemWindow the window touches (the text boxes' host is the game's own mixin)."""

    def __init__(self, scale=1.0):
        self.clipboard = ""
        self._uk = scale
        self._lock = threading.RLock()
        self._inventory = [weapon(0), weapon(1), gear("energy", "glorious_power", "Glorious Power"),
                           gear("shield", "stormsail", "Stormsail"), gear("engine", "glorious_booster", "Glorious Booster"),
                           plugin(0), plugin(1)]
        self._inventory_version = 1
        self._ship_plugin_data = None
        self._ship_plugin_version = 0
        self._turret_state = {"turrets_enabled": True, "hardpoints": [
            hardpoint(0, "kings_thumper", "King's Thumper", [("c_broad", "Broadside Controller")] * 2, dps=21000.0)]}
        self._ship_specs_data = specs()
        self._username = "mk"
        self._gui_windows = GuiWindowManager()
        self.sent: list = []
        self.chat: list = []
        self._fonts: dict = {}

    def _send_fn(self, message):
        self.sent.append(message)

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

    def _find_my_entity(self):
        return {"ship_type": "panstar", "ship_display_name": "Panstar"}

    def chat_log(self, text, colour=None):
        self.chat.append(text)

    def set_ship_specs_data(self, data):
        with self._lock:
            self._ship_specs_data = data


class Win(FakeWindow):
    """A class of its own, so ``install`` can wrap it without touching the shared FakeWindow."""


GATE_CALLS: list = []


def _original_gate(**kwargs):
    GATE_CALLS.append(kwargs)
    return bool(kwargs.get("chat_focused"))


CLIENT_GLOBALS = {"_flight_text_entry_active": _original_gate}
slw.install(Win, None, CLIENT_GLOBALS)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode(SCREEN)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        slw.reset_for_tests()
        slw.configure_for_tests(os.path.join(self._tmp.name, "ship_layouts_window.json"))
        self.addCleanup(self._cleanup)
        self.win = self.make_window()

    def _cleanup(self):
        scan_writer.flush()
        slw.reset_for_tests()
        slw.configure_for_tests(None)
        self._tmp.cleanup()

    def make_window(self, scale=1.0):
        win = Win(scale)
        slw._ensure(win)
        win._sl_store = sl.LayoutStore(os.path.join(self._tmp.name, "layouts"), submit=sync_submit)
        return win

    def add_layout(self, win=None, **kwargs):
        win = win or self.win
        layout = fit(**kwargs)
        win._sl_store.save(layout)
        return layout

    def render(self, win=None, size=SCREEN, mouse=(5, 5)):
        win = win or self.win
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
        return self.win._sl_hits["rects"]

    def click(self, pos, win=None, button=1):
        win = win or self.win
        events = [pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=button),
                  pygame.event.Event(pygame.MOUSEBUTTONUP, pos=pos, button=button)]
        with mock.patch("pygame.mouse.get_pos", return_value=pos):
            return slw.filter_events(win, events)

    def type_text(self, text, win=None):
        win = win or self.win
        events = [pygame.event.Event(pygame.KEYDOWN, key=ord(ch.lower()) if ch.isalnum() else pygame.K_SPACE,
                                     mod=0, unicode=ch) for ch in text]
        return slw.filter_events(win, events)

    def key(self, key, win=None, mod=0, unicode=""):
        win = win or self.win
        return slw.filter_events(win, [pygame.event.Event(pygame.KEYDOWN, key=key, mod=mod, unicode=unicode)])

    def field(self, win=None):
        """The open name box's TextField (caret, selection and drag live there, not in the box dict)."""
        win = win or self.win
        return win._text_field(slw.FIELD_KEY, owner=win._sl_input)


class Clock:
    def __init__(self, start=1000.0):
        self.now = start

    def monotonic(self):
        return self.now

    def time(self):
        return 1_700_000_000.0


# ── geometry and pure helpers ────────────────────────────────────────────────

class GeometryTests(unittest.TestCase):
    def rects(self, scale, size=None):
        s = (lambda px: px) if scale == 1.0 else (lambda px: round(px * scale))
        width, height = size or (s(slw._MIN_W), s(slw._MIN_H))
        window = pygame.Rect(40, 30, width, height)
        return window, slw.layout_rects(window, s)

    def test_everything_is_inside_the_window_at_every_size_and_scale(self):
        for scale in (1.0, 1.25, 1.5, 2.0):
            for grow in (0, 150):
                window, r = self.rects(scale)
                window = window.inflate(grow, grow)
                r = slw.layout_rects(window, (lambda px: round(px * scale)))
                for name, rect in r.items():
                    self.assertTrue(window.contains(rect), (scale, grow, name, rect, window))
                    self.assertGreater(rect.w, 0, (scale, name))
                    self.assertGreater(rect.h, 0, (scale, name))

    def test_the_parts_do_not_overlap(self):
        for scale in (1.0, 1.5, 2.0):
            _window, r = self.rects(scale)
            groups = [("left", ["save", "list", "update", "rename", "delete"]),
                      ("right", ["info", "options", "compare"])]
            for _title, names in groups:
                for i, a in enumerate(names):
                    for b in names[i + 1:]:
                        self.assertFalse(r[a].colliderect(r[b]), (scale, a, b, r[a], r[b]))
            self.assertFalse(r["left"].colliderect(r["right"]))
            self.assertFalse(r["body"].colliderect(r["header"]))
            self.assertFalse(r["body"].colliderect(r["status"]))
            self.assertFalse(r["field"].colliderect(r["ok"]))
            self.assertFalse(r["ok"].colliderect(r["cancel"]))
            self.assertTrue(r["save"].contains(r["field"]) and r["save"].contains(r["ok"])
                            and r["save"].contains(r["cancel"]))

    def test_the_buttons_row_sits_below_the_list_and_the_list_below_the_save_button(self):
        _window, r = self.rects(1.0)
        self.assertGreater(r["list"].top, r["save"].bottom)
        self.assertGreater(r["update"].top, r["list"].bottom)
        self.assertEqual({r["update"].y, r["rename"].y, r["delete"].y}, {r["update"].y})
        self.assertLess(r["update"].right, r["rename"].left)
        self.assertLess(r["rename"].right, r["delete"].left)

    def test_there_is_room_for_a_list_at_the_smallest_size(self):
        _window, r = self.rects(1.0)
        self.assertGreaterEqual(r["list"].h, 100)
        self.assertGreaterEqual(r["compare"].h, 150)
        self.assertGreaterEqual(r["compare"].w, 300)

    def test_the_left_column_is_a_third_at_most(self):
        window, r = self.rects(1.0, (1000, 700))
        self.assertLessEqual(r["left"].w, 280)
        self.assertGreaterEqual(r["left"].w, 190)

    def test_age_text(self):
        self.assertEqual(slw.format_age(0), "just now")
        self.assertEqual(slw.format_age(89), "just now")
        self.assertEqual(slw.format_age(600), "10 min ago")
        self.assertEqual(slw.format_age(7200), "2 h ago")
        self.assertEqual(slw.format_age(86400), "1 day ago")
        self.assertEqual(slw.format_age(3 * 86400 + 5), "3 days ago")
        self.assertEqual(slw.format_age(-5), "just now")


class BuildViewTests(unittest.TestCase):
    def diff(self, **kwargs):
        saved = fit(inventory=[weapon(0), weapon(1), gear("engine", "e1", "Old Engine"), cargo_item("kt", "KT")])
        current = fit(inventory=[weapon(0), weapon(1, "x", "Xenon"), gear("engine", "e1", "Old Engine")], **kwargs)
        return sl.compare(saved, current)

    def kinds(self, lines):
        return [kind for kind, _data in lines]

    def test_only_differences_by_default_with_a_count_for_what_matches(self):
        lines = slw.build_view(self.diff(), False, False)
        rows = [d for k, d in lines if k == "row"]
        self.assertTrue(all(r.status != sl.STATUS_SAME for r in rows))
        notes = [d for k, d in lines if k == "note"]
        self.assertIn("all 1 match", notes)                       # equipment: the engine matches, nothing else
        self.assertEqual([d.key for k, d in lines if k == "section"][:5],
                         ["weapons", "equipment", "plugins", "turrets", "cargo"])

    def test_show_matching_lists_them_too(self):
        lines = slw.build_view(self.diff(), True, False)
        self.assertTrue(any(k == "row" and d.status == sl.STATUS_SAME for k, d in lines))
        self.assertNotIn("all 1 match", [d for k, d in lines if k == "note"])

    def test_cargo_is_a_header_only_until_opened(self):
        closed = slw.build_view(self.diff(), False, False)
        opened = slw.build_view(self.diff(), False, True)
        def after_cargo(lines):
            index = next(i for i, (k, d) in enumerate(lines) if k == "section" and d.key == "cargo")
            return lines[index + 1]
        self.assertEqual(after_cargo(closed)[0], "section")          # straight on to the stats
        self.assertEqual(after_cargo(opened)[0], "row")

    def test_a_section_with_nothing_on_either_ship_says_so(self):
        lines = slw.build_view(self.diff(), False, False)
        self.assertIn("none on either ship", [d for k, d in lines if k == "note"])

    def test_the_state_note_comes_right_after_the_stats(self):
        summary = cargo_summary()
        saved = fit(inventory=[summary], specs=specs(state="in_space", effective_mass=3051514.0, accel=413.8))
        current = fit(inventory=[summary], specs=specs(state="docked", effective_mass=2664514.0, accel=474.0))
        lines = slw.build_view(sl.compare(saved, current), False, False)
        self.assertEqual([k for k, _d in lines[-4:]], ["note"] * 4)
        self.assertIn("Save in space", lines[-1][1])
        same = slw.build_view(sl.compare(saved, fit(inventory=[summary], specs=specs(state="in_space",
                                                                                     effective_mass=3051514.0,
                                                                                     accel=413.8))), False, False)
        self.assertNotIn("Save in space", " ".join(str(d) for k, d in same if k == "note"))

    def test_the_stats_come_last_grouped(self):
        lines = slw.build_view(self.diff(), False, False)
        stats_at = next(i for i, (k, d) in enumerate(lines) if k == "section" and d.key == "stats")
        tail = lines[stats_at + 1:]
        self.assertEqual(self.kinds(tail)[0], "group")
        self.assertTrue(all(k in ("group", "stat") for k in self.kinds(tail)))
        self.assertEqual([d for k, d in tail if k == "group"][0], "Damage")

    def test_no_stats_section_without_stats(self):
        diff = sl.compare(fit(specs=None, turret_state=None), fit(specs=None, turret_state=None))
        self.assertNotIn("stats", [d.key for k, d in slw.build_view(diff, False, False) if k == "section"])

    def test_heights_add_up(self):
        lines = [("section", None), ("row", None), ("note", None), ("group", None), ("stat", None), ("gap", None)]
        expected = (slw.SECTION_H + slw.ROW_H + slw.ROW_H + slw.GROUP_H + slw.ROW_H + slw.GAP_H)
        self.assertEqual(slw.view_height(lines, lambda px: px), expected)
        self.assertEqual(slw.view_height([], lambda px: px), 0)
        self.assertEqual(slw.view_height([("row", None)], lambda px: px * 2), slw.ROW_H * 2)


# ── open / close / selection ─────────────────────────────────────────────────

class OpenTests(Base):
    def test_opening_asks_for_fresh_specs_and_reads_the_folder(self):
        slw.open_window(self.win)
        self.assertTrue(slw.is_open(self.win))
        self.assertIn("SHIP_SPECS", self.win.sent)
        self.assertTrue(self.win._sl_store.loaded)

    def test_toggle(self):
        slw.toggle(self.win)
        self.assertTrue(slw.is_open(self.win))
        slw.toggle(self.win)
        self.assertFalse(slw.is_open(self.win))

    def test_closing_drops_the_typing_and_drag_state_and_saves_the_settings(self):
        slw.open_window(self.win)
        self.win._sl_input = {"mode": "save", "text": "x", "target": None}
        self.win._sl_drag = {"kind": "move"}
        self.win._sl_rect = pygame.Rect(50, 60, 700, 500)
        slw.close_window(self.win)
        scan_writer.flush()
        self.assertIsNone(self.win._sl_input)
        self.assertIsNone(self.win._sl_drag)
        with open(slw._path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["rect"], [50, 60, 700, 500])

    def test_the_selection_prefers_a_layout_saved_on_this_ship(self):
        self.add_layout(name="oak", now=3.0, specs=specs("oakheart", "Oakheart"))
        wanted = self.add_layout(name="pan old", now=1.0)
        self.add_layout(name="pan older", now=0.5)
        slw.open_window(self.win)
        self.assertEqual(self.win._sl_selected, wanted["id"])

    def test_without_a_layout_for_this_ship_the_newest_is_selected(self):
        newest = self.add_layout(name="oak", now=3.0, specs=specs("oakheart", "Oakheart"))
        self.add_layout(name="cinder", now=1.0, specs=specs("cinder", "Cinder"))
        slw.open_window(self.win)
        self.assertEqual(self.win._sl_selected, newest["id"])

    def test_an_existing_selection_is_kept(self):
        self.add_layout(name="a", now=1.0)
        chosen = self.add_layout(name="b", now=2.0, specs=specs("oakheart", "Oakheart"))
        self.win._sl_selected = chosen["id"]
        slw.open_window(self.win)
        self.assertEqual(self.win._sl_selected, chosen["id"])

    def test_a_vanished_selection_is_replaced_and_no_layouts_means_none(self):
        self.win._sl_selected = "gone"
        slw.open_window(self.win)
        self.assertIsNone(self.win._sl_selected)
        layout = self.add_layout()
        slw.tick(self.win)
        self.assertEqual(self.win._sl_selected, layout["id"])

    def test_the_ship_is_known_from_the_entity_when_the_specs_have_not_come(self):
        self.win._ship_specs_data = None
        self.assertEqual(slw.current_layout(self.win)["ship"]["ship_type"], "panstar")
        self.win._find_my_entity = lambda: None
        self.assertEqual(slw.current_layout(self.win)["ship"], {})

    def test_the_current_layout_is_cached_until_something_changes(self):
        first = slw.current_cached(self.win)
        self.assertIs(slw.current_cached(self.win), first)
        self.win._inventory_version += 1
        self.assertIsNot(slw.current_cached(self.win), first)
        again = slw.current_cached(self.win)
        self.win._turret_state = {"hardpoints": []}
        self.assertIsNot(slw.current_cached(self.win), again)
        again = slw.current_cached(self.win)
        self.win._sl_specs_count += 1
        self.assertIsNot(slw.current_cached(self.win), again)

    def test_the_current_layout_follows_plugin_data_when_the_inventory_has_no_plugin_rows(self):
        self.win._ship_plugin_data = {"installed": {"0": "plugin_a"}, "plugin_defs": {}}
        self.win._ship_plugin_version = 1
        self.win._inventory = [weapon(0)]
        self.win._inventory_version += 1
        self.assertEqual([p["plugin_id"] for p in slw.current_cached(self.win)["plugins"]], ["plugin_a"])
        self.win._ship_plugin_data = {"installed": {"0": "plugin_b"}, "plugin_defs": {}}
        self.win._ship_plugin_version = 2                                         # the inventory itself did not change
        self.assertEqual([p["plugin_id"] for p in slw.current_cached(self.win)["plugins"]], ["plugin_b"])

    def test_the_diff_follows_the_ship(self):
        layout = self.add_layout()
        self.win._sl_selected = layout["id"]
        saved, diff = slw.current_diff(self.win)
        self.assertEqual(diff.differences, 0)
        self.win._inventory = [weapon(0, "x", "Xenon")] + self.win._inventory[1:]
        self.win._inventory_version += 1
        _saved, diff = slw.current_diff(self.win)
        self.assertEqual(diff.differences, 1)
        self.assertIsNone(slw.current_diff(self.make_window_with_none())[0])

    def make_window_with_none(self):
        win = self.make_window()
        win._sl_selected = None
        return win

    def test_the_diff_is_reused_until_a_layout_or_the_ship_changes(self):
        layout = self.add_layout()
        self.win._sl_selected = layout["id"]
        _s, first = slw.current_diff(self.win)
        _s, second = slw.current_diff(self.win)
        self.assertIs(first, second)
        self.win._sl_store.save({**layout, "name": "renamed"})
        _s, third = slw.current_diff(self.win)
        self.assertIsNot(first, third)


# ── save / update / rename / delete ──────────────────────────────────────────

class SaveTests(Base):
    def setUp(self):
        super().setUp()
        self.clock = Clock()
        patcher = mock.patch.object(slw, "time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        slw.open_window(self.win)
        self.win.sent.clear()

    def reply(self):
        """The server's Ship Specs answer arrives (what the wrapped set_ship_specs_data does)."""
        self.clock.now += 0.2
        self.win.set_ship_specs_data(specs())

    def test_the_name_field_starts_with_a_sensible_name(self):
        slw.begin_input(self.win, "save")
        self.assertEqual(self.win._sl_input["text"], "Panstar layout")
        self.assertEqual(self.field().cursor, len("Panstar layout"))

    def test_an_empty_name_is_refused_and_the_field_stays(self):
        slw.begin_input(self.win, "save")
        self.win._sl_input["text"] = "   "
        slw.commit_input(self.win)
        self.assertIsNotNone(self.win._sl_input)
        self.assertIsNone(self.win._sl_pending)
        self.assertEqual(self.win._sl_status[1], "warn")

    def test_committing_waits_for_fresh_specs_then_saves_with_them(self):
        slw.begin_input(self.win, "save")
        self.win._sl_input["text"] = "hunter stealth"
        slw.commit_input(self.win)
        self.assertEqual(self.win.sent, ["SHIP_SPECS"])
        self.assertIsNone(self.win._sl_input)
        self.assertEqual(self.win._sl_store.layouts, ())                    # nothing yet
        self.clock.now += 1.0
        slw.tick(self.win)
        self.assertEqual(self.win._sl_store.layouts, ())                    # still waiting
        self.reply()
        slw.tick(self.win)
        layouts = self.win._sl_store.layouts
        self.assertEqual([d["name"] for d in layouts], ["hunter stealth"])
        self.assertEqual(self.win._sl_selected, layouts[0]["id"])
        self.assertIn("total_dps", layouts[0]["stats"])
        self.assertEqual((self.win._sl_status[0], self.win._sl_status[1]), ("Saved 'hunter stealth'.", "ok"))
        self.assertIsNone(self.win._sl_pending)

    def test_without_an_answer_it_saves_anyway_and_says_so(self):
        slw.begin_save(self.win, "no answer")
        self.clock.now += slw.SPECS_WAIT_S - 0.1
        slw.tick(self.win)
        self.assertEqual(self.win._sl_store.layouts, ())
        self.clock.now += 0.2
        slw.tick(self.win)
        self.assertEqual([d["name"] for d in self.win._sl_store.layouts], ["no answer"])
        self.assertEqual(self.win._sl_status[1], "warn")
        self.assertIn("without fresh stats", self.win._sl_status[0])

    def test_an_answer_that_was_already_on_its_way_before_the_click_does_not_count(self):
        self.win.set_ship_specs_data(specs())                                # arrives, then the user clicks
        old_count = self.win._sl_specs_count
        slw.begin_save(self.win, "x")
        self.assertEqual(self.win._sl_pending["count"], old_count)
        slw.tick(self.win)
        self.assertEqual(self.win._sl_store.layouts, ())

    def test_a_saved_layout_becomes_the_selected_one_and_the_selection_is_remembered(self):
        old = self.add_layout(name="old", now=1.0)
        self.win._sl_selected = old["id"]
        slw.begin_save(self.win, "fresh")
        self.reply()
        slw.tick(self.win)
        fresh = next(d for d in self.win._sl_store.layouts if d["name"] == "fresh")
        self.assertEqual(self.win._sl_selected, fresh["id"])
        scan_writer.flush()
        self.assertEqual(slw.load(slw._path)["selected"], fresh["id"])

    def test_while_a_save_waits_the_specs_are_not_asked_for_again_and_again(self):
        self.win._sl_selected = self.add_layout()["id"]                         # polling happens with a selection
        self.win.sent.clear()
        slw.begin_save(self.win, "waiting")
        self.clock.now += 2.5                                                  # past the poll interval, short of the wait
        slw.tick(self.win)
        self.assertEqual(self.win.sent, ["SHIP_SPECS"])

    def test_the_same_name_gets_a_number_instead_of_replacing(self):
        self.add_layout(name="hunter")
        slw.begin_input(self.win, "save")
        self.win._sl_input["text"] = "Hunter"
        slw.commit_input(self.win)
        self.reply()
        slw.tick(self.win)
        self.assertEqual(sorted(d["name"] for d in self.win._sl_store.layouts), ["Hunter (2)", "hunter"])

    def test_the_saved_layout_is_the_ship_as_it_is_now(self):
        self.win._inventory = [weapon(0, "kt", "King's Thumper")]
        slw.begin_save(self.win, "kt only")
        self.reply()
        slw.tick(self.win)
        saved = self.win._sl_store.layouts[0]
        self.assertEqual([w["item_type"] for w in saved["weapons"]], ["kt"])
        self.assertEqual(saved["saved_by"], "mk")
        self.assertEqual(saved["ship"]["ship_type"], "panstar")
        self.assertEqual(saved["saved_at"], 1_700_000_000.0)

    def test_saving_while_docked_warns_that_the_speed_figures_read_high(self):
        self.win._ship_specs_data = specs(state="docked")
        slw.begin_save(self.win, "docked save")
        self.reply_with(specs(state="docked"))
        slw.tick(self.win)
        self.assertEqual(self.win._sl_store.layouts[0]["specs_state"], "docked")
        text, kind, _until = self.win._sl_status
        self.assertEqual(kind, "warn")
        self.assertIn("while docked", text)
        self.assertIn("Save in space", text)

    def test_a_save_that_got_no_answer_does_not_blame_the_docked_state(self):
        self.win._ship_specs_data = specs(state="docked")                       # stale specs from an earlier moment
        slw.begin_save(self.win, "no answer")
        self.clock.now += slw.SPECS_WAIT_S + 0.1
        slw.tick(self.win)
        text, kind, _until = self.win._sl_status
        self.assertIn("without fresh stats", text)
        self.assertNotIn("while docked", text)

    def test_saving_in_space_has_no_warning(self):
        slw.begin_save(self.win, "flying save")
        self.reply_with(specs(state="in_space"))
        slw.tick(self.win)
        self.assertEqual(self.win._sl_status, ("Saved 'flying save'.", "ok", self.win._sl_status[2]))

    def reply_with(self, data):
        self.clock.now += 0.2
        self.win.set_ship_specs_data(data)

    def test_a_save_logs_both_readings_of_the_hull_for_the_next_investigation(self):
        self.win._inventory = self.win._inventory + [cargo_summary()]
        slw.begin_save(self.win, "logged")
        self.reply_with(specs(state="docked", used=1466, equipped=1453, hold=13))
        with self.assertLogs("ship_layouts_window", level="INFO") as logged:
            slw.tick(self.win)
        line = next(m for m in logged.output if "saved 'logged'" in m)
        for part in ("(docked)", "specs cargo 1466/667", "gear 1453", "inventory summary 3650/6810", "gear 3637"):
            self.assertIn(part, line)

    def test_the_layout_reaches_the_disk(self):
        slw.begin_save(self.win, "on disk")
        self.reply()
        slw.tick(self.win)
        self.assertEqual([d["name"] for d in sl.read_all(self.win._sl_store.directory)], ["on disk"])

    def test_update_replaces_the_selected_layout_keeping_its_id_and_name(self):
        old = self.add_layout(name="hunter", now=5.0)
        self.win._sl_selected = old["id"]
        self.win._inventory = [weapon(0, "x", "Xenon")]
        slw.begin_update(self.win)
        self.assertEqual(self.win.sent, ["SHIP_SPECS"])
        self.reply()
        slw.tick(self.win)
        layouts = self.win._sl_store.layouts
        self.assertEqual(len(layouts), 1)
        self.assertEqual((layouts[0]["id"], layouts[0]["name"]), (old["id"], "hunter"))
        self.assertEqual([w["item_type"] for w in layouts[0]["weapons"]], ["x"])
        self.assertEqual(self.win._sl_status[0], "Updated 'hunter'.")

    def test_update_does_nothing_without_a_selection_or_while_a_save_is_waiting(self):
        slw.begin_update(self.win)
        self.assertIsNone(self.win._sl_pending)
        layout = self.add_layout()
        self.win._sl_selected = layout["id"]
        slw.begin_save(self.win, "first")
        pending = self.win._sl_pending
        slw.begin_update(self.win)
        self.assertIs(self.win._sl_pending, pending)

    def test_the_default_name_fits_the_limit_even_for_a_long_ship_name(self):
        self.win._ship_specs_data = specs("big", "x" * 60)
        self.win._sl_specs_count += 1
        slw.begin_input(self.win, "save")
        self.assertLessEqual(len(self.win._sl_input["text"]), sl.MAX_NAME_LEN)
        self.assertEqual(self.field().cursor, len(self.win._sl_input["text"]))

    def test_opening_the_name_field_disarms_a_waiting_delete(self):
        layout = self.add_layout()
        self.win._sl_selected = layout["id"]
        slw.delete_selected(self.win)
        self.assertIsNotNone(self.win._sl_confirm_delete)
        slw.begin_input(self.win, "save")
        self.assertIsNone(self.win._sl_confirm_delete)

    def test_a_delete_armed_for_one_layout_does_not_delete_another_on_the_first_click(self):
        a = self.add_layout(name="a", now=1.0)
        b = self.add_layout(name="b", now=2.0)
        self.win._sl_selected = a["id"]
        slw.delete_selected(self.win)
        self.win._sl_selected = b["id"]                                         # the selection moved some other way
        slw.delete_selected(self.win)
        self.assertIsNotNone(self.win._sl_store.get(b["id"]))
        self.assertEqual(self.win._sl_confirm_delete[0], b["id"])

    def test_rename_changes_only_the_name(self):
        layout = self.add_layout(name="old name")
        self.win._sl_selected = layout["id"]
        slw.begin_input(self.win, "rename")
        self.assertEqual(self.win._sl_input["text"], "old name")
        self.win._sl_input["text"] = "new name"
        slw.commit_input(self.win)
        renamed = self.win._sl_store.get(layout["id"])
        self.assertEqual(renamed["name"], "new name")
        self.assertEqual(renamed["weapons"], layout["weapons"])
        self.assertEqual(self.win.sent, [])                                  # no specs needed for a rename
        self.assertEqual(self.win._sl_status[0], "Renamed to 'new name'.")

    def test_rename_to_another_layouts_name_gets_a_number_but_to_its_own_does_not(self):
        a = self.add_layout(name="alpha", now=1.0)
        self.add_layout(name="beta", now=2.0)
        self.win._sl_selected = a["id"]
        slw.begin_input(self.win, "rename")
        self.win._sl_input["text"] = "BETA"
        slw.commit_input(self.win)
        self.assertEqual(self.win._sl_store.get(a["id"])["name"], "BETA (2)")
        slw.begin_input(self.win, "rename")
        self.win._sl_input["text"] = "beta (2)"
        slw.commit_input(self.win)
        self.assertEqual(self.win._sl_store.get(a["id"])["name"], "beta (2)")

    def test_rename_needs_a_selection(self):
        slw.begin_input(self.win, "rename")
        self.assertIsNone(self.win._sl_input)

    def test_delete_needs_two_clicks_within_the_time(self):
        layout = self.add_layout()
        keep = self.add_layout(name="keep", now=0.5)
        self.win._sl_selected = layout["id"]
        slw.delete_selected(self.win)
        self.assertIsNotNone(self.win._sl_store.get(layout["id"]))
        self.assertEqual(self.win._sl_confirm_delete[0], layout["id"])
        slw.delete_selected(self.win)
        self.assertIsNone(self.win._sl_store.get(layout["id"]))
        self.assertEqual(self.win._sl_selected, keep["id"])
        self.assertEqual(os.listdir(self.win._sl_store.directory), [f"{keep['id']}.json"])
        self.assertIn("Deleted", self.win._sl_status[0])

    def test_a_late_second_click_only_re_arms(self):
        layout = self.add_layout()
        self.win._sl_selected = layout["id"]
        slw.delete_selected(self.win)
        self.clock.now += slw.CONFIRM_S + 0.5
        slw.delete_selected(self.win)
        self.assertIsNotNone(self.win._sl_store.get(layout["id"]))
        self.assertEqual(self.win._sl_confirm_delete[0], layout["id"])

    def test_the_armed_delete_expires_and_does_not_carry_to_another_layout(self):
        a = self.add_layout(name="a", now=1.0)
        b = self.add_layout(name="b", now=2.0)
        self.win._sl_selected = a["id"]
        slw.delete_selected(self.win)
        slw.select(self.win, b["id"])
        self.assertIsNone(self.win._sl_confirm_delete)
        slw.delete_selected(self.win)
        slw.delete_selected(self.win)
        self.assertIsNone(self.win._sl_store.get(b["id"]))
        self.assertIsNotNone(self.win._sl_store.get(a["id"]))
        slw.delete_selected(self.win)
        self.clock.now += slw.CONFIRM_S + 1
        slw.tick(self.win)
        self.assertIsNone(self.win._sl_confirm_delete)

    def test_deleting_the_last_layout_leaves_nothing_selected(self):
        layout = self.add_layout()
        self.win._sl_selected = layout["id"]
        slw.delete_selected(self.win)
        slw.delete_selected(self.win)
        self.assertIsNone(self.win._sl_selected)

    def test_selecting_remembers_and_resets_the_scroll(self):
        a, b = self.add_layout(name="a", now=1.0), self.add_layout(name="b", now=2.0)
        self.win._sl_selected = a["id"]
        self.win._sl_scroll_cmp = 200
        slw.select(self.win, b["id"])
        self.assertEqual((self.win._sl_selected, self.win._sl_scroll_cmp), (b["id"], 0))
        scan_writer.flush()
        with open(slw._path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["selected"], b["id"])

    def test_matching_toggle_is_remembered(self):
        slw.toggle_matching(self.win)
        self.assertTrue(self.win._sl_show_matching)
        scan_writer.flush()
        self.assertTrue(slw.load(slw._path)["show_matching"])

    def test_the_specs_are_polled_while_a_layout_is_selected(self):
        layout = self.add_layout()
        self.win._sl_selected = layout["id"]
        self.win.sent.clear()
        slw.tick(self.win)
        self.assertEqual(self.win.sent, [])
        self.clock.now += slw.SPECS_POLL_S + 0.1
        slw.tick(self.win)
        self.assertEqual(self.win.sent, ["SHIP_SPECS"])
        slw.tick(self.win)
        self.assertEqual(self.win.sent, ["SHIP_SPECS"])                      # not again straight away

    def test_nothing_is_polled_without_a_selection(self):
        self.win.sent.clear()
        self.clock.now += 60
        slw.tick(self.win)
        self.assertEqual(self.win.sent, [])

    def test_the_folder_is_re_read_now_and_then(self):
        refreshes = []
        self.win._sl_store.refresh = lambda: refreshes.append(1)
        slw.tick(self.win)
        self.assertEqual(refreshes, [])
        self.clock.now += slw.REFRESH_S + 1
        slw.tick(self.win)
        self.assertEqual(refreshes, [1])

    def test_a_status_message_fades(self):
        slw._say(self.win, "hello")
        slw.tick(self.win)
        self.assertIsNotNone(self.win._sl_status)
        self.clock.now += slw.STATUS_S + 1
        slw.tick(self.win)
        self.assertIsNone(self.win._sl_status)


# ── typing ───────────────────────────────────────────────────────────────────

class TypingTests(Base):
    def setUp(self):
        super().setUp()
        slw.open_window(self.win)
        slw.begin_input(self.win, "save")
        self.win._sl_input["text"] = ""   # the box's TextField adopts the new text on the next key (caret at its end)

    def text(self):
        return self.win._sl_input["text"]

    def test_keys_are_taken_while_typing_and_build_the_name(self):
        self.assertEqual(self.type_text("Hunter 2"), [])
        self.assertEqual(self.text(), "Hunter 2")

    def test_backspace_delete_and_the_cursor_keys(self):
        self.type_text("abcd")
        self.key(pygame.K_BACKSPACE)
        self.assertEqual(self.text(), "abc")
        self.key(pygame.K_LEFT)
        self.key(pygame.K_LEFT)
        self.type_text("X")
        self.assertEqual(self.text(), "aXbc")
        self.key(pygame.K_DELETE)
        self.assertEqual(self.text(), "aXc")
        self.key(pygame.K_HOME)
        self.key(pygame.K_BACKSPACE)                                         # nothing before the start
        self.type_text("^")
        self.assertEqual(self.text(), "^aXc")
        self.key(pygame.K_END)
        self.type_text("!")
        self.assertEqual(self.text(), "^aXc!")
        self.key(pygame.K_RIGHT)
        self.assertEqual(self.field().cursor, len(self.text()))

    def test_the_cursor_stops_at_the_start(self):
        self.type_text("abc")
        self.key(pygame.K_HOME)
        self.key(pygame.K_LEFT)
        self.assertEqual(self.field().cursor, 0)
        self.type_text("X")
        self.assertEqual(self.text(), "Xabc")

    def test_the_name_has_a_length_limit(self):
        self.type_text("x" * (sl.MAX_NAME_LEN + 10))
        self.assertEqual(len(self.text()), sl.MAX_NAME_LEN)

    def test_ctrl_a_selects_everything_and_typing_replaces_it(self):
        self.type_text("old name")
        self.key(pygame.K_a, mod=pygame.KMOD_CTRL)
        self.assertEqual(self.field().selection, (0, len("old name")))
        self.type_text("N")
        self.assertEqual(self.text(), "N")
        self.assertIsNone(self.field().selection)

    def test_shift_arrows_select_and_backspace_removes_the_selection(self):
        self.type_text("abcd")
        self.key(pygame.K_LEFT, mod=pygame.KMOD_SHIFT)
        self.key(pygame.K_LEFT, mod=pygame.KMOD_SHIFT)
        self.assertEqual(self.field().selected_text, "cd")
        self.key(pygame.K_BACKSPACE)
        self.assertEqual(self.text(), "ab")

    def test_copy_and_paste_use_the_clipboard_and_the_length_limit_still_holds(self):
        self.type_text("hunter")
        self.key(pygame.K_a, mod=pygame.KMOD_CTRL)
        self.key(pygame.K_c, mod=pygame.KMOD_CTRL)
        self.assertEqual(self.win.clipboard, "hunter")
        self.key(pygame.K_END)
        self.win.clipboard = "x" * (sl.MAX_NAME_LEN + 10)
        self.key(pygame.K_v, mod=pygame.KMOD_CTRL)
        self.assertEqual(len(self.text()), sl.MAX_NAME_LEN)
        self.assertTrue(self.text().startswith("hunter"))

    def test_a_new_opening_starts_with_a_fresh_box(self):
        self.type_text("abc")
        self.key(pygame.K_a, mod=pygame.KMOD_CTRL)
        self.assertIsNotNone(self.field().selection)
        slw.cancel_input(self.win)
        slw.begin_input(self.win, "save")
        self.assertIsNone(self.field().selection)
        self.assertEqual(self.field().cursor, len(self.text()))

    def test_control_characters_and_ctrl_combinations_type_nothing(self):
        self.key(pygame.K_TAB)
        self.assertEqual(self.text(), "")
        slw.filter_events(self.win, [pygame.event.Event(pygame.KEYDOWN, key=ord("v"), mod=pygame.KMOD_CTRL,
                                                        unicode="v")])
        slw.filter_events(self.win, [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a, mod=0, unicode="\x7f")])
        self.assertEqual(self.text(), "")

    def test_enter_commits_and_escape_cancels(self):
        self.type_text("Name")
        self.assertEqual(self.key(pygame.K_RETURN), [])
        self.assertIsNone(self.win._sl_input)
        self.assertIsNotNone(self.win._sl_pending)
        self.win._sl_pending = None
        slw.begin_input(self.win, "save")
        self.assertEqual(self.key(pygame.K_ESCAPE), [])
        self.assertIsNone(self.win._sl_input)
        self.assertIsNone(self.win._sl_pending)

    def test_keypad_enter_commits_too(self):
        self.type_text("Name")
        self.key(pygame.K_KP_ENTER)
        self.assertIsNotNone(self.win._sl_pending)

    def test_key_releases_are_taken_too_so_nothing_half_handled_reaches_the_game(self):
        up = pygame.event.Event(pygame.KEYUP, key=pygame.K_w, mod=0)
        self.assertEqual(slw.filter_events(self.win, [up]), [])

    def test_every_key_is_taken_while_typing_even_ones_the_field_ignores(self):
        for key in (pygame.K_F5, pygame.K_UP, pygame.K_w, pygame.K_SPACE):
            event = pygame.event.Event(pygame.KEYDOWN, key=key, mod=0, unicode="")
            self.assertEqual(slw.filter_events(self.win, [event]), [], key)

    def test_keys_belong_to_the_game_when_nothing_is_being_typed(self):
        self.win._sl_input = None
        event = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_w, mod=0, unicode="w")
        self.assertEqual(slw.filter_events(self.win, [event]), [event])

    def test_a_long_name_still_draws_with_the_caret_in_view(self):
        self.type_text("y" * sl.MAX_NAME_LEN)
        self.key(pygame.K_HOME)
        self.render()
        self.key(pygame.K_END)
        self.render()


# ── the mouse ────────────────────────────────────────────────────────────────

class MouseTests(Base):
    def setUp(self):
        super().setUp()
        self.a = self.add_layout(name="alpha", now=2.0)
        self.b = self.add_layout(name="beta", now=1.0)
        slw.open_window(self.win)
        self.win._sl_selected = self.a["id"]
        self.render()

    def test_the_window_is_drawn_and_publishes_what_it_drew(self):
        r = self.rects()
        self.assertEqual(len(self.win._sl_hits["rows"]), 2)
        self.assertEqual([lid for _rect, lid in self.win._sl_hits["rows"]], [self.a["id"], self.b["id"]])
        self.assertIn("toggle", self.win._sl_hits)
        self.assertEqual([key for _rect, key in self.win._sl_hits["headers"]], ["cargo"])
        self.assertTrue(self.win._sl_rect.contains(r["list"]))

    def test_clicks_inside_are_taken(self):
        for pos in (self.rects()["list"].center, self.rects()["compare"].center, self.rects()["header"].center):
            events = self.click(pos)
            self.assertEqual(events, [], pos)

    def test_clicks_outside_go_to_the_game(self):
        outside = (5, 700)
        self.assertFalse(self.win._sl_rect.collidepoint(outside))
        events = self.click(outside)
        self.assertEqual(len(events), 2)

    def test_right_and_middle_clicks_inside_are_taken_but_do_nothing(self):
        before = self.win._sl_selected
        for button in (2, 3):
            self.assertEqual(self.click(self.rects()["list"].center, button=button)[:0], [])
        down = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=self.win._sl_hits["rows"][1][0].center, button=3)
        self.assertEqual(slw.filter_events(self.win, [down]), [])
        self.assertEqual(self.win._sl_selected, before)

    def test_clicking_a_list_row_selects_it(self):
        row = self.win._sl_hits["rows"][1][0]
        self.click(row.center)
        self.assertEqual(self.win._sl_selected, self.b["id"])

    def test_the_close_button_closes(self):
        self.click(self.rects()["close"].center)
        self.assertFalse(slw.is_open(self.win))

    def test_save_opens_the_name_field_and_ok_confirms_cancel_cancels(self):
        self.click(self.rects()["save"].center)
        self.assertIsNotNone(self.win._sl_input)
        self.render()
        self.click(self.rects()["cancel"].center)
        self.assertIsNone(self.win._sl_input)
        self.click(self.rects()["save"].center)
        self.render()
        self.click(self.rects()["ok"].center)
        self.assertIsNotNone(self.win._sl_pending)

    def test_clicking_the_field_puts_the_caret_where_the_press_landed(self):
        self.click(self.rects()["save"].center)
        self.render()
        field = self.field()
        text = self.win._sl_input["text"]
        self.assertEqual(field.cursor, len(text))
        view = field.view
        self.assertIsNotNone(view)                      # the draw published where the text is
        near_start = (view.rect.left + 1, view.rect.centery)
        self.click(near_start)
        self.assertEqual(field.cursor, 0)               # not the end any more: the caret follows the press
        self.click((view.rect.right - 1, view.rect.centery))
        self.assertEqual(field.cursor, len(text))
        box = self.rects()["field"]
        self.click((box.left + 2, box.centery))         # the padding left of the text is part of the clickable box
        self.assertEqual(field.cursor, 0)
        self.assertEqual(self.win._sl_input["text"], text)   # a click never edits

    def test_a_drag_across_the_box_selects_and_the_release_over_the_window_ends_it(self):
        self.click(self.rects()["save"].center)
        self.render()
        field = self.field()
        view = field.view
        y = view.rect.centery
        with mock.patch("pygame.mouse.get_pos", return_value=(view.rect.left + 1, y)):
            self.assertEqual(slw.filter_events(self.win, [pygame.event.Event(
                pygame.MOUSEBUTTONDOWN, pos=(view.rect.left + 1, y), button=1)]), [])
        self.assertTrue(field.dragging)
        self.win._text_field_drag_event(pygame.event.Event(
            pygame.MOUSEMOTION, pos=(view.rect.right - 1, y), rel=(0, 0), buttons=(1, 0, 0)))
        self.assertEqual(field.selection, (0, len(self.win._sl_input["text"])))
        with mock.patch("pygame.mouse.get_pos", return_value=(view.rect.right - 1, y)):
            self.assertEqual(slw.filter_events(self.win, [pygame.event.Event(
                pygame.MOUSEBUTTONUP, pos=(view.rect.right - 1, y), button=1)]), [])
        self.assertFalse(field.dragging)                # the swallowed release still ended the drag
        self.assertIsNotNone(field.selection)           # and the selection is kept for typing over

    def test_other_controls_do_nothing_while_the_name_field_is_open(self):
        self.click(self.rects()["save"].center)
        self.render()
        row = self.win._sl_hits["rows"][1][0]
        self.click(row.center)
        self.click(self.rects()["delete"].center)
        self.assertEqual(self.win._sl_selected, self.a["id"])
        self.assertIsNone(self.win._sl_confirm_delete)
        self.assertIsNotNone(self.win._sl_input)

    def test_update_rename_and_delete_buttons(self):
        self.click(self.rects()["rename"].center)
        self.assertEqual(self.win._sl_input["mode"], "rename")
        slw.cancel_input(self.win)
        self.click(self.rects()["update"].center)
        self.assertIsNotNone(self.win._sl_pending)
        self.win._sl_pending = None
        self.click(self.rects()["delete"].center)
        self.assertIsNotNone(self.win._sl_confirm_delete)
        self.assertIsNotNone(self.win._sl_store.get(self.a["id"]))
        self.click(self.rects()["delete"].center)
        self.assertIsNone(self.win._sl_store.get(self.a["id"]))

    def test_rename_and_delete_wait_while_a_save_is_pending(self):
        self.win._sl_pending = {"name": "x", "target": None, "since": time_now_monotonic(), "count": 0}
        for key in ("update", "rename", "delete"):
            self.click(self.rects()[key].center)
        self.assertIsNone(self.win._sl_input)
        self.assertIsNone(self.win._sl_confirm_delete)
        self.assertIs(self.win._sl_pending["name"], "x")

    def test_a_release_without_motion_still_moves_the_window_to_where_it_landed(self):
        start = pygame.Rect(self.win._sl_rect)
        grab = (self.rects()["header"].centerx - 80, self.rects()["header"].centery)
        events = [pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=grab, button=1),
                  pygame.event.Event(pygame.MOUSEBUTTONUP, pos=(grab[0] - 30, grab[1] + 20), button=1)]
        slw.filter_events(self.win, events)
        self.assertEqual(self.win._sl_rect.topleft, (start.x - 30, start.y + 20))

    def test_the_wheel_over_the_rest_of_the_window_is_taken_too(self):
        wheel = pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-1)
        with mock.patch("pygame.mouse.get_pos", return_value=self.rects()["header"].center):
            self.assertEqual(slw.filter_events(self.win, [wheel]), [])

    def test_the_buttons_do_nothing_without_a_selection_or_while_saving(self):
        self.win._sl_selected = None
        self.win._sl_store.layouts = ()
        self.render()
        for key in ("update", "rename", "delete"):
            self.click(self.rects()[key].center)
        self.assertIsNone(self.win._sl_input)
        self.assertIsNone(self.win._sl_pending)
        self.assertIsNone(self.win._sl_confirm_delete)
        self.win._sl_pending = {"name": "x", "target": None, "since": time_now_monotonic(), "count": 0}
        self.click(self.rects()["save"].center)
        self.assertIsNone(self.win._sl_input)

    def test_show_matching_and_the_cargo_header_toggle(self):
        self.click(self.win._sl_hits["toggle"].center)
        self.assertTrue(self.win._sl_show_matching)
        self.render()
        header = self.win._sl_hits["headers"][0][0]
        self.click(header.center)
        self.assertTrue(self.win._sl_cargo_open)
        self.click(header.center)
        self.assertFalse(self.win._sl_cargo_open)

    def test_the_wheel_scrolls_the_pane_under_the_pointer_and_stops_at_the_ends(self):
        for i in range(30):
            self.add_layout(name=f"layout {i}", now=10.0 + i)
        self.win._sl_show_matching = True
        self.render()
        wheel = pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-1)
        with mock.patch("pygame.mouse.get_pos", return_value=self.rects()["list"].center):
            self.assertEqual(slw.filter_events(self.win, [wheel]), [])
            slw.filter_events(self.win, [pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-1)] * 500)
        self.assertLessEqual(self.win._sl_scroll_list, self.win._sl_max_scroll_list)      # clamped at once
        self.assertGreater(self.win._sl_scroll_list, 0)
        self.assertEqual(self.win._sl_scroll_cmp, 0)
        with mock.patch("pygame.mouse.get_pos", return_value=self.rects()["compare"].center):
            slw.filter_events(self.win, [pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-1)] * 40)
        self.assertGreater(self.win._sl_scroll_cmp, 0)
        self.render()
        self.assertLessEqual(self.win._sl_scroll_cmp, self.win._sl_max_scroll_cmp)
        with mock.patch("pygame.mouse.get_pos", return_value=self.rects()["compare"].center):
            slw.filter_events(self.win, [pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=1)] * 200)
        self.assertEqual(self.win._sl_scroll_cmp, 0)

    def test_the_wheel_over_the_game_is_left_alone(self):
        wheel = pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-1)
        with mock.patch("pygame.mouse.get_pos", return_value=(5, 700)):
            self.assertEqual(slw.filter_events(self.win, [wheel]), [wheel])

    def test_clicking_the_window_raises_it_above_the_windows_it_was_under(self):
        manager = self.win._gui_windows
        manager.register("other", pygame.Rect(0, 0, 100, 100), layer=GuiLayer.WINDOW)    # registered later: in front
        self.assertGreater(manager._windows["other"].z, manager._windows[slw.ID].z)
        self.click(self.rects()["list"].center)
        self.assertGreater(manager._windows[slw.ID].z, manager._windows["other"].z)

    def test_a_window_in_front_of_ours_gets_the_click(self):
        pos = self.rects()["list"].center
        self.win._gui_windows.register("in_front", pygame.Rect(pos[0] - 5, pos[1] - 5, 10, 10), layer=GuiLayer.SYSTEM)
        self.win._gui_windows._seen.add("in_front")
        events = self.click(pos)
        self.assertEqual(len(events), 2)
        self.assertEqual(self.win._sl_selected, self.a["id"])

    def test_header_drag_moves_the_window_and_remembers_it(self):
        start = pygame.Rect(self.win._sl_rect)
        grab = (self.rects()["header"].centerx - 80, self.rects()["header"].centery)
        events = [pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=grab, button=1),
                  pygame.event.Event(pygame.MOUSEMOTION, pos=(grab[0] - 60, grab[1] + 40), rel=(0, 0),
                                     buttons=(1, 0, 0)),
                  pygame.event.Event(pygame.MOUSEBUTTONUP, pos=(grab[0] - 60, grab[1] + 40), button=1)]
        with mock.patch("pygame.mouse.get_pos", return_value=grab):
            self.assertEqual(slw.filter_events(self.win, events), [])
        self.assertEqual(self.win._sl_rect, pygame.Rect(start.x - 60, start.y + 40, start.w, start.h))
        self.assertIsNone(self.win._sl_drag)
        scan_writer.flush()
        self.assertEqual(slw.load(slw._path)["rect"], list(self.win._sl_rect))

    def test_dragging_cannot_take_the_window_off_screen(self):
        grab = (self.rects()["header"].centerx - 80, self.rects()["header"].centery)
        events = [pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=grab, button=1),
                  pygame.event.Event(pygame.MOUSEMOTION, pos=(9000, 9000), rel=(0, 0), buttons=(1, 0, 0))]
        slw.filter_events(self.win, events)
        self.assertEqual(self.win._sl_rect.bottomright, SCREEN)

    def test_the_close_button_is_not_a_drag_handle(self):
        self.click(self.rects()["close"].center)
        self.assertIsNone(self.win._sl_drag)

    def test_corner_drag_resizes_down_to_the_minimum(self):
        corner = (self.win._sl_rect.right - 3, self.win._sl_rect.bottom - 3)
        events = [pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=corner, button=1),
                  pygame.event.Event(pygame.MOUSEMOTION, pos=(corner[0] - 2000, corner[1] - 2000), rel=(0, 0),
                                     buttons=(1, 0, 0)),
                  pygame.event.Event(pygame.MOUSEBUTTONUP, pos=(corner[0] - 2000, corner[1] - 2000), button=1)]
        slw.filter_events(self.win, events)
        self.assertEqual((self.win._sl_rect.w, self.win._sl_rect.h), slw.min_size(self.win))

    def test_a_motion_after_the_button_was_released_elsewhere_ends_the_drag(self):
        grab = (self.rects()["header"].centerx - 80, self.rects()["header"].centery)
        slw.filter_events(self.win, [pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=grab, button=1)])
        self.assertIsNotNone(self.win._sl_drag)
        motion = pygame.event.Event(pygame.MOUSEMOTION, pos=(10, 10), rel=(0, 0), buttons=(0, 0, 0))
        self.assertEqual(slw.filter_events(self.win, [motion]), [motion])
        self.assertIsNone(self.win._sl_drag)

    def test_a_second_press_during_a_drag_does_not_restart_it(self):
        grab = (self.rects()["header"].centerx - 80, self.rects()["header"].centery)
        slw.filter_events(self.win, [pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=grab, button=1)])
        origin = self.win._sl_drag["origin"]
        slw.filter_events(self.win, [pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(grab[0] + 9, grab[1]), button=1)])
        self.assertEqual(self.win._sl_drag["origin"], origin)

    def test_events_pass_when_the_window_is_closed(self):
        slw.close_window(self.win)
        events = [pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(100, 100), button=1),
                  pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a, mod=0, unicode="a")]
        self.assertIs(slw.filter_events(self.win, events), events)

    def test_nothing_taken_returns_the_same_list(self):
        events = [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a, mod=0, unicode="a")]
        self.assertIs(slw.filter_events(self.win, events), events)


# ── hooks ────────────────────────────────────────────────────────────────────

class HookTests(Base):
    def test_the_window_joins_the_retained_windows_while_open(self):
        self.render()
        self.assertNotIn(slw.ID, self.win._gui_windows._windows)             # closed: nothing queued
        slw.open_window(self.win)
        self.render()
        window = self.win._gui_windows._windows[slw.ID]
        self.assertEqual((window.layer, window.modal, window.focusable), (int(GuiLayer.WINDOW), False, True))
        self.assertEqual(window.rect, self.win._sl_rect)

    def test_a_closed_window_does_nothing_per_frame_not_even_ask_the_server(self):
        clock = Clock(time_now_monotonic() + 1000)                                # later than anything polled so far
        layout = self.add_layout()
        slw.open_window(self.win)
        self.win._sl_selected = layout["id"]
        slw.close_window(self.win)
        self.win.sent.clear()
        with mock.patch.object(slw, "time", clock):
            clock.now += 60
            self.render()
            clock.now += 60
            self.render()
        self.assertEqual(self.win.sent, [])
        self.assertNotIn(slw.ID, self.win._gui_windows._windows)

    def test_it_owns_the_pointer_inside_only(self):
        slw.open_window(self.win)
        self.render()
        manager = self.win._gui_windows
        self.assertEqual(manager.route_pointer(self.win._sl_rect.center).owner, slw.ID)
        outside = manager.route_pointer((5, 700))
        self.assertIsNone(outside.owner)
        self.assertTrue(outside.allows("chat"))

    def test_closing_removes_it_at_once(self):
        slw.open_window(self.win)
        self.render()
        slw.close_window(self.win)
        self.assertEqual(self.win._gui_windows.route_pointer(self.win._sl_rect.center).owner, None)

    def test_ship_specs_answers_are_counted_for_windows_using_the_feature(self):
        before = self.win._sl_specs_count
        self.win.set_ship_specs_data(specs())
        self.assertEqual(self.win._sl_specs_count, before + 1)
        self.assertIsNotNone(self.win._ship_specs_data)                       # the original still ran
        other = Win()
        other.set_ship_specs_data(specs())
        self.assertNotIn("_sl_specs_count", other.__dict__)                   # a window that never used it

    def test_a_failing_original_does_not_break_the_count_hook(self):
        class Broken(FakeWindow):
            def set_ship_specs_data(self, data):
                raise RuntimeError("boom")
        slw.install(Broken, None, None)
        win = Broken()
        slw._ensure(win)
        with self.assertRaises(RuntimeError):
            win.set_ship_specs_data({})

    def test_install_twice_wraps_once(self):
        self.assertFalse(slw.install(Win, None, CLIENT_GLOBALS))
        self.assertFalse(hasattr(Win.set_ship_specs_data.__wrapped__, "__wrapped__"))

    def test_the_flight_gate_stays_quiet_while_a_name_is_typed(self):
        gate = CLIENT_GLOBALS["_flight_text_entry_active"]
        self.assertIs(gate.__wrapped__, _original_gate)
        self.assertFalse(gate(chat_focused=False))
        slw.open_window(self.win)
        slw.begin_input(self.win, "save")
        self.assertTrue(gate(chat_focused=False))
        slw.cancel_input(self.win)
        self.assertFalse(gate(chat_focused=False))

    def test_the_gate_still_answers_for_everything_else_and_passes_every_argument(self):
        gate = CLIENT_GLOBALS["_flight_text_entry_active"]
        GATE_CALLS.clear()
        self.assertTrue(gate(chat_focused=True, redeem_code_open=False, extra_new_flag=1))
        self.assertEqual(GATE_CALLS, [{"chat_focused": True, "redeem_code_open": False, "extra_new_flag": 1}])

    def test_a_closed_window_with_a_half_typed_name_does_not_hold_the_keys(self):
        gate = CLIENT_GLOBALS["_flight_text_entry_active"]
        slw.open_window(self.win)
        slw.begin_input(self.win, "save")
        slw.close_window(self.win)
        self.assertFalse(gate(chat_focused=False))

    def test_the_command_toggles_the_window(self):
        slw.handle_command(self.win, "/layouts")
        self.assertTrue(slw.is_open(self.win))
        slw.handle_command(self.win, "/layouts")
        self.assertFalse(slw.is_open(self.win))

    def test_the_kill_switch_turns_everything_off(self):
        slw.open_window(self.win)
        events = [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a, mod=0, unicode="a")]
        slw.begin_input(self.win, "save")
        with mock.patch.dict(os.environ, {slw.ENV_OFF: "1"}):
            self.assertFalse(slw.is_open(self.win))
            self.assertFalse(slw.text_focused(self.win))
            self.assertIs(slw.filter_events(self.win, events), events)
            self.render()
            self.assertNotIn(slw.ID, self.win._gui_windows._windows)
            slw.handle_command(self.win, "")
            self.assertIn("switched off", self.win.chat[-1])

    def test_an_error_switches_the_feature_off_for_the_session_not_the_game(self):
        slw.open_window(self.win)
        events = [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a, mod=0, unicode="a")]
        with mock.patch.object(slw, "_handle", side_effect=RuntimeError("boom")):
            with self.assertLogs("ship_layouts_window", level="ERROR"):
                self.assertIs(slw.filter_events(self.win, events), events)
        self.assertFalse(slw.is_open(self.win))

    def test_an_error_while_drawing_switches_it_off(self):
        slw.open_window(self.win)
        with mock.patch.object(slw, "_draw", side_effect=RuntimeError("boom")):
            with self.assertLogs("ship_layouts_window", level="ERROR"):
                self.render()
        self.assertFalse(slw.is_open(self.win))

    def test_an_error_while_queueing_switches_it_off(self):
        slw.open_window(self.win)
        with mock.patch.object(slw, "tick", side_effect=RuntimeError("boom")):
            with self.assertLogs("ship_layouts_window", level="ERROR"):
                self.render()
        self.assertTrue(slw._failed)

    def test_the_rectangle_and_selection_survive_a_restart(self):
        layout = self.add_layout()
        slw.open_window(self.win)
        self.win._sl_selected = layout["id"]
        self.win._sl_rect = pygame.Rect(33, 44, 700, 480)
        slw.toggle_matching(self.win)
        slw.close_window(self.win)
        scan_writer.flush()
        slw.load(slw._path)
        again = Win()
        slw._ensure(again)
        self.assertEqual(again._sl_rect, pygame.Rect(33, 44, 700, 480))
        self.assertEqual(again._sl_selected, layout["id"])
        self.assertTrue(again._sl_show_matching)

    def test_the_settings_file_is_read_defensively(self):
        for text in ("not json", "[1]", '{"rect": [1, 2, 3]}', '{"rect": [1, 2, 3, "4"]}', '{"rect": [true, 2, 3, 4]}',
                     '{"selected": 5, "show_matching": "yes"}'):
            with open(slw._path, "w", encoding="utf-8") as handle:
                handle.write(text)
            state = slw.load(slw._path)
            self.assertIsNone(state["rect"], text)
            self.assertIsNone(state["selected"], text)
            self.assertFalse(state["show_matching"], text)
        with open(slw._path, "w", encoding="utf-8") as handle:
            handle.write('{"rect": [1, 2, 600, 400], "selected": "abc", "show_matching": true}')
        self.assertEqual(slw.load(slw._path), {"rect": [1, 2, 600, 400], "selected": "abc", "show_matching": True})

    def test_a_remembered_rectangle_is_pulled_back_on_screen_and_to_the_minimum(self):
        self.win._sl_rect = pygame.Rect(1200, 700, 50, 50)
        rect = slw.window_rect(self.win, *SCREEN)
        self.assertTrue(pygame.Rect(0, 0, *SCREEN).contains(rect))
        self.assertGreaterEqual(rect.w, slw.min_size(self.win)[0])
        self.assertGreaterEqual(rect.h, slw.min_size(self.win)[1])

    def test_the_first_placement_is_centred_and_on_screen(self):
        rect = slw.window_rect(self.win, *SCREEN)
        self.assertTrue(pygame.Rect(0, 0, *SCREEN).contains(rect))
        self.assertLessEqual(abs(rect.centerx - SCREEN[0] // 2), 1)


# ── drawing ──────────────────────────────────────────────────────────────────

class DrawTests(Base):
    def setUp(self):
        super().setUp()
        self.layout = self.add_layout(name="hunter stealth", now=time_now() - 7200)
        self.win._sl_selected = self.layout["id"]

    def test_nothing_is_drawn_outside_the_window(self):
        slw.open_window(self.win)
        self.win._sl_rect = pygame.Rect(200, 100, 700, 480)
        screen = self.render()
        rect = self.win._sl_rect
        for x in range(0, SCREEN[0], 9):
            for y in range(0, SCREEN[1], 9):
                if not rect.inflate(2, 2).collidepoint((x, y)):
                    self.assertEqual(tuple(screen.get_at((x, y))), BACKDROP, (x, y))
        self.assertNotEqual(tuple(screen.get_at(rect.center)), BACKDROP)

    def test_it_draws_at_every_ui_scale_with_everything_inside(self):
        for scale in (1.0, 1.25, 1.5, 2.0):
            size = (int(1280 * scale), int(720 * scale))
            win = self.make_window(scale)
            win._sl_store.save(self.layout)
            win._sl_selected = self.layout["id"]
            slw.open_window(win)
            win._sl_rect = pygame.Rect(0, 0, 10, 10)                              # clamped up to the minimum
            with mock.patch("pygame.display.get_surface", return_value=pygame.Surface(size)):
                self.render(win, size)
            window = win._sl_rect
            for name, rect in win._sl_hits["rects"].items():
                self.assertTrue(window.contains(rect), (scale, name, rect, window))
            for rect, _id in win._sl_hits["rows"]:
                self.assertTrue(window.contains(rect), scale)

    def test_the_empty_states_draw(self):
        win = self.make_window()
        win._sl_store = sl.LayoutStore(os.path.join(self._tmp.name, "empty"), submit=sync_submit)
        slw.open_window(win)
        self.render(win)                                                          # no layouts, nothing selected
        self.assertEqual(win._sl_hits["rows"], [])
        self.assertNotIn("toggle", win._sl_hits)
        win._sl_store.loaded = False
        self.render(win)

    def test_the_mixed_state_note_is_drawn_and_the_summary_does_not_count_it_as_a_change(self):
        summary = cargo_summary()
        self.win._inventory = [weapon(0), weapon(1), gear("energy", "glorious_power", "Glorious Power"),
                               gear("shield", "stormsail", "Stormsail"),
                               gear("engine", "glorious_booster", "Glorious Booster"), plugin(0), plugin(1), summary]
        self.win._inventory_version += 1
        self.win._ship_specs_data = specs(state="docked", effective_mass=2664514.0, accel=474.0)
        self.win._sl_specs_count += 1
        saved = fit(inventory=self.win._inventory, specs=specs(state="in_space", effective_mass=3051514.0, accel=413.8),
                    turret_state=self.win._turret_state, name="in space save", now=time_now())
        self.win._sl_store.save(saved)
        self.win._sl_selected = saved["id"]
        slw.open_window(self.win)
        texts = []
        original = slw._text_surface
        with mock.patch.object(slw, "_text_surface",
                               lambda win, text, *a, **k: texts.append(text) or original(win, text, *a, **k)):
            self.win._sl_scroll_cmp = 10 ** 6
            self.render()
        self.assertTrue(any("Save in space" in t for t in texts), texts)
        self.assertIn("Matches your current ship.", texts)

    def test_every_state_of_the_compare_pane_draws(self):
        slw.open_window(self.win)
        self.win._sl_show_matching = True
        self.win._sl_cargo_open = True
        self.win._inventory = [weapon(0, "x", "Xenon"), cargo_item("kt", "KT", 2),
                               gear("solar_panel_0", "sp", "Solar", "solar_panel")]
        self.win._inventory_version += 1
        self.render()
        self.win._ship_specs_data = specs("oakheart", "Oakheart", visibility=80.0)
        self.win._sl_specs_count += 1
        self.render()                                                              # a different ship: warning line
        self.win._turret_state = None
        self.win._ship_specs_data = None
        self.win._inventory = []
        self.win._inventory_version += 1
        self.render()                                                              # nothing known about the ship
        self.win._sl_show_matching = False
        self.render()

    def test_the_matching_summary_and_a_different_ship_warning_are_drawn(self):
        slw.open_window(self.win)
        texts = []
        original = slw._text_surface

        def spy(win, text, *args, **kwargs):
            texts.append(text)
            return original(win, text, *args, **kwargs)

        with mock.patch.object(slw, "_text_surface", spy):
            self.render()
            self.assertIn("Matches your current ship.", texts)
            texts.clear()
            self.win._ship_specs_data = specs("oakheart", "Oakheart")
            self.win._sl_specs_count += 1
            self.render()
            self.assertTrue(any(t.startswith("Different ship: you are flying the Oakheart, this was saved on the "
                                             "Panstar") for t in texts), texts)
            texts.clear()
            self.win._inventory = [weapon(0, "x", "Xenon")]
            self.win._inventory_version += 1
            self.render()
            self.assertTrue(any("difference" in t for t in texts), texts)

    def test_the_list_row_shows_the_ship_and_headline_numbers(self):
        slw.open_window(self.win)
        texts = []
        original = slw._text_surface
        with mock.patch.object(slw, "_text_surface",
                               lambda win, text, *a, **k: texts.append(text) or original(win, text, *a, **k)):
            self.render()
        self.assertIn("hunter stealth", texts)
        self.assertIn(sl.summary_line(self.layout), texts)

    def test_status_and_store_errors_are_shown(self):
        slw.open_window(self.win)
        texts = []
        original = slw._text_surface
        with mock.patch.object(slw, "_text_surface",
                               lambda win, text, *a, **k: texts.append(text) or original(win, text, *a, **k)):
            slw._say(self.win, "Saved 'x'.", "ok")
            self.render()
            self.assertIn("Saved 'x'.", texts)
            self.win._sl_status = None
            self.win._sl_store.error = "could not save: disk full"
            self.render()
            self.assertIn("could not save: disk full", texts)

    def test_the_delete_button_says_sure_when_armed(self):
        slw.open_window(self.win)
        texts = []
        original = slw._text_surface
        with mock.patch.object(slw, "_text_surface",
                               lambda win, text, *a, **k: texts.append(text) or original(win, text, *a, **k)):
            self.render()
            self.assertIn("DELETE", texts)
            self.assertNotIn("SURE?", texts)
            slw.delete_selected(self.win)
            self.render()
            self.assertIn("SURE?", texts)

    def test_the_save_button_shows_it_is_working(self):
        slw.open_window(self.win)
        texts = []
        original = slw._text_surface
        with mock.patch.object(slw, "_text_surface",
                               lambda win, text, *a, **k: texts.append(text) or original(win, text, *a, **k)):
            self.win._sl_pending = {"name": "x", "target": None, "since": time_now_monotonic(), "count": 0}
            self.render()
        self.assertIn("SAVING...", texts)

    def test_many_layouts_and_a_long_comparison_scroll_and_draw(self):
        for i in range(40):
            self.add_layout(name=f"layout {i}", now=1.0 + i)
        slw.open_window(self.win)
        self.win._sl_show_matching = True
        self.win._sl_cargo_open = True
        self.render()
        self.assertGreater(self.win._sl_max_scroll_list, 0)
        self.win._sl_scroll_list = 10 ** 6
        self.win._sl_scroll_cmp = 10 ** 6
        self.render()
        self.assertEqual(self.win._sl_scroll_list, self.win._sl_max_scroll_list)
        self.assertEqual(self.win._sl_scroll_cmp, self.win._sl_max_scroll_cmp)

    def test_the_gpu_style_screen_draws_too(self):
        class Gpu(pygame.Surface):
            def fork(self, size):
                return pygame.Surface(size, pygame.SRCALPHA)

        slw.open_window(self.win)
        screen = Gpu(SCREEN, pygame.SRCALPHA)
        screen.fill(BACKDROP)
        with mock.patch("pygame.mouse.get_pos", return_value=(5, 5)):
            slw.draw(self.win, screen)
        self.assertNotEqual(tuple(screen.get_at(self.win._sl_rect.center)), BACKDROP)

    def test_text_is_cached_and_the_cache_is_bounded(self):
        slw.open_window(self.win)
        first = slw._text_surface(self.win, "abc", 10, (1, 2, 3))
        self.assertIs(slw._text_surface(self.win, "abc", 10, (1, 2, 3)), first)
        for i in range(700):
            slw._text_surface(self.win, f"t{i}", 10, (1, 2, 3))
        self.assertLessEqual(len(self.win._sl_text_cache), 600)

    def test_fitting_is_remembered_so_a_frame_does_not_measure_text_again(self):
        slw.open_window(self.win)
        text = "a very long layout name that cannot possibly fit in forty pixels"
        first = slw._fit(self.win, text, 10, 40)
        with mock.patch.object(self.win, "_instrument_font", side_effect=AssertionError("measured again")):
            self.assertEqual(slw._fit(self.win, text, 10, 40), first)
        for i in range(2100):
            slw._fit(self.win, f"{i}", 10, 5)
        self.assertLessEqual(len(self.win._sl_fit_cache), 2000)

    def test_fitting_text(self):
        slw.open_window(self.win)
        self.assertEqual(slw._fit(self.win, "short", 10, 500), "short")
        long_text = "a very long layout name that cannot possibly fit in forty pixels"
        fitted = slw._fit(self.win, long_text, 10, 40)
        self.assertTrue(fitted.endswith("...") and len(fitted) < len(long_text))
        self.assertLessEqual(self.win._instrument_font(10, bold=True).size(fitted)[0], 40)
        self.assertEqual(slw._fit(self.win, "x", 10, 0), "")
        self.assertEqual(slw._fit(self.win, "wide text", 10, 3), "")


def time_now():
    import time
    return time.time()


def time_now_monotonic():
    import time
    return time.monotonic()


# ── the vanilla lines and the real window ────────────────────────────────────

class VanillaHookTests(unittest.TestCase):
    def source(self, name):
        return modloader.game_source(name)

    def test_client_py_asks_the_module(self):
        source = self.source("Client.py")
        self.assertEqual(source.count("import ship_layouts_window"), 1)
        self.assertEqual(source.count("ship_layouts_window.filter_events(self, _frame_events)"), 1)
        self.assertEqual(source.count("ship_layouts_window.queue(self, _frame)"), 1)
        self.assertEqual(source.count("ship_layouts_window.handle_command(self, _msg)"), 1)
        self.assertEqual(source.count("ship_layouts_window.toggle(self)"), 1)
        self.assertEqual(source.count("ship_layouts_window.is_open(self)"), 1)
        self.assertEqual(source.count("ship_layouts_window.install(SolarSystemWindow, _render_mixin_module, globals())"), 1)

    def test_the_event_filter_runs_before_the_events_are_used(self):
        source = self.source("Client.py")
        self.assertLess(source.index("pump_watch.get_events()"),
                        source.index("ship_layouts_window.filter_events(self, _frame_events)"))
        # the game's loop over them reads `for event in _frame_events:` (v0.5.84) or `enumerate(_frame_events)` (v0.5.86)
        loop = min(i for i in (source.find("for event in _frame_events:"),
                               source.find("for _index, event in enumerate(_frame_events):")) if i >= 0)
        self.assertLess(source.index("ship_layouts_window.filter_events(self, _frame_events)"), loop)

    def test_the_window_is_queued_before_the_retained_windows_are_flushed(self):
        source = self.source("Client.py")
        queued = source.index("ship_layouts_window.queue(self, _frame)")
        flush = source.index("_managed_draw_results = self._gui_windows.flush_draw_queue(")
        self.assertLess(queued, flush)
        self.assertLess(flush - queued, 300)                      # right next to the flush, not somewhere far above

    def test_the_command_has_its_own_branch(self):
        source = self.source("Client.py")
        self.assertIn('elif _cmd in ("layouts", "layout"):', source)

    def test_the_menu_row_has_its_rect_and_click_handler(self):
        source = self.source("Client.py")
        self.assertIn('("layouts", "SHIP LAYOUTS", ship_layouts_window.is_open(self), "", False)', source)
        self.assertEqual(source.count('self._menu_layouts_rect = item_rects["layouts"]'), 1)
        self.assertEqual(source.count("self._menu_layouts_rect = None"), 1)
        self.assertEqual(source.count("self._menu_layouts_rect.collidepoint(event.pos)"), 1)


class RealWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode(SCREEN)

    def setUp(self):
        import Client
        self.Client = Client
        slw.reset_for_tests()

    def test_the_hooks_are_installed_on_the_real_window(self):
        self.assertTrue(self.Client.SolarSystemWindow.__dict__.get("_ship_layouts_installed"))
        self.assertTrue(hasattr(self.Client.SolarSystemWindow.set_ship_specs_data, "__wrapped__"))
        self.assertTrue(hasattr(self.Client._flight_text_entry_active, "__wrapped__"))

    def test_the_real_gate_still_works_for_chat_and_for_a_typed_name(self):
        gate = self.Client._flight_text_entry_active
        base = dict(chat_focused=False, squad_invite_focused=False, mission_log_search_focused=False,
                    galaxy_map_search_focused=False, redeem_code_open=False)
        self.assertFalse(gate(**base))
        self.assertTrue(gate(**{**base, "chat_focused": True}))

    def menu_window(self):
        """A bare window with just what the HUD menu popup reads (the real _draw_menu_popup runs on it)."""
        from render_mixin import RenderMixin  # noqa: F401  (the real class is built below)
        win = self.Client.SolarSystemWindow.__new__(self.Client.SolarSystemWindow)
        win._lock = threading.RLock()
        win._uk = 1.0
        win._menu_open = True
        win._menu_btn_rect = pygame.Rect(1100, 680, 60, 30)
        win._my_missions_active = []
        win._friends_state = {}
        win._attendance_gui = SimpleNamespace(notification_count=0, open=False)
        win._run_summary = RunSummaryState()                     # the MENU's RUN SUMMARY row reads it (v0.5.80)
        win._premium_shop = premium_shop.PremiumShopState()      # the game's own default: shop off
        for name in ("_stats_open", "_friends_open", "_mission_log_open", "_coalition_open", "_market_open"):
            setattr(win, name, False)
        fonts = {}
        win._instrument_font = lambda size, bold=False: fonts.setdefault(
            (size, bold), pygame.font.SysFont("consolas", size, bold=bold))
        return win

    def test_the_hud_menu_keeps_both_rows_when_the_game_shows_its_shop(self):
        win = self.menu_window()
        win._premium_shop.enabled = True                         # the server turned the shop on
        slw.reset_for_tests()
        win._draw_menu_popup(pygame.Surface(SCREEN, pygame.SRCALPHA))
        self.assertIsNotNone(win._menu_layouts_rect)
        self.assertIsNotNone(win._menu_shop_rect)
        for rect in (win._menu_layouts_rect, win._menu_shop_rect):
            self.assertTrue(win._menu_popup_rect.contains(rect))
        self.assertFalse(win._menu_layouts_rect.colliderect(win._menu_shop_rect))
        self.assertGreater(win._menu_shop_rect.top, win._menu_layouts_rect.top)   # the game's row stays last

    def test_the_hud_menu_has_the_row_and_its_rect(self):
        win = self.menu_window()
        slw.reset_for_tests()
        screen = pygame.Surface(SCREEN, pygame.SRCALPHA)
        win._draw_menu_popup(screen)
        self.assertIsNotNone(win._menu_layouts_rect)
        self.assertTrue(win._menu_popup_rect.contains(win._menu_layouts_rect))
        self.assertGreater(win._menu_layouts_rect.top, win._menu_market_rect.top)         # below the last vanilla row
        self.assertFalse(win._menu_layouts_rect.colliderect(win._menu_market_rect))
        win._menu_open = False
        win._draw_menu_popup(screen)
        self.assertIsNone(win._menu_layouts_rect)


if __name__ == "__main__":
    unittest.main()

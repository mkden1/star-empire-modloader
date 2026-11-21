"""Tests for the LOADOUTS tab of quick_bars_window.py and the /loadout command: saving, selecting, loading, undoing, updating, renaming and
deleting loadouts through the window's own clicks and keys, the CURRENT tag, and the Loadouts keys on the KEYS tab. The game's bar is the
game's own HotbarMixin save / load code (``_hotbar_layout_out`` / ``_hotbar_layout_in``)."""

from __future__ import annotations
import modloader

import os
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

import bars
import extra_bars as eb
import keybinds as kb
import loadout_ops as ops
import loadouts as lo
import quick_bars_window as qw
import quick_keys as qk
from hotbar import HotbarMixin
from keybinds import Combo
from test_bar_context import FIGHTER, MISSILE
from test_quick_bars_tab import Base as BarsBase, BarWin, saved_set
from test_quick_bars_window import Clock, sync_submit

HAULER = {"item_class": "cargo_item", "item_category": "fighter", "item_type": "hauler", "display_name": "Hauler",
          "color_rgb": [10, 20, 30]}


SCOUT = {"item_class": "cargo_item", "item_category": "fighter", "item_type": "scout", "display_name": "Scout", "color_rgb": [1, 2, 3]}


class LoadWin(BarWin):
    """The tab tests' window with the game's own bar save / load code and an account."""

    _hotbar_layout_out = HotbarMixin._hotbar_layout_out
    _hotbar_layout_in = HotbarMixin._hotbar_layout_in

    def __init__(self, scale=1.0):
        super().__init__(scale)
        self._hotbar = [None] * 20
        self.saves = 0
        self.refreshes = 0

    def _hotbar_save(self):
        self.saves += 1

    def _refresh_class_skill_bindings(self):
        self.refreshes += 1


class Base(BarsBase):
    def setUp(self):
        super().setUp()
        ops.reset_for_tests()
        ops.STORE.refresh()
        self.addCleanup(ops.reset_for_tests)

    def make_window(self, scale=1.0):
        win = LoadWin(scale)
        qw._ensure(win)
        return win

    def seed(self, *names):
        """Loadouts with these names, each with a different thing in the first game-bar slot, saved a minute apart."""
        bindings = (FIGHTER, MISSILE, HAULER, SCOUT)          # a different thing in each, so no two loadouts are the same setup
        for index, name in enumerate(names):
            ops.STORE.save(lo.new_loadout(name, {"main": [dict(bindings[index % 4])], "bars": [], "keys": {}}, when=1000.0 + 60 * index))

    def open_loadouts(self, *names):
        if names:
            self.seed(*names)
        qw.open_window(self.win, "loadouts")
        self.render()
        return self.win

    def names(self):
        return [d["name"] for d in ops.STORE.items]

    def rows(self):
        return {loadout_id: rect for rect, loadout_id in self.win._qb_hits["rows"]}

    def id_of(self, name):
        return ops.STORE.find(name)["id"]

    def main(self):
        return [None if b is None else b["item_type"] for b in self.win._hotbar]

    def in_file(self):
        return kb.read_file(self.path).get("loadouts", {})

    def status(self):
        return self.win._qb_status[0] if self.win._qb_status else None

    def name_it(self, text):
        self.key(pygame.K_a, mod=pygame.KMOD_CTRL)
        events = [pygame.event.Event(pygame.KEYDOWN, key=ord(ch.lower()) if ch.isalnum() else pygame.K_SPACE, mod=0, unicode=ch)
                  for ch in text]
        qw.filter_events(self.win, events)
        self.key(pygame.K_RETURN)
        self.render()

    def drawn_text(self):
        """Every text the last render drew, in order."""
        drawn = []
        real_text, real_button = qw._blit_text, qw._button

        def spy_text(win, screen, text, *args, **kwargs):
            drawn.append(text)
            return real_text(win, screen, text, *args, **kwargs)

        def spy_button(win, screen, rect, label, *args, **kwargs):
            drawn.append(label)
            return real_button(win, screen, rect, label, *args, **kwargs)

        with mock.patch.object(qw, "_blit_text", spy_text), mock.patch.object(qw, "_button", spy_button):
            self.render()
        return drawn


# ── the tabs and their geometry ──────────────────────────────────────────────

class GeometryTests(unittest.TestCase):
    def rects(self, tab, scale):
        s = (lambda px: px) if scale == 1.0 else (lambda px: round(px * scale))
        window = pygame.Rect(40, 30, s(qw._MIN_W), s(qw._MIN_H))
        return window, qw.layout_rects(window, s, tab)

    def test_everything_is_inside_the_window_on_the_loadouts_tab_at_every_scale(self):
        for scale in (1.0, 1.25, 1.5, 2.0):
            window, r = self.rects("loadouts", scale)
            for name, rect in r.items():
                self.assertTrue(window.contains(rect), (scale, name, rect))
                self.assertGreater(min(rect.w, rect.h), 0, (scale, name))

    def test_the_five_buttons_the_list_and_the_info_do_not_overlap(self):
        names = ["tabs", "add", "list", "info", "l_load", "l_undo", "l_update", "l_rename", "l_delete"]
        for scale in (1.0, 1.5, 2.0):
            _window, r = self.rects("loadouts", scale)
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    self.assertFalse(r[a].colliderect(r[b]), (scale, a, b))

    def test_the_three_tabs_sit_side_by_side_inside_the_tab_strip(self):
        for tab in ("keys", "bars", "loadouts"):
            for scale in (1.0, 2.0):
                _window, r = self.rects(tab, scale)
                for a, b in (("tab_keys", "tab_bars"), ("tab_bars", "tab_loadouts"), ("tab_keys", "tab_loadouts")):
                    self.assertFalse(r[a].colliderect(r[b]), (tab, scale, a, b))
                for name in ("tab_keys", "tab_bars", "tab_loadouts"):
                    self.assertTrue(r["tabs"].contains(r[name]), (tab, scale, name))

    def test_the_name_box_sits_in_the_save_row(self):
        _window, r = self.rects("loadouts", 1.0)
        self.assertTrue(r["add"].contains(r["field"]) and r["add"].contains(r["ok"]) and r["add"].contains(r["cancel"]))

    def test_there_is_room_for_a_few_rows_at_the_smallest_size(self):
        _window, r = self.rects("loadouts", 1.0)
        self.assertGreaterEqual(r["list"].h, 2 * qw.LOADOUT_ROW_H)


class TabTests(Base):
    def test_a_click_on_the_loadouts_tab_opens_it_and_the_others_come_back(self):
        self.open()
        self.press("tab_loadouts")
        self.assertEqual(self.win._qb_tab, "loadouts")
        self.assertEqual(self.win._qb_hits["tab"], "loadouts")
        self.press("tab_bars")
        self.assertEqual(self.win._qb_tab, "bars")
        self.press("tab_loadouts")
        self.press("tab_keys")
        self.assertEqual(self.win._qb_tab, "keys")

    def test_switching_away_disarms_and_closes_the_name_box(self):
        self.open_loadouts("Alpha")
        self.press("l_delete")
        self.assertIsNotNone(self.win._qb_confirm_loadout)
        self.press("tab_keys")
        self.assertIsNone(self.win._qb_confirm_loadout)
        self.press("tab_loadouts")
        self.press("add")
        self.assertIsNotNone(self.win._qb_input)
        self.key(pygame.K_ESCAPE)
        self.press("add")
        self.click(self.rects()["tab_bars"].center)                             # the name box takes the click, the tab does not switch
        self.assertEqual(self.win._qb_tab, "loadouts")

    def test_the_slash_command_opens_the_tab_and_closes_it_when_it_is_open(self):
        qw.handle_command(self.win, "/loadout")
        self.assertTrue(qw.is_open(self.win))
        self.assertEqual(self.win._qb_tab, "loadouts")
        qw.handle_command(self.win, "/loadouts")
        self.assertFalse(qw.is_open(self.win))
        qw.open_window(self.win, "keys")
        qw.handle_command(self.win, "/loadout")
        self.assertEqual(self.win._qb_tab, "loadouts")                         # open elsewhere: goes there rather than closing

    def test_closing_the_window_disarms(self):
        self.open_loadouts("Alpha")
        self.press("l_delete")
        qw.close_window(self.win)
        self.assertIsNone(self.win._qb_confirm_loadout)


# ── the list ─────────────────────────────────────────────────────────────────

class ListTests(Base):
    def test_an_empty_list_says_so_and_only_save_current_can_be_pressed(self):
        self.open_loadouts()
        self.assertIn("No loadouts yet", self.drawn_text())
        self.assertTrue(qw.button_enabled(self.win, "lsave"))
        for name in ("l_load", "l_update", "l_rename", "l_delete", "l_undo"):
            self.assertFalse(qw.button_enabled(self.win, name), name)

    def test_the_rows_are_the_loadouts_by_name_and_the_first_is_selected(self):
        self.open_loadouts("Charlie", "alpha", "Bravo")
        self.assertEqual(list(self.rows()), [self.id_of(n) for n in ("alpha", "Bravo", "Charlie")])
        self.assertEqual(self.win._qb_loadout_selected, self.id_of("alpha"))

    def test_a_row_shows_its_name_its_contents_and_its_age(self):
        self.open_loadouts("Alpha")
        text = self.drawn_text()
        self.assertIn("Alpha", text)
        self.assertTrue(any(t.startswith("main 1  -  0 bars, 0 slots  -  ") and t.endswith(" ago") for t in text), text)

    def test_what_you_have_now_is_selected_on_opening_and_tagged_current(self):
        self.seed("Alpha", "Bravo", "Charlie")
        self.win._hotbar[0] = dict(HAULER)                       # Charlie's main
        qw.open_window(self.win, "loadouts")
        self.render()
        self.assertEqual(self.win._qb_loadout_selected, self.id_of("Charlie"))
        self.assertEqual(self.drawn_text().count("CURRENT"), 1)

    def test_nothing_is_tagged_when_what_you_have_is_not_saved(self):
        self.open_loadouts("Alpha", "Bravo")
        self.assertNotIn("CURRENT", self.drawn_text())

    def test_clicking_a_row_selects_it_and_disarms(self):
        self.open_loadouts("Alpha", "Bravo")
        self.press("l_delete")
        self.click(self.rows()[self.id_of("Bravo")].center)
        self.assertEqual(self.win._qb_loadout_selected, self.id_of("Bravo"))
        self.assertIsNone(self.win._qb_confirm_loadout)
        self.assertEqual(len(ops.STORE.items), 2)

    def test_the_selection_moves_on_when_its_loadout_is_deleted_elsewhere(self):
        self.open_loadouts("Alpha", "Bravo")
        ops.STORE.delete(self.id_of("Alpha"))
        self.render()
        self.assertEqual(self.win._qb_loadout_selected, self.id_of("Bravo"))

    def test_a_loadout_another_client_saved_shows_up_after_a_while(self):
        clock = Clock()
        with mock.patch.object(qw.time, "monotonic", clock.monotonic):
            self.open_loadouts("Alpha")
            other = lo.LoadoutStore(qk.path, qk.submitter)
            other.refresh()
            other.save(lo.new_loadout("From the other client", {"main": [], "bars": [], "keys": {}}, when=5.0))
            self.render()
            self.assertEqual(self.names(), ["Alpha"])
            clock.now += qw.REFRESH_S + 1
            self.render()
            self.assertEqual(self.names(), ["Alpha", "From the other client"])

    def test_the_list_scrolls_when_there_are_more_rows_than_fit(self):
        self.open_loadouts(*[f"Loadout {i:02d}" for i in range(30)])
        self.assertGreater(self.win._qb_max_scroll, 0)
        before = len(self.win._qb_hits["rows"])
        self.assertLess(before, 30)


# ── saving ───────────────────────────────────────────────────────────────────

class SaveTests(Base):
    def test_save_current_opens_the_name_box_with_a_suggested_name(self):
        self.open_loadouts("Alpha")
        self.press("add")
        self.assertEqual(self.win._qb_input["mode"], "lsave")
        self.assertEqual(self.win._qb_input["text"], "Loadout 2")
        self.assertTrue(qw.capturing(self.win))

    def test_the_suggested_name_is_not_one_that_is_taken(self):
        self.open_loadouts("Loadout 2")                         # one loadout, so the next would be "Loadout 2"
        self.press("add")
        self.assertEqual(self.win._qb_input["text"], "Loadout 2 (2)")

    def test_enter_saves_what_you_have_selects_it_and_says_so(self):
        self.win._hotbar[3] = dict(HAULER)
        self.open_loadouts()
        self.press("add")
        self.name_it("Warden")
        self.assertEqual(self.names(), ["Warden"])
        item = ops.STORE.find("Warden")
        self.assertEqual(item["main"][3]["item_type"], "hauler")
        self.assertEqual(self.win._qb_loadout_selected, item["id"])
        self.assertEqual(self.status(), "Saved 'Warden' (main 1  -  0 bars, 0 slots).")
        self.assertEqual(list(self.in_file()), [item["id"]])
        self.assertEqual(ops.match(self.win)["id"], item["id"])
        self.assertIn("CURRENT", self.drawn_text())

    def test_it_keeps_the_extra_bars_and_their_keys(self):
        eb._raw = {"alice": saved_set("Combat")}
        qw._ensure(self.win)
        eb.ensure_loaded(self.win)
        eb.runtime(self.win).barset.get("b1").slots[4] = dict(FIGHTER)
        qk.set_binding("slot.b1.3", Combo(pygame.K_F3, ()))
        self.open_loadouts()
        self.press("add")
        self.name_it("Combat setup")
        item = ops.STORE.find("Combat setup")
        self.assertEqual([b["name"] for b in item["bars"]], ["Combat"])
        self.assertEqual(item["bars"][0]["slots"][4]["item_type"], "interceptor")
        self.assertEqual(item["keys"], {"slot.b1.3": {"key": "f3", "mods": []}})
        self.assertEqual(self.status(), "Saved 'Combat setup' (main 0  -  1 bar, 1 slot).")

    def test_a_name_that_is_taken_gets_a_number(self):
        self.open_loadouts("Alpha")
        self.press("add")
        self.name_it("alpha")
        self.assertEqual(self.names(), ["Alpha", "alpha (2)"])

    def test_an_empty_name_is_refused_and_the_box_stays_open(self):
        self.open_loadouts()
        self.press("add")
        self.key(pygame.K_a, mod=pygame.KMOD_CTRL)
        self.key(pygame.K_BACKSPACE)
        self.key(pygame.K_RETURN)
        self.assertIsNotNone(self.win._qb_input)
        self.assertEqual(self.status(), "A loadout needs a name.")
        self.assertEqual(self.names(), [])

    def test_the_name_is_capped_at_the_loadout_limit_which_is_longer_than_a_bars(self):
        self.open_loadouts()
        self.press("add")
        self.name_it("x" * 80)
        self.assertEqual(len(self.names()[0]), lo.MAX_NAME_LEN)
        self.assertGreater(lo.MAX_NAME_LEN, bars.MAX_NAME_LEN)

    def test_ok_saves_and_esc_cancels(self):
        self.open_loadouts()
        self.press("add")
        self.press("cancel")
        self.assertIsNone(self.win._qb_input)
        self.assertEqual(self.names(), [])
        self.press("add")
        self.press("ok")
        self.assertEqual(len(self.names()), 1)
        self.press("add")
        self.key(pygame.K_ESCAPE)
        self.assertIsNone(self.win._qb_input)
        self.assertEqual(len(self.names()), 1)

    def test_the_flight_keys_are_quiet_while_the_name_is_typed(self):
        self.open_loadouts()
        self.press("add")
        self.assertTrue(qw.any_capturing())
        self.key(pygame.K_ESCAPE)
        self.assertFalse(qw.any_capturing())

    def test_nothing_can_be_saved_without_a_logged_in_account(self):
        self.win._username = None
        self.open_loadouts()
        self.assertFalse(qw.button_enabled(self.win, "lsave"))
        self.press("add")
        self.assertIsNone(self.win._qb_input)
        qw.begin_input(self.win, "lsave")
        self.assertIsNone(self.win._qb_input)
        self.assertIn("Each character has its own bars: log in to use loadouts.", self.drawn_text())

    def test_a_save_that_the_store_refuses_before_it_has_read_the_file_is_told_not_lost(self):
        self.open_loadouts()
        ops.reset_for_tests()                                      # a store that has not read the file yet
        self.press("add")
        self.name_it("Early")
        self.assertEqual(self.status(), "Still loading the saved loadouts; try again in a moment.")
        self.assertEqual(self.win._qb_status[1], "warn")


# ── loading and undoing ──────────────────────────────────────────────────────

class LoadTests(Base):
    def test_load_puts_the_selected_loadout_on_the_bar_and_says_so(self):
        self.open_loadouts("Alpha", "Bravo")
        self.click(self.rows()[self.id_of("Bravo")].center)
        self.press("l_load")
        self.assertEqual(self.main()[0], "kinetic_missile")
        self.assertEqual(self.status(), "Loaded 'Bravo'. UNDO brings back what you had.")
        self.assertEqual(self.win.saves, 1)
        self.assertEqual(self.win.refreshes, 1)
        self.assertEqual(ops.match(self.win)["name"], "Bravo")
        self.assertIn("CURRENT", self.drawn_text())

    def test_a_double_click_loads_it_and_two_slow_clicks_do_not(self):
        clock = Clock()
        with mock.patch.object(qw.time, "monotonic", clock.monotonic):
            self.open_loadouts("Alpha", "Bravo")
            row = self.rows()[self.id_of("Bravo")].center
            self.click(row)
            self.assertIsNone(self.main()[0])
            clock.now += 0.2
            self.click(row)
            self.assertEqual(self.main()[0], "kinetic_missile")
            self.win._hotbar[0] = None
            clock.now += 5
            self.click(self.rows()[self.id_of("Alpha")].center)
            clock.now += qw.DOUBLE_CLICK_S + 0.3
            self.click(self.rows()[self.id_of("Alpha")].center)
            self.assertIsNone(self.main()[0])

    def test_a_double_click_on_two_different_rows_loads_nothing(self):
        clock = Clock()
        with mock.patch.object(qw.time, "monotonic", clock.monotonic):
            self.open_loadouts("Alpha", "Bravo")
            self.click(self.rows()[self.id_of("Alpha")].center)
            clock.now += 0.1
            self.click(self.rows()[self.id_of("Bravo")].center)
        self.assertIsNone(self.main()[0])

    def test_undo_is_only_there_after_a_load_and_brings_back_what_you_had(self):
        self.win._hotbar[5] = dict(HAULER)
        self.open_loadouts("Alpha")
        self.assertFalse(qw.button_enabled(self.win, "l_undo"))
        self.press("l_undo")
        self.assertIsNone(self.status())
        self.press("l_load")
        self.assertTrue(qw.button_enabled(self.win, "l_undo"))
        self.assertIsNone(self.main()[5])
        self.press("l_undo")
        self.assertEqual(self.main()[5], "hauler")
        self.assertIsNone(self.main()[0])
        self.assertEqual(self.status(), "Put your bars back as they were before the last load.")
        self.assertFalse(qw.button_enabled(self.win, "l_undo"))

    def test_the_info_line_follows_the_selection(self):
        self.open_loadouts("Alpha", "Bravo")
        self.assertIn("LOAD replaces your bars with 'Alpha'.", self.drawn_text())
        self.assertIn("What you have now is not saved as a loadout: SAVE CURRENT first, or UNDO after.", self.drawn_text())
        self.press("l_load")
        text = self.drawn_text()
        self.assertIn("Alpha is what you have now.", text)
        self.assertIn("Changes to your bars are not saved into it until you UPDATE.", text)
        self.click(self.rows()[self.id_of("Bravo")].center)
        text = self.drawn_text()
        self.assertIn("LOAD replaces your bars with 'Bravo'.", text)
        self.assertIn("What you have now is saved as 'Alpha'.", text)

    def test_a_loadout_that_the_game_chokes_on_is_reported_and_the_bar_is_left_alone(self):
        self.open_loadouts("Alpha")
        self.win._hotbar[7] = dict(HAULER)
        real = self.win._hotbar_layout_in
        calls = []

        def broken(data):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("no")
            real(data)

        self.win._hotbar_layout_in = broken
        with self.assertLogs("loadout_ops", level="ERROR"):
            self.press("l_load")
        self.assertEqual(self.win._qb_status[1], "warn")
        self.assertIn("left as they were", self.status())
        self.assertEqual(self.main()[7], "hauler")

    def test_the_buttons_do_nothing_when_nothing_is_selected(self):
        self.open_loadouts()
        for name in ("l_load", "l_update", "l_rename", "l_delete", "l_undo"):
            self.press(name)
        self.assertIsNone(self.win._qb_input)
        self.assertIsNone(self.status())


# ── update, rename, delete ───────────────────────────────────────────────────

class ManageTests(Base):
    def test_update_needs_a_second_click_then_stores_what_you_have_now_under_the_same_name(self):
        self.open_loadouts("Alpha", "Bravo")
        old = ops.STORE.find("Alpha")
        self.win._hotbar[9] = dict(HAULER)
        self.press("l_update")
        self.assertEqual(ops.STORE.get(old["id"])["main"], old["main"])
        self.assertIn("SURE?", self.drawn_text())
        self.press("l_update")
        new = ops.STORE.get(old["id"])
        self.assertEqual(new["name"], "Alpha")
        self.assertEqual(new["main"][9]["item_type"], "hauler")
        self.assertIsNone(new["main"][0])
        self.assertEqual(len(ops.STORE.items), 2)
        self.assertEqual(self.status(), "Updated 'Alpha' (main 1  -  0 bars, 0 slots).")
        self.assertEqual(ops.STORE.find("Bravo")["main"][0]["item_type"], "kinetic_missile")      # the other one is not touched
        self.assertEqual(ops.match(self.win)["id"], old["id"])

    def test_an_armed_update_expires_and_a_different_button_does_not_confirm_it(self):
        clock = Clock()
        with mock.patch.object(qw.time, "monotonic", clock.monotonic):
            self.open_loadouts("Alpha")
            old = ops.STORE.find("Alpha")
            self.win._hotbar[9] = dict(HAULER)
            self.press("l_update")
            clock.now += qw.CONFIRM_S + 1
            self.render()
            self.assertIsNone(self.win._qb_confirm_loadout)
            self.press("l_update")
            self.assertEqual(ops.STORE.get(old["id"])["main"], old["main"])
            self.press("l_delete")                       # DELETE armed, then UPDATE pressed: neither goes through
            self.press("l_update")
            self.assertEqual(len(ops.STORE.items), 1)
            self.assertEqual(ops.STORE.get(old["id"])["main"], old["main"])

    def test_a_delete_armed_for_one_loadout_does_not_delete_another_the_command_selected(self):
        self.open_loadouts("Alpha", "Bravo")
        self.press("l_delete")                                         # armed for Alpha
        qw.handle_command(self.win, "/loadout load Bravo")             # selects Bravo without a click on its row
        self.press("l_delete")
        self.assertEqual(self.names(), ["Alpha", "Bravo"])
        self.assertEqual(self.win._qb_confirm_loadout[:2], ("delete", self.id_of("Bravo")))

    def test_a_click_after_the_time_is_up_does_not_confirm_even_if_nothing_was_drawn_in_between(self):
        clock = Clock()
        with mock.patch.object(qw.time, "monotonic", clock.monotonic):
            self.open_loadouts("Alpha")
            self.win._hotbar[9] = dict(HAULER)
            old = ops.STORE.find("Alpha")
            self.click(self.rects()["l_update"].center)
            clock.now += qw.CONFIRM_S + 1
            self.click(self.rects()["l_update"].center)                       # no frame in between: only the click itself knows
            self.assertEqual(ops.STORE.get(old["id"])["main"], old["main"])

    def test_rename_starts_with_the_current_name_and_keeps_everything_else(self):
        self.open_loadouts("Alpha", "Bravo")
        self.click(self.rows()[self.id_of("Bravo")].center)
        old = ops.STORE.find("Bravo")
        self.press("l_rename")
        self.assertEqual(self.win._qb_input["mode"], "lrename")
        self.assertEqual(self.win._qb_input["text"], "Bravo")
        self.name_it("Zulu")
        self.assertEqual(self.names(), ["Alpha", "Zulu"])
        new = ops.STORE.get(old["id"])
        self.assertEqual((new["name"], new["main"], new["saved_at"]), ("Zulu", old["main"], old["saved_at"]))
        self.assertEqual(self.status(), "Renamed to 'Zulu'.")
        self.assertEqual(self.in_file()[old["id"]]["name"], "Zulu")
        self.assertEqual(self.win._qb_loadout_selected, old["id"])

    def test_a_name_another_loadout_has_gets_a_number_but_its_own_name_in_other_case_does_not(self):
        self.open_loadouts("Alpha", "Bravo")
        self.press("l_rename")                                       # Alpha is selected: a name Bravo already has
        self.name_it("bravo")
        self.assertEqual(self.names(), ["Bravo", "bravo (2)"])
        self.assertEqual(self.win._qb_loadout_selected, self.id_of("bravo (2)"))
        self.press("l_rename")                                       # the same name in other letters is its own: no number
        self.name_it("BRAVO (2)")
        self.assertEqual(self.names(), ["Bravo", "BRAVO (2)"])

    def test_an_empty_new_name_is_refused(self):
        self.open_loadouts("Alpha")
        self.press("l_rename")
        self.key(pygame.K_a, mod=pygame.KMOD_CTRL)
        self.key(pygame.K_BACKSPACE)
        self.key(pygame.K_RETURN)
        self.assertEqual(self.status(), "A loadout needs a name.")
        self.assertEqual(self.names(), ["Alpha"])
        self.assertIsNotNone(self.win._qb_input)

    def test_delete_needs_a_second_click_on_the_same_loadout(self):
        self.open_loadouts("Alpha", "Bravo", "Charlie")
        self.press("l_delete")
        self.assertEqual(len(self.names()), 3)
        self.assertEqual(self.win._qb_confirm_loadout[:2], ("delete", self.id_of("Alpha")))
        self.assertIn("SURE?", self.drawn_text())
        self.click(self.rows()[self.id_of("Bravo")].center)                        # another row disarms
        self.assertIsNone(self.win._qb_confirm_loadout)
        self.press("l_delete")
        self.press("l_delete")
        self.assertEqual(self.names(), ["Alpha", "Charlie"])
        self.assertEqual(self.win._qb_loadout_selected, self.id_of("Alpha"))       # the first one left is selected now
        self.assertEqual(self.status(), "Deleted 'Bravo'.")
        self.assertEqual(sorted(self.in_file()), sorted(d["id"] for d in ops.STORE.items))

    def test_deleting_the_last_one_leaves_an_empty_list_and_a_disabled_row_of_buttons(self):
        self.open_loadouts("Alpha")
        self.press("l_delete")
        self.press("l_delete")
        self.assertEqual(self.names(), [])
        self.assertIsNone(self.win._qb_loadout_selected)
        self.assertFalse(qw.button_enabled(self.win, "l_delete"))
        self.assertEqual(self.in_file(), {})

    def test_deleting_a_loadout_leaves_the_bars_you_have_alone(self):
        self.open_loadouts("Alpha")
        self.press("l_load")
        before = self.main()
        self.press("l_delete")
        self.press("l_delete")
        self.assertEqual(self.main(), before)


# ── the /loadout command ─────────────────────────────────────────────────────

class CommandTests(Base):
    def setUp(self):
        super().setUp()
        self.seed("Alpha", "Bravo", "Hauler", "Hunter")

    def run_command(self, text):
        qw.handle_command(self.win, text)
        return self.win.chat

    def test_list_names_the_loadouts_and_marks_the_one_you_have(self):
        self.win._hotbar[0] = dict(MISSILE)                                # Bravo's main
        chat = self.run_command("/loadout list")
        self.assertEqual(len(chat), 4)
        self.assertTrue(chat[1].startswith("  Bravo   main 1") and chat[1].endswith("<- what you have now"))
        self.assertFalse(any("<-" in line for line in (chat[0], chat[2], chat[3])))

    def test_list_with_nothing_saved(self):
        kb.write_section(self.path, "loadouts", {})
        ops.STORE.refresh()
        self.assertTrue(self.run_command("/loadout ls")[0].startswith("No loadouts saved yet."))

    def test_list_before_the_file_is_read(self):
        ops.reset_for_tests()
        self.assertEqual(self.run_command("/loadout list")[0], "Still loading the saved loadouts; try again in a moment.")

    def test_save_stores_what_you_have_under_the_name(self):
        self.win._hotbar[2] = dict(HAULER)
        chat = self.run_command("/loadout save Warden build")
        self.assertEqual(chat[0], "Saved 'Warden build' (main 1  -  0 bars, 0 slots).")
        self.assertEqual(ops.STORE.find("Warden build")["main"][2]["item_type"], "hauler")
        self.assertEqual(len(ops.STORE.items), 5)

    def test_save_on_an_existing_name_updates_that_loadout(self):
        old = ops.STORE.find("Bravo")
        self.win._hotbar[2] = dict(HAULER)
        chat = self.run_command("/loadout save bravo")
        self.assertTrue(chat[0].startswith("Updated 'Bravo'"))
        self.assertEqual(len(ops.STORE.items), 4)
        self.assertEqual(ops.STORE.get(old["id"])["main"][2]["item_type"], "hauler")

    def test_save_with_only_the_start_of_a_name_makes_a_new_loadout(self):
        chat = self.run_command("/loadout save Brav")
        self.assertTrue(chat[0].startswith("Saved 'Brav'"))
        self.assertEqual(len(ops.STORE.items), 5)

    def test_save_without_a_name_says_how(self):
        self.assertEqual(self.run_command("/loadout save")[0], "Usage: /loadout save <name>")
        self.assertEqual(len(ops.STORE.items), 4)

    def test_load_by_name_or_by_the_start_of_a_name(self):
        self.assertTrue(self.run_command("/loadout load bravo")[0].startswith("Loaded 'Bravo'"))
        self.assertEqual(self.main()[0], "kinetic_missile")
        self.assertEqual(self.win._qb_loadout_selected, self.id_of("Bravo"))
        self.run_command("/loadout use hau")
        self.assertEqual(self.main()[0], "hauler")                          # Hauler: the only name starting with 'hau'

    def test_an_ambiguous_or_unknown_name_loads_nothing(self):
        for name in ("h", "zz"):
            chat = self.run_command(f"/loadout load {name}")
            self.assertEqual(chat[-1], f"No loadout called '{name}'. /loadout list shows them.")
        self.assertEqual(self.run_command("/loadout load")[-1], "Usage: /loadout load <name>")
        self.assertIsNone(self.main()[0])

    def test_undo(self):
        self.win._hotbar[4] = dict(HAULER)
        self.assertEqual(self.run_command("/loadout undo")[0], "Nothing to undo.")
        self.run_command("/loadout load Alpha")
        self.assertIsNone(self.main()[4])
        self.run_command("/loadout undo")
        self.assertEqual(self.main()[4], "hauler")

    def test_next_and_prev_cycle_through_the_loadouts(self):
        self.run_command("/loadout next")
        self.assertEqual(ops.match(self.win)["name"], "Alpha")
        self.run_command("/loadout next")
        self.assertEqual(ops.match(self.win)["name"], "Bravo")
        self.run_command("/loadout prev")
        self.run_command("/loadout previous")
        self.assertEqual(ops.match(self.win)["name"], "Hunter")                # Alpha, then back past the start

    def test_an_unknown_word_shows_the_help(self):
        self.assertIn("/loadout list", self.run_command("/loadout dance")[0])

    def test_the_commands_need_an_account_to_change_anything(self):
        self.win._username = None
        self.assertIn("Log in", self.run_command("/loadout save X")[0])
        self.assertIn("Log in", self.run_command("/loadout load Alpha")[-1])
        self.assertEqual(len(ops.STORE.items), 4)


class InstallTests(Base):
    def test_installing_reads_the_saved_loadouts_so_a_loadout_key_works_before_the_window_is_opened(self):
        item = lo.new_loadout("Alpha", {"main": [dict(FIGHTER)], "bars": [], "keys": {}}, when=5.0)
        kb.write_entry(self.path, "loadouts", item["id"], item)
        ops.reset_for_tests()                                  # nothing read yet
        self.assertFalse(ops.STORE.loaded)

        class Fresh(type(self.win)):
            pass

        self.assertTrue(qw.install(Fresh))
        self.assertTrue(ops.STORE.loaded)
        self.assertEqual(self.names(), ["Alpha"])


# ── the loadout keys on the KEYS tab ─────────────────────────────────────────

class KeysTabTests(Base):
    def test_the_two_loadout_actions_have_their_own_group_and_can_be_given_a_key(self):
        self.open()
        self.start_capture("loadout.next")
        self.key(pygame.K_F6)
        self.assertEqual(self.combo("loadout.next"), Combo(pygame.K_F6, ()))
        self.assertEqual(kb.read_file(self.path)["keys"]["loadout.next"], {"key": "f6", "mods": []})

    def test_a_key_chosen_there_cycles_the_loadouts_in_the_game(self):
        self.seed("Alpha", "Bravo")
        self.open()
        self.start_capture("loadout.prev")
        self.key(pygame.K_F5)
        event, consumed = qk.on_key_down(self.win, pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F5, mod=0, unicode="", scancode=0))
        self.assertTrue(consumed)
        self.assertEqual(ops.match(self.win)["name"], "Bravo")                 # nothing matched: previous = the last one

    def test_the_list_has_a_loadouts_group_after_the_game_keys(self):
        groups = [data for kind, data in qw.view_rows() if kind == "group"]
        self.assertEqual(groups[-1], kb.GROUP_LOADOUTS)
        self.assertEqual(len(groups), 3)
        self.assertEqual([a.group for a in kb.CATALOG][-2:], [kb.GROUP_LOADOUTS] * 2)


if __name__ == "__main__":
    unittest.main()

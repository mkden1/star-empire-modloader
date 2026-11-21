"""Tests for loadout_ops.py: saving, loading and undoing loadouts on the game's real bar code (the dev's bar test window: the real
HotbarMixin, so ``_hotbar_layout_out`` / ``_hotbar_layout_in`` are the game's own), the extra bars and the keys changed for their slots."""

from __future__ import annotations
import modloader

import copy
import os
import unittest
from unittest.mock import patch

import pygame

import bars
import extra_bars as eb
import keybinds as kb
import loadout_ops as ops
import loadouts as lo
import quick_keys as qk
import test_extra_bars as teb
from keybinds import Combo
from test_bar_context import FIGHTER, MISSILE

HAULER = {"item_class": "cargo_item", "item_category": "fighter", "item_type": "hauler", "display_name": "Hauler",
          "color_rgb": [10, 20, 30]}


def key_event(key, mod=0):
    return pygame.event.Event(pygame.KEYDOWN, key=key, mod=mod, unicode="", scancode=0)


class Base(teb.Base):
    def setUp(self):
        super().setUp()
        ops.reset_for_tests()
        self.refreshed = 0
        win = self.win
        win._refresh_class_skill_bindings = lambda: setattr(self, "refreshed", self.refreshed + 1)
        ops.STORE.refresh()                                  # nothing saved yet, and the store knows it

    def tearDown(self):
        ops.reset_for_tests()

    def main(self, win=None):
        return [None if b is None else b["item_type"] for b in (win or self.win)._hotbar]

    def fill_main(self, *pairs, win=None):
        for index, binding in pairs:
            (win or self.win)._hotbar[index] = dict(binding)

    def fill_bar(self, bar_id, *pairs, win=None):
        rt = eb.ensure_loaded(win or self.win)
        for index, binding in pairs:
            rt.barset.get(bar_id).slots[index] = dict(binding)

    def bar_types(self, bar_id, win=None):
        rt = eb.ensure_loaded(win or self.win)
        return [None if s is None else s["item_type"] for s in rt.barset.get(bar_id).slots]

    def arrange(self):
        """A game bar with two things on it, two extra bars with one each, and a key changed for a slot on the first."""
        self.given("Combat", "Mining")
        self.fill_main((0, FIGHTER), (11, MISSILE))
        self.fill_bar("b1", (2, FIGHTER))
        self.fill_bar("b2", (5, MISSILE))
        eb.ensure_loaded(self.win)
        qk.set_binding("slot.b1.3", Combo(pygame.K_F3, ()))

    def save(self, name="Warden", target=None, win=None):
        ok, text = ops.save_current(win or self.win, name, target)
        self.assertTrue(ok, text)
        return ops.STORE.find(name) if target is None else ops.STORE.get(target)


class CaptureTests(Base):
    def test_a_capture_holds_the_game_bar_the_extra_bars_and_their_keys(self):
        self.arrange()
        got = ops.capture(self.win)
        self.assertEqual([None if b is None else b["item_type"] for b in got["main"]][0], "interceptor")
        self.assertEqual(got["main"][11]["item_type"], "kinetic_missile")
        self.assertEqual(len(got["main"]), 20)
        self.assertEqual([b["name"] for b in got["bars"]], ["Combat", "Mining"])
        self.assertEqual(got["bars"][0]["slots"][2]["item_type"], "interceptor")
        self.assertEqual(got["keys"], {"slot.b1.3": {"key": "f3", "mods": []}})

    def test_keys_changed_for_the_games_own_slots_and_hotkeys_are_not_part_of_it(self):
        self.arrange()
        qk.set_binding("hotkey.dock", Combo(pygame.K_b, ()))
        qk.set_binding("slot.main.2", Combo(pygame.K_q, ()))
        self.assertEqual(sorted(ops.capture(self.win)["keys"]), ["slot.b1.3"])

    def test_with_no_extra_bars_it_is_just_the_game_bar(self):
        self.fill_main((1, FIGHTER))
        got = ops.capture(self.win)
        self.assertEqual(got["bars"], [])
        self.assertEqual(got["keys"], {})
        self.assertEqual(got["main"][1]["item_type"], "interceptor")

    def test_a_window_without_the_games_bar_methods_captures_no_game_bar(self):
        plain = teb.PlainWin()
        plain._username = "alice"
        plain._layout = None
        with patch.object(teb.PlainWin, "_hotbar_layout_out", None, create=True):
            self.assertEqual(ops.capture(plain)["main"], [])

    def test_the_capture_is_a_copy(self):
        self.arrange()
        got = ops.capture(self.win)
        got["main"][0]["item_type"] = "changed"
        got["bars"][0]["slots"][2]["item_type"] = "changed"
        self.assertEqual(self.win._hotbar[0]["item_type"], "interceptor")
        self.assertEqual(self.bar_types("b1")[2], "interceptor")


class SaveTests(Base):
    def test_saving_stores_what_you_have(self):
        self.arrange()
        ok, text = ops.save_current(self.win, "Warden")
        self.assertTrue(ok)
        self.assertEqual(text, "Saved 'Warden' (main 2  -  2 bars, 2 slots).")
        item = ops.STORE.find("Warden")
        self.assertEqual(lo.setup_key(lo.setup_of(item)), lo.setup_key(ops.capture(self.win)))
        self.assertEqual(list(kb.read_file(self.path)["loadouts"]), [item["id"]])

    def test_updating_keeps_the_name_and_the_id_and_takes_what_you_have_now(self):
        self.arrange()
        old = self.save("Warden")
        self.fill_main((3, HAULER))
        ok, text = ops.save_current(self.win, "ignored name", old["id"])
        self.assertTrue(ok)
        self.assertTrue(text.startswith("Updated 'Warden'"))
        self.assertEqual(len(ops.STORE.items), 1)
        new = ops.STORE.get(old["id"])
        self.assertEqual(new["name"], "Warden")
        self.assertEqual(new["main"][3]["item_type"], "hauler")

    def test_updating_something_that_was_deleted_in_the_meantime_saves_it_as_new(self):
        self.arrange()
        ok, text = ops.save_current(self.win, "Fresh", "no-such-id")
        self.assertTrue(ok)
        self.assertTrue(text.startswith("Saved 'Fresh'"))

    def test_nothing_is_saved_without_an_account(self):
        win = teb.XWin(username="")
        ok, text = ops.save_current(win, "X")
        self.assertFalse(ok)
        self.assertIn("Log in", text)
        self.assertEqual(ops.STORE.items, ())

    def test_nothing_is_saved_before_the_list_has_been_read(self):
        ops.reset_for_tests()                                    # a store that has not looked at the file yet
        ok, text = ops.save_current(self.win, "X")
        self.assertFalse(ok)
        self.assertIn("Still loading", text)
        self.assertEqual(ops.STORE.items, ())


class LoadTests(Base):
    def test_a_load_puts_the_game_bar_the_extra_bars_and_their_keys_in_place(self):
        self.arrange()
        item = self.save("Warden")
        # now a different arrangement
        self.win._hotbar[:] = [None] * 20
        self.fill_main((4, HAULER))
        eb.remove_bar(self.win, "b2")
        qk.reset_binding("slot.b1.3")
        self.assertNotEqual(lo.setup_key(lo.setup_of(item)), lo.setup_key(ops.capture(self.win)))
        ok, text = ops.load(self.win, item)
        self.assertTrue(ok)
        self.assertEqual(text, "Loaded 'Warden'. UNDO brings back what you had.")
        self.assertEqual(lo.setup_key(lo.setup_of(item)), lo.setup_key(ops.capture(self.win)))
        self.assertEqual(self.main()[0], "interceptor")
        self.assertIsNone(self.main()[4])
        self.assertEqual(self.main()[11], "kinetic_missile")
        self.assertEqual([b.name for b in eb.runtime(self.win).barset], ["Combat", "Mining"])
        self.assertEqual(self.bar_types("b2")[5], "kinetic_missile")
        self.assertEqual(qk.table().combo("slot.b1.3"), Combo(pygame.K_F3, ()))

    def test_it_is_all_saved_the_game_bar_through_the_games_own_save_the_bars_and_keys_to_the_file(self):
        self.arrange()
        item = self.save("Warden")
        self.win.saves = 0
        self.win._hotbar[:] = [None] * 20
        ops.load(self.win, item)
        self.assertEqual(self.win.saves, 1)
        self.assertEqual(self.refreshed, 1)
        data = kb.read_file(self.path)
        self.assertEqual([b["name"] for b in data["bars"]["alice"]["bars"]], ["Combat", "Mining"])
        self.assertEqual(data["keys"], {"slot.b1.3": {"key": "f3", "mods": []}})

    def test_a_loadout_with_a_shorter_game_bar_empties_the_slots_it_leaves_out(self):
        self.arrange()
        self.fill_main((15, HAULER))
        short = lo.new_loadout("Short", {"main": [dict(FIGHTER)], "bars": [], "keys": {}})
        ops.STORE.save(short)
        ok, _ = ops.load(self.win, short)
        self.assertTrue(ok)
        self.assertEqual(self.main()[0], "interceptor")
        self.assertEqual(self.main()[1:], [None] * 19)
        self.assertEqual(len(self.win._hotbar), 20)

    def test_a_loadout_longer_than_the_bar_loses_the_part_that_does_not_fit(self):
        long = lo.new_loadout("Long", {"main": [dict(FIGHTER)] * 30, "bars": [], "keys": {}})
        ops.STORE.save(long)
        ok, _ = ops.load(self.win, long)
        self.assertTrue(ok)
        self.assertEqual(len(self.win._hotbar), 20)
        self.assertTrue(all(b["item_type"] == "interceptor" for b in self.win._hotbar))

    def test_the_game_bar_keeps_copies_not_the_loadouts_own_dicts(self):
        item = lo.new_loadout("W", {"main": [dict(FIGHTER)], "bars": [], "keys": {}})
        ops.STORE.save(item)
        ops.load(self.win, item)
        self.win._hotbar[0]["item_type"] = "changed"
        self.assertEqual(item["main"][0]["item_type"], "interceptor")
        self.assertEqual(ops.STORE.get(item["id"])["main"][0]["item_type"], "interceptor")

    def test_keys_changed_for_the_old_bars_do_not_outlive_them_but_other_keys_do(self):
        self.arrange()
        item = lo.new_loadout("Bare", {"main": [], "bars": [], "keys": {}})
        ops.STORE.save(item)
        qk.set_binding("hotkey.dock", Combo(pygame.K_b, ()))
        ops.load(self.win, item)
        self.assertEqual(kb.read_file(self.path)["keys"], {"hotkey.dock": {"key": "b", "mods": []}})
        self.assertEqual(list(eb.runtime(self.win).barset), [])

    def test_a_bar_with_the_same_id_gets_the_loadouts_keys_not_the_old_ones(self):
        self.arrange()
        qk.set_binding("slot.b1.7", Combo(pygame.K_F7, ()))
        loadout = lo.new_loadout("Keys", {"main": [], "bars": [teb.saved_set("Only")["bars"][0]],
                                          "keys": {"slot.b1.4": {"key": "f4", "mods": []}}})
        ops.STORE.save(loadout)
        ops.load(self.win, loadout)
        self.assertEqual(sorted(kb.read_file(self.path)["keys"]), ["slot.b1.4"])
        self.assertEqual(qk.table().combo("slot.b1.4"), Combo(pygame.K_F4, ()))
        self.assertEqual(qk.table().combo("slot.b1.3"), qk.table().by_id["slot.b1.3"].default)

    def test_the_key_table_has_the_slots_of_the_new_bars_and_not_of_the_old_ones(self):
        self.arrange()
        brand_new = {**teb.saved_set("Brand new")["bars"][0], "id": "zz1"}
        item = lo.new_loadout("Other ids", {"main": [], "bars": [brand_new], "keys": {"slot.zz1.2": {"key": "f2", "mods": []}}})
        ops.STORE.save(item)
        ops.load(self.win, item)
        table = qk.table()
        self.assertIn("slot.zz1.1", table.by_id)
        self.assertNotIn("slot.b1.1", table.by_id)
        self.assertEqual(table.combo("slot.zz1.2"), Combo(pygame.K_F2, ()))
        self.assertTrue(table.has_bars)
        self.fill_bar("zz1", (1, FIGHTER))
        self.win.sent.clear()
        event, consumed = qk.on_key_down(self.win, key_event(pygame.K_F2))              # the new bar's slot key fires the new bar's slot
        self.assertTrue(consumed)
        self.assertTrue(self.win.sent)

    def test_the_new_bars_are_drawn_and_clickable_straight_away(self):
        self.arrange()
        item = self.save("Warden")
        eb.remove_bar(self.win, "b1")
        eb.remove_bar(self.win, "b2")
        self.draw()
        ops.load(self.win, item)
        self.draw()
        self.assertEqual(set(eb.runtime(self.win).views), {"b1", "b2"})
        self.assertTrue(self.win._gui_windows.__dict__ is not None)
        self.down(self.slot_center(2, "b1"))
        self.up(self.slot_center(2, "b1"))
        self.assertTrue(self.win.sent)                                   # the slot fired as a click on the loaded bar

    def test_a_loadout_cannot_be_loaded_without_an_account(self):
        win = teb.XWin(username="")
        item = lo.new_loadout("W", {"main": [dict(FIGHTER)], "bars": [], "keys": {}})
        ok, text = ops.load(win, item)
        self.assertFalse(ok)
        self.assertIn("Log in", text)
        self.assertIsNone(win._hotbar[0])

    def test_a_load_does_not_touch_other_accounts_bars(self):
        self.given("Combat", account="alice")
        eb._raw["bob"] = teb.saved_set("Bob's")
        item = lo.new_loadout("W", {"main": [], "bars": [], "keys": {}})
        ops.STORE.save(item)
        ops.load(self.win, item)
        self.assertEqual(kb.read_file(self.path).get("bars", {}).get("bob"), None)       # never written; bob's entry is as it was
        self.assertEqual(eb._raw["bob"], teb.saved_set("Bob's"))

    def test_a_loadout_saved_by_one_character_loads_on_another(self):
        self.arrange()
        item = self.save("Warden")
        other = teb.XWin(username="bob")
        other._refresh_class_skill_bindings = lambda: None
        ok, _ = ops.load(other, item)
        self.assertTrue(ok)
        self.assertEqual(other._hotbar[0]["item_type"], "interceptor")
        self.assertEqual([b.name for b in eb.runtime(other).barset], ["Combat", "Mining"])
        data = kb.read_file(self.path)
        self.assertEqual(sorted(data["bars"]), ["bob"])                 # bob's bars are saved under bob (alice's were never written)
        self.assertEqual(data["bars"]["bob"]["bars"][0]["slots"][2]["item_type"], "interceptor")

    def test_the_class_skill_bindings_are_refreshed_before_the_save(self):
        order = []
        self.win._refresh_class_skill_bindings = lambda: order.append("refresh")
        self.win._hotbar_save = lambda: order.append("save")
        item = lo.new_loadout("W", {"main": [dict(FIGHTER)], "bars": [], "keys": {}})
        ops.STORE.save(item)
        ops.load(self.win, item)
        self.assertEqual(order, ["refresh", "save"])

    def test_a_game_window_without_a_refresh_method_still_loads(self):
        del self.win._refresh_class_skill_bindings
        item = lo.new_loadout("W", {"main": [dict(FIGHTER)], "bars": [], "keys": {}})
        ops.STORE.save(item)
        self.assertTrue(ops.load(self.win, item)[0])


class UndoTests(Base):
    def test_undo_brings_back_everything_the_load_replaced(self):
        self.arrange()
        before = lo.setup_key(ops.capture(self.win))
        other = lo.new_loadout("Other", {"main": [dict(HAULER)], "bars": [teb.saved_set("Solo")["bars"][0]],
                                         "keys": {"slot.b1.9": {"key": "f9", "mods": []}}})
        ops.STORE.save(other)
        ops.load(self.win, other)
        self.assertNotEqual(lo.setup_key(ops.capture(self.win)), before)
        ok, text = ops.undo(self.win)
        self.assertTrue(ok)
        self.assertEqual(text, "Put your bars back as they were before the last load.")
        self.assertEqual(lo.setup_key(ops.capture(self.win)), before)
        self.assertEqual(self.main()[0], "interceptor")
        self.assertEqual(qk.table().combo("slot.b1.3"), Combo(pygame.K_F3, ()))
        self.assertEqual(sorted(kb.read_file(self.path)["keys"]), ["slot.b1.3"])

    def test_there_is_one_step_of_undo_and_it_is_used_up(self):
        self.arrange()
        other = lo.new_loadout("Other", {"main": [], "bars": [], "keys": {}})
        ops.STORE.save(other)
        ops.load(self.win, other)
        self.assertTrue(ops.undo(self.win)[0])
        ok, text = ops.undo(self.win)
        self.assertFalse(ok)
        self.assertEqual(text, "Nothing to undo.")
        self.assertNotIn("alice", kb.read_file(self.path).get("previous", {}))

    def test_a_second_load_replaces_the_undo_with_what_it_replaced(self):
        self.arrange()
        one = lo.new_loadout("One", {"main": [dict(HAULER)], "bars": [], "keys": {}})
        two = lo.new_loadout("Two", {"main": [dict(MISSILE)], "bars": [], "keys": {}})
        ops.STORE.save(one)
        ops.STORE.save(two)
        ops.load(self.win, one)
        ops.load(self.win, two)
        ops.undo(self.win)
        self.assertEqual(self.main()[0], "hauler")                        # back to One, not to the first arrangement

    def test_undoing_is_not_itself_undoable(self):
        self.arrange()
        other = lo.new_loadout("Other", {"main": [], "bars": [], "keys": {}})
        ops.STORE.save(other)
        ops.load(self.win, other)
        ops.undo(self.win)
        self.assertIsNone(eb.runtime(self.win).undo)

    def test_the_undo_is_in_the_file_so_a_restart_still_has_it(self):
        self.arrange()
        before = lo.setup_key(ops.capture(self.win))
        other = lo.new_loadout("Other", {"main": [], "bars": [], "keys": {}})
        ops.STORE.save(other)
        ops.load(self.win, other)
        self.assertIn("alice", kb.read_file(self.path)["previous"])
        # a restart: nothing in memory, the file is read again
        eb.reset_for_tests()
        qk.configure_for_tests(self.path, teb.sync_submit)
        eb.load()
        win = teb.XWin()
        win._refresh_class_skill_bindings = lambda: None
        self.assertIsNotNone(eb.ensure_loaded(win).undo)
        self.assertTrue(ops.undo(win)[0])
        self.assertEqual(lo.setup_key(ops.capture(win)), before)

    def test_each_character_has_their_own_undo(self):
        self.arrange()
        other = lo.new_loadout("Other", {"main": [], "bars": [], "keys": {}})
        ops.STORE.save(other)
        ops.load(self.win, other)
        bob = teb.XWin(username="bob")
        self.assertIsNone(eb.ensure_loaded(bob).undo)
        self.assertEqual(ops.undo(bob), (False, "Nothing to undo."))
        self.assertIsNotNone(eb.runtime(self.win).undo)

    def test_changing_character_changes_the_undo_with_them(self):
        self.arrange()
        other = lo.new_loadout("Other", {"main": [], "bars": [], "keys": {}})
        ops.STORE.save(other)
        ops.load(self.win, other)
        self.win._username = "bob"
        self.assertIsNone(eb.ensure_loaded(self.win).undo)
        self.win._username = "alice"
        self.assertIsNotNone(eb.ensure_loaded(self.win).undo)

    def test_a_damaged_undo_in_the_file_is_no_undo(self):
        kb.write_entry(self.path, "previous", "alice", "junk")
        eb.reset_for_tests()
        eb.load()
        self.assertIsNone(eb.ensure_loaded(teb.XWin()).undo)


class UndoAcrossAccountsTests(Base):
    def test_a_used_up_undo_does_not_come_back_when_the_character_changes_and_returns(self):
        self.arrange()
        other = lo.new_loadout("Other", {"main": [], "bars": [], "keys": {}})
        ops.STORE.save(other)
        ops.load(self.win, other)
        self.assertTrue(ops.undo(self.win)[0])
        self.win._username = "bob"
        eb.ensure_loaded(self.win)
        self.win._username = "alice"
        self.assertIsNone(eb.ensure_loaded(self.win).undo)

    def test_the_undo_goes_with_the_account_not_the_window(self):
        self.arrange()
        other = lo.new_loadout("Other", {"main": [], "bars": [], "keys": {}})
        ops.STORE.save(other)
        ops.load(self.win, other)
        again = teb.XWin(username="alice")                    # the same character in a second client
        self.assertIsNotNone(eb.ensure_loaded(again).undo)


class ReplaceSetTests(Base):
    def test_it_needs_an_account(self):
        with self.assertRaises(ValueError):
            eb.replace_set(teb.XWin(username=""), [], {})

    def test_a_gesture_in_progress_on_a_replaced_bar_is_dropped(self):
        self.arrange()
        rt = eb.ensure_loaded(self.win)
        rt.press_bar = "b1"
        rt.drag = {"bar": "b1", "dx": 1, "dy": 1}
        eb.replace_set(self.win, [], {})
        self.assertIsNone(rt.press_bar)
        self.assertIsNone(rt.drag)
        self.assertEqual(list(rt.barset), [])

    def test_the_bars_are_saved_for_the_account(self):
        self.given("Combat")
        eb.replace_set(self.win, [teb.saved_set("A", "B")["bars"][1]], {})
        self.assertEqual([b["name"] for b in self.file_bars()["bars"]], ["B"])

    def test_keys_for_bars_that_are_not_there_any_more_go_and_those_for_the_new_ones_come(self):
        self.arrange()
        eb.replace_set(self.win, [teb.saved_set("Only")["bars"][0]], {"slot.b1.6": {"key": "f6", "mods": []}})
        self.assertEqual(sorted(kb.read_file(self.path)["keys"]), ["slot.b1.6"])


class StaleKeysTests(Base):
    def test_a_key_left_for_a_bar_id_the_new_set_brings_does_not_stay(self):
        self.given("Combat")
        eb.ensure_loaded(self.win)
        # a key for a bar this account does not have (another character's), kept in the file as unknown
        qk.configure_for_tests(self.path, teb.sync_submit)
        kb.write_section(self.path, "keys", {"slot.b9.2": {"key": "f2", "mods": []}, "hotkey.dock": {"key": "b", "mods": []}})
        qk.load(self.path)
        eb.reset_for_tests()
        eb._raw = {"alice": teb.saved_set("Combat")}
        self.win = teb.XWin()
        eb.ensure_loaded(self.win)
        newbar = {**teb.saved_set("Brand new")["bars"][0], "id": "b9"}
        eb.replace_set(self.win, [newbar], {})
        keys = kb.read_file(self.path)["keys"]
        self.assertNotIn("slot.b9.2", keys)
        self.assertEqual(keys["hotkey.dock"], {"key": "b", "mods": []})

    def test_bar_key_overrides_is_only_those_of_the_asked_bars(self):
        self.arrange()
        qk.set_binding("slot.b2.4", Combo(pygame.K_F4, ()))
        self.assertEqual(sorted(qk.bar_key_overrides(["b1"])), ["slot.b1.3"])
        self.assertEqual(sorted(qk.bar_key_overrides(["b1", "b2"])), ["slot.b1.3", "slot.b2.4"])
        self.assertEqual(qk.bar_key_overrides([]), {})


class RollbackTests(Base):
    def test_a_loader_that_fails_leaves_the_bars_as_they_were_and_keeps_the_old_undo(self):
        self.arrange()
        first = lo.new_loadout("First", {"main": [dict(HAULER)], "bars": [], "keys": {}})
        ops.STORE.save(first)
        ops.load(self.win, first)
        before = lo.setup_key(ops.capture(self.win))
        undo_before = copy.deepcopy(eb.runtime(self.win).undo)
        item = lo.new_loadout("Broken", {"main": [dict(MISSILE)], "bars": [teb.saved_set("X")["bars"][0]], "keys": {}})
        ops.STORE.save(item)
        real_in = self.win._hotbar_layout_in
        calls = []

        def broken(data):
            calls.append(1)
            if len(calls) == 1:
                real_in(data)                       # half way through the game's own loader...
                raise RuntimeError("the game choked")
            real_in(data)

        self.win._hotbar_layout_in = broken
        with self.assertLogs("loadout_ops", level="ERROR"):
            ok, text = ops.load(self.win, item)
        self.assertFalse(ok)
        self.assertIn("left as they were", text)
        self.assertEqual(lo.setup_key(ops.capture(self.win)), before)
        self.assertEqual(eb.runtime(self.win).undo, undo_before)

    def test_even_a_failed_rollback_does_not_raise(self):
        item = lo.new_loadout("Broken", {"main": [dict(MISSILE)], "bars": [], "keys": {}})
        ops.STORE.save(item)
        self.win._hotbar_layout_in = lambda data: (_ for _ in ()).throw(RuntimeError("always"))
        with self.assertLogs("loadout_ops", level="ERROR"):
            ok, text = ops.load(self.win, item)
        self.assertFalse(ok)

    def test_a_loadout_that_cannot_be_read_is_refused(self):
        ok, text = ops.apply(self.win, "junk")
        self.assertFalse(ok)
        self.assertIn("cannot be read", text)


class MatchTests(Base):
    def test_the_loadout_that_is_what_you_have_is_found(self):
        self.arrange()
        a = self.save("Alpha")
        self.assertEqual(ops.match(self.win)["id"], a["id"])

    def test_it_stops_matching_the_moment_you_change_a_slot_anywhere(self):
        self.arrange()
        self.save("Alpha")
        for change in (lambda: self.fill_main((7, HAULER)), lambda: self.fill_bar("b2", (0, HAULER)),
                       lambda: qk.set_binding("slot.b2.1", Combo(pygame.K_F1, ())), lambda: eb.set_visible(self.win, "b1", False),
                       lambda: eb.rename_bar(self.win, "b1", "Other"), lambda: eb.remove_bar(self.win, "b2")):
            self.arrange()
            ops.STORE.refresh()
            self.assertIsNotNone(ops.match(self.win))
            change()
            self.assertIsNone(ops.match(self.win), change)
            ops.load(self.win, ops.STORE.find("Alpha"))
            self.assertIsNotNone(ops.match(self.win), change)

    def test_nothing_matches_with_nothing_saved(self):
        self.assertIsNone(ops.match(self.win))

    def test_a_loadout_saved_by_another_character_matches_too(self):
        self.arrange()
        self.save("Alpha")
        bob = teb.XWin(username="bob")
        ops.load(bob, ops.STORE.find("Alpha"))
        self.assertEqual(ops.match(bob)["name"], "Alpha")


class CycleTests(Base):
    def setUp(self):
        super().setUp()
        for name, binding in (("Alpha", FIGHTER), ("Bravo", MISSILE), ("Charlie", HAULER)):
            ops.STORE.save(lo.new_loadout(name, {"main": [dict(binding)], "bars": [], "keys": {}}, when=1.0))

    def current(self):
        item = ops.match(self.win)
        return item["name"] if item else None

    def test_next_goes_down_the_list_and_wraps(self):
        self.assertTrue(ops.cycle(self.win, 1))
        self.assertEqual(self.current(), "Alpha")                          # none matched: the first
        ops.cycle(self.win, 1)
        self.assertEqual(self.current(), "Bravo")
        ops.cycle(self.win, 1)
        ops.cycle(self.win, 1)
        self.assertEqual(self.current(), "Alpha")

    def test_previous_goes_up_the_list_and_wraps(self):
        ops.cycle(self.win, -1)
        self.assertEqual(self.current(), "Charlie")                        # none matched: the last
        ops.cycle(self.win, -1)
        self.assertEqual(self.current(), "Bravo")
        ops.cycle(self.win, -1)
        ops.cycle(self.win, -1)
        self.assertEqual(self.current(), "Charlie")

    def test_it_says_what_it_did_in_chat(self):
        ops.cycle(self.win, 1)
        self.assertEqual(self.win.logs[-1], "Loaded 'Alpha'. UNDO brings back what you had.")

    def test_with_nothing_saved_it_says_so_and_still_uses_the_key(self):
        kb.write_section(self.path, "loadouts", {})
        ops.STORE.refresh()
        self.assertTrue(ops.cycle(self.win, 1))
        self.assertIn("No loadouts saved yet", self.win.logs[-1])

    def test_a_failed_load_is_told_too(self):
        self.win._hotbar_layout_in = lambda data: (_ for _ in ()).throw(RuntimeError("no"))
        with self.assertLogs("loadout_ops", level="ERROR"):
            self.assertTrue(ops.cycle(self.win, 1))
        self.assertIn("Could not load", self.win.logs[-1])

    def test_a_window_without_chat_just_loads(self):
        self.win.chat_log = None
        self.assertTrue(ops.cycle(self.win, 1))
        self.assertEqual(self.current(), "Alpha")


class KeyTests(Base):
    """The two loadout key actions, through the hook the game's event loop calls."""

    def setUp(self):
        super().setUp()
        for name, binding in (("Alpha", FIGHTER), ("Bravo", MISSILE)):
            ops.STORE.save(lo.new_loadout(name, {"main": [dict(binding)], "bars": [], "keys": {}}, when=1.0))

    def test_the_loadout_actions_have_no_key_until_one_is_chosen(self):
        self.assertIsNone(qk.table().combo("loadout.next"))
        event = key_event(pygame.K_F6)
        self.assertEqual(qk.on_key_down(self.win, event), (event, False))

    def test_a_key_bound_to_next_loads_the_next_loadout_and_is_used_up(self):
        qk.set_binding("loadout.next", Combo(pygame.K_F6, ()))
        qk.set_binding("loadout.prev", Combo(pygame.K_F5, ()))
        event, consumed = qk.on_key_down(self.win, key_event(pygame.K_F6))
        self.assertTrue(consumed)
        self.assertEqual(ops.match(self.win)["name"], "Alpha")
        qk.on_key_down(self.win, key_event(pygame.K_F6))
        self.assertEqual(ops.match(self.win)["name"], "Bravo")
        qk.on_key_down(self.win, key_event(pygame.K_F5))
        self.assertEqual(ops.match(self.win)["name"], "Alpha")

    def test_a_modifier_matters(self):
        qk.set_binding("loadout.next", Combo(pygame.K_F6, ("ctrl",)))
        _, consumed = qk.on_key_down(self.win, key_event(pygame.K_F6))
        self.assertFalse(consumed)
        _, consumed = qk.on_key_down(self.win, key_event(pygame.K_F6, pygame.KMOD_CTRL))
        self.assertTrue(consumed)

    def test_the_actions_are_listed_for_the_key_window_in_their_own_group(self):
        actions = [a for a in kb.CATALOG if a.group == kb.GROUP_LOADOUTS]
        self.assertEqual([a.id for a in actions], ["loadout.next", "loadout.prev"])


if __name__ == "__main__":
    unittest.main()

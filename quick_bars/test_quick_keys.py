"""Tests for quick_keys.py: what the key hook does with a key press, the slot labels, saving, and the hook's place in the game."""

from __future__ import annotations
import modloader

import inspect
import os
import re
import tempfile
import unittest
from unittest import mock

import pygame

import keybinds as kb
import quick_keys as qk
from keybinds import Combo

pygame.init()


def sync_submit(fn, *args):
    fn(*args)


def key_event(key, mod=0, unicode="", scancode=0):
    return pygame.event.Event(pygame.KEYDOWN, key=key, mod=mod, unicode=unicode, scancode=scancode)


class FakeWindow:
    """The few game methods the hook calls, recording what it asks of them."""

    def __init__(self, offset=0):
        self.offset = offset
        self.calls: list = []

    def _hotbar_shown_offset(self):
        return self.offset

    def _hotbar_activate_slot(self, slot, from_key=False):
        self.calls.append(("activate", slot, from_key))

    def _hotbar_key_hold_begin(self, slot, key):
        self.calls.append(("hold", slot, key))

    def _hotbar_page(self, step):
        self.calls.append(("page", step))


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "quick_bars.json")
        qk.reset_for_tests()
        qk.configure_for_tests(self.path, sync_submit)
        self.addCleanup(self._cleanup)
        self.win = FakeWindow()

    def _cleanup(self):
        qk.reset_for_tests()
        self._tmp.cleanup()

    def press(self, key, mod=0, win=None):
        return qk.on_key_down(win or self.win, key_event(key, mod))


class DefaultTests(Base):
    def test_with_nothing_changed_every_key_goes_to_the_game_untouched(self):
        for key in (pygame.K_r, pygame.K_1, pygame.K_e, pygame.K_b, pygame.K_F5, pygame.K_z):
            event = key_event(key, pygame.KMOD_SHIFT)
            got, consumed = qk.on_key_down(self.win, event)
            self.assertIs(got, event)
            self.assertFalse(consumed)
        self.assertEqual(self.win.calls, [])

    def test_the_labels_are_the_games_own(self):
        self.assertEqual(qk.slot_label(self.win, 0, "1"), "1")
        self.assertEqual(qk.slot_label(self.win, 9, "0"), "0")

    def test_the_bar_cache_key_gets_nothing_added(self):
        self.assertIsNone(qk.label_key(self.win, ((), None)))

    def test_the_table_is_built_on_first_use_not_at_load(self):
        self.assertIsNone(qk._table)
        qk.table()
        self.assertIsNotNone(qk._table)


class HotkeyTests(Base):
    def test_a_rebound_hotkey_comes_back_as_the_key_the_game_knows_it_by(self):
        qk.set_binding("hotkey.dock", Combo(pygame.K_b))
        event = key_event(pygame.K_b, pygame.KMOD_LSHIFT, unicode="b", scancode=5)
        got, consumed = qk.on_key_down(self.win, event)
        self.assertFalse(consumed)
        self.assertEqual(got.type, pygame.KEYDOWN)
        self.assertEqual(got.key, pygame.K_r)
        self.assertEqual(got.mod, pygame.KMOD_LSHIFT)         # Shift+Z-style reversals keep working
        self.assertEqual((got.unicode, got.scancode), ("b", 5))
        self.assertEqual(event.key, pygame.K_b)                # the original is not changed
        self.assertEqual(self.win.calls, [])

    def test_the_old_key_of_a_moved_hotkey_does_nothing(self):
        qk.set_binding("hotkey.dock", Combo(pygame.K_b))
        event = key_event(pygame.K_r)
        got, consumed = qk.on_key_down(self.win, event)
        self.assertTrue(consumed)

    def test_an_unbound_hotkey_does_nothing(self):
        qk.set_binding("hotkey.dock", None)
        self.assertTrue(self.press(pygame.K_r)[1])
        self.assertFalse(self.press(pygame.K_e)[1])             # others are untouched

    def test_a_hotkey_that_still_has_its_key_goes_through_as_it_is(self):
        qk.set_binding("hotkey.dock", Combo(pygame.K_b))
        event = key_event(pygame.K_e)
        got, consumed = qk.on_key_down(self.win, event)
        self.assertIs(got, event)
        self.assertFalse(consumed)

    def test_a_hotkey_with_modifiers_needs_them(self):
        qk.set_binding("hotkey.dock", Combo(pygame.K_b, {"ctrl"}))
        self.assertEqual(self.press(pygame.K_b, pygame.KMOD_LCTRL)[0].key, pygame.K_r)
        got, consumed = self.press(pygame.K_b)
        self.assertEqual(got.key, pygame.K_b)                   # plain B is not bound: the game gets it as B (it does nothing)
        self.assertFalse(consumed)
        self.assertTrue(self.press(pygame.K_r)[1])              # and R, the old key, is dead

    def test_two_hotkeys_can_swap_keys(self):
        qk.set_binding("hotkey.dock", Combo(pygame.K_e))
        qk.set_binding("hotkey.cycle_weapon", Combo(pygame.K_r))
        self.assertEqual(self.press(pygame.K_e)[0].key, pygame.K_r)        # E now docks: the game is handed R
        self.assertEqual(self.press(pygame.K_r)[0].key, pygame.K_e)        # R now cycles the weapon: the game is handed E


class SlotTests(Base):
    def test_a_slot_key_fires_that_slot_of_the_tab_shown_with_the_physical_key(self):
        qk.set_binding("slot.main.3", Combo(pygame.K_F3))
        got, consumed = self.press(pygame.K_F3)
        self.assertTrue(consumed)
        self.assertEqual(self.win.calls, [("activate", 2, True), ("hold", 2, pygame.K_F3)])

    def test_the_slot_is_in_the_tab_the_bar_shows(self):
        qk.set_binding("slot.main.3", Combo(pygame.K_F3))
        win = FakeWindow(offset=10)
        self.press(pygame.K_F3, win=win)
        self.assertEqual(win.calls, [("activate", 12, True), ("hold", 12, pygame.K_F3)])

    def test_the_default_number_keys_still_work_once_something_else_changed(self):
        qk.set_binding("hotkey.dock", Combo(pygame.K_b))
        self.assertTrue(self.press(pygame.K_1)[1])
        self.assertEqual(self.win.calls, [("activate", 0, True), ("hold", 0, pygame.K_1)])
        self.win.calls.clear()
        self.assertTrue(self.press(pygame.K_0, pygame.KMOD_SHIFT)[1])      # "Shift or not", as the game does
        self.assertEqual(self.win.calls[0], ("activate", 9, True))

    def test_a_modified_slot_key_does_not_fire_the_plain_one(self):
        qk.set_binding("slot.main.1", Combo(pygame.K_1, {"shift"}))
        qk.set_binding("slot.main.2", Combo(pygame.K_1))              # takes plain 1 from slot 1... which is rebound anyway
        self.press(pygame.K_1, pygame.KMOD_LSHIFT)
        self.assertEqual(self.win.calls[0], ("activate", 0, True))
        self.win.calls.clear()
        self.press(pygame.K_1)
        self.assertEqual(self.win.calls[0], ("activate", 1, True))

    def test_an_unbound_slot_key_does_nothing(self):
        qk.set_binding("slot.main.1", None)
        got, consumed = self.press(pygame.K_1)
        self.assertTrue(consumed)
        self.assertEqual(self.win.calls, [])

    def test_a_game_without_the_bar_methods_is_left_alone(self):
        qk.set_binding("slot.main.3", Combo(pygame.K_F3))
        event = key_event(pygame.K_F3)
        got, consumed = qk.on_key_down(object(), event)
        self.assertIs(got, event)
        self.assertFalse(consumed)

    def test_tab_keys_switch_the_tab(self):
        qk.set_binding("tab.next", Combo(pygame.K_F5))
        qk.set_binding("tab.prev", Combo(pygame.K_F6, {"shift"}))
        self.assertTrue(self.press(pygame.K_F5)[1])
        self.assertTrue(self.press(pygame.K_F6, pygame.KMOD_SHIFT)[1])
        self.assertEqual(self.win.calls, [("page", 1), ("page", -1)])
        self.assertFalse(self.press(pygame.K_F6)[1])                    # without Shift it is not that binding

    def test_a_tab_key_needs_the_bars_page_method(self):
        qk.set_binding("tab.next", Combo(pygame.K_F5))
        self.assertFalse(qk.on_key_down(object(), key_event(pygame.K_F5))[1])


class SwitchTests(Base):
    def test_the_env_switch_turns_it_off(self):
        qk.set_binding("hotkey.dock", Combo(pygame.K_b))
        with mock.patch.dict(os.environ, {qk.ENV_OFF: "1"}):
            event = key_event(pygame.K_r)
            self.assertEqual(qk.on_key_down(self.win, event), (event, False))
            self.assertEqual(qk.slot_label(self.win, 0, "1"), "1")
            self.assertIsNone(qk.label_key(self.win, ((), None)))

    def test_an_error_switches_it_off_and_lets_the_key_through(self):
        qk.set_binding("slot.main.3", Combo(pygame.K_F3))

        class Broken(FakeWindow):
            def _hotbar_activate_slot(self, slot, from_key=False):
                raise RuntimeError("boom")

        event = key_event(pygame.K_F3)
        with self.assertLogs(qk.logger, "ERROR"):
            self.assertEqual(qk.on_key_down(Broken(), event), (event, False))
        self.assertFalse(qk.enabled())
        self.assertEqual(qk.on_key_down(self.win, key_event(pygame.K_r)), (mock.ANY, False))


class LabelTests(Base):
    def test_a_slot_shows_its_combo(self):
        qk.set_binding("slot.main.1", Combo(pygame.K_F1))
        qk.set_binding("slot.main.2", Combo(pygame.K_1, {"ctrl"}))
        qk.set_binding("slot.main.3", None)
        self.assertEqual(qk.slot_label(self.win, 0, "1"), "F1")
        self.assertEqual(qk.slot_label(self.win, 1, "2"), "c1")
        self.assertEqual(qk.slot_label(self.win, 2, "3"), "")
        self.assertEqual(qk.slot_label(self.win, 3, "4"), "4")           # an untouched slot still shows its number

    def test_the_bar_cache_key_gets_the_ten_labels_when_something_changed(self):
        qk.set_binding("slot.main.1", Combo(pygame.K_F1))
        got = qk.label_key(self.win, (("slots",), "hover"))
        self.assertEqual(got[:2], (("slots",), "hover"))
        self.assertEqual(len(got[2]), 10)
        self.assertEqual(got[2][0], "F1")
        self.assertEqual(got[2][1], "2")

    def test_a_changed_label_changes_the_key(self):
        qk.set_binding("slot.main.1", Combo(pygame.K_F1))
        before = qk.label_key(self.win, ((), None))
        qk.set_binding("slot.main.1", Combo(pygame.K_F2))
        self.assertNotEqual(before, qk.label_key(self.win, ((), None)))

    def test_the_hook_goes_on_the_games_method_and_only_changes_what_it_returns_after_a_change(self):
        class Window:
            def _hotbar_drawn_state(self, hover_slot, tooltip=True):
                return ("slots", "hover")

        self.assertEqual(qk.install(Window), [])
        window = Window()
        self.assertEqual(window._hotbar_drawn_state(1), ("slots", "hover"))
        qk.set_binding("slot.main.1", Combo(pygame.K_F1))
        got = window._hotbar_drawn_state(1, tooltip=False)
        self.assertEqual(got[:2], ("slots", "hover"))
        self.assertEqual(got[2][0], "F1")


class SaveTests(Base):
    def test_a_change_is_saved_and_read_back(self):
        qk.set_binding("hotkey.dock", Combo(pygame.K_b, {"ctrl"}))
        qk.set_binding("hotkey.missions", None)
        self.assertEqual(kb.read_file(self.path)["keys"],
                         {"hotkey.dock": {"key": "b", "mods": ["ctrl"]}, "hotkey.missions": None})
        qk.load(self.path)
        self.assertEqual(qk.table().combo("hotkey.dock"), Combo(pygame.K_b, {"ctrl"}))
        self.assertIsNone(qk.table().combo("hotkey.missions"))

    def test_displacing_another_action_saves_both(self):
        displaced = qk.set_binding("hotkey.dock", Combo(pygame.K_e))
        self.assertEqual([a.id for a in displaced], ["hotkey.cycle_weapon"])
        self.assertEqual(sorted(kb.read_file(self.path)["keys"]), ["hotkey.cycle_weapon", "hotkey.dock"])

    def test_reset_removes_the_change_from_the_file(self):
        qk.set_binding("hotkey.dock", Combo(pygame.K_b))
        qk.reset_binding("hotkey.dock")
        self.assertEqual(kb.read_file(self.path)["keys"], {})
        qk.set_binding("hotkey.dock", Combo(pygame.K_b))
        qk.set_binding("tab.next", Combo(pygame.K_F5))
        qk.reset_all()
        self.assertEqual(kb.read_file(self.path)["keys"], {})
        self.assertTrue(qk.table().is_default)

    def test_the_other_sections_of_the_file_survive_a_save(self):
        kb.write_section(self.path, "loadouts", {"a": 1})
        qk.set_binding("hotkey.dock", Combo(pygame.K_b))
        self.assertEqual(kb.read_file(self.path)["loadouts"], {"a": 1})

    def test_a_broken_or_missing_file_gives_the_defaults(self):
        qk.load(os.path.join(self._tmp.name, "nothing.json"))
        self.assertTrue(qk.table().is_default)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{broken")
        qk.load(self.path)
        self.assertTrue(qk.table().is_default)

    def test_a_hand_edited_file_with_junk_still_loads_what_it_can(self):
        kb.write_section(self.path, "keys", {"hotkey.dock": {"key": "b"}, "hotkey.zzz": 5, "tab.next": {"key": "escape"}})
        qk.load(self.path)
        self.assertEqual(qk.table().combo("hotkey.dock"), Combo(pygame.K_b))
        self.assertIsNone(qk.table().combo("tab.next"))


class RealGameTests(unittest.TestCase):
    """The game's own code, as the patch and the catalog rely on it."""

    @classmethod
    def setUpClass(cls):
        import Client
        cls.window_cls = Client.SolarSystemWindow
        cls.client = modloader.game_source("Client.py")

    def test_the_game_has_the_methods_the_hook_calls_with_the_arguments_it_gives_them(self):
        cls = self.window_cls
        for name in ("_hotbar_shown_offset", "_hotbar_activate_slot", "_hotbar_key_hold_begin", "_hotbar_page",
                     "_hotbar_drawn_state"):
            self.assertTrue(hasattr(cls, name), name)
        self.assertEqual(list(inspect.signature(cls._hotbar_activate_slot).parameters), ["self", "slot_idx", "from_key"])
        self.assertEqual(list(inspect.signature(cls._hotbar_key_hold_begin).parameters), ["self", "slot_idx", "key"])
        self.assertEqual(list(inspect.signature(cls._hotbar_page).parameters), ["self", "step"])

    def test_the_catalogs_slot_keys_are_the_games_own(self):
        window = self.window_cls.__new__(self.window_cls)
        window._init_hotbar_slots()
        for action in kb.CATALOG:
            if action.kind == "slot":
                self.assertEqual(window._hotbar_key_to_slot[action.default.key], action.arg, action.id)
        self.assertEqual(len(window._hotbar_key_to_slot), 10)

    def test_the_hook_sits_right_after_the_repeat_guard_and_before_the_first_hotkey(self):
        guard = "                    self._keys_held.add(event.key)\n"
        hook = "event, _qb_consumed = quick_keys.on_key_down(self, event)"
        self.assertEqual(self.client.count(hook), 1)
        at = self.client.index(hook)
        before = self.client[:at]
        guard_at = before.rindex(guard)
        self.assertLess(at - guard_at, 700)                              # nothing but my comment between them
        after = self.client[at:at + 400]
        self.assertIn("if _qb_consumed:\n                        continue", after)
        self.assertLess(at, self.client.index("if event.key == pygame.K_SPACE:", at))

    def test_the_hook_is_inside_the_key_down_branch_of_the_event_loop(self):
        """Text alone cannot show where a line runs: walk the syntax tree from the hook up to the loop it continues."""
        import ast
        tree = ast.parse(self.client)
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        hooks = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                 and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute)
                 and n.value.func.attr == "on_key_down"]
        self.assertEqual(len(hooks), 1)
        chain, node = [], hooks[0]
        while node in parents:
            node = parents[node]
            chain.append(node)
        tests = [ast.unparse(n.test) for n in chain if isinstance(n, ast.If)]
        self.assertTrue(any("pygame.KEYDOWN" in t for t in tests), tests)
        self.assertTrue(any(isinstance(n, ast.For) for n in chain))          # the loop its ``continue`` belongs to
        after = parents[hooks[0]].body[parents[hooks[0]].body.index(hooks[0]) + 1]
        self.assertIsInstance(after, ast.If)
        self.assertIsInstance(after.body[0], ast.Continue)

    def test_the_label_hook_is_in_the_bar_renderer(self):
        source = modloader.game_source("render_mixin.py")
        self.assertEqual(source.count("quick_keys.slot_label(self, _hb_i,"), 1)
        self.assertNotIn('_hb_num_txt = str(_hb_i + 1) if _hb_i < 9 else "0"\n', source)

    def test_every_one_press_hotkey_of_the_game_is_in_the_catalog(self):
        """A game update that adds a hotkey to the chain (or removes one) says so here: add it to keybinds.HOTKEYS."""
        start = self.client.index("event, _qb_consumed = quick_keys.on_key_down(self, event)")
        end = self.client.index("elif event.type == pygame.KEYUP:", start)
        region = self.client[start:end]
        heads = re.findall(r"^ {20}(?:el)?if event\.key == pygame\.K_(\w+):", region, flags=re.M)
        # (Space, Esc ... are the game's own keys, the reserved ones: they have branches in the chain but cannot be bound)
        in_game = {kb.key_name(getattr(pygame, "K_" + name)) for name in heads
                   if getattr(pygame, "K_" + name) not in kb.RESERVED_KEYS}
        catalog = {kb.key_name(a.default.key) for a in kb.CATALOG if a.kind == "hotkey"}
        self.assertEqual(in_game, catalog)
        self.assertIn("elif event.key in self._hotbar_key_to_slot:", region)

    def test_every_catalogued_hotkey_default_is_a_branch_the_game_runs_after_the_hook(self):
        start = self.client.index("quick_keys.on_key_down(self, event)")
        end = self.client.index("elif event.type == pygame.KEYUP:", start)
        region = self.client[start:end]
        for action in kb.CATALOG:
            if action.kind == "hotkey":
                self.assertIn(f"event.key == pygame.K_{kb.key_name(action.default.key)}:", region, action.id)


if __name__ == "__main__":
    unittest.main()

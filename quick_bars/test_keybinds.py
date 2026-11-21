"""Tests for keybinds.py: combos, the action catalog, how a key press is matched, conflicts, and the saved file."""

from __future__ import annotations
import modloader

import json
import os
import tempfile
import unittest

import pygame

import keybinds as kb
from keybinds import Combo

CTRL, SHIFT, ALT = pygame.KMOD_CTRL, pygame.KMOD_SHIFT, pygame.KMOD_ALT


class ComboTests(unittest.TestCase):
    def test_modifiers_come_from_the_event_mask(self):
        self.assertEqual(kb.mods_from_event(0), frozenset())
        self.assertEqual(kb.mods_from_event(pygame.KMOD_LCTRL), {"ctrl"})
        self.assertEqual(kb.mods_from_event(pygame.KMOD_RSHIFT | pygame.KMOD_LALT), {"shift", "alt"})
        self.assertEqual(kb.mods_from_event(CTRL | SHIFT | ALT), {"ctrl", "shift", "alt"})

    def test_caps_lock_num_lock_and_the_windows_key_do_not_count(self):
        self.assertEqual(kb.mods_from_event(pygame.KMOD_CAPS | pygame.KMOD_NUM | pygame.KMOD_LMETA), frozenset())

    def test_a_modifier_key_on_its_own_is_not_a_combo(self):
        for key in (pygame.K_LSHIFT, pygame.K_RCTRL, pygame.K_LALT, pygame.K_CAPSLOCK):
            self.assertIsNone(kb.combo_from_event(key, 0))
        self.assertIsNone(kb.combo_from_event(0, 0))
        self.assertEqual(kb.combo_from_event(pygame.K_r, CTRL), Combo(pygame.K_r, {"ctrl"}))

    def test_combos_compare_and_hash_by_key_and_modifiers(self):
        self.assertEqual(Combo(pygame.K_r, {"ctrl"}), Combo(pygame.K_r, frozenset(["ctrl"])))
        self.assertNotEqual(Combo(pygame.K_r), Combo(pygame.K_r, {"ctrl"}))
        self.assertEqual(len({Combo(pygame.K_r), Combo(pygame.K_r, set())}), 1)

    def test_display_text(self):
        self.assertEqual(kb.format_combo(None), "-")
        self.assertEqual(kb.format_combo(Combo(pygame.K_r)), "R")
        self.assertEqual(kb.format_combo(Combo(pygame.K_1, {"shift", "ctrl"})), "Ctrl+Shift+1")
        self.assertEqual(kb.format_combo(Combo(pygame.K_F5, {"alt"})), "Alt+F5")
        self.assertEqual(kb.format_combo(Combo(pygame.K_KP1)), "Keypad 1")
        self.assertEqual(kb.format_combo(Combo(pygame.K_HOME)), "Home")

    def test_the_short_label_fits_a_slot_key_tab(self):
        self.assertEqual(kb.short_label(None), "")
        self.assertEqual(kb.short_label(Combo(pygame.K_q)), "Q")
        self.assertEqual(kb.short_label(Combo(pygame.K_1)), "1")
        self.assertEqual(kb.short_label(Combo(pygame.K_F5)), "F5")
        self.assertEqual(kb.short_label(Combo(pygame.K_1, {"ctrl"})), "c1")
        self.assertEqual(kb.short_label(Combo(pygame.K_1, {"shift"})), "s1")
        self.assertEqual(kb.short_label(Combo(pygame.K_1, {"alt"})), "a1")
        self.assertEqual(kb.short_label(Combo(pygame.K_1, {"ctrl", "shift"})), "cs1")
        self.assertEqual(kb.short_label(Combo(pygame.K_KP5)), "5")
        for combo in (Combo(pygame.K_HOME), Combo(pygame.K_PAGEDOWN, {"ctrl", "shift", "alt"}), Combo(pygame.K_INSERT)):
            self.assertLessEqual(len(kb.short_label(combo)), 6)

    def test_json_round_trip(self):
        for combo in (Combo(pygame.K_r), Combo(pygame.K_1, {"ctrl", "alt"}), Combo(pygame.K_F5, {"shift"}),
                      Combo(pygame.K_KP1), Combo(pygame.K_HOME), Combo(pygame.K_BACKQUOTE)):
            data = kb.combo_to_json(combo)
            self.assertEqual(kb.combo_from_json(json.loads(json.dumps(data))), combo, data)
        self.assertIsNone(kb.combo_to_json(None))

    def test_json_modifiers_are_in_a_fixed_order(self):
        self.assertEqual(kb.combo_to_json(Combo(pygame.K_1, {"alt", "ctrl", "shift"}))["mods"], ["ctrl", "shift", "alt"])

    def test_unusable_json_is_refused(self):
        for bad in (None, "r", 5, [], {}, {"key": 5}, {"key": "nonsense key"}, {"key": ""}, {"key": "r", "mods": "ctrl"},
                    {"key": "r", "mods": ["hyper"]}, {"key": "r", "mods": [None]},
                    {"key": "escape"}, {"key": "space"}, {"key": "w"}, {"key": "left shift"}, {"key": "tab"}):
            self.assertIsNone(kb.combo_from_json(bad), bad)


class CatalogTests(unittest.TestCase):
    def test_ids_are_unique_and_every_action_has_a_label_and_a_known_kind(self):
        ids = [a.id for a in kb.CATALOG]
        self.assertEqual(len(ids), len(set(ids)))
        for a in kb.CATALOG:
            self.assertTrue(a.label.strip())
            self.assertIn(a.kind, ("hotkey", "slot", "tab"))
            self.assertIn(a.group, (kb.GROUP_BAR, kb.GROUP_HOTKEYS))

    def test_the_defaults_are_the_games_own_and_none_clash(self):
        defaults = [a.default for a in kb.CATALOG if a.default is not None]
        self.assertEqual(len(defaults), len(set(defaults)))
        self.assertEqual(kb.BY_ID["slot.main.1"].default, Combo(pygame.K_1))
        self.assertEqual(kb.BY_ID["slot.main.10"].default, Combo(pygame.K_0))
        self.assertEqual(kb.BY_ID["hotkey.dock"].default, Combo(pygame.K_r))
        self.assertEqual(kb.BY_ID["hotkey.autopilot"].default, Combo(pygame.K_v))
        self.assertIsNone(kb.BY_ID["tab.next"].default)

    def test_no_default_is_a_reserved_or_modifier_key(self):
        for a in kb.CATALOG:
            if a.default is not None:
                self.assertNotIn(a.default.key, kb.RESERVED_KEYS, a.id)
                self.assertNotIn(a.default.key, kb.MODIFIER_KEYS, a.id)

    def test_every_default_key_round_trips_through_its_name(self):
        for a in kb.CATALOG:
            if a.default is not None:
                self.assertEqual(kb.combo_from_json(kb.combo_to_json(a.default)), a.default, a.id)

    def test_there_is_a_slot_action_per_slot_and_it_knows_its_index(self):
        slots = [a for a in kb.CATALOG if a.kind == "slot"]
        self.assertEqual([a.arg for a in slots], list(range(10)))
        self.assertEqual([a.id for a in slots], [kb.slot_id(i) for i in range(10)])
        self.assertEqual([a.arg for a in kb.CATALOG if a.kind == "tab"], [1, -1])

    def test_the_default_keys_are_exactly_the_defaults_of_the_catalog(self):
        self.assertEqual(kb.DEFAULT_KEYS, {a.default.key for a in kb.CATALOG if a.default is not None})
        self.assertIn(pygame.K_r, kb.DEFAULT_KEYS)
        self.assertNotIn(pygame.K_b, kb.DEFAULT_KEYS)


class ResolveTests(unittest.TestCase):
    def test_a_default_table_resolves_every_default_key_to_its_action(self):
        table = kb.Table()
        self.assertTrue(table.is_default)
        for a in kb.CATALOG:
            if a.default is not None:
                self.assertEqual(table.resolve(a.default.key, frozenset()).action, a, a.id)

    def test_a_key_the_catalog_does_not_use_is_left_to_the_game(self):
        got = kb.Table().resolve(pygame.K_b, frozenset())
        self.assertIsNone(got.action)
        self.assertFalse(got.swallow)

    def test_a_plain_binding_ignores_the_modifiers_held(self):
        """The game's hotkeys always did: Shift+Z is Z run backwards, Ctrl+1 fires slot 1."""
        table = kb.Table()
        for mods in (frozenset(), {"shift"}, {"ctrl"}, {"ctrl", "shift", "alt"}):
            self.assertEqual(table.resolve(pygame.K_z, frozenset(mods)).action.id, "hotkey.next_ship")
            self.assertEqual(table.resolve(pygame.K_1, frozenset(mods)).action.id, "slot.main.1")

    def test_a_binding_with_modifiers_needs_exactly_those(self):
        table = kb.Table({"hotkey.dock": Combo(pygame.K_b, {"ctrl"})})
        self.assertEqual(table.resolve(pygame.K_b, {"ctrl"}).action.id, "hotkey.dock")
        self.assertIsNone(table.resolve(pygame.K_b, frozenset()).action)
        self.assertIsNone(table.resolve(pygame.K_b, {"ctrl", "shift"}).action)
        self.assertIsNone(table.resolve(pygame.K_b, {"shift"}).action)

    def test_the_most_specific_binding_wins(self):
        table = kb.Table({"hotkey.dock": Combo(pygame.K_1, {"shift"})})
        self.assertEqual(table.resolve(pygame.K_1, {"shift"}).action.id, "hotkey.dock")
        self.assertEqual(table.resolve(pygame.K_1, frozenset()).action.id, "slot.main.1")
        self.assertEqual(table.resolve(pygame.K_1, {"ctrl"}).action.id, "slot.main.1")        # no exact one: the plain one

    def test_the_old_key_of_an_action_that_moved_is_swallowed(self):
        table = kb.Table({"hotkey.dock": Combo(pygame.K_b)})
        self.assertEqual(table.resolve(pygame.K_b, frozenset()).action.id, "hotkey.dock")
        got = table.resolve(pygame.K_r, frozenset())
        self.assertIsNone(got.action)
        self.assertTrue(got.swallow)

    def test_an_unbound_action_swallows_its_key(self):
        table = kb.Table({"hotkey.dock": None})
        self.assertTrue(table.resolve(pygame.K_r, frozenset()).swallow)
        self.assertTrue(table.resolve(pygame.K_r, {"shift"}).swallow)

    def test_two_actions_can_trade_keys(self):
        table = kb.Table({"hotkey.dock": Combo(pygame.K_e), "hotkey.cycle_weapon": Combo(pygame.K_r)})
        self.assertEqual(table.resolve(pygame.K_r, frozenset()).action.id, "hotkey.cycle_weapon")
        self.assertEqual(table.resolve(pygame.K_e, frozenset()).action.id, "hotkey.dock")

    def test_a_key_that_is_free_again_is_no_longer_swallowed_when_another_action_has_it(self):
        table = kb.Table({"hotkey.dock": Combo(pygame.K_b), "hotkey.missions": Combo(pygame.K_r)})
        self.assertEqual(table.resolve(pygame.K_r, frozenset()).action.id, "hotkey.missions")

    def test_a_new_binding_on_a_non_default_key_works_and_does_not_swallow_its_neighbours(self):
        table = kb.Table({"tab.next": Combo(pygame.K_F5), "tab.prev": Combo(pygame.K_F6, {"shift"})})
        self.assertEqual(table.resolve(pygame.K_F5, frozenset()).action.arg, 1)
        self.assertEqual(table.resolve(pygame.K_F6, {"shift"}).action.arg, -1)
        self.assertIsNone(table.resolve(pygame.K_F7, frozenset()).action)
        self.assertFalse(table.resolve(pygame.K_F7, frozenset()).swallow)


class TableEditTests(unittest.TestCase):
    def test_set_binds_and_reports_nobody_displaced(self):
        table = kb.Table()
        self.assertEqual(table.set("hotkey.dock", Combo(pygame.K_b)), [])
        self.assertEqual(table.combo("hotkey.dock"), Combo(pygame.K_b))
        self.assertFalse(table.is_default)
        self.assertTrue(table.is_changed("hotkey.dock"))
        self.assertFalse(table.is_changed("hotkey.galaxy_map"))

    def test_a_combo_held_by_another_action_is_taken_from_it(self):
        table = kb.Table()
        displaced = table.set("hotkey.dock", Combo(pygame.K_e))
        self.assertEqual([a.id for a in displaced], ["hotkey.cycle_weapon"])
        self.assertIsNone(table.combo("hotkey.cycle_weapon"))
        self.assertEqual(table.resolve(pygame.K_e, frozenset()).action.id, "hotkey.dock")

    def test_the_same_key_with_other_modifiers_is_not_a_conflict(self):
        table = kb.Table()
        self.assertEqual(table.set("hotkey.dock", Combo(pygame.K_e, {"ctrl"})), [])
        self.assertEqual(table.combo("hotkey.cycle_weapon"), Combo(pygame.K_e))

    def test_setting_an_action_to_its_own_combo_changes_nothing(self):
        table = kb.Table()
        self.assertEqual(table.set("hotkey.dock", Combo(pygame.K_r)), [])
        self.assertTrue(table.is_default)
        self.assertEqual(table.overrides, {})

    def test_unbinding(self):
        table = kb.Table()
        table.set("hotkey.dock", None)
        self.assertIsNone(table.combo("hotkey.dock"))
        self.assertEqual(table.overrides, {"hotkey.dock": None})

    def test_a_reserved_key_cannot_be_bound(self):
        table = kb.Table()
        for key in (pygame.K_ESCAPE, pygame.K_w, pygame.K_SPACE, pygame.K_TAB, pygame.K_RETURN, pygame.K_LSHIFT):
            with self.assertRaises(ValueError):
                table.set("hotkey.dock", Combo(key))
        self.assertTrue(table.is_default)

    def test_an_unknown_action_is_refused(self):
        with self.assertRaises(ValueError):
            kb.Table().set("hotkey.nonsense", Combo(pygame.K_b))

    def test_reset_returns_the_default_and_unbinds_whoever_took_it(self):
        table = kb.Table()
        table.set("hotkey.dock", Combo(pygame.K_b))
        table.set("hotkey.missions", Combo(pygame.K_r))             # took dock's old key
        table.reset("hotkey.dock")
        self.assertEqual(table.combo("hotkey.dock"), Combo(pygame.K_r))
        self.assertIsNone(table.combo("hotkey.missions"))
        self.assertEqual(table.resolve(pygame.K_r, frozenset()).action.id, "hotkey.dock")

    def test_reset_all(self):
        table = kb.Table()
        table.set("hotkey.dock", Combo(pygame.K_b))
        table.set("tab.next", Combo(pygame.K_F5))
        table.reset_all()
        self.assertTrue(table.is_default)
        self.assertEqual(table.overrides, {})
        self.assertEqual(table.resolve(pygame.K_r, frozenset()).action.id, "hotkey.dock")

    def test_only_changes_are_kept_as_overrides(self):
        table = kb.Table()
        table.set("hotkey.dock", Combo(pygame.K_b))
        table.set("hotkey.dock", Combo(pygame.K_r))                  # back to the default
        self.assertEqual(table.overrides, {})

    def test_a_table_built_from_overrides_ignores_unknown_ids_and_junk(self):
        table = kb.Table({"hotkey.nonsense": Combo(pygame.K_b), "hotkey.dock": "R", "tab.next": Combo(pygame.K_F5)})
        self.assertEqual(table.overrides, {"tab.next": Combo(pygame.K_F5)})
        self.assertEqual(table.combo("hotkey.dock"), Combo(pygame.K_r))


class JsonTests(unittest.TestCase):
    def test_only_changes_are_saved(self):
        table = kb.Table()
        self.assertEqual(table.to_json(), {})
        table.set("hotkey.dock", Combo(pygame.K_b, {"ctrl"}))
        table.set("hotkey.missions", None)
        self.assertEqual(table.to_json(), {"hotkey.dock": {"key": "b", "mods": ["ctrl"]}, "hotkey.missions": None})

    def test_round_trip(self):
        table = kb.Table()
        table.set("hotkey.dock", Combo(pygame.K_b, {"ctrl"}))
        table.set("hotkey.missions", None)
        table.set("slot.main.3", Combo(pygame.K_F3, {"shift"}))
        again = kb.Table.from_json(json.loads(json.dumps(table.to_json())))
        self.assertEqual(again.bindings, table.bindings)
        self.assertEqual(again.overrides, table.overrides)

    def test_junk_entries_are_skipped_and_the_rest_kept(self):
        got = kb.Table.from_json({"hotkey.dock": {"key": "b"}, "hotkey.missions": "nope", "hotkey.zzz": {"key": "c"},
                                  "tab.next": {"key": "escape"}, "slot.main.1": {"key": "f1", "mods": ["bogus"]}})
        self.assertEqual(got.combo("hotkey.dock"), Combo(pygame.K_b))
        self.assertEqual(got.combo("hotkey.missions"), Combo(pygame.K_j))      # the default stays
        self.assertIsNone(got.combo("tab.next"))
        self.assertEqual(got.combo("slot.main.1"), Combo(pygame.K_1))

    def test_not_a_dict_gives_the_defaults(self):
        for junk in (None, [], "x", 5):
            self.assertTrue(kb.Table.from_json(junk).is_default)

    def test_two_actions_saved_with_one_combo_keep_the_first_in_catalog_order(self):
        got = kb.Table.from_json({"hotkey.dock": {"key": "b"}, "hotkey.missions": {"key": "b"}})
        holders = [a.id for a in kb.CATALOG if got.combo(a.id) == Combo(pygame.K_b)]
        self.assertEqual(holders, ["hotkey.dock"])
        self.assertIsNone(got.combo("hotkey.missions"))

    def test_an_action_saved_with_a_default_that_another_action_also_defaults_to_is_deduplicated(self):
        got = kb.Table.from_json({"hotkey.dock": {"key": "e"}})
        holders = [a.id for a in kb.CATALOG if got.combo(a.id) == Combo(pygame.K_e)]
        self.assertEqual(len(holders), 1)


class FileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "sub", "quick_bars.json")

    def test_a_missing_or_broken_file_reads_as_empty(self):
        self.assertEqual(kb.read_file(self.path), {})
        os.makedirs(os.path.dirname(self.path))
        for text in ("{nope", "[1, 2]", ""):
            with open(self.path, "w", encoding="utf-8") as handle:
                handle.write(text)
            self.assertEqual(kb.read_file(self.path), {})

    def test_writing_a_section_keeps_the_others(self):
        kb.write_section(self.path, "keys", {"hotkey.dock": None})
        kb.write_section(self.path, "bars", {"x": 1})
        kb.write_section(self.path, "keys", {"hotkey.missions": None})
        data = kb.read_file(self.path)
        self.assertEqual(data["keys"], {"hotkey.missions": None})
        self.assertEqual(data["bars"], {"x": 1})
        self.assertEqual(data["version"], 1)
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["quick_bars.json"])

    def test_two_clients_each_write_their_own_section(self):
        """The second client read the file after the first one had saved its section: both are in it."""
        kb.write_section(self.path, "keys", {"hotkey.dock": None})
        kb.write_section(self.path, "loadouts", {"a": 1})
        self.assertEqual(sorted(kb.read_file(self.path)), ["keys", "loadouts", "version"])

    def test_the_default_path_is_next_to_the_client(self):
        self.assertEqual(os.path.basename(kb.default_path()), "quick_bars.json")
        self.assertEqual(os.path.dirname(kb.default_path()), str(modloader.REPO_DIR))


if __name__ == "__main__":
    unittest.main()

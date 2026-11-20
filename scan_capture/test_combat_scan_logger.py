import json
import tempfile
import unittest
from pathlib import Path

import combat_scan_logger
from combat_scan_logger import (load_combat_scan, record_inventory_items,
                                 record_ship_plugin_data, record_ship_specs,
                                 record_skills_data, record_turret_state)


class _ResetsDedupCache(unittest.TestCase):
    """Base for any test touching a recorder that skips a repeat via combat_scan_logger's own
    module-level, per-process ``_last_seen`` cache (see its own docstring) -- that cache is
    deliberately NOT scoped to a single call or file, so different test methods sharing the same
    fixture data (e.g. two tests both calling ``self._specs()`` with its own default args) would
    otherwise silently contaminate each other depending on run order, exactly the "did this
    already run once" state the cache exists to track in the first place."""

    def setUp(self):
        combat_scan_logger._last_seen.clear()


class RecordInventoryItemsTests(unittest.TestCase):
    def test_one_row_per_distinct_item_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_inventory_items(
                [{"item_type": "pulse_laser", "stats": {"Damage": "42.5"}},
                 {"item_type": "auto_cannon", "stats": {"Damage": "18.0"}}],
                out_path=path)
            rows = load_combat_scan(path)
            self.assertEqual(len(rows), 2)
            by_key = {r["key"]: r["data"] for r in rows}
            self.assertEqual(by_key["pulse_laser"]["stats"]["Damage"], "42.5")
            self.assertEqual(by_key["auto_cannon"]["stats"]["Damage"], "18.0")

    def test_reseeing_the_same_item_type_overwrites_rather_than_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_inventory_items(
                [{"item_type": "pulse_laser", "stats": {"Damage": "42.5"}}], out_path=path)
            record_inventory_items(
                [{"item_type": "pulse_laser", "stats": {"Damage": "50.0"}}], out_path=path)
            rows = load_combat_scan(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["data"]["stats"]["Damage"], "50.0")

    def test_reseeing_byte_identical_data_does_not_rewrite_the_row(self):
        # Same fix as every other recorder here: re-seeing the exact same item (e.g. just glancing
        # at your inventory again) shouldn't cost a real file rewrite. Proven via captured_at,
        # which only ever gets refreshed on an actual write -- an unchanged captured_at directly
        # shows the second call took the skip-write path, not just that the visible data matches.
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_inventory_items(
                [{"item_type": "pulse_laser", "stats": {"Damage": "42.5"}}], out_path=path)
            first_captured_at = load_combat_scan(path)[0]["captured_at"]
            record_inventory_items(
                [{"item_type": "pulse_laser", "stats": {"Damage": "42.5"}}], out_path=path)
            rows = load_combat_scan(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["captured_at"], first_captured_at)

    def test_items_missing_a_type_or_cargo_key_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_inventory_items(
                [{"stats": {"Damage": "1"}}, {"item_type": "", "stats": {}}], out_path=path)
            self.assertEqual(load_combat_scan(path), [])

    def test_non_dict_entries_and_non_list_input_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_inventory_items(["not a dict", None, 42], out_path=path)
            self.assertEqual(load_combat_scan(path), [])
            record_inventory_items(None, out_path=path)  # never raises
            self.assertEqual(load_combat_scan(path), [])

    def test_falls_back_to_cargo_key_when_item_type_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_inventory_items([{"cargo_key": "abc123", "stats": {}}], out_path=path)
            rows = load_combat_scan(path)
            self.assertEqual(rows[0]["key"], "abc123")

    def test_a_stats_less_capture_never_overwrites_a_richer_one(self):
        # Real bug found this session: a player station's Trade Manager catalogue carries only
        # identity/category/tech/price for a "ton of different items" -- no ``stats`` dict at
        # all -- while a real trade listing or your own inventory carries the full stats dict.
        # Opening the catalogue AFTER already having a weapon's real stats from cargo silently
        # clobbered them, because both sources share the same item_type keyspace.
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_inventory_items(
                [{"item_type": "mag_cannon3", "stats": {"Damage": "203x2"}}], out_path=path)
            record_inventory_items(
                [{"item_type": "mag_cannon3", "category": "weapon", "tech": 7}], out_path=path)
            rows = load_combat_scan(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["data"]["stats"], {"Damage": "203x2"})

    def test_a_stats_less_capture_is_still_recorded_when_nothing_else_exists(self):
        # The catalogue's own identity/category/tech/price data is still worth keeping for an
        # item_type we've never seen with stats at all -- only clobbering a RICHER row is guarded
        # against, not recording leaner data in the first place.
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_inventory_items(
                [{"item_type": "ablative_shell", "category": "shield", "tech": 2}], out_path=path)
            rows = load_combat_scan(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["data"]["category"], "shield")

    def test_a_richer_capture_can_still_update_an_existing_richer_row(self):
        # The guard is one-directional -- stats-having data replacing other stats-having data
        # (e.g. a corrected/updated real observation) must still go through normally.
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_inventory_items(
                [{"item_type": "mag_cannon3", "stats": {"Damage": "203x2"}}], out_path=path)
            record_inventory_items(
                [{"item_type": "mag_cannon3", "stats": {"Damage": "210x2"}}], out_path=path)
            rows = load_combat_scan(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["data"]["stats"], {"Damage": "210x2"})

    def test_non_combat_categories_are_skipped_entirely(self):
        # Real finding this session: opening a station's Trade Manager catalogue floods the log
        # with hundreds of rows for every item type in the game -- blueprints, station kits,
        # resources, scanners, tokens -- the large majority irrelevant to weapon/plugin/gear
        # analysis. These should never even get a leaner-than-nothing row.
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_inventory_items([
                {"item_type": "alpha_station_design_blueprint", "category": "blueprint",
                 "stats": {"Uses": "∞"}},
                {"item_type": "colonists", "category": "resource"},
                {"item_type": "planet_scanner_mk1", "item_category": "scanner",
                 "stats": {"Scan Range": "500 u"}},
                {"item_type": "mag_cannon3", "category": "weapon", "stats": {"Damage": "203x2"}},
            ], out_path=path)
            rows = load_combat_scan(path)
            self.assertEqual({r["key"] for r in rows}, {"mag_cannon3"})

    def test_an_unrecognized_category_is_kept_not_silently_dropped(self):
        # Deliberately a denylist, not an allowlist -- a real gear category not seen yet (or one
        # this list simply doesn't know about) must still be logged for review.
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_inventory_items(
                [{"item_type": "mystery_gadget", "category": "something_new"}], out_path=path)
            rows = load_combat_scan(path)
            self.assertEqual({r["key"] for r in rows}, {"mystery_gadget"})


class RecordShipSpecsTests(_ResetsDedupCache):
    def _specs(self, hull="Frigate", weapon_types=("pulse_laser",), plugin_ids=()):
        return {
            "ship": {"display_name": hull},
            "weapons": [{"weapon_type": wt, "damage": 42.5, "base_damage": 40.0,
                         "fire_rate": 0.5, "dps": 85.0} for wt in weapon_types],
            "plugins": [{"plugin_id": pid, "display_name": pid} for pid in plugin_ids],
        }

    def test_distinct_loadout_combos_produce_separate_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_ship_specs(self._specs(plugin_ids=()), out_path=path)
            record_ship_specs(self._specs(plugin_ids=("overcharge",)), out_path=path)
            rows = load_combat_scan(path)
            self.assertEqual(len(rows), 2)
            self.assertEqual({r["source"] for r in rows}, {"ship_specs"})

    def test_reseeing_the_identical_combo_overwrites_rather_than_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_ship_specs(self._specs(), out_path=path)
            record_ship_specs(self._specs(), out_path=path)
            self.assertEqual(len(load_combat_scan(path)), 1)

    def test_weapon_and_plugin_order_does_not_affect_the_key(self):
        # The key must be order-independent (sorted) so the SAME combo equipped in a different
        # slot order still dedupes to one row, not two.
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_ship_specs(self._specs(weapon_types=("a", "b"), plugin_ids=("x", "y")),
                               out_path=path)
            record_ship_specs(self._specs(weapon_types=("b", "a"), plugin_ids=("y", "x")),
                               out_path=path)
            self.assertEqual(len(load_combat_scan(path)), 1)

    def test_current_energy_ticking_alone_does_not_trigger_a_new_row(self):
        # The real fix for a real player report: their game client lagged whenever they opened
        # the Stats page, traced to Ship Specs polling once a second and current_energy
        # legitimately changing almost every poll even when nothing else about the loadout has --
        # comparing the raw payload verbatim would make record_ship_specs() rewrite the entire
        # scan file every single second for no real reason. The energy sample is still recorded
        # every time regardless (see RecordEnergySampleTests) -- that's the one place this ticking
        # reading is actually meant to be tracked.
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_ship_specs({**self._specs(), "energy": {"current_energy": 1000.0}},
                               out_path=path)
            record_ship_specs({**self._specs(), "energy": {"current_energy": 950.0}},
                               out_path=path)
            ship_specs_rows = [r for r in load_combat_scan(path) if r["source"] == "ship_specs"]
            self.assertEqual(len(ship_specs_rows), 1)
            energy_samples = [r for r in load_combat_scan(path) if r["source"] == "energy_sample"]
            self.assertEqual(len(energy_samples), 2)

    def test_a_real_change_within_the_same_combo_still_updates_the_row(self):
        # Proves the dedup check isn't just "same combo key" -- it compares the actual payload
        # (minus current_energy), so a real change (here, a weapon's own displayed damage) for
        # the identical (hull, weapon types, plugin ids) combo is still captured, not silently
        # swallowed just because SOME earlier snapshot for this combo already exists.
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_ship_specs({**self._specs(), "energy": {"current_energy": 1000.0}},
                               out_path=path)
            changed = self._specs()
            changed["weapons"][0]["damage"] = 99.0
            record_ship_specs({**changed, "energy": {"current_energy": 950.0}}, out_path=path)
            ship_specs_rows = [r for r in load_combat_scan(path) if r["source"] == "ship_specs"]
            self.assertEqual(len(ship_specs_rows), 1)
            self.assertEqual(ship_specs_rows[0]["data"]["weapons"][0]["damage"], 99.0)

    def test_an_error_payload_is_not_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_ship_specs({"error": "build failed"}, out_path=path)
            self.assertEqual(load_combat_scan(path), [])

    def test_non_dict_input_never_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_ship_specs(None, out_path=path)
            record_ship_specs("not a dict", out_path=path)
            self.assertEqual(load_combat_scan(path), [])


class RecordEnergySampleTests(_ResetsDedupCache):
    def _specs(self, current_energy, hull="Frigate", weapon_types=("pulse_laser",),
               max_energy=1000.0, energy_output=50.0):
        return {
            "ship": {"display_name": hull},
            "weapons": [{"weapon_type": wt} for wt in weapon_types],
            "plugins": [],
            "energy": {"current_energy": current_energy, "max_energy": max_energy,
                       "energy_output": energy_output},
        }

    def test_every_ship_specs_push_appends_its_own_energy_sample(self):
        # The upsert above only keeps the LATEST ship_specs row per combo -- energy_sample is
        # the one place every single reading survives, since that's the whole point (a real
        # time series to measure actual drain from, not just a snapshot).
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_ship_specs(self._specs(1000.0), out_path=path)
            record_ship_specs(self._specs(950.0), out_path=path)
            record_ship_specs(self._specs(900.0), out_path=path)
            samples = [r for r in load_combat_scan(path) if r["source"] == "energy_sample"]
            self.assertEqual([s["data"]["current_energy"] for s in samples],
                              [1000.0, 950.0, 900.0])

    def test_samples_are_tagged_with_the_same_combo_key_as_the_snapshot_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_ship_specs(self._specs(1000.0), out_path=path)
            rows = load_combat_scan(path)
            snapshot = next(r for r in rows if r["source"] == "ship_specs")
            sample = next(r for r in rows if r["source"] == "energy_sample")
            self.assertEqual(sample["data"]["combo_key"], snapshot["key"])

    def test_a_hull_with_no_energy_block_at_all_records_no_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            specs = self._specs(1000.0)
            del specs["energy"]
            record_ship_specs(specs, out_path=path)
            self.assertEqual([r for r in load_combat_scan(path) if r["source"] == "energy_sample"],
                              [])

    def test_an_error_payload_records_no_sample_either(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_ship_specs({"error": "build failed"}, out_path=path)
            self.assertEqual(load_combat_scan(path), [])

    def test_max_energy_and_energy_output_are_captured_alongside_current_energy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_ship_specs(self._specs(500.0, max_energy=1000.0, energy_output=42.0),
                               out_path=path)
            sample = next(r for r in load_combat_scan(path) if r["source"] == "energy_sample")
            self.assertEqual(sample["data"]["max_energy"], 1000.0)
            self.assertEqual(sample["data"]["energy_output"], 42.0)


class RecordTurretStateAndShipPluginDataTests(_ResetsDedupCache):
    def test_a_genuinely_new_observation_is_still_appended_unconditionally(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_turret_state({"hardpoints": [{"weapon_type": "pulse_laser"}]}, out_path=path)
            record_turret_state({"hardpoints": [{"weapon_type": "auto_cannon"}]}, out_path=path)
            rows = load_combat_scan(path)
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(r["source"] == "turret_state" for r in rows))

    def test_a_byte_identical_repeat_is_skipped_not_appended_again(self):
        # The real fix for a real player report: their game client lagged badly, traced in part
        # to TURRET_STATE re-pushing an UNCHANGED rack every few seconds while the Turret Layout
        # screen just stayed open (473 real rows in one session, the large majority duplicates) --
        # unbounded append-only growth for data that never actually changed.
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_turret_state({"hardpoints": [{"weapon_type": "pulse_laser"}]}, out_path=path)
            record_turret_state({"hardpoints": [{"weapon_type": "pulse_laser"}]}, out_path=path)
            rows = load_combat_scan(path)
            self.assertEqual(len(rows), 1)

    def test_a_real_change_after_a_repeat_is_appended_again(self):
        # The dedup only ever compares against the LAST payload seen, not everything ever seen --
        # a real change is never permanently suppressed just because a duplicate was skipped.
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_turret_state({"hardpoints": [{"weapon_type": "pulse_laser"}]}, out_path=path)
            record_turret_state({"hardpoints": [{"weapon_type": "pulse_laser"}]}, out_path=path)
            record_turret_state({"hardpoints": [{"weapon_type": "auto_cannon"}]}, out_path=path)
            rows = load_combat_scan(path)
            self.assertEqual(len(rows), 2)
            self.assertEqual([r["data"]["hardpoints"][0]["weapon_type"] for r in rows],
                              ["pulse_laser", "auto_cannon"])

    def test_append_only_rows_survive_a_later_unrelated_upsert(self):
        # Real bug found this session: _upsert()'s own read/write round-trip used to key EVERY
        # row -- including append-only ones -- by (source, key). Every append-only row shares
        # the exact same key (None), so multiple of them collided into a single dict slot, and
        # the NEXT unrelated upsert to run against the same file (any record_inventory_items/
        # record_ship_specs/record_skills_data call) silently rewrote the file from that dict,
        # destroying every prior append-only observation but the last one.
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_turret_state({"hardpoints": [{"weapon_type": "a"}]}, out_path=path)
            record_turret_state({"hardpoints": [{"weapon_type": "b"}]}, out_path=path)
            record_inventory_items([{"item_type": "mag_cannon3", "stats": {"Damage": "1"}}],
                                    out_path=path)
            turret_rows = [r for r in load_combat_scan(path) if r["source"] == "turret_state"]
            self.assertEqual(len(turret_rows), 2)

    def test_ship_plugin_data_appends_every_genuinely_new_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_ship_plugin_data({"plugins": []}, out_path=path)
            record_ship_plugin_data({"plugins": [{"plugin_id": "x"}]}, out_path=path)
            rows = load_combat_scan(path)
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(r["source"] == "ship_plugin_data" for r in rows))

    def test_ship_plugin_data_skips_a_byte_identical_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_ship_plugin_data({"plugins": [{"plugin_id": "x"}]}, out_path=path)
            record_ship_plugin_data({"plugins": [{"plugin_id": "x"}]}, out_path=path)
            rows = load_combat_scan(path)
            self.assertEqual(len(rows), 1)

    def test_non_dict_input_never_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_turret_state(None, out_path=path)
            record_turret_state("nope", out_path=path)
            record_ship_plugin_data(None, out_path=path)
            self.assertEqual(load_combat_scan(path), [])


class RecordSkillsDataTests(_ResetsDedupCache):
    def test_one_row_per_skill_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_skills_data([
                {"skill_id": "weapons", "display_name": "Weapons", "level": 3, "max_level": 5,
                 "pct_bonus": {"weapon_damage": 0.02}},
                {"skill_id": "shields", "display_name": "Shields", "level": 1, "max_level": 5,
                 "stat_bonus": {"max_shields": 50}},
            ], out_path=path)
            rows = load_combat_scan(path)
            self.assertEqual({r["key"] for r in rows}, {"weapons", "shields"})
            weapons_row = next(r for r in rows if r["key"] == "weapons")
            self.assertEqual(weapons_row["source"], "skill")
            self.assertEqual(weapons_row["data"]["pct_bonus"]["weapon_damage"], 0.02)

    def test_retraining_a_level_overwrites_rather_than_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_skills_data([{"skill_id": "weapons", "level": 1}], out_path=path)
            record_skills_data([{"skill_id": "weapons", "level": 2}], out_path=path)
            rows = load_combat_scan(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["data"]["level"], 2)

    def test_skills_missing_a_skill_id_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_skills_data([{"display_name": "No ID"}, {"skill_id": "", "level": 1}],
                                out_path=path)
            self.assertEqual(load_combat_scan(path), [])

    def test_reseeing_an_identical_skill_does_not_rewrite_the_file(self):
        # Same fix as every other recorder here: a skill that hasn't actually changed shouldn't
        # cost a real file rewrite just because SKILLS_DATA happened to push again. Proven via
        # captured_at, which only ever gets refreshed on an actual write.
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_skills_data([{"skill_id": "weapons", "level": 1}], out_path=path)
            first_captured_at = load_combat_scan(path)[0]["captured_at"]
            record_skills_data([{"skill_id": "weapons", "level": 1}], out_path=path)
            rows = load_combat_scan(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["data"]["level"], 1)
            self.assertEqual(rows[0]["captured_at"], first_captured_at)

    def test_mastery_tree_is_appended_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_skills_data([], mastery_tree=[{"level": 2, "class_id": "hullbreaker"}],
                                out_path=path)
            rows = load_combat_scan(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["source"], "mastery_tree")

    def test_mastery_tree_skips_a_byte_identical_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_skills_data([], mastery_tree=[{"level": 2, "class_id": "hullbreaker"}],
                                out_path=path)
            record_skills_data([], mastery_tree=[{"level": 2, "class_id": "hullbreaker"}],
                                out_path=path)
            rows = [r for r in load_combat_scan(path) if r["source"] == "mastery_tree"]
            self.assertEqual(len(rows), 1)

    def test_mastery_tree_records_a_real_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_skills_data([], mastery_tree=[{"level": 2, "class_id": "hullbreaker"}],
                                out_path=path)
            record_skills_data([], mastery_tree=[{"level": 3, "class_id": "hullbreaker"}],
                                out_path=path)
            rows = [r for r in load_combat_scan(path) if r["source"] == "mastery_tree"]
            self.assertEqual(len(rows), 2)

    def test_no_mastery_tree_records_nothing_extra(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_skills_data([{"skill_id": "weapons", "level": 1}], mastery_tree=None,
                                out_path=path)
            rows = load_combat_scan(path)
            self.assertEqual({r["source"] for r in rows}, {"skill"})

    def test_non_list_or_non_dict_input_never_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            record_skills_data(None, out_path=path)
            record_skills_data("nope", out_path=path)
            record_skills_data([None, "nope", 42], out_path=path)
            self.assertEqual(load_combat_scan(path), [])


class LoadCombatScanTests(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "does_not_exist.jsonl")
            self.assertEqual(load_combat_scan(path), [])

    def test_a_malformed_line_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "combat_scan.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                f.write("not valid json\n")
                f.write(json.dumps({"source": "inventory_item", "key": "a", "data": {}}) + "\n")
            rows = load_combat_scan(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["key"], "a")


if __name__ == "__main__":
    unittest.main()

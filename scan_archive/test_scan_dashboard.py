import json
import math
import re
import shutil
import subprocess
import unittest

from scan_dashboard import build_dashboard_html, galaxy_summary, planet_records, _num


class NumCoercionTests(unittest.TestCase):
    def test_blank_becomes_none(self):
        self.assertIsNone(_num(""))
        self.assertIsNone(_num(None))

    def test_integral_float_string_becomes_int(self):
        self.assertEqual(_num("84"), 84)
        self.assertEqual(_num("84.0"), 84)

    def test_fractional_string_stays_float(self):
        self.assertEqual(_num("12345.6"), 12345.6)

    def test_non_numeric_passes_through_unchanged(self):
        self.assertEqual(_num("Mega Jungle"), "Mega Jungle")


class BuildDashboardHtmlTests(unittest.TestCase):
    def _row(self, **overrides):
        row = {
            "system": "Peacock Station",
            "planet_name": "Cryldorwen",
            "planet_type": "Terra",
            "colonization_index": "84",
            "orbit_distance": "12345",
            "orbit_period_seconds": "706",
            "metal_ore_slots": "14", "silicon_slots": "133",
            "copper_slots": "3", "titanium_slots": "1",
            "gold_slots": "0", "oil_slots": "2",
            "wood_slots": "34", "space_corn_slots": "165",
            "gravity_setting": "Heavy Gravity", "gravity_penalty_pct": "80",
            "temperature_setting": "Temperate", "temperature_penalty_pct": "0",
            "atmosphere_setting": "", "atmosphere_penalty_pct": "",
            "geology_setting": "", "geology_penalty_pct": "",
            "ecology_setting": "", "ecology_penalty_pct": "",
            "extra_modifiers": "",
        }
        row.update(overrides)
        return row

    def test_empty_rows_still_produce_a_valid_page(self):
        page = build_dashboard_html([])
        self.assertIn("<html>", page)
        self.assertIn("const DATA = []", page)

    def test_row_data_is_embedded_as_json(self):
        page = build_dashboard_html([self._row()])
        start = page.index("const DATA = ") + len("const DATA = ")
        end = page.index(";\n", start)
        data = json.loads(page[start:end])
        self.assertEqual(len(data), 1)
        rec = data[0]
        self.assertEqual(rec["planet_name"], "Cryldorwen")
        self.assertEqual(rec["colonization_index"], 84)
        self.assertEqual(rec["resources"]["metal_ore"], 14)
        self.assertEqual(rec["resources"]["gold"], 0)
        # Only modifiers with a non-empty setting are included.
        categories = {m["category"] for m in rec["modifiers"]}
        self.assertEqual(categories, {"Gravity", "Temperature"})

    def test_parent_planet_is_embedded_for_a_moon(self):
        page = build_dashboard_html(
            [self._row(planet_name="Little Moon", parent_planet="Cryldorwen")])
        start = page.index("const DATA = ") + len("const DATA = ")
        end = page.index(";\n", start)
        data = json.loads(page[start:end])
        self.assertEqual(data[0]["parent_planet"], "Cryldorwen")

    def test_ordinary_planet_has_blank_parent(self):
        page = build_dashboard_html([self._row()])
        start = page.index("const DATA = ") + len("const DATA = ")
        end = page.index(";\n", start)
        data = json.loads(page[start:end])
        self.assertEqual(data[0]["parent_planet"], "")

    def test_focus_system_is_embedded(self):
        page = build_dashboard_html([self._row()], focus_system="Peacock Station")
        self.assertIn('const FOCUS_SYSTEM = "Peacock Station"', page)

    def test_planet_name_with_special_characters_produces_valid_json(self):
        page = build_dashboard_html([self._row(planet_name='Test "Quote" </script>')])
        start = page.index("const DATA = ") + len("const DATA = ")
        end = page.index(";\n", start)
        data = json.loads(page[start:end])
        self.assertEqual(data[0]["planet_name"], 'Test "Quote" </script>')


class GalaxySummaryTests(unittest.TestCase):
    """The Galaxies tab's aggregation -- requested this session as a per-galaxy rollup on the
    scan archive (not colony_tool): list the planets in each galaxy, and if scanned, the total
    resources available there."""

    def _row(self, **overrides):
        row = {
            "system": "Peacock Station", "planet_name": "Cryldorwen", "planet_type": "Terra",
            "parent_planet": "", "colonization_index": "84", "orbit_distance": "12345",
            "orbit_period_seconds": "706",
            "metal_ore_slots": "14", "silicon_slots": "133", "copper_slots": "3",
            "titanium_slots": "1", "gold_slots": "0", "oil_slots": "2", "wood_slots": "34",
            "space_corn_slots": "165",
            "gravity_setting": "", "gravity_penalty_pct": "", "temperature_setting": "",
            "temperature_penalty_pct": "", "atmosphere_setting": "", "atmosphere_penalty_pct": "",
            "geology_setting": "", "geology_penalty_pct": "", "ecology_setting": "",
            "ecology_penalty_pct": "", "extra_modifiers": "",
        }
        row.update(overrides)
        return row

    def test_explored_totals_gives_total_and_type_counts(self):
        # explored_totals is already aggregate (see galaxy_export.build_export_json) -- there's no
        # per-planet moon entry to exclude here, unlike scan rows.
        explored_totals = {"Zordoil Frontier": {"planet_types": {"Terra": 2, "Barren": 1},
                                                 "moon_count": 1}}
        summary = galaxy_summary([], explored_totals)
        entry = summary["Zordoil Frontier"]
        self.assertEqual(entry["total_planets"], 3)
        self.assertEqual(entry["types"], {"Terra": 2, "Barren": 1})

    def test_unscanned_galaxy_has_zero_resources_and_scanned_false(self):
        explored_totals = {"Zordoil Frontier": {"planet_types": {"Terra": 1}, "moon_count": 0}}
        summary = galaxy_summary([], explored_totals)
        entry = summary["Zordoil Frontier"]
        self.assertFalse(entry["scanned"])
        self.assertEqual(entry["scanned_planets"], 0)
        self.assertEqual(entry["resources"]["metal_ore"], 0)

    def test_scanning_a_planet_marks_the_galaxy_scanned_and_sums_resources(self):
        rows = [self._row(system="Zordoil Frontier", planet_name="Cryldorwen"),
                self._row(system="Zordoil Frontier", planet_name="Droasheth", metal_ore_slots="6")]
        summary = galaxy_summary(rows)
        entry = summary["Zordoil Frontier"]
        self.assertTrue(entry["scanned"])
        self.assertEqual(entry["scanned_planets"], 2)
        # slots are per station and a planet holds 3 stations: (14 + 6) x 3
        self.assertEqual(entry["resources"]["metal_ore"], 60)

    def test_a_scanned_moon_contributes_resources_but_not_to_scanned_planets(self):
        rows = [self._row(planet_name="Cryldorwen"),
                self._row(planet_name="CryldorwenI", parent_planet="Cryldorwen",
                           metal_ore_slots="5", silicon_slots="0")]
        summary = galaxy_summary(rows)
        entry = summary["Peacock Station"]
        self.assertEqual(entry["scanned_planets"], 1)   # the moon doesn't count as a planet
        # the moon's ore DOES count, but a moon holds one station and a planet three: 14 x 3 + 5 x 1
        self.assertEqual(entry["resources"]["metal_ore"], 47)

    def test_promethium_on_a_gas_giant_counts_x3_and_on_a_moon_x1(self):
        zero = dict(metal_ore_slots="0", silicon_slots="0", copper_slots="0", titanium_slots="0",
                    oil_slots="0", wood_slots="0", space_corn_slots="0")
        rows = [self._row(planet_name="Kaermaryn", planet_type="Gas Giant",
                          promethium_slots="4", **zero),
                self._row(planet_name="Moonlet", parent_planet="Kaermaryn",
                          promethium_slots="2", **zero)]
        entry = galaxy_summary(rows)["Peacock Station"]
        self.assertEqual(entry["resources"]["promethium"], 4 * 3 + 2 * 1)

    def test_a_csv_from_before_promethium_existed_reads_as_zero_slots(self):
        entry = galaxy_summary([self._row()])["Peacock Station"]      # no promethium_slots column
        self.assertEqual(entry["resources"]["promethium"], 0)

    def test_a_system_only_ever_explored_still_appears_with_zero_scan_data(self):
        # The whole point of this session's fix: a system you've flown into but never
        # individually scanned still shows its full planet-type breakdown -- no scan required.
        explored_totals = {"Never Scanned System": {"planet_types": {"Ferrous": 1}, "moon_count": 0}}
        summary = galaxy_summary([], explored_totals)
        self.assertIn("Never Scanned System", summary)
        self.assertEqual(summary["Never Scanned System"]["total_planets"], 1)
        self.assertEqual(summary["Never Scanned System"]["scanned_planets"], 0)

    def test_no_explored_totals_given_still_works_from_scan_rows_alone(self):
        # export_for_claude.json might not exist yet (best-effort in generate_and_open), or
        # predates this feature -- the Galaxies tab should still show whatever's been scanned,
        # falling back to a scan-derived total_planets/types rather than showing zero.
        summary = galaxy_summary([self._row()], None)
        self.assertIn("Peacock Station", summary)
        self.assertEqual(summary["Peacock Station"]["total_planets"], 1)
        self.assertEqual(summary["Peacock Station"]["types"], {"Terra": 1})
        self.assertEqual(summary["Peacock Station"]["scanned_planets"], 1)

    def test_explored_totals_overrides_the_scan_derived_fallback(self):
        # explored_totals is always at least as complete as a partial scan history -- it should
        # win even when scan rows exist for the same system.
        rows = [self._row(system="Zordoil Frontier", planet_name="Cryldorwen")]
        explored_totals = {"Zordoil Frontier": {"planet_types": {"Terra": 2, "Barren": 1},
                                                 "moon_count": 0}}
        summary = galaxy_summary(rows, explored_totals)
        entry = summary["Zordoil Frontier"]
        self.assertEqual(entry["total_planets"], 3)   # not 1, the scan-only count
        self.assertEqual(entry["types"], {"Terra": 2, "Barren": 1})
        self.assertEqual(entry["scanned_planets"], 1)   # scan-derived fields are untouched

    def test_hazard_rating_comes_from_explored_totals(self):
        explored_totals = {"Zordoil Frontier": {"planet_types": {"Terra": 1}, "moon_count": 0,
                                                  "hazard_rating": 20}}
        summary = galaxy_summary([], explored_totals)
        self.assertEqual(summary["Zordoil Frontier"]["hazard_rating"], 20)

    def test_hazard_rating_zero_is_a_real_value_not_missing(self):
        explored_totals = {"Zordoil Frontier": {"planet_types": {"Terra": 1}, "moon_count": 0,
                                                  "hazard_rating": 0}}
        summary = galaxy_summary([], explored_totals)
        self.assertEqual(summary["Zordoil Frontier"]["hazard_rating"], 0)
        self.assertIsNotNone(summary["Zordoil Frontier"]["hazard_rating"])

    def test_hazard_rating_is_none_when_explored_totals_does_not_cover_the_system(self):
        # A scan-only system (no explored_systems entry, e.g. an older export) has no way to know
        # its hazard level -- must stay None, never default to 0 (a real, confirmed-safe value).
        summary = galaxy_summary([self._row()], None)
        self.assertIsNone(summary["Peacock Station"]["hazard_rating"])


class PlanetRecordSlotTests(unittest.TestCase):
    """Per-station slots are what a scan reports and what planet_scans.csv stores; a body's
    TOTAL is that x the stations it can hold (3 on a planet, 1 on a moon)."""

    @staticmethod
    def _row(**overrides):
        row = {"system": "Sys", "planet_name": "Alpha", "planet_type": "Terra", "parent_planet": "",
               "metal_ore_slots": "14", "promethium_slots": "0"}
        row.update(overrides)
        return row

    def test_a_planet_holds_three_stations(self):
        rec = planet_records([self._row()])[0]
        self.assertEqual(rec["stations"], 3)
        self.assertEqual(rec["resources"]["metal_ore"], 14)          # per station, as scanned
        self.assertEqual(rec["resource_totals"]["metal_ore"], 42)    # across the 3 stations

    def test_a_moon_holds_one_station(self):
        rec = planet_records([self._row(planet_name="Beta", parent_planet="Alpha")])[0]
        self.assertEqual(rec["stations"], 1)
        self.assertEqual(rec["resource_totals"]["metal_ore"], 14)

    def test_promethium_is_a_resource_column_with_its_total(self):
        rec = planet_records([self._row(planet_type="Gas Giant", metal_ore_slots="0",
                                        promethium_slots="5")])[0]
        self.assertEqual(rec["resources"]["promethium"], 5)
        self.assertEqual(rec["resource_totals"]["promethium"], 15)

    def test_the_page_offers_a_promethium_column_and_explains_the_multiplier(self):
        page = build_dashboard_html([self._row(planet_type="Gas Giant", promethium_slots="5")])
        columns = json.loads(re.search(r"const RESOURCE_COLS = (\[.*?\]);\n", page, re.S).group(1))
        self.assertIn({"key": "promethium", "label": "Promethium"}, columns)
        self.assertIn("3 on a planet, 1 on a moon", page)
        self.assertIn("per station", page)

    def test_the_embedded_row_data_carries_the_totals_the_table_reads(self):
        page = build_dashboard_html([self._row(planet_type="Gas Giant", metal_ore_slots="0",
                                               promethium_slots="5")])
        data = json.loads(re.search(r"const DATA = (\[.*?\]);\n", page, re.S).group(1))
        self.assertEqual(data[0]["resource_totals"]["promethium"], 15)
        self.assertEqual(data[0]["stations"], 3)


class GalaxyDataEmbeddingTests(unittest.TestCase):
    """build_dashboard_html's own embedding of galaxy_summary()'s output as the page's GALAXY
    constant, and the Galaxies tab markup being present."""

    def test_galaxy_json_is_embedded(self):
        page = build_dashboard_html([], explored_totals={
            "Zordoil Frontier": {"planet_types": {"Terra": 1}, "moon_count": 0}})
        start = page.index("const GALAXY = ") + len("const GALAXY = ")
        end = page.index(";\n", start)
        galaxy = json.loads(page[start:end])
        self.assertEqual(galaxy["Zordoil Frontier"]["total_planets"], 1)

    def test_no_explored_totals_given_embeds_an_empty_object_not_a_crash(self):
        page = build_dashboard_html([])
        start = page.index("const GALAXY = ") + len("const GALAXY = ")
        end = page.index(";\n", start)
        self.assertEqual(json.loads(page[start:end]), {})

    def test_tabs_markup_is_present(self):
        page = build_dashboard_html([])
        self.assertIn('id="tabPlanetsBtn"', page)
        self.assertIn('id="tabGalaxiesBtn"', page)
        self.assertIn('id="galaxiesView"', page)


class BotActivityEmbeddingTests(unittest.TestCase):
    """build_dashboard_html's own embedding of bot_activity_summary()'s output as the page's
    BOTS constant, and the Bot Activity tab markup being present."""

    def _bots_json(self, page):
        start = page.index("const BOTS = ") + len("const BOTS = ")
        end = page.index(";\n", start)
        return json.loads(page[start:end])

    def test_bots_json_is_embedded_from_station_log_rows(self):
        rows = [{"station_id": "7", "id": "1", "time": "1",
                  "text": "Ver-A4 (TB-ExampleAlt3) docked."}]
        page = build_dashboard_html([], station_log_rows=rows)
        bots = self._bots_json(page)
        self.assertEqual(bots["stations"]["7"]["bot_docks"], 1)
        self.assertEqual(bots["bots"]["Ver-A4"]["docks"], 1)

    def test_no_station_log_rows_given_embeds_empty_structure_not_a_crash(self):
        page = build_dashboard_html([])
        bots = self._bots_json(page)
        self.assertEqual(bots, {"stations": {}, "bots": {}, "accounts": {}})

    def test_station_names_bot_fleet_rows_and_slots_all_flow_through(self):
        rows = [{"station_id": "7", "id": "1", "time": "1",
                  "text": "Ver-A4 (TB-ExampleAlt3) docked."}]
        page = build_dashboard_html(
            [], station_log_rows=rows,
            station_names={"7": "Londdalen - 1"},
            bot_fleet_rows=[{"key": "501", "captured_at": 1,
                              "context": {"username": "ExampleAlt3"},
                              "data": {"nickname": "Ver-A4", "ship_type": "type_a",
                                       "state": "following", "bot_cargo_cap": 500,
                                       "bot_cargo_used": 200, "haul_orders": ["o1", "o2"]}}],
            trade_bot_slots={"ExampleAlt3": {"slots_used": 41, "slots_total": 59,
                                            "updated_at": 1}})
        bots = self._bots_json(page)
        self.assertIn("Londdalen - 1", bots["stations"])
        self.assertEqual(bots["bots"]["Ver-A4"]["cargo_cap"], 500)
        self.assertEqual(bots["accounts"]["TB-ExampleAlt3"]["slots_used"], 41)

    def test_colony_scan_rows_flow_through_to_station_storage(self):
        rows = [{"source": "station_setup", "key": "7", "captured_at": 1, "context": {},
                  "data": {"hull_used": 100, "hull_space": 200,
                           "resource_caps": {"wood": 500},
                           "cargo": [{"item_key": "wood", "item_category": "resource",
                                      "cargo_size": 300}]}}]
        page = build_dashboard_html([], colony_scan_rows=rows,
                                     station_names={"7": "Londdalen - 1"})
        bots = self._bots_json(page)
        st = bots["stations"]["Londdalen - 1"]
        self.assertEqual(st["hull_fill_pct"], 50.0)
        self.assertEqual(st["resources"]["wood"], 300)
        self.assertEqual(st["resource_caps"]["wood"], 500)

    def test_production_and_bot_coverage_flow_through_to_the_embedded_json(self):
        log_rows = [
            {"station_id": "7", "id": "1", "time": "1000", "text": "Ver-A4 (TB-ExampleAlt3) docked."},
            {"station_id": "7", "id": "2", "time": "1030", "text": "Ver-A4 (TB-ExampleAlt3) docked."},
            {"station_id": "7", "id": "3", "time": "1000",
             "text": "Ver-A4 (TB-ExampleAlt3) bought 100 Wood for 100 credits."},
        ]
        colony_rows = [{"source": "station_setup", "key": "7", "captured_at": 1, "context": {},
                         "data": {"hull_used": 0, "hull_space": 0, "resource_caps": {},
                                  "resource_reserves": {}, "cargo": [
                             {"item_key": "item:station:wood_cutter", "item_category": "station",
                              "quantity": 1,
                              "stats": {"Cycle Output": "1 Wood", "Cycle Time": "10 seconds"}},
                         ]}}]
        page = build_dashboard_html([], station_log_rows=log_rows, colony_scan_rows=colony_rows,
                                     station_names={"7": "Londdalen - 1"})
        bots = self._bots_json(page)
        st = bots["stations"]["Londdalen - 1"]
        self.assertAlmostEqual(st["resource_production_per_day"]["wood"], 8640.0, places=1)
        self.assertIsNotNone(st["resource_balance"]["wood"]["bought_per_day"])
        self.assertIsNotNone(st["overall_pct_satisfied"])
        self.assertEqual(st["bot_breakdown"]["Ver-A4"]["docks"], 2)

    def test_the_observed_rates_flow_through_and_the_bot_activity_tab_shows_both(self):
        log_rows = []
        for i in range(5):
            log_rows.append({"station_id": "7", "id": f"d{i}", "time": str(1000 + i * 60), "text": "Ver-A4 (TB-ExampleAlt3) docked."})
            log_rows.append({"station_id": "7", "id": f"b{i}", "time": str(1000 + i * 60),
                             "text": "Ver-A4 (TB-ExampleAlt3) bought 100 Wood for 100 credits."})
        log_rows.append({"station_id": "7", "id": "p", "time": str(1000 + 4 * 3600), "text": "ExamplePlayer docked."})
        colony_rows = [{"source": "station_setup", "key": "7", "captured_at": 1, "context": {},
                        "data": {"hull_used": 0, "hull_space": 0, "resource_caps": {}, "resource_reserves": {}, "cargo": [
                            {"item_key": "item:station:wood_cutter", "item_category": "station", "quantity": 1,
                             "stats": {"Cycle Output": "1 Wood", "Cycle Time": "10 seconds"}}]}}]
        page = build_dashboard_html([], station_log_rows=log_rows, colony_scan_rows=colony_rows,
                                    station_names={"7": "Londdalen - 1"})
        st = self._bots_json(page)["stations"]["Londdalen - 1"]
        self.assertEqual(st["log_window"]["hours"], 4.0)
        self.assertAlmostEqual(st["resource_balance"]["wood"]["bought_observed_per_day"], 3000.0, places=1)
        self.assertAlmostEqual(st["overall_pct_observed"], 100 * 3000 / 8640, places=1)
        self.assertEqual(st["bot_breakdown"]["Ver-A4"]["resource_rates"]["wood"]["observed_per_day"], 3000)
        # the tab's own script knows to show them
        for text in ('key: "overall_pct_observed"', "Coverage % (observed)", "Coverage % (if non-stop)",
                     "really bought", "/day really", "/day if non-stop", "% observed"):
            self.assertIn(text, page)

    def test_shuttle_network_rows_give_a_feeder_station_shuttle_coverage(self):
        # The Bot Coverage % ask: a feeder station with zero bot docks that offloads everything
        # via the orbital shuttle network to a hub must not read as 0% satisfied.
        colony_rows = [
            {"source": "station_setup", "key": "9", "captured_at": 1, "context": {},
             "data": {"hull_used": 0, "hull_space": 0, "resource_caps": {},
                       "resource_reserves": {}, "cargo": [
                 {"item_key": "item:station:test_extractor", "item_category": "station",
                  "quantity": 1, "stats": {"Cycle Output": "1 Wood", "Cycle Time": "10 seconds"}},
             ]}},
            {"source": "shuttle_network", "key": "1", "captured_at": 1, "context": {},
             "data": {"transfer_limit": 1000000, "transfer_interval_seconds": 10.0,
                       "routes": [{"direction": "import", "enabled": True,
                                   "target_station_id": 9, "resource_key": "wood"}]}},
        ]
        page = build_dashboard_html([], colony_scan_rows=colony_rows,
                                     station_names={"1": "Hub", "9": "Feeder"})
        bots = self._bots_json(page)
        st = bots["stations"]["Feeder"]
        self.assertEqual(st["overall_pct_satisfied"], 100.0)
        self.assertEqual(st["resource_balance"]["wood"]["shuttled_to"], ["Hub"])

    def test_station_systems_flow_through_to_the_galaxy_field(self):
        rows = [{"station_id": "7", "id": "1", "time": "1",
                  "text": "Ver-A4 (TB-ExampleAlt3) docked."}]
        page = build_dashboard_html(
            [], station_log_rows=rows, station_names={"7": "Londdalen - 1"},
            station_systems={"7": "Zordoil Frontier"})
        bots = self._bots_json(page)
        self.assertEqual(bots["stations"]["Londdalen - 1"]["system"], "Zordoil Frontier")

    def test_tab_and_view_markup_is_present(self):
        page = build_dashboard_html([])
        self.assertIn('id="tabBotsBtn"', page)
        self.assertIn('id="botsView"', page)
        self.assertIn('id="bsTbody"', page)
        self.assertIn('id="bbTbody"', page)

    def test_no_classlist_add_call_embeds_a_multi_word_string_literal(self):
        # Regression guard: classList.add("badge badge-yes") is invalid --
        # classList.add() treats its argument as ONE token and throws
        # InvalidCharacterError on a space, which aborted the whole Bot
        # Activity render the instant any station had a real (non-null)
        # shuttle_covered value (found live this session -- both tables came
        # up completely empty with no visible error to the player). A class
        # list needs either separate arguments (.add("badge", "badge-yes"))
        # or a single hyphenated token, never one string with a space.
        page = build_dashboard_html([])
        for call in re.finditer(r'classList\.add\(([^)]*)\)', page):
            for literal in re.findall(r'"([^"]*)"', call.group(1)):
                self.assertNotIn(" ", literal,
                                  f'classList.add(...) call {call.group(0)!r} has a '
                                  f'string literal with a space -- invalid token')


NODE_COLUMN_SCRIPT = r"""
const vm = require("vm");
const {code, rows} = JSON.parse(require("fs").readFileSync(0, "utf8"));
const cols = vm.runInNewContext(code + "\nBASE_COLS");
const col = cols.find(c => c.key === "orbit_speed_ups");
process.stdout.write(JSON.stringify({
  order: cols.map(c => c.key), label: col.label, numeric: col.numeric,
  results: rows.map(r => { const v = col.get(r); return {value: v, text: col.fmt(v), tip: col.tip(r)}; }),
}));
"""


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class OrbitSpeedColumnTests(unittest.TestCase):
    """The Planets tab's Orbit Speed column: worked out in the page from the scanned orbit distance and
    period (one lap = 2*pi*r in one period), not stored. The page's own functions run under node."""

    @classmethod
    def setUpClass(cls):
        page = build_dashboard_html([])
        helpers = page[page.index("function fmtDuration("):page.index("function cellValue(")]
        columns = page[page.index("const BASE_COLS = ["):page.index("// Slots across every station")]
        cls.code = helpers + "\n" + columns

    def _run(self, rows):
        done = subprocess.run(["node", "-e", NODE_COLUMN_SCRIPT], input=json.dumps({"code": self.code, "rows": rows}),
                              capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_the_column_follows_the_orbit_period_and_is_sortable(self):
        out = self._run([])
        self.assertEqual(out["order"].index("orbit_speed_ups"), out["order"].index("orbit_period_seconds") + 1)
        self.assertEqual(out["label"], "Orbit Speed (u/s)")
        self.assertTrue(out["numeric"])

    def test_speed_is_one_lap_per_period(self):
        planet = {"orbit_distance": 12345, "orbit_period_seconds": 706, "parent_planet": ""}
        result = self._run([planet])["results"][0]
        expected = 2 * math.pi * 12345 / 706
        self.assertAlmostEqual(result["value"], expected, places=9)
        self.assertEqual(result["text"], f"{expected:.1f}")
        self.assertIn(f"{expected:.2f} u/s", result["tip"])
        self.assertIn("12,345 u", result["tip"])
        self.assertIn("11m 46s", result["tip"])
        self.assertNotIn("moon", result["tip"])

    def test_speed_agrees_with_the_scanned_angular_rate(self):
        # orbit_period_seconds is 2*pi / orbit_speed (rad/s), so r * orbit_speed is the same number.
        omega = 0.0035676
        planet = {"orbit_distance": 46005, "orbit_period_seconds": 2 * math.pi / omega, "parent_planet": ""}
        self.assertAlmostEqual(self._run([planet])["results"][0]["value"], 46005 * omega, places=6)

    def test_a_moon_says_its_speed_is_relative_to_its_planet(self):
        moon = {"orbit_distance": 789, "orbit_period_seconds": 296.4, "parent_planet": "Alpha"}
        tip = self._run([moon])["results"][0]["tip"]
        self.assertIn("relative to Alpha", tip)

    def test_a_body_without_both_figures_or_that_does_not_move_shows_a_dash(self):
        rows = [{"orbit_distance": 5000, "orbit_period_seconds": None, "parent_planet": ""},
                {"orbit_distance": None, "orbit_period_seconds": 600, "parent_planet": ""},
                {"orbit_distance": 5000, "orbit_period_seconds": 0, "parent_planet": ""},
                {"orbit_distance": 0, "orbit_period_seconds": 600, "parent_planet": ""},
                {"orbit_distance": "", "orbit_period_seconds": "", "parent_planet": ""}]
        for result in self._run(rows)["results"]:
            self.assertIsNone(result["value"])
            self.assertEqual(result["text"], "—")
            self.assertEqual(result["tip"], "")


if __name__ == "__main__":
    unittest.main()

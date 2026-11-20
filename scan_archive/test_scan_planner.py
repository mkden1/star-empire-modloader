"""The scan archive's PLANNER tab: its catalogue, how it is wired into the page, and (through node)
the planning maths and UI actions in scan_planner.js."""
import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import scan_dashboard
import scan_logger
import scan_planner
from scan_dashboard import build_dashboard_html

HERE = os.path.dirname(os.path.abspath(__file__))


def _good_catalog():
    return {
        "version": 1,
        "kits": [{"key": "k_planet", "kind": "planet", "tech": 9, "station_slots": 9, "cost": None},
                 {"key": "k_moon", "kind": "moon", "tech": None, "station_slots": 2, "cost": {"metal": 5}}],
        "extractors": {"silicon": [{"key": "s_basic", "tier": "basic", "cost": None},
                                   {"key": "s_ind", "tier": "industrial", "tech": 9, "cost": {}}],
                       "gold": [{"key": "g1", "tier": "single", "cost": None}]},
        "gear": [{"key": "shield", "slot": "shield", "tech": 3, "size": 5, "cost": {"metal": 10.5}}],
    }


class ShippedCatalogueTests(unittest.TestCase):
    def setUp(self):
        self.catalog, self.problems = scan_planner.load_catalog()

    def test_it_loads_with_no_problems(self):
        self.assertEqual(self.problems, [])

    def test_there_is_a_t9_planet_base_and_a_moon_base(self):
        kits = {k["key"]: k for k in self.catalog["kits"]}
        self.assertEqual(kits["attached_station_kit_iii"]["tech"], 9)
        self.assertEqual(kits["attached_station_kit_iii"]["kind"], "planet")
        self.assertTrue(any(k["kind"] == "moon" for k in kits.values()))

    def test_every_resource_a_scan_can_report_has_an_extractor(self):
        # a resource with slots but nothing to extract it with would silently drop out of a plan
        missing = [r for r in scan_logger.RESOURCE_KEYS if r not in self.catalog["extractors"]]
        self.assertEqual(missing, [])

    def test_every_resource_with_three_tiers_names_them_basic_advanced_industrial(self):
        for resource, items in self.catalog["extractors"].items():
            if len(items) > 1:
                self.assertEqual([i["tier"] for i in items], ["basic", "advanced", "industrial"], resource)

    # --- the gear upgrade chains, from the player's blueprint screenshots (2026-10-12)
    FAMILIES = {   # family -> the extra material its Mk III blueprint takes
        "station_beam_cannon": {"gold": 100}, "station_mag_cannon": {"gold": 100},
        "station_energy": {"oil": 500}, "station_shield": {"titanium": 500},
        "station_shield_transference": {"gold": 100}, "station_sensor": {"titanium": 500},
    }

    def gear(self):
        return {g["key"]: g for g in self.catalog["gear"]}

    def test_every_gear_family_is_a_chain_from_a_bought_mk1(self):
        gear = self.gear()
        for family in self.FAMILIES:
            with self.subTest(family=family):
                self.assertNotIn("upgrade_from", gear[f"{family}_mk1"])
                self.assertEqual(gear[f"{family}_mk1"]["acquire"], "bought from an AI station")
                self.assertEqual(gear[f"{family}_mk2"]["upgrade_from"], f"{family}_mk1")
                self.assertEqual(gear[f"{family}_mk3"]["upgrade_from"], f"{family}_mk2")
                self.assertEqual(gear[f"{family}_mk2"]["upgrade_qty"], 1)
                self.assertEqual(gear[f"{family}_mk3"]["upgrade_qty"], 1)

    def test_mk2_upgrades_cost_what_the_blueprints_say(self):
        gear = self.gear()
        for family in self.FAMILIES:
            self.assertEqual(gear[f"{family}_mk2"]["cost"],
                             {"manhours": 100000, "metal": 200000, "microchips": 5000}, family)

    def test_mk3_upgrades_cost_what_the_blueprints_say(self):
        gear = self.gear()
        for family, extra in self.FAMILIES.items():
            self.assertEqual(gear[f"{family}_mk3"]["cost"],
                             {"manhours": 1000000, "metal": 2500000, "microchips": 100000, **extra}, family)

    # --- prices and tiers from the player's screenshots of the AI station's shop (2026-10-12)
    SHOP_EXTRACTORS = {   # key: (tech, size, buy price in credits)
        "promethium_extractor": (0, 5, 50_000_000), "silicon_drill": (0, 5, 25_000),
        "wood_cutter": (0, 4, 25_000), "gold_extractor": (1, 5, 10_000),
        "titanium_extractor": (1, 6, 1_000_000),
        "advanced_harvester": (6, 4, 500_000), "advanced_metal_drill": (6, 5, 500_000),
        "advanced_silicon_drill": (6, 5, 500_000), "advanced_wood_cutter": (6, 4, 500_000),
        "industrial_harvester": (9, 4, 1_000_000), "industrial_metal_drill": (9, 5, 1_000_000),
        "industrial_silicon_drill": (9, 5, 1_000_000), "industrial_wood_cutter": (9, 4, 1_000_000),
        # the shop's 'Station' tab, scrolled to the top
        "copper_extractor": (0, 5, 3_200), "harvester": (0, 4, 25_000), "metal_drill": (0, 5, 25_000),
        "oil_drill": (0, 5, 500_000),
    }
    MK1_PRICES = {"station_beam_cannon_mk1": 5_500, "station_energy_mk1": 6_000, "station_mag_cannon_mk1": 5_000,
                  "station_point_defense_laser_mk1": 5_500, "station_sensor_mk1": 7_000,
                  "station_shield_mk1": 8_000, "station_shield_transference_mk1": 6_000}

    def extractors(self):
        return {e["key"]: e for items in self.catalog["extractors"].values() for e in items}

    def test_extractors_cost_their_ai_station_buy_price_in_credits(self):
        extractors = self.extractors()
        for key, (tech, size, price) in self.SHOP_EXTRACTORS.items():
            with self.subTest(key=key):
                self.assertEqual(extractors[key]["cost"], {"credits": price})
                self.assertEqual((extractors[key]["tech"], extractors[key]["size"]), (tech, size))

    def test_the_shop_sells_only_the_basic_promethium_extractor(self):
        self.assertEqual([e["key"] for e in self.catalog["extractors"]["promethium"]], ["promethium_extractor"])

    def test_mk1_gear_costs_its_ai_station_buy_price(self):
        gear = self.gear()
        for key, price in self.MK1_PRICES.items():
            self.assertEqual(gear[key]["cost"], {"credits": price}, key)

    def test_mk2_is_tier_6_and_mk3_tier_9(self):
        gear = self.gear()
        for family in self.FAMILIES:
            self.assertEqual(gear[f"{family}_mk2"]["tech"], 6, family)
            self.assertEqual(gear[f"{family}_mk3"]["tech"], 9, family)

    def test_layered_plating_mk3_is_built_from_scratch_not_upgraded(self):
        plating = self.gear()["layered_plating_mk3"]
        self.assertNotIn("upgrade_from", plating)
        self.assertEqual(plating["cost"], {"manhours": 1_000_000, "credits": 5_000_000,
                                           "metal": 10_000_000, "titanium": 250})
        self.assertEqual(plating["blueprint"], "Layered Plating Mk III Blueprint")
        self.assertEqual(plating["blueprint_price"], {"credits": 25_000_000})
        self.assertEqual(plating["tech"], 9)

    def test_the_orbital_shuttle_network_is_a_priced_optional_item(self):
        shuttle = self.gear()["orbital_shuttle_network"]
        self.assertEqual((shuttle["tech"], shuttle["size"], shuttle["cost"]), (9, 1, {"credits": 50_000_000}))

    # --- output per extractor per day: read off real extractors' item stats, or flagged as assumed
    CAPTURED_RATES = {"copper_extractor": 8.64, "gold_extractor": 8.64, "titanium_extractor": 8.64,
                      "oil_drill": 8.64, "wood_cutter": 8640, "industrial_harvester": 25920,
                      "industrial_metal_drill": 25920, "industrial_silicon_drill": 25920,
                      "industrial_wood_cutter": 25920,
                      "promethium_extractor": 1440,                       # 1 per 60 s, off its tooltip
                      # the player: "the advanced versions are 17280 per day"
                      "advanced_harvester": 17280, "advanced_metal_drill": 17280,
                      "advanced_silicon_drill": 17280, "advanced_wood_cutter": 17280}
    ASSUMED_RATES = {"metal_drill": 8640, "silicon_drill": 8640, "harvester": 8640}

    def test_extractor_output_rates_match_what_real_extractors_showed(self):
        extractors = self.extractors()
        for key, rate in self.CAPTURED_RATES.items():
            self.assertEqual(extractors[key]["per_day"], rate, key)
            self.assertFalse(extractors[key].get("per_day_inferred"), key)

    def test_assumed_rates_are_flagged_as_assumed(self):
        extractors = self.extractors()
        for key, rate in self.ASSUMED_RATES.items():
            self.assertEqual(extractors[key]["per_day"], rate, key)
            self.assertTrue(extractors[key]["per_day_inferred"], key)

    def test_every_extractor_has_an_output_rate(self):
        self.assertEqual([k for k, e in self.extractors().items() if e["per_day"] is None], [])

    def test_the_tiers_make_one_two_and_three_times_the_basic_rate(self):
        extractors = self.extractors()
        for resource, basic, advanced, industrial in (
                ("metal_ore", "metal_drill", "advanced_metal_drill", "industrial_metal_drill"),
                ("silicon", "silicon_drill", "advanced_silicon_drill", "industrial_silicon_drill"),
                ("wood", "wood_cutter", "advanced_wood_cutter", "industrial_wood_cutter"),
                ("space_corn", "harvester", "advanced_harvester", "industrial_harvester")):
            with self.subTest(resource=resource):
                rate = extractors[basic]["per_day"]
                self.assertEqual(extractors[advanced]["per_day"], 2 * rate)
                self.assertEqual(extractors[industrial]["per_day"], 3 * rate)

    def test_a_tier_9_extractor_makes_three_times_a_basic_one(self):
        # the game's own ratio (colony_tool tech_multipliers: tech3 1.0, tech9 3.0)
        extractors = self.extractors()
        self.assertEqual(extractors["industrial_wood_cutter"]["per_day"], 3 * extractors["wood_cutter"]["per_day"])

    # --- station kits, from the 'Station' tab of the AI station's shop
    KITS = {   # key: (kind, tech, size, buy price in credits)
        "attached_station_kit_i": ("planet", 3, 6, 100_000),
        "attached_station_kit_ii": ("planet", 6, 6, 500_000),
        "attached_station_kit_iii": ("planet", 9, 6, 2_500_000),
        "lunar_station_kit": ("moon", 9, 6, 2_500_000),
        # free-standing stations, for defence
        "unattached_station_kit_i": ("unattached", 3, 6, 100_000),
        "unattached_station_kit_ii": ("unattached", 6, 6, 500_000),
        "unattached_station_kit_iii": ("unattached", 9, 6, 2_500_000),
    }

    def test_station_kits_cost_their_ai_station_buy_price(self):
        kits = {k["key"]: k for k in self.catalog["kits"]}
        self.assertEqual(sorted(kits), sorted(self.KITS))
        for key, (kind, tech, size, price) in self.KITS.items():
            with self.subTest(key=key):
                self.assertEqual((kits[key]["kind"], kits[key]["tech"], kits[key]["size"]), (kind, tech, size))
                self.assertEqual(kits[key]["cost"], {"credits": price})

    def test_the_t9_planet_base_is_attached_station_kit_iii(self):
        kits = {k["key"]: k for k in self.catalog["kits"]}
        top = max((k for k in kits.values() if k["kind"] == "planet"), key=lambda k: k["tech"])
        self.assertEqual(top["key"], "attached_station_kit_iii")

    def test_the_moon_base_is_labelled_as_the_shop_names_it(self):
        moon = [k for k in self.catalog["kits"] if k["kind"] == "moon"][0]
        self.assertEqual(moon["label"], "Lunar Station Kit I")

    def test_every_item_in_the_catalogue_now_has_a_cost(self):
        everything = (self.catalog["kits"] + self.catalog["gear"]
                      + [i for items in self.catalog["extractors"].values() for i in items])
        self.assertEqual([i["key"] for i in everything if i["cost"] is None], [])

    def test_the_blueprint_names_are_recorded(self):
        gear = self.gear()
        self.assertEqual(gear["station_shield_transference_mk3"]["blueprint"],
                         "Station Shield Transference Mk III Blueprint")
        self.assertEqual(gear["station_sensor_mk2"]["blueprint"], "Station Sensor Mk II Blueprint")

    def test_the_station_slot_costs_match_colony_tools_rules(self):
        for kit in self.catalog["kits"]:
            self.assertEqual(kit["station_slots"], 2 if kit["kind"] == "moon" else 9, kit["key"])

    def test_an_unattached_station_takes_the_same_slots_as_an_attached_one(self):
        # the player: "the station slots for unattached are the same as attached"
        slots = {k["kind"]: k["station_slots"] for k in self.catalog["kits"]}
        self.assertEqual(slots["unattached"], slots["planet"])

    def test_the_top_unattached_base_is_kit_iii(self):
        free = [k for k in self.catalog["kits"] if k["kind"] == "unattached"]
        self.assertEqual(max(free, key=lambda k: k["tech"])["key"], "unattached_station_kit_iii")


class ValidateCatalogTests(unittest.TestCase):
    def test_a_good_catalogue_has_no_problems(self):
        self.assertEqual(scan_planner.validate_catalog(_good_catalog()), [])

    def assertProblem(self, catalog, fragment):
        problems = scan_planner.validate_catalog(catalog)
        self.assertTrue(any(fragment in p for p in problems), (fragment, problems))

    def test_not_an_object(self):
        self.assertProblem([], "not a JSON object")

    def test_duplicate_keys_are_caught_across_groups(self):
        cat = _good_catalog()
        cat["gear"][0]["key"] = "s_basic"
        self.assertProblem(cat, "duplicate key 's_basic'")

    def test_a_missing_key_is_caught(self):
        cat = _good_catalog()
        del cat["gear"][0]["key"]
        self.assertProblem(cat, "missing key")

    def test_an_unattached_kit_is_a_valid_kind(self):
        cat = _good_catalog()
        cat["kits"].append({"key": "k_free", "kind": "unattached", "tech": 9, "station_slots": None, "cost": {"credits": 1}})
        self.assertEqual(scan_planner.validate_catalog(cat), [])

    def test_a_kit_needs_a_known_kind(self):
        cat = _good_catalog()
        cat["kits"][0]["kind"] = "asteroid"
        self.assertProblem(cat, "kind must be one of")

    def test_costs_must_be_null_or_non_negative_numbers(self):
        for bad in ("free", {"metal": "lots"}, {"metal": -1}, {"metal": True}, [1]):
            cat = _good_catalog()
            cat["gear"][0]["cost"] = bad
            self.assertProblem(cat, "cost")

    def test_tech_and_size_must_be_whole_numbers_or_null(self):
        cat = _good_catalog()
        cat["gear"][0]["tech"] = "9"
        self.assertProblem(cat, "tech must be a whole number")

    def test_tier_must_be_known(self):
        cat = _good_catalog()
        cat["extractors"]["silicon"][0]["tier"] = "quantum"
        self.assertProblem(cat, "tier must be one of")

    def test_two_extractors_cannot_share_a_tier(self):
        cat = _good_catalog()
        cat["extractors"]["silicon"][1]["tier"] = "basic"
        self.assertProblem(cat, "share a tier")

    def test_single_tier_is_only_for_a_lone_extractor(self):
        cat = _good_catalog()
        cat["extractors"]["silicon"][0]["tier"] = "single"
        self.assertProblem(cat, "cannot use tier 'single'")

    def chain_catalog(self):
        cat = _good_catalog()
        cat["gear"] = [{"key": "ga", "slot": "x", "cost": None},
                       {"key": "gb", "slot": "x", "upgrade_from": "ga", "upgrade_qty": 1, "cost": {"metal": 1}},
                       {"key": "gc", "slot": "x", "upgrade_from": "gb", "cost": {"metal": 2}}]
        return cat

    def test_a_valid_upgrade_chain_is_accepted(self):
        self.assertEqual(scan_planner.validate_catalog(self.chain_catalog()), [])

    def test_an_upgrade_must_name_a_gear_item_that_exists(self):
        cat = self.chain_catalog()
        cat["gear"][1]["upgrade_from"] = "ghost"
        self.assertProblem(cat, "upgrade_from 'ghost'")

    def test_an_upgrade_cannot_name_a_kit_or_extractor(self):
        cat = self.chain_catalog()
        cat["gear"][1]["upgrade_from"] = "k_planet"
        self.assertProblem(cat, "upgrade_from 'k_planet'")

    def test_the_upgrade_quantity_must_be_a_positive_whole_number(self):
        for bad in (0, -1, 1.5, "2", True):
            cat = self.chain_catalog()
            cat["gear"][1]["upgrade_qty"] = bad
            self.assertProblem(cat, "upgrade_qty")

    def test_a_chain_that_loops_is_rejected(self):
        cat = self.chain_catalog()
        cat["gear"][0]["upgrade_from"] = "gc"
        self.assertProblem(cat, "loops back")

    def test_an_item_cannot_upgrade_from_itself(self):
        cat = self.chain_catalog()
        cat["gear"][1]["upgrade_from"] = "gb"
        self.assertProblem(cat, "loops back")

    def test_a_blueprint_price_is_checked_like_a_cost(self):
        for bad in ("cheap", {"credits": -5}, {"credits": "lots"}):
            cat = self.chain_catalog()
            cat["gear"][1]["blueprint_price"] = bad
            self.assertProblem(cat, "blueprint_price")
        cat = self.chain_catalog()
        cat["gear"][1]["blueprint_price"] = {"credits": 25000000}
        self.assertEqual(scan_planner.validate_catalog(cat), [])

    def test_per_day_must_be_a_non_negative_number_or_null(self):
        for bad in ("fast", -1, True):
            cat = _good_catalog()
            cat["extractors"]["silicon"][0]["per_day"] = bad
            self.assertProblem(cat, "per_day must be a number")
        cat = _good_catalog()
        cat["extractors"]["silicon"][0]["per_day"] = 8.64
        cat["extractors"]["silicon"][1]["per_day"] = None
        self.assertEqual(scan_planner.validate_catalog(cat), [])

    def test_per_day_inferred_must_be_true_or_false(self):
        cat = _good_catalog()
        cat["extractors"]["silicon"][0]["per_day_inferred"] = "yes"
        self.assertProblem(cat, "per_day_inferred must be true or false")

    def test_a_note_must_be_text(self):
        cat = self.chain_catalog()
        cat["gear"][0]["note"] = 3
        self.assertProblem(cat, "note must be text")

    def test_blueprint_and_acquire_must_be_text(self):
        for field in ("blueprint", "acquire"):
            cat = self.chain_catalog()
            cat["gear"][1][field] = 5
            self.assertProblem(cat, f"{field} must be text")

    def test_an_empty_extractor_list_is_caught(self):
        cat = _good_catalog()
        cat["extractors"]["gold"] = []
        self.assertProblem(cat, "non-empty list")


class LoadCatalogTests(unittest.TestCase):
    def write(self, text):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "catalog.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_a_missing_file_gives_an_empty_catalogue_and_a_reason(self):
        catalog, problems = scan_planner.load_catalog(os.path.join(tempfile.gettempdir(), "no_such_catalog.json"))
        self.assertEqual(catalog["kits"], [])
        self.assertTrue(problems and "could not read" in problems[0])

    def test_broken_json_gives_an_empty_catalogue_and_a_reason(self):
        catalog, problems = scan_planner.load_catalog(self.write("{not json"))
        self.assertEqual(catalog["gear"], [])
        self.assertTrue(problems)

    def test_an_invalid_catalogue_is_not_used(self):
        bad = _good_catalog()
        bad["gear"][0]["cost"] = -4
        catalog, problems = scan_planner.load_catalog(self.write(json.dumps(bad)))
        self.assertEqual(catalog["gear"], [])
        self.assertTrue(problems)

    def test_a_valid_file_is_returned_as_is(self):
        catalog, problems = scan_planner.load_catalog(self.write(json.dumps(_good_catalog())))
        self.assertEqual(problems, [])
        self.assertEqual(catalog["gear"][0]["key"], "shield")

    def test_the_empty_fallback_is_not_shared_between_callers(self):
        first, _ = scan_planner.load_catalog(os.path.join(tempfile.gettempdir(), "no_such_catalog.json"))
        first["kits"].append("junk")
        second, _ = scan_planner.load_catalog(os.path.join(tempfile.gettempdir(), "no_such_catalog.json"))
        self.assertEqual(second["kits"], [])


class PlannerPageTests(unittest.TestCase):
    ROW = {"system": "Sys", "planet_name": "Alpha", "planet_type": "Terra", "parent_planet": "",
           "silicon_slots": "10"}

    def setUp(self):
        self.page = build_dashboard_html([self.ROW])

    def test_the_tab_button_and_view_are_present(self):
        self.assertIn('id="tabPlannerBtn"', self.page)
        self.assertIn('id="plannerView"', self.page)
        self.assertRegex(self.page, r'showTab\("planner"\)')

    def test_nothing_is_left_unreplaced(self):
        self.assertEqual(re.findall(r"__[A-Z][A-Z_]+__", self.page), [])

    def test_the_planner_script_follows_the_main_script(self):
        scripts = re.findall(r"<script>(.*?)</script>", self.page, re.S)
        self.assertEqual(len(scripts), 2)
        self.assertIn("const DATA = ", scripts[0])
        self.assertIn("const PLANNER_CATALOG = ", scripts[1])

    def test_the_catalogue_is_embedded_as_data(self):
        embedded = re.search(r"const PLANNER_CATALOG = (\{.*?\});\n", self.page, re.S).group(1)
        self.assertIn("attached_station_kit_iii", [k["key"] for k in json.loads(embedded)["kits"]])

    def test_the_embedded_script_has_the_core_functions(self):
        self.assertIn("function compute(", self.page)
        self.assertIn("function normalisePlan(", self.page)

    def test_text_in_the_catalogue_cannot_close_the_script_tag(self):
        catalog = _good_catalog()
        catalog["gear"][0]["label"] = "evil </script><b>"
        with mock.patch.object(scan_dashboard, "load_catalog", return_value=(catalog, [])):
            page = build_dashboard_html([self.ROW])
        self.assertEqual(len(re.findall(r"<script>", page)), 2)
        self.assertEqual(len(re.findall(r"</script>", page)), 2)

    def test_catalogue_problems_are_passed_to_the_page(self):
        with mock.patch.object(scan_dashboard, "load_catalog",
                               return_value=(copy.deepcopy(scan_planner.EMPTY_CATALOG), ["gear[0]: missing key"])):
            page = build_dashboard_html([self.ROW])
        self.assertIn('const PLANNER_PROBLEMS = ["gear[0]: missing key"]', page)

    def test_a_page_with_no_scans_still_builds(self):
        self.assertIn('id="plannerView"', build_dashboard_html([]))

    def test_the_bodies_rows_carry_what_the_planner_reads(self):
        data = json.loads(re.search(r"const DATA = (\[.*?\]);\n", self.page, re.S).group(1))
        self.assertEqual(data[0]["resources"]["silicon"], 10)      # slots per station
        self.assertEqual(data[0]["stations"], 3)                   # what the planner multiplies by


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class PlannerScriptTests(unittest.TestCase):
    def test_the_planning_maths_and_ui_actions(self):
        result = subprocess.run(["node", "test_scan_planner.js"], cwd=HERE, capture_output=True,
                                text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"\d+ passed")

    def test_the_embedded_scripts_are_valid_javascript(self):
        page = build_dashboard_html([PlannerPageTests.ROW])
        with tempfile.TemporaryDirectory() as tmp:
            for i, code in enumerate(re.findall(r"<script>(.*?)</script>", page, re.S)):
                path = os.path.join(tmp, f"page_{i}.js")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(code)
                result = subprocess.run(["node", "--check", path], capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, f"script {i}: {result.stderr}")


if __name__ == "__main__":
    unittest.main()

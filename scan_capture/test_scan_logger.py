import csv
import math
import tempfile
import unittest
from pathlib import Path

import scan_logger
from scan_logger import (FIELDNAMES, RESOURCE_LABELS, load_scanned_moons_by_system,
                          load_scanned_planets_by_type,
                          load_scanned_resources_by_system, record_scan,
                          scan_result_row, stations_for_body)


class ScanResultRowTests(unittest.TestCase):
    def test_full_payload_maps_resources_and_known_modifiers(self):
        data = {
            "ok": True,
            "planet_name": "Cryldorwen",
            "planet_type": "Terra",
            "resources": {"metal_ore": 3, "silicon": 5, "copper": 0},
            "extractors": {"metal_ore": 14, "silicon": 133, "copper": 3},
            "colonization_index": 84,
            "colonization": [
                {"category_label": "Gravity", "setting_label": "Heavy Gravity",
                 "penalty_pct": 80},
                {"category_label": "Temperature", "setting_label": "Temperate",
                 "penalty_pct": 0},
            ],
        }
        row = scan_result_row("Peacock Station", data,
                               orbit_radius=12345.0, orbit_speed=math.pi / 100)

        self.assertEqual(row["system"], "Peacock Station")
        self.assertEqual(row["planet_name"], "Cryldorwen")
        self.assertEqual(row["planet_type"], "Terra")
        self.assertEqual(row["colonization_index"], 84)
        self.assertEqual(row["orbit_distance"], 12345.0)
        self.assertAlmostEqual(row["orbit_period_seconds"], 200.0)
        self.assertEqual(row["metal_ore_slots"], 14)
        self.assertEqual(row["silicon_slots"], 133)
        # copper had a resources amount of 0, so its extractor count is
        # zeroed out even though "extractors" itself said 3 — matches the
        # client's own `if amount > 0` gate before it trusts extractor slots.
        self.assertEqual(row["copper_slots"], 0)
        self.assertEqual(row["titanium_slots"], 0)
        self.assertEqual(row["gravity_setting"], "Heavy Gravity")
        self.assertEqual(row["gravity_penalty_pct"], 80)
        self.assertEqual(row["temperature_setting"], "Temperate")
        self.assertEqual(row["temperature_penalty_pct"], 0)
        # Categories absent from this payload still get columns, just empty.
        self.assertEqual(row["atmosphere_setting"], "")
        self.assertEqual(row["extra_modifiers"], "")

    def test_unknown_modifier_category_goes_to_extra_modifiers(self):
        data = {
            "colonization": [
                {"category_label": "Radiation", "setting_label": "Elevated",
                 "penalty_pct": 15},
            ],
        }
        row = scan_result_row("Sys", data)
        self.assertEqual(row["extra_modifiers"], "Radiation: Elevated (-15%)")

    def test_missing_optional_fields_default_sanely(self):
        row = scan_result_row("Sys", {})
        self.assertEqual(row["planet_name"], "")
        self.assertEqual(row["colonization_index"], "")
        self.assertEqual(row["metal_ore_slots"], 0)
        self.assertEqual(row["gravity_setting"], "")

    def test_missing_orbit_data_leaves_orbit_columns_blank(self):
        row = scan_result_row("Sys", {})
        self.assertEqual(row["orbit_distance"], "")
        self.assertEqual(row["orbit_period_seconds"], "")

    def test_stationary_orbit_speed_reports_stationary_period(self):
        row = scan_result_row("Sys", {}, orbit_radius=500.0, orbit_speed=0.0)
        self.assertEqual(row["orbit_distance"], 500.0)
        self.assertIsNone(row["orbit_period_seconds"])

    def test_raw_orbit_speed_and_angle_seed_are_kept_alongside_the_period(self):
        row = scan_result_row("Sys", {}, orbit_radius=500.0,
                               orbit_speed=math.pi / 100,
                               orbit_angle_seed=1.25)
        self.assertAlmostEqual(row["orbit_speed"], math.pi / 100)
        self.assertEqual(row["orbit_angle_seed"], 1.25)

    def test_missing_orbit_angle_seed_left_blank(self):
        row = scan_result_row("Sys", {})
        self.assertEqual(row["orbit_speed"], "")
        self.assertEqual(row["orbit_angle_seed"], "")

    def test_parent_planet_defaults_blank(self):
        row = scan_result_row("Sys", {})
        self.assertEqual(row["parent_planet"], "")

    def test_parent_planet_recorded_for_a_moon(self):
        row = scan_result_row("Sys", {}, parent_planet="Cryldorwen")
        self.assertEqual(row["parent_planet"], "Cryldorwen")


class LoadScannedPlanetsByTypeTests(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "no_such_file.csv")
            self.assertEqual(load_scanned_planets_by_type(path), {})

    def test_groups_planet_name_to_type_by_system(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "scans.csv")
            record_scan("Peacock Station",
                         {"planet_name": "Cryldorwen", "planet_type": "Terra"},
                         out_path=path)
            record_scan("Peacock Station",
                         {"planet_name": "Xanselwen", "planet_type": "Mega Jungle"},
                         out_path=path)
            record_scan("Eddaex Prime",
                         {"planet_name": "Nyxzetherel", "planet_type": "Barren"},
                         out_path=path)

            index = load_scanned_planets_by_type(path)
            self.assertEqual(index["Peacock Station"],
                              {"Cryldorwen": "Terra", "Xanselwen": "Mega Jungle"})
            self.assertEqual(index["Eddaex Prime"], {"Nyxzetherel": "Barren"})

    def test_moons_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "scans.csv")
            record_scan("Sys", {"planet_name": "Cryldorwen", "planet_type": "Terra"},
                        out_path=path)
            record_scan("Sys",
                        {"planet_name": "Little Moon", "planet_type": "Barren"},
                        parent_planet="Cryldorwen", out_path=path)
            index = load_scanned_planets_by_type(path)
            self.assertEqual(index["Sys"], {"Cryldorwen": "Terra"})

    def test_rescanning_a_planet_does_not_duplicate_its_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "scans.csv")
            record_scan("Sys", {"planet_name": "Cryldorwen"}, out_path=path)
            record_scan("Sys", {"planet_name": "Cryldorwen"}, out_path=path)
            index = load_scanned_planets_by_type(path)
            self.assertEqual(index["Sys"], {"Cryldorwen": ""})
            with open(path, newline="", encoding="utf-8") as f:
                self.assertEqual(len(list(csv.DictReader(f))), 1)


class LoadScannedMoonsBySystemTests(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "no_such_file.csv")
            self.assertEqual(load_scanned_moons_by_system(path), {})

    def test_groups_moon_names_by_system(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "scans.csv")
            record_scan("Sys", {"planet_name": "Little Moon"},
                        parent_planet="Cryldorwen", out_path=path)
            record_scan("Sys", {"planet_name": "Other Moon"},
                        parent_planet="Cryldorwen", out_path=path)
            index = load_scanned_moons_by_system(path)
            self.assertEqual(index["Sys"], {"Little Moon", "Other Moon"})

    def test_planets_are_excluded(self):
        # Mirror image of test_moons_are_excluded on the planet-side loader
        # — a plain planet scan (no parent_planet) must never show up here.
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "scans.csv")
            record_scan("Sys", {"planet_name": "Cryldorwen"}, out_path=path)
            record_scan("Sys", {"planet_name": "Little Moon"},
                        parent_planet="Cryldorwen", out_path=path)
            index = load_scanned_moons_by_system(path)
            self.assertEqual(index["Sys"], {"Little Moon"})

    def test_every_row_lands_in_exactly_one_of_the_two_loaders(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "scans.csv")
            record_scan("Sys", {"planet_name": "Cryldorwen"}, out_path=path)
            record_scan("Sys", {"planet_name": "Little Moon"},
                        parent_planet="Cryldorwen", out_path=path)
            planets = load_scanned_planets_by_type(path)
            moons = load_scanned_moons_by_system(path)
            self.assertEqual(set(planets["Sys"]) | moons["Sys"],
                              {"Cryldorwen", "Little Moon"})
            self.assertEqual(set(planets["Sys"]) & moons["Sys"], set())


class LoadScannedResourcesBySystemTests(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "no_such_file.csv")
            self.assertEqual(load_scanned_resources_by_system(path), {})

    def test_per_body_extractor_slots_grouped_by_system(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "scans.csv")
            record_scan("Peacock Station", {
                "planet_name": "Cryldorwen",
                "resources": {"metal_ore": 3, "silicon": 5, "copper": 0},
                "extractors": {"metal_ore": 14, "silicon": 133, "copper": 3},
            }, out_path=path)

            index = load_scanned_resources_by_system(path)

            # copper had a resources amount of 0, so its slot count is
            # zeroed and dropped, same rule scan_result_row applies.
            self.assertEqual(
                index["Peacock Station"]["Cryldorwen"],
                {"metal_ore": 14, "silicon": 133})

    def test_multiple_bodies_in_a_system_stay_per_body_not_pre_summed(self):
        # The whole reason this loader keeps a per-body shape instead of a
        # running total: the galaxy map popup sums across bodies itself, so
        # a caller can tell two planets' contributions apart if it needs to.
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "scans.csv")
            record_scan("Sys", {
                "planet_name": "Alpha",
                "resources": {"metal_ore": 1}, "extractors": {"metal_ore": 10},
            }, out_path=path)
            record_scan("Sys", {
                "planet_name": "Beta",
                "resources": {"metal_ore": 1}, "extractors": {"metal_ore": 6},
            }, out_path=path)

            index = load_scanned_resources_by_system(path)

            self.assertEqual(index["Sys"]["Alpha"], {"metal_ore": 10})
            self.assertEqual(index["Sys"]["Beta"], {"metal_ore": 6})
            self.assertEqual(
                sum(body.get("metal_ore", 0)
                    for body in index["Sys"].values()), 16)

    def test_rescanning_a_body_replaces_rather_than_adds(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "scans.csv")
            record_scan("Sys", {
                "planet_name": "Cryldorwen",
                "resources": {"metal_ore": 1}, "extractors": {"metal_ore": 14},
            }, out_path=path)
            record_scan("Sys", {
                "planet_name": "Cryldorwen",
                "resources": {"metal_ore": 1}, "extractors": {"metal_ore": 14},
            }, out_path=path)

            index = load_scanned_resources_by_system(path)

            self.assertEqual(index["Sys"]["Cryldorwen"], {"metal_ore": 14})

    def test_moons_are_included_unlike_the_planet_type_loader(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "scans.csv")
            record_scan("Sys", {
                "planet_name": "Little Moon",
                "resources": {"gold": 2}, "extractors": {"gold": 5},
            }, parent_planet="Cryldorwen", out_path=path)

            index = load_scanned_resources_by_system(path)

            self.assertEqual(index["Sys"]["Little Moon"], {"gold": 5})

    def test_body_with_no_detected_resources_gets_an_empty_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "scans.csv")
            record_scan("Sys", {"planet_name": "Barren Rock"}, out_path=path)

            index = load_scanned_resources_by_system(path)

            self.assertEqual(index["Sys"]["Barren Rock"], {})


class PromethiumTests(unittest.TestCase):
    """Gas giants hold one resource, Promethium, which the Promethium Extractors take."""

    GAS_GIANT = {
        "planet_name": "Kaermaryn", "planet_type": "Gas Giant",
        "resources": {"promethium": 3}, "extractors": {"promethium": 4},
    }

    def test_the_row_has_a_promethium_slots_column(self):
        self.assertIn("promethium_slots", FIELDNAMES)
        row = scan_result_row("Sys", self.GAS_GIANT)
        self.assertEqual(row["promethium_slots"], 4)
        self.assertEqual(row["metal_ore_slots"], 0)

    def test_promethium_has_a_display_label(self):
        self.assertEqual(RESOURCE_LABELS["promethium"], "Promethium")

    def test_a_scanned_gas_giant_is_written_and_read_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "scans.csv")
            record_scan("Sys", self.GAS_GIANT, out_path=path)

            self.assertEqual(
                load_scanned_resources_by_system(path)["Sys"]["Kaermaryn"],
                {"promethium": 4})

    def test_a_csv_written_before_promethium_existed_is_upgraded_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scans.csv"
            old_fields = [f for f in FIELDNAMES if f != "promethium_slots"]
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=old_fields)
                writer.writeheader()
                writer.writerow({"system": "Sys", "planet_name": "Old Planet",
                                 "metal_ore_slots": "9"})

            record_scan("Sys", self.GAS_GIANT, out_path=str(path))   # rewrites with the new header

            index = load_scanned_resources_by_system(str(path))
            self.assertEqual(index["Sys"]["Old Planet"], {"metal_ore": 9})   # kept
            self.assertEqual(index["Sys"]["Kaermaryn"], {"promethium": 4})
            with open(path, newline="", encoding="utf-8") as f:
                self.assertEqual(csv.DictReader(f).fieldnames, list(FIELDNAMES))

    def test_the_csv_stays_per_station_whatever_the_body(self):
        # colony_tool reads these columns as per-station deposit counts and does its own station
        # arithmetic, so the x3 / x1 is applied where totals are shown, never stored.
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "scans.csv")
            record_scan("Sys", self.GAS_GIANT, out_path=path)
            record_scan("Sys", dict(self.GAS_GIANT, planet_name="Moonlet"),
                        parent_planet="Kaermaryn", out_path=path)

            index = load_scanned_resources_by_system(path)

            self.assertEqual(index["Sys"]["Kaermaryn"], {"promethium": 4})
            self.assertEqual(index["Sys"]["Moonlet"], {"promethium": 4})

    def test_a_resource_the_archive_has_no_column_for_is_reported_once(self):
        scan_logger._warned_unknown.clear()
        data = {"planet_name": "Odd", "resources": {"unobtainium": 2},
                "extractors": {"unobtainium": 3}}
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "scans.csv")
            with self.assertLogs("scan_logger", level="WARNING") as logged:
                record_scan("Sys", data, out_path=path)
                record_scan("Sys", data, out_path=path)
        self.assertEqual(len([m for m in logged.output if "unobtainium" in m]), 1)

    def test_known_resources_do_not_warn(self):
        scan_logger._warned_unknown.clear()
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "scans.csv")
            with self.assertNoLogs("scan_logger", level="WARNING"):
                record_scan("Sys", self.GAS_GIANT, out_path=path)


class StationsForBodyTests(unittest.TestCase):
    def test_three_stations_on_a_planet_one_on_a_moon(self):
        self.assertEqual(stations_for_body(False), 3)
        self.assertEqual(stations_for_body(True), 1)


if __name__ == "__main__":
    unittest.main()

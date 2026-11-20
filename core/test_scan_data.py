"""scan_data.py: the read side of the scan files, moved out of the recorder mods so the scan archive and the galaxy map
need only core. The recorders import every name back; each one still writes the very file its reader here reads."""
import csv
import os
import tempfile
import unittest

import scan_data

# (recorder module, names it must still offer, its private path helper -> the function in scan_data that is the same object)
RECORDERS = (
    ("scan_logger", ("RESOURCE_KEYS", "RESOURCE_LABELS", "STATIONS_PER_PLANET", "STATIONS_PER_MOON", "stations_for_body",
                     "load_scanned_planets_by_type", "load_scanned_resources_by_system", "load_scanned_moons_by_system"),
     "planet_scans_path"),
    ("bot_fleet_scan_logger", ("load_bot_fleet_scan",), "bot_fleet_scan_path"),
    ("colony_scan_logger", ("load_colony_scan",), "colony_scan_path"),
    ("shuttle_route_capture", ("load_shuttle_target_station_ids", "shuttle_data_has_been_captured"), "shuttle_route_path"),
    ("station_id_logger", ("load_station_names", "load_station_systems"), "station_ids_path"),
    ("station_log_capture", ("load_station_log_rows",), "station_logs_path"),
    ("trade_bot_slots_capture", ("load_trade_bot_slots",), "trade_bot_slots_path"),
    ("station_profile", ("compact_cargo", "load_profiles"), None),
)


class RecordersKeepTheirNamesTests(unittest.TestCase):
    def test_every_name_the_recorders_used_to_define_is_the_same_object_in_scan_data(self):
        import importlib
        for module_name, names, path_name in RECORDERS:
            module = importlib.import_module(module_name)
            for name in names:
                with self.subTest(module=module_name, name=name):
                    self.assertIs(getattr(module, name), getattr(scan_data, name))
            if path_name:
                with self.subTest(module=module_name, name="_default_out_path"):
                    self.assertIs(module._default_out_path, getattr(scan_data, path_name))

    def test_the_station_profile_directory_name_is_one_definition(self):
        import station_profile
        self.assertEqual(station_profile.DIR_NAME, scan_data.PROFILE_DIR_NAME)


class ReadersTests(unittest.TestCase):
    def test_a_missing_file_reads_as_nothing_scanned_yet(self):
        missing = os.path.join(tempfile.gettempdir(), "no_such_scan_file_for_the_test.csv")
        self.assertEqual(scan_data.load_scanned_planets_by_type(missing), {})
        self.assertEqual(scan_data.load_scanned_moons_by_system(missing), {})
        self.assertEqual(scan_data.load_scanned_resources_by_system(missing), {})

    def test_planets_and_moons_are_told_apart_by_the_parent_planet_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "planet_scans.csv")
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, ["system", "planet_name", "planet_type", "parent_planet", "gold_slots"])
                writer.writeheader()
                writer.writerow({"system": "Sol", "planet_name": "Earth", "planet_type": "Terra", "parent_planet": "",
                                 "gold_slots": "2"})
                writer.writerow({"system": "Sol", "planet_name": "Luna", "planet_type": "Barren", "parent_planet": "Earth",
                                 "gold_slots": ""})
            self.assertEqual(scan_data.load_scanned_planets_by_type(path), {"Sol": {"Earth": "Terra"}})
            self.assertEqual(scan_data.load_scanned_moons_by_system(path), {"Sol": {"Luna"}})
            self.assertEqual(scan_data.load_scanned_resources_by_system(path),
                             {"Sol": {"Earth": {"gold": 2}, "Luna": {}}})

    def test_a_planet_has_three_stations_and_a_moon_one(self):
        self.assertEqual((scan_data.stations_for_body(False), scan_data.stations_for_body(True)), (3, 1))

    def test_the_readers_need_nothing_but_scan_paths_and_the_standard_library(self):
        import ast
        with open(scan_data.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertEqual(imported - {"__future__", "csv", "json", "logging", "os"}, {"scan_paths"})


if __name__ == "__main__":
    unittest.main()

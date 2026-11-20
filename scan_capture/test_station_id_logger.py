import tempfile
import unittest
from pathlib import Path

from station_id_logger import load_station_ids, load_station_names, record_stations


class RecordStationsTests(unittest.TestCase):
    def test_writes_one_row_per_station(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "station_ids.csv")
            record_stations(
                "Zordoil Frontier",
                [{"station_id": 4795, "name": "rylvyron - 1"},
                 {"station_id": 4798, "name": "solrivenion - 1"}],
                out_path=path)
            loaded = load_station_ids(path)
            self.assertEqual(loaded["Zordoil Frontier"]["rylvyron - 1"], "4795")
            self.assertEqual(loaded["Zordoil Frontier"]["solrivenion - 1"], "4798")

    def test_reseeing_a_station_overwrites_rather_than_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "station_ids.csv")
            record_stations("Sys", [{"station_id": 1, "name": "old name"}], out_path=path)
            record_stations("Sys", [{"station_id": 1, "name": "renamed"}], out_path=path)
            loaded = load_station_ids(path)
            self.assertEqual(loaded["Sys"], {"renamed": "1"})

    def test_entries_missing_a_station_id_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "station_ids.csv")
            record_stations(
                "Sys",
                [{"station_id": "", "name": "no id"},
                 {"name": "missing id key"},
                 {"station_id": 7, "name": "kept"}],
                out_path=path)
            loaded = load_station_ids(path)
            self.assertEqual(loaded["Sys"], {"kept": "7"})

    def test_non_dict_entries_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "station_ids.csv")
            record_stations("Sys", ["not a dict", None, 42], out_path=path)
            self.assertEqual(load_station_ids(path), {})

    def test_two_systems_stay_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "station_ids.csv")
            record_stations("Shaula Sound", [{"station_id": 1, "name": "a"}], out_path=path)
            record_stations("Veritate Basin", [{"station_id": 1, "name": "b"}], out_path=path)
            loaded = load_station_ids(path)
            self.assertEqual(loaded["Shaula Sound"], {"a": "1"})
            self.assertEqual(loaded["Veritate Basin"], {"b": "1"})


class LoadStationIdsTests(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "does_not_exist.csv")
            self.assertEqual(load_station_ids(path), {})


class LoadStationNamesTests(unittest.TestCase):
    def test_reverse_mapping_from_station_id_to_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "station_ids.csv")
            record_stations("Zordoil Frontier", [
                {"station_id": "7", "name": "Londdalen - 1"},
                {"station_id": "8", "name": "Londdalen - 2"},
            ], out_path=path)
            self.assertEqual(load_station_names(path),
                              {"7": "Londdalen - 1", "8": "Londdalen - 2"})

    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "does_not_exist.csv")
            self.assertEqual(load_station_names(path), {})

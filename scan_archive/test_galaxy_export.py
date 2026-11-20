import json
import math
import unittest

from galaxy_export import WORLD_CENTER, build_export_json


def _system(sun_x=0.0, sun_y=0.0, gates=None):
    return {"sun_x": sun_x, "sun_y": sun_y, "warp_gates": gates or []}


class BuildExportJsonTests(unittest.TestCase):
    def test_gate_target_becomes_symmetric_adjacency(self):
        visited = {
            "Peacock Station": _system(gates=[
                {"name": "Gate A", "x": 100.0, "y": 0.0,
                 "target_system": "Mjolnir Hollows"},
            ]),
        }
        payload = json.loads(build_export_json(visited, []))
        self.assertEqual(
            payload["systems"]["Peacock Station"]["connects_to"],
            ["Mjolnir Hollows"])
        # Mjolnir Hollows hasn't been visited, so we only know it connects
        # back and have no gate list for it — but its sun still resolves to
        # the universal world-center convention, flagged as unmeasured.
        entry = payload["systems"]["Mjolnir Hollows"]
        self.assertEqual(entry["connects_to"], ["Peacock Station"])
        self.assertEqual(entry["sun"], {"x": WORLD_CENTER, "y": WORLD_CENTER})
        self.assertFalse(entry["sun_measured"])
        self.assertEqual(entry["gates"], [])

    def test_gate_distance_and_bearing_measured_from_the_sun(self):
        visited = {
            "Sys": _system(sun_x=10.0, sun_y=10.0, gates=[
                {"name": "Gate", "x": 110.0, "y": 10.0,
                 "target_system": "Other"},
            ]),
        }
        payload = json.loads(build_export_json(visited, []))
        gate = payload["systems"]["Sys"]["gates"][0]
        self.assertAlmostEqual(gate["distance_from_sun"], 100.0)
        self.assertAlmostEqual(gate["bearing_deg"], 0.0)

    def test_gate_bearing_matches_atan2_convention_for_a_diagonal(self):
        visited = {
            "Sys": _system(sun_x=0.0, sun_y=0.0, gates=[
                {"name": "Gate", "x": 0.0, "y": 100.0,
                 "target_system": "Other"},
            ]),
        }
        payload = json.loads(build_export_json(visited, []))
        gate = payload["systems"]["Sys"]["gates"][0]
        self.assertAlmostEqual(gate["bearing_deg"], 90.0)

    def test_missing_sun_position_falls_back_to_world_center(self):
        # Shaped like an EXPLORED_SYSTEMS login-restore entry: gates and
        # target_system are known, but sun_x/sun_y were never sent, since
        # that only arrives via a live visit this session. The sun still
        # resolves (every system's sits at the same fixed origin) — flagged
        # unmeasured rather than silently indistinguishable from a real one.
        visited = {
            "Old System": {
                "warp_gates": [
                    {"name": "Gate", "x": 200.0, "y": 200.0,
                     "target_system": "Other"},
                ],
            },
        }
        payload = json.loads(build_export_json(visited, []))
        entry = payload["systems"]["Old System"]
        self.assertEqual(entry["sun"], {"x": WORLD_CENTER, "y": WORLD_CENTER})
        self.assertFalse(entry["sun_measured"])
        gate = entry["gates"][0]
        expected_dist = math.hypot(200.0 - WORLD_CENTER, 200.0 - WORLD_CENTER)
        self.assertAlmostEqual(gate["distance_from_sun"], expected_dist)
        self.assertIsNotNone(gate["bearing_deg"])
        self.assertEqual(gate["target_system"], "Other")
        self.assertEqual(entry["connects_to"], ["Other"])

    def test_measured_sun_is_flagged_as_such(self):
        visited = {"Sys": _system(sun_x=42.0, sun_y=99.0)}
        payload = json.loads(build_export_json(visited, []))
        entry = payload["systems"]["Sys"]
        self.assertEqual(entry["sun"], {"x": 42.0, "y": 99.0})
        self.assertTrue(entry["sun_measured"])

    def test_gate_missing_position_reports_null_regardless_of_sun(self):
        visited = {
            "Sys": _system(sun_x=0.0, sun_y=0.0, gates=[
                {"name": "Gate", "target_system": "Other"},  # no x/y
            ]),
        }
        payload = json.loads(build_export_json(visited, []))
        gate = payload["systems"]["Sys"]["gates"][0]
        self.assertIsNone(gate["distance_from_sun"])
        self.assertIsNone(gate["bearing_deg"])

    def test_system_with_no_gates_still_gets_empty_connects_to(self):
        visited = {"Isolated": _system()}
        payload = json.loads(build_export_json(visited, []))
        self.assertEqual(payload["systems"]["Isolated"]["connects_to"], [])

    def test_planet_rows_pass_through_planet_records(self):
        rows = [{
            "system": "Peacock Station", "planet_name": "Cryldorwen",
            "planet_type": "Terra", "colonization_index": "84",
        }]
        payload = json.loads(build_export_json({}, rows))
        self.assertEqual(len(payload["planets"]), 1)
        self.assertEqual(payload["planets"][0]["planet_name"], "Cryldorwen")

    def test_server_elapsed_is_embedded(self):
        payload = json.loads(build_export_json({}, [], server_elapsed=1234.5))
        self.assertEqual(payload["server_elapsed"], 1234.5)

    def test_no_data_still_produces_valid_json(self):
        payload = json.loads(build_export_json({}, []))
        self.assertEqual(payload["systems"], {})
        self.assertEqual(payload["planets"], [])
        self.assertIsNone(payload["server_elapsed"])
        self.assertEqual(payload["explored_systems"], {})

    def test_explored_systems_surfaces_planet_types_with_no_scan_needed(self):
        # Real bug found this session: EXPLORED_SYSTEMS/a live visit already gives the client a
        # full planet-type breakdown for a system the moment you fly into it -- no scan required
        # -- but it was being captured onto visited_systems and then never actually surfaced in
        # this export at all. "explored_systems" (aggregate counts, not named planets -- that's
        # all this data ever was) fixes that.
        visited = {"Erebus System": {
            "sun_x": 0.0, "sun_y": 0.0, "warp_gates": [],
            "planet_types": {"Terra": 2, "Barren": 1}, "moon_count": 3,
        }}
        payload = json.loads(build_export_json(visited, []))
        self.assertEqual(payload["explored_systems"]["Erebus System"],
                          {"planet_types": {"Barren": 1, "Terra": 2}, "moon_count": 3,
                           "hazard_rating": 0})

    def test_hazard_rating_is_surfaced_the_same_way(self):
        # Same data source as planet_types/moon_count -- see
        # protocol._on_explored_systems/Client.py's live layout capture -- no scan needed.
        visited = {"Erebus System": {
            "sun_x": 0.0, "sun_y": 0.0, "warp_gates": [],
            "planet_types": {"Terra": 1}, "moon_count": 0, "hazard_rating": 20,
        }}
        payload = json.loads(build_export_json(visited, []))
        self.assertEqual(payload["explored_systems"]["Erebus System"]["hazard_rating"], 20)

    def test_a_system_with_no_planet_type_data_is_omitted_from_explored_systems(self):
        # A system known only via a gate reference from elsewhere (never actually visited) has no
        # planet_types/moon_count at all -- it shouldn't get a bogus all-zero entry.
        visited = {"Never Visited": {"sun_x": 0.0, "sun_y": 0.0, "warp_gates": []}}
        payload = json.loads(build_export_json(visited, []))
        self.assertNotIn("Never Visited", payload["explored_systems"])

    def test_zero_count_planet_types_are_dropped(self):
        visited = {"Sys": {"sun_x": 0.0, "sun_y": 0.0, "warp_gates": [],
                            "planet_types": {"Terra": 2, "Barren": 0}, "moon_count": 0}}
        payload = json.loads(build_export_json(visited, []))
        self.assertEqual(payload["explored_systems"]["Sys"]["planet_types"], {"Terra": 2})

    def test_moon_count_alone_still_qualifies_a_system_for_inclusion(self):
        visited = {"Sys": {"sun_x": 0.0, "sun_y": 0.0, "warp_gates": [],
                            "planet_types": {}, "moon_count": 2}}
        payload = json.loads(build_export_json(visited, []))
        self.assertEqual(payload["explored_systems"]["Sys"],
                          {"planet_types": {}, "moon_count": 2, "hazard_rating": 0})


if __name__ == "__main__":
    unittest.main()

"""station_bodies.py: which planet or moon a station is built on (shared by station_profile and station_auto_name)."""
import unittest

import station_bodies
import station_profile

LAYOUT = {"planets": [
    {"id": 7, "name": "Casashius"},
    {"id": 8, "name": "Casa Minor", "is_moon": True, "parent_planet_id": 7},
    "not a planet",
]}


class BodyOfTests(unittest.TestCase):
    def test_a_planet_is_found_by_the_dock_payloads_planet_id(self):
        self.assertEqual(station_bodies.body_of({"planet_id": 7}, LAYOUT, [], 1),
                         {"name": "Casashius", "kind": "planet", "parent": None})

    def test_a_moon_names_its_parent_and_ids_match_as_numbers_or_text(self):
        self.assertEqual(station_bodies.body_of({"planet_id": "8"}, LAYOUT, [], 1),
                         {"name": "Casa Minor", "kind": "moon", "parent": "Casashius"})

    def test_the_possessions_list_fills_in_when_the_layout_does_not_know_the_body(self):
        rows = [{"station_id": 5, "body_name": "Rylvyron", "body_kind": "Moon"}, "junk"]
        self.assertEqual(station_bodies.body_of({"planet_id": 99}, LAYOUT, rows, "5"),
                         {"name": "Rylvyron", "kind": "moon", "parent": None})
        rows[0]["body_kind"] = "asteroid"
        self.assertEqual(station_bodies.body_of({}, None, rows, 5)["kind"], None)

    def test_an_unknown_station_has_no_body(self):
        self.assertEqual(station_bodies.body_of({}, {}, [], 1), {"name": None, "kind": None, "parent": None})

    def test_same_compares_ids_whatever_their_type(self):
        self.assertTrue(station_bodies.same(3, "3"))
        self.assertFalse(station_bodies.same(3, 4))
        self.assertTrue(station_bodies.same("abc", "abc"))
        self.assertFalse(station_bodies.same(None, 0))


class StationProfileKeepsTheNamesTests(unittest.TestCase):
    def test_station_profile_still_offers_body_of_and_its_own_same(self):
        self.assertIs(station_profile.body_of, station_bodies.body_of)
        self.assertIs(station_profile._same, station_bodies.same)


if __name__ == "__main__":
    unittest.main()

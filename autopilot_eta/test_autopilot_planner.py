"""autopilot_planner.py: the route as gate-to-gate legs (build_legs), which the banner's ETA flies."""
import unittest

import autopilot_planner as planner


def visited_fixture():
    def gate(name, x, y, target):
        return {"name": name, "x": x, "y": y, "target_system": target, "target_gate": ""}
    return {
        "B": {"warp_gates": [gate("to A", 0.0, 0.0, "A"), gate("to C", 0.0, 12000.0, "C"),
                             gate("to Z", 5000.0, 5000.0, "Z")]},
        "C": {"warp_gates": [gate("to B", 100.0, 100.0, "B"), gate("to D", -3000.0, 7000.0, "D")]},
    }


class BuildLegsTests(unittest.TestCase):
    def test_legs_follow_the_route_through_the_paired_gates(self):
        legs = planner.build_legs((0.0, -12000.0), ["B", "C", "D"], "A", visited_fixture())
        self.assertEqual(legs, [
            planner.Leg(None, (0.0, -12000.0), False),
            planner.Leg((0.0, 0.0), (0.0, 12000.0), False),       # B: in from A, out to C
            planner.Leg((100.0, 100.0), (-3000.0, 7000.0), True),  # C: in from B, out to D (the last gate)
        ])

    def test_a_one_system_route_is_just_the_last_gate(self):
        legs = planner.build_legs((1.0, 2.0), ["B"], "A", visited_fixture())
        self.assertEqual(legs, [planner.Leg(None, (1.0, 2.0), True)])
        self.assertEqual(planner.build_legs((1.0, 2.0), [], "A", visited_fixture()), [])

    def test_missing_geometry_ends_the_list_early(self):
        legs = planner.build_legs((0.0, -12000.0), ["B", "C", "D"], "A", {"B": visited_fixture()["B"]})
        self.assertEqual(len(legs), 2)
        self.assertFalse(legs[-1].final)                    # not the end of the route, just the end of what is known
        self.assertEqual(len(planner.build_legs((0.0, 0.0), ["Q", "R"], "A", visited_fixture())), 1)

    def test_names_are_matched_without_regard_to_spaces_and_case(self):
        legs = planner.build_legs((0.0, 0.0), [" b ", "C"], " A ", visited_fixture())
        self.assertEqual(len(legs), 2)
        self.assertTrue(legs[1].final)


if __name__ == "__main__":
    unittest.main()

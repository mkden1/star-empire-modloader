"""autopilot_route_sim.py: the rest of a route flown on the game's own flight plan (the estimate behind the ETA).

Everything here is pure; the leg rules it copies from Client.py (core-circle warp, last-gate stop, warp arrival
keeps the velocity) are pinned against the game's source in test_autopilot_eta.HookSourceTests."""
import math
import unittest
from dataclasses import replace
from unittest import mock

import autopilot_planner as planner
import autopilot_route_sim as sim
import flight_plan as fp
from autopilot_route import WARP_ACTIVATION_RADIUS

SHIP = planner.ShipStats(accel=64.9, turn_rate=20.1, max_speed=677.0)      # the user's heavy ship
FLIGHT_SHIP = fp.Ship(SHIP.accel, SHIP.turn_rate, SHIP.max_speed)
CRUISING_NORTH = (0.0, 0.0, 0.0, -677.0, 0.0)                              # x, y, vx, vy, nose angle
CRUISING_EAST = (0.0, 0.0, 677.0, 0.0, 90.0)


def leg(entry, exit_, final=False, core=40.0):
    return planner.Leg(entry, exit_, final, core)


def straight_legs():
    return [leg(None, (0.0, -12000.0)), leg((0.0, 0.0), (0.0, -12000.0), final=True)]


def hairpin_legs():
    """Fly 12,000 units north through gate one, appear at (0, 0) in the next system, whose last gate is 12,000
    units SOUTH of there."""
    return [leg(None, (0.0, -12000.0)), leg((0.0, 0.0), (0.0, 12000.0), final=True)]


class FlyLegTests(unittest.TestCase):
    def reach(self, y=-12000.0, radius=40.0):
        return fp.Leg(0.0, y, fp.REACH, radius=radius)

    def test_a_straight_reach_leg_takes_its_distance_over_top_speed(self):
        seconds, end = sim.fly_leg(FLIGHT_SHIP, CRUISING_NORTH, self.reach(), fp.Pilot())
        self.assertAlmostEqual(seconds, (12000.0 - 40.0) / 677.0, delta=0.2)
        self.assertAlmostEqual(end[1], -12000.0 + 40.0, delta=677.0 / 30.0)        # it ends on the core's edge
        self.assertAlmostEqual(math.hypot(end[2], end[3]), 677.0, delta=1.0)       # still at full speed

    def test_a_reach_leg_that_starts_inside_the_core_is_over_at_once(self):
        seconds, end = sim.fly_leg(FLIGHT_SHIP, (0.0, -11990.0, 0.0, -677.0, 0.0), self.reach(), fp.Pilot())
        self.assertEqual(seconds, 0.0)
        self.assertEqual(end, (0.0, -11990.0, 0.0, -677.0, 0.0))

    def test_a_bigger_core_ends_the_leg_sooner(self):
        small = sim.fly_leg(FLIGHT_SHIP, CRUISING_NORTH, self.reach(radius=40.0), fp.Pilot())[0]
        large = sim.fly_leg(FLIGHT_SHIP, CRUISING_NORTH, self.reach(radius=400.0), fp.Pilot())[0]
        self.assertGreater(small - large, 0.3)

    def test_a_stop_leg_ends_slow_inside_the_arrival_circle(self):
        stop = fp.Leg(0.0, -12000.0, fp.STOP, radius=sim.FINAL_RADIUS, aim=sim.FINAL_AIM)
        seconds, end = sim.fly_leg(FLIGHT_SHIP, CRUISING_NORTH, stop, fp.Pilot())
        self.assertGreater(seconds, 12000.0 / 677.0)                         # stopping costs time on top of the cruise
        self.assertLessEqual(math.hypot(end[0], end[1] + 12000.0), sim.FINAL_RADIUS)
        self.assertLess(math.hypot(end[2], end[3]), 2.0 * SHIP.accel * sim.DT + 0.1)    # steer's own 'slow enough' bound

    def test_a_leg_that_never_ends_is_no_estimate(self):
        self.assertIsNone(sim.fly_leg(FLIGHT_SHIP, CRUISING_NORTH, self.reach(), fp.Pilot(), max_s=5.0))

    def test_pause_is_called_while_a_long_leg_is_flown(self):
        calls = []
        sim.fly_leg(FLIGHT_SHIP, CRUISING_NORTH, self.reach(), fp.Pilot(), pause=lambda: calls.append(1))
        self.assertGreaterEqual(len(calls), 3)

    def test_the_maneuver_of_the_pilot_changes_a_leg_that_starts_on_a_wrong_course(self):
        leg_ = self.reach()
        curve = sim.fly_leg(FLIGHT_SHIP, CRUISING_EAST, leg_, fp.Pilot(fp.CURVE))[0]
        hold = sim.fly_leg(FLIGHT_SHIP, CRUISING_EAST, leg_, fp.Pilot(fp.HOLD_SPEED))[0]
        self.assertGreater(hold - curve, 5.0)


class WarpDueTests(unittest.TestCase):
    """Client.py ``_ap_flythrough_warp_due``: the gate's core entered, or the closest approach inside the
    activation circle once the ship moves away from the centre."""
    GATE = fp.Leg(0.0, 0.0, fp.REACH, radius=40.0)

    def due(self, x, y, vx, vy, previous=None):
        return sim._warp_due(x, y, vx, vy, self.GATE, previous)

    def test_the_core_entered(self):
        self.assertTrue(self.due(0.0, 30.0, 0.0, -677.0))
        self.assertFalse(self.due(0.0, 300.0, 0.0, -677.0))                   # inside 450 but still closing in

    def test_a_step_through_the_core_is_due_and_one_passing_wide_of_it_is_not(self):
        # (The game checks the core by the step's segment; for any step shorter than the activation circle the
        # closest-approach rule below already holds from the far side, so this pins the result, not the branch.)
        self.assertTrue(self.due(0.0, -100.0, 0.0, -677.0, previous=(0.0, 100.0)))
        self.assertFalse(self.due(500.0, -100.0, 0.0, -677.0, previous=(500.0, 100.0)))    # 500 wide, outside 450

    def test_a_course_that_misses_the_core_warps_at_its_closest_approach(self):
        self.assertFalse(self.due(300.0, 100.0, 0.0, -677.0))                 # still approaching the closest point
        self.assertTrue(self.due(300.0, -100.0, 0.0, -677.0))                 # past it, inside 450, moving away

    def test_outside_the_activation_circle_nothing_warps(self):
        self.assertFalse(self.due(0.0, -(WARP_ACTIVATION_RADIUS + 50.0), 0.0, -677.0))


class RouteTests(unittest.TestCase):
    def test_a_straight_run_is_its_two_legs_plus_a_warp_after_each(self):
        got = sim.estimate_route_time(straight_legs(), CRUISING_NORTH, SHIP)
        first = (12000.0 - 40.0) / 677.0
        stop = fp.Leg(0.0, -12000.0, fp.STOP, radius=sim.FINAL_RADIUS, aim=sim.FINAL_AIM)
        final = sim.fly_leg(FLIGHT_SHIP, CRUISING_NORTH, stop, fp.Pilot())[0]
        self.assertAlmostEqual(got, first + final + 2 * planner.WARP_TRANSITION_S, delta=0.4)

    def test_the_dev_flight_plans_own_closed_form_estimate_agrees_on_a_long_stop(self):
        """flight_plan.plan estimates a leg with a coarse test flight plus a closed-form remainder; the full
        simulation of the same leg must land within a few percent of it."""
        stop = fp.Leg(0.0, -12000.0, fp.STOP, radius=sim.FINAL_RADIUS, aim=sim.FINAL_AIM)
        dev = fp.plan(0.0, 0.0, 0.0, -677.0, 0.0, FLIGHT_SHIP, stop).seconds[0][1]
        ours = sim.estimate_route_time([leg(None, (0.0, -12000.0), final=True)], CRUISING_NORTH, SHIP, warp_s=0.0)
        self.assertLess(abs(ours - dev) / dev, 0.04)

    def test_each_warp_costs_about_the_measured_round_trip(self):
        """The client's own SOLAR_WARP -> layout timing over 561 warps: median 0.20 s, p90 0.27 s."""
        self.assertTrue(0.15 <= planner.WARP_TRANSITION_S <= 0.5)
        free = sim.estimate_route_time(straight_legs(), CRUISING_NORTH, SHIP, warp_s=0.0)
        costed = sim.estimate_route_time(straight_legs(), CRUISING_NORTH, SHIP)
        self.assertAlmostEqual(costed - free, 2 * planner.WARP_TRANSITION_S)

    def test_no_estimate_when_the_route_is_not_known_to_its_end(self):
        self.assertIsNone(sim.estimate_route_time([leg(None, (0.0, -12000.0))], CRUISING_NORTH, SHIP))
        self.assertIsNone(sim.estimate_route_time([], CRUISING_NORTH, SHIP))

    def test_a_later_leg_without_an_arrival_gate_is_no_estimate(self):
        legs = [leg(None, (0.0, -12000.0)), leg(None, (0.0, -12000.0), final=True)]
        self.assertIsNone(sim.estimate_route_time(legs, CRUISING_NORTH, SHIP))

    def test_a_hairpin_is_much_slower_than_a_straight_run(self):
        straight = sim.estimate_route_time(straight_legs(), CRUISING_NORTH, SHIP)
        hairpin = sim.estimate_route_time(hairpin_legs(), CRUISING_NORTH, SHIP)
        self.assertGreater(hairpin - straight, 10.0)                          # the ship must turn its whole velocity round

    def test_a_warp_arrival_keeps_the_velocity_and_the_nose_but_takes_the_new_position(self):
        seen = []
        real_plan = fp.plan

        def spy(x, y, vx, vy, angle, ship, leg_, **kwargs):
            seen.append((x, y, vx, vy, angle))
            return real_plan(x, y, vx, vy, angle, ship, leg_, **kwargs)

        with mock.patch.object(fp, "plan", spy):
            sim.estimate_route_time([leg(None, (0.0, -12000.0)), leg((500.0, 600.0), (0.0, -12000.0), final=True)],
                                    CRUISING_NORTH, SHIP)
        self.assertEqual(len(seen), 2)                                        # leg 0 and the last gate each planned a maneuver
        x, y, vx, vy, angle = seen[1]
        self.assertEqual((x, y), (500.0, 600.0))
        self.assertAlmostEqual(math.hypot(vx, vy), 677.0, delta=1.0)
        self.assertAlmostEqual(vy, -677.0, delta=1.0)
        self.assertAlmostEqual(angle % 360.0, 0.0, delta=0.5)

    def test_the_live_pilot_is_used_for_the_first_leg_only_and_never_changed(self):
        live = fp.Pilot(fp.HOLD_SPEED)
        live_before = replace(live)
        with mock.patch.object(fp, "plan", wraps=fp.plan) as planned:
            sim.estimate_route_time(straight_legs(), CRUISING_EAST, SHIP, pilot=live)
        self.assertEqual(planned.call_count, 1)                               # only the last gate picks a maneuver itself
        self.assertEqual(live, live_before)
        with_hold = sim.estimate_route_time(straight_legs(), CRUISING_EAST, SHIP, pilot=live)
        without = sim.estimate_route_time(straight_legs(), CRUISING_EAST, SHIP)
        self.assertGreater(with_hold - without, 5.0)

    def test_a_heavier_ship_takes_longer(self):
        heavy = replace(SHIP, accel=SHIP.accel * 0.5, turn_rate=SHIP.turn_rate * 0.5)
        self.assertGreater(sim.estimate_route_time(hairpin_legs(), CRUISING_NORTH, heavy),
                           sim.estimate_route_time(hairpin_legs(), CRUISING_NORTH, SHIP))

    def test_a_longer_route_takes_longer(self):
        legs = [leg(None, (0.0, -9000.0)), leg((0.0, 0.0), (0.0, -9000.0)),
                leg((0.0, 0.0), (0.0, -9000.0), final=True)]
        self.assertGreater(sim.estimate_route_time(legs, CRUISING_NORTH, SHIP),
                           sim.estimate_route_time(straight_legs(), CRUISING_NORTH, SHIP))

    def test_a_leg_that_cannot_be_flown_is_no_estimate(self):
        with mock.patch.object(sim, "MAX_LEG_S", 2.0):
            self.assertIsNone(sim.estimate_route_time(straight_legs(), CRUISING_NORTH, SHIP))

    def test_one_unflyable_leg_among_flyable_ones_is_still_no_estimate(self):
        legs = [leg(None, (0.0, -30000.0)), leg((0.0, 0.0), (0.0, -12000.0), final=True)]    # 44 s, then 23 s
        with mock.patch.object(sim, "MAX_LEG_S", 25.0):
            self.assertIsNone(sim.estimate_route_time(legs, CRUISING_NORTH, SHIP))
            self.assertIsNotNone(sim.estimate_route_time(straight_legs(), CRUISING_NORTH, SHIP))   # each leg fits in 25 s

    def test_the_estimate_costs_little_enough_to_run_beside_the_game(self):
        import time
        legs = ([leg(None, (0.0, -9000.0))] + [leg((0.0, 0.0), (3000.0 * (-1) ** k, -9000.0)) for k in range(8)]
                + [leg((0.0, 0.0), (0.0, -9000.0), final=True)])
        began = time.perf_counter()
        self.assertIsNotNone(sim.estimate_route_time(legs, CRUISING_NORTH, SHIP))
        self.assertLess(time.perf_counter() - began, 2.0)                    # a ten-gate route, a generous bound


class LegForTests(unittest.TestCase):
    def test_a_mid_route_gate_is_a_reach_leg_into_its_core(self):
        flight_leg = sim.leg_for(leg(None, (1.0, 2.0), core=77.0))
        self.assertEqual((flight_leg.x, flight_leg.y, flight_leg.mode, flight_leg.radius), (1.0, 2.0, fp.REACH, 77.0))

    def test_the_last_gate_is_a_stop_leg_with_the_games_radius_and_aim(self):
        flight_leg = sim.leg_for(leg(None, (1.0, 2.0), final=True))
        self.assertEqual((flight_leg.mode, flight_leg.radius, flight_leg.aim),
                         (fp.STOP, WARP_ACTIVATION_RADIUS * 0.5, 120.0))


class BuildLegsCoreTests(unittest.TestCase):
    def gate(self, x, y, target, **extra):
        return {"x": x, "y": y, "target_system": target, **extra}

    def visited(self):
        return {"B": {"warp_gates": [self.gate(0.0, 0.0, "A"), self.gate(0.0, 12000.0, "C", radius=90.0)]},
                "C": {"warp_gates": [self.gate(1.0, 1.0, "B"), self.gate(2.0, 2.0, "D", radius=5.0)]}}

    def test_every_leg_gets_the_core_of_its_exit_gate(self):
        legs = planner.build_legs((0.0, -1000.0), ["B", "C", "D"], "A", self.visited(), current_core=55.0)
        self.assertEqual([item.core for item in legs], [55.0, 90.0, 25.0])      # 5.0 is under the game's minimum

    def test_a_gate_without_a_radius_is_forty_and_the_minimum_is_the_callers(self):
        visited = {"B": {"warp_gates": [self.gate(0.0, 0.0, "A"), self.gate(0.0, 12000.0, "C")]}}
        legs = planner.build_legs((0.0, -1000.0), ["B", "C"], "A", visited)
        self.assertEqual([item.core for item in legs], [40.0, 40.0])
        visited["B"]["warp_gates"][1]["radius"] = 10.0
        self.assertEqual(planner.build_legs((0.0, -1.0), ["B", "C"], "A", visited, core_min=30.0)[1].core, 30.0)
        visited["B"]["warp_gates"][1]["radius"] = "wide"
        self.assertEqual(planner.build_legs((0.0, -1.0), ["B", "C"], "A", visited)[1].core, 40.0)

    def test_the_geometry_is_otherwise_what_it_was(self):
        legs = planner.build_legs((0.0, -12000.0), ["B", "C"], "A", self.visited())
        self.assertEqual([(item.entry, item.exit, item.final) for item in legs],
                         [(None, (0.0, -12000.0), False), ((0.0, 0.0), (0.0, 12000.0), True)])


if __name__ == "__main__":
    unittest.main()

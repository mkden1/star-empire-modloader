"""autopilot_eta.py: the estimated time remaining on the route autopilot's banner.

Since v0.5.82 the autopilot flies on the dev's flight_plan; the estimate flies the rest of the route on it
too (autopilot_route_sim). These tests use a small fake window whose ``_ap_fly`` is a stub, because what is
under test is the hook, the worker and the banner; the flying itself is test_autopilot_route_sim.py."""
import modloader
import logging
import os
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import autopilot_eta as eta
import autopilot_planner as planner
import autopilot_route_sim as route_sim
import flight_plan
import render_mixin
from test_locked_gate_autopilot import SolarSystemWindow

SHIP = planner.ShipStats(accel=64.9, turn_rate=20.1, max_speed=677.0)
FRAME = (0.0, -12000.0, flight_plan.REACH, 40.0, 0.0, 677.0, 64.9, 1 / 30)    # _ap_fly(tx, ty, mode, radius, aim, max_spd, accel, dt)
STOP_FRAME = (0.0, -12000.0, flight_plan.STOP, 225.0, 120.0, 677.0, 64.9, 1 / 30)


def visited_fixture():
    def gate(name, x, y, target, **extra):
        return {"name": name, "x": x, "y": y, "target_system": target, "target_gate": "", **extra}
    return {
        "B": {"warp_gates": [gate("to A", 0.0, 0.0, "A"), gate("to C", 0.0, 12000.0, "C"),
                             gate("to Z", 5000.0, 5000.0, "Z")]},
        "C": {"warp_gates": [gate("to B", 100.0, 100.0, "B"), gate("to D", -3000.0, 7000.0, "D")]},
    }


class FlightWindow:
    """Just enough window for the hook: the autopilot's one steering call, as a stub."""
    _AP_GATE_CORE_MIN = 25.0

    def _ap_fly(self, tx, ty, mode, radius, aim, max_spd, accel, dt):
        self.calls.append((tx, ty, mode, radius, aim, max_spd, accel, dt))
        return True, False, False


def fake_window(cls, *, route=("B", "C"), speed=677.0, dungeon=False):
    win = cls()
    win.calls = []
    win._local_vx, win._local_vy = 0.0, -speed
    win._local_x, win._local_y, win._local_angle = 0.0, 0.0, 0.0
    win._autopilot = True
    win._autopilot_route = list(route)
    win._autopilot_target = (0.0, -12000.0)
    win._autopilot_route_dungeon_gate_id = None
    win._layout = {"system_name": "A", **({"is_dungeon": True} if dungeon else {})}
    win._lock = threading.RLock()
    win._visited_systems = visited_fixture()
    win._TURN_RATE, win._turning_limiter = 20.1, 100.0
    return win


class EtaFixture(unittest.TestCase):
    def setUp(self):
        eta.reset_for_tests()
        self.addCleanup(eta.reset_for_tests)
        self.windows = []
        self.addCleanup(self.stop_workers)

    def stop_workers(self):
        for win in self.windows:
            win._autopilot = False
            state = getattr(win, "_ap_eta_state", None)
            if state is not None:
                state.stop.set()
                if state.thread is not None:
                    state.thread.join(timeout=5)

    def real_window(self, **kwargs):
        cls = type("EtaWin", (FlightWindow,), {})
        self.assertTrue(eta.install(cls))
        win = fake_window(cls, **kwargs)
        self.windows.append(win)
        return win


class FormatTests(unittest.TestCase):
    def test_minutes_and_seconds_rounded_up(self):
        for seconds, text in ((0, "0:00"), (0.2, "0:01"), (7, "0:07"), (59.01, "1:00"), (102, "1:42"),
                              (600, "10:00"), (3723, "1:02:03"), (-5, "0:00")):
            with self.subTest(seconds=seconds):
                self.assertEqual(eta.format_eta(seconds), text)


class BannerTests(EtaFixture):
    def render_module(self):
        module = SimpleNamespace()
        module._format_autopilot_status = lambda route, dest="", name="": (
            f"Autopiloting to {dest or route[-1]}, {len(route)} jump{'s' if len(route) != 1 else ''} left"
            if route else "Autopilot engaged")
        return module

    def test_the_estimate_is_appended_and_counts_down(self):
        module = self.render_module()
        self.assertTrue(eta.install_banner(module))
        self.assertFalse(eta.install_banner(module))        # twice is harmless
        self.assertEqual(module._format_autopilot_status(["Beta", "Gamma"], "Gamma"),
                         "Autopiloting to Gamma, 2 jumps left")     # nothing estimated yet
        eta._publish("Gamma", 65.0)
        self.assertEqual(module._format_autopilot_status(["Beta", "Gamma"], "Gamma"),
                         "Autopiloting to Gamma, 2 jumps left  ·  ETA 1:05")
        self.assertEqual(eta.eta_text(["Beta", "Gamma"], now=eta._latest[2] + 10.0), "0:55")
        eta._publish("Gamma", 5.0)
        self.assertEqual(eta.eta_text(["Gamma"], now=eta._latest[2] + 10.0), "0:00")    # overdue is never negative

    def test_no_estimate_for_another_destination_an_empty_route_or_a_stale_estimate(self):
        eta._publish("Gamma", 65.0)
        self.assertEqual(eta.eta_text(["Beta", "Delta"]), "")
        self.assertEqual(eta.eta_text([]), "")
        self.assertEqual(eta.eta_text(["Gamma"], now=eta._latest[2] + eta.STALE_S + 1.0), "")

    def test_the_estimate_carries_across_a_warp_that_pops_the_route(self):
        eta._publish("Gamma", 65.0)
        self.assertNotEqual(eta.eta_text(["Beta", "Gamma"]), "")
        self.assertNotEqual(eta.eta_text(["Gamma"]), "")    # one hop later, same destination, no gap

    def test_the_real_banner_text_function_is_wrapped(self):
        self.assertTrue(getattr(render_mixin._format_autopilot_status, "_ap_eta_wrapped", False))
        self.assertEqual(render_mixin._format_autopilot_status([], "", "Helix Station"),
                         "Autopiloting to Helix Station")                     # in-system autopilots unchanged


class RouteContextTests(unittest.TestCase):
    def test_windows_that_are_not_on_a_solar_route_have_no_context(self):
        self.assertIsNone(eta.route_context(SimpleNamespace()))
        win = fake_window(FlightWindow)
        self.assertIsNotNone(eta.route_context(win))
        win._autopilot_route = []
        self.assertIsNone(eta.route_context(win))
        win = fake_window(FlightWindow, dungeon=True)
        self.assertIsNone(eta.route_context(win))
        win = fake_window(FlightWindow)
        win._autopilot_route_dungeon_gate_id = "up"
        self.assertIsNone(eta.route_context(win))
        win = fake_window(FlightWindow)
        del win._lock                                       # the windows vanilla tests build
        self.assertIsNone(eta.route_context(win))

    def test_the_key_changes_with_every_leg(self):
        win = fake_window(FlightWindow)
        first = eta.route_context(win)[0]
        win._autopilot_route = ["C"]
        self.assertNotEqual(eta.route_context(win)[0], first)
        win._autopilot_target = (5.0, 5.0)
        self.assertNotEqual(eta.route_context(win)[0], first)


class InstallTests(EtaFixture):
    def test_the_wrapper_returns_the_original_answer_and_passes_every_argument_through(self):
        cls = type("Win", (FlightWindow,), {})
        self.assertTrue(eta.install(cls))
        win = fake_window(cls, route=())                    # not on a route: nothing for ensure to do
        self.assertEqual(win._ap_fly(*FRAME), (True, False, False))
        self.assertEqual(win._ap_fly(0.0, -1.0, flight_plan.STOP, 225.0, 120.0, max_spd=500.0, accel=40.0, dt=0.01),
                         (True, False, False))
        self.assertEqual(win.calls[0], FRAME)
        self.assertEqual(win.calls[1], (0.0, -1.0, flight_plan.STOP, 225.0, 120.0, 500.0, 40.0, 0.01))

    def test_each_call_hands_the_ships_accel_and_top_speed_to_ensure(self):
        cls = type("Win", (FlightWindow,), {})
        eta.install(cls)
        win = fake_window(cls)
        with mock.patch.object(eta, "ensure") as ensure:
            win._ap_fly(*FRAME)
            win._ap_fly(0.0, -1.0, flight_plan.STOP, 225.0, 120.0, max_spd=500.0, accel=40.0, dt=0.01)
        self.assertEqual([c.args for c in ensure.call_args_list], [(win, 64.9, 677.0), (win, 40.0, 500.0)])

    def test_installing_twice_is_harmless_and_keeps_the_original(self):
        cls = type("Win", (FlightWindow,), {})
        self.assertTrue(eta.install(cls))
        self.assertFalse(eta.install(cls))
        self.assertTrue(hasattr(cls._ap_fly, "__wrapped__"))
        self.assertIs(cls._ap_fly.__wrapped__, FlightWindow._ap_fly)

    def test_a_game_without_ap_fly_is_left_alone_and_nothing_raises(self):
        with self.assertLogs(eta.logger, logging.WARNING) as logs:
            self.assertFalse(eta.install(type("Bare", (), {})))
        self.assertIn("no _ap_fly", logs.output[0])

    def test_an_ap_fly_that_no_longer_takes_accel_and_top_speed_is_left_alone(self):
        class Changed:
            def _ap_fly(self, tx, ty, mode, radius, aim, dt):
                return True, False, False

        with self.assertLogs(eta.logger, logging.WARNING) as logs:
            self.assertFalse(eta.install(Changed))
        self.assertIn("accel and max_spd", logs.output[0])
        self.assertNotIn("_ap_eta_wrapped", vars(Changed._ap_fly))

    def test_a_call_that_leaves_the_ship_numbers_to_their_defaults_still_returns_the_answer(self):
        class Defaults:
            def _ap_fly(self, tx, ty, mode, radius, aim, max_spd=677.0, accel=64.9, dt=0.01):
                return "answer"

        self.assertTrue(eta.install(Defaults))
        with mock.patch.object(eta, "ensure") as ensure:
            self.assertEqual(Defaults()._ap_fly(0.0, 0.0, "reach", 40.0, 0.0), "answer")
        ensure.assert_not_called()                           # no numbers to give: no estimate, no error

    def test_the_real_game_window_carries_the_wrapper(self):
        self.assertTrue(getattr(SolarSystemWindow._ap_fly, "_ap_eta_wrapped", False))
        self.assertTrue(hasattr(SolarSystemWindow._ap_fly, "__wrapped__"))


class WorkerTests(EtaFixture):
    def wait_for_text(self, route, timeout=30.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            text = eta.eta_text(route)
            if text:
                return text
            time.sleep(0.02)
        self.fail("no estimate arrived")

    def seconds(self, text):
        parts = [int(p) for p in text.split(":")]
        return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))

    def expected(self, win, legs, pilot=None):
        here = (win._local_x, win._local_y, win._local_vx, win._local_vy, win._local_angle)
        return route_sim.estimate_route_time(legs, here, SHIP, pilot=pilot)

    def hairpin_legs(self, win):
        return planner.build_legs(win._autopilot_target, win._autopilot_route, "A", win._visited_systems)

    def test_the_first_frame_of_a_leg_starts_an_estimate_that_matches_the_simulation(self):
        win = self.real_window()
        win._ap_fly(*FRAME)
        self.assertEqual(win._ap_eta_state.thread.name, "ap-eta")
        shown = self.seconds(self.wait_for_text(["B", "C"]))
        expected = self.expected(win, self.hairpin_legs(win))
        self.assertLess(abs(shown - expected), 3.0)
        self.assertGreater(shown, 20)

    def test_the_last_gate_gets_an_estimate_too(self):
        win = self.real_window(route=("B",))
        win._ap_fly(*STOP_FRAME)
        expected = self.expected(win, [planner.Leg(None, (0.0, -12000.0), True)])
        shown = self.seconds(self.wait_for_text(["B"]))
        self.assertLess(abs(shown - expected), 3.0)

    def test_a_new_leg_replaces_the_estimate_state_and_stops_the_old_worker(self):
        win = self.real_window()
        win._ap_fly(*FRAME)
        old = win._ap_eta_state
        win._autopilot_route = ["C"]
        win._autopilot_target = (0.0, 12000.0)
        win._ap_fly(0.0, 12000.0, flight_plan.STOP, 225.0, 120.0, 677.0, 64.9, 1 / 30)
        self.assertIsNot(win._ap_eta_state, old)
        self.assertTrue(old.stop.is_set())
        self.assertEqual(win._ap_eta_trip["destination"], "C")      # still the same trip
        self.assertEqual(win._ap_eta_trip["jumps"], 2)

    def test_leaving_the_route_clears_the_state(self):
        win = self.real_window()
        win._ap_fly(*FRAME)
        state = win._ap_eta_state
        win._autopilot_route = []
        win._ap_fly(*FRAME)
        self.assertIsNone(win._ap_eta_state)
        self.assertTrue(state.stop.is_set())

    def test_no_estimate_when_a_later_system_is_unexplored(self):
        win = self.real_window(route=("B", "Z", "D"))                # Z has not been explored: its gates are unknown
        win._ap_fly(*FRAME)
        self.assertIsNone(win._ap_eta_state.thread)
        self.assertEqual(eta.eta_text(["B", "Z", "D"]), "")

    def test_an_unexplored_destination_does_not_matter(self):
        """Only the systems the ship flies through need their gates; the destination is not flown."""
        win = self.real_window(route=("B", "C", "D"))                # D is the destination, never entered
        win._ap_fly(*FRAME)
        self.assertIsNotNone(win._ap_eta_state.thread)

    def test_the_live_pilots_maneuver_is_the_one_the_estimate_flies(self):
        win = self.real_window()
        win._local_vx, win._local_vy, win._local_angle = 677.0, 0.0, 90.0    # cruising east, the gate is north: the course is off
        win._ap_pilot = flight_plan.Pilot(flight_plan.HOLD_SPEED)           # this leg started out holding its speed
        win._ap_fly(*FRAME)
        legs = self.hairpin_legs(win)
        shown = self.seconds(self.wait_for_text(["B", "C"]))
        holding = self.expected(win, legs, pilot=flight_plan.Pilot(flight_plan.HOLD_SPEED))
        default = self.expected(win, legs)
        self.assertGreater(holding, default + 5.0)                   # the test is only meaningful if they differ
        self.assertLess(abs(shown - holding), 3.0)
        self.assertEqual(win._ap_pilot.maneuver, flight_plan.HOLD_SPEED)    # the live pilot itself was not moved

    def test_each_legs_gate_core_comes_from_the_game_and_from_the_explored_gates(self):
        win = self.real_window(route=("B", "C", "D"))
        win._ap_flythrough_core_radius = lambda: 123.0
        win._visited_systems["B"]["warp_gates"][1]["radius"] = 60.0       # B's gate to C
        win._visited_systems["C"]["warp_gates"][1]["radius"] = 3.0        # C's gate to D: below the game's minimum
        win._ap_fly(*FRAME)
        cores = [leg.core for leg in win._ap_eta_state.legs]
        self.assertEqual(cores, [123.0, 60.0, FlightWindow._AP_GATE_CORE_MIN])

    def test_a_missing_core_falls_back_to_forty(self):
        win = self.real_window()
        win._ap_fly(*FRAME)
        self.assertEqual([leg.core for leg in win._ap_eta_state.legs], [40.0, 40.0])

    def test_arriving_at_the_last_gate_is_logged_with_the_first_estimate(self):
        win = self.real_window(route=("B",))
        with self.assertLogs(eta.logger, "INFO") as logs:
            win._ap_fly(*STOP_FRAME)
            self.wait_for_text(["B"])
            win._autopilot = False                                   # the last warp fired
            win._ap_eta_state.thread.join(timeout=10)
        text = "\n".join(logs.output)
        self.assertIn("route to B, 1 jump: estimated", text)
        self.assertIn("arrived at B after", text)

    def test_a_cancelled_route_is_not_logged_as_an_arrival(self):
        win = self.real_window(route=("B",))
        with self.assertLogs(eta.logger, "INFO") as logs:
            win._ap_fly(*STOP_FRAME)
            self.wait_for_text(["B"])
            win._autopilot_route = []                                # the pilot cancelled
            win._ap_fly(*STOP_FRAME)
            win._ap_eta_state = None
            win._autopilot = False
            time.sleep(0.6)
        self.assertNotIn("arrived", "\n".join(logs.output))

    def test_a_simulation_that_fails_turns_the_estimate_off_quietly_and_the_autopilot_goes_on(self):
        win = self.real_window()
        with mock.patch.object(route_sim, "estimate_route_time", side_effect=RuntimeError("the steering changed")):
            with self.assertLogs(eta.logger, "ERROR"):
                win._ap_fly(*FRAME)
                end = time.monotonic() + 30.0
                while time.monotonic() < end and not eta.has_failed():
                    time.sleep(0.02)
        self.assertTrue(eta.has_failed())
        self.assertEqual(eta.eta_text(["B", "C"]), "")
        self.assertEqual(win._ap_fly(*FRAME), (True, False, False))        # the autopilot is not affected

    def test_a_window_that_cannot_start_an_estimate_is_logged_once_and_the_autopilot_goes_on(self):
        win = self.real_window()
        del win._turning_limiter                                     # ship_stats reads it
        with self.assertLogs(eta.logger, "ERROR"):
            self.assertEqual(win._ap_fly(*FRAME), (True, False, False))
        self.assertTrue(eta.has_failed())
        self.assertEqual(win._ap_fly(*FRAME), (True, False, False))        # off now: no second log, no error


class RealWindowTests(EtaFixture):
    """The real SolarSystemWindow, built bare: its real ``_ap_fly`` (the dev's flight_plan steering) carries our
    wrapper, and the estimate it starts follows what the autopilot then does."""

    def window(self, route=("B", "C")):
        win = SolarSystemWindow.__new__(SolarSystemWindow)
        win._lock = threading.RLock()
        win._local_x, win._local_y, win._local_vx, win._local_vy, win._local_angle = 0.0, 0.0, 0.0, -677.0, 0.0
        win._autopilot = True
        win._autopilot_route = list(route)
        win._autopilot_target = (0.0, -12000.0)
        win._autopilot_route_dungeon_gate_id = None
        win._autopilot_route_gate_id = 7
        win._layout = {"system_name": "A", "warp_gates": [{"id": 7, "x": 0.0, "y": -12000.0, "radius": 90.0}]}
        win._visited_systems = visited_fixture()
        win._TURN_RATE = 20.1
        win._thrust_limiter = 100.0
        win._turning_limiter = 100.0
        win._mission_nav_pending = None
        win._ap_pilot = None
        self.windows.append(win)
        return win

    def test_the_real_ap_fly_flies_as_before_and_starts_the_estimate(self):
        win = self.window()
        thrust, brake, done = win._ap_fly(0.0, -12000.0, flight_plan.REACH, 90.0, 0.0, 677.0, 64.9, 1 / 60)
        self.assertEqual((thrust, brake, done), (True, False, False))     # cruising straight at the gate: burn lit
        self.assertEqual(win._ap_eta_state.thread.name, "ap-eta")
        self.assertEqual(win._ap_eta_state.legs[0].core, 90.0)            # the game's own _ap_flythrough_core_radius
        legs = planner.build_legs(win._autopilot_target, win._autopilot_route, "A", win._visited_systems,
                                  current_core=90.0)
        here = (0.0, 0.0, 0.0, -677.0, 0.0)
        pilot = win._ap_pilot                                              # chosen by the real _ap_fly on this frame
        self.assertIsInstance(pilot, flight_plan.Pilot)
        stats = planner.ShipStats(64.9, win._TURN_RATE, 677.0)
        expected = route_sim.estimate_route_time(legs, here, stats, pilot=pilot)
        shown = WorkerTests.seconds(self, self.wait_text(["B", "C"]))
        self.assertLess(abs(shown - expected), 3.0)

    def wait_text(self, route, timeout=30.0):
        return WorkerTests.wait_for_text(self, route, timeout)

    def test_the_estimate_does_not_change_what_the_real_autopilot_flies(self):
        """The same frames with and without the wrapper give the same commands and the same pilot."""
        plain = self.window()
        wrapped = self.window()
        original = SolarSystemWindow._ap_fly.__wrapped__
        for step in range(200):
            args = (0.0, -12000.0, flight_plan.REACH, 90.0, 0.0, 677.0, 64.9, 1 / 60)
            a = original(plain, *args)
            b = wrapped._ap_fly(*args)
            self.assertEqual(a, b)
            for win in (plain, wrapped):                                   # advance both ships identically
                win._local_vx, win._local_vy = flight_plan.advance_velocity(
                    win._local_vx, win._local_vy, win._local_angle, flight_plan.Ship(64.9, win._TURN_RATE, 677.0),
                    a[0], a[1], 1 / 60)
                win._local_x += win._local_vx / 60
                win._local_y += win._local_vy / 60
        self.assertEqual((plain._local_x, plain._local_y, plain._local_angle),
                         (wrapped._local_x, wrapped._local_y, wrapped._local_angle))
        self.assertEqual(plain._ap_pilot, wrapped._ap_pilot)


class SimulationMatchesTheRealAutopilotTests(EtaFixture):
    """The estimate's whole point: a leg flown frame by frame by the real ``SolarSystemWindow._ap_fly`` (at the
    game's frame rate, the shared physics step applied by hand) takes what the 30 Hz simulation says."""

    def fly_real(self, state, gate, mode, radius, aim, hz):
        win = RealWindowTests.window(self)
        win._local_x, win._local_y, win._local_vx, win._local_vy, win._local_angle = state
        original = SolarSystemWindow._ap_fly.__wrapped__
        ship = flight_plan.Ship(64.9, win._TURN_RATE, 677.0)
        leg = flight_plan.Leg(gate[0], gate[1], mode, radius=radius, aim=aim)
        dt = 1.0 / hz
        elapsed = 0.0
        while elapsed < 400.0:
            thrust, brake, done = original(win, gate[0], gate[1], mode, radius, aim, 677.0, 64.9, dt)
            if done:
                return elapsed
            # _ap_fly already applied the brake to the velocity itself (the shared physics skips it on autopilot frames)
            win._local_vx, win._local_vy = flight_plan.advance_velocity(
                win._local_vx, win._local_vy, win._local_angle, ship, thrust, False, dt)
            before = (win._local_x, win._local_y)
            win._local_x += win._local_vx * dt
            win._local_y += win._local_vy * dt
            elapsed += dt
            if mode == flight_plan.REACH and route_sim._warp_due(win._local_x, win._local_y, win._local_vx,
                                                                win._local_vy, leg, before):
                return elapsed
        self.fail("the real autopilot never finished the leg")

    def test_legs_flown_by_the_real_ap_fly_take_what_the_simulation_predicts(self):
        cases = {
            "cruising at the gate": ((0.0, 0.0, 0.0, -677.0, 0.0), (0.0, -12000.0), flight_plan.REACH, 40.0, 0.0),
            "from rest": ((0.0, 0.0, 0.0, 0.0, 0.0), (0.0, -12000.0), flight_plan.REACH, 40.0, 0.0),
            "cruising across the course": ((0.0, 0.0, 677.0, 0.0, 90.0), (0.0, -12000.0), flight_plan.REACH, 40.0, 0.0),
            "cruising away from the gate": ((0.0, 0.0, 0.0, 677.0, 180.0), (0.0, -12000.0), flight_plan.REACH, 40.0, 0.0),
            "the last gate from cruise": ((0.0, 0.0, 0.0, -677.0, 0.0), (0.0, -12000.0), flight_plan.STOP, 225.0, 120.0),
            "the last gate, cruising away": ((0.0, 0.0, 0.0, 677.0, 180.0), (0.0, -6000.0), flight_plan.STOP, 225.0, 120.0),
        }
        ship = flight_plan.Ship(64.9, 20.1, 677.0)
        for name, (state, gate, mode, radius, aim) in cases.items():
            for hz in (60, 144):
                with self.subTest(case=name, hz=hz):
                    leg = flight_plan.Leg(gate[0], gate[1], mode, radius=radius, aim=aim)
                    pilot = flight_plan.Pilot(flight_plan.plan(*state, ship, leg).maneuver)
                    predicted = route_sim.fly_leg(ship, state, leg, pilot)[0]
                    flown = self.fly_real(state, gate, mode, radius, aim, hz)
                    self.assertLess(abs(predicted - flown) / flown, 0.01)


class HookSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = modloader.game_source("Client.py")

    def test_both_hooks_are_installed_after_the_window_class_and_before_the_entry_point(self):
        window_class = self.source.index("\nclass SolarSystemWindow")
        entry = self.source.index('if __name__ == "__main__":')
        for line in ("autopilot_eta.install(SolarSystemWindow)", "autopilot_eta.install_banner(_render_mixin_module)"):
            with self.subTest(line=line):
                install = self.source.index(line)
                self.assertLess(window_class, install)
                self.assertLess(install, entry)

    def test_the_hud_still_gets_its_banner_text_from_the_function_we_wrap(self):
        render_source = modloader.game_source("render_mixin.py")
        self.assertIn("from autopilot_status import format_autopilot_status as _format_autopilot_status",
                      render_source)
        self.assertEqual(render_source.count("_format_autopilot_status("), 1)

    def test_the_game_flies_every_route_leg_through_the_one_ap_fly_we_wrap(self):
        # The estimate is started from _ap_fly. If the dev flew a leg some other way, that leg would have no ETA.
        self.assertEqual(self.source.count("self._ap_fly("), 3)
        self.assertEqual(self.source.count("def _ap_fly("), 1)
        for name in ("_ap_warp_approach_controls", "_ap_final_warp_controls"):
            self.assertNotIn(f"self.{name}(", self.source)

    def test_the_game_still_warps_on_the_gate_core_the_estimate_ends_a_leg_on(self):
        for needle in ("def _ap_flythrough_core_radius", "def _ap_flythrough_warp_due", "_AP_GATE_CORE_MIN = ",
                       "_WARP_ACTIVATION_RADIUS * 0.5", "_flight_plan.STOP,", "_flight_plan.REACH,"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.source)


if __name__ == "__main__":
    unittest.main()

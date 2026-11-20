"""construction_eta.py: an estimated time to complete for each Construction Bay project.

The model is pinned on numbers read off a real recorded session (queue order decides who gets the scarce
resource, progress per update follows that resource's supply, a pause must not dilute a speed), the text
and the hooks are pinned separately, and the drawing is checked against the game's REAL Construction Bay
rendering: the estimate must land in the free space left of each progress bar and nowhere else."""
import logging
import os
import threading
import time
import unittest
from collections import OrderedDict
from types import SimpleNamespace
from unittest import mock

import pygame

import construction_eta as ce

TICK = 10.0


def costs(**amounts):
    return [{"resource": r, "amount": a, "timing": "periodic"} for r, a in amounts.items()]


def row(pid, order=1, done=0.0, total=1_000_000.0, per_unit=1_000_000.0, resource=None, paused=False,
        name="Project", **extra):
    resource = {"metal": 1_000_000.0} if resource is None else resource      # 1.0 metal per man-hour
    return {"project_id": pid, "name": name, "queue_order": order, "manhours_done": done,
            "manhours_total": total, "manhours": per_unit, "paused": paused, "is_paused": int(paused),
            "progress": done / total if total else 0.0, "resource_costs": costs(**resource),
            "station_id": 1, **extra}


class Run:
    """Drives one StationState with a queue whose projects advance by given man-hours per update."""

    def __init__(self, *specs, stock=None):
        self.state = ce.StationState()
        self.specs = {s["project_id"]: dict(s) for s in specs}
        self.t = 1000.0
        self.stock = stock
        self.last = None

    def step(self, moves=None, dt=TICK, paused=None, drop=(), cargo=None):
        self.t += dt
        if cargo is not None:                  # the cargo stream: one message a second over this gap
            for second in range(1, int(dt) + 1):
                moment = self.t - dt + second
                self.state.flow.update(dict(cargo(moment) if callable(cargo) else cargo), moment)
        for pid, moved in (moves or {}).items():
            self.specs[pid]["manhours_done"] += moved
        for pid, flag in (paused or {}).items():
            self.specs[pid]["paused"] = self.specs[pid]["is_paused"] = bool(flag)
        for pid in drop:
            self.specs.pop(pid)
        queue = []
        for spec in self.specs.values():
            spec["progress"] = spec["manhours_done"] / spec["manhours_total"] if spec["manhours_total"] else 0.0
            queue.append(dict(spec))
        self.last = self.state.update(queue, self.t, self.stock)
        return self.last

    def run(self, updates, moves, **kwargs):
        for _ in range(updates):
            self.step(moves, **kwargs)
        return self.last


class RatioTests(unittest.TestCase):
    def test_periodic_costs_per_manhour_and_up_front_costs_ignored(self):
        data = row(1, per_unit=10_000, resource={"metal": 20_000})
        data["resource_costs"].append({"resource": "silicon", "amount": 999, "timing": "initial"})
        data["resource_costs"].append({"resource": "oil", "amount": 500, "timing": "periodic"})
        self.assertEqual(ce.project_ratios(data), {"metal": 2.0, "oil": 0.05})

    def test_the_real_oakheart_blueprint(self):
        data = {"manhours": 75_000_000, "resource_costs": costs(metal=100_000_000, microchips=2_000_000,
                                                                   titanium=500, oil=500)}
        ratios = ce.project_ratios(data)
        self.assertAlmostEqual(ratios["metal"], 4 / 3)
        self.assertAlmostEqual(ratios["microchips"], 2_000_000 / 75_000_000)

    def test_no_manhours_means_no_ratios(self):
        self.assertEqual(ce.project_ratios(row(1, per_unit=0)), {})
        self.assertEqual(ce.project_ratios({"manhours": "x"}), {})

    def test_an_older_server_with_only_cost_fields(self):
        data = {"manhours": 10_000, "cost_metal": 20_000, "cost_credits": 50_000, "cost_item_amount": 3,
                "cost_oil": 0, "cost_silicon": "bad"}
        self.assertEqual(ce.project_ratios(data), {"metal": 2.0})

    def test_stock_is_the_cargo_size_of_resource_rows_without_workers(self):
        cargo = [{"item_category": "resource", "item_key": "metal", "cargo_size": 210_267},
                 {"item_category": "resource", "item_key": "workers", "cargo_size": 73_820, "quantity": 13},
                 {"item_category": "station", "item_key": "item:station:vacuskin", "quantity": 1},
                 {"item_category": "resource", "item_key": "oil", "quantity": 30}]
        self.assertEqual(ce.stock_from_cargo(cargo), {"metal": 210_267.0, "oil": 30.0})
        self.assertIsNone(ce.stock_from_cargo(None))
        self.assertIsNone(ce.stock_from_cargo("x"))


class LeaderTests(unittest.TestCase):
    def test_estimating_until_there_is_enough_history_and_then_work_left_over_speed(self):
        run = Run(row(1, total=1_000_000))
        run.step()
        for _ in range(ce.MIN_INTERVALS - 1):
            self.assertEqual(run.step({1: 5_000})[1].kind, "estimating")
        eta = run.step({1: 5_000})[1]
        self.assertEqual(eta.kind, "eta")
        remaining = 1_000_000 - 5_000 * ce.MIN_INTERVALS
        self.assertAlmostEqual(eta.seconds, remaining / 500.0)            # 5,000 man-hours per 10 s
        self.assertEqual(eta.ahead, 0)
        self.assertFalse(eta.parallel)

    def test_an_estimate_on_little_history_is_marked_rough_until_about_three_minutes(self):
        run = Run(row(1, total=10_000_000))
        run.step()
        run.run(ce.MIN_INTERVALS, {1: 5_000})
        self.assertTrue(run.last[1].rough)
        run.run(ce.ROUGH_BELOW - ce.MIN_INTERVALS - 1, {1: 5_000})
        self.assertTrue(run.last[1].rough)
        run.step({1: 5_000})
        self.assertFalse(run.last[1].rough)

    def test_a_pause_does_not_dilute_the_speed(self):
        run = Run(row(1, total=10_000_000))
        run.step()
        run.run(12, {1: 5_000})
        before = run.last[1].seconds
        run.run(5, None, paused={1: True})
        self.assertEqual(run.last[1].kind, "paused")
        run.step(None, paused={1: False})                       # the update that resumes it: not an interval
        eta = run.run(10, {1: 5_000})[1]
        self.assertEqual(eta.kind, "eta")
        remaining = 10_000_000 - 5_000 * 22
        self.assertAlmostEqual(eta.seconds, remaining / 500.0)  # still 500 man-hours/s, the pause left out
        self.assertLess(before, 10_000_000 / 500.0)

    def test_a_first_project_that_makes_no_progress_is_waiting_for_resources(self):
        run = Run(row(1))
        run.step()
        self.assertEqual(run.run(ce.MIN_INTERVALS, {1: 0})[1].kind, "starved")

    def test_a_burst_then_nothing_averages_out_like_the_real_session(self):
        moves = [13_628, 13_629, 9_823, 5_807, 0, 157, 12_162, 151, 7_056, 94, 1_758, 0]
        run = Run(row(1, total=75_000_000, done=74_433_316, per_unit=75_000_000,
                      resource={"metal": 100_000_000}))
        run.step()
        for moved in moves:
            run.step({1: moved})
        rate = sum(moves) / (TICK * len(moves))
        self.assertAlmostEqual(run.last[1].seconds, (75_000_000 - run.specs[1]["manhours_done"]) / rate)

    def test_only_the_last_window_counts(self):
        run = Run(row(1, total=100_000_000))
        run.step()
        run.run(ce.WINDOW_INTERVALS, {1: 1_000})
        run.run(ce.WINDOW_INTERVALS, {1: 5_000})
        remaining = 100_000_000 - run.specs[1]["manhours_done"]
        self.assertAlmostEqual(run.last[1].seconds, remaining / 500.0)

    def test_a_finished_project_is_done(self):
        run = Run(row(1, total=10_000, done=9_999))
        run.step({1: 1})
        self.assertEqual(run.last[1].kind, "done")

    def test_a_gap_in_the_updates_is_not_an_interval(self):
        run = Run(row(1))
        run.step()
        run.step({1: 5_000}, dt=ce.MAX_INTERVAL_S + 1)
        self.assertEqual(run.state.own_count(1, "leader"), 0)
        run.step({1: 5_000}, dt=ce.MAX_INTERVAL_S)
        self.assertEqual(run.state.own_count(1, "leader"), 1)

    def test_progress_that_goes_backwards_and_odd_rows_never_raise(self):
        run = Run(row(1))
        run.step()
        run.step({1: -500})
        self.assertEqual(run.state.own_count(1, "leader"), 0)
        results = ce.StationState().update([None, 5, {"project_id": None}, {"project_id": 9}], 1.0)
        self.assertEqual(set(results), {9})
        self.assertEqual(ce.StationState().update("not a list", 1.0), {})

    def test_a_paused_project_is_paused_from_the_first_update(self):
        run = Run(row(1, paused=True))
        self.assertEqual(run.step()[1].kind, "paused")


class FollowerTests(unittest.TestCase):
    """The real queue: Oakheart (4/3 metal per man-hour) ahead of expanders at 2.0 metal per man-hour."""

    def oakheart(self, **kw):
        return row(1, order=1, total=75_000_000, done=kw.pop("done", 0), per_unit=75_000_000,
                   resource={"metal": 100_000_000}, **kw)

    def gamma(self, pid, order, total, **kw):
        return row(pid, order=order, total=total, per_unit=10_000, resource={"metal": 20_000}, **kw)

    def test_a_follower_waits_for_everything_ahead_then_runs_at_the_speed_the_metal_allows(self):
        run = Run(self.oakheart(), self.gamma(2, 2, 200_000))
        run.step()
        run.run(ce.WINDOW_INTERVALS, {1: 6_000})                # 600 man-hours/s = 800 metal/s
        leader, follower = run.last[1], run.last[2]
        leader_seconds = (75_000_000 - run.specs[1]["manhours_done"]) / 600.0
        self.assertAlmostEqual(leader.seconds, leader_seconds)
        own_seconds = 200_000 / (800.0 / 2.0)                   # 400 man-hours/s of 2.0 metal each
        self.assertEqual(follower.kind, "eta")
        self.assertAlmostEqual(follower.seconds, leader_seconds + own_seconds)
        self.assertEqual(follower.ahead, 1)
        self.assertFalse(follower.parallel)

    def test_projects_further_back_add_up(self):
        run = Run(self.oakheart(), self.gamma(2, 2, 100_000), self.gamma(3, 3, 200_000))
        run.step()
        run.run(ce.WINDOW_INTERVALS, {1: 6_000})
        second, third = run.last[2], run.last[3]
        self.assertAlmostEqual(third.seconds - second.seconds, 200_000 / 400.0)
        self.assertEqual((second.ahead, third.ahead), (1, 2))

    def test_a_paused_project_is_skipped_and_not_counted_as_ahead(self):
        run = Run(self.oakheart(), self.gamma(2, 2, 100_000, paused=True), self.gamma(3, 3, 200_000))
        run.step()
        run.run(ce.WINDOW_INTERVALS, {1: 6_000})
        self.assertEqual(run.last[2].kind, "paused")
        self.assertEqual(run.last[3].ahead, 1)
        self.assertAlmostEqual(run.last[3].seconds - run.last[1].seconds, 200_000 / 400.0)

    def test_queue_order_not_list_order_decides_who_leads(self):
        run = Run(self.gamma(2, 2, 200_000), self.oakheart())     # listed second, queue_order 1
        run.step()
        run.run(ce.MIN_INTERVALS, {1: 6_000})
        self.assertEqual(run.last[1].ahead, 0)
        self.assertEqual(run.last[2].ahead, 1)

    def test_when_the_leader_pauses_the_next_project_takes_over_at_once_from_the_stations_supply(self):
        run = Run(self.oakheart(), self.gamma(2, 2, 200_000))
        run.step()
        run.run(ce.WINDOW_INTERVALS, {1: 6_000})
        run.step(None, paused={1: True})
        taken = run.last[2]
        self.assertEqual(run.last[1].kind, "paused")
        self.assertEqual((taken.kind, taken.ahead), ("eta", 0))
        self.assertAlmostEqual(taken.seconds, 200_000 / 400.0)    # 800 metal/s of the old leader / 2.0

    def test_an_update_where_a_project_pauses_or_the_queue_changes_adds_nothing_to_the_supply(self):
        run = Run(self.oakheart(), self.gamma(2, 2, 200_000))
        run.step()
        run.run(ce.MIN_INTERVALS, {1: 6_000})
        count = len(run.state.supply)
        run.step(None, paused={1: True})                          # the leader pauses: what it used in that gap is unknown
        self.assertEqual(len(run.state.supply), count)
        run.step({2: 3_000}, paused={1: False})                    # and resumes
        self.assertEqual(len(run.state.supply), count)
        run.specs[3] = self.gamma(3, 3, 50_000)                    # a project is added
        run.step({1: 6_000})
        self.assertEqual(len(run.state.supply), count)
        run.step({1: 6_000})                                       # a plain update counts again
        self.assertEqual(len(run.state.supply), count + 1)

    def test_a_stretch_with_everything_paused_does_not_dilute_the_stations_supply(self):
        run = Run(self.oakheart(), self.gamma(2, 2, 200_000))
        run.step()
        run.run(ce.WINDOW_INTERVALS, {1: 6_000})
        run.run(10, None, paused={1: True, 2: True})               # nothing runs for 100 s
        run.step(None, paused={1: False, 2: False})
        run.run(ce.MIN_INTERVALS, {1: 6_000})
        gap = run.last[2].seconds - run.last[1].seconds
        self.assertAlmostEqual(gap, 200_000 / 400.0)               # still 800 metal/s over 2.0 per man-hour

    def test_a_project_that_vanishes_from_the_queue_in_an_update_adds_nothing_to_the_supply(self):
        run = Run(self.oakheart(), self.gamma(2, 2, 200_000), self.gamma(3, 3, 200_000))
        run.step()
        run.run(ce.MIN_INTERVALS, {1: 6_000})
        count = len(run.state.supply)
        run.step({1: 6_000}, drop=[3])                              # it finished or was cancelled in that gap
        self.assertEqual(len(run.state.supply), count)

    def test_the_stations_supply_is_averaged_over_the_long_window_and_forgets_what_is_older(self):
        with mock.patch.object(ce, "FLOW_WINDOW_S", 600.0):
            run = Run(self.oakheart(), self.gamma(2, 2, 200_000))
            run.step()
            run.run(60, {1: 3_000})                                 # 400 metal/s, a full window of it
            run.run(30, {1: 6_000})                                 # then 800 metal/s for half a window
            half_and_half = run.last[2].seconds - run.last[1].seconds
            run.run(60, {1: 6_000})                                 # and a whole window of 800
        self.assertAlmostEqual(run.last[2].seconds - run.last[1].seconds, 200_000 / 400.0)
        self.assertGreater(half_and_half, 200_000 / 400.0)           # still remembers some of the slow time

    def test_a_follower_is_estimating_not_queued_while_the_supply_history_is_too_short(self):
        run = Run(self.oakheart(), self.gamma(2, 2, 200_000))
        run.step()
        for index in range(ce.MIN_INTERVALS + 2):                   # the follower flips every update: no steady interval
            run.step({1: 6_000}, paused={2: index % 2 == 0})
        run.step({1: 6_000}, paused={2: False})
        self.assertEqual(run.state.own_count(1, "leader"), ce.MIN_INTERVALS + 3)
        self.assertLess(len(run.state.supply), ce.MIN_INTERVALS)
        self.assertEqual(run.last[1].kind, "eta")
        self.assertEqual(run.last[2].kind, "estimating")

    def test_after_the_leader_has_run_a_while_its_own_speed_replaces_the_projection(self):
        run = Run(self.oakheart(), self.gamma(2, 2, 200_000))
        run.step()
        run.run(ce.WINDOW_INTERVALS, {1: 6_000})
        run.step(None, paused={1: True})
        run.run(ce.MIN_INTERVALS + 1, {2: 3_000})                # 300 man-hours/s: slower than projected
        eta = run.last[2]
        self.assertAlmostEqual(eta.seconds, (200_000 - run.specs[2]["manhours_done"]) / 300.0)

    def test_a_follower_seen_progressing_on_its_own_is_timed_on_its_own_speed(self):
        run = Run(self.oakheart(), self.gamma(2, 2, 200_000))
        run.step()
        run.run(ce.WINDOW_INTERVALS, {1: 6_000, 2: 1_000})        # disjoint resources would look like this
        follower = run.last[2]
        self.assertTrue(follower.parallel)
        self.assertAlmostEqual(follower.seconds, (200_000 - run.specs[2]["manhours_done"]) / 100.0)
        self.assertEqual(follower.ahead, 1)

    def test_the_scarce_resource_is_found_from_the_stations_stock(self):
        leader = row(1, order=1, total=10_000_000, per_unit=10_000_000, resource={"alpha": 20_000_000, "beta": 5_000_000})
        follower = row(2, order=2, total=100_000, per_unit=100_000, resource={"alpha": 100_000, "beta": 300_000})
        scarce_alpha = {"alpha": 0.0, "beta": 1e9}
        scarce_beta = {"alpha": 1e9, "beta": 0.0}
        speeds = {}
        for label, stock in (("alpha", scarce_alpha), ("beta", scarce_beta), ("unknown", None)):
            run = Run(leader, follower, stock=stock)
            run.step()
            run.run(ce.MIN_INTERVALS + 2, {1: 5_000})              # 500 man-hours/s: alpha 1,000/s, beta 250/s
            speeds[label] = 100_000 / (run.last[2].seconds - run.last[1].seconds)
        self.assertAlmostEqual(speeds["alpha"], 1_000.0 / 1.0)     # alpha supply over alpha per man-hour
        self.assertAlmostEqual(speeds["beta"], 250.0 / 3.0)
        self.assertAlmostEqual(speeds["unknown"], 250.0 / 3.0)     # no stock known: the follower's heaviest cost

    def test_a_follower_that_needs_nothing_anyone_consumes_is_unknown_until_the_cargo_has_been_watched(self):
        run = Run(self.oakheart(), row(2, order=2, total=1000, per_unit=1000, resource={"silicon": 5000}))
        run.step()
        run.run(ce.MIN_INTERVALS + 1, {1: 6_000})
        self.assertEqual(run.last[2].kind, "estimating")           # "nothing arrives" needs both views to agree

    def test_a_follower_that_needs_nothing_anyone_consumes_and_nothing_arrives_is_blocked_by_name(self):
        run = Run(self.oakheart(), row(2, order=2, total=1000, per_unit=1000, resource={"silicon": 5000}))
        run.step()
        run.run(15, {1: 6_000}, cargo={"metal": 0.0, "silicon": 0.0})
        self.assertEqual((run.last[2].kind, run.last[2].resource), ("blocked", "Silicon"))

    def test_a_follower_whose_resources_are_all_in_stock_is_queued_because_nothing_sets_its_speed(self):
        run = Run(self.oakheart(), row(2, order=2, total=1000, per_unit=1000, resource={"silicon": 5000}))
        run.step()
        run.run(15, {1: 6_000}, cargo={"metal": 0.0, "silicon": 1e9})
        self.assertEqual(run.last[2].kind, "queued")

    def test_before_there_is_any_supply_history_followers_are_estimating(self):
        run = Run(self.oakheart(), self.gamma(2, 2, 200_000))
        run.step()
        run.run(2, {1: 6_000})
        self.assertEqual(run.last[1].kind, "estimating")
        self.assertEqual(run.last[2].kind, "estimating")

    def test_a_starved_leader_is_skipped_so_the_next_project_leads_and_knows_nothing_yet(self):
        run = Run(self.oakheart(), self.gamma(2, 2, 200_000))
        run.step()
        run.run(ce.MIN_INTERVALS + 1, {1: 0})
        self.assertEqual(run.last[1].kind, "starved")
        self.assertEqual(run.last[2].kind, "estimating")           # nothing consumed or arrived to go on


class FinishLoggingTests(unittest.TestCase):
    def test_a_finished_project_logs_its_first_estimate_and_the_real_time(self):
        run = Run(row(1, total=100_000))
        run.step()
        run.run(ce.MIN_INTERVALS, {1: 5_000})                    # first estimate here: 70,000 left at 500/s = 140 s
        first = run.last[1].seconds
        run.run(13, {1: 5_000})                                  # 5,000 x 19 = 95,000 done
        run.specs[1]["manhours_done"] = 100_000
        run.step(None)                                           # at 100% it is still listed...
        with self.assertLogs(ce.logger, "INFO") as logs:
            run.step(None, drop=[1])                             # ...then it leaves the queue
        line = logs.output[0]
        self.assertIn("finished", line)
        self.assertIn(f"first estimate ~{first:.0f}s", line)
        self.assertIn("project 1", line)
        self.assertIn("with 0 ahead", line)

    def test_a_project_that_leaves_early_is_logged_as_cancelled(self):
        run = Run(row(1, total=1_000_000))
        run.step()
        run.run(ce.MIN_INTERVALS, {1: 5_000})
        with self.assertLogs(ce.logger, "INFO") as logs:
            run.step(None, drop=[1])
        self.assertIn("cancelled?", logs.output[0])

    def test_nothing_is_logged_for_a_project_that_never_had_an_estimate(self):
        run = Run(row(1))
        run.step()
        run.step({1: 10})
        with self.assertNoLogs(ce.logger, "INFO"):
            run.step(None, drop=[1])


class TextTests(unittest.TestCase):
    def eta(self, seconds, now=100.0, **kw):
        return ce.Eta("eta", seconds, made_at=now, **kw)

    def test_durations(self):
        for seconds, text in ((3, "<15s"), (37, "35s"), (75, "1m 20s"), (119, "2m"), (840, "14m"),
                              (5430, "1h 30m"), (7200, "2h 00m"), (100_000, "27h 47m"), (400_000, "4d 15h")):
            self.assertEqual(ce.format_duration(seconds), text, seconds)

    def test_a_running_estimate_counts_down_from_when_it_was_made(self):
        eta = self.eta(840.0, now=100.0)
        self.assertEqual(ce.eta_text(eta, 100.0), "~14m")
        self.assertEqual(ce.eta_text(eta, 150.0), "~13m")                     # 790 s left
        short = self.eta(20.0, now=100.0)
        self.assertEqual(ce.eta_text(short, 100.0), "~20s")
        self.assertEqual(ce.eta_text(short, 110.0), "~<15s")                  # 10 s left
        self.assertEqual(ce.eta_text(short, 130.0), "~<15s")                  # overdue: still shown, never negative

    def test_queued_behind_others_and_rough_ones_say_so(self):
        self.assertEqual(ce.eta_text(self.eta(840, ahead=2), 100.0), "~14m (after 2)")
        self.assertEqual(ce.eta_text(self.eta(840, rough=True), 100.0), "~14m?")
        self.assertEqual(ce.eta_text(self.eta(840, ahead=1, parallel=True), 100.0), "~14m")

    def test_the_other_states(self):
        for kind, text in (("estimating", "estimating..."), ("starved", "waiting for resources"),
                           ("queued", "queued"), ("paused", "paused"), ("done", "")):
            self.assertEqual(ce.eta_text(ce.Eta(kind, made_at=1.0), 2.0), text)
        self.assertEqual(ce.eta_text(None, 1.0), "")
        self.assertEqual(ce.eta_text(self.eta(840), 100.0, paused=True), "paused")

    def test_an_estimate_from_updates_that_stopped_is_not_shown(self):
        self.assertEqual(ce.eta_text(self.eta(840, now=100.0), 100.0 + ce.STALE_S + 1), "")


class _Fixture(unittest.TestCase):
    def setUp(self):
        ce.reset_for_tests()
        self.addCleanup(ce.reset_for_tests)
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(ce.ENV_OFF, None)


class HookTests(_Fixture):
    def make_class(self):
        calls = []

        class Window:
            def set_ps_construction_bay(self, data, extra=None):
                calls.append(("feed", data, extra))
                return "fed"

            def _draw_station_overlay(self, ctx, extra=None):
                calls.append(("draw", ctx, extra))
                return "drawn"

        return Window, calls

    def test_install_wraps_both_methods_once(self):
        cls, _ = self.make_class()
        self.assertTrue(ce.install(cls))
        first = (cls.set_ps_construction_bay, cls._draw_station_overlay)
        self.assertFalse(ce.install(cls))
        self.assertIs(cls.set_ps_construction_bay, first[0])
        self.assertTrue(all(hasattr(f, "__wrapped__") for f in first))

    def test_the_originals_run_first_with_the_same_arguments_and_results(self):
        cls, calls = self.make_class()
        ce.install(cls)
        window = cls()
        data = {"queue": [], "station_id": 1}
        self.assertEqual(window.set_ps_construction_bay(data, extra=5), "fed")
        self.assertEqual(window._draw_station_overlay("ctx", extra=6), "drawn")
        self.assertEqual(calls, [("feed", data, 5), ("draw", "ctx", 6)])

    def test_an_error_in_the_original_still_reaches_the_caller_and_nothing_is_fed(self):
        cls, _ = self.make_class()

        def boom(self, data):
            raise RuntimeError("handler failed")
        cls.set_ps_construction_bay = boom
        ce.install(cls)
        with mock.patch.object(ce, "observe") as observe:
            with self.assertRaises(RuntimeError):
                cls().set_ps_construction_bay({"queue": []})
        observe.assert_not_called()

    def test_an_update_feeds_the_estimate_with_the_stations_stock(self):
        cls, _ = self.make_class()
        ce.install(cls)
        window = cls()
        window._ps_station_cargo = [{"item_category": "resource", "item_key": "metal", "cargo_size": 7}]
        with mock.patch.object(ce, "observe") as observe:
            window.set_ps_construction_bay({"queue": [row(1)], "station_id": 2105})
        station, queue, now, stock = observe.call_args.args
        self.assertEqual((station, len(queue), stock), (2105, 1, {"metal": 7.0}))
        self.assertAlmostEqual(now, time.monotonic(), delta=5)

    def test_a_window_without_cargo_attributes_still_feeds(self):
        cls, _ = self.make_class()
        ce.install(cls)
        cls().set_ps_construction_bay({"queue": [row(1)], "station_id": 1})
        self.assertIn(1, ce._results)

    def test_an_error_while_estimating_switches_it_off_and_never_touches_the_game(self):
        cls, calls = self.make_class()
        ce.install(cls)
        window = cls()
        with mock.patch.object(ce, "observe", side_effect=ValueError("bad shape")):
            with self.assertLogs(ce.logger, "ERROR"):
                self.assertEqual(window.set_ps_construction_bay({"queue": []}), "fed")
        self.assertTrue(ce._failed)
        with mock.patch.object(ce, "observe") as observe, mock.patch.object(ce, "_draw") as draw:
            window.set_ps_construction_bay({"queue": []})
            window._draw_station_overlay("ctx")
        observe.assert_not_called()
        draw.assert_not_called()
        self.assertEqual(len(calls), 3)                               # the game's own methods always ran

    def test_an_error_while_drawing_switches_it_off_but_the_frame_still_draws(self):
        cls, _ = self.make_class()
        ce.install(cls)
        with mock.patch.object(ce, "_draw", side_effect=ValueError("bad")):
            with self.assertLogs(ce.logger, "ERROR"):
                self.assertEqual(cls()._draw_station_overlay("ctx"), "drawn")
        self.assertTrue(ce._failed)

    def test_switched_off_by_the_environment_it_neither_feeds_nor_draws(self):
        os.environ[ce.ENV_OFF] = "1"
        cls, _ = self.make_class()
        ce.install(cls)
        with mock.patch.object(ce, "observe") as observe, mock.patch.object(ce, "_draw") as draw:
            window = cls()
            window.set_ps_construction_bay({"queue": [row(1)]})
            window._draw_station_overlay("ctx")
        observe.assert_not_called()
        draw.assert_not_called()

    def test_non_dict_updates_are_ignored(self):
        cls, _ = self.make_class()
        ce.install(cls)
        with mock.patch.object(ce, "observe") as observe:
            cls().set_ps_construction_bay(None)
            cls().set_ps_construction_bay([1])
        observe.assert_not_called()

    def test_the_stations_are_remembered_up_to_a_limit(self):
        for station in range(ce.MAX_STATIONS + 3):
            ce.observe(station, [row(station)], 1000.0 + station)
        self.assertEqual(len(ce._states), ce.MAX_STATIONS)
        self.assertNotIn(0, ce._states)
        self.assertIn(ce.MAX_STATIONS + 2, ce._states)


class _Label(pygame.Surface):
    pass


class DrawTests(_Fixture):
    """The drawing hook with a stand-in window: when it draws, where, and what it leaves alone."""

    @classmethod
    def setUpClass(cls):
        pygame.font.init()

    def make(self, tab="Construction Bay", kind="player_station", cancel=None, rects=None):
        font = pygame.font.Font(None, 14)
        window = SimpleNamespace(
            _station_overlay={"kind": kind}, _ps_tab=tab, _ps_cb_cancel_confirm=cancel,
            _s=lambda px: px, _instrument_font=lambda size, bold=False: font, _F=lambda size, bold=False: font,
            _cached_label=lambda text, colour, f: f.render(text, True, colour),
            _ps_cb_proj_rects=rects if rects is not None else [
                (1, pygame.Rect(32, 100, 626, 26), {"name": "Packaged Oakheart", "paused": False}),
                (2, pygame.Rect(32, 126, 626, 26), {"name": "Gamma", "paused": True})])
        ctx = SimpleNamespace(screen=pygame.Surface((700, 300)))
        now = time.monotonic()
        ce._results = {1: ce.Eta("eta", 840.0, made_at=now), 2: ce.Eta("paused", made_at=now)}
        return window, ctx

    def ink(self, surface, rect):
        sub = surface.subsurface(rect)
        return pygame.mask.from_threshold(sub, (0, 0, 0, 255), (1, 1, 1, 255)).count() != sub.get_width() * sub.get_height()

    def test_it_draws_into_the_free_space_left_of_the_progress_bar_only(self):
        window, ctx = self.make()
        ce._draw(window, ctx, ce._palette(None))
        rect = window._ps_cb_proj_rects[0][1]
        column_left = rect.x + 30 + 220 + 6
        column_width = rect.width - 250 - 20
        bar_left = column_left + (column_width - column_width // 2)
        self.assertTrue(self.ink(ctx.screen, pygame.Rect(column_left, rect.y, bar_left - 8 - column_left, rect.height)))
        self.assertFalse(self.ink(ctx.screen, pygame.Rect(bar_left - 8, rect.y, 700 - bar_left + 8, rect.height)))
        self.assertFalse(self.ink(ctx.screen, pygame.Rect(0, rect.y, column_left, rect.height)))
        self.assertFalse(self.ink(ctx.screen, pygame.Rect(0, 0, 700, rect.y)))

    def test_nothing_is_drawn_on_other_tabs_other_overlays_or_under_the_cancel_dialog(self):
        for kwargs in ({"tab": "Storage"}, {"kind": "npc"}, {"cancel": {"project_id": 1}}, {"rects": []}):
            window, ctx = self.make(**kwargs)
            ce._draw(window, ctx, ce._palette(None))
            self.assertFalse(self.ink(ctx.screen, ctx.screen.get_rect()), kwargs)

    def test_a_missing_overlay_or_attributes_do_not_raise(self):
        ce._draw(SimpleNamespace(), SimpleNamespace(screen=pygame.Surface((10, 10))), ce._palette(None))
        window, ctx = self.make()
        window._station_overlay = None
        ce._draw(window, ctx, ce._palette(None))

    def test_each_state_has_its_own_colour(self):
        palette = {"text": (255, 0, 0), "caption": (0, 255, 0), "paused": (0, 0, 255), "warn": (255, 255, 0)}
        rects = [(pid, pygame.Rect(32, 100 + 26 * i, 626, 26), {"name": "Gamma", "paused": pid == 2})
                 for i, pid in enumerate((1, 2, 3, 4))]
        window, ctx = self.make(rects=rects)
        now = time.monotonic()
        ce._results = {1: ce.Eta("eta", 840.0, made_at=now), 2: ce.Eta("paused", made_at=now),
                       3: ce.Eta("estimating", made_at=now), 4: ce.Eta("starved", made_at=now)}
        ce._draw(window, ctx, palette)
        has = lambda rect, colour: pygame.mask.from_threshold(
            ctx.screen.subsurface(rect), colour, (40, 40, 40, 255)).count() > 0
        for (pid, rect, _), colour in zip(rects, ((255, 0, 0), (0, 0, 255), (0, 255, 0), (255, 255, 0))):
            others = [c for c in palette.values() if c != colour]
            self.assertTrue(has(rect, colour), pid)
            self.assertFalse(any(has(rect, c) for c in others), pid)

    def test_a_long_product_name_leaves_no_room_so_nothing_is_drawn(self):
        window, ctx = self.make()
        window._ps_cb_proj_rects[0][2]["name"] = "A" * 200
        ce._draw(window, ctx, ce._palette(None))
        rect = window._ps_cb_proj_rects[0][1]
        self.assertFalse(self.ink(ctx.screen, rect))

    def test_without_room_for_the_after_suffix_the_plain_estimate_is_drawn(self):
        window, ctx = self.make()
        window._s = lambda px: px
        now = time.monotonic()
        ce._results[1] = ce.Eta("eta", 840.0, ahead=3, made_at=now)
        wide = window._cached_label("~14m (after 3)", (255, 255, 255), window._F(12)).get_width()
        window._ps_cb_proj_rects[0][1].width = 250 + 20 + 2 * (wide - 14)       # room a little short of the suffix
        ce._draw(window, ctx, ce._palette(None))
        rect = window._ps_cb_proj_rects[0][1]
        self.assertTrue(self.ink(ctx.screen, rect))


class RealRenderTests(_Fixture):
    """The estimate against the game's own Construction Bay drawing (a real SolarSystemWindow)."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        pygame.init()
        pygame.display.set_mode((1280, 720))

    def window(self, scale=1.0):
        import Client
        from Client import SolarSystemWindow
        window = SolarSystemWindow.__new__(SolarSystemWindow)
        window._lock = threading.RLock()
        window._uk = scale
        window._username = "Owner"
        window._elapsed = 0.0
        window._label_cache = OrderedDict()
        window._hud_plate_cache = {}
        window._npc_market_watch_sent = None  # v0.5.70: _draw_station_overlay now syncs the NPC market watch every frame
        fonts = {}

        def font(size, bold=False, *args, **kwargs):
            key = (size, bold)
            if key not in fonts:
                fonts[key] = pygame.font.SysFont("consolas", max(6, round(size * scale)), bold=bold)
            return fonts[key]

        window._F = window._inventory_font = window._instrument_font = font
        queue = [dict(row(9774, 1, done=74_800_000, total=75_000_000, per_unit=75_000_000,
                          resource={"metal": 100_000_000}), name="Packaged Oakheart", qty=1),
                 dict(row(9782, 2, total=100_000, per_unit=10_000, resource={"metal": 20_000}, paused=True),
                      name="Station Expander Gamma", qty=10),
                 dict(row(9783, 3, done=22_925, total=200_000, per_unit=10_000, resource={"metal": 20_000}),
                      name="Station Expander Gamma", qty=20)]
        window._station_overlay = {"kind": "player_station", "x": 20, "y": 20, "data": {
            "attached_station_id": 2105, "owner_name": "Owner", "is_owner": True, "station_name": "Test Station",
            "management_access": True, "construction_enabled": True, "construction_projects": queue,
            "blueprints": []}}
        window._deployed_stations = [{"id": 2105, "shields": 100, "max_shields": 1000, "energy": 500,
                                      "max_energy": 1000}]
        window._ps_tab = "Construction Bay"
        for name, value in dict(
                _ps_control_scroll=0, _ps_control_sb_dragging=False, _ps_equipped_module_counts={},
                _ps_station_credits=100, _ps_station_cargo=[], _ps_workers_used=0, _ps_rename_dialog=None,
                _ps_motd_dialog=None, _ps_dock_perms_dialog=None, _ps_management_perms_dialog=None,
                _ps_cb_search="", _ps_cb_search_focus=False, _ps_cb_search_rect=None, _ps_cb_selected_proj=None,
                _ps_cb_cancel_confirm=None, _ps_cb_proj_scroll=0, _ps_cb_bp_scroll=0, _ps_cb_proj_panel_rect=None,
                _ps_cb_bp_panel_rect=None, _ps_cb_proj_sb_dragging=False, _ps_cb_proj_sb_drag_oy=0,
                _ps_cb_proj_sb_track_rect=None, _ps_cb_proj_sb_thumb_rect=None, _ps_cb_proj_sb_thumb_travel=0,
                _ps_cb_proj_sb_max_scroll=0, _ps_cb_bp_sb_dragging=False, _ps_cb_bp_sb_drag_oy=0,
                _ps_cb_bp_sb_track_rect=None, _ps_cb_bp_sb_thumb_rect=None, _ps_cb_bp_sb_thumb_travel=0,
                _ps_cb_bp_sb_max_scroll=0, _ps_cb_proj_rects=[], _ps_cb_bp_rects=[], _ps_cb_pause_rect=None,
                _ps_cb_cancel_rect=None, _ps_cb_bp_popup=None, _ps_cb_ctx_menu=None).items():
            setattr(window, name, value)
        return window, queue

    def render(self, window):
        from render_mixin import RenderFrame
        screen = pygame.Surface((1280, 720), pygame.SRCALPHA)
        screen.fill((6, 10, 20, 255))
        window._draw_station_overlay(RenderFrame(screen, 1280, 720, lambda *a: a, 1.0, {}, 1, 1, 1 / 60))
        return screen

    def differing_rows(self, a, b, rects):
        """For each row rect, the bounding box of the pixels that differ between two renders."""
        boxes = {}
        for pid, rect, _project in rects:
            top, bottom, left, right = None, None, None, None
            for y in range(rect.y, rect.bottom):
                for x in range(rect.x, rect.right):
                    if a.get_at((x, y)) != b.get_at((x, y)):
                        left = x if left is None else min(left, x)
                        right = x if right is None else max(right, x)
                        top = y if top is None else min(top, y)
                        bottom = y if bottom is None else max(bottom, y)
            boxes[pid] = None if left is None else pygame.Rect(left, top, right - left + 1, bottom - top + 1)
        return boxes

    def check(self, scale):
        window, queue = self.window(scale)
        now = time.monotonic()
        ce._results = {9774: ce.Eta("eta", 700.0, made_at=now, rough=True), 9782: ce.Eta("paused", made_at=now),
                       9783: ce.Eta("eta", 1200.0, ahead=1, made_at=now)}
        with mock.patch.object(ce, "_enabled", return_value=False):          # the game's own drawing alone
            plain = self.render(window)
        rects = list(window._ps_cb_proj_rects)
        with_eta = self.render(window)
        self.assertEqual({pid for pid, _, _ in rects}, {9774, 9782, 9783})
        boxes = self.differing_rows(plain, with_eta, rects)
        s = window._s
        for pid, rect, _project in rects:
            box = boxes[pid]
            self.assertIsNotNone(box, f"no estimate drawn for {pid}")
            column_left = rect.x + s(30) + s(220) + s(6)
            column_width = rect.width - (s(30) + s(220)) - s(20)
            bar_left = column_left + (column_width - column_width // 2)
            self.assertGreaterEqual(box.x, column_left, pid)                  # right of the product column
            self.assertLessEqual(box.right, bar_left - s(8) + 1, pid)         # clear of the progress bar
            self.assertTrue(rect.contains(box), pid)                          # inside its own row

    def test_the_estimate_lands_left_of_each_bar_in_the_real_construction_bay(self):
        self.check(1.0)

    def test_and_at_the_largest_ui_scale(self):
        self.check(1.5)

    def test_the_game_pixels_are_untouched_apart_from_the_estimates(self):
        window, queue = self.window()
        now = time.monotonic()
        ce._results = {9774: ce.Eta("eta", 700.0, made_at=now), 9782: ce.Eta("paused", made_at=now),
                       9783: ce.Eta("eta", 1200.0, ahead=1, made_at=now)}
        with mock.patch.object(ce, "_enabled", return_value=False):
            plain = self.render(window)
        rects = list(window._ps_cb_proj_rects)
        with_eta = self.render(window)
        outside = 0
        for y in range(720):
            for x in range(0, 1280):
                if plain.get_at((x, y)) != with_eta.get_at((x, y)) and not any(r.collidepoint(x, y) for _, r, _ in rects):
                    outside += 1
        self.assertEqual(outside, 0)

    def test_scrolled_or_overflowing_lists_only_get_text_on_the_rows_that_are_shown(self):
        window, queue = self.window()
        window._station_overlay["data"]["construction_projects"] = [
            dict(queue[0], project_id=1000 + i, queue_order=i, name=f"Job {i}") for i in range(12)]
        now = time.monotonic()
        ce._results = {1000 + i: ce.Eta("eta", 100.0 * i, ahead=i, made_at=now) for i in range(12)}
        self.render(window)
        shown = [pid for pid, _, _ in window._ps_cb_proj_rects]
        self.assertLess(len(shown), 12)
        self.assertTrue(shown)

    def test_the_real_class_is_hooked_by_importing_the_client(self):
        import Client
        self.assertTrue(Client.SolarSystemWindow.__dict__.get("_construction_eta_installed"))
        self.assertTrue(hasattr(Client.SolarSystemWindow.set_ps_construction_bay, "__wrapped__"))
        self.assertTrue(hasattr(Client.SolarSystemWindow._draw_station_overlay, "__wrapped__"))

    def test_a_real_update_through_the_real_window_method_feeds_the_estimate(self):
        window, queue = self.window()
        data = {"queue": queue, "blueprints": [], "station_id": 2105}
        window.set_ps_construction_bay(data)
        self.assertEqual(ce.eta_for(9774).kind, "estimating")
        self.assertEqual(ce.eta_for(9782).kind, "paused")
        self.assertEqual(window._station_overlay["data"]["construction_projects"], queue)   # the game still got it


if __name__ == "__main__":
    unittest.main()

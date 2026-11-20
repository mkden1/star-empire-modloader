"""construction_eta.py, resource-aware: what ARRIVES at the station (its cargo stream) next to what the projects
use up, projects that cannot get a resource ("waiting for Titanium"), and queue order that skips the ones that
cannot run.

Read off a real station (9448, eight projects): a project needs ALL its resources at once, one that is short of
titanium or oil does not hold back the ones behind it (the Beam Cannon, further down the queue, took all the
metal), and a delivery of 21 titanium let the Shield run for six updates and then it waited again."""
import os
import threading
import time
import unittest
from collections import deque
from types import SimpleNamespace
from unittest import mock

import pygame

import construction_eta as ce
from test_construction_eta import TICK, Run, costs, row


def make_row(ratios, remaining, labels=None, pid=1):
    return ce._Row(pid=pid, name="p", done=0.0, total=remaining, paused=False, ratios=ratios, progress=0.0,
                   labels=labels or {})


def state_with(used=None, arrived=None, used_watched=0.0, flow_watched=0.0):
    state = ce.StationState()
    state.used.watched, state.used.totals = used_watched, dict(used or {})
    state.flow.arrivals.watched, state.flow.arrivals.totals = flow_watched, dict(arrived or {})
    return state


class TallyTests(unittest.TestCase):
    def test_a_rate_needs_the_minimum_watched_time(self):
        tally = ce.Tally(60.0)
        tally.add(1000.0, 30.0, {"a": 30.0})
        self.assertIsNone(tally.rate("a"))
        tally.add(1030.0, 40.0, {"a": 10.0})
        self.assertAlmostEqual(tally.rate("a"), 40.0 / 70.0)
        self.assertEqual(tally.rate("never seen"), 0.0)

    def test_old_buckets_fall_out_of_the_window(self):
        with mock.patch.object(ce, "FLOW_WINDOW_S", 600.0):
            tally = ce.Tally(10.0)
            tally.add(0.0, 60.0, {"a": 600.0})
            tally.add(300.0, 60.0, {"a": 0.0})
            self.assertAlmostEqual(tally.rate("a"), 600.0 / 120.0)
            tally.add(1000.0, 60.0, {"a": 60.0})                 # the first two are now older than the window
            self.assertAlmostEqual(tally.rate("a"), 1.0)
            self.assertAlmostEqual(tally.watched, 60.0)
            self.assertEqual(len(tally.buckets), 1)

    def test_a_long_run_does_not_keep_a_bucket_per_message(self):
        tally = ce.Tally(10.0)
        for second in range(3600):
            tally.add(float(second), 1.0, {"a": 1.0})
        self.assertLessEqual(len(tally.buckets), 61)
        self.assertAlmostEqual(tally.rate("a"), 1.0)


class FlowTests(unittest.TestCase):
    def test_rises_count_falls_and_the_first_message_do_not(self):
        flow = ce.Flow()
        flow.update({"metal": 500.0}, 1000.0)                    # the baseline, not an arrival
        for k in range(1, 131):
            stock = 500.0 + (100.0 if k >= 50 else 0.0) - (30.0 if k >= 90 else 0.0)
            flow.update({"metal": stock}, 1000.0 + k)
        self.assertAlmostEqual(flow.rate("metal"), 100.0 / 130.0)     # one rise of 100 over 130 s; the fall is ignored

    def test_nothing_is_believed_before_two_minutes_of_stream(self):
        flow = ce.Flow()
        for k in range(int(ce.MIN_FLOW_S)):
            flow.update({"a": 0.0}, 1000.0 + k)
        self.assertIsNone(flow.rate("a"))                        # 119 s watched
        flow.update({"a": 0.0}, 1000.0 + ce.MIN_FLOW_S)
        self.assertEqual(flow.rate("a"), 0.0)                    # 120 s: "nothing arrives" is now believed

    def test_a_gap_in_the_stream_is_not_time_watched_and_hides_what_came_in_it(self):
        flow = ce.Flow()
        flow.update({"m": 0.0}, 1000.0)
        flow.update({"m": 100.0}, 1000.0 + ce.MAX_INTERVAL_S + 1)
        self.assertEqual(flow.arrivals.watched, 0.0)
        flow.update({"m": 100.0}, 1000.0 + ce.MAX_INTERVAL_S + 2)
        self.assertEqual(flow.arrivals.watched, 1.0)

    def test_a_resource_that_leaves_the_cargo_is_a_fall_not_an_arrival(self):
        flow = ce.Flow()
        flow.update({"m": 50.0, "o": 5.0}, 1000.0)
        flow.update({"o": 5.0}, 1001.0)
        self.assertEqual(flow.arrivals.totals.get("m", 0.0), 0.0)
        flow.update({"o": 5.0, "m": 20.0}, 1002.0)               # and coming back IS one
        self.assertEqual(flow.arrivals.totals["m"], 20.0)

    def test_the_latest_stock_is_kept_as_a_copy(self):
        flow = ce.Flow()
        assert flow.stock is None
        stock = {"a": 1.0}
        flow.update(stock, 1.0)
        stock["a"] = 99.0
        self.assertEqual(flow.stock, {"a": 1.0})


class SupplyTests(unittest.TestCase):
    def test_unknown_while_neither_view_has_watched_long_enough(self):
        self.assertIsNone(state_with().supply_rate("x"))

    def test_the_larger_of_the_two_views(self):
        state = state_with(used={"x": 100.0}, used_watched=100.0, arrived={"x": 300.0}, flow_watched=200.0)
        self.assertAlmostEqual(state.supply_rate("x"), 1.5)
        state = state_with(used={"x": 400.0}, used_watched=100.0, arrived={"x": 300.0}, flow_watched=200.0)
        self.assertAlmostEqual(state.supply_rate("x"), 4.0)

    def test_consumption_alone_is_believed_when_it_is_positive(self):
        self.assertAlmostEqual(state_with(used={"x": 100.0}, used_watched=100.0).supply_rate("x"), 1.0)
        self.assertAlmostEqual(state_with(arrived={"x": 50.0}, flow_watched=200.0).supply_rate("x"), 0.25)

    def test_nothing_arrives_is_only_believed_when_both_views_agree(self):
        self.assertIsNone(state_with(used_watched=100.0).supply_rate("x"))           # consumption says 0, cargo unwatched
        self.assertIsNone(state_with(flow_watched=200.0).supply_rate("x"))           # cargo says 0, nothing consumed yet
        self.assertEqual(state_with(used_watched=100.0, flow_watched=200.0).supply_rate("x"), 0.0)


class ShortageTests(unittest.TestCase):
    """A project needing 6,000 titanium (0.0005 per man-hour, 12M man-hours left) with a thousand in stock."""

    def setUp(self):
        self.row = make_row({"titanium": 0.0005}, 12_000_000.0, {"titanium": "Titanium"})

    def times(self, state, stock):
        return state.shortage_times(self.row, stock)

    def test_no_cargo_known_means_nothing_can_be_said(self):
        self.assertEqual(self.times(state_with(), None), {})

    def test_enough_in_stock_is_no_shortage(self):
        self.assertEqual(self.times(state_with(), {"titanium": 6_000.0}), {})
        self.assertEqual(self.times(state_with(), {"titanium": 1e9}), {})

    def test_the_time_to_bring_in_the_difference(self):
        state = state_with(arrived={"titanium": 10.0}, flow_watched=1000.0, used_watched=1000.0)     # 0.01 per second
        self.assertAlmostEqual(self.times(state, {"titanium": 1_000.0})["titanium"], 5_000.0 / 0.01)

    def test_a_missing_stock_row_is_zero(self):
        state = state_with(arrived={"titanium": 10.0}, flow_watched=1000.0, used_watched=1000.0)
        self.assertAlmostEqual(self.times(state, {"metal": 5.0})["titanium"], 6_000.0 / 0.01)

    def test_unknown_supply_is_unknown_and_nothing_arriving_is_never(self):
        self.assertIsNone(self.times(state_with(), {})["titanium"])
        state = state_with(used_watched=1000.0, flow_watched=1000.0)
        self.assertEqual(self.times(state, {})["titanium"], float("inf"))

    def test_blocked_when_it_would_take_longer_than_a_week(self):
        for arrived, blocked in ((9.0, True), (10.0, False)):       # 6,000 / (X / 1000 s) against 604,800 s
            state = state_with(arrived={"titanium": arrived}, flow_watched=1000.0, used_watched=1000.0)
            self.assertEqual(state.blocked_on(self.row, {}), "titanium" if blocked else None, arrived)

    def test_blocked_stays_blocked_until_it_would_take_under_half_a_week(self):
        state = state_with(arrived={"titanium": 15.0}, flow_watched=1000.0, used_watched=1000.0)     # 4 days
        self.assertIsNone(state.blocked_on(self.row, {}))
        self.assertEqual(state.blocked_on(self.row, {}, was_blocked=True), "titanium")
        state = state_with(arrived={"titanium": 20.0}, flow_watched=1000.0, used_watched=1000.0)     # 3.5 days
        self.assertIsNone(state.blocked_on(self.row, {}, was_blocked=True))

    def test_the_worst_shortage_is_the_one_that_would_take_longest(self):
        row = make_row({"titanium": 0.0005, "oil": 0.0005, "metal": 1.0}, 12_000_000.0)
        state = state_with(used={"metal": 1e6}, arrived={"titanium": 100.0, "oil": 10.0}, used_watched=1000.0,
                           flow_watched=1000.0)
        worst = state.worst_shortage(row, {})
        self.assertEqual(worst[0], "oil")                              # 6,000 / 0.01 beats 6,000 / 0.1 and metal's

    def test_the_floor_is_the_longest_finite_wait_and_names_its_resource(self):
        row = make_row({"titanium": 0.0005, "oil": 0.0005}, 12_000_000.0)
        state = state_with(arrived={"titanium": 100.0, "oil": 10.0}, flow_watched=1000.0, used_watched=1000.0)
        seconds, resource = state.floor(row, {})
        self.assertEqual(resource, "oil")
        self.assertAlmostEqual(seconds, 6_000.0 / 0.01)
        self.assertEqual(state_with().floor(row, {}), (0.0, None))
        never = state_with(used_watched=1000.0, flow_watched=1000.0)
        self.assertEqual(never.floor(row, {}), (0.0, None))          # "never" is a block, not a floor


class BlockedTests(unittest.TestCase):
    """Shield (needs titanium), Beam and Xfer (metal and gold): only Beam ever moves."""

    STOCK = {"metal": 0.0, "titanium": 0.0, "gold": 1_000_000.0}

    def queue(self, stuck_resource=None):
        stuck = row(1, order=1, total=2_000_000, per_unit=2_000_000, name="Shield",
                    resource=stuck_resource or {"metal": 5_000_000, "titanium": 1_000})
        beam = row(2, order=2, total=2_000_000, per_unit=2_000_000, name="Beam",
                   resource={"metal": 5_000_000, "gold": 200})
        xfer = row(3, order=3, total=2_000_000, per_unit=2_000_000, name="Xfer",
                   resource={"metal": 5_000_000, "gold": 200})
        return stuck, beam, xfer

    def run_it(self, updates=20, cargo=STOCK, moves=None, stuck_resource=None):
        run = Run(*self.queue(stuck_resource))
        run.step()
        run.run(updates, moves or {2: 4_000}, cargo=cargo)           # Beam: 400 man-hours/s = 1,000 metal/s
        return run

    def test_a_project_short_of_a_resource_that_never_arrives_is_blocked_and_named(self):
        run = self.run_it()
        self.assertEqual((run.last[1].kind, run.last[1].resource), ("blocked", "Titanium"))
        self.assertEqual(ce.eta_text(run.last[1], run.t), "waiting for Titanium")

    def test_the_project_behind_it_leads_and_the_one_after_waits_only_for_that_one(self):
        run = self.run_it()
        beam, xfer = run.last[2], run.last[3]
        remaining_beam = 2_000_000 - 4_000 * 20
        self.assertEqual((beam.kind, beam.ahead, beam.parallel), ("eta", 0, False))
        self.assertAlmostEqual(beam.seconds, remaining_beam / 400.0)
        self.assertEqual((xfer.kind, xfer.ahead), ("eta", 1))              # the blocked Shield is not "ahead"
        self.assertAlmostEqual(xfer.seconds, remaining_beam / 400.0 + 2_000_000 / 400.0)

    def test_nothing_is_called_blocked_before_the_cargo_has_been_watched(self):
        run = Run(*self.queue())
        run.step()
        run.run(20, {2: 4_000})                                            # no cargo stream at all
        self.assertEqual((run.last[1].kind, run.last[1].resource), ("starved", ""))

    def test_nothing_is_called_blocked_in_the_first_two_minutes_of_the_stream(self):
        run = Run(*self.queue())
        run.step()
        run.run(8, {2: 4_000}, cargo=self.STOCK)                           # ~80 s of cargo
        self.assertEqual((run.last[1].kind, run.last[1].resource), ("starved", ""))   # stuck, but the cause is not known yet

    def test_enough_in_stock_means_not_blocked(self):
        run = self.run_it(cargo={"metal": 0.0, "titanium": 10_000.0, "gold": 1_000_000.0})
        self.assertNotEqual(run.last[1].resource, "Titanium")

    def test_a_resource_that_keeps_arriving_is_not_a_block(self):
        def arriving(second):                                              # 5 in, then gone: 2.5 per second arrive
            return {"metal": 0.0, "titanium": 5.0 if int(second) % 2 == 0 else 0.0, "gold": 1_000_000.0}
        run = Run(*self.queue())
        run.step()
        run.run(20, {1: 1_000, 2: 4_000}, cargo=arriving)
        self.assertEqual(run.last[1].kind, "eta")                          # moving, and what it needs arrives in minutes
        self.assertEqual(run.last[1].resource, "")

    def test_the_label_is_the_servers_display_name(self):
        stuck = {"metal": 5_000_000, "titanium": 1_000}
        queue = list(self.queue(stuck))
        queue[0]["resource_costs"][1]["display_name"] = "Titanium Ore"
        run = Run(*queue)
        run.step()
        run.run(20, {2: 4_000}, cargo=self.STOCK)
        self.assertEqual(run.last[1].resource, "Titanium Ore")

    def test_a_paused_project_is_paused_not_blocked(self):
        run = Run(*self.queue())
        run.step(paused={1: True})
        run.run(20, {2: 4_000}, cargo=self.STOCK)
        self.assertEqual(run.last[1].kind, "paused")

    def test_a_project_that_finishes_is_done_not_blocked(self):
        run = Run(*self.queue())
        run.specs[1]["manhours_done"] = run.specs[1]["manhours_total"]
        run.step()
        run.run(20, {2: 4_000}, cargo=self.STOCK)
        self.assertEqual(run.last[1].kind, "done")


class StarvedTests(unittest.TestCase):
    def two(self):
        return (row(1, order=1, total=2_000_000, per_unit=2_000_000, name="Stuck", resource={"metal": 5_000_000}),
                row(2, order=2, total=2_000_000, per_unit=2_000_000, name="Runs", resource={"metal": 5_000_000}))

    def test_a_leader_that_has_not_moved_for_its_whole_window_is_skipped_so_the_next_leads(self):
        run = Run(*self.two())
        run.step()
        run.run(ce.MIN_INTERVALS + 4, {2: 4_000})                           # the leader is stuck, the follower runs
        self.assertEqual(run.last[1].kind, "starved")
        runs = run.last[2]
        self.assertEqual((runs.kind, runs.ahead, runs.parallel), ("eta", 0, False))   # it leads now, from its own history

    def test_a_skipped_leader_keeps_recording_and_leads_again_the_moment_it_moves(self):
        run = Run(*self.two())
        run.step()
        run.run(ce.MIN_INTERVALS + 4, {2: 4_000})
        self.assertGreater(run.state.own_count(1, "leader"), ce.MIN_INTERVALS)      # skipped, yet still recording
        run.step({1: 4_000, 2: 4_000})                                       # it moves: not starved in that very update
        self.assertEqual(run.last[1].kind, "eta")
        self.assertEqual(run.last[1].ahead, 0)

    def test_a_stuck_leader_with_a_known_shortage_is_named_not_just_starved(self):
        def arriving(second):                                               # 2 titanium per 20 s: 10,000 s to bring in 1,000
            return {"metal": 0.0, "titanium": 2.0 if int(second) % 20 == 0 else 0.0}
        stuck = row(1, order=1, total=2_000_000, per_unit=2_000_000, name="Stuck",
                    resource={"metal": 5_000_000, "titanium": 1_000})
        runs = row(2, order=2, total=2_000_000, per_unit=2_000_000, name="Runs", resource={"metal": 5_000_000})
        run = Run(stuck, runs)
        run.step()
        run.run(30, {2: 4_000}, cargo=arriving)                             # titanium arrives, yet it does not move
        self.assertEqual((run.last[1].kind, run.last[1].resource), ("blocked", "Titanium"))    # longer than metal's 5,000 s

    def test_a_stuck_leader_with_nothing_known_is_just_starved(self):
        run = Run(*self.two())
        run.step()
        run.run(ce.MIN_INTERVALS + 4, {2: 4_000})
        self.assertEqual((run.last[1].kind, run.last[1].resource), ("starved", ""))

    def test_a_follower_that_has_not_moved_is_not_skipped_it_is_waiting_its_turn(self):
        run = Run(*self.two())
        run.step()
        run.run(ce.MIN_INTERVALS + 4, {1: 4_000})                           # the leader runs, the follower gets nothing
        self.assertEqual(run.last[1].kind, "eta")
        self.assertEqual(run.last[2].ahead, 1)


class OwnSpeedTests(unittest.TestCase):
    def test_a_new_leader_falls_back_on_what_it_did_as_a_follower_when_that_was_progress(self):
        state = ce.StationState()
        state.history[(7, "follower")] = deque([(10.0, 4_000.0)] * 10)
        self.assertEqual(state.own_speed(7, "leader"), (400.0, 10))

    def test_not_when_the_follower_merely_stood_still(self):
        state = ce.StationState()
        state.history[(7, "follower")] = deque([(10.0, 0.0)] * 10)
        self.assertEqual(state.own_speed(7, "leader"), (None, 0))

    def test_a_follower_does_not_borrow_its_old_leader_speed(self):
        state = ce.StationState()
        state.history[(7, "leader")] = deque([(10.0, 4_000.0)] * 10)
        self.assertEqual(state.own_speed(7, "follower"), (None, 0))

    def test_its_own_role_wins_when_it_has_enough_history(self):
        state = ce.StationState()
        state.history[(7, "leader")] = deque([(10.0, 1_000.0)] * 8)
        state.history[(7, "follower")] = deque([(10.0, 4_000.0)] * 10)
        self.assertEqual(state.own_speed(7, "leader"), (100.0, 8))


class TrickleTests(unittest.TestCase):
    """A project whose time is set by what it waits for - much longer than its work - trickles along: it uses
    little of what the others share, so it neither delays them for its whole time nor counts as ahead of them."""

    def build(self, titanium_amount):
        state = ce.StationState()
        state.history[(1, "leader")] = deque([(10.0, 4_000.0)] * 10)               # 400 man-hours/s: 5,000 s of work
        state.used.watched, state.used.totals = 1000.0, {"metal": 1_000_000.0, "titanium": 1_000.0}   # 1/s titanium
        leader = row(1, order=1, total=2_000_000, per_unit=2_000_000, name="Slow",
                     resource={"titanium": titanium_amount}, done=0)
        follower = row(2, order=2, total=2_000_000, per_unit=2_000_000, name="Fast", resource={"metal": 5_000_000})
        return state, [leader, follower]

    def test_a_much_longer_wait_than_work_is_shown_with_its_resource_and_blocks_nobody(self):
        state, queue = self.build(15_000)                                           # 15,000 s to bring in, 5,000 s of work
        results = state.update(queue, 1000.0, {"titanium": 0.0})
        slow, fast = results[1], results[2]
        self.assertEqual(slow.kind, "eta")
        self.assertAlmostEqual(slow.seconds, 15_000.0)
        self.assertEqual(slow.limited_by, "Titanium")
        self.assertEqual(ce.eta_text(ce.Eta(slow.kind, slow.seconds, slow.ahead, slow.parallel, 1000.0, slow.rough,
                                            limited_by=slow.limited_by), 1000.0), "~4h 10m? (Titanium)")   # 10 updates: rough
        self.assertEqual(fast.ahead, 0)                                             # the trickling project is not ahead
        self.assertAlmostEqual(fast.seconds, 2_000_000 / 400.0)                    # and does not add its time

    def test_a_slightly_longer_wait_is_not_a_trickle_it_is_the_normal_case(self):
        state, queue = self.build(6_000)                                            # 6,000 s against 5,000 s of work
        results = state.update(queue, 1000.0, {"titanium": 0.0})
        slow, fast = results[1], results[2]
        self.assertAlmostEqual(slow.seconds, 6_000.0)                               # never sooner than the wait
        self.assertEqual(slow.limited_by, "")
        self.assertEqual(fast.ahead, 1)
        self.assertAlmostEqual(fast.seconds, 5_000.0 + 2_000_000 / 400.0)           # it does wait for the work

    def test_a_wait_shorter_than_the_work_changes_nothing(self):
        state, queue = self.build(1_000)
        slow = state.update(queue, 1000.0, {"titanium": 0.0})[1]
        self.assertAlmostEqual(slow.seconds, 5_000.0)
        self.assertEqual(slow.limited_by, "")

    def test_the_floor_also_lifts_a_follower_timed_by_its_own_speed(self):
        state = ce.StationState()
        state.history[(1, "leader")] = deque([(10.0, 4_000.0)] * 10)
        state.history[(2, "follower")] = deque([(10.0, 1_000.0)] * 10)              # 100 man-hours/s, in parallel
        state.used.watched, state.used.totals = 1000.0, {"metal": 1_000_000.0, "titanium": 1_000.0}
        queue = [row(1, order=1, total=2_000_000, per_unit=2_000_000, resource={"metal": 5_000_000}),
                 row(2, order=2, total=2_000_000, per_unit=2_000_000, resource={"titanium": 100_000})]
        follower = state.update(queue, 1000.0, {"titanium": 0.0})[2]
        self.assertTrue(follower.parallel)
        self.assertAlmostEqual(follower.seconds, 100_000.0)                         # 20,000 s of work, 100,000 s to wait
        self.assertEqual(follower.limited_by, "Titanium")


class ScenarioTests(unittest.TestCase):
    """The real station in miniature (projects of 20M man-hours like the real ones): Shield and Sensor want
    titanium, Energy wants oil, Beam and Xfer want gold - and everyone wants metal. A few titanium arrive now
    and then."""

    def build(self):
        def project(pid, order, name, extra):
            return row(pid, order=order, total=20_000_000, per_unit=20_000_000, name=name,
                       resource={"metal": 50_000_000, **extra})
        return Run(project(1, 1, "Shield", {"titanium": 10_000}), project(2, 2, "Energy", {"oil": 10_000}),
                   project(3, 3, "Sensor", {"titanium": 10_000}), project(4, 4, "Beam", {"gold": 2_000}),
                   project(5, 5, "Xfer", {"gold": 2_000}))

    STOCK = {"metal": 0.0, "titanium": 0.0, "oil": 0.0, "gold": 1_000_000.0}

    def test_the_stuck_ones_are_named_the_runner_leads_and_the_last_waits_for_it_alone(self):
        run = self.build()
        run.step()
        run.run(20, {4: 4_000}, cargo=self.STOCK)                                    # Beam: 400 man-hours/s
        kinds = {pid: (e.kind, e.resource) for pid, e in run.last.items()}
        self.assertEqual(kinds[1], ("blocked", "Titanium"))
        self.assertEqual(kinds[2], ("blocked", "Oil"))
        self.assertEqual(kinds[3], ("blocked", "Titanium"))
        beam, xfer = run.last[4], run.last[5]
        self.assertEqual((beam.kind, beam.ahead), ("eta", 0))
        self.assertEqual((xfer.kind, xfer.ahead), ("eta", 1))
        self.assertAlmostEqual(xfer.seconds, beam.seconds + 20_000_000 / 400.0, delta=1.0)

    def test_after_hours_of_watching_a_small_delivery_of_the_scarce_resource_changes_nothing_behind_it(self):
        run = self.build()
        run.step()
        run.run(1080, {4: 4_000}, cargo=self.STOCK)                                  # three hours
        steady = run.last[5].seconds
        delivery = {"metal": 0.0, "titanium": 30.0, "oil": 0.0, "gold": 1_000_000.0}
        run.step({1: 3_400, 4: 600}, cargo=delivery)                                 # 30 titanium: the Shield runs
        run.run(5, {1: 3_400}, cargo=self.STOCK)
        run.run(5, {4: 4_000}, cargo=self.STOCK)                                     # and then it is over
        self.assertLess(abs(run.last[5].seconds / steady - 1), 0.1)
        self.assertEqual((run.last[1].kind, run.last[1].resource), ("blocked", "Titanium"))
        self.assertEqual(run.last[2].resource, "Oil")
        self.assertEqual(run.last[3].resource, "Titanium")
        self.assertEqual(run.last[5].ahead, 1)


class _Fixture(unittest.TestCase):
    def setUp(self):
        ce.reset_for_tests()
        self.addCleanup(ce.reset_for_tests)
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(ce.ENV_OFF, None)


class CargoHookTests(_Fixture):
    def make_class(self):
        calls = []

        class Window:
            def set_ps_construction_bay(self, data, extra=None):
                calls.append(("bay", data))
                return "bay"

            def _draw_station_overlay(self, ctx, extra=None):
                return "drawn"

            def set_ps_station_cargo(self, data, extra=None):
                calls.append(("cargo", data, extra))
                return "cargo"

        return Window, calls

    def cargo(self, metal=100.0, station=2105):
        return {"station_id": station, "cargo": [
            {"item_category": "resource", "item_key": "metal", "cargo_size": metal},
            {"item_category": "resource", "item_key": "workers", "cargo_size": 99, "quantity": 3}]}

    def test_the_cargo_method_is_wrapped_the_original_runs_first_and_its_result_passes_through(self):
        cls, calls = self.make_class()
        ce.install(cls)
        data = self.cargo()
        self.assertEqual(cls().set_ps_station_cargo(data, extra=5), "cargo")
        self.assertEqual(calls, [("cargo", data, 5)])
        self.assertTrue(hasattr(cls.set_ps_station_cargo, "__wrapped__"))

    def test_an_error_in_the_original_cargo_method_reaches_the_caller_and_nothing_is_fed(self):
        cls, _ = self.make_class()

        def boom(self, data):
            raise RuntimeError("cargo failed")
        cls.set_ps_station_cargo = boom
        ce.install(cls)
        with mock.patch.object(ce, "observe_cargo") as observe:
            with self.assertRaises(RuntimeError):
                cls().set_ps_station_cargo(self.cargo())
        observe.assert_not_called()

    def test_the_stream_feeds_the_station_flow_and_its_latest_stock(self):
        cls, _ = self.make_class()
        ce.install(cls)
        window = cls()
        with mock.patch.object(ce.time, "monotonic", side_effect=[1000.0, 1001.0]):
            window.set_ps_station_cargo(self.cargo(100.0))
            window.set_ps_station_cargo(self.cargo(250.0))
        flow = ce._states[2105].flow
        self.assertEqual(flow.stock, {"metal": 250.0})                              # workers are not a resource
        self.assertEqual(flow.arrivals.totals, {"metal": 150.0})                    # the rise between the two messages

    def test_an_empty_cargo_list_means_not_loaded_yet_not_nothing_in_stock(self):
        cls, _ = self.make_class()
        ce.install(cls)
        cls().set_ps_station_cargo({"station_id": 1, "cargo": []})
        self.assertNotIn(1, ce._states)

    def test_odd_payloads_are_ignored(self):
        cls, _ = self.make_class()
        ce.install(cls)
        for data in (None, [1], {"station_id": 1}, {"station_id": 1, "cargo": "bad"}):
            cls().set_ps_station_cargo(data)
        self.assertFalse(ce._failed)

    def test_an_error_while_reading_the_cargo_switches_off_and_never_touches_the_game(self):
        cls, calls = self.make_class()
        ce.install(cls)
        with mock.patch.object(ce, "observe_cargo", side_effect=ValueError("bad")):
            with self.assertLogs(ce.logger, "ERROR"):
                self.assertEqual(cls().set_ps_station_cargo(self.cargo()), "cargo")
        self.assertTrue(ce._failed)
        self.assertEqual(calls[0][0], "cargo")

    def test_switched_off_by_the_environment_the_cargo_is_not_read(self):
        os.environ[ce.ENV_OFF] = "1"
        cls, _ = self.make_class()
        ce.install(cls)
        cls().set_ps_station_cargo(self.cargo())
        self.assertEqual(len(ce._states), 0)

    def test_a_class_without_a_cargo_method_still_installs(self):
        class Old:
            def set_ps_construction_bay(self, data):
                return 1

            def _draw_station_overlay(self, ctx):
                return 2

        self.assertTrue(ce.install(Old))
        self.assertEqual(Old().set_ps_construction_bay({"queue": []}), 1)

    def test_a_bay_update_uses_the_streams_stock_when_the_window_has_no_cargo_yet(self):
        cls, _ = self.make_class()
        ce.install(cls)
        window = cls()
        window.set_ps_station_cargo(self.cargo(77.0, station=9))
        window._ps_station_cargo = []                                                # not loaded into the window
        seen = []
        real = ce.StationState.update

        def spy(self, queue, now, stock=None):
            seen.append(stock)
            return real(self, queue, now, stock)

        with mock.patch.object(ce.StationState, "update", spy):
            window.set_ps_construction_bay({"queue": [row(1)], "station_id": 9})
        self.assertEqual(seen, [None])                      # nothing passed: the state falls back on its own stream
        self.assertEqual(ce._states[9].flow.stock, {"metal": 77.0})


class TextTests(unittest.TestCase):
    def eta(self, seconds=840.0, **kw):
        return ce.Eta("eta", seconds, made_at=100.0, **kw)

    def test_blocked_names_the_resource(self):
        self.assertEqual(ce.eta_text(ce.Eta("blocked", resource="Titanium", made_at=1.0), 2.0), "waiting for Titanium")
        self.assertEqual(ce.eta_text(ce.Eta("blocked", made_at=1.0), 2.0), "waiting for resources")

    def test_blocked_is_shown_however_old_it_is(self):
        self.assertEqual(ce.eta_text(ce.Eta("blocked", resource="Oil", made_at=1.0), 1e6), "waiting for Oil")

    def test_what_limits_an_estimate_and_what_it_waits_behind_share_the_suffix(self):
        self.assertEqual(ce.eta_text(self.eta(limited_by="Titanium"), 100.0), "~14m (Titanium)")
        self.assertEqual(ce.eta_text(self.eta(ahead=2, limited_by="Titanium"), 100.0), "~14m (after 2, Titanium)")
        self.assertEqual(ce.eta_text(self.eta(ahead=2, limited_by="Titanium", parallel=True), 100.0), "~14m (Titanium)")
        self.assertEqual(ce.eta_text(self.eta(limited_by="Titanium", rough=True), 100.0), "~14m? (Titanium)")


class DrawTests(_Fixture):
    @classmethod
    def setUpClass(cls):
        pygame.font.init()

    def window(self, width=626):
        font = pygame.font.Font(None, 14)
        window = SimpleNamespace(
            _station_overlay={"kind": "player_station"}, _ps_tab="Construction Bay", _ps_cb_cancel_confirm=None,
            _s=lambda px: px, _instrument_font=lambda size, bold=False: font, _F=lambda size, bold=False: font,
            _cached_label=lambda text, colour, f: f.render(text, True, colour),
            _ps_cb_proj_rects=[(1, pygame.Rect(32, 100, width, 26), {"name": "Beam", "paused": False})])
        return window, SimpleNamespace(screen=pygame.Surface((700, 300))), font

    def ink(self, surface, rect):
        sub = surface.subsurface(rect)
        return pygame.mask.from_threshold(sub, (0, 0, 0, 255), (1, 1, 1, 255)).count() != sub.get_width() * sub.get_height()

    def test_a_blocked_project_is_drawn_in_the_warning_colour(self):
        palette = {"text": (255, 0, 0), "caption": (0, 255, 0), "paused": (0, 0, 255), "warn": (255, 255, 0)}
        window, ctx, _ = self.window()
        ce._results = {1: ce.Eta("blocked", resource="Titanium", made_at=time.monotonic())}
        ce._draw(window, ctx, palette)
        rect = window._ps_cb_proj_rects[0][1]
        sub = ctx.screen.subsurface(rect)
        self.assertGreater(pygame.mask.from_threshold(sub, (255, 255, 0), (40, 40, 40, 255)).count(), 0)
        self.assertEqual(pygame.mask.from_threshold(sub, (255, 0, 0), (40, 40, 40, 255)).count(), 0)

    def test_without_room_for_the_whole_suffix_the_plain_estimate_is_drawn(self):
        window, ctx, font = self.window()
        ce._results = {1: ce.Eta("eta", 840.0, ahead=2, made_at=time.monotonic(), limited_by="Titanium")}
        full = font.size("~14m (after 2, Titanium)")[0]
        plain = font.size("~14m")[0]
        rect = window._ps_cb_proj_rects[0][1]
        # the room left of the bar is about half the progress column less 8 px: make it fit the plain text only
        wanted = (full + plain) // 2
        column_width = 2 * (wanted + 8)
        rect.width = column_width + 250 + 20
        ce._draw(window, ctx, ce._palette(None))
        column_left = rect.x + 250 + 6
        bar_left = column_left + (column_width - column_width // 2)
        room = pygame.Rect(column_left, rect.y, bar_left - 8 - column_left, rect.height)
        drawn = pygame.mask.from_threshold(ctx.screen.subsurface(room), (0, 0, 0, 255), (1, 1, 1, 255))
        ink_width = max((r.right for r in (drawn.get_bounding_rects() or [pygame.Rect(0, 0, 0, 0)])), default=0)
        self.assertTrue(self.ink(ctx.screen, rect))
        self.assertLessEqual(plain, wanted)
        self.assertLess(wanted, full)
        self.assertLessEqual(ink_width, room.width)


class RealWindowTests(_Fixture):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        pygame.init()
        pygame.display.set_mode((1280, 720))

    def test_the_real_cargo_method_is_hooked_and_still_stores_the_cargo(self):
        import Client
        window = Client.SolarSystemWindow.__new__(Client.SolarSystemWindow)
        window._lock = threading.RLock()
        window._station_overlay = None
        window._ps_workers_used, window._ps_resource_caps, window._ps_resource_reserves = 0, {}, {}
        window._ps_station_credits, window._ps_storage_version = 0.0, 0
        self.assertTrue(hasattr(Client.SolarSystemWindow.set_ps_station_cargo, "__wrapped__"))
        data = {"station_id": 2105, "cargo": [{"item_category": "resource", "item_key": "metal", "cargo_size": 5}],
                "hull_used": 1, "hull_space": 10, "workers_used": 3, "equipped_module_counts": {}}
        window.set_ps_station_cargo(data)
        self.assertEqual(window._ps_station_cargo, data["cargo"])                    # the game still got it
        self.assertEqual(ce._states[2105].flow.stock, {"metal": 5.0})                # and so did the estimate


if __name__ == "__main__":
    unittest.main()

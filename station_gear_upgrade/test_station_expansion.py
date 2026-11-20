"""station_expansion: the ADD 50 GAMMA + 50 WORKERS button of the station prefab dialog - what it moves, the order and
the waits of its commands, how it fails, and how it is drawn, clicked and hooked beside UPGRADE GEAR."""
import json
import os
import threading
import unittest
from collections import OrderedDict
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame

import station_expansion as sxp
import station_gear_upgrade as sgu

GAMMA = "station_expander_gamma"
GAMMA_KEY = "item:station:station_expander_gamma"
GAMMA_NAME = "Station Expander Gamma"
BEAM2, BEAM3 = "station_beam_cannon_mk2", "station_beam_cannon_mk3"


def hold_gamma(qty=50, bound=False, uid="55"):
    """Station Expander Gammas in the ship's hold, as the server lists them."""
    row = {"item_class": "cargo_item", "item_category": "station", "item_type": GAMMA, "display_name": GAMMA_NAME,
           "cargo_key": f"item:station:{GAMMA}:{uid}", "stack_quantity": qty}
    if bound:
        row["bound"] = True
    return row


def hold_workers(amount=50, pinned=False):
    row = {"item_class": "cargo", "item_type": "workers", "resource": "workers", "amount": amount}
    if pinned:
        row["pinned"] = True
    return row


def workers_row(size):
    return {"item_key": "workers", "item_category": "resource", "item_type": "workers", "cargo_size": size}


def gamma_row(qty, row_id=31):
    return {"item_key": GAMMA_KEY, "item_type": GAMMA, "display_name": GAMMA_NAME, "quantity": qty, "row_id": row_id,
            "equippable": True, "equip_singleton": False}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Fake:
    """The parts of SolarSystemWindow the button touches, plus the station's side of the conversation: the
    ``server_*`` methods change the state the way the station's cargo push would report it."""

    def __init__(self):
        self.sent, self.equips, self.log = [], [], []
        self._lock = threading.RLock()
        self._station_overlay = {"kind": "player_station", "data": {"attached_station_id": 7, "management_access": True}}
        self._ps_station_cargo = [workers_row(10), {"item_key": "item:station:hull_plate", "item_type": "hull_plate",
                                                    "quantity": 40, "row_id": 12}]
        self._ps_equipped_module_counts = {}
        self._ps_workers_used = 4
        self._inventory = [hold_gamma(), hold_workers()]
        self._ps_prefab_dialog = {"station_id": 7}

    def _send_json(self, cmd, payload):
        self.sent.append((cmd, payload))

    def _send_equip_json(self, cmd, payload):
        self.sent.append((cmd, payload))
        self.equips.append((cmd, payload))

    def chat_log(self, text, colour=None):
        self.log.append(text)

    # the station's side
    def server_take_workers(self, amount):
        for row in self._ps_station_cargo:
            if row.get("item_key") == "workers":
                row["cargo_size"] += amount
                return
        self._ps_station_cargo.append(workers_row(amount))

    def server_take_gammas(self, amount, row_id=31):
        for row in self._ps_station_cargo:
            if row.get("item_type") == GAMMA:
                row["quantity"] += amount
                return
        self._ps_station_cargo.append(gamma_row(amount, row_id))

    def server_fit(self, amount):
        self._ps_equipped_module_counts[GAMMA] = self._ps_equipped_module_counts.get(GAMMA, 0) + amount
        self._ps_workers_used += amount

    def commands(self):
        return [cmd for cmd, _ in self.sent]


def last(win):
    return win.log[-1]


class SetUp(unittest.TestCase):
    def setUp(self):
        sgu.reset_for_tests()
        sxp.reset_for_tests()
        self.clock = Clock()
        for module in (sxp, sgu):
            patcher = mock.patch.object(module, "time", SimpleNamespace(monotonic=self.clock))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.win = Fake()


class HoldTests(unittest.TestCase):
    def test_stacks_add_up_and_bound_or_pinned_things_are_counted_apart(self):
        counts = sxp.hold_counts([hold_gamma(30, uid="1"), hold_gamma(20, uid="2"), hold_gamma(7, bound=True, uid="3"),
                                  hold_workers(40), hold_workers(15, pinned=True)])
        self.assertEqual((counts["expanders"], counts["bound"], counts["workers"], counts["pinned"]), (50, 7, 40, 15))
        self.assertEqual(counts["name"], GAMMA_NAME)

    def test_a_single_expander_row_without_a_stack_count_is_one(self):
        row = hold_gamma()
        del row["stack_quantity"]
        self.assertEqual(sxp.hold_counts([row])["expanders"], 1)

    def test_other_cargo_is_not_taken_for_expanders_or_workers(self):
        odd = [{"item_class": "cargo_item", "item_category": "ship_module", "item_type": GAMMA},
               {"item_class": "cargo_item", "item_category": "station", "item_type": "station_expander_beta"},
               {"item_class": "weapon", "item_category": "station", "item_type": GAMMA},
               {"item_class": "cargo", "item_type": "metal", "resource": "metal", "amount": 99}, "junk", None, {}]
        counts = sxp.hold_counts(odd)
        self.assertEqual((counts["expanders"], counts["bound"], counts["workers"], counts["pinned"]), (0, 0, 0, 0))
        self.assertEqual(sxp.hold_counts(None)["expanders"], 0)

    def test_workers_are_found_by_resource_name_or_item_type_and_a_bad_amount_is_zero(self):
        by_type = {"item_class": "cargo", "item_type": "workers", "amount": 6}
        bad = {"item_class": "cargo", "resource": "workers", "amount": "lots"}
        negative = {"item_class": "cargo", "resource": "workers", "amount": -4}
        self.assertEqual(sxp.hold_counts([by_type, bad, negative])["workers"], 6)

    def test_only_cargo_rows_count_as_workers_and_only_station_items_as_expanders(self):
        counts = sxp.hold_counts([{"item_class": "cargo_item", "item_type": "workers", "amount": 5},
                                  {"item_class": "cargo_item", "item_category": "ship_module", "item_type": GAMMA,
                                   "stack_quantity": 9}])
        self.assertEqual((counts["workers"], counts["expanders"]), (0, 0))

    def test_the_name_comes_from_the_hold_row(self):
        row = hold_gamma()
        row["display_name"] = "Gamma Expander"
        self.assertEqual(sxp.hold_counts([row])["name"], "Gamma Expander")


class StationCountTests(SetUp):
    def test_workers_in_the_station_are_the_cargo_size_of_the_workers_row(self):
        self.assertEqual(sxp.station_workers(self.win), 10)
        self.win._ps_station_cargo.append(workers_row(5))
        self.assertEqual(sxp.station_workers(self.win), 15)

    def test_a_row_without_cargo_size_falls_back_to_its_quantity(self):
        self.win._ps_station_cargo = [{"item_key": "workers", "quantity": 8}]
        self.assertEqual(sxp.station_workers(self.win), 8)

    def test_other_rows_and_odd_data_are_not_workers(self):
        self.win._ps_station_cargo = [{"item_key": "metal_ore", "cargo_size": 99}, None, "x",
                                      {"item_key": "workers", "cargo_size": "?"}, {"item_key": "workers", "cargo_size": -5}]
        self.assertEqual(sxp.station_workers(self.win), 0)
        self.win._ps_station_cargo = None
        self.assertEqual(sxp.station_workers(self.win), 0)

    def test_free_workers_are_what_the_fitted_modules_leave(self):
        self.assertEqual(sxp.free_workers(self.win), 6)
        self.win._ps_workers_used = 99
        self.assertEqual(sxp.free_workers(self.win), 0)

    def test_unfitted_expanders_are_the_stored_ones_less_the_fitted(self):
        self.assertEqual(sxp.unfitted(self.win), 0)
        self.win._ps_station_cargo.append(gamma_row(30))
        self.assertEqual(sxp.unfitted(self.win), 30)
        self.win._ps_equipped_module_counts[GAMMA] = 12
        self.assertEqual(sxp.unfitted(self.win), 18)
        self.win._ps_equipped_module_counts[GAMMA] = 99
        self.assertEqual(sxp.unfitted(self.win), 0)


class PlanTests(unittest.TestCase):
    def plan(self, inventory, stored=0, free=0):
        return sxp.build_plan(inventory, stored, free)

    def test_fifty_of_each_from_a_full_hold(self):
        plan = self.plan([hold_gamma(80), hold_workers(120)], free=0)
        self.assertEqual((plan["expanders"], plan["workers"], plan["fit"]), (50, 50, 50))
        self.assertEqual((plan["hold_expanders"], plan["hold_workers"]), (80, 120))

    def test_a_short_hold_gives_what_it_has(self):
        plan = self.plan([hold_gamma(20), hold_workers(12)])
        self.assertEqual((plan["expanders"], plan["workers"], plan["fit"]), (20, 12, 20))

    def test_the_fit_counts_what_is_already_in_the_station_but_never_more_than_fifty(self):
        self.assertEqual(self.plan([hold_gamma(20)], stored=10)["fit"], 30)
        self.assertEqual(self.plan([hold_gamma(50)], stored=30)["fit"], 50)
        self.assertEqual(self.plan([], stored=30)["fit"], 30)

    def test_free_workers_and_the_workers_added_decide_how_many_will_be_fitted(self):
        self.assertEqual(self.plan([hold_gamma(), hold_workers(20)], free=6)["fits"], 26)
        self.assertEqual(self.plan([hold_gamma(), hold_workers(50)], free=6)["fits"], 50)
        self.assertEqual(self.plan([hold_gamma(), hold_workers(0)], free=60)["fits"], 50)
        self.assertEqual(self.plan([hold_gamma(), hold_workers(0)], free=0)["fits"], 0)


class EvaluateTests(SetUp):
    def test_a_docked_station_with_expanders_in_the_hold_can_start(self):
        problem, plan = sxp.evaluate(self.win)
        self.assertIsNone(problem)
        self.assertEqual((plan["expanders"], plan["workers"], plan["fit"], plan["fits"]), (50, 50, 50, 50))

    def test_no_dialog_no_overlay_another_station_a_jump_gate_and_no_management_each_say_why(self):
        win = Fake()
        win._ps_prefab_dialog = None
        self.assertIn("prefab dialog", sxp.evaluate(win)[0])
        win = Fake()
        win._station_overlay = None
        self.assertIn("not docked", sxp.evaluate(win)[0])
        win = Fake()
        win._station_overlay["data"]["attached_station_id"] = 8
        self.assertIn("not docked", sxp.evaluate(win)[0])
        win = Fake()
        win._station_overlay["data"]["jump_gate_key"] = "gate"
        self.assertEqual(sxp.evaluate(win)[0], "A jump gate has no station modules.")
        win = Fake()
        win._station_overlay["data"]["management_access"] = False
        self.assertIn("cannot manage", sxp.evaluate(win)[0])

    def test_the_gear_upgrade_keeps_its_own_jump_gate_wording(self):
        win = Fake()
        win._station_overlay["data"]["jump_gate_key"] = "gate"
        self.assertEqual(sgu.evaluate(win)[0], "A jump gate has no station modules to upgrade.")

    def test_no_expanders_anywhere_says_so(self):
        win = Fake()
        win._inventory = [hold_workers()]
        problem, plan = sxp.evaluate(win)
        self.assertEqual(problem, "No Station Expander Gamma in your hold.")
        self.assertEqual(plan["fit"], 0)

    def test_bound_expanders_cannot_go_into_a_station(self):
        win = Fake()
        win._inventory = [hold_gamma(5, bound=True), hold_workers()]
        self.assertEqual(sxp.evaluate(win)[0], "Your Station Expander Gamma is bound, so it cannot go into a station.")

    def test_expanders_left_in_the_station_are_enough_to_start(self):
        win = Fake()
        win._inventory = []
        win._ps_station_cargo.append(gamma_row(30))
        problem, plan = sxp.evaluate(win)
        self.assertIsNone(problem)
        self.assertEqual((plan["expanders"], plan["fit"]), (0, 30))


class RunTests(SetUp):
    def test_the_whole_run_in_order_and_each_step_waits_for_the_station(self):
        win = self.win
        self.assertTrue(sxp.start(win))
        self.assertEqual(win.sent, [("PS_TRANSFER_TO_STATION", {"station_id": 7, "item_key": "workers", "amount": 50})])
        sxp.advance(win)
        self.assertEqual(len(win.sent), 1)                         # the station has not shown it yet
        win.server_take_workers(50)
        sxp.advance(win)
        self.assertEqual(win.sent[1], ("PS_TRANSFER_TO_STATION", {"station_id": 7, "item_key": GAMMA_KEY, "amount": 50}))
        sxp.advance(win)
        self.assertEqual(len(win.sent), 2)
        win.server_take_gammas(50)
        sxp.advance(win)
        self.assertEqual(win.sent[2], ("PS_EQUIP_MODULE", {"station_id": 7, "row_id": 31, "module_type": GAMMA,
                                                           "count": 50}))
        self.assertEqual(win.equips, [win.sent[2]])                # the fit goes through the game's click-sound funnel
        self.assertTrue(sxp.running(win))
        sxp.advance(win)
        self.assertTrue(sxp.running(win))                          # not fitted yet
        win.server_fit(50)
        sxp.advance(win)
        self.assertFalse(sxp.running(win))
        self.assertEqual(len(win.sent), 3)
        self.assertEqual(last(win), "Station expansion: done - 50 workers added, 50 Station Expander Gamma added, "
                                    "50 fitted.")

    def test_the_start_says_what_it_will_do(self):
        sxp.start(self.win)
        self.assertEqual(self.win.log, ["Station expansion: adding 50 Station Expander Gamma and 50 workers, then "
                                        "fitting 50 expanders."])

    def test_a_short_hold_sends_only_what_it_has(self):
        win = self.win
        win._inventory = [hold_gamma(20), hold_workers(12)]
        sxp.start(win)
        self.assertEqual(win.sent[0][1]["amount"], 12)
        win.server_take_workers(12)
        sxp.advance(win)
        self.assertEqual(win.sent[1][1]["amount"], 20)
        win.server_take_gammas(20)
        sxp.advance(win)
        self.assertEqual(win.sent[2][1]["count"], 20)
        win.server_fit(20)
        sxp.advance(win)
        self.assertEqual(last(win), "Station expansion: done - 12 workers added, 20 Station Expander Gamma added, "
                                    "20 fitted.")

    def test_a_hold_without_workers_starts_with_the_expanders(self):
        win = self.win
        win._inventory = [hold_gamma()]
        sxp.start(win)
        self.assertEqual(win.sent, [("PS_TRANSFER_TO_STATION", {"station_id": 7, "item_key": GAMMA_KEY, "amount": 50})])
        self.assertIn("adding 50 Station Expander Gamma, then", win.log[0])
        win.server_take_gammas(50)
        sxp.advance(win)
        win.server_fit(50)
        sxp.advance(win)
        self.assertEqual(last(win), "Station expansion: done - 50 Station Expander Gamma added, 50 fitted.")
        self.assertEqual(win.commands(), ["PS_TRANSFER_TO_STATION", "PS_EQUIP_MODULE"])

    def test_expanders_left_in_the_station_are_fitted_with_nothing_moved(self):
        win = self.win
        win._inventory = []
        win._ps_station_cargo.append(gamma_row(30))
        sxp.start(win)
        self.assertEqual(win.sent, [("PS_EQUIP_MODULE", {"station_id": 7, "row_id": 31, "module_type": GAMMA,
                                                         "count": 30})])
        self.assertEqual(win.log[0], "Station expansion: fitting the 30 expanders already in the station.")
        win.server_fit(30)
        sxp.advance(win)
        self.assertEqual(last(win), "Station expansion: done - 30 fitted.")

    def test_workers_alone_are_added_before_the_expanders_left_in_the_station_are_fitted(self):
        win = self.win
        win._inventory = [hold_workers(10)]
        win._ps_station_cargo.append(gamma_row(30))
        sxp.start(win)
        self.assertEqual(win.sent[0][1], {"station_id": 7, "item_key": "workers", "amount": 10})
        win.server_take_workers(10)
        sxp.advance(win)
        self.assertEqual(win.commands(), ["PS_TRANSFER_TO_STATION", "PS_EQUIP_MODULE"])
        self.assertEqual(win.sent[1][1]["count"], 30)

    def test_no_more_than_fifty_are_fitted_whatever_is_stored(self):
        win = self.win
        win._ps_station_cargo.append(gamma_row(30))
        sxp.start(win)
        win.server_take_workers(50)
        sxp.advance(win)
        self.assertEqual(win.sent[1][1]["amount"], 50)
        win.server_take_gammas(50)
        sxp.advance(win)
        self.assertEqual(win.sent[2][1]["count"], 50)              # 80 are in the station; 50 are fitted

    def test_expanders_already_in_the_station_do_not_count_as_the_deposit_arriving(self):
        win = self.win
        win._ps_station_cargo.append(gamma_row(50))                 # 50 unfitted ones are already in storage
        sxp.start(win)
        win.server_take_workers(50)
        sxp.advance(win)
        self.assertEqual(win.commands(), ["PS_TRANSFER_TO_STATION", "PS_TRANSFER_TO_STATION"])
        sxp.advance(win)
        self.assertEqual(len(win.sent), 2)                          # still waiting for the 50 from the hold
        win.server_take_gammas(50)
        sxp.advance(win)
        self.assertEqual(win.commands()[-1], "PS_EQUIP_MODULE")

    def test_expanders_fitted_before_are_not_counted_as_this_fit(self):
        win = self.win
        win._ps_station_cargo.append(gamma_row(50))
        win._ps_equipped_module_counts[GAMMA] = 50                  # 50 are already fitted
        sxp.start(win)
        win.server_take_workers(50)
        sxp.advance(win)
        win.server_take_gammas(50)
        sxp.advance(win)
        self.assertEqual(win.sent[-1][1]["count"], 50)
        sxp.advance(win)
        self.assertTrue(sxp.running(win))                           # the old 50 do not finish the run
        win.server_fit(50)
        sxp.advance(win)
        self.assertFalse(sxp.running(win))
        self.assertIn("50 fitted.", last(win))
        self.assertNotIn("100 fitted", last(win))

    def test_an_expander_already_fitted_is_not_asked_for_again(self):
        win = self.win
        win._ps_station_cargo.append(gamma_row(10))
        win._ps_equipped_module_counts[GAMMA] = 10
        win._inventory = [hold_gamma(5)]
        sxp.start(win)
        win.server_take_gammas(5)
        sxp.advance(win)
        self.assertEqual(win.sent[-1][1]["count"], 5)

    def test_a_partial_worker_deposit_is_kept_after_the_wait_and_the_run_goes_on(self):
        win = self.win
        sxp.start(win)
        win.server_take_workers(30)
        sxp.advance(win)
        self.assertEqual(len(win.sent), 1)                         # the rest may still come
        self.clock.now += sxp.STEP_WAIT_S + 1
        sxp.advance(win)
        self.assertEqual(win.commands(), ["PS_TRANSFER_TO_STATION", "PS_TRANSFER_TO_STATION"])
        win.server_take_gammas(50)
        sxp.advance(win)
        win.server_fit(50)
        sxp.advance(win)
        self.assertEqual(last(win), "Station expansion: done - 30 workers added, 50 Station Expander Gamma added, "
                                    "50 fitted.")

    def test_a_partial_expander_deposit_is_fitted_as_far_as_it_goes(self):
        win = self.win
        sxp.start(win)
        win.server_take_workers(50)
        sxp.advance(win)
        win.server_take_gammas(35)
        self.clock.now += sxp.STEP_WAIT_S + 1
        sxp.advance(win)
        self.assertEqual(win.sent[2][1]["count"], 35)
        win.server_fit(35)
        sxp.advance(win)
        self.assertEqual(last(win), "Station expansion: done - 50 workers added, 35 Station Expander Gamma added, "
                                    "35 fitted.")

    def test_a_short_run_is_reported_in_the_warning_colour_and_a_full_one_is_not(self):
        colours = []
        win = self.win
        win.chat_log = lambda text, colour=None: colours.append(colour)
        sxp.start(win)
        win.server_take_workers(30)
        self.clock.now += sxp.STEP_WAIT_S + 1
        sxp.advance(win)
        win.server_take_gammas(50)
        sxp.advance(win)
        win.server_fit(50)
        sxp.advance(win)
        self.assertEqual(colours[0], (120, 220, 160))
        self.assertEqual(colours[-1], (255, 170, 80))
        win = Fake()
        win.chat_log = lambda text, colour=None: colours.append(colour)
        sxp.start(win)
        win.server_take_workers(50)
        sxp.advance(win)
        win.server_take_gammas(50)
        sxp.advance(win)
        win.server_fit(50)
        sxp.advance(win)
        self.assertEqual(colours[-1], (120, 220, 160))

    def test_every_kind_of_short_run_is_reported_in_the_warning_colour(self):
        def run(prepare, take_workers=50, take_gammas=50, fit=50):
            colours = []
            win = Fake()
            win.chat_log = lambda text, colour=None: colours.append(colour)
            prepare(win)
            sxp.start(win)
            win.server_take_workers(take_workers)
            self.clock.now += sxp.STEP_WAIT_S + 1
            sxp.advance(win)
            win.server_take_gammas(take_gammas)
            self.clock.now += sxp.STEP_WAIT_S + 1
            sxp.advance(win)
            win.server_fit(fit)
            self.clock.now += sxp.STEP_WAIT_S + 1
            sxp.advance(win)
            return colours[-1]

        warn, fine = (255, 170, 80), (120, 220, 160)
        self.assertEqual(run(lambda win: None), fine)
        self.assertEqual(run(lambda win: None, take_workers=30), warn)       # fewer workers arrived
        self.assertEqual(run(lambda win: None, take_gammas=35, fit=35), warn)   # fewer expanders arrived
        self.assertEqual(run(lambda win: None, fit=20), warn)                 # fewer were fitted

    def test_workers_that_never_arrive_stop_the_run_with_nothing_moved(self):
        win = self.win
        sxp.start(win)
        self.clock.now += sxp.STEP_WAIT_S + 1
        sxp.advance(win)
        self.assertFalse(sxp.running(win))
        self.assertEqual(len(win.sent), 1)
        self.assertEqual(last(win), "Station expansion: stopped at the workers step: the station did not take the "
                                    "workers (is its store full?). Nothing was moved.")

    def test_a_step_is_not_given_up_before_its_time_but_is_at_the_limit(self):
        win = self.win
        sxp.start(win)
        self.clock.now += sxp.STEP_WAIT_S - 0.5
        sxp.advance(win)
        self.assertTrue(sxp.running(win))
        self.clock.now += 0.5                                       # exactly STEP_WAIT_S since the command
        sxp.advance(win)
        self.assertFalse(sxp.running(win))

    def test_a_deposit_larger_than_asked_counts_only_what_was_asked(self):
        win = self.win
        sxp.start(win)
        win.server_take_workers(60)                                 # another source added 10 on top
        sxp.advance(win)
        win.server_take_gammas(55)
        sxp.advance(win)
        win.server_fit(50)
        sxp.advance(win)
        self.assertEqual(last(win), "Station expansion: done - 50 workers added, 50 Station Expander Gamma added, "
                                    "50 fitted.")

    def test_expanders_that_never_arrive_leave_the_workers_in_the_station_and_say_so(self):
        win = self.win
        sxp.start(win)
        win.server_take_workers(50)
        sxp.advance(win)
        self.clock.now += sxp.STEP_WAIT_S + 1
        sxp.advance(win)
        self.assertFalse(sxp.running(win))
        self.assertEqual(last(win), "Station expansion: stopped at the expanders step: the station did not take the "
                                    "expanders (is its storage full?). 50 workers are in the station; press the "
                                    "button again to go on.")

    def test_each_step_has_its_own_time_limit(self):
        win = self.win
        sxp.start(win)
        self.clock.now += sxp.STEP_WAIT_S - 1
        win.server_take_workers(50)
        sxp.advance(win)                                           # the expanders step starts now
        self.clock.now += sxp.STEP_WAIT_S - 1
        sxp.advance(win)
        self.assertTrue(sxp.running(win))

    def test_a_refused_fit_stops_with_the_servers_reason_and_leaves_everything_in_the_station(self):
        win = self.win
        sxp.start(win)
        win.server_take_workers(50)
        sxp.advance(win)
        win.server_take_gammas(50)
        sxp.advance(win)
        sent = len(win.sent)
        sxp.on_equip_result(win, {"ok": False, "module_type": GAMMA, "reason": "Not enough free workers."})
        self.assertFalse(sxp.running(win))
        self.assertEqual(len(win.sent), sent)                      # nothing is sent back to the hold
        self.assertEqual(last(win), "Station expansion: stopped at the equip step: Not enough free workers. The "
                                    "expanders and workers are in the station; fit the expanders in its Storage tab, "
                                    "or press the button again.")

    def fitting(self):
        win = self.win
        sxp.start(win)
        win.server_take_workers(50)
        sxp.advance(win)
        win.server_take_gammas(50)
        sxp.advance(win)
        return win

    def test_a_partial_grant_ends_the_run_when_the_station_shows_them_and_says_why(self):
        win = self.fitting()
        sxp.on_equip_result(win, {"ok": True, "module_type": GAMMA, "count": 30, "reason": "Only 30 workers were free."})
        self.assertTrue(sxp.running(win))                          # the station has not shown them yet
        win.server_fit(30)
        sxp.advance(win)
        self.assertFalse(sxp.running(win))
        self.assertEqual(last(win), "Station expansion: done - 50 workers added, 50 Station Expander Gamma added, "
                                    "30 fitted. 20 expanders are in the station's storage, not fitted (Only 30 "
                                    "workers were free).")

    def test_an_answer_that_comes_after_the_cargo_push_finishes_the_run_at_once(self):
        win = self.fitting()
        win.server_fit(30)
        sxp.advance(win)
        self.assertTrue(sxp.running(win))                          # 30 of 50: more may come
        sxp.on_equip_result(win, {"ok": True, "module_type": GAMMA, "count": 30, "reason": "Only 30 were free."})
        self.assertFalse(sxp.running(win))
        self.assertIn("30 fitted. 20 expanders are in the station's storage", last(win))

    def test_a_full_grant_needs_no_reason(self):
        win = self.fitting()
        sxp.on_equip_result(win, {"ok": True, "module_type": GAMMA, "count": 50})
        win.server_fit(50)
        sxp.advance(win)
        self.assertEqual(last(win), "Station expansion: done - 50 workers added, 50 Station Expander Gamma added, "
                                    "50 fitted.")

    def test_a_grant_cannot_make_the_run_wait_for_more_than_was_asked(self):
        win = self.fitting()
        sxp.on_equip_result(win, {"ok": True, "module_type": GAMMA, "count": 80})
        win.server_fit(50)
        sxp.advance(win)
        self.assertFalse(sxp.running(win))

    def test_an_answer_without_a_count_waits_for_everything_or_the_time_limit(self):
        win = self.fitting()
        sxp.on_equip_result(win, {"ok": True, "module_type": GAMMA})
        win.server_fit(1)
        sxp.advance(win)
        self.assertTrue(sxp.running(win))
        win.server_fit(49)
        sxp.advance(win)
        self.assertFalse(sxp.running(win))

    def test_an_answer_that_fitted_none_stops_the_run_with_its_reason(self):
        win = self.fitting()
        sxp.on_equip_result(win, {"ok": True, "module_type": GAMMA, "count": 0, "reason": "No free workers."})
        self.assertFalse(sxp.running(win))
        self.assertIn("No free workers", last(win))
        win = Fake()
        sxp.start(win)
        win.server_take_workers(50)
        sxp.advance(win)
        win.server_take_gammas(50)
        sxp.advance(win)
        sxp.on_equip_result(win, {"ok": True, "module_type": GAMMA, "count": 0})
        self.assertIn("fitted none", last(win))

    def test_a_fit_that_is_never_confirmed_stops_the_run(self):
        win = self.fitting()
        self.clock.now += sxp.STEP_WAIT_S + 1
        sxp.advance(win)
        self.assertFalse(sxp.running(win))
        self.assertIn("the station did not fit them", last(win))

    def test_some_fitted_when_time_runs_out_still_counts(self):
        win = self.fitting()
        win.server_fit(20)
        sxp.advance(win)
        self.assertTrue(sxp.running(win))
        self.clock.now += sxp.STEP_WAIT_S + 1
        sxp.advance(win)
        self.assertFalse(sxp.running(win))
        self.assertEqual(last(win), "Station expansion: done - 50 workers added, 50 Station Expander Gamma added, "
                                    "20 fitted. 30 expanders are in the station's storage, not fitted (not enough "
                                    "free workers?).")

    def test_answers_that_are_not_about_this_fit_are_ignored(self):
        win = self.fitting()
        sxp.on_equip_result(win, {"ok": False, "module_type": "metal_drill", "reason": "x"})
        sxp.on_equip_result(win, "not a dict")
        sxp.on_equip_result(win, None)
        self.assertTrue(sxp.running(win))
        sxp.on_equip_result(win, {"ok": False, "reason": "no module named"})
        self.assertFalse(sxp.running(win))

    def test_an_answer_outside_the_fit_step_is_ignored(self):
        win = self.win
        sxp.start(win)
        sxp.on_equip_result(win, {"ok": False, "module_type": GAMMA, "reason": "x"})
        self.assertTrue(sxp.running(win))
        sxp.on_equip_result(Fake(), {"ok": False, "reason": "x"})              # no run at all

    def test_awaiting_the_fit_only_in_that_step(self):
        win = self.win
        self.assertFalse(sxp.awaiting_equip(win))
        self.assertFalse(sxp.awaiting_equip(None))
        sxp.start(win)
        self.assertFalse(sxp.awaiting_equip(win))
        win.server_take_workers(50)
        sxp.advance(win)
        self.assertFalse(sxp.awaiting_equip(win))                  # depositing the expanders
        win.server_take_gammas(50)
        sxp.advance(win)
        self.assertTrue(sxp.awaiting_equip(win))

    def test_a_station_row_without_an_id_stops_the_run_cleanly_instead_of_fitting_blind(self):
        win = self.win
        sxp.start(win)
        win.server_take_workers(50)
        sxp.advance(win)
        win.server_take_gammas(50, row_id=None)
        sxp.advance(win)
        self.assertFalse(sxp.running(win))
        self.assertIn("stopped at the equip step: the station has no expanders to fit", last(win))
        self.assertEqual(win.equips, [])

    def test_nothing_unfitted_after_the_deposit_stops_the_run_cleanly(self):
        win = self.win
        win._ps_equipped_module_counts[GAMMA] = 50                  # the station already shows 50 fitted
        sxp.start(win)
        win.server_take_workers(50)
        sxp.advance(win)
        win.server_take_gammas(50)
        sxp.advance(win)
        self.assertFalse(sxp.running(win))
        self.assertIn("stopped at the equip step: the station has no expanders to fit", last(win))
        self.assertEqual(win.equips, [])

    def test_leaving_the_station_stops_the_run_and_sends_nothing_more(self):
        win = self.win
        sxp.start(win)
        win._station_overlay = None
        win.server_take_workers(50)
        sxp.advance(win)
        self.assertFalse(sxp.running(win))
        self.assertEqual(len(win.sent), 1)
        self.assertIn("no longer docked", last(win))

    def test_docking_at_another_station_stops_it_too(self):
        win = self.win
        sxp.start(win)
        win._station_overlay["data"]["attached_station_id"] = 9
        sxp.advance(win)
        self.assertFalse(sxp.running(win))

    def test_a_second_press_does_not_start_a_second_run(self):
        win = self.win
        self.assertTrue(sxp.start(win))
        self.assertFalse(sxp.start(win))
        self.assertEqual(len(win.sent), 1)

    def test_it_will_not_start_while_the_gear_upgrade_runs_and_the_gear_upgrade_will_not_start_during_it(self):
        win = Fake()
        win._ps_station_cargo += [{"item_key": "item:station:" + BEAM2, "item_type": BEAM2, "display_name": "Beam Mk II",
                                   "quantity": 1, "row_id": 11, "equippable": True, "equip_singleton": True}]
        win._ps_equipped_module_counts = {BEAM2: 1}
        win._inventory += [{"item_class": "cargo_item", "item_category": "station", "item_type": BEAM3,
                            "display_name": "Beam Mk III", "cargo_key": "item:station:" + BEAM3 + ":9"}]
        self.assertTrue(sgu.start(win))
        count = len(win.sent)
        self.assertFalse(sxp.start(win))
        self.assertEqual(len(win.sent), count)
        win = Fake()
        win._ps_station_cargo += [{"item_key": "item:station:" + BEAM2, "item_type": BEAM2, "display_name": "Beam Mk II",
                                   "quantity": 1, "row_id": 11, "equippable": True, "equip_singleton": True}]
        win._ps_equipped_module_counts = {BEAM2: 1}
        win._inventory += [{"item_class": "cargo_item", "item_category": "station", "item_type": BEAM3,
                            "display_name": "Beam Mk III", "cargo_key": "item:station:" + BEAM3 + ":9"}]
        self.assertTrue(sxp.start(win))
        count = len(win.sent)
        self.assertFalse(sgu.start(win))
        self.assertEqual(len(win.sent), count)

    def test_nothing_to_add_and_refusals_say_so_and_send_nothing(self):
        win = Fake()
        win._inventory = []
        self.assertFalse(sxp.start(win))
        self.assertEqual(last(win), "Station expansion: No Station Expander Gamma in your hold.")
        win = Fake()
        win._station_overlay = None
        self.assertFalse(sxp.start(win))
        self.assertIn("not docked", last(win))
        self.assertEqual(win.sent, [])
        self.assertFalse(sxp.running(win))

    def test_stopping_after_an_error_says_so_and_clears_the_run(self):
        win = self.win
        sxp.start(win)
        sxp.stop_after_error(win)
        self.assertFalse(sxp.running(win))
        self.assertIn("stopped after an error", last(win))
        count = len(win.log)
        sxp.stop_after_error(win)                                  # nothing running: silent
        sxp.stop_after_error(None)
        self.assertEqual(len(win.log), count)

    def test_without_a_run_advance_does_nothing(self):
        sxp.advance(self.win)
        self.assertEqual(self.win.sent, [])


class PanelStateTests(SetUp):
    def test_what_each_situation_shows(self):
        win = self.win
        state = sxp.panel_state(win)
        self.assertEqual((state["enabled"], state["label"]), (True, "ADD 50 GAMMA + 50 WORKERS"))
        self.assertEqual(state["lines"], [("row", "50 Station Expander Gamma"), ("row", "50 Workers")])

        win = Fake()
        win._inventory = [hold_workers()]
        state = sxp.panel_state(win)
        self.assertEqual((state["enabled"], state["label"]), (False, "NOTHING TO ADD"))
        self.assertEqual(state["lines"], [("dim", "No Station Expander Gamma in your hold.")])

        win = Fake()
        win._station_overlay = None
        state = sxp.panel_state(win)
        self.assertEqual((state["enabled"], state["lines"]), (False, [("dim", "You are not docked at this station.")]))

    def test_the_label_follows_what_the_hold_has(self):
        win = self.win
        win._inventory = [hold_gamma(20), hold_workers(12)]
        self.assertEqual(sxp.panel_state(win)["label"], "ADD 20 GAMMA + 12 WORKERS")
        win._inventory = [hold_gamma(20)]
        state = sxp.panel_state(win)
        self.assertEqual(state["label"], "ADD 20 GAMMA + 0 WORKERS")
        self.assertEqual(state["lines"][0], ("row", "20 Station Expander Gamma"))
        self.assertNotIn(("row", "0 Workers"), state["lines"])

    def test_a_shortfall_in_the_hold_is_a_warning(self):
        win = self.win
        win._inventory = [hold_gamma(20), hold_workers(12)]
        win._ps_workers_used = 0                                    # 10 free + 12 added cover the 20 expanders
        texts = [text for kind, text in sxp.panel_state(win)["lines"] if kind == "warn"]
        self.assertEqual(texts, ["Hold has only 20 expanders.", "Hold has only 12 workers."])
        win._inventory = [hold_gamma(), hold_workers(0)]
        win._ps_station_cargo = [workers_row(100)]
        texts = [text for kind, text in sxp.panel_state(win)["lines"] if kind == "warn"]
        self.assertEqual(texts, ["No workers in your hold."])

    def test_bound_expanders_and_pinned_workers_are_named(self):
        win = self.win
        win._inventory = [hold_gamma(), hold_gamma(4, bound=True, uid="9"), hold_workers(), hold_workers(3, pinned=True)]
        texts = [text for kind, text in sxp.panel_state(win)["lines"] if kind == "warn"]
        self.assertEqual(texts, ["4 bound stay in your hold.", "3 pinned stay in your hold."])

    def test_too_few_free_workers_is_the_first_warning(self):
        win = self.win
        win._inventory = [hold_gamma(), hold_workers(20)]
        win._ps_workers_used = 4
        warns = [text for kind, text in sxp.panel_state(win)["lines"] if kind == "warn"]
        self.assertEqual(warns[0], "Workers for only 26 of 50.")
        self.assertEqual(len(warns), 2)

    def test_expanders_already_in_the_station_are_mentioned(self):
        win = self.win
        win._inventory = [hold_gamma(20), hold_workers()]
        win._ps_station_cargo.append(gamma_row(30))
        lines = sxp.panel_state(win)["lines"]
        self.assertIn(("dim", "+ 30 in storage, to fit too"), lines)

    def test_with_only_stored_expanders_there_is_no_row_for_expanders_from_the_hold(self):
        win = self.win
        win._inventory = [hold_workers()]
        win._ps_station_cargo.append(gamma_row(30))
        state = sxp.panel_state(win)
        self.assertTrue(state["enabled"])
        self.assertEqual(state["label"], "ADD 0 GAMMA + 50 WORKERS")
        self.assertEqual(state["lines"][:2], [("row", "50 Workers"), ("dim", "+ 30 in storage, to fit too")])
        self.assertFalse([text for _, text in state["lines"] if text.startswith("0 ")])

    def test_the_lines_never_exceed_the_room_and_warnings_win(self):
        win = self.win
        win._inventory = [hold_gamma(20), hold_workers(20)]
        win._ps_station_cargo.append(gamma_row(30))
        win._ps_workers_used = 4
        lines = sxp.panel_state(win)["lines"]
        self.assertEqual(len(lines), sxp.MAX_LINES)
        self.assertEqual([kind for kind, _ in lines], ["row", "row", "warn", "warn"])

    def test_the_run_state_is_the_panel_state(self):
        win = self.win
        sxp.start(win)
        state = sxp.panel_state(win)
        self.assertEqual((state["enabled"], state["label"]), (False, "ADDING...  1 / 3"))
        self.assertEqual(state["lines"], [("row", "50 Station Expander Gamma"), ("dim", sxp.STEP_TEXT["workers"])])
        win.server_take_workers(50)
        sxp.advance(win)
        self.assertEqual(sxp.panel_state(win)["label"], "ADDING...  2 / 3")
        self.assertEqual(sxp.panel_state(win)["lines"][1], ("dim", sxp.STEP_TEXT["expanders"]))
        win.server_take_gammas(50)
        sxp.advance(win)
        self.assertEqual(sxp.panel_state(win)["label"], "ADDING...  3 / 3")
        self.assertEqual(sxp.panel_state(win)["lines"][1], ("dim", sxp.STEP_TEXT["equip"]))

    def test_a_run_with_fewer_steps_counts_them(self):
        win = self.win
        win._inventory = [hold_gamma()]
        sxp.start(win)
        self.assertEqual(sxp.panel_state(win)["label"], "ADDING...  1 / 2")

    def test_the_gear_upgrade_running_makes_both_wait(self):
        win = self.win
        win._sgu = {"cur": {"old_name": "A Mk I", "new_name": "A Mk II"}, "step": "deposit", "done": [], "total": 1}
        state = sxp.panel_state(win)
        self.assertEqual((state["enabled"], state["label"]), (False, "PLEASE WAIT"))
        win = self.win = Fake()
        sxp.start(win)
        state = sgu.panel_state(win)
        self.assertEqual((state["enabled"], state["label"]), (False, "PLEASE WAIT"))
        self.assertEqual(state["lines"], [("dim", "The station expansion is running.")])


class SectionTests(unittest.TestCase):
    def test_the_band_is_split_into_the_gear_part_and_the_expansion_part(self):
        band = pygame.Rect(408, 367, 464, 112)
        left, right = sgu.columns(band, lambda value: value)
        self.assertEqual(left, pygame.Rect(408, 367, 248, 112))
        self.assertEqual(right, pygame.Rect(668, 367, 204, 112))
        self.assertEqual(right.right, band.right)
        left, right = sgu.columns(band, lambda value: value // 2)
        self.assertEqual(right.left - left.right, 6)
        self.assertEqual(right.right, band.right)

    def test_the_expansion_area_is_the_right_column_once_the_dialog_is_drawn(self):
        win = SimpleNamespace(_s=lambda value: value, _ps_prefab_dialog={
            "header_rect": pygame.Rect(408, 245, 464, 30), "load_rect": pygame.Rect(640, 505, 232, 32)})
        self.assertEqual(sgu._expansion_area(win), pygame.Rect(668, 367, 204, 112))
        win._ps_prefab_dialog = {"header_rect": None}
        self.assertIsNone(sgu._expansion_area(win))
        win._ps_prefab_dialog = None
        self.assertIsNone(sgu._expansion_area(win))
        win._ps_prefab_dialog = {"header_rect": pygame.Rect(0, 0, 100, 30), "load_rect": pygame.Rect(0, 100, 50, 32)}
        self.assertIsNone(sgu._expansion_area(win))                # no room


class WrapperTests(SetUp):
    def make_class(self):
        class Window(Fake):
            def set_ps_station_cargo(self, data):
                self.pushed = getattr(self, "pushed", []) + [data]
                return "game result"

            def _draw_ps_prefab_dialog(self, ctx):
                self.drawn = getattr(self, "drawn", 0) + 1

        return Window

    def test_the_cargo_push_runs_the_game_first_then_moves_the_run_on(self):
        cls = self.make_class()
        sgu.install(cls)
        win = cls()
        sxp.start(win)
        win.server_take_workers(50)
        self.assertEqual(win.set_ps_station_cargo({"cargo": []}), "game result")
        self.assertEqual(win.pushed, [{"cargo": []}])
        self.assertEqual(win.commands(), ["PS_TRANSFER_TO_STATION", "PS_TRANSFER_TO_STATION"])

    def test_a_failure_in_the_run_switches_only_this_button_off_and_the_game_still_answers(self):
        cls = self.make_class()
        sgu.install(cls)
        win = cls()
        sxp.start(win)
        with mock.patch.object(sxp, "advance", side_effect=RuntimeError("boom")):
            with self.assertLogs("station_expansion", level="ERROR"):
                self.assertEqual(win.set_ps_station_cargo({}), "game result")
        self.assertFalse(sxp.enabled())
        self.assertTrue(sgu.enabled())
        self.assertFalse(sxp.running(win))
        self.assertIn("stopped after an error", last(win))
        count = len(win.sent)
        win.set_ps_station_cargo({})
        self.assertEqual(len(win.sent), count)

    def test_a_failure_in_the_gear_upgrade_leaves_this_button_working(self):
        cls = self.make_class()
        sgu.install(cls)
        win = cls()
        sxp.start(win)
        win.server_take_workers(50)
        with mock.patch.object(sgu, "advance", side_effect=RuntimeError("boom")):
            with self.assertLogs("station_gear_upgrade", level="ERROR"):
                win.set_ps_station_cargo({})
        self.assertFalse(sgu.enabled())
        self.assertTrue(sxp.enabled())
        self.assertEqual(win.commands(), ["PS_TRANSFER_TO_STATION", "PS_TRANSFER_TO_STATION"])   # it still moved on

    def test_the_dialog_draw_runs_the_game_then_both_sections(self):
        cls = self.make_class()
        sgu.install(cls)
        win = cls()
        with mock.patch.object(sgu, "draw_section") as gear, mock.patch.object(sxp, "draw_section") as ours:
            win._draw_ps_prefab_dialog("ctx")
        self.assertEqual(win.drawn, 1)
        gear.assert_called_once_with(win, "ctx")
        ours.assert_called_once_with(win, "ctx", None)             # no band yet: the dialog has not been laid out

    def test_a_failure_in_the_draw_switches_only_this_button_off_and_the_game_dialog_stays(self):
        cls = self.make_class()
        sgu.install(cls)
        win = cls()
        with mock.patch.object(sgu, "draw_section") as gear:
            with mock.patch.object(sxp, "draw_section", side_effect=RuntimeError("boom")):
                with self.assertLogs("station_expansion", level="ERROR"):
                    win._draw_ps_prefab_dialog("ctx")
        gear.assert_called_once_with(win, "ctx")
        self.assertEqual(win.drawn, 1)
        self.assertFalse(sxp.enabled())
        self.assertTrue(sgu.enabled())

    def test_the_environment_switch_leaves_the_game_alone(self):
        cls = self.make_class()
        sgu.install(cls)
        win = cls()
        sxp.start(win)
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_NO_STATION_EXPANSION": "1"}):
            with mock.patch.object(sxp, "draw_section") as ours, mock.patch.object(sxp, "advance") as moves:
                win._draw_ps_prefab_dialog("ctx")
                win.set_ps_station_cargo({})
        ours.assert_not_called()
        moves.assert_not_called()
        self.assertEqual((win.drawn, len(win.pushed)), (1, 1))


class ProtocolHookTests(SetUp):
    def protocol(self):
        class Protocol:
            handled = []

            def _on_ps_equip_module_result(self, payload):
                """the game's"""
                Protocol.handled.append(payload)
                return "game"

        Protocol.handled = []
        return Protocol

    def fitting_window(self):
        win = Fake()
        sxp.start(win)
        win.server_take_workers(50)
        sxp.advance(win)
        win.server_take_gammas(50)
        sxp.advance(win)
        return win

    def test_a_refusal_reaches_the_run_and_the_game_still_handles_it(self):
        cls = self.protocol()
        sgu.install_protocol(cls)
        proto = cls()
        proto._solar_window = self.fitting_window()
        payload = json.dumps({"ok": False, "module_type": GAMMA, "reason": "No free workers."})
        self.assertEqual(proto._on_ps_equip_module_result(payload), "game")
        self.assertEqual(cls.handled, [payload])
        self.assertFalse(sxp.running(proto._solar_window))
        self.assertIn("No free workers", last(proto._solar_window))

    def test_a_grant_reaches_the_run(self):
        cls = self.protocol()
        sgu.install_protocol(cls)
        proto = cls()
        win = proto._solar_window = self.fitting_window()
        proto._on_ps_equip_module_result(json.dumps({"ok": True, "module_type": GAMMA, "count": 40, "reason": "r"}))
        win.server_fit(40)
        sxp.advance(win)
        self.assertFalse(sxp.running(win))
        self.assertIn("40 fitted", last(win))

    def test_answers_are_only_parsed_while_a_fit_is_awaited(self):
        cls = self.protocol()
        sgu.install_protocol(cls)
        proto = cls()
        proto._solar_window = Fake()
        with mock.patch.object(sgu.json, "loads") as loads:
            proto._on_ps_equip_module_result("{}")
            win = proto._solar_window = Fake()
            sxp.start(win)                                          # still depositing the workers
            proto._on_ps_equip_module_result("{}")
        loads.assert_not_called()

    def test_a_bad_payload_or_no_window_does_no_harm(self):
        cls = self.protocol()
        sgu.install_protocol(cls)
        proto = cls()
        proto._solar_window = self.fitting_window()
        self.assertEqual(proto._on_ps_equip_module_result("not json"), "game")
        self.assertTrue(sxp.running(proto._solar_window))
        self.assertTrue(sxp.enabled())
        proto._solar_window = None
        self.assertEqual(proto._on_ps_equip_module_result("{}"), "game")
        self.assertEqual(len(cls.handled), 2)

    def test_a_failure_switches_only_this_button_off_and_the_game_still_handles_the_answer(self):
        cls = self.protocol()
        sgu.install_protocol(cls)
        proto = cls()
        proto._solar_window = self.fitting_window()
        with mock.patch.object(sxp, "on_equip_result", side_effect=RuntimeError("boom")):
            with self.assertLogs("station_expansion", level="ERROR"):
                self.assertEqual(proto._on_ps_equip_module_result("{}"), "game")
        self.assertFalse(sxp.enabled())
        self.assertTrue(sgu.enabled())
        self.assertFalse(sxp.running(proto._solar_window))
        self.assertEqual(len(cls.handled), 1)

    def test_the_environment_switch(self):
        cls = self.protocol()
        sgu.install_protocol(cls)
        proto = cls()
        proto._solar_window = self.fitting_window()
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_NO_STATION_EXPANSION": "1"}):
            self.assertEqual(proto._on_ps_equip_module_result(json.dumps({"ok": False, "reason": "x"})), "game")
        self.assertTrue(sxp.running(proto._solar_window))

    def test_the_gear_upgrade_switched_off_does_not_keep_the_answer_from_this_button(self):
        cls = self.protocol()
        sgu.install_protocol(cls)
        proto = cls()
        proto._solar_window = self.fitting_window()
        with mock.patch.object(sgu, "_failed", True):
            self.assertFalse(sgu.enabled())
            proto._on_ps_equip_module_result(json.dumps({"ok": False, "module_type": GAMMA, "reason": "No free workers."}))
        self.assertFalse(sxp.running(proto._solar_window))
        self.assertIn("No free workers", last(proto._solar_window))
        self.assertEqual(len(cls.handled), 1)

    def test_resetting_for_tests_resets_both_buttons(self):
        sxp._failed = True
        sgu.reset_for_tests()
        self.assertTrue(sxp.enabled())

    def test_the_gear_upgrade_still_gets_its_own_answers(self):
        cls = self.protocol()
        sgu.install_protocol(cls)
        proto = cls()
        win = proto._solar_window = Fake()
        win._ps_station_cargo += [{"item_key": "item:station:" + BEAM2, "item_type": BEAM2, "display_name": "Beam Mk II",
                                   "quantity": 1, "row_id": 11, "equippable": True, "equip_singleton": True}]
        win._ps_equipped_module_counts = {BEAM2: 1}
        win._inventory = [{"item_class": "cargo_item", "item_category": "station", "item_type": BEAM3,
                           "display_name": "Beam Mk III", "cargo_key": "item:station:" + BEAM3 + ":9"}]
        sgu.start(win)
        win._ps_station_cargo.append({"item_key": "item:station:" + BEAM3, "item_type": BEAM3, "quantity": 1,
                                      "row_id": 31, "equippable": True, "equip_singleton": True})
        sgu.advance(win)
        proto._on_ps_equip_module_result(json.dumps({"ok": False, "module_type": BEAM3, "reason": "No free slot."}))
        self.assertFalse(sgu.running(win))


class EventTests(SetUp):
    def setUp(self):
        super().setUp()
        self.win._ps_prefab_dialog = {"station_id": 7, "sxp_rect": pygame.Rect(300, 200, 200, 30)}

    def click(self, x, y, button=1):
        return pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(x, y), button=button)

    def test_a_press_on_the_button_starts_the_run_and_the_game_never_sees_it(self):
        other = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a)
        kept = sgu.filter_events(self.win, [other, self.click(350, 210)])
        self.assertEqual(kept, [other])
        self.assertTrue(sxp.running(self.win))
        self.assertEqual(self.win.sent[0][1]["item_key"], "workers")

    def test_other_presses_pass_through_untouched(self):
        events = [self.click(10, 10), self.click(350, 210, button=3), self.click(350, 210, button=2),
                  pygame.event.Event(pygame.MOUSEBUTTONUP, pos=(350, 210), button=1),
                  pygame.event.Event(pygame.MOUSEMOTION, pos=(350, 210), rel=(0, 0), buttons=(0, 0, 0))]
        self.assertIs(sgu.filter_events(self.win, events), events)
        self.assertFalse(sxp.running(self.win))

    def test_a_dropdown_a_prompt_or_no_dialog_means_the_button_is_not_there(self):
        for change in ({"dropdown_open": True}, {"name_input": {"text": ""}}, {"delete_confirm": {"name": "x"}}):
            win = Fake()
            win._ps_prefab_dialog = {"station_id": 7, "sxp_rect": pygame.Rect(300, 200, 200, 30), **change}
            events = [self.click(350, 210)]
            self.assertIs(sgu.filter_events(win, events), events)
            self.assertFalse(sxp.running(win))
        win = Fake()
        win._ps_prefab_dialog = None
        events = [self.click(350, 210)]
        self.assertIs(sgu.filter_events(win, events), events)

    def test_every_frame_it_gives_up_on_a_step_that_took_too_long(self):
        sxp.start(self.win)
        self.clock.now += sxp.STEP_WAIT_S + 1
        sgu.filter_events(self.win, [])
        self.assertFalse(sxp.running(self.win))

    def test_a_run_goes_on_with_the_dialog_closed(self):
        sxp.start(self.win)
        self.win._ps_prefab_dialog = None
        self.win.server_take_workers(50)
        sgu.filter_events(self.win, [])
        self.assertEqual(self.win.commands(), ["PS_TRANSFER_TO_STATION", "PS_TRANSFER_TO_STATION"])

    def test_the_environment_switch_passes_everything_and_does_nothing(self):
        events = [self.click(350, 210)]
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_NO_STATION_EXPANSION": "1"}):
            self.assertIs(sgu.filter_events(self.win, events), events)
        self.assertFalse(sxp.running(self.win))

    def test_a_failure_switches_only_this_button_off_and_the_events_still_pass(self):
        sxp.start(self.win)
        events = [self.click(10, 10)]
        with mock.patch.object(sxp, "advance", side_effect=RuntimeError("boom")):
            with self.assertLogs("station_expansion", level="ERROR"):
                self.assertIs(sgu.filter_events(self.win, events), events)
        self.assertFalse(sxp.enabled())
        self.assertTrue(sgu.enabled())
        self.assertFalse(sxp.running(self.win))

    def test_the_gear_button_still_works_beside_it(self):
        win = Fake()
        win._ps_station_cargo += [{"item_key": "item:station:" + BEAM2, "item_type": BEAM2, "display_name": "Beam Mk II",
                                   "quantity": 1, "row_id": 11, "equippable": True, "equip_singleton": True}]
        win._ps_equipped_module_counts = {BEAM2: 1}
        win._inventory = [{"item_class": "cargo_item", "item_category": "station", "item_type": BEAM3,
                           "display_name": "Beam Mk III", "cargo_key": "item:station:" + BEAM3 + ":9"}]
        win._ps_prefab_dialog = {"station_id": 7, "sgu_rect": pygame.Rect(100, 200, 200, 30),
                                 "sxp_rect": pygame.Rect(320, 200, 200, 30)}
        self.assertEqual(sgu.filter_events(win, [self.click(150, 210)]), [])
        self.assertTrue(sgu.running(win))
        self.assertFalse(sxp.running(win))

    def test_the_gear_upgrade_switched_off_does_not_stop_this_button(self):
        sgu._switch_off.__globals__["_failed"] = True
        try:
            events = [self.click(350, 210)]
            self.assertEqual(sgu.filter_events(self.win, events), [])
            self.assertTrue(sxp.running(self.win))
        finally:
            sgu.reset_for_tests()


class RealDialogTests(SetUp):
    """The game's own prefab dialog on a real SolarSystemWindow, with both sections inside it."""

    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode((1280, 720))

    def window(self, scale=1.0, gear=True):
        from Client import SolarSystemWindow
        window = SolarSystemWindow.__new__(SolarSystemWindow)
        fake = Fake()
        window._lock = threading.RLock()
        window._uk = scale
        window._elapsed = 0.0
        window._label_cache = OrderedDict()
        window._hud_plate_cache = {}
        fonts = {}

        def font(size, bold=False, *args, **kwargs):
            if (size, bold) not in fonts:
                fonts[(size, bold)] = pygame.font.SysFont("consolas", max(6, round(size * scale)), bold=bold)
            return fonts[(size, bold)]

        window._F = window._inventory_font = window._instrument_font = font
        window._ps_prefab_can_load = True
        window._station_overlay = fake._station_overlay
        window._ps_station_cargo = fake._ps_station_cargo
        window._ps_equipped_module_counts = fake._ps_equipped_module_counts
        window._ps_workers_used = fake._ps_workers_used
        window._inventory = fake._inventory
        if gear:
            window._ps_station_cargo.append({"item_key": "item:station:" + BEAM2, "item_type": BEAM2,
                                             "display_name": "Station Beam Cannon Mk II", "quantity": 1, "row_id": 11,
                                             "equippable": True, "equip_singleton": True})
            window._ps_equipped_module_counts[BEAM2] = 1
            window._inventory.append({"item_class": "cargo_item", "item_category": "station", "item_type": BEAM3,
                                      "display_name": "Station Beam Cannon Mk III",
                                      "cargo_key": "item:station:" + BEAM3 + ":9"})
        window._ps_prefab_dialog = {
            "station_id": 7, "prefabs": [{"name": "dps", "item_count": 8}], "selected": None, "dropdown_open": False,
            "list_scroll": 0, "panel_rect": None, "close_rect": None, "header_rect": None, "row_rects": [],
            "delete_rects": [], "save_rect": None, "load_rect": None, "name_input": None, "delete_confirm": None}
        window.chat_log = lambda text, colour=None: None
        window.sent = []
        window._send_json = lambda cmd, payload: window.sent.append((cmd, payload))
        window._send_equip_json = window._send_json
        return window

    def draw(self, window, width=1280, height=720):
        from render_mixin import RenderFrame
        screen = pygame.Surface((width, height), pygame.SRCALPHA)
        screen.fill((6, 10, 20, 255))
        window._draw_ps_prefab_dialog(RenderFrame(screen, width, height, lambda *a: a, 1.0, {}, 1, 1, 1 / 60))
        return screen

    def test_the_two_buttons_sit_side_by_side_in_the_free_band_of_the_dialog(self):
        window = self.window()
        screen = self.draw(window)
        dialog = window._ps_prefab_dialog
        gear, ours = dialog["sgu_rect"], dialog["sxp_rect"]
        panel = pygame.Rect(390, 165, 500, 390)
        self.assertIsNotNone(gear)
        self.assertIsNotNone(ours)
        for button in (gear, ours):
            self.assertTrue(panel.contains(button))
            self.assertGreater(button.top, dialog["header_rect"].bottom + 80)
            self.assertLess(button.bottom, dialog["load_rect"].top - 20)
            self.assertFalse(button.colliderect(dialog["load_rect"]) or button.colliderect(dialog["save_rect"]))
        self.assertEqual(gear.top, ours.top)
        self.assertEqual(ours.left - gear.right, 12)
        self.assertEqual(gear.left, dialog["header_rect"].left)
        self.assertEqual(ours.right, dialog["header_rect"].right)
        self.assertEqual(screen.get_at(ours.move(3, 3).topleft)[:3], (20, 62, 45))       # an enabled button's fill

    def test_everything_the_sections_draw_stays_inside_the_dialog(self):
        window = self.window()
        quiet = self.window()
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_NO_GEAR_UPGRADE": "1", "STAR_EMPIRE_NO_STATION_EXPANSION": "1"}):
            before = self.draw(quiet)
        after = self.draw(window)
        panel = pygame.Rect(390, 165, 500, 390)
        changed = [(x, y) for x in range(0, 1280, 2) for y in range(0, 720, 2)
                   if before.get_at((x, y)) != after.get_at((x, y))]
        self.assertTrue(changed)
        self.assertTrue(all(panel.collidepoint(point) for point in changed), "drew outside the dialog")

    def test_the_text_of_each_column_stays_in_its_own_column(self):
        window = self.window()
        window._inventory = [hold_gamma(20), hold_workers(12), window._inventory[-1]]
        window._ps_station_cargo.append(gamma_row(30))
        quiet = self.window()
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_NO_GEAR_UPGRADE": "1", "STAR_EMPIRE_NO_STATION_EXPANSION": "1"}):
            before = self.draw(quiet)
        after = self.draw(window)
        dialog = window._ps_prefab_dialog
        gear, ours = dialog["sgu_rect"], dialog["sxp_rect"]
        gap = pygame.Rect(gear.right, gear.top - 4, ours.left - gear.right, 120)
        self.assertEqual(gap.width, 12)
        self.assertTrue(all(before.get_at((x, y)) == after.get_at((x, y)) for x in range(gap.left, gap.right)
                            for y in range(gap.top, gap.bottom)), "text crossed into the gap between the columns")

    def test_the_gear_title_row_keeps_its_hint_only_when_there_is_room_for_it(self):
        quiet = self.window()
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_NO_GEAR_UPGRADE": "1", "STAR_EMPIRE_NO_STATION_EXPANSION": "1"}):
            before = self.draw(quiet)

        def title_row_changes(window):
            after = self.draw(window)
            gear = window._ps_prefab_dialog["sgu_rect"]
            title_w = window._instrument_font(11, bold=True).size(sgu.TITLE)[0]
            row = range(gear.top - 20, gear.top - 7)
            right_of_title = range(gear.left + title_w + 4, gear.right)
            return [x for x in right_of_title if any(before.get_at((x, y)) != after.get_at((x, y)) for y in row)]

        self.assertEqual(title_row_changes(self.window()), [], "the hint was drawn over the title in the narrow column")
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_NO_STATION_EXPANSION": "1"}):
            self.assertTrue(title_row_changes(self.window()), "the hint is missing where there is room")

    def test_a_label_wider_than_its_column_is_cut_to_fit(self):
        window = self.window()
        screen = pygame.Surface((400, 200), pygame.SRCALPHA)
        screen.fill((6, 10, 20, 255))
        area = pygame.Rect(150, 20, 100, 112)
        state = {"label": "A VERY LONG BUTTON LABEL INDEED", "enabled": True, "lines": []}
        sgu.draw_panel(window, SimpleNamespace(screen=screen), area, "T", "", state, {}, "k")
        outside = [(x, y) for x in range(0, 400) for y in range(0, 200)
                   if not area.collidepoint((x, y)) and screen.get_at((x, y)) != (6, 10, 20, 255)]
        self.assertEqual(outside, [], "the label spilled out of its column")

    def test_the_button_label_fits_its_column(self):
        window = self.window()
        self.draw(window)
        ours = window._ps_prefab_dialog["sxp_rect"]
        label = window._instrument_font(12, bold=True).size("ADD 50 GAMMA + 50 WORKERS")[0]
        self.assertLessEqual(label, ours.width - 8)

    def test_with_this_button_off_the_gear_section_has_the_whole_band(self):
        window = self.window()
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_NO_STATION_EXPANSION": "1"}):
            self.draw(window)
        dialog = window._ps_prefab_dialog
        self.assertIsNone(dialog.get("sxp_rect"))
        self.assertEqual(dialog["sgu_rect"].width, dialog["header_rect"].width)

    def test_with_the_gear_upgrade_off_this_button_keeps_its_place(self):
        window = self.window()
        reference = self.window()
        self.draw(reference)
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_NO_GEAR_UPGRADE": "1"}):
            self.draw(window)
        dialog = window._ps_prefab_dialog
        self.assertIsNone(dialog.get("sgu_rect"))
        self.assertEqual(dialog["sxp_rect"], reference._ps_prefab_dialog["sxp_rect"])

    def test_nothing_to_add_draws_a_dead_button(self):
        window = self.window()
        window._inventory = [window._inventory[-1]]               # only the gear remains
        screen = self.draw(window)
        dialog = window._ps_prefab_dialog
        self.assertIsNone(dialog["sxp_rect"])
        self.assertIsNotNone(dialog["sgu_rect"])                   # the gear button is untouched
        enabled_window = self.window()
        enabled_screen = self.draw(enabled_window)
        probe = enabled_window._ps_prefab_dialog["sxp_rect"].move(0, 6).topleft
        self.assertNotEqual(screen.get_at(probe)[:3], enabled_screen.get_at(probe)[:3])

    def test_no_section_over_the_dropdown_a_prompt_or_a_closed_dialog(self):
        for change in ({"dropdown_open": True}, {"name_input": {"text": ""}}):
            window = self.window()
            window._ps_prefab_dialog.update(change)
            self.draw(window)
            self.assertIsNone(window._ps_prefab_dialog["sxp_rect"])
            self.assertIsNone(window._ps_prefab_dialog["sgu_rect"])
        window = self.window()
        window._ps_prefab_dialog = None
        self.draw(window)

    def test_the_dialog_is_the_games_own_without_the_sections(self):
        window = self.window()
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_NO_GEAR_UPGRADE": "1", "STAR_EMPIRE_NO_STATION_EXPANSION": "1"}):
            self.draw(window)
        self.assertIsNone(window._ps_prefab_dialog.get("sxp_rect"))
        self.assertIsNone(window._ps_prefab_dialog.get("sgu_rect"))

    def test_the_button_text_changes_with_a_running_run_and_the_gear_button_waits(self):
        window = self.window()
        idle = self.draw(window)
        ours, gear = window._ps_prefab_dialog["sxp_rect"], window._ps_prefab_dialog["sgu_rect"]
        sxp.start(window)
        busy = self.draw(window)
        self.assertIsNone(window._ps_prefab_dialog["sxp_rect"])
        self.assertIsNone(window._ps_prefab_dialog["sgu_rect"])     # one run at a time
        for button in (ours, gear):
            probe = pygame.Rect(button.x + 8, button.y + 4, button.width - 16, button.height - 8)
            self.assertNotEqual(pygame.image.tobytes(idle.subsurface(probe), "RGB"),
                                pygame.image.tobytes(busy.subsurface(probe), "RGB"))

    def test_at_one_and_a_half_times_the_size(self):
        window = self.window(scale=1.5)
        window._s = lambda value: int(round(value * 1.5))
        self.draw(window, 1920, 1080)
        dialog = window._ps_prefab_dialog
        ours = dialog["sxp_rect"]
        self.assertIsNotNone(ours)
        self.assertLess(ours.bottom, dialog["load_rect"].top)
        self.assertGreater(ours.top, dialog["header_rect"].bottom)
        self.assertEqual(ours.height, 45)
        self.assertEqual(ours.right, dialog["header_rect"].right)

    def test_a_click_on_the_drawn_button_starts_the_run_and_the_gear_button_still_starts_its_own(self):
        window = self.window()
        self.draw(window)
        click = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=window._ps_prefab_dialog["sxp_rect"].center, button=1)
        self.assertEqual(sgu.filter_events(window, [click]), [])
        self.assertEqual(window.sent, [("PS_TRANSFER_TO_STATION", {"station_id": 7, "item_key": "workers",
                                                                   "amount": 50})])
        sxp.reset_for_tests()
        window = self.window()
        self.draw(window)
        click = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=window._ps_prefab_dialog["sgu_rect"].center, button=1)
        self.assertEqual(sgu.filter_events(window, [click]), [])
        self.assertEqual(window.sent[0][1]["item_key"], "item:station:" + BEAM3)

    def test_a_click_between_the_buttons_goes_to_the_game(self):
        window = self.window()
        self.draw(window)
        gear, ours = window._ps_prefab_dialog["sgu_rect"], window._ps_prefab_dialog["sxp_rect"]
        click = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=((gear.right + ours.left) // 2, gear.centery), button=1)
        self.assertEqual(sgu.filter_events(window, [click]), [click])
        self.assertEqual(window.sent, [])

    def test_the_real_window_sends_the_games_own_command_lines(self):
        from Client import SolarSystemWindow
        win = SolarSystemWindow.__new__(SolarSystemWindow)
        lines = []
        win._send_fn = lines.append
        win._audio = SimpleNamespace(play_equip_click=lambda: lines.append("click"))
        win._json_cmd = SolarSystemWindow._json_cmd
        win._send_json("PS_TRANSFER_TO_STATION", {"station_id": 7, "item_key": "workers", "amount": 50})
        win._send_json("PS_TRANSFER_TO_STATION", {"station_id": 7, "item_key": GAMMA_KEY, "amount": 50})
        win._send_equip_json("PS_EQUIP_MODULE", {"station_id": 7, "row_id": 31, "module_type": GAMMA, "count": 50})
        self.assertEqual(lines[0], 'PS_TRANSFER_TO_STATION {"station_id":7,"item_key":"workers","amount":50}')
        self.assertEqual(lines[1], 'PS_TRANSFER_TO_STATION {"station_id":7,"item_key":"item:station:station_expander_gamma",'
                                   '"amount":50}')
        self.assertEqual(lines[2], "click")
        self.assertEqual(lines[3], 'PS_EQUIP_MODULE {"station_id":7,"row_id":31,"module_type":"station_expander_gamma",'
                                   '"count":50}')

    def test_the_real_classes_carry_the_hooks_this_button_rides_on(self):
        from Client import SolarSystemWindow, ProtocolMixin
        for name in ("set_ps_station_cargo", "_draw_ps_prefab_dialog"):
            self.assertTrue(hasattr(getattr(SolarSystemWindow, name), "__wrapped__"), name)
        self.assertTrue(getattr(ProtocolMixin.__dict__["_on_ps_equip_module_result"], "_station_gear_upgrade", False))


if __name__ == "__main__":
    unittest.main()

"""station_gear_upgrade: the UPGRADE GEAR button of the station prefab dialog - which swaps it makes, the order and
the waits of its commands, how it fails, and how it is drawn and hooked."""
import modloader
import json
import os
import threading
import unittest
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame

import station_gear_upgrade as sgu

BEAM2, BEAM3 = "station_beam_cannon_mk2", "station_beam_cannon_mk3"
SHIELD1, SHIELD3 = "station_shield_mk1", "station_shield_mk3"


def hold_row(item_type, name, qty=None, bound=False, uid="77"):
    """One item in the ship's hold, as the server lists it."""
    row = {"item_class": "cargo_item", "item_category": "station", "item_type": item_type, "display_name": name,
           "cargo_key": f"item:station:{item_type}:{uid}"}
    if qty:
        row["stack_quantity"] = qty
    if bound:
        row["bound"] = True
    return row


def station_row(item_type, name, row_id, qty=1, single=True):
    """One stack in the station's cargo, as the server lists it."""
    return {"item_key": f"item:station:{item_type}", "item_type": item_type, "display_name": name, "quantity": qty,
            "row_id": row_id, "equippable": True, "equip_singleton": single}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Fake:
    """The parts of SolarSystemWindow the upgrade touches, plus the station's side of the conversation: the
    ``server_*`` methods change the state the way the station's cargo push would report it."""

    def __init__(self):
        self.sent, self.log = [], []
        self._lock = threading.RLock()
        self._station_overlay = {"kind": "player_station", "data": {"attached_station_id": 7, "management_access": True}}
        self._ps_station_cargo = [station_row(BEAM2, "Station Beam Cannon Mk II", 11),
                                  station_row("hull_plate", "Hull Plate", 12, qty=40, single=False)]
        self._ps_equipped_module_counts = {BEAM2: 1}
        self._inventory = [hold_row(BEAM3, "Station Beam Cannon Mk III")]
        self._ps_prefab_dialog = {"station_id": 7}

    def _send_json(self, cmd, payload):
        self.sent.append((cmd, payload))

    def _send_equip_json(self, cmd, payload):
        self.sent.append((cmd, payload))

    def chat_log(self, text, colour=None):
        self.log.append(text)

    # the station's side
    def server_take(self, item_type, name, row_id):
        for row in self._ps_station_cargo:
            if row["item_type"] == item_type:
                row["quantity"] += 1
                break
        else:
            self._ps_station_cargo.append(station_row(item_type, name, row_id))
        for index, item in enumerate(self._inventory):
            if item["item_type"] == item_type:
                del self._inventory[index]
                break

    def server_fit(self, new, old=None):
        self._ps_equipped_module_counts[new] = self._ps_equipped_module_counts.get(new, 0) + 1
        if old:
            self._ps_equipped_module_counts.pop(old, None)

    def server_unfit(self, item_type):
        self._ps_equipped_module_counts.pop(item_type, None)

    def server_give_back(self, item_type):
        for row in list(self._ps_station_cargo):
            if row["item_type"] == item_type:
                row["quantity"] -= 1
                if row["quantity"] <= 0:
                    self._ps_station_cargo.remove(row)
                break

    def commands(self):
        return [cmd for cmd, _ in self.sent]


class PlanTests(unittest.TestCase):
    def station(self, *rows):
        return list(rows)

    def test_marks_are_read_from_the_module_type(self):
        self.assertEqual(sgu.tier_of("station_beam_cannon_mk3"), ("station_beam_cannon", 3))
        self.assertEqual(sgu.tier_of("station_shield_transference_mk2"), ("station_shield_transference", 2))
        self.assertEqual(sgu.tier_of("csw_layered_plating_mk3"), ("csw_layered_plating", 3))
        for untiered in ("station_expander_gamma", "metal_drill", "", None, "mk3", "station_mk"):
            self.assertIsNone(sgu.tier_of(untiered), untiered)

    def test_a_shield_is_not_a_shield_transference(self):
        self.assertNotEqual(sgu.tier_of("station_shield_mk3")[0], sgu.tier_of("station_shield_transference_mk3")[0])

    def test_the_transfer_key_is_the_cargo_key_without_its_unique_id(self):
        self.assertEqual(sgu.type_key("item:station:station_beam_cannon_mk3:99123", BEAM3), "item:station:" + BEAM3)
        self.assertEqual(sgu.type_key("item:station:" + BEAM3, BEAM3), "item:station:" + BEAM3)
        self.assertEqual(sgu.type_key(None, BEAM3), "item:station:" + BEAM3)
        self.assertEqual(sgu.type_key("item:station:something_else:5", BEAM3), "item:station:" + BEAM3)

    def test_the_label_shares_the_stem_when_it_can(self):
        swap = {"old_name": "Station Beam Cannon Mk II", "new_name": "Station Beam Cannon Mk III"}
        self.assertEqual(sgu.swap_label(swap), "Station Beam Cannon: Mk II -> Mk III")
        swap = {"old_name": "Old Thing", "new_name": "Station Beam Cannon Mk III"}
        self.assertEqual(sgu.swap_label(swap), "Old Thing -> Station Beam Cannon Mk III")

    def test_a_fitted_mark_with_a_higher_one_in_the_hold_is_swapped(self):
        win = Fake()
        swaps, notes = sgu.build_plan(win._ps_station_cargo, win._ps_equipped_module_counts, win._inventory)
        self.assertEqual(notes, [])
        self.assertEqual(len(swaps), 1)
        swap = swaps[0]
        self.assertEqual((swap["old_type"], swap["new_type"], swap["old_mark"], swap["new_mark"]), (BEAM2, BEAM3, 2, 3))
        self.assertEqual((swap["old_name"], swap["new_name"]),
                         ("Station Beam Cannon Mk II", "Station Beam Cannon Mk III"))
        self.assertEqual(swap["new_key"], "item:station:" + BEAM3)

    def test_the_highest_unbound_mark_wins(self):
        win = Fake()
        win._inventory = [hold_row("station_beam_cannon_mk1", "Beam Mk I"), hold_row(BEAM3, "Beam Mk III"),
                          hold_row("station_beam_cannon_mk4", "Beam Mk IV", bound=True),
                          hold_row("station_beam_cannon_mk9", "Beam Mk IX", bound=True)]
        swaps, notes = sgu.build_plan(win._ps_station_cargo, win._ps_equipped_module_counts, win._inventory)
        self.assertEqual([s["new_type"] for s in swaps], [BEAM3])
        self.assertEqual(notes, [])

    def test_with_two_usable_marks_the_highest_one_is_taken(self):
        win = Fake()
        win._ps_station_cargo = [station_row("station_beam_cannon_mk1", "Beam Mk I", 11)]
        win._ps_equipped_module_counts = {"station_beam_cannon_mk1": 1}
        win._inventory = [hold_row(BEAM2, "Beam Mk II"), hold_row(BEAM3, "Beam Mk III"), hold_row(BEAM2, "Beam Mk II")]
        swaps, _ = sgu.build_plan(win._ps_station_cargo, win._ps_equipped_module_counts, win._inventory)
        self.assertEqual([(s["old_mark"], s["new_mark"]) for s in swaps], [(1, 3)])

    def test_the_same_or_a_lower_mark_is_not_an_upgrade(self):
        win = Fake()
        win._inventory = [hold_row(BEAM2, "Station Beam Cannon Mk II"), hold_row("station_beam_cannon_mk1", "Mk I")]
        self.assertEqual(sgu.build_plan(win._ps_station_cargo, win._ps_equipped_module_counts, win._inventory),
                         ([], []))

    def test_a_mark_that_is_not_fitted_is_not_touched(self):
        win = Fake()
        win._ps_equipped_module_counts = {}
        self.assertEqual(sgu.build_plan(win._ps_station_cargo, win._ps_equipped_module_counts, win._inventory),
                         ([], []))

    def test_bound_gear_is_named_not_sent(self):
        win = Fake()
        win._inventory = [hold_row(BEAM3, "Station Beam Cannon Mk III", bound=True)]
        swaps, notes = sgu.build_plan(win._ps_station_cargo, win._ps_equipped_module_counts, win._inventory)
        self.assertEqual(swaps, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("Station Beam Cannon Mk III", notes[0])
        self.assertIn("bound", notes[0])

    def test_a_loose_copy_is_used_when_another_copy_is_bound(self):
        win = Fake()
        win._inventory = [hold_row(BEAM3, "Beam Mk III", bound=True, uid="1"), hold_row(BEAM3, "Beam Mk III", uid="2")]
        swaps, notes = sgu.build_plan(win._ps_station_cargo, win._ps_equipped_module_counts, win._inventory)
        self.assertEqual(len(swaps), 1)
        self.assertEqual(notes, [])

    def test_a_stacked_row_counts_and_a_module_the_server_does_not_call_single_slot_is_left_alone(self):
        win = Fake()
        win._inventory = [hold_row(BEAM3, "Beam Mk III", qty=3)]
        win._ps_station_cargo[0]["equip_singleton"] = False
        swaps, notes = sgu.build_plan(win._ps_station_cargo, win._ps_equipped_module_counts, win._inventory)
        self.assertEqual(swaps, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("single-slot", notes[0])

    def test_two_fitted_copies_are_not_single_slot(self):
        win = Fake()
        win._ps_equipped_module_counts = {BEAM2: 2}
        swaps, notes = sgu.build_plan(win._ps_station_cargo, win._ps_equipped_module_counts, win._inventory)
        self.assertEqual(swaps, [])
        self.assertEqual(len(notes), 1)

    def test_no_note_when_nothing_higher_is_carried(self):
        win = Fake()
        win._inventory = []
        win._ps_station_cargo[0]["equip_singleton"] = False
        self.assertEqual(sgu.build_plan(win._ps_station_cargo, win._ps_equipped_module_counts, win._inventory),
                         ([], []))

    def test_each_family_is_swapped_on_its_own(self):
        win = Fake()
        win._ps_station_cargo += [station_row(SHIELD1, "Station Shield Mk I", 13)]
        win._ps_equipped_module_counts = {BEAM2: 1, SHIELD1: 1}
        win._inventory = [hold_row(SHIELD3, "Station Shield Mk III"), hold_row(BEAM3, "Station Beam Cannon Mk III"),
                          hold_row("station_shield_transference_mk3", "Shield Transference Mk III")]
        swaps, _ = sgu.build_plan(win._ps_station_cargo, win._ps_equipped_module_counts, win._inventory)
        self.assertEqual([(s["old_type"], s["new_type"]) for s in swaps], [(BEAM2, BEAM3), (SHIELD1, SHIELD3)])

    def test_a_csw_only_module_never_swaps_with_an_ordinary_one(self):
        win = Fake()
        win._ps_station_cargo = [station_row("csw_layered_plating_mk2", "[CSW] Layered Plating Mk II", 20)]
        win._ps_equipped_module_counts = {"csw_layered_plating_mk2": 1}
        win._inventory = [hold_row("layered_plating_mk3", "Layered Plating Mk III")]
        self.assertEqual(sgu.build_plan(win._ps_station_cargo, win._ps_equipped_module_counts, win._inventory),
                         ([], []))

    def test_other_cargo_is_not_mistaken_for_station_gear(self):
        win = Fake()
        win._inventory = [{"item_class": "weapon", "item_category": "weapon", "item_type": BEAM3, "display_name": "x"},
                          {"item_class": "cargo_item", "item_category": "ship_module", "item_type": BEAM3,
                           "display_name": "y"}, "junk", None]
        self.assertEqual(sgu.build_plan(win._ps_station_cargo, win._ps_equipped_module_counts, win._inventory),
                         ([], []))

    def test_missing_or_odd_data_gives_no_plan(self):
        self.assertEqual(sgu.build_plan(None, None, None), ([], []))
        self.assertEqual(sgu.build_plan([None, "x", {}], {"station_beam_cannon_mk2": "bad", "x": 1}, [{}]), ([], []))


class SectionRectTests(unittest.TestCase):
    def test_the_band_is_what_the_game_leaves_between_its_help_text_and_its_status_line(self):
        dialog = {"header_rect": pygame.Rect(408, 245, 464, 30), "load_rect": pygame.Rect(640, 505, 232, 32)}
        self.assertEqual(sgu.section_rect(dialog, lambda value: value), pygame.Rect(408, 367, 464, 112))
        self.assertEqual(sgu.section_rect(dialog, lambda value: value // 2), pygame.Rect(408, 321, 464, 171))   # scaled

    def test_a_dialog_with_no_room_or_not_drawn_yet_has_no_band(self):
        self.assertIsNone(sgu.section_rect({"header_rect": pygame.Rect(0, 0, 100, 30),
                                            "load_rect": pygame.Rect(0, 100, 50, 32)}, lambda value: value))
        self.assertIsNone(sgu.section_rect({"header_rect": None, "load_rect": pygame.Rect(0, 400, 50, 32)},
                                           lambda value: value))
        self.assertIsNone(sgu.section_rect({"header_rect": pygame.Rect(0, 0, 100, 30), "load_rect": None},
                                           lambda value: value))
        self.assertIsNone(sgu.section_rect({}, lambda value: value))


class EvaluateTests(unittest.TestCase):
    def test_a_docked_station_with_a_plan(self):
        problem, swaps, _ = sgu.evaluate(Fake())
        self.assertIsNone(problem)
        self.assertEqual(len(swaps), 1)

    def test_no_dialog_no_overlay_another_station_a_jump_gate_and_no_management_each_say_why(self):
        win = Fake()
        win._ps_prefab_dialog = None
        self.assertIn("prefab dialog", sgu.evaluate(win)[0])
        win = Fake()
        win._station_overlay = None
        self.assertIn("not docked", sgu.evaluate(win)[0])
        win = Fake()
        win._station_overlay["data"]["attached_station_id"] = 8
        self.assertIn("not docked", sgu.evaluate(win)[0])
        win = Fake()
        win._station_overlay["kind"] = "npc_station"
        self.assertIn("not docked", sgu.evaluate(win)[0])
        win = Fake()
        win._station_overlay["data"]["jump_gate_key"] = "gate"
        self.assertIn("jump gate", sgu.evaluate(win)[0])
        win = Fake()
        win._station_overlay["data"]["management_access"] = False
        self.assertIn("cannot manage", sgu.evaluate(win)[0])

    def test_unknown_management_access_is_not_a_refusal(self):
        win = Fake()
        del win._station_overlay["data"]["management_access"]
        self.assertIsNone(sgu.evaluate(win)[0])


class RunTests(unittest.TestCase):
    def setUp(self):
        sgu.reset_for_tests()
        self.clock = Clock()
        patcher = mock.patch.object(sgu, "time", SimpleNamespace(monotonic=self.clock))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.win = Fake()

    def run_one_swap_to_the_equip_step(self):
        win = self.win
        self.assertTrue(sgu.start(win))
        win.server_take(BEAM3, "Station Beam Cannon Mk III", 31)
        sgu.advance(win)
        return win

    def test_the_whole_swap_in_order_and_each_step_waits_for_the_station(self):
        win = self.win
        self.assertTrue(sgu.start(win))
        self.assertEqual(win.sent, [("PS_TRANSFER_TO_STATION", {"station_id": 7, "item_key": "item:station:" + BEAM3,
                                                                  "amount": 1})])
        sgu.advance(win)                                            # the station has not answered: nothing more
        self.assertEqual(len(win.sent), 1)

        win.server_take(BEAM3, "Station Beam Cannon Mk III", 31)
        sgu.advance(win)
        self.assertEqual(win.sent[1], ("PS_EQUIP_MODULE", {"station_id": 7, "row_id": 31, "module_type": BEAM3,
                                                           "count": 1}))
        sgu.advance(win)
        self.assertEqual(len(win.sent), 2)                          # not fitted yet

        win.server_fit(BEAM3, old=BEAM2)                            # the server swaps the old mark out
        sgu.advance(win)
        self.assertEqual(win.sent[2], ("PS_TRANSFER_TO_SHIP", {"station_id": 7, "row_id": 11, "amount": 1}))
        sgu.advance(win)
        self.assertEqual(len(win.sent), 3)
        self.assertTrue(sgu.running(win))

        win.server_give_back(BEAM2)
        sgu.advance(win)
        self.assertFalse(sgu.running(win))
        self.assertEqual(win.commands(), ["PS_TRANSFER_TO_STATION", "PS_EQUIP_MODULE", "PS_TRANSFER_TO_SHIP"])
        self.assertTrue(any("done" in line and "back in your hold" in line for line in win.log), win.log)
        self.assertEqual(win.log[-1], "Station gear: upgraded 1 module in 0.0 s.")      # the test clock never moved

    def test_the_summary_says_where_the_time_went(self):
        win = self.win
        sgu.start(win)
        self.clock.now += 2.0
        win.server_take(BEAM3, "Station Beam Cannon Mk III", 31)
        sgu.advance(win)
        self.clock.now += 4.0
        win.server_fit(BEAM3, old=BEAM2)
        sgu.advance(win)
        self.clock.now += 3.5
        win.server_give_back(BEAM2)
        sgu.advance(win)
        self.assertEqual(win.log[-1], "Station gear: upgraded 1 module in 9.5 s (moving in 2.0 s, fitting 4.0 s, "
                                      "moving back 3.5 s).")

    def test_taking_the_old_mark_off_by_command_has_its_own_share_of_the_time(self):
        win = self.win
        sgu.start(win)
        self.clock.now += 1.0
        win.server_take(BEAM3, "Station Beam Cannon Mk III", 31)
        sgu.advance(win)
        self.clock.now += 1.0
        win.server_fit(BEAM3)                                       # both fitted: the server did not swap
        sgu.advance(win)
        self.clock.now += sgu.SWAP_GRACE_S                          # the grace is spent fitting, then the unequip goes
        sgu.advance(win)
        self.clock.now += 2.0
        win.server_unfit(BEAM2)
        sgu.advance(win)
        self.clock.now += 0.5
        win.server_give_back(BEAM2)
        sgu.advance(win)
        self.assertEqual(win.log[-1], "Station gear: upgraded 1 module in 6.0 s (moving in 1.0 s, fitting 2.5 s, "
                                      "taking off 2.0 s, moving back 0.5 s).")

    def test_the_times_of_several_swaps_add_up_per_step(self):
        win = self.win
        win._ps_station_cargo += [station_row(SHIELD1, "Station Shield Mk I", 13)]
        win._ps_equipped_module_counts = {BEAM2: 1, SHIELD1: 1}
        win._inventory = [hold_row(BEAM3, "Station Beam Cannon Mk III"), hold_row(SHIELD3, "Station Shield Mk III")]
        sgu.start(win)
        for new, old, name, row_id in ((BEAM3, BEAM2, "Station Beam Cannon Mk III", 31),
                                       (SHIELD3, SHIELD1, "Station Shield Mk III", 32)):
            self.clock.now += 1.0
            win.server_take(new, name, row_id)
            sgu.advance(win)
            self.clock.now += 2.0
            win.server_fit(new, old=old)
            sgu.advance(win)
            self.clock.now += 0.5
            win.server_give_back(old)
            sgu.advance(win)
        self.assertEqual(win.log[-1], "Station gear: upgraded 2 modules in 7.0 s (moving in 2.0 s, fitting 4.0 s, "
                                      "moving back 1.0 s).")

    def test_a_run_that_fails_does_not_claim_a_time(self):
        win = self.win
        sgu.start(win)
        self.clock.now += sgu.STEP_WAIT_S + 1
        sgu.advance(win)
        self.assertNotIn("upgraded", win.log[-1])

    def test_only_one_old_copy_comes_back_when_the_station_keeps_spares(self):
        win = self.win
        win._ps_station_cargo[0]["quantity"] = 4                    # one fitted, three spares in storage
        sgu.start(win)
        win.server_take(BEAM3, "Station Beam Cannon Mk III", 31)
        sgu.advance(win)
        win.server_fit(BEAM3, old=BEAM2)
        sgu.advance(win)
        self.assertEqual(win.sent[-1], ("PS_TRANSFER_TO_SHIP", {"station_id": 7, "row_id": 11, "amount": 1}))
        win.server_give_back(BEAM2)                                 # 4 -> 3: one came back
        sgu.advance(win)
        self.assertFalse(sgu.running(win))

    def test_several_swaps_run_one_after_the_other(self):
        win = self.win
        win._ps_station_cargo += [station_row(SHIELD1, "Station Shield Mk I", 13)]
        win._ps_equipped_module_counts = {BEAM2: 1, SHIELD1: 1}
        win._inventory = [hold_row(BEAM3, "Station Beam Cannon Mk III"), hold_row(SHIELD3, "Station Shield Mk III")]
        self.assertTrue(sgu.start(win))
        self.assertEqual(len(win.sent), 1)                          # only the first swap's deposit
        win.server_take(BEAM3, "Station Beam Cannon Mk III", 31)
        sgu.advance(win)
        win.server_fit(BEAM3, old=BEAM2)
        sgu.advance(win)
        win.server_give_back(BEAM2)
        sgu.advance(win)
        self.assertTrue(sgu.running(win))                           # the second swap starts right away
        self.assertEqual(win.sent[-1], ("PS_TRANSFER_TO_STATION", {"station_id": 7, "item_key": "item:station:" + SHIELD3,
                                                                     "amount": 1}))
        win.server_take(SHIELD3, "Station Shield Mk III", 32)
        sgu.advance(win)
        win.server_fit(SHIELD3, old=SHIELD1)
        sgu.advance(win)
        win.server_give_back(SHIELD1)
        sgu.advance(win)
        self.assertFalse(sgu.running(win))
        self.assertEqual(win.log[-1], "Station gear: upgraded 2 modules in 0.0 s.")
        self.assertEqual(win.commands().count("PS_EQUIP_MODULE"), 2)

    def test_when_the_server_does_not_swap_the_old_mark_comes_off_by_command_after_a_short_wait(self):
        win = self.run_one_swap_to_the_equip_step()
        win.server_fit(BEAM3)                                       # both fitted now
        sgu.advance(win)
        self.assertEqual(len(win.sent), 2)                          # give the server a moment to take it out itself
        self.clock.now += sgu.SWAP_GRACE_S - 0.1
        sgu.advance(win)
        self.assertEqual(len(win.sent), 2)
        self.clock.now += 0.2
        sgu.advance(win)
        self.assertEqual(win.sent[2], ("PS_UNEQUIP_MODULE", {"station_id": 7, "module_type": BEAM2, "count": 1}))
        sgu.advance(win)
        self.assertEqual(len(win.sent), 3)
        win._ps_equipped_module_counts.pop(BEAM2)
        sgu.advance(win)
        self.assertEqual(win.sent[3][0], "PS_TRANSFER_TO_SHIP")

    def test_an_old_mark_that_leaves_during_the_wait_needs_no_unequip(self):
        win = self.run_one_swap_to_the_equip_step()
        win.server_fit(BEAM3)
        sgu.advance(win)
        self.clock.now += 1.0
        win._ps_equipped_module_counts.pop(BEAM2)
        sgu.advance(win)
        self.assertEqual(win.commands(), ["PS_TRANSFER_TO_STATION", "PS_EQUIP_MODULE", "PS_TRANSFER_TO_SHIP"])

    def test_an_unfitted_new_gear_never_makes_the_old_one_come_off(self):
        win = self.run_one_swap_to_the_equip_step()
        for _ in range(3):
            self.clock.now += sgu.SWAP_GRACE_S + 1.0            # well past the grace wait, short of the step's timeout
            sgu.advance(win)
            self.assertEqual(win.commands(), ["PS_TRANSFER_TO_STATION", "PS_EQUIP_MODULE"])
        self.assertTrue(sgu.running(win))
        self.assertLess(self.clock.now - win._sgu["t"], sgu.STEP_WAIT_S)

    def test_each_step_has_its_own_time_limit(self):
        win = self.win
        sgu.start(win)
        self.clock.now += sgu.STEP_WAIT_S - 1.0                 # the deposit lands late
        win.server_take(BEAM3, "Station Beam Cannon Mk III", 31)
        sgu.advance(win)
        self.assertEqual(win.commands(), ["PS_TRANSFER_TO_STATION", "PS_EQUIP_MODULE"])
        self.clock.now += sgu.STEP_WAIT_S - 1.0                 # long after the run began, not after the fit was sent
        sgu.advance(win)
        self.assertTrue(sgu.running(win))
        self.clock.now += 2.0
        sgu.advance(win)
        self.assertFalse(sgu.running(win))

    def test_a_new_gear_that_is_fitted_is_never_sent_back(self):
        win = self.run_one_swap_to_the_equip_step()
        win._ps_equipped_module_counts[BEAM3] = 1               # it is fitted after all: the answer was late or wrong
        count = len(win.sent)
        sgu.on_equip_result(win, {"ok": False, "module_type": BEAM3, "reason": "stale"})
        self.assertEqual(len(win.sent), count)
        self.assertIn("in the station's storage", win.log[-1])

    def test_a_station_that_never_takes_the_new_gear_stops_the_run_with_nothing_moved(self):
        win = self.win
        sgu.start(win)
        self.clock.now += sgu.STEP_WAIT_S - 0.5
        sgu.advance(win)
        self.assertTrue(sgu.running(win))
        self.clock.now += 1.0
        sgu.advance(win)
        self.assertFalse(sgu.running(win))
        self.assertEqual(win.commands(), ["PS_TRANSFER_TO_STATION"])
        self.assertIn("storage full", win.log[-1])
        self.assertIn("Nothing was moved", win.log[-1])

    def test_a_refused_fit_sends_the_new_gear_back_and_gives_the_servers_reason(self):
        win = self.run_one_swap_to_the_equip_step()
        sgu.on_equip_result(win, {"ok": False, "module_type": BEAM3, "reason": "Not enough free workers."})
        self.assertFalse(sgu.running(win))
        self.assertEqual(win.sent[-1], ("PS_TRANSFER_TO_SHIP", {"station_id": 7, "row_id": 31, "amount": 1}))
        self.assertIn("Not enough free workers", win.log[-1])
        self.assertIn("sent back to your hold", win.log[-1])
        self.assertNotIn("..", win.log[-1])

    def test_a_fit_that_is_never_confirmed_is_treated_like_a_refusal(self):
        win = self.run_one_swap_to_the_equip_step()
        self.clock.now += sgu.STEP_WAIT_S + 1
        sgu.advance(win)
        self.assertFalse(sgu.running(win))
        self.assertEqual(win.sent[-1][0], "PS_TRANSFER_TO_SHIP")
        self.assertIn("Log tab", win.log[-1])

    def test_results_that_are_not_a_refusal_of_this_fit_are_ignored(self):
        win = self.run_one_swap_to_the_equip_step()
        sgu.on_equip_result(win, {"ok": True, "module_type": BEAM3})
        sgu.on_equip_result(win, {"ok": False, "module_type": "station_energy_mk3", "reason": "other"})
        sgu.on_equip_result(win, "not a dict")
        sgu.on_equip_result(win, None)
        self.assertTrue(sgu.running(win))
        self.assertEqual(len(win.sent), 2)

    def test_a_refusal_outside_the_fit_step_is_ignored(self):
        win = self.win
        sgu.start(win)
        sgu.on_equip_result(win, {"ok": False, "reason": "x"})
        self.assertTrue(sgu.running(win))
        sgu.on_equip_result(Fake(), {"ok": False, "reason": "x"})     # nothing running at all

    def test_a_refusal_without_a_module_name_still_counts(self):
        win = self.run_one_swap_to_the_equip_step()
        sgu.on_equip_result(win, {"ok": False})
        self.assertFalse(sgu.running(win))
        self.assertIn("refused to fit", win.log[-1])

    def test_an_old_mark_that_does_not_come_back_is_reported_where_it_is(self):
        win = self.run_one_swap_to_the_equip_step()
        win.server_fit(BEAM3, old=BEAM2)
        sgu.advance(win)
        self.clock.now += sgu.STEP_WAIT_S + 1
        sgu.advance(win)
        self.assertFalse(sgu.running(win))
        self.assertIn("storage", win.log[-1])
        self.assertIn("Storage tab", win.log[-1])

    def test_an_old_mark_that_will_not_come_off_is_reported(self):
        win = self.run_one_swap_to_the_equip_step()
        win.server_fit(BEAM3)
        sgu.advance(win)
        self.clock.now += sgu.SWAP_GRACE_S + 0.1
        sgu.advance(win)
        self.clock.now += sgu.STEP_WAIT_S + 1
        sgu.advance(win)
        self.assertFalse(sgu.running(win))
        self.assertIn("Both marks are fitted", win.log[-1])

    def test_a_failure_says_what_was_done_and_what_was_not_started(self):
        win = self.win
        win._ps_station_cargo += [station_row(SHIELD1, "Station Shield Mk I", 13)]
        win._ps_equipped_module_counts = {BEAM2: 1, SHIELD1: 1}
        win._inventory = [hold_row(BEAM3, "Station Beam Cannon Mk III"), hold_row(SHIELD3, "Station Shield Mk III")]
        sgu.start(win)
        win.server_take(BEAM3, "Station Beam Cannon Mk III", 31)
        sgu.advance(win)
        win.server_fit(BEAM3, old=BEAM2)
        sgu.advance(win)
        win.server_give_back(BEAM2)
        sgu.advance(win)
        self.clock.now += sgu.STEP_WAIT_S + 1
        sgu.advance(win)                                            # the second deposit never arrives
        self.assertFalse(sgu.running(win))
        self.assertIn("Station Shield", win.log[-1])
        self.assertIn("Done: Station Beam Cannon: Mk II -> Mk III", win.log[-1])
        win2 = Fake()
        win2._ps_station_cargo += [station_row(SHIELD1, "Station Shield Mk I", 13)]
        win2._ps_equipped_module_counts = {BEAM2: 1, SHIELD1: 1}
        win2._inventory = [hold_row(BEAM3, "Beam Mk III"), hold_row(SHIELD3, "Shield Mk III")]
        sgu.start(win2)
        self.clock.now += sgu.STEP_WAIT_S + 1
        sgu.advance(win2)
        self.assertIn("1 more not started", win2.log[-1])

    def test_leaving_the_station_stops_the_run_and_sends_nothing_more(self):
        win = self.run_one_swap_to_the_equip_step()
        win._station_overlay = None
        count = len(win.sent)
        sgu.advance(win)
        self.assertFalse(sgu.running(win))
        self.assertEqual(len(win.sent), count)
        self.assertIn("no longer docked", win.log[-1])
        self.assertIn("in the station's storage", win.log[-1])

    def test_docking_at_another_station_stops_it_too(self):
        win = self.win
        sgu.start(win)
        win._station_overlay["data"]["attached_station_id"] = 9
        sgu.advance(win)
        self.assertFalse(sgu.running(win))

    def test_the_station_forgetting_the_new_rows_stops_the_run_cleanly(self):
        win = self.run_one_swap_to_the_equip_step()
        win.server_fit(BEAM3, old=BEAM2)
        win._ps_station_cargo = [r for r in win._ps_station_cargo if r["item_type"] != BEAM2]
        sgu.advance(win)
        self.assertFalse(sgu.running(win))
        self.assertIn("no row for the old gear", win.log[-1])

        win = self.win = Fake()
        sgu.start(win)
        win._ps_station_cargo.append(station_row(BEAM3, "Beam Mk III", None))
        sgu.advance(win)
        self.assertFalse(sgu.running(win))
        self.assertIn("no row for the new gear", win.log[-1])

    def test_a_second_press_does_not_start_a_second_run(self):
        win = self.win
        self.assertTrue(sgu.start(win))
        self.assertFalse(sgu.start(win))
        self.assertEqual(len(win.sent), 1)

    def test_nothing_to_upgrade_and_refusals_say_so_and_send_nothing(self):
        win = Fake()
        win._inventory = []
        self.assertFalse(sgu.start(win))
        self.assertIn("Nothing to upgrade", win.log[-1])
        win = Fake()
        win._station_overlay = None
        self.assertFalse(sgu.start(win))
        self.assertIn("not docked", win.log[-1])
        self.assertEqual(win.sent, [])

    def test_the_run_state_is_the_panel_state(self):
        win = self.win
        before = sgu.panel_state(win)
        self.assertTrue(before["enabled"])
        self.assertEqual(before["label"], "UPGRADE GEAR  (1)")
        sgu.start(win)
        during = sgu.panel_state(win)
        self.assertFalse(during["enabled"])
        self.assertEqual(during["label"], "UPGRADING...  1 / 1")
        self.assertEqual(during["lines"][0], ("row", "Station Beam Cannon: Mk II -> Mk III"))
        self.assertEqual(during["lines"][1][1], sgu.STEP_TEXT["deposit"])

    def test_stopping_after_an_error_says_so_and_clears_the_run(self):
        win = self.win
        sgu.start(win)
        sgu.stop_after_error(win)
        self.assertFalse(sgu.running(win))
        self.assertIn("stopped after an error", win.log[-1])
        sgu.stop_after_error(win)                                   # nothing running: silent
        sgu.stop_after_error(None)


class PanelStateTests(unittest.TestCase):
    def test_what_each_situation_shows(self):
        win = Fake()
        state = sgu.panel_state(win)
        self.assertEqual((state["enabled"], state["label"]), (True, "UPGRADE GEAR  (1)"))
        self.assertEqual(state["lines"], [("row", "Station Beam Cannon: Mk II -> Mk III")])

        win._inventory = []
        state = sgu.panel_state(win)
        self.assertEqual((state["enabled"], state["label"]), (False, "NOTHING TO UPGRADE"))
        self.assertEqual(state["lines"][0][0], "dim")

        win = Fake()
        win._inventory = [hold_row(BEAM3, "Station Beam Cannon Mk III", bound=True)]
        state = sgu.panel_state(win)
        self.assertFalse(state["enabled"])
        self.assertEqual([kind for kind, _ in state["lines"]], ["dim", "warn"])

        win = Fake()
        win._station_overlay = None
        state = sgu.panel_state(win)
        self.assertEqual((state["enabled"], state["lines"]), (False, [("dim", "You are not docked at this station.")]))

    def test_a_long_list_is_cut_with_a_count(self):
        win = Fake()
        names = [("station_shield_mk1", "Shield"), ("station_energy_mk1", "Energy"), ("station_mag_cannon_mk1", "Mag"),
                 ("station_sensor_mk1", "Sensor")]
        win._ps_station_cargo = [station_row(BEAM2, "Beam Mk II", 11)]
        win._ps_equipped_module_counts = {BEAM2: 1}
        win._inventory = [hold_row(BEAM3, "Beam Mk III")]
        for index, (item_type, name) in enumerate(names):
            win._ps_station_cargo.append(station_row(item_type, f"Station {name} Mk I", 20 + index))
            win._ps_equipped_module_counts[item_type] = 1
            win._inventory.append(hold_row(item_type.replace("mk1", "mk3"), f"Station {name} Mk III"))
        state = sgu.panel_state(win)
        self.assertEqual(state["label"], "UPGRADE GEAR  (5)")
        self.assertEqual([kind for kind, _ in state["lines"]], ["row"] * (sgu.MAX_LINES - 1) + ["dim"])
        self.assertEqual(state["lines"][-1][1], "+ 2 more")

    def test_the_list_uses_every_line_when_it_just_fits(self):
        win = Fake()
        win._ps_station_cargo, win._ps_equipped_module_counts, win._inventory = [], {}, []
        for index, family in enumerate(("station_beam_cannon", "station_shield", "station_energy", "station_sensor")):
            win._ps_station_cargo.append(station_row(family + "_mk1", f"{family} Mk I", 10 + index))
            win._ps_equipped_module_counts[family + "_mk1"] = 1
            win._inventory.append(hold_row(family + "_mk3", f"{family} Mk III"))
        state = sgu.panel_state(win)
        self.assertEqual([kind for kind, _ in state["lines"]], ["row"] * 4)

    def test_a_note_is_never_pushed_out_by_a_long_list(self):
        win = Fake()
        win._ps_station_cargo, win._ps_equipped_module_counts, win._inventory = [], {}, []
        for index, family in enumerate(("station_beam_cannon", "station_shield", "station_energy", "station_sensor",
                                        "station_mag_cannon")):
            win._ps_station_cargo.append(station_row(family + "_mk1", f"{family} Mk I", 10 + index))
            win._ps_equipped_module_counts[family + "_mk1"] = 1
            win._inventory.append(hold_row(family + "_mk3", f"{family} Mk III"))
        win._ps_station_cargo.append(station_row("station_point_defense_laser_mk1", "PD Laser Mk I", 30))
        win._ps_equipped_module_counts["station_point_defense_laser_mk1"] = 1
        win._inventory.append(hold_row("station_point_defense_laser_mk3", "PD Laser Mk III", bound=True))
        state = sgu.panel_state(win)
        self.assertEqual(len(state["lines"]), sgu.MAX_LINES)
        self.assertEqual([kind for kind, _ in state["lines"]], ["row", "row", "dim", "warn"])
        self.assertEqual(state["lines"][2][1], "+ 3 more")
        self.assertIn("bound", state["lines"][3][1])


class WrapperTests(unittest.TestCase):
    def setUp(self):
        sgu.reset_for_tests()
        self.clock = Clock()
        patcher = mock.patch.object(sgu, "time", SimpleNamespace(monotonic=self.clock))
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_class(self):
        class Window(Fake):
            def set_ps_station_cargo(self, data):
                self.pushed = getattr(self, "pushed", []) + [data]
                return "game result"

            def _draw_ps_prefab_dialog(self, ctx):
                self.drawn = getattr(self, "drawn", 0) + 1

        return Window

    def test_install_is_idempotent_and_refuses_a_class_without_the_methods(self):
        cls = self.make_class()
        self.assertTrue(sgu.install(cls))
        self.assertFalse(sgu.install(cls))
        self.assertTrue(hasattr(cls.set_ps_station_cargo, "__wrapped__"))
        self.assertTrue(hasattr(cls._draw_ps_prefab_dialog, "__wrapped__"))

        class Bare:
            pass

        self.assertFalse(sgu.install(Bare))
        self.assertFalse(Bare.__dict__.get("_station_gear_upgrade_installed", False))

    def test_the_cargo_push_runs_the_game_first_then_moves_the_run_on(self):
        cls = self.make_class()
        sgu.install(cls)
        win = cls()
        sgu.start(win)
        win.server_take(BEAM3, "Station Beam Cannon Mk III", 31)
        self.assertEqual(win.set_ps_station_cargo({"cargo": []}), "game result")
        self.assertEqual(win.pushed, [{"cargo": []}])
        self.assertEqual(win.commands(), ["PS_TRANSFER_TO_STATION", "PS_EQUIP_MODULE"])

    def test_a_failure_in_the_run_switches_it_off_ends_the_run_and_the_game_still_answers(self):
        cls = self.make_class()
        sgu.install(cls)
        win = cls()
        sgu.start(win)
        with mock.patch.object(sgu, "advance", side_effect=RuntimeError("boom")):
            with self.assertLogs("station_gear_upgrade", level="ERROR"):
                self.assertEqual(win.set_ps_station_cargo({}), "game result")
        self.assertFalse(sgu.enabled())
        self.assertFalse(sgu.running(win))
        self.assertIn("stopped after an error", win.log[-1])
        count = len(win.sent)
        win.set_ps_station_cargo({})
        self.assertEqual(len(win.sent), count)

    def test_the_dialog_draw_runs_the_game_then_ours(self):
        cls = self.make_class()
        sgu.install(cls)
        win = cls()
        with mock.patch.object(sgu, "draw_section") as ours:
            win._draw_ps_prefab_dialog("ctx")
        self.assertEqual(win.drawn, 1)
        ours.assert_called_once_with(win, "ctx")

    def test_a_failure_in_the_draw_switches_it_off_and_the_game_dialog_stays(self):
        cls = self.make_class()
        sgu.install(cls)
        win = cls()
        with mock.patch.object(sgu, "draw_section", side_effect=RuntimeError("boom")):
            with self.assertLogs("station_gear_upgrade", level="ERROR"):
                win._draw_ps_prefab_dialog("ctx")
        self.assertEqual(win.drawn, 1)
        self.assertFalse(sgu.enabled())

    def test_the_environment_switch_leaves_the_game_alone(self):
        cls = self.make_class()
        sgu.install(cls)
        win = cls()
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_NO_GEAR_UPGRADE": "1"}):
            with mock.patch.object(sgu, "draw_section") as ours, mock.patch.object(sgu, "advance") as moves:
                win._draw_ps_prefab_dialog("ctx")
                win.set_ps_station_cargo({})
        ours.assert_not_called()
        moves.assert_not_called()
        self.assertEqual((win.drawn, len(win.pushed)), (1, 1))


class ProtocolHookTests(unittest.TestCase):
    def setUp(self):
        sgu.reset_for_tests()
        self.clock = Clock()
        patcher = mock.patch.object(sgu, "time", SimpleNamespace(monotonic=self.clock))
        patcher.start()
        self.addCleanup(patcher.stop)

    def protocol(self):
        class Protocol:
            handled = []

            def _on_ps_equip_module_result(self, payload):
                """the game's"""
                Protocol.handled.append(payload)
                return "game"

        Protocol.handled = []
        return Protocol

    def waiting_window(self):
        win = Fake()
        sgu.start(win)
        win.server_take(BEAM3, "Station Beam Cannon Mk III", 31)
        sgu.advance(win)
        return win

    def test_a_refusal_reaches_the_run_and_the_game_still_handles_it(self):
        cls = self.protocol()
        self.assertTrue(sgu.install_protocol(cls))
        self.assertFalse(sgu.install_protocol(cls))
        self.assertEqual(cls._on_ps_equip_module_result.__doc__, "the game's")
        proto = cls()
        proto._solar_window = self.waiting_window()
        payload = json.dumps({"ok": False, "module_type": BEAM3, "reason": "No free slot."})
        self.assertEqual(proto._on_ps_equip_module_result(payload), "game")
        self.assertEqual(cls.handled, [payload])
        self.assertFalse(sgu.running(proto._solar_window))
        self.assertIn("No free slot", proto._solar_window.log[-1])

    def test_answers_are_only_parsed_while_a_fit_is_awaited(self):
        cls = self.protocol()
        sgu.install_protocol(cls)
        proto = cls()
        proto._solar_window = Fake()
        with mock.patch.object(sgu.json, "loads") as loads:
            proto._on_ps_equip_module_result("{}")
            proto._solar_window = self.waiting_window()
            proto._solar_window._sgu["step"] = "deposit"
            proto._on_ps_equip_module_result("{}")
        loads.assert_not_called()

    def test_a_bad_payload_or_no_window_does_no_harm(self):
        cls = self.protocol()
        sgu.install_protocol(cls)
        proto = cls()
        proto._solar_window = self.waiting_window()
        self.assertEqual(proto._on_ps_equip_module_result("not json"), "game")
        self.assertTrue(sgu.running(proto._solar_window))
        self.assertTrue(sgu.enabled())
        proto._solar_window = None
        self.assertEqual(proto._on_ps_equip_module_result("{}"), "game")
        self.assertEqual(len(cls.handled), 2)

    def test_a_failure_switches_it_off_and_the_game_still_handles_the_answer(self):
        cls = self.protocol()
        sgu.install_protocol(cls)
        proto = cls()
        proto._solar_window = self.waiting_window()
        with mock.patch.object(sgu, "on_equip_result", side_effect=RuntimeError("boom")):
            with self.assertLogs("station_gear_upgrade", level="ERROR"):
                self.assertEqual(proto._on_ps_equip_module_result("{}"), "game")
        self.assertFalse(sgu.enabled())
        self.assertFalse(sgu.running(proto._solar_window))
        self.assertEqual(len(cls.handled), 1)

    def test_a_class_without_the_handler_is_refused(self):
        class Bare:
            pass

        self.assertFalse(sgu.install_protocol(Bare))

    def test_the_environment_switch(self):
        cls = self.protocol()
        sgu.install_protocol(cls)
        proto = cls()
        proto._solar_window = self.waiting_window()
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_NO_GEAR_UPGRADE": "1"}):
            self.assertEqual(proto._on_ps_equip_module_result(json.dumps({"ok": False, "reason": "x"})), "game")
        self.assertTrue(sgu.running(proto._solar_window))


class EventTests(unittest.TestCase):
    def setUp(self):
        sgu.reset_for_tests()
        self.clock = Clock()
        patcher = mock.patch.object(sgu, "time", SimpleNamespace(monotonic=self.clock))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.win = Fake()
        self.win._ps_prefab_dialog = {"station_id": 7, "sgu_rect": pygame.Rect(100, 200, 300, 30)}

    def click(self, x, y, button=1):
        return pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(x, y), button=button)

    def test_a_press_on_the_button_starts_the_run_and_the_game_never_sees_it(self):
        other = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a)
        kept = sgu.filter_events(self.win, [other, self.click(150, 210)])
        self.assertEqual(kept, [other])
        self.assertTrue(sgu.running(self.win))
        self.assertEqual(self.win.commands(), ["PS_TRANSFER_TO_STATION"])

    def test_other_presses_pass_through_untouched(self):
        events = [self.click(10, 10), self.click(150, 210, button=3), self.click(150, 210, button=2),
                  pygame.event.Event(pygame.MOUSEBUTTONUP, pos=(150, 210), button=1),
                  pygame.event.Event(pygame.MOUSEMOTION, pos=(150, 210), rel=(0, 0), buttons=(0, 0, 0))]
        self.assertIs(sgu.filter_events(self.win, events), events)
        self.assertFalse(sgu.running(self.win))

    def test_a_dropdown_a_prompt_or_no_dialog_means_the_button_is_not_there(self):
        for change in ({"dropdown_open": True}, {"name_input": {"text": ""}}, {"delete_confirm": {"name": "x"}}):
            win = Fake()
            win._ps_prefab_dialog = {"station_id": 7, "sgu_rect": pygame.Rect(100, 200, 300, 30), **change}
            events = [self.click(150, 210)]
            self.assertIs(sgu.filter_events(win, events), events)
            self.assertFalse(sgu.running(win))
        win = Fake()
        win._ps_prefab_dialog = None
        events = [self.click(150, 210)]
        self.assertIs(sgu.filter_events(win, events), events)

    def test_every_frame_it_gives_up_on_a_step_that_took_too_long(self):
        sgu.start(self.win)
        self.clock.now += sgu.STEP_WAIT_S + 1
        sgu.filter_events(self.win, [])
        self.assertFalse(sgu.running(self.win))

    def test_a_run_goes_on_with_the_dialog_closed(self):
        sgu.start(self.win)
        self.win._ps_prefab_dialog = None
        self.win.server_take(BEAM3, "Station Beam Cannon Mk III", 31)
        sgu.filter_events(self.win, [])
        self.assertEqual(self.win.commands(), ["PS_TRANSFER_TO_STATION", "PS_EQUIP_MODULE"])

    def test_the_environment_switch_passes_everything_and_does_nothing(self):
        events = [self.click(150, 210)]
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_NO_GEAR_UPGRADE": "1"}):
            self.assertIs(sgu.filter_events(self.win, events), events)
        self.assertFalse(sgu.running(self.win))

    def test_a_failure_switches_it_off_and_the_events_still_pass(self):
        sgu.start(self.win)
        events = [self.click(10, 10)]
        with mock.patch.object(sgu, "advance", side_effect=RuntimeError("boom")):
            with self.assertLogs("station_gear_upgrade", level="ERROR"):
                self.assertIs(sgu.filter_events(self.win, events), events)
        self.assertFalse(sgu.enabled())
        self.assertFalse(sgu.running(self.win))


class ClientHookTests(unittest.TestCase):
    def test_client_py_installs_it_and_feeds_it_events(self):
        source = modloader.game_source("Client.py")
        self.assertIn("\nimport station_gear_upgrade", source)
        self.assertIn("\nstation_gear_upgrade.install(SolarSystemWindow, _render_mixin_module)", source)
        self.assertIn("\nstation_gear_upgrade.install_protocol(ProtocolMixin)", source)
        self.assertEqual(source.count("_frame_events = station_gear_upgrade.filter_events(self, _frame_events)"), 1)

    def test_the_real_classes_are_hooked(self):
        from Client import SolarSystemWindow, ProtocolMixin
        self.assertTrue(SolarSystemWindow.__dict__.get("_station_gear_upgrade_installed"))
        for name in ("set_ps_station_cargo", "_draw_ps_prefab_dialog"):
            self.assertTrue(hasattr(getattr(SolarSystemWindow, name), "__wrapped__"), name)
        self.assertTrue(getattr(ProtocolMixin.__dict__["_on_ps_equip_module_result"], "_station_gear_upgrade", False))

    def test_the_real_window_sends_the_games_own_command_lines(self):
        sgu.reset_for_tests()
        from Client import SolarSystemWindow
        win = SolarSystemWindow.__new__(SolarSystemWindow)
        lines = []
        win._send_fn = lines.append
        win._audio = SimpleNamespace(play_equip_click=lambda: lines.append("click"))
        win._json_cmd = SolarSystemWindow._json_cmd
        win._send_json("PS_TRANSFER_TO_STATION", {"station_id": 7, "item_key": "item:station:" + BEAM3, "amount": 1})
        win._send_equip_json("PS_EQUIP_MODULE", {"station_id": 7, "row_id": 31, "module_type": BEAM3, "count": 1})
        self.assertEqual(lines[0], 'PS_TRANSFER_TO_STATION {"station_id":7,"item_key":"item:station:station_beam_cannon_mk3",'
                                   '"amount":1}')
        self.assertEqual(lines[1], "click")
        self.assertEqual(lines[2], 'PS_EQUIP_MODULE {"station_id":7,"row_id":31,"module_type":"station_beam_cannon_mk3",'
                                   '"count":1}')

    def test_the_real_equip_result_handler_still_logs_for_the_game(self):
        sgu.reset_for_tests()
        from Client import ProtocolMixin
        logged = []
        proto = ProtocolMixin.__new__(ProtocolMixin)
        proto._log = lambda text, colour=None: logged.append(text)
        proto._solar_window = None
        proto._on_ps_equip_module_result(json.dumps({"ok": False, "reason": "No free slot."}))
        self.assertEqual(logged, ["[STATION] Equip failed: No free slot."])


class RealDialogTests(unittest.TestCase):
    """The game's own prefab dialog on a real SolarSystemWindow, with the section inside it."""

    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode((1280, 720))

    def setUp(self):
        sgu.reset_for_tests()

    def window(self, scale=1.0):
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
        window._inventory = fake._inventory
        window._ps_prefab_dialog = {
            "station_id": 7, "prefabs": [{"name": "dps", "item_count": 8}], "selected": None, "dropdown_open": False,
            "list_scroll": 0, "panel_rect": None, "close_rect": None, "header_rect": None, "row_rects": [],
            "delete_rects": [], "save_rect": None, "load_rect": None, "name_input": None, "delete_confirm": None}
        return window

    def draw(self, window, width=1280, height=720):
        from render_mixin import RenderFrame
        screen = pygame.Surface((width, height), pygame.SRCALPHA)
        screen.fill((6, 10, 20, 255))
        window._draw_ps_prefab_dialog(RenderFrame(screen, width, height, lambda *a: a, 1.0, {}, 1, 1, 1 / 60))
        return screen

    def test_the_button_sits_in_the_free_band_of_the_dialog(self):
        window = self.window()
        screen = self.draw(window)
        dialog = window._ps_prefab_dialog
        button = dialog["sgu_rect"]
        panel = pygame.Rect(390, 165, 500, 390)
        self.assertIsNotNone(button)
        self.assertTrue(panel.contains(button))
        self.assertGreater(button.top, dialog["header_rect"].bottom + 80)             # below the game's help text
        self.assertLess(button.bottom, dialog["load_rect"].top - 20)                  # above its status line and buttons
        self.assertFalse(button.colliderect(dialog["load_rect"]) or button.colliderect(dialog["save_rect"]))
        self.assertEqual((button.left, button.width), (dialog["header_rect"].left, 248))    # the left part of the band
        self.assertEqual(screen.get_at(button.move(3, 3).topleft)[:3], (20, 62, 45))       # an enabled button's fill
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_NO_STATION_EXPANSION": "1"}):         # no neighbour: the whole band
            alone = self.window()
            self.draw(alone)
        self.assertEqual(alone._ps_prefab_dialog["sgu_rect"].width, dialog["header_rect"].width)

    def test_everything_the_section_draws_stays_inside_the_dialog(self):
        window = self.window()
        quiet = self.window()
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_NO_GEAR_UPGRADE": "1"}):
            before = self.draw(quiet)
        after = self.draw(window)
        panel = pygame.Rect(390, 165, 500, 390)
        changed = [(x, y) for x in range(0, 1280, 2) for y in range(0, 720, 2)
                   if before.get_at((x, y)) != after.get_at((x, y))]
        self.assertTrue(changed)
        self.assertTrue(all(panel.collidepoint(point) for point in changed), "drew outside the dialog")

    def test_nothing_to_upgrade_draws_a_dead_button(self):
        window = self.window()
        window._inventory = []
        screen = self.draw(window)
        dialog = window._ps_prefab_dialog
        self.assertIsNone(dialog["sgu_rect"])
        enabled_window = self.window()
        enabled_screen = self.draw(enabled_window)
        probe = enabled_window._ps_prefab_dialog["sgu_rect"].move(0, 6).topleft
        self.assertNotEqual(screen.get_at(probe)[:3], enabled_screen.get_at(probe)[:3])

    def test_no_section_over_the_dropdown_a_prompt_or_a_closed_dialog(self):
        window = self.window()
        window._ps_prefab_dialog["dropdown_open"] = True
        self.draw(window)
        self.assertIsNone(window._ps_prefab_dialog["sgu_rect"])
        window = self.window()
        window._ps_prefab_dialog["name_input"] = {"text": ""}
        self.draw(window)
        self.assertIsNone(window._ps_prefab_dialog["sgu_rect"])
        window = self.window()
        window._ps_prefab_dialog = None
        self.draw(window)

    def test_the_dialog_is_the_games_own_without_the_section(self):
        window = self.window()
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_NO_GEAR_UPGRADE": "1"}):
            self.draw(window)
        self.assertIsNone(window._ps_prefab_dialog.get("sgu_rect"))

    def test_the_button_text_changes_with_a_running_upgrade(self):
        window = self.window()
        window._send_json = lambda *a: None
        window.chat_log = lambda text, colour=None: None
        idle = self.draw(window)
        button = window._ps_prefab_dialog["sgu_rect"]
        sgu.start(window)
        busy = self.draw(window)
        self.assertIsNone(window._ps_prefab_dialog["sgu_rect"])
        probe = pygame.Rect(button.x + 8, button.y + 4, button.width - 16, button.height - 8)
        self.assertNotEqual(pygame.image.tobytes(idle.subsurface(probe), "RGB"),
                            pygame.image.tobytes(busy.subsurface(probe), "RGB"))

    def test_at_one_and_a_half_times_the_size(self):
        window = self.window(scale=1.5)
        window._s = lambda value: int(round(value * 1.5))
        screen = self.draw(window, 1920, 1080)
        dialog = window._ps_prefab_dialog
        button = dialog["sgu_rect"]
        self.assertIsNotNone(button)
        self.assertLess(button.bottom, dialog["load_rect"].top)
        self.assertGreater(button.top, dialog["header_rect"].bottom)
        self.assertEqual(button.height, 45)

    def test_a_click_on_the_drawn_button_starts_the_upgrade(self):
        window = self.window()
        sent = []
        window._send_json = lambda cmd, payload: sent.append(cmd)
        window.chat_log = lambda text, colour=None: None
        self.draw(window)
        button = window._ps_prefab_dialog["sgu_rect"]
        click = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=button.center, button=1)
        self.assertEqual(sgu.filter_events(window, [click]), [])
        self.assertEqual(sent, ["PS_TRANSFER_TO_STATION"])


if __name__ == "__main__":
    unittest.main()

"""station_probe: /stationprobe sends the Logs tab's PS_LOGS_REQUEST for a station you type and reports the answer."""
import modloader
import json
import os
import threading
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import station_probe as probe


class Fake:
    def __init__(self):
        self.sent, self.log = [], []
        self._lock = threading.RLock()
        self._station_overlay = None
        self._layout = {"system_name": "Veritate Basin"}
        self._my_stations_data = [
            {"station_id": 2083, "display_name": "eskvenyreth - 1", "system_name": "Veritate Basin"},
            {"station_id": 2105, "display_name": "cryldorwen - 1 - col", "system_name": "Zordoil Frontier"},
            {"station_id": 5125, "display_name": "Small Central Command", "system_name": "Vesmaeth Deep"},
            {"display_name": "no id"},
        ]
        self.logs_replies, self.cargo_replies, self.bay_replies = [], [], []
        self.ticks = 0

    def _send_json(self, cmd, payload):
        self.sent.append((cmd, payload))

    def _send_fn(self, line):
        self.sent.append(line)

    def set_ps_station_cargo(self, data):
        self.cargo_replies.append(data)

    def set_ps_construction_bay(self, data):
        self.bay_replies.append(data)

    def chat_log(self, text, colour=None):
        self.log.append(text)

    def set_station_logs(self, data):
        self.logs_replies.append(data)

    def _station_logs_tick(self):
        self.ticks += 1


def make_class():
    class Window(Fake):
        pass
    return Window


class CommandTests(unittest.TestCase):
    def setUp(self):
        probe.reset_for_tests()
        self.win = Fake()

    def test_no_arguments_print_the_usage(self):
        probe.handle_command(self.win, "/stationprobe")
        self.assertIn("Usage: /stationprobe <station id>", self.win.log[0])
        self.assertEqual(self.win.sent, [])

    def test_an_id_sends_exactly_the_logs_tabs_request(self):
        probe.handle_command(self.win, "/stationprobe 2105")
        (command, payload), = self.win.sent
        self.assertEqual(command, "PS_LOGS_REQUEST")
        self.assertEqual(set(payload), {"station_id", "query", "before", "request_id"})
        self.assertEqual((payload["station_id"], payload["query"], payload["before"]), (2105, "", None))
        self.assertGreaterEqual(payload["request_id"], probe.REQUEST_ID_BASE)
        self.assertGreaterEqual(probe.REQUEST_ID_BASE, 1_000_000)        # the Logs tab counts 1, 2, 3...: never meet
        json.dumps(payload)                                              # it must be sendable as it stands
        self.assertIn("Probe sent for station 2105 (not docked, in Veritate Basin)", self.win.log[0])

    def test_a_bad_id_sends_nothing(self):
        for text in ("abc", "-4", "12.5", "0x10"):
            probe.handle_command(self.win, f"/stationprobe {text}")
        self.assertEqual(self.win.sent, [])
        self.assertEqual(len(self.win.log), 4)
        self.assertIn("is not a station id", self.win.log[0])

    def test_list_shows_the_stations_that_have_an_id_and_filters_by_text(self):
        probe.handle_command(self.win, "/stationprobe list")
        self.assertEqual(self.win.log[:3], ["  2083  eskvenyreth - 1  (Veritate Basin)",
                                           "  2105  cryldorwen - 1 - col  (Zordoil Frontier)",
                                           "  5125  Small Central Command  (Vesmaeth Deep)"])
        self.assertEqual(self.win.log[3], "3 of 4 station(s) match.")
        self.win.log.clear()
        probe.handle_command(self.win, "/stationprobe list zordoil")
        self.assertEqual(self.win.log, ["  2105  cryldorwen - 1 - col  (Zordoil Frontier)", "1 of 4 station(s) match."])
        self.win.log.clear()
        probe.handle_command(self.win, "/stationprobe list 5125")
        self.assertEqual(len(self.win.log), 2)

    def test_list_before_the_possessions_list_has_loaded_says_what_to_do(self):
        self.win._my_stations_data = []
        probe.handle_command(self.win, "/stationprobe list")
        self.assertIn("open Stats > Possessions > Stations", self.win.log[0])

    def test_a_long_list_is_cut_and_says_so(self):
        self.win._my_stations_data = [{"station_id": i, "display_name": f"s{i}", "system_name": "X"}
                                      for i in range(40)]
        probe.handle_command(self.win, "/stationprobe list")
        self.assertEqual(len(self.win.log), probe.LIST_MAX + 1)
        self.assertIn(f"first {probe.LIST_MAX} shown", self.win.log[-1])

    def test_the_kill_switch_refuses(self):
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_NO_STATION_PROBE": "1"}):
            probe.handle_command(self.win, "/stationprobe 2105")
        self.assertEqual(self.win.sent, [])
        self.assertIn("switched off", self.win.log[0])


class SendTests(unittest.TestCase):
    def setUp(self):
        probe.reset_for_tests()
        self.win = Fake()

    def test_one_probe_at_a_time_and_a_gap_between_probes(self):
        self.assertTrue(probe.send(self.win, 2105))
        self.assertFalse(probe.send(self.win, 2083))
        self.assertIn("still waiting", self.win.log[-1])
        self.assertEqual(len(self.win.sent), 1)
        self.win._probe = None                                           # answered, but too soon for another
        self.assertFalse(probe.send(self.win, 2083))
        self.assertIn("Wait a moment", self.win.log[-1])
        self.win._probe_last_sent -= probe.MIN_GAP_S + 1
        self.assertTrue(probe.send(self.win, 2083))
        self.assertEqual(len(self.win.sent), 2)

    def test_every_probe_has_its_own_request_id(self):
        probe.send(self.win, 1)
        first = self.win.sent[0][1]["request_id"]
        self.win._probe = None
        self.win._probe_last_sent -= 100
        probe.send(self.win, 1)
        self.assertEqual(self.win.sent[1][1]["request_id"], first + 1)

    def test_a_probe_that_got_no_answer_can_be_followed_by_another(self):
        probe.send(self.win, 1)
        self.win._probe["sent"] -= probe.TIMEOUT_S + 1
        self.win._probe_last_sent -= probe.TIMEOUT_S + 1
        self.assertTrue(probe.send(self.win, 2))

    def test_the_context_says_where_the_player_was(self):
        self.assertEqual(probe.context(self.win), "not docked, in Veritate Basin")
        self.win._station_overlay = {"kind": "player_station", "data": {"attached_station_id": 2083,
                                                                          "station_name": "eskvenyreth - 1"}}
        self.assertEqual(probe.context(self.win), "docked at station 2083 (eskvenyreth - 1), in Veritate Basin")
        self.win._station_overlay = {"kind": "npc", "data": {}}
        self.win._layout = None
        self.assertEqual(probe.context(self.win), "not docked, in an unknown system")


class ReplyTests(unittest.TestCase):
    def setUp(self):
        probe.reset_for_tests()
        self.win = Fake()
        probe.send(self.win, 2105)
        self.rid = self.win.sent[0][1]["request_id"]
        self.win.log.clear()

    def test_rows_mean_it_answered(self):
        data = {"request_id": self.rid, "station_id": 2105, "has_more": True,
                "rows": [{"id": 9, "time": 1789917904, "text": "Alice docked"}, {"id": 8, "time": 1, "text": "x"}]}
        self.assertTrue(probe.on_reply(self.win, data))
        self.assertIn("ANSWERED with 2 log row(s), more available", self.win.log[0])
        self.assertIn("Alice docked", self.win.log[1])
        self.assertIsNone(self.win._probe)

    def test_an_error_text_is_shown(self):
        probe.on_reply(self.win, {"request_id": self.rid, "station_id": 2105, "rows": [],
                                  "error": "You must be docked at this station."})
        self.assertIn("answered with an error: You must be docked at this station.", self.win.log[0])

    def test_an_empty_answer_is_not_called_a_refusal(self):
        probe.on_reply(self.win, {"request_id": self.rid, "station_id": 2105, "rows": []})
        self.assertIn("answered with no rows and no error", self.win.log[0])

    def test_a_reply_that_names_another_station_says_so(self):
        probe.on_reply(self.win, {"request_id": self.rid, "station_id": 7, "rows": []})
        self.assertIn("[the reply says station 7]", self.win.log[0])

    def test_somebody_elses_reply_is_not_taken(self):
        self.assertFalse(probe.on_reply(self.win, {"request_id": 3, "station_id": 2105, "rows": []}))
        self.assertFalse(probe.on_reply(self.win, {"station_id": 2105, "rows": []}))
        self.assertIsNotNone(self.win._probe)
        self.assertEqual(self.win.log, [])

    def test_a_reply_with_nothing_waiting_is_not_taken(self):
        probe.on_reply(self.win, {"request_id": self.rid, "rows": []})
        self.assertFalse(probe.on_reply(self.win, {"request_id": self.rid, "rows": []}))

    def test_the_timeout_reports_once(self):
        probe.tick(self.win, time.monotonic() + probe.TIMEOUT_S - 1)
        self.assertEqual(self.win.log, [])
        probe.tick(self.win, time.monotonic() + probe.TIMEOUT_S + 1)
        self.assertIn("no PS_LOGS reply in 8 s", self.win.log[0])
        probe.tick(self.win, time.monotonic() + 100)
        self.assertEqual(len(self.win.log), 1)

    def test_the_result_is_written_to_the_client_log(self):
        with self.assertLogs(probe.logger, "INFO") as logged:
            probe.on_reply(self.win, {"request_id": self.rid, "station_id": 2105, "rows": [], "error": "nope"})
        self.assertIn("reply for station 2105 (not docked, in Veritate Basin)", logged.output[0])
        self.assertIn('"error":"nope"', logged.output[0])


class HookTests(unittest.TestCase):
    def setUp(self):
        probe.reset_for_tests()
        self.cls = make_class()
        self.assertTrue(probe.install(self.cls))
        self.win = self.cls()

    def test_install_is_idempotent_and_refuses_a_window_without_the_methods(self):
        self.assertFalse(probe.install(self.cls))
        with self.assertLogs(probe.logger, "WARNING"):
            self.assertFalse(probe.install(type("Bare", (), {})))

    def test_the_probes_reply_is_kept_from_the_logs_tab_and_everyone_elses_goes_through(self):
        probe.send(self.win, 2105)
        rid = self.win.sent[0][1]["request_id"]
        self.win.set_station_logs({"request_id": rid, "station_id": 2105, "rows": []})
        self.assertEqual(self.win.logs_replies, [])
        other = {"request_id": 4, "station_id": 2083, "rows": []}
        self.win.set_station_logs(other)
        self.assertEqual(self.win.logs_replies, [other])
        self.win.set_station_logs("junk")                                  # not a dict: the game's own code decides
        self.assertEqual(len(self.win.logs_replies), 2)

    def test_the_per_frame_tick_runs_the_games_and_then_the_timeout_check(self):
        probe.send(self.win, 2105)
        self.win._probe["sent"] -= probe.TIMEOUT_S + 1
        self.win._station_logs_tick()
        self.assertEqual(self.win.ticks, 1)
        self.assertIn("no PS_LOGS reply", self.win.log[-1])

    def test_a_failure_switches_it_off_and_the_game_still_runs(self):
        with mock.patch.object(probe, "on_reply", side_effect=RuntimeError("boom")), \
                self.assertLogs(probe.logger, "ERROR"):
            self.win.set_station_logs({"request_id": 1, "rows": []})
        self.assertEqual(len(self.win.logs_replies), 1)
        self.assertFalse(probe.enabled())
        self.win.set_station_logs({"request_id": 2, "rows": []})
        self.assertEqual(len(self.win.logs_replies), 2)
        self.win._station_logs_tick()
        self.assertEqual(self.win.ticks, 1)


class CargoProbeTests(unittest.TestCase):
    def setUp(self):
        probe.reset_for_tests()
        self.cls = make_class()
        probe.install(self.cls)
        self.win = self.cls()

    def test_cargo_sends_the_plain_command_the_dock_sends(self):
        probe.handle_command(self.win, "/stationprobe cargo 2105")
        self.assertEqual(self.win.sent, ["PS_REQUEST_CARGO 2105"])
        self.assertIn("PS_REQUEST_CARGO", self.win.log[0])
        self.assertEqual(self.win._probe["kind"], "cargo")

    def test_construction_sends_its_own_command(self):
        probe.handle_command(self.win, "/stationprobe construction 2105")
        self.assertEqual(self.win.sent, ["PS_REQUEST_CONSTRUCTION_BAY 2105"])

    def test_they_refuse_to_run_while_docked_because_the_reply_has_no_station_id(self):
        for kind in ("cargo", "construction"):
            for overlay in ("player_station", "npc"):
                self.win._station_overlay = {"kind": overlay, "data": {}}
                probe.handle_command(self.win, f"/stationprobe {kind} 2105")
        self.assertEqual(self.win.sent, [])
        self.assertIn("only runs while you are not docked", self.win.log[0])

    def test_a_missing_or_bad_id_sends_nothing(self):
        probe.handle_command(self.win, "/stationprobe cargo")
        probe.handle_command(self.win, "/stationprobe cargo abc")
        self.assertEqual(self.win.sent, [])
        self.assertIn("Usage: /stationprobe cargo <station id>", self.win.log[0])
        self.assertIn("is not a station id", self.win.log[1])

    def test_the_cargo_reply_is_reported_and_kept_from_the_games_state(self):
        probe.handle_command(self.win, "/stationprobe cargo 2105")
        self.win.log.clear()
        reply = {"cargo": [{"item_key": "metal_ore", "cargo_size": 50}] * 3, "hull_used": 40, "hull_space": 100,
                 "station_credits": 12345}
        self.win.set_ps_station_cargo(reply)
        self.assertEqual(self.win.cargo_replies, [])
        self.assertIn("ANSWERED: 3 cargo row(s), hull 40/100, credits 12345", self.win.log[0])
        self.assertIsNone(self.win._probe)
        self.win.set_ps_station_cargo(reply)                              # nothing waits any more: the game's own
        self.assertEqual(self.win.cargo_replies, [reply])

    def test_the_construction_reply_is_reported(self):
        probe.handle_command(self.win, "/stationprobe construction 2105")
        self.win.log.clear()
        self.win.set_ps_construction_bay({"queue": [{}, {}], "blueprints": [{}]})
        self.assertEqual(self.win.bay_replies, [])
        self.assertIn("ANSWERED: 2 project(s), 1 blueprint(s)", self.win.log[0])

    def test_an_error_in_the_reply_is_shown(self):
        probe.handle_command(self.win, "/stationprobe cargo 2105")
        self.win.log.clear()
        self.win.set_ps_station_cargo({"cargo": [], "error": "no permission"})
        self.assertIn("answered with an error: no permission", self.win.log[0])

    def test_another_kinds_reply_is_not_taken(self):
        probe.handle_command(self.win, "/stationprobe cargo 2105")
        self.win.set_ps_construction_bay({"queue": []})                   # a cargo probe waits, not this
        self.assertEqual(len(self.win.bay_replies), 1)
        self.win.set_station_logs({"request_id": self.win._probe["request_id"], "rows": []})
        self.assertEqual(len(self.win.logs_replies), 1)                   # the logs path ignores a cargo probe
        self.assertIsNotNone(self.win._probe)

    def test_a_logs_probe_does_not_swallow_a_cargo_push(self):
        probe.handle_command(self.win, "/stationprobe 2105")
        self.win.set_ps_station_cargo({"cargo": []})
        self.assertEqual(len(self.win.cargo_replies), 1)
        self.assertIsNotNone(self.win._probe)

    def test_the_timeout_names_the_reply_that_never_came(self):
        probe.handle_command(self.win, "/stationprobe cargo 2105")
        self.win.log.clear()
        probe.tick(self.win, time.monotonic() + probe.TIMEOUT_S + 1)
        self.assertIn("no PS_STATION_CARGO reply in 8 s", self.win.log[0])
        self.win._probe_last_sent -= 10
        probe.handle_command(self.win, "/stationprobe construction 2105")
        probe.tick(self.win, time.monotonic() + probe.TIMEOUT_S + 1)
        self.assertIn("no PS_CONSTRUCTION_BAY reply", self.win.log[-1])

    def test_the_cargo_probe_is_off_with_the_kill_switch(self):
        probe._failed = True
        try:
            self.win.set_ps_station_cargo({"cargo": []})
        finally:
            probe.reset_for_tests()
        self.assertEqual(len(self.win.cargo_replies), 1)


class RenameProbeTests(unittest.TestCase):
    """/stationprobe rename <id>: the one probe that writes, so it only ever sends a station its own current name."""

    def setUp(self):
        probe.reset_for_tests()
        self.win = Fake()

    def test_it_sends_exactly_the_rename_dialogs_request_with_the_name_the_station_has(self):
        probe.handle_command(self.win, "/stationprobe rename 2105")
        (command, payload), = self.win.sent
        self.assertEqual(command, "PS_RENAME_STATION")
        self.assertEqual(payload, {"station_id": 2105, "name": "cryldorwen - 1 - col"})
        json.dumps(payload)
        self.assertIn("Probe sent for station 2105 (not docked, in Veritate Basin): PS_RENAME_STATION, to its own name "
                      "'cryldorwen - 1 - col' (nothing changes if the server accepts it)", self.win.log[0])
        self.assertEqual(self.win._probe["kind"], "rename")

    def test_it_also_runs_docked_at_the_station_as_the_control(self):
        self.win._station_overlay = {"kind": "player_station", "data": {"attached_station_id": 2083,
                                                                          "station_name": "eskvenyreth - 1"}}
        probe.handle_command(self.win, "/stationprobe rename 2083")
        self.assertEqual(self.win.sent, [("PS_RENAME_STATION", {"station_id": 2083, "name": "eskvenyreth - 1"})])
        self.assertIn("docked at station 2083", self.win.log[0])

    def test_a_missing_or_bad_id_sends_nothing(self):
        for text in ("rename", "rename abc", "rename -3", "rename 1.5"):
            probe.handle_command(self.win, f"/stationprobe {text}")
        self.assertEqual(self.win.sent, [])
        self.assertIn("Usage: /stationprobe rename <station id>", self.win.log[0])
        self.assertIn("is not a station id", self.win.log[1])

    def test_a_station_that_is_not_yours_is_never_sent(self):
        probe.handle_command(self.win, "/stationprobe rename 999999")
        self.win._my_stations_data = []
        self.win._probe_last_sent = -1e9
        probe.handle_command(self.win, "/stationprobe rename 2105")
        self.assertEqual(self.win.sent, [])
        self.assertIn("is not in your Possessions list", self.win.log[0])
        self.assertIn("is not in your Possessions list", self.win.log[1])
        self.assertIsNone(self.win.__dict__.get("_probe"))

    def test_another_players_station_in_this_system_is_not_sent_even_when_its_name_is_known(self):
        self.win._deployed_stations = [{"attached_station_id": 777, "display_name": "Theirs - 1", "owner_name": "Bob"}]
        probe.handle_command(self.win, "/stationprobe rename 777")
        self.assertEqual(self.win.sent, [])

    def test_a_jump_gate_is_refused(self):
        self.win._my_stations_data = [{"station_id": 55, "display_name": "Gate", "jump_gate": True}]
        probe.handle_command(self.win, "/stationprobe rename 55")
        self.assertEqual(self.win.sent, [])
        self.assertIn("jump gate", self.win.log[0])

    def test_a_station_with_no_name_is_never_renamed_to_a_default(self):
        self.win._my_stations_data = [{"station_id": 56}, {"station_id": 57, "display_name": "   "}]
        probe.handle_command(self.win, "/stationprobe rename 56")
        self.win._probe_last_sent = -1e9
        probe.handle_command(self.win, "/stationprobe rename 57")
        self.assertEqual(self.win.sent, [])
        self.assertIn("no name in the lists", self.win.log[0])
        self.assertIn("no name in the lists", self.win.log[1])

    def test_the_live_station_list_must_agree_with_possessions(self):
        self.win._deployed_stations = [{"attached_station_id": 2105, "display_name": "cryldorwen - 1 - col"}]
        probe.handle_command(self.win, "/stationprobe rename 2105")
        self.assertEqual(len(self.win.sent), 1)                           # same name in both: sent
        self.win._probe = None
        self.win._probe_last_sent = -1e9
        self.win._deployed_stations = [{"attached_station_id": 2105, "display_name": "Renamed this session"}]
        probe.handle_command(self.win, "/stationprobe rename 2105")
        self.assertEqual(len(self.win.sent), 1)                           # a stale name would rename it for real
        self.assertIn("disagree", self.win.log[-1])
        self.assertIn("Renamed this session", self.win.log[-1])
        self.assertIn("cryldorwen - 1 - col", self.win.log[-1])

    def test_the_live_list_is_read_by_either_id_key_and_a_blank_live_name_is_ignored(self):
        self.win._deployed_stations = [{"id": 2105, "display_name": "other"}]
        probe.handle_command(self.win, "/stationprobe rename 2105")
        self.assertEqual(self.win.sent, [])                               # matched by "id": it disagrees
        self.win._deployed_stations = [{"attached_station_id": 2105, "display_name": ""}, "junk", None]
        probe.handle_command(self.win, "/stationprobe rename 2105")
        self.assertEqual(self.win.sent, [("PS_RENAME_STATION", {"station_id": 2105, "name": "cryldorwen - 1 - col"})])

    def test_ids_are_compared_as_text_in_the_possessions_list_too(self):
        self.win._my_stations_data = [{"station_id": "2105", "display_name": "text id"}]
        probe.handle_command(self.win, "/stationprobe rename 2105")
        self.assertEqual(self.win.sent, [("PS_RENAME_STATION", {"station_id": 2105, "name": "text id"})])

    def test_a_logs_probes_timeout_does_not_carry_the_rename_sentence(self):
        probe.handle_command(self.win, "/stationprobe 2105")
        self.win.log.clear()
        probe.tick(self.win, time.monotonic() + probe.TIMEOUT_S + 1)
        self.assertIn("no PS_LOGS reply", self.win.log[0])
        self.assertNotIn("nothing changed", self.win.log[0])

    def test_a_local_refusal_does_not_use_up_the_probe_slot(self):
        probe.handle_command(self.win, "/stationprobe rename 999999")
        self.assertIsNone(self.win.__dict__.get("_probe"))
        self.assertNotIn("_probe_last_sent", self.win.__dict__)
        self.assertNotIn("_probe_count", self.win.__dict__)
        probe.handle_command(self.win, "/stationprobe rename 2105")
        self.assertEqual(len(self.win.sent), 1)

    def test_one_probe_at_a_time_and_a_gap_apply_to_it_too(self):
        probe.handle_command(self.win, "/stationprobe rename 2105")
        probe.handle_command(self.win, "/stationprobe rename 2083")
        self.assertIn("still waiting", self.win.log[-1])
        self.win._probe = None
        probe.handle_command(self.win, "/stationprobe rename 2083")
        self.assertIn("Wait a moment", self.win.log[-1])
        self.assertEqual(len(self.win.sent), 1)

    def test_usage_lists_the_rename_probe(self):
        probe.handle_command(self.win, "/stationprobe help")
        self.assertTrue(any("/stationprobe rename <station id>" in line for line in self.win.log))

    def test_the_kill_switch_refuses(self):
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_NO_STATION_PROBE": "1"}):
            probe.handle_command(self.win, "/stationprobe rename 2105")
        self.assertEqual(self.win.sent, [])

    # ---- the answer ----
    def sent_probe(self):
        probe.handle_command(self.win, "/stationprobe rename 2105")
        self.win.log.clear()
        return self.win._probe

    def test_the_confirmation_for_that_station_and_name_is_the_answer(self):
        self.sent_probe()
        reply = {"station_id": 2105, "name": "cryldorwen - 1 - col"}
        self.assertTrue(probe.on_rename(self.win, reply))
        self.assertIn("ACCEPTED", self.win.log[0])
        self.assertIn("'cryldorwen - 1 - col' (unchanged)", self.win.log[0])
        self.assertIn("not docked, in Veritate Basin", self.win.log[0])
        self.assertIsNone(self.win._probe)
        self.assertFalse(probe.on_rename(self.win, reply))                # nothing waits any more

    def test_a_confirmation_for_something_else_is_not_taken(self):
        self.sent_probe()
        for reply in ({"station_id": 2083, "name": "cryldorwen - 1 - col"},         # another station
                      {"station_id": 2105, "name": "A real rename by the player"},   # same station, another name
                      {"name": "cryldorwen - 1 - col"}, {}, "junk", None):
            self.assertFalse(probe.on_rename(self.win, reply), reply)
        self.assertIsNotNone(self.win._probe)
        self.assertEqual(self.win.log, [])

    def test_the_station_id_is_compared_as_text(self):
        self.sent_probe()
        self.assertTrue(probe.on_rename(self.win, {"station_id": "2105", "name": "cryldorwen - 1 - col"}))

    def test_only_a_rename_probe_takes_a_rename_confirmation(self):
        probe.handle_command(self.win, "/stationprobe 2105")              # a logs probe waits
        self.win._probe["name"] = "cryldorwen - 1 - col"                  # even one that matches by station and name
        self.assertFalse(probe.on_rename(self.win, {"station_id": 2105, "name": "cryldorwen - 1 - col"}))
        self.assertIsNotNone(self.win._probe)

    def test_a_rename_probe_ignores_the_other_kinds_replies(self):
        probe_state = self.sent_probe()
        self.assertFalse(probe.on_cargo(self.win, {"cargo": []}))
        self.assertFalse(probe.on_cargo(self.win, {"queue": []}))
        self.assertFalse(probe.on_reply(self.win, {"request_id": probe_state["request_id"], "rows": []}))
        self.assertIs(self.win._probe, probe_state)

    def test_the_timeout_names_the_reply_and_says_nothing_changed(self):
        self.sent_probe()
        probe.tick(self.win, time.monotonic() + probe.TIMEOUT_S + 1)
        self.assertIn("no PS_RENAME_STATION_OK reply in 8 s", self.win.log[0])
        self.assertIn("nothing changed either way", self.win.log[0])
        self.assertIn("Log tab", self.win.log[0])

    def test_the_result_is_written_to_the_client_log(self):
        self.sent_probe()
        with self.assertLogs(probe.logger, "INFO") as logged:
            probe.on_rename(self.win, {"station_id": 2105, "name": "cryldorwen - 1 - col"})
        self.assertIn("rename reply for station 2105 (not docked, in Veritate Basin)", logged.output[0])


class RenameHookTests(unittest.TestCase):
    """The wrapper on ProtocolMixin's PS_RENAME_STATION_OK handler: it reads the answer, and the game's handler always runs."""

    def setUp(self):
        probe.reset_for_tests()
        self.calls = []

        class Protocol:
            def _on_ps_rename_station_ok(inner, payload, *args, **kwargs):
                """the game's own handler"""
                self.calls.append((payload, args, kwargs))
                return "game result"

        self.cls = Protocol
        self.assertTrue(probe.install_protocol(Protocol))
        self.win = Fake()
        self.app = Protocol()
        self.app._solar_window = self.win
        probe.handle_command(self.win, "/stationprobe rename 2105")
        self.win.log.clear()

    def answer(self, data=None, raw=None):
        payload = raw if raw is not None else json.dumps(data)
        return self.app._on_ps_rename_station_ok(payload)

    def test_the_probes_answer_is_reported_and_the_game_still_handles_it(self):
        payload = json.dumps({"station_id": 2105, "name": "cryldorwen - 1 - col"})
        self.assertEqual(self.app._on_ps_rename_station_ok(payload, 1, key=2), "game result")
        self.assertIn("ACCEPTED", self.win.log[0])
        self.assertEqual(self.calls, [(payload, (1,), {"key": 2})])        # same arguments, once

    def test_every_other_answer_goes_to_the_game_alone(self):
        self.answer({"station_id": 2083, "name": "something"})
        self.answer({"station_id": 2105, "name": "Player's own rename"})
        self.answer(raw="not json {")
        self.answer(raw=b"\xff\xfe")
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.win.log, [])
        self.assertTrue(probe.enabled())                                    # bad data never switches the probe off
        self.assertIsNotNone(self.win._probe)

    def test_with_no_rename_probe_waiting_the_payload_is_not_even_parsed(self):
        self.win._probe = None
        with mock.patch.object(probe.json, "loads") as loads:
            self.answer({"station_id": 2105, "name": "x"})
        loads.assert_not_called()
        self.assertEqual(len(self.calls), 1)

    def test_no_window_yet_is_fine(self):
        self.app._solar_window = None
        self.answer({"station_id": 2105, "name": "x"})
        self.assertEqual(len(self.calls), 1)

    def test_a_failure_switches_the_probe_off_and_the_game_still_runs(self):
        with mock.patch.object(probe, "on_rename", side_effect=RuntimeError("boom")), \
                self.assertLogs(probe.logger, "ERROR"):
            self.answer({"station_id": 2105, "name": "x"})
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(probe.enabled())
        self.answer({"station_id": 2105, "name": "cryldorwen - 1 - col"})
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.win.log, [])

    def test_the_kill_switch_leaves_only_the_game(self):
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_NO_STATION_PROBE": "1"}):
            self.answer({"station_id": 2105, "name": "cryldorwen - 1 - col"})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.win.log, [])

    def test_install_is_idempotent_keeps_the_original_and_refuses_a_class_without_the_handler(self):
        self.assertFalse(probe.install_protocol(self.cls))
        wrapper = self.cls.__dict__["_on_ps_rename_station_ok"]
        self.assertEqual(wrapper.__name__, "_on_ps_rename_station_ok")
        self.assertEqual(wrapper.__doc__, "the game's own handler")
        self.assertTrue(callable(wrapper.__wrapped__))
        with self.assertLogs(probe.logger, "WARNING"):
            self.assertFalse(probe.install_protocol(type("Bare", (), {})))


class ClientHookTests(unittest.TestCase):
    def test_client_py_has_the_command_and_the_install(self):
        from pathlib import Path
        source = modloader.game_source("Client.py")
        self.assertIn('elif _cmd == "stationprobe":', source)
        self.assertIn("station_probe.handle_command(self, _msg)", source)
        self.assertIn("\nstation_probe.install(SolarSystemWindow)\n", source)
        self.assertIn("\nstation_probe.install_protocol(ProtocolMixin)", source)

    def test_the_real_protocol_handler_is_hooked_and_the_games_own_handler_still_runs(self):
        import types
        from Client import SolarSystemWindow  # noqa: F401  (Client's bottom installs the hooks)
        from protocol import ProtocolMixin
        probe.reset_for_tests()
        self.assertTrue(getattr(ProtocolMixin.__dict__["_on_ps_rename_station_ok"], "_station_probe", False))
        win = Fake()
        win._station_overlay = {"kind": "player_station", "data": {"attached_station_id": 2105, "station_name": "stale"}}
        win._deployed_stations = [{"attached_station_id": 2105, "display_name": "stale"}]
        win._probe = {"request_id": 9_000_001, "station_id": 2105, "sent": time.monotonic(), "where": "not docked, in X",
                      "kind": "rename", "name": "cryldorwen - 1 - col"}
        app = types.SimpleNamespace(_solar_window=win)
        ProtocolMixin._on_ps_rename_station_ok(app, json.dumps({"station_id": 2105, "name": "cryldorwen - 1 - col"}))
        self.assertTrue(any("ACCEPTED" in text for text in win.log))
        # the game's own handler ran too: the overlay header and the deployed list took the confirmed name
        self.assertEqual(win._station_overlay["data"]["station_name"], "cryldorwen - 1 - col")
        self.assertEqual(win._deployed_stations[0]["display_name"], "cryldorwen - 1 - col")

    def test_the_real_window_sends_the_rename_as_a_real_command_line(self):
        from Client import SolarSystemWindow
        probe.reset_for_tests()
        window = SolarSystemWindow.__new__(SolarSystemWindow)
        window._lock = threading.RLock()
        window._station_overlay = None
        window._layout = {"system_name": "Talreis Pass"}
        window._my_stations_data = [{"station_id": 9448, "display_name": "M/S Zeph M 1", "system_name": "Talreis Pass"}]
        window._deployed_stations = []
        lines, logged = [], []
        window._send_fn = lines.append
        window.chat_log = lambda text, colour=None: logged.append(text)
        probe.handle_command(window, "/stationprobe rename 9448")
        (line,) = lines
        self.assertTrue(line.startswith("PS_RENAME_STATION {"))
        self.assertEqual(json.loads(line[len("PS_RENAME_STATION "):]), {"station_id": 9448, "name": "M/S Zeph M 1"})
        self.assertTrue(any("PS_RENAME_STATION, to its own name 'M/S Zeph M 1'" in text for text in logged))

    def test_the_real_window_is_hooked_and_a_probe_goes_out_as_a_real_command_line(self):
        from Client import SolarSystemWindow
        probe.reset_for_tests()
        self.assertTrue(SolarSystemWindow.__dict__.get("_station_probe_installed"))
        window = SolarSystemWindow.__new__(SolarSystemWindow)
        window._lock = threading.RLock()
        window._station_overlay = None
        window._layout = {"system_name": "Talreis Pass"}
        lines, logged = [], []
        window._send_fn = lines.append
        window.chat_log = lambda text, colour=None: logged.append(text)
        probe.handle_command(window, "/stationprobe 9448")
        (line,) = lines
        self.assertTrue(line.startswith("PS_LOGS_REQUEST {"))
        payload = json.loads(line[len("PS_LOGS_REQUEST "):])
        self.assertEqual((payload["station_id"], payload["query"], payload["before"]), (9448, "", None))
        # the game's own Logs-tab state is not touched by the probe's reply
        window._station_logs = {"request": 1}
        window.set_station_logs({"request_id": payload["request_id"], "station_id": 9448,
                                 "rows": [{"id": 1, "time": 5, "text": "hello"}]})
        self.assertEqual(window._station_logs, {"request": 1})
        self.assertTrue(any("ANSWERED with 1 log row(s)" in text for text in logged))

    def test_the_real_window_sends_the_cargo_command_as_a_plain_line_and_keeps_the_reply_from_its_state(self):
        from Client import SolarSystemWindow
        probe.reset_for_tests()
        window = SolarSystemWindow.__new__(SolarSystemWindow)
        window._lock = threading.RLock()
        window._station_overlay = None
        window._layout = {"system_name": "Talreis Pass"}
        lines, logged = [], []
        window._send_fn = lines.append
        window.chat_log = lambda text, colour=None: logged.append(text)
        window._ps_station_cargo = ["untouched"]
        probe.handle_command(window, "/stationprobe cargo 9448")
        self.assertEqual(lines, ["PS_REQUEST_CARGO 9448"])
        window.set_ps_station_cargo({"cargo": [{"item_key": "metal_ore", "cargo_size": 5}], "hull_used": 1,
                                     "hull_space": 2})
        self.assertEqual(window._ps_station_cargo, ["untouched"])          # the probe's reply, not the game's state
        self.assertTrue(any("ANSWERED: 1 cargo row(s)" in text for text in logged))


if __name__ == "__main__":
    unittest.main()

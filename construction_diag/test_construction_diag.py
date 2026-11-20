"""construction_diag.py: log what the server sends about station construction.

Diagnostics only. The estimate of how long a Construction Bay project will take depends on fields the
client does not use today (man-hours, workers, resource costs per tick, stock), so this logs the real
messages; these tests pin what is written, that the message thread only enqueues, and that the hooks are
transparent: the original handler always runs, with the same arguments, and its result and errors pass
through untouched."""
import json
import logging
import os
import threading
import time
import unittest
from unittest import mock

import construction_diag as cd


def project(pid, name="Station Expander Alpha", qty=1, progress=0.0, paused=False, **extra):
    return {"project_id": pid, "name": name, "qty": qty, "progress": progress, "paused": paused, **extra}


def blueprint(name="Alpha", manhours=120000, with_costs=True):
    row = {"id": 7, "display_name": name, "tech": "T1", "uses": None}
    if manhours:
        row["manhours"] = manhours
    if with_costs:
        row["resource_costs"] = [{"resource": "metal", "amount": 50000, "timing": "periodic"},
                                 {"resource": "silicon", "amount": 900, "timing": "initial"}]
    return row


def bay(queue, blueprints=None, **extra):
    return {"queue": queue, "blueprints": [blueprint()] if blueprints is None else blueprints, **extra}


def cargo(sid=2083, workers_qty=58, workers_cargo_size=1215, used=34, resources=None, reserves=None, **extra):
    rows = [{"item_key": "workers", "item_category": "resource", "quantity": workers_qty,
             "cargo_size": workers_cargo_size},
            {"item_key": "item:station:vacuskin", "item_category": "station", "quantity": 1}]
    for key, amount in (resources if resources is not None else {"metal": 1000, "silicon": 50}).items():
        # as the server sends them: a resource's amount is cargo_size, and there is no quantity
        rows.append({"item_key": key, "item_category": "resource", "cargo_size": amount})
    return {"station_id": sid, "cargo": rows, "workers_used": used,
            "resource_reserves": reserves or {}, "hull_used": 10, **extra}


def joined(lines):
    return "\n".join(lines)


class BayLineTests(unittest.TestCase):
    def setUp(self):
        self.t = cd.Tracker()

    def test_the_first_message_names_the_fields_and_dumps_each_project_and_a_blueprint_in_full(self):
        data = bay([project(11, workers_assigned=40, manhours_done=1200.5)], station_id=2083)
        text = joined(self.t.bay_lines(data, 100.0))
        self.assertIn("PS_CONSTRUCTION_BAY fields: {blueprints:list[1], queue:list[1], station_id:int}", text)
        self.assertIn("queue entry fields: ", text)
        self.assertIn("manhours_done:float", text)
        self.assertIn("workers_assigned:int", text)
        self.assertIn('project 11 in full: {', text)
        self.assertIn('"workers_assigned": 40', text)
        self.assertIn("1 blueprint(s), 1 with manhours, 1 with resource_costs; one in full:", text)
        self.assertIn('"manhours": 120000', text)
        self.assertIn('"timing": "periodic"', text)
        self.assertIn("bay #1 first station=2083: 11 Station Expander Alpha x1 progress=0.0 (new)", text)

    def test_a_later_message_shows_the_gap_and_what_moved(self):
        self.t.bay_lines(bay([project(11, progress=0.10, waiting_for=None, manhours_done=100)]), 100.0)
        text = joined(self.t.bay_lines(
            bay([project(11, progress=0.1234, waiting_for="metal", manhours_done=250)]), 104.0))
        self.assertIn("bay #2 +4.0s:", text)
        self.assertIn("progress 0.1->0.1234", text)
        self.assertIn("changed: ", text)
        self.assertIn("manhours_done: 100->250", text)
        self.assertIn("waiting_for: null->metal", text)
        self.assertNotIn("in full", text)                    # nothing new to dump
        self.assertNotIn("blueprint(s)", text)
        self.assertNotIn("fields", text)

    def test_an_unchanged_project_is_marked_so(self):
        self.t.bay_lines(bay([project(11, progress=0.5)]), 100.0)
        text = joined(self.t.bay_lines(bay([project(11, progress=0.5)]), 101.0))
        self.assertIn("progress 0.5 (no change)", text)
        self.assertNotIn("changed:", text)

    def test_a_project_that_leaves_the_queue_is_reported_with_its_last_progress(self):
        self.t.bay_lines(bay([project(11, progress=0.99), project(12, name="Beta")]), 100.0)
        text = joined(self.t.bay_lines(bay([project(12, name="Beta", progress=0.2)]), 105.0))
        self.assertIn("11 Station Expander Alpha LEFT the queue at progress 0.99", text)

    def test_a_project_is_dumped_in_full_once_when_it_first_appears(self):
        self.t.bay_lines(bay([project(11)]), 100.0)
        text = joined(self.t.bay_lines(bay([project(11), project(12, name="Beta")]), 101.0))
        self.assertIn("project 12 in full", text)
        self.assertNotIn("project 11 in full", text)
        self.assertIn("12 Beta x1 progress=0.0 (new)", text)
        again = joined(self.t.bay_lines(bay([project(11), project(12, name="Beta")]), 102.0))
        self.assertNotIn("in full", again)

    def test_the_full_dumps_are_capped_per_session_but_every_project_still_gets_its_line(self):
        with mock.patch.object(cd, "MAX_FULL_DUMPS", 1):
            t = cd.Tracker()
        text = joined(t.bay_lines(bay([project(1), project(2, name="B"), project(3, name="C")]), 1.0))
        self.assertEqual(text.count("in full:"), 2)          # one project + the blueprint
        for pid in ("1 ", "2 B", "3 C"):
            self.assertIn(pid, text)

    def test_a_long_entry_is_cut_and_says_how_much_was_left_out(self):
        big = project(11, notes="x" * (cd.MAX_FULL_CHARS * 2))
        text = joined(self.t.bay_lines(bay([big]), 1.0))
        self.assertIn("... (+", text)
        self.assertLess(max(len(line) for line in text.split("\n")), cd.MAX_FULL_CHARS + 400)

    def test_paused_is_tagged_and_the_flag_change_is_listed(self):
        self.t.bay_lines(bay([project(11, progress=0.3)]), 1.0)
        text = joined(self.t.bay_lines(bay([project(11, progress=0.3, paused=True)]), 2.0))
        self.assertIn("[PAUSED]", text)
        self.assertIn("paused: false->true", text)

    def test_blueprints_are_summarised_once_with_an_example_that_has_hours_and_costs(self):
        plain = blueprint("Plain", manhours=0, with_costs=False)
        rich = blueprint("Rich", manhours=777)
        text = joined(self.t.bay_lines(bay([], blueprints=[plain, rich]), 1.0))
        self.assertIn("2 blueprint(s), 1 with manhours, 1 with resource_costs", text)
        self.assertIn('"display_name": "Rich"', text)
        self.assertNotIn('"display_name": "Plain"', text)
        self.assertNotIn("blueprint(s)", joined(self.t.bay_lines(bay([], blueprints=[plain, rich]), 2.0)))

    def test_an_empty_queue_says_so_and_notes_the_fields_are_not_known_yet(self):
        text = joined(self.t.bay_lines(bay([], blueprints=[]), 1.0))
        self.assertIn("(queue empty so far)", text)
        self.assertIn("(none)", text)
        self.assertIn("bay #1 first: queue empty", text)

    def test_a_new_top_level_field_is_called_out(self):
        self.t.bay_lines(bay([project(11)]), 1.0)
        text = joined(self.t.bay_lines(bay([project(11)], workers_free=12), 2.0))
        self.assertIn("fields ADDED: top-level {workers_free:int}", text)
        self.assertNotIn("queue entry", text)

    def test_fields_that_first_show_up_with_the_first_project_are_announced_as_added(self):
        self.t.bay_lines(bay([]), 1.0)
        text = joined(self.t.bay_lines(bay([project(11, workers_assigned=40)]), 2.0))
        self.assertIn("fields ADDED: queue entry {", text)
        self.assertIn("workers_assigned:int", text)
        self.assertNotIn("top-level", text)

    def test_a_queue_that_empties_and_refills_is_not_announced_again(self):
        self.t.bay_lines(bay([project(11)]), 1.0)
        self.t.bay_lines(bay([]), 2.0)
        self.assertNotIn("fields", joined(self.t.bay_lines(bay([project(11)]), 3.0)))

    def test_a_field_flipping_between_null_and_a_value_is_not_a_new_field(self):
        self.t.bay_lines(bay([project(11, waiting_for=None)]), 1.0)
        self.assertNotIn("fields", joined(self.t.bay_lines(bay([project(11, waiting_for="metal")]), 2.0)))

    def test_a_blueprint_field_that_appears_later_is_announced(self):
        self.t.bay_lines(bay([], blueprints=[blueprint(manhours=0, with_costs=False)]), 1.0)
        text = joined(self.t.bay_lines(bay([], blueprints=[blueprint()]), 2.0))
        self.assertIn("fields ADDED: blueprint {", text)
        self.assertIn("manhours:int", text)

    def test_odd_payloads_never_raise(self):
        self.assertIn("not an object", joined(self.t.bay_lines(["x"], 1.0)))
        self.assertIn("not an object", joined(self.t.bay_lines(None, 2.0)))
        lines = self.t.bay_lines({"queue": [1, "x", project(5)], "blueprints": "none"}, 3.0)
        self.assertIn("5 Station Expander Alpha", joined(lines))

    def test_many_changed_fields_are_listed_up_to_a_limit(self):
        before = project(11, **{f"f{i}": 0 for i in range(10)})
        after = project(11, **{f"f{i}": 1 for i in range(10)})
        self.t.bay_lines(bay([before]), 1.0)
        text = joined(self.t.bay_lines(bay([after]), 2.0))
        self.assertIn("+4 more", text)


class CargoLineTests(unittest.TestCase):
    def setUp(self):
        self.t = cd.Tracker()

    def test_workers_and_every_resource_are_summarised_and_other_rows_ignored(self):
        text = joined(self.t.cargo_lines(cargo(), 50.0))
        self.assertIn("PS_STATION_CARGO fields: {", text)
        self.assertIn("cargo #1 first station=2083: workers qty=58 cargo_size=1215 used=34", text)
        self.assertIn("stock metal=1000, silicon=50", text)
        self.assertNotIn("vacuskin", text)
        self.assertNotIn("workers=", text)                   # workers are not a stock entry

    def test_the_second_message_gives_each_resources_change_and_the_seconds_it_took(self):
        self.t.cargo_lines(cargo(resources={"metal": 1000, "silicon": 50, "oil": 7}), 50.0)
        text = joined(self.t.cargo_lines(cargo(resources={"metal": 1500, "silicon": 30, "oil": 7}), 60.0))
        self.assertIn("+10.0s", text)
        self.assertIn("changed over 10.0s: metal +500, silicon -20", text)
        self.assertNotIn("oil +", text)
        self.assertNotIn("PS_STATION_CARGO fields", text)

    def test_the_stations_credits_are_tracked_like_a_resource(self):
        self.t.cargo_lines(cargo(station_credits=122_402_974.0), 50.0)
        text = joined(self.t.cargo_lines(cargo(station_credits=122_402_374.0), 60.0))
        self.assertIn("credits=122402374", text)
        self.assertIn("credits -600", text)
        self.assertNotIn("credits", joined(self.t.cargo_lines(cargo(), 70.0)).split("stock")[0])

    def test_no_change_is_said_plainly(self):
        self.t.cargo_lines(cargo(), 50.0)
        self.assertIn("no stock change over 5.0s", joined(self.t.cargo_lines(cargo(), 55.0)))

    def test_cargo_messages_closer_than_the_gap_are_counted_but_not_logged(self):
        self.assertTrue(self.t.cargo_lines(cargo(resources={"metal": 100}), 50.0))
        self.assertEqual(self.t.cargo_lines(cargo(resources={"metal": 150}), 51.0), [])
        self.assertEqual(self.t.cargo_lines(cargo(resources={"metal": 190}), 54.9), [])
        text = joined(self.t.cargo_lines(cargo(resources={"metal": 260}), 55.0))
        self.assertIn("cargo #4 +5.0s", text)                  # numbered by messages received, not lines written
        self.assertIn("changed over 5.0s: metal +160", text)   # measured from the last line written

    def test_the_gap_is_per_station(self):
        self.t.cargo_lines(cargo(sid=1), 50.0)
        self.assertTrue(self.t.cargo_lines(cargo(sid=2), 50.5))
        self.assertEqual(self.t.cargo_lines(cargo(sid=1), 51.0), [])

    def test_a_new_field_is_still_announced_inside_the_gap(self):
        self.t.cargo_lines(cargo(), 50.0)
        text = joined(self.t.cargo_lines(cargo(construction_completed=[]), 51.0))
        self.assertIn("PS_STATION_CARGO fields ADDED: {construction_completed:list[0]}", text)
        self.assertNotIn("cargo #2", text)

    def test_stations_are_tracked_separately(self):
        self.t.cargo_lines(cargo(sid=1, resources={"metal": 10}), 50.0)
        text = joined(self.t.cargo_lines(cargo(sid=2, resources={"metal": 99}), 51.0))
        self.assertIn("cargo #2 first station=2:", text)
        self.assertNotIn("changed over", text)

    def test_reserves_are_shown_when_there_are_any(self):
        text = joined(self.t.cargo_lines(cargo(reserves={"metal": 5000}), 1.0))
        self.assertIn('reserves {"metal": 5000}', text)
        self.assertNotIn("reserves", joined(self.t.cargo_lines(cargo(), 12.0)))

    def test_a_new_cargo_field_is_announced_once(self):
        self.t.cargo_lines(cargo(), 1.0)
        text = joined(self.t.cargo_lines(cargo(construction_workers=12), 12.0))
        self.assertIn("PS_STATION_CARGO fields ADDED: {construction_workers:int}", text)
        self.assertNotIn("fields ADDED", joined(self.t.cargo_lines(cargo(construction_workers=13), 22.0)))

    def test_a_resources_stock_is_its_cargo_size_and_quantity_is_only_a_fallback(self):
        data = cargo(resources={"metal": 210267})
        data["cargo"].append({"item_key": "oil", "item_category": "resource", "quantity": 30})
        data["cargo"].append({"item_key": "wood", "item_category": "resource", "cargo_size": 816, "quantity": 2})
        text = joined(self.t.cargo_lines(data, 1.0))
        self.assertIn("metal=210267", text)
        self.assertIn("oil=30", text)
        self.assertIn("wood=816", text)

    def test_no_workers_row_is_stated(self):
        data = cargo()
        data["cargo"] = [r for r in data["cargo"] if r["item_key"] != "workers"]
        self.assertIn("no workers row used=34", joined(self.t.cargo_lines(data, 1.0)))

    def test_odd_numbers_and_payloads_never_raise(self):
        data = cargo(resources={"metal": "lots"}, workers_qty=None)
        self.assertIn("metal=0", joined(self.t.cargo_lines(data, 1.0)))
        self.assertIn("not an object", joined(self.t.cargo_lines("x", 2.0)))
        self.assertTrue(self.t.cargo_lines({"cargo": "bad"}, 30.0))


class LineLimitTests(unittest.TestCase):
    def test_after_the_cap_one_notice_is_written_and_then_nothing(self):
        with mock.patch.object(cd, "MAX_LINES", 3):
            t = cd.Tracker()
        out = t.limited(["a", "b", "c", "d", "e"])
        self.assertEqual(out[:3], ["a", "b", "c"])
        self.assertIn("line limit (3) reached", out[3])
        self.assertEqual(len(out), 4)
        self.assertEqual(t.limited(["f", "g"]), [])


def _wait_idle(timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cd._queue.unfinished_tasks == 0:
            return True
        time.sleep(0.01)
    return False


class _Proto:
    """Stands in for protocol.ProtocolMixin: two handlers that record what they were called with."""

    def __init__(self):
        self.calls = []

    def _on_ps_construction_bay(self, payload):
        self.calls.append(("bay", payload))
        return "bay-result"

    def _on_ps_station_cargo(self, payload):
        self.calls.append(("cargo", payload))
        return "cargo-result"

    def _other(self):
        return 1


class _Fixture(unittest.TestCase):
    def setUp(self):
        cd.reset_for_tests()
        self.addCleanup(cd.reset_for_tests)
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(cd.ENV_OFF, None)
        self.cls = type("Proto", (_Proto,), {})
        self.cls._on_ps_construction_bay = _Proto._on_ps_construction_bay
        self.cls._on_ps_station_cargo = _Proto._on_ps_station_cargo


class HookTests(_Fixture):
    def test_install_hooks_the_two_handlers_once(self):
        self.assertTrue(cd.install(self.cls))
        first = (self.cls.__dict__["_on_ps_construction_bay"], self.cls.__dict__["_on_ps_station_cargo"])
        self.assertFalse(cd.install(self.cls))
        self.assertIs(self.cls.__dict__["_on_ps_construction_bay"], first[0])
        self.assertTrue(all(hasattr(f, "__wrapped__") for f in first))
        self.assertFalse(hasattr(self.cls._other, "__wrapped__"))      # nothing else is touched

    def test_the_original_runs_with_the_same_arguments_and_its_result_passes_through(self):
        cd.install(self.cls)
        obj = self.cls()
        self.assertEqual(obj._on_ps_construction_bay("{}"), "bay-result")
        self.assertEqual(obj._on_ps_station_cargo(payload="{}"), "cargo-result")
        self.assertEqual(obj.calls, [("bay", "{}"), ("cargo", "{}")])

    def test_extra_positional_and_keyword_arguments_reach_the_original_untouched(self):
        seen = []

        def handler(self, payload, extra=None, *rest, flag=False):
            seen.append((payload, extra, rest, flag))
            return "ok"

        self.cls._on_ps_construction_bay = handler
        cd.install(self.cls)
        obj = self.cls()
        self.assertEqual(obj._on_ps_construction_bay("{}", 5, 6, flag=True), "ok")
        self.assertEqual(obj._on_ps_construction_bay("{}", extra=7), "ok")
        self.assertEqual(seen, [("{}", 5, (6,), True), ("{}", 7, (), False)])

    def test_an_error_in_the_original_still_reaches_the_caller_and_the_message_is_still_logged(self):
        def boom(self, payload):
            raise RuntimeError("handler failed")
        self.cls._on_ps_construction_bay = boom
        cd.install(self.cls)
        with self.assertLogs(cd.logger, "INFO") as logs:
            with self.assertRaises(RuntimeError):
                self.cls()._on_ps_construction_bay(json.dumps(bay([project(11)])))
            self.assertTrue(_wait_idle())
        self.assertIn("project 11 in full", "\n".join(logs.output))

    def test_the_message_thread_only_enqueues_the_parsing_happens_on_the_worker(self):
        seen = []
        real = json.loads

        def spy(text, *a, **k):
            seen.append(threading.current_thread().name)
            return real(text, *a, **k)

        cd.install(self.cls)
        with mock.patch.object(cd.json, "loads", spy):
            self.cls()._on_ps_construction_bay(json.dumps(bay([project(11)])))
            self.assertTrue(_wait_idle())
        self.assertEqual(seen, ["construction-diag"])          # parsed once, and not by this thread

    def test_lines_are_logged_for_both_kinds_with_the_prefix(self):
        cd.install(self.cls)
        obj = self.cls()
        with self.assertLogs(cd.logger, "INFO") as logs:
            obj._on_ps_construction_bay(json.dumps(bay([project(11, progress=0.25)])))
            obj._on_ps_station_cargo(json.dumps(cargo()))
            self.assertTrue(_wait_idle())
        self.assertTrue(all(line.split(":", 2)[2].startswith("construction_diag: ") for line in logs.output),
                        logs.output)
        text = "\n".join(logs.output)
        self.assertIn("bay #1 first", text)
        self.assertIn("cargo #1 first station=2083", text)

    def test_switched_off_by_the_environment_nothing_is_queued_but_the_game_still_works(self):
        os.environ[cd.ENV_OFF] = "1"
        cd.install(self.cls)
        obj = self.cls()
        with self.assertNoLogs(cd.logger, "INFO"):
            self.assertEqual(obj._on_ps_construction_bay(json.dumps(bay([project(11)]))), "bay-result")
            time.sleep(0.1)
        self.assertEqual(cd._queue.qsize(), 0)
        self.assertIsNone(cd._thread)

    def test_garbage_payloads_are_ignored_quietly(self):
        cd.install(self.cls)
        obj = self.cls()
        with self.assertNoLogs(cd.logger, "WARNING"):
            obj._on_ps_construction_bay("not json at all")
            obj._on_ps_station_cargo(None)
            obj._on_ps_construction_bay(b"\xff\xfe")
            obj._on_ps_construction_bay({"queue": []})            # already a dict: used as is
            self.assertTrue(_wait_idle())

    def test_a_full_queue_drops_messages_without_blocking_and_says_so_once(self):
        gate = threading.Event()
        real = cd._tracker.bay_lines

        def slow(data, now):
            gate.wait(5)
            return real(data, now)

        cd.install(self.cls)
        obj = self.cls()
        payload = json.dumps(bay([project(11)]))
        with mock.patch.object(cd._tracker, "bay_lines", slow):
            began = time.perf_counter()
            for _ in range(cd.QUEUE_SIZE + 25):
                obj._on_ps_construction_bay(payload)
            self.assertLess(time.perf_counter() - began, 1.0)
            self.assertGreater(cd._dropped, 0)
            with self.assertLogs(cd.logger, "INFO") as logs:
                gate.set()
                self.assertTrue(_wait_idle())
        dropped = [line for line in logs.output if "were dropped because the queue was full" in line]
        self.assertEqual(len(dropped), 1, logs.output)

    def test_an_error_while_describing_switches_it_off_for_the_session_and_never_touches_the_game(self):
        cd.install(self.cls)
        obj = self.cls()
        with mock.patch.object(cd._tracker, "bay_lines", side_effect=ValueError("bad shape")):
            with self.assertLogs(cd.logger, "ERROR"):
                self.assertEqual(obj._on_ps_construction_bay(json.dumps(bay([]))), "bay-result")
                self.assertTrue(_wait_idle())
        self.assertTrue(cd._failed)
        with self.assertNoLogs(cd.logger, "INFO"):
            self.assertEqual(obj._on_ps_construction_bay(json.dumps(bay([project(1)]))), "bay-result")
            time.sleep(0.1)

    def test_the_worker_is_started_lazily_and_only_once(self):
        cd.install(self.cls)
        self.assertIsNone(cd._thread)
        obj = self.cls()
        for _ in range(5):
            obj._on_ps_construction_bay(json.dumps(bay([])))
        self.assertTrue(_wait_idle())
        self.assertEqual([t.name for t in threading.enumerate()].count("construction-diag"), 1)


class RealProtocolTests(unittest.TestCase):
    def setUp(self):
        cd.reset_for_tests()
        self.addCleanup(cd.reset_for_tests)

    def test_importing_the_client_hooks_the_real_handlers(self):
        import Client  # noqa: F401  (the install block at the bottom of Client.py runs on import)
        import protocol
        for name in ("_on_ps_construction_bay", "_on_ps_station_cargo"):
            self.assertTrue(getattr(protocol.ProtocolMixin.__dict__[name], "_construction_diag", False), name)

    def test_the_real_bay_handler_still_hands_the_data_to_the_window_and_logs_it(self):
        import Client  # noqa: F401
        import protocol
        received = []
        window = type("W", (), {"set_ps_construction_bay": lambda self, data: received.append(data)})()
        stand_in = type("S", (), {"_solar_window": window})()
        message = bay([project(11, progress=0.5, workers_assigned=3)])
        with self.assertLogs(cd.logger, "INFO") as logs:
            protocol.ProtocolMixin._on_ps_construction_bay(stand_in, json.dumps(message))
            self.assertTrue(_wait_idle())
        self.assertEqual(received, [message])
        self.assertIn("workers_assigned", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()

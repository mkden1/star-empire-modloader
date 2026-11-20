"""station_overview: the STATIONS tab's model - last-docked snapshots joined with the bot activity numbers and the
bots' current pickup orders."""
import unittest

import station_overview as so

NOW = 1_800_000_000.0
DAY = 86400.0

DRILL = {"name": "Industrial Metal Drill", "quantity": 10,
         "stats": {"Cycle Output": "3 Metal Ore", "Cycle Time": "10 seconds"}}        # 25,920 a day each
CORN = {"name": "Industrial Harvester", "quantity": 2,
        "stats": {"Cycle Output": "3 Space Corn", "Cycle Time": "10 seconds"}}
CONVERTER = {"name": "Ration Processor", "quantity": 5,
             "stats": {"Cycle Output": "1 Rations", "Cycle Time": "10 seconds", "Cycle Input": "2 Space Corn"}}
SHIELD = {"name": "Station Shield Mk III", "quantity": 1, "stats": {}}


BODIES = [{"system": "Zordoil Frontier", "planet_name": "Cryldorwen", "parent_planet": "", "stations": 3,
           "resources": {"metal_ore": 10, "space_corn": 2, "copper": 0}}]


def profile(sid="2105", age_days=0.5, **extra):
    base = {"station_id": sid, "seen_at": NOW - age_days * DAY, "station_name": "cryldorwen - 1",
            "planet_id": 32627, "body_name": "Cryldorwen", "body_kind": "planet", "body_parent": None,
            "system_name": "Zordoil Frontier", "owner_name": "ExamplePlayer", "taken_by": "ExamplePlayer", "docking": "officer",
            "management": "owner", "motd": "hi", "has_colony": True, "flags": {"trade_enabled": True},
            "resources": {"metal_ore": 1000, "space_corn": 100, "workers": 40}, "resource_caps": {"metal_ore": 50000,
                                                                                                     "space_corn": 50000},
            "resource_reserves": {}, "credits": 99.0, "workers_used": 3, "hull_used": 1000, "hull_space": 100000,
            "modules": [DRILL, CORN, CONVERTER, SHIELD], "trade_entries": [{"item_type": "metal"}] * 2,
            "trade_listed": 4, "construction": {"projects": [], "blueprints": 1}}
    base.update(extra)
    return base


# Stock that is piling up, not being used on the spot: for tests of what leaves, where the default 1,000 of a 50,000 cap
# would (rightly) read as output the station consumes itself.
PILING = {"metal_ore": 40000, "space_corn": 40000, "workers": 40}


def balance(pct, bought=0.0, shuttled=0.0):
    return {"production_per_day": 1.0, "bought_per_day": bought, "shuttled_per_day": shuttled, "shuttled_to": [],
            "pct_satisfied": pct}


def activity(sid="2105", name="cryldorwen - 1", **balances):
    return {"stations": {name: {"station_id": sid, "system": "Zordoil Frontier", "bot_docks": 10, "unique_bots": 2,
                                "shuttle_covered": False, "resource_balance": balances,
                                "resources": {"metal_ore": 5, "space_corn": 5}, "resource_caps": {},
                                "resource_production_per_day": {"metal_ore": 259200.0, "space_corn": 51840.0},
                                "hull_used": 10, "hull_space": 100}}}


def bot(key, orders, nickname="Zor-C1", cargo=20000, account="ExamplePlayer", captured=1.0):
    data = None if orders is None else {"nickname": nickname, "ship_type": "CosmicCaravan", "bot_cargo_cap": cargo,
                                        "state": "hauling", "system_name": "Zordoil Frontier", "haul_orders": orders}
    return {"key": key, "captured_at": captured, "context": {"username": account}, "data": data}


def dock(sid):
    return {"type": "dock", "station_id": sid, "mode": "LOAD", "item_key": ""}


def trade(sid, mode, item):
    return {"type": "trade", "station_id": sid, "mode": mode, "item_key": item, "amount": "all"}


def one(profiles=None, act=None, fleet=None, sid="2105", bodies=BODIES):
    out = so.station_overview(profiles, act, fleet, now=NOW, bodies=bodies)
    return next(s for s in out["stations"] if s["id"] == sid)


class RouteTests(unittest.TestCase):
    def test_only_pickups_are_routes(self):
        orders = [dock("2105"), trade("2105", "BUY", "metal_ore"), trade("2105", "LOAD", "space_corn"),
                  trade("9448", "SELL", "metal_ore"), trade("9448", "UNLOAD", "metal_ore"),
                  {"type": "warp", "station_id": "", "mode": "LOAD", "item_key": ""}]
        routes = so.routes_from_fleet([bot("1", orders)])
        self.assertEqual(sorted(routes), [("2105", "metal_ore"), ("2105", "space_corn")])
        self.assertEqual(routes[("2105", "metal_ore")][0]["nickname"], "Zor-C1")
        self.assertEqual(routes[("2105", "metal_ore")][0]["stops"], 1)

    def test_a_bots_newest_row_wins_and_an_empty_row_removes_it(self):
        rows = [bot("1", [trade("2105", "BUY", "wood")], captured=1.0),
                bot("1", [trade("2105", "BUY", "metal_ore")], captured=2.0),
                bot("2", [trade("2106", "BUY", "wood")], captured=1.0), bot("2", None, captured=2.0)]
        routes = so.routes_from_fleet(rows)
        self.assertEqual(sorted(routes), [("2105", "metal_ore")])

    def test_an_order_that_repeats_counts_the_bot_once(self):
        routes = so.routes_from_fleet([bot("1", [trade("2105", "BUY", "x"), trade("2105", "BUY", "x")])])
        self.assertEqual(len(routes[("2105", "x")]), 1)

    def test_junk_is_ignored(self):
        self.assertEqual(so.routes_from_fleet([None, "x", {"key": "", "data": {}}, bot("1", [None, "o"])]), {})
        self.assertEqual(so.routes_from_fleet(None), {})


class ProductionTests(unittest.TestCase):
    def test_extractors_count_and_converters_and_gear_do_not(self):
        stations = so.station_overview({"2105": profile()}, None, None, now=NOW)["stations"]
        rows = {r["key"]: r for r in stations[0]["resources"]}
        self.assertAlmostEqual(rows["metal_ore"]["production_per_day"], 259200.0)
        self.assertAlmostEqual(rows["space_corn"]["production_per_day"], 51840.0)
        self.assertNotIn("rations", rows)
        self.assertEqual(stations[0]["extractors"], 12)
        self.assertEqual([m["name"] for m in stations[0]["modules"]],
                         ["Industrial Metal Drill", "Ration Processor", "Industrial Harvester", "Station Shield Mk III"])

    def test_without_a_snapshot_the_scans_production_is_used(self):
        station = one(None, activity(metal_ore=balance(100.0)))
        self.assertEqual(station["source"], "scan")
        rows = {r["key"]: r for r in station["resources"]}
        self.assertEqual(rows["metal_ore"]["production_per_day"], 259200.0)
        self.assertEqual(station["extractors"], 0)


class MergeTests(unittest.TestCase):
    def test_a_snapshot_and_a_scan_of_one_station_are_one_station(self):
        out = so.station_overview({2105: profile()}, activity(metal_ore=balance(80.0)), None, now=NOW)
        self.assertEqual(len(out["stations"]), 1)
        station = out["stations"][0]
        self.assertEqual((station["id"], station["source"], station["name"]), ("2105", "profile", "cryldorwen - 1"))
        self.assertEqual((station["docking"], station["management"], station["owner"]), ("officer", "owner", "ExamplePlayer"))
        self.assertEqual((station["credits"], station["workers"], station["workers_used"]), (99.0, 40, 3))
        self.assertEqual((station["trade_entries"], station["trade_listed"]), (2, 4))
        self.assertAlmostEqual(station["age_days"], 0.5)
        self.assertEqual(station["hull_fill_pct"], 1.0)
        self.assertEqual(station["bot_docks"], 10)

    def test_the_name_in_the_snapshot_is_the_one_shown(self):
        # a station renamed since the scan: the snapshot is newer than the activity data's key
        out = so.station_overview({"2105": profile(station_name="renamed - 1")}, activity(), None, now=NOW)
        self.assertEqual(out["stations"][0]["name"], "renamed - 1")

    def test_a_station_known_only_from_the_logs_is_listed_with_its_name_and_system(self):
        out = so.station_overview(None, activity(), None, station_names={"2105": "ignored"}, now=NOW)
        station = out["stations"][0]
        self.assertEqual((station["name"], station["system"], station["source"]),
                         ("cryldorwen - 1", "Zordoil Frontier", "scan"))

    def test_names_and_systems_fall_back_to_the_id_maps(self):
        out = so.station_overview({"7": profile("7", station_name="", system_name="")}, None, None,
                                  station_names={"7": "From map"}, station_systems={"7": "Mapped Sound"}, now=NOW)
        self.assertEqual((out["stations"][0]["name"], out["stations"][0]["system"]), ("From map", "Mapped Sound"))
        bare = so.station_overview({"8": profile("8", station_name="", system_name="")}, None, None, now=NOW)
        self.assertEqual(bare["stations"][0]["name"], "Station 8")

    def test_nothing_in_nothing_out(self):
        out = so.station_overview(None, None, None, now=NOW)
        self.assertEqual((out["stations"], out["systems"], out["resources"]), ([], [], []))
        self.assertEqual(out["generated_at"], NOW)

    def test_stations_are_ordered_by_id_numerically(self):
        out = so.station_overview({"10": profile("10"), "9": profile("9"), "100": profile("100")}, None, None, now=NOW)
        self.assertEqual([s["id"] for s in out["stations"]], ["9", "10", "100"])


class StatusTests(unittest.TestCase):
    def rows(self, pct, routed=False, sid="2105"):
        fleet = [bot("1", [trade(sid, "BUY", "metal_ore")])] if routed else None
        act = activity(metal_ore=balance(pct)) if pct is not None else activity()
        station = one({sid: profile(sid, resources=PILING)}, act, fleet, sid)
        return {r["key"]: r for r in station["resources"]}

    def test_the_status_follows_how_much_of_the_production_leaves(self):
        self.assertEqual(self.rows(100.0)["metal_ore"]["status"], "ok")
        self.assertEqual(self.rows(90.0)["metal_ore"]["status"], "ok")
        self.assertEqual(self.rows(89.9)["metal_ore"]["status"], "partial")
        self.assertEqual(self.rows(50.0)["metal_ore"]["status"], "partial")
        self.assertEqual(self.rows(49.9)["metal_ore"]["status"], "low")
        self.assertEqual(self.rows(5.0)["metal_ore"]["status"], "low")
        self.assertEqual(self.rows(4.9)["metal_ore"]["status"], "no-route")

    def test_a_route_that_takes_nothing_is_idle_not_missing(self):
        self.assertEqual(self.rows(0.0, routed=True)["metal_ore"]["status"], "idle")

    def test_no_log_data_is_unknown_never_no_route(self):
        self.assertEqual(self.rows(None)["metal_ore"]["status"], "unknown")
        self.assertEqual(self.rows(None, routed=True)["metal_ore"]["status"], "routed")

    def test_a_resource_that_is_only_held_has_stock_status(self):
        station = one({"2105": profile(resources={"gold": 5})}, activity(metal_ore=balance(100.0)))
        rows = {r["key"]: r for r in station["resources"]}
        self.assertEqual(rows["gold"]["status"], "stock")

    def test_routes_and_fill_are_attached_to_the_resource(self):
        fleet = [bot("1", [dock("2105"), trade("2105", "BUY", "metal_ore")], nickname="A"),
                 bot("2", [trade("2105", "LOAD", "")], nickname="B"), bot("3", [trade("2105", "SELL", "metal_ore")])]
        station = one({"2105": profile()}, activity(metal_ore=balance(60.0, bought=5.0, shuttled=7.0)), fleet)
        row = {r["key"]: r for r in station["resources"]}["metal_ore"]
        self.assertEqual(sorted(r["nickname"] for r in row["routes"]), ["A", "B"])    # B loads anything
        self.assertEqual((row["stock"], row["cap"], row["fill_pct"]), (1000, 50000, 2.0))
        self.assertEqual((row["bought_per_day"], row["shuttled_per_day"], row["pct_satisfied"]), (5.0, 7.0, 60.0))

    def test_workers_are_not_a_resource_row(self):
        station = one({"2105": profile()}, None)
        self.assertNotIn("workers", [r["key"] for r in station["resources"]])


class IssueTests(unittest.TestCase):
    def codes(self, station, severity=None):
        return {(i["code"], i["severity"]) for i in station["issues"] if severity in (None, i["severity"])}

    def test_a_missing_snapshot_is_info_and_an_old_one_is_a_warning_then_bad(self):
        self.assertIn(("no-snapshot", "info"), self.codes(one(None, activity())))
        self.assertNotIn("no-snapshot", {c for c, _ in self.codes(one({"2105": profile()}, None))})
        self.assertIn(("stale", "warn"), self.codes(one({"2105": profile(age_days=3)}, None)))
        self.assertNotIn("stale", {c for c, _ in self.codes(one({"2105": profile(age_days=2.9)}, None))})
        self.assertIn(("very-stale", "bad"), self.codes(one({"2105": profile(age_days=14)}, None)))

    def test_a_full_hull_is_bad_from_a_snapshot_and_info_from_an_old_scan(self):
        full = one({"2105": profile(hull_used=95000)}, None)
        self.assertIn(("backed-up", "bad"), self.codes(full))
        scan = activity()
        scan["stations"]["cryldorwen - 1"].update(hull_used=95, hull_space=100)
        self.assertIn(("backed-up", "info"), self.codes(one(None, scan)))

    def test_a_resource_at_its_cap_warns_from_a_snapshot_only(self):
        station = one({"2105": profile(resources={"metal_ore": 49000})}, activity(metal_ore=balance(100.0)))
        self.assertIn(("at-cap", "warn"), self.codes(station))
        old = activity(metal_ore=balance(100.0))
        old["stations"]["cryldorwen - 1"].update(resources={"metal_ore": 49000}, resource_caps={"metal_ore": 50000})
        self.assertIn(("at-cap", "info"), self.codes(one(None, old)))
        self.assertIn("older scan", [i for i in one(None, old)["issues"] if i["code"] == "at-cap"][0]["text"])

    def test_a_produced_resource_nobody_takes_is_bad_but_a_trickle_is_only_info(self):
        trickle = {"name": "Copper Extractor", "quantity": 2,
                   "stats": {"Cycle Output": "1 Copper", "Cycle Time": "10000 seconds"}}
        station = one({"2105": profile(modules=[DRILL, trickle], resources=PILING)},
                      activity(metal_ore=balance(0.0), copper=balance(0.0)))
        by_resource = {i["resource"]: i["severity"] for i in station["issues"] if i["code"] == "no-route"}
        self.assertEqual(by_resource, {"metal_ore": "bad", "copper": "info"})

    def test_idle_and_low_coverage_warn(self):
        fleet = [bot("1", [trade("2105", "BUY", "metal_ore")])]
        idle = one({"2105": profile(resources=PILING)}, activity(metal_ore=balance(0.0)), fleet)
        self.assertIn(("idle-route", "warn"), self.codes(idle))
        low = one({"2105": profile()}, activity(metal_ore=balance(30.0)), fleet)
        self.assertIn(("low-coverage", "warn"), self.codes(low))

    def test_no_log_data_is_one_info_line_and_never_a_route_complaint(self):
        station = one({"2105": profile()}, activity())
        self.assertIn(("no-log-data", "info"), self.codes(station))
        self.assertNotIn("no-route", {c for c, _ in self.codes(station)})
        text = [i["text"] for i in station["issues"] if i["code"] == "no-log-data"][0]
        self.assertIn("Metal Ore", text)

    def test_a_healthy_station_has_no_issues(self):
        station = one({"2105": profile()}, activity(metal_ore=balance(100.0), space_corn=balance(95.0)))
        self.assertEqual(station["issues"], [])


def quiet_activity(rows=30, docks=0, **balances):
    """The log of a station was fetched (``rows`` entries) and shows ``docks`` bot docks."""
    act = activity(**balances)
    act["stations"]["cryldorwen - 1"].update(bot_docks=docks, log_rows=rows)
    return act


class QuietStationTests(unittest.TestCase):
    """A station whose log was fetched and shows no bot docking is known to lose nothing to bots - it is not "unknown"."""

    def status(self, act, fleet=None, **profile_extra):
        station = one({"2105": profile(resources=PILING, **profile_extra)}, act, fleet)
        return {r["key"]: r for r in station["resources"]}["metal_ore"]["status"]

    def test_a_fetched_log_with_no_bot_dock_means_nothing_leaves(self):
        self.assertEqual(self.status(quiet_activity()), "no-route")

    def test_with_a_route_it_is_idle(self):
        self.assertEqual(self.status(quiet_activity(), [bot("1", [trade("2105", "LOAD", "metal_ore")])]), "idle")

    def test_a_log_that_was_never_fetched_stays_unknown(self):
        self.assertEqual(self.status(quiet_activity(rows=0)), "unknown")

    def test_a_single_bot_dock_has_no_rate_and_stays_unknown(self):
        self.assertEqual(self.status(quiet_activity(docks=1)), "unknown")

    def test_the_issue_says_how_much_log_there_is_and_there_is_no_log_data_complaint(self):
        station = one({"2105": profile(resources=PILING)}, quiet_activity(rows=1234))
        codes = {i["code"] for i in station["issues"]}
        self.assertIn("no-route", codes)
        self.assertNotIn("no-log-data", codes)
        text = [i["text"] for i in station["issues"] if i["code"] == "no-route"][0]
        self.assertIn("Its log (1,234 entries) shows no bot ever docking.", text)
        busy = one({"2105": profile(resources=PILING)}, activity(metal_ore=balance(0.0)))
        self.assertNotIn("Its log", [i["text"] for i in busy["issues"] if i["code"] == "no-route"][0])

    def test_the_log_row_count_is_carried_on_the_station(self):
        self.assertEqual(one({"2105": profile()}, quiet_activity(rows=77))["log_rows"], 77)
        self.assertIsNone(one({"2105": profile()}, None)["log_rows"])


class UsedOnSiteTests(unittest.TestCase):
    """Stock far below the cap at a station that makes its whole cap in a day or two, with nothing leaving: it is
    consumed on the spot (a hub's foundries), not an unserved resource."""

    def row(self, stock=1000, cap=50000, pct=0.0, key="metal_ore", **kwargs):
        station = one({"2105": profile(resources={key: stock}, resource_caps={key: cap})},
                      activity(**{key: balance(pct)}), **kwargs)
        return {r["key"]: r for r in station["resources"]}[key]

    def test_low_stock_and_a_big_output_is_used_on_site(self):
        self.assertEqual(self.row()["status"], "used")

    def test_a_quarter_full_or_more_is_not(self):
        self.assertEqual(self.row(stock=12400)["status"], "used")          # 24.8%
        self.assertEqual(self.row(stock=12500)["status"], "no-route")      # 25.0%

    def test_an_output_that_would_take_days_to_fill_the_cap_proves_nothing(self):
        # 259,200 a day: exactly half the cap fills it in two days (used); a hair more cap does not.
        self.assertEqual(self.row(cap=518400)["status"], "used")
        self.assertEqual(self.row(cap=518401)["status"], "no-route")

    def test_something_leaving_is_not_used_on_site(self):
        self.assertEqual(self.row(pct=5.0)["status"], "low")
        self.assertEqual(self.row(pct=95.0)["status"], "ok")

    def test_a_scan_is_never_called_used_because_its_stock_may_be_old(self):
        old = activity(metal_ore=balance(0.0))
        old["stations"]["cryldorwen - 1"].update(resources={"metal_ore": 1000}, resource_caps={"metal_ore": 50000})
        station = one(None, old)
        self.assertEqual({r["key"]: r for r in station["resources"]}["metal_ore"]["status"], "no-route")

    def test_no_cap_means_no_inference(self):
        station = one({"2105": profile(resource_caps={})}, activity(metal_ore=balance(0.0)))
        self.assertEqual({r["key"]: r for r in station["resources"]}["metal_ore"]["status"], "no-route")

    def test_a_used_resource_raises_no_route_or_idle_issue(self):
        fleet = [bot("1", [trade("2105", "BUY", "metal_ore")])]
        for flt in (None, fleet):
            station = one({"2105": profile()}, activity(metal_ore=balance(0.0), space_corn=balance(0.0)), flt)
            self.assertEqual([i for i in station["issues"] if i["code"] in ("no-route", "idle-route", "low-coverage")], [])

    def test_a_quiet_station_with_low_stock_is_used_too(self):
        station = one({"2105": profile()}, quiet_activity())
        self.assertEqual({r["key"]: r for r in station["resources"]}["metal_ore"]["status"], "used")

    def test_used_rows_and_unknown_rows_do_not_count_towards_coverage(self):
        # metal ore is used on site and corn is fully hauled: only the corn counts.
        mixed = one({"2105": profile(resources={"metal_ore": 1000, "space_corn": 40000})},
                    activity(metal_ore=balance(0.0), space_corn=balance(100.0)))
        self.assertEqual(mixed["coverage_pct"], 100.0)
        # corn has no log figure at all: only the ore, which is hauled in full, counts
        unknown = one({"2105": profile(resources={"metal_ore": 40000, "space_corn": 40000})},
                      activity(metal_ore=balance(100.0)))
        self.assertEqual(unknown["coverage_pct"], 100.0)
        # corn has a route but no log figure: "routed" is just as unknown, so it is left out as well
        routed = one({"2105": profile(resources={"metal_ore": 40000, "space_corn": 40000})},
                     activity(metal_ore=balance(100.0)), [bot("1", [trade("2105", "BUY", "space_corn")])])
        self.assertEqual({r["key"]: r for r in routed["resources"]}["space_corn"]["status"], "routed")
        self.assertEqual(routed["coverage_pct"], 100.0)
        # nothing left that is a hauling question
        self.assertIsNone(one({"2105": profile(resources={"metal_ore": 1000, "space_corn": 100})},
                              activity(metal_ore=balance(0.0), space_corn=balance(0.0)))["coverage_pct"])

    def test_the_system_rollup_leaves_out_what_is_used_on_site(self):
        out = so.station_overview({"2105": profile(resources={"metal_ore": 1000, "space_corn": 40000})},
                                  activity(metal_ore=balance(0.0), space_corn=balance(100.0)), None, now=NOW)
        system = out["systems"][0]
        self.assertEqual(sorted(system["production"]), ["space_corn"])
        self.assertEqual(sorted(system["removed"]), ["space_corn"])


def extractor(name, resource, count, seconds=10, per_cycle=3):
    return {"name": name, "quantity": count,
            "stats": {"Cycle Output": f"{per_cycle} {resource}", "Cycle Time": f"{seconds} seconds"}}


class SlotTests(unittest.TestCase):
    """The extractors fitted against the extractor slots the planet scan gives each station on it."""

    def slots(self, modules, bodies=BODIES, **extra):
        return one({"2105": profile(modules=modules, **extra)}, None, None, "2105", bodies)["slots"]

    def test_a_station_with_every_slot_filled_is_ok(self):
        slots = self.slots([DRILL, CORN])                       # 10 metal ore + 2 space corn = the scan's 10 and 2
        self.assertEqual((slots["status"], slots["missing_total"], slots["slots_total"]), ("ok", 0, 12))
        self.assertEqual(slots["body"], "Cryldorwen")
        self.assertEqual(slots["matched"], "snapshot")
        self.assertEqual(one({"2105": profile(modules=[DRILL, CORN])}, None)["issues"][0]["code"], "no-log-data")

    def test_missing_extractors_are_counted_per_resource_with_the_output_they_cost(self):
        slots = self.slots([extractor("Industrial Metal Drill", "Metal Ore", 7), CORN])
        self.assertEqual(slots["status"], "short")
        rows = {r["key"]: r for r in slots["rows"]}
        self.assertEqual((rows["metal_ore"]["slots"], rows["metal_ore"]["fitted"], rows["metal_ore"]["missing"]), (10, 7, 3))
        self.assertEqual(rows["space_corn"]["missing"], 0)
        self.assertAlmostEqual(rows["metal_ore"]["lost_per_day"], 3 * 25920.0)          # 3 more of the same drill
        self.assertEqual((slots["slots_total"], slots["fitted_total"], slots["missing_total"]), (12, 9, 3))
        self.assertEqual(slots["rows"][0]["key"], "metal_ore")                           # the shortest resource first

    def test_a_resource_with_slots_and_no_extractor_at_all_is_short_with_no_estimate(self):
        slots = self.slots([DRILL])
        corn = {r["key"]: r for r in slots["rows"]}["space_corn"]
        self.assertEqual((corn["slots"], corn["fitted"], corn["missing"], corn["lost_per_day"]), (2, 0, 2, None))

    def test_the_slots_are_per_station_not_per_planet(self):
        # the scan lists slots per station; a planet holds 3 stations but each one has its own set
        slots = self.slots([extractor("Industrial Metal Drill", "Metal Ore", 10), CORN])
        self.assertEqual(slots["status"], "ok")
        self.assertEqual(slots["slots_total"], 12)

    def test_extractors_beyond_the_slots_are_reported_not_hidden(self):
        slots = self.slots([extractor("Industrial Metal Drill", "Metal Ore", 12), CORN])
        row = {r["key"]: r for r in slots["rows"]}["metal_ore"]
        self.assertEqual((row["extra"], row["missing"]), (2, 0))
        self.assertEqual(slots["status"], "ok")

    def test_converters_and_gear_do_not_fill_slots(self):
        slots = self.slots([CONVERTER, SHIELD, DRILL, CORN])
        self.assertNotIn("rations", [r["key"] for r in slots["rows"]])
        self.assertEqual(slots["status"], "ok")

    def test_different_tiers_of_one_resource_add_up(self):
        modules = [extractor("Advanced Metal Drill", "Metal Ore", 4, per_cycle=2), extractor("Industrial", "Metal Ore", 6),
                   CORN]
        self.assertEqual(self.slots(modules)["status"], "ok")

    def test_the_issue_names_the_worst_resources_and_the_lost_output(self):
        station = one({"2105": profile(modules=[extractor("Industrial Metal Drill", "Metal Ore", 7), CORN])}, None)
        issue = [i for i in station["issues"] if i["code"] == "unfilled-slots"][0]
        self.assertEqual(issue["severity"], "warn")
        self.assertIn("3 of 12 extractor slots on Cryldorwen are empty: Metal Ore 7/10", issue["text"])
        self.assertIn("77,760/day lost", issue["text"])

    def test_half_the_slots_or_more_empty_is_a_problem_not_a_warning(self):
        station = one({"2105": profile(modules=[extractor("Industrial Metal Drill", "Metal Ore", 4), CORN])}, None)
        issue = [i for i in station["issues"] if i["code"] == "unfilled-slots"][0]
        self.assertEqual(issue["severity"], "bad")                                       # 6 of 12: exactly half
        just_under = one({"2105": profile(modules=[extractor("Industrial Metal Drill", "Metal Ore", 5), CORN])}, None)
        self.assertEqual([i["severity"] for i in just_under["issues"] if i["code"] == "unfilled-slots"], ["warn"])  # 5 of 12
        none_fitted = one({"2105": profile(modules=[SHIELD])}, None)
        self.assertEqual([i["severity"] for i in none_fitted["issues"] if i["code"] == "unfilled-slots"], ["bad"])

    def test_the_body_comes_from_the_snapshot_even_when_the_station_was_renamed(self):
        renamed = profile(station_name="C/G/M/O/S/T Norv 3", body_name="cryldorwen", modules=[DRILL, CORN])
        station = one({"2105": renamed}, None)
        self.assertEqual((station["slots"]["status"], station["slots"]["matched"]), ("ok", "snapshot"))

    def test_an_older_snapshot_without_a_body_falls_back_to_the_station_name(self):
        for name in ("cryldorwen - 1", "cryldorwen - 2 - col", "C/G/M/O/S/T Cryldorwen 3", "Cryldorwen 1"):
            old = profile(station_name=name, body_name=None, modules=[DRILL])
            slots = one({"2105": old}, None)["slots"]
            self.assertEqual((slots["status"], slots["matched"]), ("short", "name"), name)

    def test_a_planet_with_no_scan_is_info_and_an_unknown_planet_is_info(self):
        unscanned = one({"2105": profile(body_name="Elsewhere")}, None)
        self.assertEqual(unscanned["slots"]["status"], "unscanned")
        self.assertEqual([(i["code"], i["severity"]) for i in unscanned["issues"] if "body" in i["code"]],
                         [("unscanned-body", "info")])
        unknown = one({"2105": profile(body_name=None, station_name="Mystery")}, None)
        self.assertEqual(unknown["slots"]["status"], "no-body")
        self.assertEqual([(i["code"], i["severity"]) for i in unknown["issues"] if "body" in i["code"]],
                         [("unknown-body", "info")])

    def test_without_a_snapshot_the_modules_are_unknown_and_nothing_is_claimed(self):
        station = one(None, activity())
        self.assertEqual(station["slots"], {"status": "no-snapshot"})
        self.assertNotIn("unfilled-slots", [i["code"] for i in station["issues"]])

    def test_the_system_counts_its_short_stations(self):
        profiles = {"2105": profile("2105", modules=[DRILL]), "2106": profile("2106", modules=[DRILL, CORN])}
        out = so.station_overview(profiles, None, None, now=NOW, bodies=BODIES)
        self.assertEqual(out["systems"][0]["short"], 1)

    def test_an_abbreviated_station_name_matches_the_one_body_it_starts(self):
        bodies = BODIES + [{"system": "Talreis Pass", "planet_name": "Norvendorok", "parent_planet": "", "stations": 3,
                            "resources": {"metal_ore": 10}},
                           {"system": "Talreis Pass", "planet_name": "Norvelmar", "parent_planet": "", "stations": 3,
                            "resources": {"metal_ore": 1}}]
        old = profile(station_name="C/G/M/O/S/T Norvend 3", body_name=None, system_name="Talreis Pass", modules=[DRILL])
        slots = one({"2105": old}, None, None, "2105", bodies)["slots"]
        self.assertEqual((slots["status"], slots["matched"], slots["body"]), ("ok", "name", "Norvendorok"))
        ambiguous = profile(station_name="Norv 3", body_name=None, system_name="Talreis Pass", modules=[DRILL])
        self.assertEqual(one({"2105": ambiguous}, None, None, "2105", bodies)["slots"]["status"], "no-body")
        too_short = profile(station_name="No 3", body_name=None, system_name="Talreis Pass", modules=[DRILL])
        self.assertEqual(one({"2105": too_short}, None, None, "2105", bodies)["slots"]["status"], "no-body")

    def test_an_abbreviation_never_crosses_into_another_system(self):
        old = profile(station_name="Cryld 1", body_name=None, system_name="Elsewhere", modules=[DRILL])
        self.assertEqual(one({"2105": old}, None)["slots"]["status"], "no-body")

    def test_a_planet_with_no_extractor_slots_is_not_called_fully_filled(self):
        bodies = [{"system": "Zordoil Frontier", "planet_name": "Cryldorwen", "parent_planet": "", "stations": 3,
                   "resources": {"metal_ore": 0}}]
        slots = one({"2105": profile(modules=[DRILL])}, None, None, "2105", bodies)["slots"]
        self.assertEqual((slots["status"], slots["slots_total"]), ("no-slots", 0))

    def test_a_free_standing_station_has_no_planet_to_check(self):
        station = one({"2105": profile(planet_id=None, body_name=None, station_name="Small Central Command")}, None)
        self.assertEqual(station["slots"], {"status": "unattached"})
        self.assertNotIn("unknown-body", [i["code"] for i in station["issues"]])

    def test_without_a_snapshot_the_older_scans_modules_are_used_and_say_so(self):
        scan = [{"source": "station_setup", "key": "2105", "captured_at": 1.0, "data": {"cargo": [
            {"item_key": "item:station:drill", "display_name": "Industrial Metal Drill", "quantity": 7,
             "stats": {"Cycle Output": "3 Metal Ore", "Cycle Time": "10 seconds"}}]}}]
        out = so.station_overview(None, activity(), None, now=NOW, bodies=BODIES, colony_scan_rows=scan)
        station = out["stations"][0]
        self.assertEqual((station["slots"]["status"], station["slots"]["from_scan"], station["slots"]["matched"]),
                         ("short", True, "name"))
        issue = [i for i in station["issues"] if i["code"] == "unfilled-slots"][0]
        self.assertEqual(issue["severity"], "warn")                                    # never "bad" from an old scan
        self.assertIn("From an older scan", issue["text"])

    def test_an_older_scan_is_never_a_problem_however_much_it_says_is_missing(self):
        scan = [{"source": "station_setup", "key": "2105", "captured_at": 1.0, "data": {"cargo": []}}]
        out = so.station_overview(None, activity(), None, now=NOW, bodies=BODIES, colony_scan_rows=scan)
        issue = [i for i in out["stations"][0]["issues"] if i["code"] == "unfilled-slots"][0]
        self.assertEqual(out["stations"][0]["slots"]["missing_total"], 12)             # every slot empty ...
        self.assertEqual(issue["severity"], "warn")                                    # ... still only a warning

    def test_the_shortest_abbreviation_that_counts_is_three_letters(self):
        bodies = [{"system": "Talreis Pass", "planet_name": "Norvendorok", "parent_planet": "", "stations": 3,
                   "resources": {"metal_ore": 10}}]

        def status(name):
            old = profile(station_name=name, body_name=None, system_name="Talreis Pass", modules=[DRILL])
            return one({"2105": old}, None, None, "2105", bodies)["slots"]["status"]

        self.assertEqual(status("No 3"), "no-body")                                    # two letters: too little to go on
        self.assertEqual(status("Nor 3"), "ok")                                        # three: the only body starting so

    def test_the_newest_scan_of_a_station_is_the_one_used(self):
        def row(captured, count):
            return {"source": "station_setup", "key": "2105", "captured_at": captured, "data": {"cargo": [
                {"item_key": "item:station:drill", "display_name": "Drill", "quantity": count,
                 "stats": {"Cycle Output": "3 Metal Ore", "Cycle Time": "10 seconds"}}]}}
        modules = so._scan_modules([row(5.0, 10), row(9.0, 3), row(1.0, 99)])
        self.assertEqual(modules["2105"][0]["quantity"], 3)

    def test_a_snapshot_beats_the_scan(self):
        scan = [{"source": "station_setup", "key": "2105", "captured_at": 1.0, "data": {"cargo": []}}]
        out = so.station_overview({"2105": profile(modules=[DRILL, CORN])}, activity(), None, now=NOW, bodies=BODIES,
                                  colony_scan_rows=scan)
        self.assertEqual((out["stations"][0]["slots"]["status"], out["stations"][0]["slots"]["from_scan"]), ("ok", False))

    def test_no_bodies_at_all_still_works(self):
        out = so.station_overview({"2105": profile()}, None, None, now=NOW)
        self.assertEqual(out["stations"][0]["slots"]["status"], "unscanned")


WINDOW = {"start": NOW - 80000, "end": NOW, "hours": 22.2}


def twice(running, observed, bought_observed=0.0):
    """A resource balance with both figures: ``running`` % if the bots ran non-stop, ``observed`` % in the newest log
    window."""
    row = balance(running)
    row.update(bought_observed_per_day=bought_observed, sold_observed_per_day=0.0, pct_observed=observed)
    return row


def windowed(**balances):
    act = activity(**balances)
    act["stations"]["cryldorwen - 1"]["log_window"] = WINDOW
    return act


class TwoRatesTests(unittest.TestCase):
    """What leaves is judged on what bots really took in the newest log window; the non-stop figure is shown beside it
    and used only where there is no window."""

    def rows(self, act, fleet=None):
        station = one({"2105": profile(resources=PILING)}, act, fleet)
        return {r["key"]: r for r in station["resources"]}

    def test_status_and_percentage_follow_the_observed_figure(self):
        row = self.rows(windowed(metal_ore=twice(150.0, 30.0, bought_observed=77.0)))["metal_ore"]
        self.assertEqual((row["pct_leaves"], row["basis"], row["status"]), (30.0, "observed", "low"))
        self.assertEqual((row["pct_satisfied"], row["pct_observed"]), (150.0, 30.0))      # both stay on the row
        self.assertEqual(row["bought_observed_per_day"], 77.0)

    def test_with_no_window_the_non_stop_figure_stands_in_and_says_so(self):
        row = self.rows(activity(metal_ore=balance(95.0)))["metal_ore"]
        self.assertEqual((row["pct_leaves"], row["basis"], row["status"]), (95.0, "running", "ok"))
        self.assertIsNone(row["pct_observed"])
        self.assertIsNone(row["bought_observed_per_day"])

    def test_with_neither_figure_nothing_is_claimed(self):
        row = self.rows(activity())["metal_ore"]
        self.assertEqual((row["pct_leaves"], row["basis"], row["status"]), (None, None, "unknown"))

    def test_bots_that_take_it_but_none_left_in_the_window_is_idle_not_no_route(self):
        row = self.rows(windowed(metal_ore=twice(60.0, 0.0)))["metal_ore"]          # no route of the user's own
        self.assertEqual(row["status"], "idle")

    def test_nobody_ever_taking_it_is_still_no_route(self):
        row = self.rows(windowed(metal_ore=twice(0.0, 0.0)))["metal_ore"]
        self.assertEqual(row["status"], "no-route")

    def test_station_coverage_and_the_system_rollup_follow_the_observed_figure(self):
        out = so.station_overview({"2105": profile(resources=PILING)}, windowed(metal_ore=twice(100.0, 20.0)), None, now=NOW,
                                  bodies=BODIES)
        station = out["stations"][0]
        self.assertAlmostEqual(station["coverage_pct"], 20.0, places=1)
        self.assertAlmostEqual(out["systems"][0]["removed"]["metal_ore"], 259200.0 * 0.2)

    def test_the_issue_text_gives_the_window_and_the_non_stop_figure(self):
        station = one({"2105": profile()}, windowed(metal_ore=twice(150.0, 30.0)))
        text = [i["text"] for i in station["issues"] if i["code"] == "low-coverage"][0]
        self.assertIn("only 30% of production left in the newest 22 h of its log (150% if its bots ran non-stop)", text)
        plain = one({"2105": profile()}, activity(metal_ore=balance(30.0)))
        self.assertIn("only 30% of production leaves the station.",
                      [i["text"] for i in plain["issues"] if i["code"] == "low-coverage"][0])

    def test_the_idle_text_says_bots_take_it(self):
        station = one({"2105": profile(resources=PILING)}, windowed(metal_ore=twice(60.0, 0.0)))
        text = [i["text"] for i in station["issues"] if i["code"] == "idle-route"][0]
        self.assertIn("bots take it but little leaves", text)
        self.assertIn("0% of production left in the newest 22 h", text)

    def test_the_window_is_carried_on_the_station(self):
        self.assertEqual(one({"2105": profile()}, windowed())["log_window"], WINDOW)
        self.assertIsNone(one({"2105": profile()}, activity())["log_window"])


class BotsSeenTests(unittest.TestCase):
    def station(self):
        act = windowed(metal_ore=twice(150.0, 30.0))
        act["stations"]["cryldorwen - 1"]["bot_breakdown"] = {
            "Metal Ore (TB-No U)": {"tag": "TB-No U", "nickname": "Metal Ore", "docks": 30, "window_docks": 14,
                                    "loop_seconds": 262, "resources": {},
                                    "resource_rates": {"silicon": {"per_dock": 5, "running_per_day": 50, "observed_per_day": None},
                                                       "metal_ore": {"per_dock": 1000, "running_per_day": 5000,
                                                                     "observed_per_day": 800}}},
            "3": {"tag": "TB-111", "nickname": "3", "docks": 9, "window_docks": 2, "loop_seconds": None, "resources": {},
                  "resource_rates": {"wood": {"per_dock": 10, "running_per_day": None, "observed_per_day": 10000}}},
            "Idle": {"tag": "TB-9", "nickname": "Idle", "docks": 1, "window_docks": 0, "loop_seconds": None,
                     "resources": {}, "resource_rates": {}},
        }
        act["bots"] = {"Metal Ore (TB-No U)": {"roster_known": False}, "3": {"roster_known": True}}
        return one({"2105": profile()}, act)

    def test_every_bot_that_moved_something_is_listed_busiest_first_whoever_owns_it(self):
        bots = self.station()["bots"]
        self.assertEqual([b["name"] for b in bots], ["3", "Metal Ore (TB-No U)"])      # "Idle" moved nothing
        outsider = bots[1]
        self.assertEqual((outsider["nickname"], outsider["account"], outsider["loop_seconds"], outsider["docks"],
                          outsider["window_docks"]), ("Metal Ore", "TB-No U", 262, 30, 14))

    def test_a_bot_knows_whether_it_is_in_the_users_fleet_scan(self):
        bots = {b["name"]: b for b in self.station()["bots"]}
        self.assertTrue(bots["3"]["in_fleet"])
        self.assertFalse(bots["Metal Ore (TB-No U)"]["in_fleet"])

    def test_a_bots_resources_carry_both_rates_and_come_busiest_first(self):
        rows = {b["name"]: b for b in self.station()["bots"]}["Metal Ore (TB-No U)"]["rows"]
        self.assertEqual([r["key"] for r in rows], ["metal_ore", "silicon"])
        self.assertEqual(rows[0], {"key": "metal_ore", "label": "Metal Ore", "per_dock": 1000, "running_per_day": 5000,
                                   "observed_per_day": 800})

    def test_a_bot_of_one_of_the_users_accounts_is_mine_even_when_retired(self):
        # account 111's current bots are a-d: "1" (TB-111) is an old one, still in the log but not in the fleet scan
        act = windowed(metal_ore=twice(150.0, 30.0))
        act["stations"]["cryldorwen - 1"]["bot_breakdown"] = {
            "1 (TB-111)": {"tag": "TB-111", "nickname": "1", "docks": 500, "window_docks": 0, "last_dock": 1000,
                           "loop_seconds": 315, "resources": {},
                           "resource_rates": {"metal_ore": {"per_dock": 13000, "running_per_day": 3600000, "observed_per_day": 0}}},
            "1 (TB-444)": {"tag": "TB-444", "nickname": "1", "docks": 80, "window_docks": 9, "last_dock": 5000,
                           "loop_seconds": 314, "resources": {},
                           "resource_rates": {"metal_ore": {"per_dock": 34000, "running_per_day": 9400000, "observed_per_day": 4100000}}},
            "Metal Ore (TB-No U)": {"tag": "TB-No U", "nickname": "Metal Ore", "docks": 5, "window_docks": 0, "last_dock": 900,
                                    "loop_seconds": 60, "resources": {},
                                    "resource_rates": {"metal_ore": {"per_dock": 1, "running_per_day": 1, "observed_per_day": 0}}}}
        act["bots"] = {"1 (TB-444)": {"roster_known": True}, "1 (TB-111)": {"roster_known": False},
                       "Metal Ore (TB-No U)": {"roster_known": False}}
        fleet = [bot("1", [trade("2105", "BUY", "metal_ore")], nickname="a", account="111"),
                 bot("2", [trade("2105", "BUY", "metal_ore")], nickname="1", account="444")]
        bots = {b["name"]: b for b in one({"2105": profile()}, act, fleet)["bots"]}
        self.assertEqual((bots["1 (TB-444)"]["mine"], bots["1 (TB-444)"]["in_fleet"]), (True, True))
        self.assertEqual((bots["1 (TB-111)"]["mine"], bots["1 (TB-111)"]["in_fleet"]), (True, False))   # retired
        self.assertEqual((bots["Metal Ore (TB-No U)"]["mine"], bots["Metal Ore (TB-No U)"]["in_fleet"]), (False, False))
        self.assertEqual((bots["1 (TB-111)"]["last_seen"], bots["1 (TB-111)"]["window_docks"]), (1000, 0))

    def test_without_a_fleet_scan_no_account_is_mine(self):
        act = windowed(metal_ore=twice(150.0, 30.0))
        act["stations"]["cryldorwen - 1"]["bot_breakdown"] = {
            "x": {"tag": "TB-111", "nickname": "x", "docks": 1, "window_docks": 0, "last_dock": 1, "loop_seconds": None,
                  "resources": {}, "resource_rates": {"wood": {"per_dock": 1, "running_per_day": None, "observed_per_day": 0}}}}
        self.assertFalse(one({"2105": profile()}, act, None)["bots"][0]["mine"])

    def test_a_fleet_row_with_no_username_makes_no_account_mine(self):
        act = windowed(metal_ore=twice(150.0, 30.0))
        act["stations"]["cryldorwen - 1"]["bot_breakdown"] = {
            "x": {"tag": "TB-None", "nickname": "x", "docks": 1, "window_docks": 0, "last_dock": 1, "loop_seconds": None,
                  "resources": {}, "resource_rates": {"wood": {"per_dock": 1, "running_per_day": None, "observed_per_day": 0}}}}
        fleet = [{"key": "1", "captured_at": 1, "context": {}, "data": {"nickname": "n", "haul_orders": []}}]
        self.assertFalse(one({"2105": profile()}, act, fleet)["bots"][0]["mine"])

    def test_bots_idle_in_the_window_come_most_recent_first(self):
        act = windowed(metal_ore=twice(150.0, 30.0))
        rates = {"wood": {"per_dock": 1, "running_per_day": 5, "observed_per_day": 0}}
        act["stations"]["cryldorwen - 1"]["bot_breakdown"] = {
            name: {"tag": "TB-1", "nickname": name, "docks": 1, "window_docks": 0, "last_dock": last, "loop_seconds": None,
                   "resources": {}, "resource_rates": rates} for name, last in (("old", 10), ("new", 99), ("mid", 50))}
        self.assertEqual([b["name"] for b in one({"2105": profile()}, act)["bots"]], ["new", "mid", "old"])

    def test_a_station_with_no_log_has_no_bots(self):
        self.assertEqual(one({"2105": profile()}, None)["bots"], [])
        self.assertEqual(one({"2105": profile()}, activity())["bots"], [])

    def test_the_bots_survive_json(self):
        import json
        json.dumps(self.station())


def gear_row(item_type, name, quantity=1):
    return {"item_key": f"item:station:{item_type}", "item_type": item_type, "display_name": name,
            "item_category": "station", "quantity": quantity, "stats": {}}


def setup_scan(sid="2105", fitted=None, cargo=None, captured=NOW - DAY, **data):
    """A station-setup scan row (colony_scan.jsonl): the PS_STATION_CARGO payload with the game's fitted counts."""
    payload = {"cargo": cargo if cargo is not None else [], "equipped_module_counts": fitted or {}}
    payload.update(data)
    return {"source": "station_setup", "key": sid, "captured_at": captured, "data": payload}


def gear_of_station(scans, sid="2105", profiles=None, act=None):
    out = so.station_overview(profiles, act or activity(sid=sid), None, now=NOW, bodies=BODIES, colony_scan_rows=scans)
    return next(s for s in out["stations"] if s["id"] == sid)["gear"]


class GearTests(unittest.TestCase):
    """Which marked station gear (Mk I-III) a station has FITTED, from its newest setup scan."""

    SHIELD2 = gear_row("station_shield_mk2", "Station Shield Mk II")
    SHIELD3 = gear_row("station_shield_mk3", "Station Shield Mk III")
    BEAM2 = gear_row("station_beam_cannon_mk2", "Station Beam Cannon Mk II")

    def test_marks_come_from_the_type_names(self):
        self.assertEqual(so.tier_of("station_beam_cannon_mk2"), ("station_beam_cannon", 2))
        self.assertEqual(so.tier_of("csw_layered_plating_mk3"), ("csw_layered_plating", 3))
        self.assertEqual(so.tier_of("station_shield_transference_mk10"), ("station_shield_transference", 10))
        for plain in ("station_expander_gamma", "metal_drill", "", None, "mk3", "x_mk"):
            self.assertIsNone(so.tier_of(plain), plain)

    def test_only_what_is_fitted_counts_not_what_is_in_storage(self):
        scan = setup_scan(cargo=[dict(self.SHIELD2, quantity=30), self.SHIELD3],
                          fitted={"station_shield_mk3": 1})                  # thirty spare Mk II, one Mk III fitted
        gear = gear_of_station([scan])
        self.assertEqual([m["type"] for m in gear["fitted"]], ["station_shield_mk3"])
        self.assertEqual(gear["marks"], [{"mark": 3, "count": 1}])
        self.assertEqual(gear["behind"], 0)

    def test_spare_gear_alone_does_not_make_a_station_a_mk2_station(self):
        gear = gear_of_station([setup_scan(cargo=[self.SHIELD2], fitted={})])
        self.assertEqual((gear["known"], gear["fitted"], gear["marks"]), (True, [], []))

    def test_marks_are_counted_per_mark_lowest_first(self):
        fitted = {"station_beam_cannon_mk2": 1, "station_energy_mk2": 1, "station_shield_mk3": 2,
                  "station_sensor_mk1": 1}
        gear = gear_of_station([setup_scan(fitted=fitted)])
        self.assertEqual(gear["marks"], [{"mark": 1, "count": 1}, {"mark": 2, "count": 2}, {"mark": 3, "count": 2}])

    def test_the_label_is_the_name_without_its_mark(self):
        gear = gear_of_station([setup_scan(cargo=[self.BEAM2, gear_row("station_energy_mk2", "Station Energy Mk. 2"),
                                                  gear_row("station_sensor_mk2", "Sensor")],
                                           fitted={"station_beam_cannon_mk2": 1, "station_energy_mk2": 1,
                                                   "station_sensor_mk2": 1, "station_shield_mk2": 1})])
        labels = {m["type"]: m["label"] for m in gear["fitted"]}
        self.assertEqual(labels["station_beam_cannon_mk2"], "Station Beam Cannon")
        self.assertEqual(labels["station_energy_mk2"], "Station Energy")
        self.assertEqual(labels["station_sensor_mk2"], "Sensor")
        self.assertEqual(labels["station_shield_mk2"], "Station Shield")             # no cargo row: from the type
        self.assertEqual([m["label"] for m in gear["fitted"]], sorted(m["label"] for m in gear["fitted"]))

    def test_a_module_is_behind_when_a_higher_mark_of_its_family_exists_anywhere(self):
        mine = setup_scan("1", fitted={"station_beam_cannon_mk2": 1}, cargo=[self.BEAM2])
        other = setup_scan("2", fitted={}, cargo=[gear_row("station_beam_cannon_mk3", "Station Beam Cannon Mk III")])
        act = {"stations": {**activity("1", "one")["stations"], **activity("2", "two")["stations"]}}
        gear = gear_of_station([mine, other], "1", act=act)
        module = gear["fitted"][0]
        self.assertEqual((module["mark"], module["top"], module["behind"]), (2, 3, True))
        self.assertEqual(gear["behind"], 1)

    def test_the_top_mark_is_the_highest_seen_whatever_order_the_scans_come_in(self):
        high = setup_scan("2", fitted={}, cargo=[gear_row("station_beam_cannon_mk3", "Station Beam Cannon Mk III")])
        low = setup_scan("1", fitted={"station_beam_cannon_mk2": 1}, cargo=[self.BEAM2])
        act = {"stations": {**activity("1", "one")["stations"], **activity("2", "two")["stations"]}}
        for scans in ([high, low], [low, high]):
            module = gear_of_station(scans, "1", act=act)["fitted"][0]
            self.assertEqual((module["top"], module["behind"]), (3, True))

    def test_behind_counts_modules_not_kinds(self):
        scans = [setup_scan(fitted={"station_beam_cannon_mk2": 3, "station_shield_mk3": 2},
                            cargo=[gear_row("station_beam_cannon_mk3", "Station Beam Cannon Mk III")])]
        gear = gear_of_station(scans)
        self.assertEqual(gear["behind"], 3)

    def test_a_higher_mark_in_another_stations_snapshot_counts_too(self):
        profiles = {"9": profile("9", modules=[{"item_key": "item:station:station_beam_cannon_mk3", "name": "x",
                                                "quantity": 1, "stats": {}}])}
        mine = setup_scan("1", fitted={"station_beam_cannon_mk2": 1})
        act = {"stations": activity("1", "one")["stations"]}
        out = so.station_overview(profiles, act, None, now=NOW, bodies=BODIES, colony_scan_rows=[mine])
        gear = next(s for s in out["stations"] if s["id"] == "1")["gear"]
        self.assertTrue(gear["fitted"][0]["behind"])

    def test_a_family_that_only_has_one_mark_is_never_behind(self):
        gear = gear_of_station([setup_scan(fitted={"station_point_defense_laser_mk1": 1})])
        module = gear["fitted"][0]
        self.assertEqual((module["mark"], module["top"], module["behind"]), (1, 1, False))
        self.assertEqual(gear["behind"], 0)

    def test_one_familys_top_mark_is_not_anothers(self):
        scans = [setup_scan(fitted={"station_beam_cannon_mk2": 1, "station_shield_mk2": 1, "station_shield_transference_mk2": 1},
                            cargo=[gear_row("station_shield_mk3", "Station Shield Mk III")])]
        gear = gear_of_station(scans)
        behind = {m["family"]: m["behind"] for m in gear["fitted"]}
        self.assertEqual(behind, {"station_beam_cannon": False, "station_shield": True, "station_shield_transference": False})

    def test_a_csw_only_family_is_its_own(self):
        scans = [setup_scan(fitted={"csw_layered_plating_mk2": 1},
                            cargo=[gear_row("layered_plating_mk3", "Layered Plating Mk III")])]
        self.assertFalse(gear_of_station(scans)["fitted"][0]["behind"])

    def test_no_scan_is_unknown_and_a_scan_with_nothing_fitted_is_known_and_empty(self):
        self.assertEqual(gear_of_station([]), {"known": False})
        self.assertEqual(gear_of_station([setup_scan(fitted={})])["known"], True)
        self.assertEqual(gear_of_station(None), {"known": False})

    def test_a_scan_that_says_nothing_about_fitted_modules_is_unknown(self):
        scan = {"source": "station_setup", "key": "2105", "captured_at": 1.0, "data": {"cargo": []}}
        self.assertEqual(gear_of_station([scan]), {"known": False})

    def test_the_newest_scan_of_a_station_wins(self):
        old = setup_scan(fitted={"station_shield_mk1": 1}, captured=NOW - 9 * DAY)
        new = setup_scan(fitted={"station_shield_mk3": 1}, captured=NOW - DAY)
        for scans in ([old, new], [new, old]):
            self.assertEqual(gear_of_station(scans)["fitted"][0]["mark"], 3)

    def test_scans_of_other_kinds_and_other_stations_are_ignored(self):
        other = dict(setup_scan("77", fitted={"station_shield_mk1": 1}))
        wrong_kind = dict(setup_scan(fitted={"station_shield_mk1": 1}), source="colony_data")
        self.assertEqual(gear_of_station([other, wrong_kind]), {"known": False})

    def test_an_older_servers_list_of_types_still_counts(self):
        scan = {"source": "station_setup", "key": "2105", "captured_at": NOW - DAY,
                "data": {"cargo": [], "equipped_module_types": ["station_shield_mk2", "station_shield_mk2",
                                                                "station_energy_mk2", "", None]}}
        gear = gear_of_station([scan])
        self.assertEqual({m["type"]: m["count"] for m in gear["fitted"]},
                         {"station_shield_mk2": 2, "station_energy_mk2": 1})

    def test_junk_counts_are_ignored(self):
        fitted = {"station_shield_mk2": "x", "station_energy_mk2": 0, "": 1, "station_sensor_mk2": "2",
                  "station_beam_cannon_mk2": -3, "station_mag_cannon_mk2": None}
        gear = gear_of_station([setup_scan(fitted=fitted)])
        self.assertEqual([(m["type"], m["count"]) for m in gear["fitted"]], [("station_sensor_mk2", 2)])

    def test_modules_without_a_mark_are_not_gear(self):
        gear = gear_of_station([setup_scan(fitted={"industrial_metal_drill": 41, "station_expander_gamma": 50})])
        self.assertEqual((gear["known"], gear["fitted"], gear["marks"]), (True, [], []))

    def test_the_age_is_that_of_the_scan(self):
        self.assertEqual(gear_of_station([setup_scan(captured=NOW - 2 * DAY)])["age_days"], 2.0)
        self.assertEqual(gear_of_station([setup_scan(captured=NOW - 2 * DAY)])["seen_at"], NOW - 2 * DAY)
        scan = setup_scan()
        scan["captured_at"] = None
        self.assertIsNone(gear_of_station([scan])["age_days"])

    def test_the_gear_does_not_depend_on_having_a_snapshot(self):
        scans = [setup_scan(fitted={"station_shield_mk2": 1})]
        with_profile = gear_of_station(scans, profiles={"2105": profile()})
        without = gear_of_station(scans)
        self.assertEqual(with_profile, without)
        self.assertEqual(with_profile["marks"], [{"mark": 2, "count": 1}])

    def test_a_snapshots_modules_do_not_say_what_is_fitted(self):
        gear = gear_of_station([setup_scan(fitted={})], profiles={"2105": profile(modules=[SHIELD])})
        self.assertEqual(gear["fitted"], [])                                          # SHIELD is in storage or fitted: unknown

    def test_a_station_with_no_scan_beside_one_with_a_scan(self):
        act = {"stations": {**activity("1", "one")["stations"], **activity("2", "two")["stations"]}}
        out = so.station_overview(None, act, None, now=NOW, bodies=BODIES,
                                  colony_scan_rows=[setup_scan("1", fitted={"station_shield_mk2": 1})])
        gears = {s["id"]: s["gear"]["known"] for s in out["stations"]}
        self.assertEqual(gears, {"1": True, "2": False})

    def test_the_result_is_json_serialisable_with_no_sets_left_in_it(self):
        import json
        gear = gear_of_station([setup_scan(fitted={"station_shield_mk2": 1}, cargo=[self.SHIELD2])])
        json.dumps(gear)

    def test_what_the_slot_check_reads_is_unchanged(self):
        scan = setup_scan(fitted={"station_shield_mk2": 1}, cargo=[
            {"item_key": "item:station:drill", "display_name": "Industrial Metal Drill", "quantity": 7,
             "stats": {"Cycle Output": "3 Metal Ore", "Cycle Time": "10 seconds"}}])
        out = so.station_overview(None, activity(), None, now=NOW, bodies=BODIES, colony_scan_rows=[scan])
        slots = out["stations"][0]["slots"]
        self.assertEqual((slots["status"], slots["from_scan"]), ("short", True))


class RollupTests(unittest.TestCase):
    def test_coverage_is_weighted_by_production(self):
        station = one({"2105": profile(resources=PILING)}, activity(metal_ore=balance(100.0), space_corn=balance(0.0)))
        self.assertAlmostEqual(station["coverage_pct"], 100.0 * 259200 / (259200 + 51840), places=1)
        self.assertIsNone(one({"2105": profile(modules=[SHIELD])}, None)["coverage_pct"])

    def test_systems_count_stations_snapshots_and_issues(self):
        profiles = {"2105": profile("2105"), "2106": profile("2106", system_name="Other", age_days=20)}
        out = so.station_overview(profiles, activity(metal_ore=balance(100.0), space_corn=balance(100.0)), None,
                                  now=NOW)
        systems = {s["name"]: s for s in out["systems"]}
        self.assertEqual(systems["Zordoil Frontier"]["stations"], 1)
        self.assertEqual((systems["Other"]["stations"], systems["Other"]["snapshots"], systems["Other"]["bad"]), (1, 1, 1))
        self.assertAlmostEqual(systems["Zordoil Frontier"]["production"]["metal_ore"], 259200.0)
        self.assertAlmostEqual(systems["Zordoil Frontier"]["removed"]["metal_ore"], 259200.0)
        self.assertEqual(out["resources"], ["metal_ore", "space_corn"])

    def test_a_station_with_no_system_is_listed_under_unknown(self):
        out = so.station_overview({"5": profile("5", system_name="")}, None, None, now=NOW)
        self.assertEqual([s["name"] for s in out["systems"]], ["Unknown"])

    def test_the_whole_result_is_json_serialisable(self):
        import json
        out = so.station_overview({"2105": profile()}, activity(metal_ore=balance(50.0)),
                                  [bot("1", [trade("2105", "BUY", "metal_ore")])], now=NOW)
        json.dumps(out)


if __name__ == "__main__":
    unittest.main()

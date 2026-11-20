import unittest

from bot_activity import (
    _newest_window,
    _extractor_output_per_unit_per_day, _resolve_resource_key,
    _shuttle_flow_edges_from_colony_scan, _solve_shuttle_flows, bot_activity_summary,
    parse_log_line,
)


class ParseLogLineTests(unittest.TestCase):
    def test_bot_dock(self):
        r = parse_log_line("Ver-A4 (TB-ExampleAlt3) docked.")
        self.assertEqual(r, {"actor": "Ver-A4", "tag": "TB-ExampleAlt3",
                              "is_bot": True, "kind": "docked"})

    def test_bot_undock(self):
        r = parse_log_line("Ver-A4 (TB-ExampleAlt3) undocked.")
        self.assertEqual(r["kind"], "undocked")
        self.assertTrue(r["is_bot"])

    def test_player_dock_has_no_tag(self):
        r = parse_log_line("ExamplePlayer docked.")
        self.assertEqual(r, {"actor": "ExamplePlayer", "tag": None,
                              "is_bot": False, "kind": "docked"})

    def test_bot_buy_with_thousands_separator(self):
        r = parse_log_line("Ver-A3 (TB-ExampleAlt) bought 4,683 Space Corn for 4,683 credits.")
        self.assertEqual(r, {"actor": "Ver-A3", "tag": "TB-ExampleAlt", "is_bot": True,
                              "kind": "bought", "qty": 4683, "item": "Space Corn",
                              "price": 4683})

    def test_bot_sell(self):
        r = parse_log_line("Ruski (TB-ExamplePlayer) sold 25 A King's Siege Cannon for 5,000 credits.")
        self.assertEqual(r["kind"], "sold")
        self.assertEqual(r["item"], "A King's Siege Cannon")
        self.assertEqual(r["qty"], 25)
        self.assertEqual(r["price"], 5000)

    def test_non_dock_non_txn_line_returns_none(self):
        self.assertIsNone(parse_log_line("ExamplePlayer withdrew 250,000,000 credits."))
        self.assertIsNone(parse_log_line("ExamplePlayer equipped 10 Station Expander Gamma."))


class BotActivitySummaryTests(unittest.TestCase):
    def _rows(self, *entries):
        return [{"station_id": sid, "id": str(i), "time": "1", "text": text}
                for i, (sid, text) in enumerate(entries)]

    def test_counts_bot_docks_and_txns_per_station(self):
        rows = self._rows(
            ("7", "Ver-A4 (TB-ExampleAlt3) docked."),
            ("7", "Ver-A4 (TB-ExampleAlt3) bought 100 Wood for 100 credits."),
            ("7", "Ver-A4 (TB-ExampleAlt3) undocked."),
        )
        out = bot_activity_summary(rows)
        st = out["stations"]["7"]
        self.assertEqual(st["bot_docks"], 1)
        self.assertEqual(st["unique_bots"], 1)
        self.assertEqual(st["txns"], 1)
        self.assertEqual(st["bot_txns"], 1)

    def test_station_name_resolved_when_known(self):
        rows = self._rows(("7", "Ver-A4 (TB-ExampleAlt3) docked."))
        out = bot_activity_summary(rows, station_names={"7": "Londdalen - 1"})
        self.assertIn("Londdalen - 1", out["stations"])
        self.assertNotIn("7", out["stations"])

    def test_station_falls_back_to_raw_id_when_name_unknown(self):
        rows = self._rows(("7", "Ver-A4 (TB-ExampleAlt3) docked."))
        out = bot_activity_summary(rows)
        self.assertIn("7", out["stations"])

    def test_station_system_resolved_when_known(self):
        rows = self._rows(("7", "Ver-A4 (TB-ExampleAlt3) docked."))
        out = bot_activity_summary(rows, station_systems={"7": "Vesmaeth Deep"})
        self.assertEqual(out["stations"]["7"]["system"], "Vesmaeth Deep")

    def test_station_system_is_none_when_unknown(self):
        rows = self._rows(("7", "Ver-A4 (TB-ExampleAlt3) docked."))
        out = bot_activity_summary(rows)
        self.assertIsNone(out["stations"]["7"]["system"])

    def test_player_own_dock_counted_separately_from_bot_docks(self):
        rows = self._rows(("7", "ExamplePlayer docked."), ("7", "ExamplePlayer undocked."))
        out = bot_activity_summary(rows)
        st = out["stations"]["7"]
        self.assertEqual(st["player_docks"], 2)
        self.assertEqual(st["bot_docks"], 0)

    def test_station_with_docks_but_no_transactions(self):
        rows = self._rows(
            *[("7", "Ver-A2 (TB-ExampleAlt2) docked.") for _ in range(5)],
        )
        out = bot_activity_summary(rows)
        st = out["stations"]["7"]
        self.assertEqual(st["bot_docks"], 5)
        self.assertEqual(st["txns"], 0)

    def test_bot_txn_per_dock_ratio(self):
        rows = self._rows(
            ("7", "Ver-A1 (TB-ExampleAlt3) docked."),
            ("7", "Ver-A1 (TB-ExampleAlt3) bought 1 Wood for 1 credits."),
            ("7", "Ver-A1 (TB-ExampleAlt3) sold 1 Wood for 1 credits."),
        )
        out = bot_activity_summary(rows)
        bot = out["bots"]["Ver-A1"]
        self.assertEqual(bot["docks"], 1)
        self.assertEqual(bot["txns"], 2)
        self.assertEqual(bot["txn_per_dock"], 2.0)

    def test_bot_with_zero_docks_has_null_ratio_not_a_crash(self):
        # Defensive: a bot could in principle only ever appear via a txn row
        # (e.g. a search-filtered partial capture) with no matching dock row.
        rows = self._rows(("7", "Ver-A1 (TB-ExampleAlt3) bought 1 Wood for 1 credits."))
        out = bot_activity_summary(rows)
        self.assertIsNone(out["bots"]["Ver-A1"]["txn_per_dock"])

    def test_bot_stations_visited_counts_distinct_stations(self):
        rows = self._rows(
            ("7", "Ver-A1 (TB-ExampleAlt3) docked."),
            ("8", "Ver-A1 (TB-ExampleAlt3) docked."),
            ("7", "Ver-A1 (TB-ExampleAlt3) docked."),
        )
        out = bot_activity_summary(rows)
        self.assertEqual(out["bots"]["Ver-A1"]["stations_visited"], 2)

    def test_bot_fleet_row_enriches_bot_entry(self):
        rows = self._rows(("7", "Ver-A1 (TB-ExampleAlt3) docked."))
        fleet_rows = [{"key": "501", "captured_at": 1, "context": {"username": "ExampleAlt3"},
                       "data": {"nickname": "Ver-A1", "ship_type": "type_a",
                                "state": "following", "bot_cargo_cap": 500,
                                "bot_cargo_used": 500,
                                "haul_orders": ["o1", "o2", "o3"]}}]
        out = bot_activity_summary(rows, bot_fleet_rows=fleet_rows)
        bot = out["bots"]["Ver-A1"]
        self.assertTrue(bot["roster_known"])
        self.assertEqual(bot["cargo_cap"], 500)
        self.assertEqual(bot["cargo_used"], 500)
        self.assertEqual(bot["haul_order_count"], 3)

    def test_two_fleet_rows_for_the_same_nickname_the_newer_wins(self):
        rows = self._rows(("7", "Ver-A1 (TB-ExampleAlt3) docked."))
        fleet_rows = [
            {"key": "501", "captured_at": 1, "context": {},
             "data": {"nickname": "Ver-A1", "state": "following"}},
            {"key": "502", "captured_at": 2, "context": {},
             "data": {"nickname": "Ver-A1", "state": "hauling"}},
        ]
        out = bot_activity_summary(rows, bot_fleet_rows=fleet_rows)
        self.assertEqual(out["bots"]["Ver-A1"]["state"], "hauling")

    def test_bot_without_fleet_row_has_null_fields_not_missing_keys(self):
        rows = self._rows(("7", "Ver-A1 (TB-ExampleAlt3) docked."))
        out = bot_activity_summary(rows)
        bot = out["bots"]["Ver-A1"]
        self.assertFalse(bot["roster_known"])
        self.assertIsNone(bot["cargo_cap"])
        self.assertIsNone(bot["haul_order_count"])

    def test_shuttle_covered_is_none_when_shuttle_data_never_captured(self):
        rows = self._rows(("7", "Ver-A1 (TB-ExampleAlt3) docked."))
        out = bot_activity_summary(rows, shuttle_data_captured=False)
        self.assertIsNone(out["stations"]["7"]["shuttle_covered"])

    def test_shuttle_covered_true_when_station_is_a_captured_target(self):
        rows = self._rows(("7", "Ver-A1 (TB-ExampleAlt3) docked."))
        out = bot_activity_summary(rows, shuttle_targets={"7"}, shuttle_data_captured=True)
        self.assertTrue(out["stations"]["7"]["shuttle_covered"])

    def test_shuttle_covered_false_when_data_captured_but_station_not_a_target(self):
        rows = self._rows(("7", "Ver-A1 (TB-ExampleAlt3) docked."))
        out = bot_activity_summary(rows, shuttle_targets={"9"}, shuttle_data_captured=True)
        self.assertFalse(out["stations"]["7"]["shuttle_covered"])

    def test_accounts_pass_through_trade_bot_slots_keyed_by_tag(self):
        slots = {"ExampleAlt3": {"slots_used": 41, "slots_total": 59, "updated_at": 1}}
        out = bot_activity_summary([], trade_bot_slots=slots)
        self.assertEqual(out["accounts"]["TB-ExampleAlt3"]["slots_used"], 41)

    def test_unrecognised_log_lines_are_silently_ignored(self):
        rows = self._rows(("7", "ExamplePlayer withdrew 250,000,000 credits."))
        out = bot_activity_summary(rows)
        self.assertEqual(out["stations"], {})

    def test_empty_input_produces_empty_but_valid_structure(self):
        out = bot_activity_summary([])
        self.assertEqual(out, {"stations": {}, "bots": {}, "accounts": {}})


class StationStorageFromColonyScanTests(unittest.TestCase):
    """The Cargo/Storage cross-reference: PS_STATION_CARGO capacity, current stock, and caps
    (see colony_scan_logger.py) merged onto the same per-station entries the log-based dock/txn
    counts already populate."""

    def _setup_row(self, station_id="7", captured_at=1, **data_overrides):
        data = {
            "hull_used": 719978, "hull_space": 8715062,
            "resource_caps": {"metal_ore": 100000, "wood": 100000},
            "resource_reserves": {},
            "cargo": [
                {"item_key": "metal_ore", "item_category": "resource", "cargo_size": 98804},
                {"item_key": "wood", "item_category": "resource", "cargo_size": 108},
                {"item_key": "item:station:wood_cutter", "item_category": "station",
                 "cargo_size": 216, "quantity": 54},
            ],
        }
        data.update(data_overrides)
        return {"source": "station_setup", "key": station_id, "captured_at": captured_at,
                "context": {"station_name": "eskvenyreth - 1"}, "data": data}

    def _rows(self, *entries):
        return [{"station_id": sid, "id": str(i), "time": "1", "text": text}
                for i, (sid, text) in enumerate(entries)]

    def test_hull_and_resource_fields_populate(self):
        out = bot_activity_summary([], colony_scan_rows=[self._setup_row()])
        st = out["stations"]["7"]
        self.assertEqual(st["hull_used"], 719978)
        self.assertEqual(st["hull_space"], 8715062)
        self.assertAlmostEqual(st["hull_fill_pct"], round(100 * 719978 / 8715062, 1))
        self.assertEqual(st["resource_caps"], {"metal_ore": 100000, "wood": 100000})

    def test_raw_resource_quantity_comes_from_cargo_size(self):
        # A raw resource's item_key never starts with "item:" -- cargo_size IS its stock count
        # (confirmed against Client.py's own Storage-tab renderer, not guessed).
        out = bot_activity_summary([], colony_scan_rows=[self._setup_row()])
        self.assertEqual(out["stations"]["7"]["resources"]["metal_ore"], 98804)
        self.assertEqual(out["stations"]["7"]["resources"]["wood"], 108)

    def test_equipped_module_quantity_comes_from_quantity_not_cargo_size(self):
        # "item:station:wood_cutter" starts with "item:" -- an equipped module, not a resource
        # stock -- quantity (54, how many are installed) is the right count, not cargo_size (216,
        # its per-unit space cost).
        out = bot_activity_summary([], colony_scan_rows=[self._setup_row()])
        self.assertNotIn("item:station:wood_cutter", out["stations"]["7"]["resources"])

    def test_non_resource_cargo_items_are_excluded_from_resources(self):
        row = self._setup_row(cargo=[
            {"item_key": "item:station:vacuskin", "item_category": "station", "quantity": 1},
        ])
        out = bot_activity_summary([], colony_scan_rows=[row])
        self.assertEqual(out["stations"]["7"]["resources"], {})

    def test_extractor_production_summed_across_module_quantity(self):
        row = self._setup_row(cargo=[
            {"item_key": "item:station:industrial_metal_drill", "item_category": "station",
             "quantity": 5, "stats": {"Cycle Output": "3 Metal Ore", "Cycle Time": "10 seconds"}},
        ])
        out = bot_activity_summary([], colony_scan_rows=[row])
        # 5 installed units * (3 every 10s -> 25,920/day per unit) = 129,600/day.
        self.assertAlmostEqual(
            out["stations"]["7"]["resource_production_per_day"]["metal_ore"], 129600.0)

    def test_production_from_multiple_extractor_types_is_kept_separate(self):
        row = self._setup_row(cargo=[
            {"item_key": "item:station:wood_cutter", "item_category": "station",
             "quantity": 2, "stats": {"Cycle Output": "1 Wood", "Cycle Time": "10 seconds"}},
            {"item_key": "item:station:copper_extractor", "item_category": "station",
             "quantity": 1, "stats": {"Cycle Output": "1 Copper", "Cycle Time": "10000 seconds"}},
        ])
        out = bot_activity_summary([], colony_scan_rows=[row])
        production = out["stations"]["7"]["resource_production_per_day"]
        self.assertAlmostEqual(production["wood"], 2 * (1 / 10 * 86400), places=1)
        self.assertAlmostEqual(production["copper"], 1 * (1 / 10000 * 86400), places=1)

    def test_converter_module_does_not_contribute_production(self):
        row = self._setup_row(cargo=[
            {"item_key": "item:station:ration_processor", "item_category": "station",
             "quantity": 50, "stats": {"Cycle Input": "2 Space Corn + 5 Credits",
                                        "Cycle Output": "1 Ration", "Cycle Time": "12 seconds"}},
        ])
        out = bot_activity_summary([], colony_scan_rows=[row])
        self.assertEqual(out["stations"]["7"]["resource_production_per_day"], {})

    def test_non_producing_module_does_not_contribute_production(self):
        row = self._setup_row(cargo=[
            {"item_key": "item:station:vacuskin", "item_category": "station",
             "quantity": 1, "stats": {"Shield Bank": "250000 HP"}},
        ])
        out = bot_activity_summary([], colony_scan_rows=[row])
        self.assertEqual(out["stations"]["7"]["resource_production_per_day"], {})

    def test_hull_fill_pct_is_none_when_hull_space_is_zero(self):
        row = self._setup_row(hull_space=0)
        out = bot_activity_summary([], colony_scan_rows=[row])
        self.assertIsNone(out["stations"]["7"]["hull_fill_pct"])

    def test_station_seen_only_via_colony_scan_still_gets_an_entry(self):
        # The whole point of this feature: a station piling up resources that no bot has ever
        # docked at (zero log_rows for it) must still be surfaced, not silently dropped.
        out = bot_activity_summary([], colony_scan_rows=[self._setup_row()])
        st = out["stations"]["7"]
        self.assertEqual(st["bot_docks"], 0)
        self.assertEqual(st["txns"], 0)
        self.assertEqual(st["hull_used"], 719978)

    def test_station_seen_only_via_logs_has_null_storage_fields(self):
        # None (uncaptured), never 0 -- 0 would be indistinguishable from a real, confirmed-empty
        # station.
        rows = self._rows(("7", "Ver-A4 (TB-ExampleAlt3) docked."))
        out = bot_activity_summary(rows)
        st = out["stations"]["7"]
        self.assertIsNone(st["hull_used"])
        self.assertIsNone(st["resources"])
        self.assertIsNone(st["resource_caps"])

    def test_newest_row_wins_for_a_duplicate_station_id(self):
        old = self._setup_row(captured_at=1, hull_used=100)
        new = self._setup_row(captured_at=2, hull_used=999)
        out = bot_activity_summary([], colony_scan_rows=[old, new])
        self.assertEqual(out["stations"]["7"]["hull_used"], 999)

    def test_non_station_setup_sources_are_ignored(self):
        rows = [{"source": "colony_data", "key": "7", "captured_at": 1,
                  "context": {}, "data": {"hull_used": 500}}]
        out = bot_activity_summary([], colony_scan_rows=rows)
        self.assertEqual(out["stations"], {})

    def test_station_names_resolve_for_colony_scan_only_stations_too(self):
        out = bot_activity_summary(
            [], colony_scan_rows=[self._setup_row()],
            station_names={"7": "eskvenyreth - 1"})
        self.assertIn("eskvenyreth - 1", out["stations"])


class StationCadenceAndBackedUpTests(unittest.TestCase):
    """median_dock_gap_seconds (real per-station bot cadence) and backed_up (hull_fill_pct >=
    BACKED_UP_HULL_FILL_PCT) -- see bot_activity_summary's own docstring for why the median of
    real consecutive-dock gaps replaces the original (wrong) docks-over-whole-capture-window
    approach: it looked like "once per 27 minutes" for a bot that was really cycling every
    ~40-50 real seconds, because the naive version divided by station_logs.csv's entire
    multi-day span rather than the bot's actual active window."""

    def _dock_rows(self, station_id, *times):
        return [{"station_id": station_id, "id": str(i), "time": str(t),
                  "text": "Ves-A2 (TB-ExamplePlayer) docked."}
                for i, t in enumerate(times)]

    def _setup_row(self, station_id="7", hull_used=100, hull_space=200):
        return {"source": "station_setup", "key": station_id, "captured_at": 1,
                "context": {}, "data": {"hull_used": hull_used, "hull_space": hull_space,
                                         "resource_caps": {}, "cargo": []}}

    def test_median_gap_from_real_consecutive_dock_timestamps(self):
        # 1000 -> 1040 -> 1090 -> 1100: gaps 40, 50, 10 -> median 40
        rows = self._dock_rows("7", 1000, 1040, 1090, 1100)
        out = bot_activity_summary(rows)
        self.assertEqual(out["stations"]["7"]["median_dock_gap_seconds"], 40)

    def test_a_single_multi_hour_reconnect_gap_does_not_skew_the_median(self):
        # One huge outlier gap (a session reconnect) among many short, regular ones --
        # the median should reflect the real cadence, not get dragged toward the mean.
        times = [1000 + i * 45 for i in range(20)]
        times.append(times[-1] + 7200)   # one 2-hour gap
        rows = self._dock_rows("7", *times)
        out = bot_activity_summary(rows)
        self.assertEqual(out["stations"]["7"]["median_dock_gap_seconds"], 45)

    def test_fewer_than_two_docks_gives_no_gap(self):
        rows = self._dock_rows("7", 1000)
        out = bot_activity_summary(rows)
        self.assertIsNone(out["stations"]["7"]["median_dock_gap_seconds"])

    def test_no_docks_at_all_gives_no_gap(self):
        out = bot_activity_summary([], colony_scan_rows=[self._setup_row()])
        self.assertIsNone(out["stations"]["7"]["median_dock_gap_seconds"])

    def test_backed_up_true_at_or_above_the_threshold(self):
        out = bot_activity_summary([], colony_scan_rows=[
            self._setup_row(hull_used=180, hull_space=200)])   # 90%
        self.assertTrue(out["stations"]["7"]["backed_up"])

    def test_backed_up_false_below_the_threshold(self):
        out = bot_activity_summary([], colony_scan_rows=[
            self._setup_row(hull_used=100, hull_space=200)])   # 50%
        self.assertFalse(out["stations"]["7"]["backed_up"])

    def test_backed_up_is_none_when_hull_fill_is_unknown(self):
        rows = self._dock_rows("7", 1000, 1040)
        out = bot_activity_summary(rows)
        self.assertIsNone(out["stations"]["7"]["backed_up"])

    def test_a_hammered_but_still_backed_up_station_shows_both_signals(self):
        # The real paelruxaius-1 case: frequent real dock cadence AND still near hull cap --
        # the bot is working flat out and it's still not enough, not a neglect problem.
        times = [1000 + i * 45 for i in range(50)]
        rows = self._dock_rows("7", *times)
        out = bot_activity_summary(rows, colony_scan_rows=[
            self._setup_row(hull_used=199, hull_space=200)])
        st = out["stations"]["7"]
        self.assertTrue(st["backed_up"])
        self.assertEqual(st["median_dock_gap_seconds"], 45)
        self.assertEqual(st["bot_docks"], 50)


class ExtractorOutputTests(unittest.TestCase):
    def test_extractor_rate_from_cycle_output_and_time(self):
        item = {"stats": {"Cycle Output": "3 Metal Ore", "Cycle Time": "10 seconds"}}
        name, per_day = _extractor_output_per_unit_per_day(item)
        self.assertEqual(name, "Metal Ore")
        self.assertAlmostEqual(per_day, 25920.0)

    def test_handles_thousands_separator_in_cycle_output(self):
        item = {"stats": {"Cycle Output": "1,000 Silicon", "Cycle Time": "10 seconds"}}
        name, per_day = _extractor_output_per_unit_per_day(item)
        self.assertEqual(name, "Silicon")
        self.assertAlmostEqual(per_day, 1000 / 10 * 86400)

    def test_converter_with_cycle_input_is_excluded(self):
        # A converter (consumes another resource) rather than a raw extractor -- its real
        # throughput is capped by available input stock, not modeled here.
        item = {"stats": {"Cycle Input": "2 Space Corn + 5 Credits",
                           "Cycle Output": "1 Ration", "Cycle Time": "12 seconds"}}
        self.assertIsNone(_extractor_output_per_unit_per_day(item))

    def test_module_without_cycle_stats_is_excluded(self):
        item = {"stats": {"Shield Bank": "250000 HP"}}
        self.assertIsNone(_extractor_output_per_unit_per_day(item))

    def test_missing_stats_dict_is_excluded(self):
        self.assertIsNone(_extractor_output_per_unit_per_day({}))


class ResolveResourceKeyTests(unittest.TestCase):
    def test_exact_slug_match(self):
        self.assertEqual(_resolve_resource_key("Metal Ore", {"metal_ore"}), "metal_ore")

    def test_trailing_s_added_when_plural_key_is_the_real_one(self):
        # A module's Cycle Output text ("Ration") doesn't always agree with the game's own
        # plural resource key ("rations") -- confirmed against real captured data.
        self.assertEqual(_resolve_resource_key("Ration", {"rations"}), "rations")

    def test_trailing_s_stripped_when_singular_key_is_the_real_one(self):
        self.assertEqual(_resolve_resource_key("Rations", {"ration"}), "ration")

    def test_falls_back_to_plain_slug_when_nothing_matches(self):
        self.assertEqual(_resolve_resource_key("Mystery Ore", set()), "mystery_ore")


class ShuttleFlowEdgeTests(unittest.TestCase):
    """_shuttle_flow_edges_from_colony_scan -- turning colony_scan_logger's captured
    "shuttle_network" rows (PS_SHUTTLE_DATA) into directed {(source, dest, resource): rate/day}
    edges. A route's direction is relative to the HUB whose dialog captured it."""

    DAY_RATE = 200 / 10 * 86400

    def _network_row(self, hub_id, routes, transfer_limit=200, transfer_interval=10.0):
        return {"source": "shuttle_network", "key": hub_id, "captured_at": 1, "context": {},
                "data": {"transfer_limit": transfer_limit,
                         "transfer_interval_seconds": transfer_interval, "routes": routes}}

    def test_import_route_flows_from_the_target_to_the_hub(self):
        row = self._network_row("1", [
            {"direction": "import", "enabled": True, "target_station_id": 2,
             "resource_key": "wood"},
        ])
        edges = _shuttle_flow_edges_from_colony_scan([row])
        self.assertEqual(list(edges), [("2", "1", "wood")])
        self.assertAlmostEqual(edges[("2", "1", "wood")], self.DAY_RATE)

    def test_export_route_flows_from_the_hub_to_the_target(self):
        row = self._network_row("1", [
            {"direction": "export", "enabled": True, "target_station_id": 2,
             "resource_key": "wood"},
        ])
        edges = _shuttle_flow_edges_from_colony_scan([row])
        self.assertEqual(list(edges), [("1", "2", "wood")])

    def test_disabled_route_is_excluded(self):
        row = self._network_row("1", [
            {"direction": "import", "enabled": False, "target_station_id": 2,
             "resource_key": "wood"},
        ])
        self.assertEqual(_shuttle_flow_edges_from_colony_scan([row]), {})

    def test_an_import_and_an_export_for_the_same_flow_are_both_real_routes(self):
        # Real data (kortherxaxar-1/-2): the hub's own "import silicon from -2" (route 1126) and
        # -2's own "export silicon to the hub" (route 1136) are distinct routes with distinct
        # ids, each with its own independent capacity -- so the same edge sums both.
        rows = [
            self._network_row("1", [{"direction": "import", "enabled": True,
                                      "target_station_id": 2, "resource_key": "silicon"}]),
            self._network_row("2", [{"direction": "export", "enabled": True,
                                      "target_station_id": 1, "resource_key": "silicon"}]),
        ]
        edges = _shuttle_flow_edges_from_colony_scan(rows)
        self.assertAlmostEqual(edges[("2", "1", "silicon")], 2 * self.DAY_RATE)

    def test_lunar_station_id_used_when_target_station_id_missing(self):
        row = self._network_row("1", [
            {"direction": "import", "enabled": True, "lunar_station_id": 9,
             "resource_key": "wood"},
        ])
        self.assertIn(("9", "1", "wood"), _shuttle_flow_edges_from_colony_scan([row]))

    def test_unknown_direction_is_ignored(self):
        row = self._network_row("1", [
            {"direction": "sideways", "enabled": True, "target_station_id": 2,
             "resource_key": "wood"},
        ])
        self.assertEqual(_shuttle_flow_edges_from_colony_scan([row]), {})

    def test_non_shuttle_network_sources_are_ignored(self):
        row = {"source": "station_setup", "key": "1", "captured_at": 1, "context": {},
               "data": {"cargo": []}}
        self.assertEqual(_shuttle_flow_edges_from_colony_scan([row]), {})


class SolveShuttleFlowsTests(unittest.TestCase):
    """_solve_shuttle_flows -- how much each edge really moves, limited by what the SENDER has,
    including what is itself shuttled in from further upstream."""

    def test_moves_at_most_the_senders_supply(self):
        outflow, inflow = _solve_shuttle_flows({("a", "b", "wood"): 1000.0}, {("a", "wood"): 300.0})
        self.assertAlmostEqual(outflow[("a", "wood")]["rate"], 300.0)
        self.assertAlmostEqual(inflow[("b", "wood")]["rate"], 300.0)

    def test_moves_at_most_the_declared_capacity(self):
        outflow, _ = _solve_shuttle_flows({("a", "b", "wood"): 100.0}, {("a", "wood"): 5000.0})
        self.assertAlmostEqual(outflow[("a", "wood")]["rate"], 100.0)

    def test_nothing_moves_out_of_a_sender_with_unknown_supply(self):
        outflow, inflow = _solve_shuttle_flows({("a", "b", "wood"): 1000.0}, {})
        self.assertEqual((outflow, inflow), ({}, {}))

    def test_splits_across_multiple_destinations_in_proportion_to_capacity(self):
        edges = {("a", "b", "wood"): 300.0, ("a", "c", "wood"): 100.0}
        outflow, inflow = _solve_shuttle_flows(edges, {("a", "wood"): 200.0})
        self.assertAlmostEqual(inflow[("b", "wood")]["rate"], 150.0)
        self.assertAlmostEqual(inflow[("c", "wood")]["rate"], 50.0)

    def test_a_chain_passes_supply_through_two_hops(self):
        # a -> b -> c: b re-ships what it received from a, so c gets it too.
        edges = {("a", "b", "wood"): 1000.0, ("b", "c", "wood"): 1000.0}
        _, inflow = _solve_shuttle_flows(edges, {("a", "wood"): 400.0})
        self.assertAlmostEqual(inflow[("c", "wood")]["rate"], 400.0)

    def test_a_same_resource_cycle_terminates(self):
        # Not seen in real data, but a misconfigured A <-> B loop must not hang or blow up.
        edges = {("a", "b", "wood"): 1000.0, ("b", "a", "wood"): 1000.0}
        outflow, inflow = _solve_shuttle_flows(edges, {("a", "wood"): 100.0})
        self.assertLessEqual(outflow[("a", "wood")]["rate"], 1000.0)


class ResourceBalanceTests(unittest.TestCase):
    """resource_activity/resource_balance/overall_pct_satisfied/bot_breakdown -- crossing real
    bot purchase quantities (from station_logs.csv txn lines) against real production (from
    colony_scan_rows' equipped extractors, see StationStorageFromColonyScanTests)."""

    def _dock_and_buy_rows(self, station_id, bot, tag, dock_times, buys):
        """``dock_times`` is every real dock timestamp for ``bot`` at this station.
        ``buys`` is a list of (time, qty, item) bought immediately after some of those docks --
        deliberately not one-per-dock, since a real bot doesn't buy on every single visit."""
        rows = []
        for i, t in enumerate(dock_times):
            rows.append({"station_id": station_id, "id": f"d{i}", "time": str(t),
                          "text": f"{bot} ({tag}) docked."})
        for i, (t, qty, item) in enumerate(buys):
            rows.append({"station_id": station_id, "id": f"b{i}", "time": str(t),
                          "text": f"{bot} ({tag}) bought {qty} {item} for {qty} credits."})
        return rows

    def _dock_and_sell_rows(self, station_id, bot, tag, dock_times, sells):
        rows = []
        for i, t in enumerate(dock_times):
            rows.append({"station_id": station_id, "id": f"d{i}", "time": str(t),
                          "text": f"{bot} ({tag}) docked."})
        for i, (t, qty, item) in enumerate(sells):
            rows.append({"station_id": station_id, "id": f"s{i}", "time": str(t),
                          "text": f"{bot} ({tag}) sold {qty} {item} for {qty} credits."})
        return rows

    def _production_row(self, station_id, quantity, cycle_output, cycle_time):
        return {"source": "station_setup", "key": station_id, "captured_at": 1, "context": {},
                "data": {"hull_used": 0, "hull_space": 0, "resource_caps": {},
                         "resource_reserves": {}, "cargo": [
                    {"item_key": "item:station:test_extractor", "item_category": "station",
                     "quantity": quantity,
                     "stats": {"Cycle Output": cycle_output, "Cycle Time": cycle_time}},
                ]}}

    def _shuttle_row(self, hub_id, target_station_id, resource_key,
                      transfer_limit=200, transfer_interval=10.0):
        return {"source": "shuttle_network", "key": hub_id, "captured_at": 1, "context": {},
                "data": {"transfer_limit": transfer_limit,
                         "transfer_interval_seconds": transfer_interval,
                         "routes": [{"direction": "import", "enabled": True,
                                     "target_station_id": target_station_id,
                                     "resource_key": resource_key}]}}

    def test_bought_per_day_uses_real_dock_cadence_not_wall_clock_span(self):
        # 3 docks 30s apart -> median gap 30 -> 2880 docks/day. 240 Metal Ore bought total
        # across those 3 docks -> 80/dock -> 80*2880 = 230,400/day. If this instead divided by
        # the log's raw wall-clock span it would be a much smaller (wrong) number.
        rows = self._dock_and_buy_rows(
            "7", "Ver-A1", "TB-ExamplePlayer", [1000, 1030, 1060],
            [(1000, 100, "Metal Ore"), (1030, 140, "Metal Ore")])
        out = bot_activity_summary(rows, colony_scan_rows=[
            self._production_row("7", 10, "1 Metal Ore", "10 seconds")])
        st = out["stations"]["7"]
        self.assertEqual(st["median_dock_gap_seconds"], 30)
        self.assertAlmostEqual(st["resource_balance"]["metal_ore"]["bought_per_day"], 230400.0, places=1)

    def test_pct_satisfied_and_overall_match_for_a_single_resource_station(self):
        rows = self._dock_and_buy_rows(
            "7", "Ver-A1", "TB-ExamplePlayer", [1000, 1030, 1060],
            [(1000, 100, "Metal Ore"), (1030, 140, "Metal Ore")])
        # Production: 10 units * (1 every 10s) = 86,400/day. Bought: 230,400/day (see above).
        # pct_satisfied = 230400/86400*100 = 266.666...7%.
        out = bot_activity_summary(rows, colony_scan_rows=[
            self._production_row("7", 10, "1 Metal Ore", "10 seconds")])
        st = out["stations"]["7"]
        self.assertAlmostEqual(st["resource_balance"]["metal_ore"]["pct_satisfied"], 266.7, places=1)
        # Only one resource is produced here, so the volume-weighted overall equals it exactly.
        self.assertAlmostEqual(st["overall_pct_satisfied"], 266.7, places=1)

    def test_resource_bought_with_no_known_extractor_has_no_pct_satisfied(self):
        rows = self._dock_and_buy_rows(
            "7", "Ver-A1", "TB-ExamplePlayer", [1000, 1030],
            [(1000, 50, "Titanium")])
        out = bot_activity_summary(rows)   # no colony_scan_rows at all -- no known production
        balance = out["stations"]["7"]["resource_balance"]["titanium"]
        self.assertIsNone(balance["production_per_day"])
        self.assertIsNotNone(balance["bought_per_day"])
        self.assertIsNone(balance["pct_satisfied"])

    def test_resource_balance_all_none_without_real_dock_data(self):
        # Production is known (colony_scan captured), but there are zero real log rows to derive
        # a cadence from -- bought_per_day/pct_satisfied/overall must all stay None (unknown),
        # never a misleading 0.
        out = bot_activity_summary([], colony_scan_rows=[
            self._production_row("7", 10, "1 Metal Ore", "10 seconds")])
        st = out["stations"]["7"]
        self.assertIsNone(st["resource_balance"]["metal_ore"]["bought_per_day"])
        self.assertIsNone(st["resource_balance"]["metal_ore"]["pct_satisfied"])
        self.assertIsNone(st["overall_pct_satisfied"])

    def test_overall_pct_satisfied_is_volume_weighted_not_averaged(self):
        # Resource A: fully satisfied (100%) but tiny (1,000/day). Resource B: completely
        # unsatisfied (0%) but huge (1,000,000/day). A naive average of the two percentages
        # would read 50%; the real, volume-weighted answer is close to 0%.
        rows = self._dock_and_buy_rows(
            "7", "Ver-A1", "TB-ExamplePlayer", [1000, 1000 + 86400],  # exactly 1 dock/day cadence
            [(1000, 1000, "Widget A"), (1000 + 86400, 1000, "Widget A")])
        out = bot_activity_summary(rows, colony_scan_rows=[
            {"source": "station_setup", "key": "7", "captured_at": 1, "context": {}, "data": {
                "hull_used": 0, "hull_space": 0, "resource_caps": {}, "resource_reserves": {},
                "cargo": [
                    {"item_key": "item:station:a_maker", "item_category": "station",
                     "quantity": 1,
                     "stats": {"Cycle Output": "1000 Widget A", "Cycle Time": "86400 seconds"}},
                    {"item_key": "item:station:b_maker", "item_category": "station",
                     "quantity": 1,
                     "stats": {"Cycle Output": "1000000 Widget B", "Cycle Time": "86400 seconds"}},
                ]}},
        ])
        st = out["stations"]["7"]
        self.assertEqual(st["resource_balance"]["widget_a"]["pct_satisfied"], 100.0)
        self.assertEqual(st["resource_balance"]["widget_b"]["pct_satisfied"], 0.0)
        self.assertLess(st["overall_pct_satisfied"], 5.0)

    def test_bot_breakdown_tracks_docks_and_resources_per_bot_at_this_station(self):
        rows = self._dock_and_buy_rows(
            "7", "Ver-A1", "TB-ExamplePlayer", [1000, 1030],
            [(1000, 50, "Wood"), (1030, 30, "Wood")])
        out = bot_activity_summary(rows)
        breakdown = out["stations"]["7"]["bot_breakdown"]["Ver-A1"]
        self.assertEqual(breakdown["tag"], "TB-ExamplePlayer")
        self.assertEqual(breakdown["docks"], 2)
        self.assertEqual(breakdown["resources"]["wood"], {"bought": 80, "sold": 0})

    def test_bot_breakdown_keeps_different_bots_separate(self):
        rows = (
            self._dock_and_buy_rows("7", "Ver-A1", "TB-ExamplePlayer", [1000], [(1000, 50, "Wood")])
            + self._dock_and_buy_rows("7", "Ver-A2", "TB-ExampleAlt", [1010], [(1010, 999, "Wood")])
        )
        out = bot_activity_summary(rows)
        breakdown = out["stations"]["7"]["bot_breakdown"]
        self.assertEqual(breakdown["Ver-A1"]["resources"]["wood"]["bought"], 50)
        self.assertEqual(breakdown["Ver-A2"]["resources"]["wood"]["bought"], 999)

    def test_feeder_with_zero_bot_docks_still_gets_shuttle_coverage(self):
        # The actual scenario this was built for: station 9 never sees a bot dock, but the hub
        # (station 1) pulls its wood via the orbital shuttle network -- must NOT read as 0%.
        out = bot_activity_summary([], colony_scan_rows=[
            self._production_row("9", 1, "1 Wood", "10 seconds"),  # 8,640/day
            self._shuttle_row("1", 9, "wood", transfer_limit=1000000, transfer_interval=10.0),
        ], station_names={"1": "Hub Station", "9": "Feeder Station"})
        st = out["stations"]["Feeder Station"]
        self.assertEqual(st["bot_docks"], 0)
        balance = st["resource_balance"]["wood"]
        self.assertIsNone(balance["bought_per_day"])
        self.assertAlmostEqual(balance["shuttled_per_day"], 8640.0, places=1)
        self.assertEqual(balance["pct_satisfied"], 100.0)
        self.assertEqual(balance["shuttled_to"], ["Hub Station"])
        self.assertEqual(st["overall_pct_satisfied"], 100.0)

    def test_shuttled_capacity_is_capped_at_the_resource_own_production(self):
        # Regression guard: the route's declared max (huge, module-rated) must never be reported
        # as-is against a trickle resource's real (tiny) production -- found by computing this
        # against real data: a Copper Extractor making ~8.6/day against a 200-units/10s shuttle
        # module read as a 20,000,000%+ pct_satisfied before this cap was added.
        out = bot_activity_summary([], colony_scan_rows=[
            self._production_row("9", 1, "1 Copper", "10000 seconds"),  # 8.64/day
            self._shuttle_row("1", 9, "copper", transfer_limit=200, transfer_interval=10.0),
        ])
        balance = out["stations"]["9"]["resource_balance"]["copper"]
        self.assertAlmostEqual(balance["shuttled_per_day"], 8.6, places=1)
        self.assertEqual(balance["pct_satisfied"], 100.0)

    def test_shuttle_throughput_below_production_shows_the_shuttle_as_the_bottleneck(self):
        # The opposite case: production genuinely exceeds what the shuttle module can move --
        # pct_satisfied should reflect the module's real cap, not silently hide the shortfall.
        out = bot_activity_summary([], colony_scan_rows=[
            self._production_row("9", 1000, "1 Metal Ore", "10 seconds"),  # 8,640,000/day
            self._shuttle_row("1", 9, "metal_ore", transfer_limit=200, transfer_interval=10.0),
        ])
        balance = out["stations"]["9"]["resource_balance"]["metal_ore"]
        self.assertAlmostEqual(balance["shuttled_per_day"], 200 / 10 * 86400, places=1)
        self.assertLess(balance["pct_satisfied"], 100.0)

    def test_bought_and_shuttled_amounts_add_together(self):
        rows = self._dock_and_buy_rows(
            "9", "Ver-A1", "TB-ExamplePlayer", [1000, 1030], [(1000, 50, "Wood")])
        out = bot_activity_summary(rows, colony_scan_rows=[
            self._production_row("9", 1, "1 Wood", "1 seconds"),  # 86,400/day
            self._shuttle_row("1", 9, "wood", transfer_limit=1000000, transfer_interval=10.0),
        ])
        balance = out["stations"]["9"]["resource_balance"]["wood"]
        self.assertGreater(balance["bought_per_day"], 0)
        self.assertGreater(balance["shuttled_per_day"], 0)
        expected_pct = round(100 * (balance["bought_per_day"] + balance["shuttled_per_day"])
                              / balance["production_per_day"], 1)
        self.assertAlmostEqual(balance["pct_satisfied"], expected_pct, places=1)

    def test_export_route_does_not_count_as_the_targets_own_coverage(self):
        # "export" is the hub pushing ITS OWN stock out to the target -- the target receiving
        # something is not the target offloading its own production.
        out = bot_activity_summary([], colony_scan_rows=[
            self._production_row("9", 1, "1 Wood", "10 seconds"),
            {"source": "shuttle_network", "key": "1", "captured_at": 1, "context": {},
             "data": {"transfer_limit": 200, "transfer_interval_seconds": 10.0,
                       "routes": [{"direction": "export", "enabled": True,
                                   "target_station_id": 9, "resource_key": "wood"}]}},
        ])
        self.assertEqual(out["stations"]["9"]["resource_balance"]["wood"]["shuttled_per_day"], 0.0)

    def test_shuttle_route_for_uncaptured_station_creates_no_fabricated_entry(self):
        # A route referencing a station whose own Storage tab has never been captured (no
        # production known, no log rows either) must not conjure up an entry with made-up data.
        out = bot_activity_summary([], colony_scan_rows=[self._shuttle_row("1", 9, "gold")])
        self.assertNotIn("9", out["stations"])

    def test_sold_with_no_local_extractor_still_creates_effective_supply(self):
        # A bot SELLING a resource TO the station (importing it from elsewhere) is real inflow
        # needing coverage, even with zero local production -- previously this resource could
        # never get a pct_satisfied at all without a known extractor.
        rows = self._dock_and_sell_rows(
            "9", "Ver-A1", "TB-ExamplePlayer", [1000, 1030, 1060],
            [(1000, 100, "Wood"), (1030, 200, "Wood")])
        out = bot_activity_summary(rows)
        balance = out["stations"]["9"]["resource_balance"]["wood"]
        self.assertIsNone(balance["production_per_day"])
        self.assertGreater(balance["sold_per_day"], 0)
        # Real signal: something is being delivered in, but nothing buys it back out yet.
        self.assertEqual(balance["pct_satisfied"], 0.0)

    def test_sold_and_bought_of_the_same_resource_combine_for_the_denominator(self):
        rows = [
            {"station_id": "9", "id": "d0", "time": "1000", "text": "Ver-A1 (TB-ExamplePlayer) docked."},
            {"station_id": "9", "id": "d1", "time": "1030", "text": "Ver-A1 (TB-ExamplePlayer) docked."},
            {"station_id": "9", "id": "s0", "time": "1000",
             "text": "Ver-A1 (TB-ExamplePlayer) sold 100 Wood for 100 credits."},
            {"station_id": "9", "id": "b0", "time": "1030",
             "text": "Ver-A1 (TB-ExamplePlayer) bought 100 Wood for 100 credits."},
        ]
        out = bot_activity_summary(rows)   # no production row -- effective supply is sold alone
        balance = out["stations"]["9"]["resource_balance"]["wood"]
        self.assertAlmostEqual(balance["bought_per_day"], balance["sold_per_day"], places=1)
        self.assertEqual(balance["pct_satisfied"], 100.0)

    def test_shuttle_cap_uses_effective_supply_not_just_local_production(self):
        # Regression guard for the fix: previously the shuttle cap used production_per_day
        # alone, so a station mostly fed by bot deliveries (not its own tiny extractor) would
        # have its real shuttled capacity throttled down to the tiny local number.
        rows = [
            {"station_id": "9", "id": "d0", "time": "1000", "text": "Ver-A1 (TB-ExamplePlayer) docked."},
            {"station_id": "9", "id": "d1", "time": "1030", "text": "Ver-A1 (TB-ExamplePlayer) docked."},
            {"station_id": "9", "id": "s0", "time": "1000",
             "text": "Ver-A1 (TB-ExamplePlayer) sold 100000 Wood for 100000 credits."},
        ]
        out = bot_activity_summary(rows, colony_scan_rows=[
            self._production_row("9", 1, "1 Wood", "10000 seconds"),  # ~8.6/day, negligible
            self._shuttle_row("1", 9, "wood", transfer_limit=100, transfer_interval=10.0),  # 864,000/day cap
        ])
        balance = out["stations"]["9"]["resource_balance"]["wood"]
        self.assertAlmostEqual(balance["shuttled_per_day"], 864000.0, delta=1.0)
        self.assertGreater(balance["shuttled_per_day"], balance["production_per_day"] * 1000)


    def test_hub_supply_includes_what_its_feeders_shuttle_in(self):
        # The reported bug (kortherxaxar-1 read 307% while sitting at its storage cap): the hub's
        # bot hauls out the feeders' shuttled-in stock too, so measuring it against the hub's OWN
        # extraction alone overstates coverage ~3x. Hub 1 and feeder 9 each make 86,400/day
        # (10 extractors, 1 Wood / 10s); the bot buys 172,800/day -- that's exactly everything
        # there is to clear (100%), not 200%.
        rows = self._dock_and_buy_rows("1", "Ver-A1", "TB-ExamplePlayer", [1000, 1030],
                                        [(1000, 120, "Wood")])
        out = bot_activity_summary(rows, colony_scan_rows=[
            self._production_row("1", 10, "1 Wood", "10 seconds"),
            self._production_row("9", 10, "1 Wood", "10 seconds"),
            self._shuttle_row("1", 9, "wood", transfer_limit=1000000, transfer_interval=10.0),
        ], station_names={"1": "Hub", "9": "Feeder"})
        hub = out["stations"]["Hub"]["resource_balance"]["wood"]
        self.assertAlmostEqual(hub["bought_per_day"], 172800.0, places=1)
        self.assertAlmostEqual(hub["shuttled_in_per_day"], 86400.0, places=1)
        self.assertEqual(hub["shuttled_from"], ["Feeder"])
        self.assertEqual(hub["pct_satisfied"], 100.0)
        self.assertEqual(out["stations"]["Hub"]["overall_pct_satisfied"], 100.0)

    def test_an_export_route_gives_the_target_extra_supply(self):
        # Hub 1 pushes its own wood OUT to station 9 ("export") -- station 9 now has that much
        # more to move on, and hub 1 is the one being offloaded.
        out = bot_activity_summary([], colony_scan_rows=[
            self._production_row("1", 10, "1 Wood", "10 seconds"),
            self._production_row("9", 10, "1 Wood", "10 seconds"),
            {"source": "shuttle_network", "key": "1", "captured_at": 1, "context": {},
             "data": {"transfer_limit": 1000000, "transfer_interval_seconds": 10.0,
                       "routes": [{"direction": "export", "enabled": True,
                                   "target_station_id": 9, "resource_key": "wood"}]}},
        ], station_names={"1": "Hub", "9": "Target"})
        hub = out["stations"]["Hub"]["resource_balance"]["wood"]
        target = out["stations"]["Target"]["resource_balance"]["wood"]
        self.assertAlmostEqual(hub["shuttled_per_day"], 86400.0, places=1)
        self.assertEqual(hub["shuttled_to"], ["Target"])
        self.assertAlmostEqual(target["shuttled_in_per_day"], 86400.0, places=1)
        self.assertEqual(target["shuttled_from"], ["Hub"])
        self.assertEqual(target["shuttled_per_day"], 0.0)

    def test_inflow_from_a_station_with_unknown_supply_adds_nothing(self):
        # Station 9 is named in the hub's route, but its own Storage tab was never captured, so
        # what it could send is unknown -- must not be guessed at.
        out = bot_activity_summary([], colony_scan_rows=[
            self._production_row("1", 10, "1 Wood", "10 seconds"),
            self._shuttle_row("1", 9, "wood", transfer_limit=1000000, transfer_interval=10.0),
        ])
        hub = out["stations"]["1"]["resource_balance"]["wood"]
        self.assertEqual(hub["shuttled_in_per_day"], 0.0)
        self.assertEqual(hub["shuttled_from"], [])


class LoadUnloadLineTests(unittest.TestCase):
    """A bot on LOAD / UNLOAD orders logs withdrew / deposited lines with no credits (Talreis since 2026-10-26); they
    used to be skipped, so every station served only by such bots read as 0% taken."""

    def test_bot_withdraw_counts_as_bought_for_no_credits(self):
        r = parse_log_line("Metal Ore (TB-No U) withdrew 32,643 Metal Ore.")
        self.assertEqual(r, {"actor": "Metal Ore", "tag": "TB-No U", "is_bot": True, "kind": "bought",
                             "qty": 32643, "item": "Metal Ore", "price": 0, "transfer": True})

    def test_bot_deposit_counts_as_sold(self):
        r = parse_log_line("b (TB-222) deposited 15,468 Metal Ore.")
        self.assertEqual((r["kind"], r["qty"], r["item"], r["price"], r["actor"], r["tag"]),
                         ("sold", 15468, "Metal Ore", 0, "b", "TB-222"))

    def test_item_names_with_spaces_and_an_apostrophe_survive(self):
        r = parse_log_line("a (TB-111) withdrew 5 King's Siege Cannon.")
        self.assertEqual((r["kind"], r["qty"], r["item"]), ("bought", 5, "King's Siege Cannon"))

    def test_a_players_own_withdraw_or_deposit_is_still_not_bot_activity(self):
        self.assertIsNone(parse_log_line("ExamplePlayer withdrew 5 Workers."))
        self.assertIsNone(parse_log_line("zestyjuicers deposited 1,000 Metal Ore."))
        self.assertIsNone(parse_log_line("No U deposited 250,000 credits."))

    def test_plain_buys_do_not_gain_the_transfer_flag(self):
        self.assertNotIn("transfer", parse_log_line("a (TB-1) bought 5 Wood for 5 credits."))

    def test_a_load_route_is_measured_like_a_buy_route(self):
        # 3 docks 30 s apart, 240 withdrawn over them -> 80 a dock x 2,880 docks/day = 230,400/day, the same as the
        # equivalent bought lines in ResourceBalanceTests.
        rows = []
        for i, t in enumerate((1000, 1030, 1060)):
            rows.append({"station_id": "7", "id": f"d{i}", "time": str(t), "text": "Ore (TB-No U) docked."})
        for i, (t, qty) in enumerate(((1000, 100), (1030, 140))):
            rows.append({"station_id": "7", "id": f"w{i}", "time": str(t),
                         "text": f"Ore (TB-No U) withdrew {qty} Metal Ore."})
        extractors = {"source": "station_setup", "key": "7", "captured_at": 1, "context": {},
                      "data": {"hull_used": 0, "hull_space": 0, "resource_caps": {}, "resource_reserves": {}, "cargo": [
                          {"item_key": "item:station:test_extractor", "item_category": "station", "quantity": 10,
                           "stats": {"Cycle Output": "1 Metal Ore", "Cycle Time": "10 seconds"}}]}}
        out = bot_activity_summary(rows, colony_scan_rows=[extractors])
        st = out["stations"]["7"]
        self.assertEqual(st["txns"], 2)
        balance = st["resource_balance"]["metal_ore"]
        self.assertAlmostEqual(balance["bought_per_day"], 230400.0, places=1)
        self.assertAlmostEqual(balance["pct_satisfied"], 266.7, places=1)

    def test_log_rows_counts_every_fetched_line_so_a_bot_free_log_is_told_from_no_log(self):
        rows = [{"station_id": "5", "id": "1", "time": "1", "text": "ExamplePlayer equipped 10 Station Expander Gamma."},
                {"station_id": "5", "id": "2", "time": "2", "text": "ExamplePlayer docked."},
                {"station_id": "6", "id": "3", "time": "3", "text": "a (TB-1) docked."}]
        storage = {"source": "station_setup", "key": "7", "captured_at": 1, "context": {},
                   "data": {"hull_used": 0, "hull_space": 0, "resource_caps": {}, "resource_reserves": {}, "cargo": []}}
        out = bot_activity_summary(rows, colony_scan_rows=[storage])["stations"]
        self.assertEqual((out["5"]["log_rows"], out["5"]["bot_docks"]), (2, 0))
        self.assertEqual((out["6"]["log_rows"], out["6"]["bot_docks"]), (1, 1))
        self.assertEqual(out["7"]["log_rows"], 0)          # a station with setup data and no log at all

    def test_an_unload_counts_as_what_the_station_receives(self):
        rows = [{"station_id": "1", "id": "d0", "time": "1000", "text": "b (TB-222) docked."},
                {"station_id": "1", "id": "d1", "time": "1030", "text": "b (TB-222) docked."},
                {"station_id": "1", "id": "u0", "time": "1000", "text": "b (TB-222) deposited 300 Metal Ore."}]
        out = bot_activity_summary(rows)
        self.assertEqual(out["stations"]["1"]["resource_activity"]["metal_ore"], {"bought": 0, "sold": 300})


T0 = 1_000_000


def _log(station, time, text):
    return {"station_id": station, "id": f"{station}-{time}-{text[:6]}", "time": str(time), "text": text}


def _extractors(station="7", quantity=10):
    """10 extractors of 1 Metal Ore per 10 s: 86,400 a day."""
    return {"source": "station_setup", "key": station, "captured_at": 1, "context": {},
            "data": {"hull_used": 0, "hull_space": 0, "resource_caps": {}, "resource_reserves": {}, "cargo": [
                {"item_key": "item:station:test_extractor", "item_category": "station", "quantity": quantity,
                 "stats": {"Cycle Output": "1 Metal Ore", "Cycle Time": "10 seconds"}}]}}


def _burst(station, bot, tag, start, docks=5, step=60, qty=100):
    """``docks`` docks ``step`` seconds apart from ``start``, each with a purchase of ``qty`` Metal Ore."""
    rows = []
    for i in range(docks):
        t = start + i * step
        rows.append(_log(station, t, f"{bot} ({tag}) docked."))
        rows.append(_log(station, t, f"{bot} ({tag}) bought {qty} Metal Ore for {qty} credits."))
    return rows


class NewestWindowTests(unittest.TestCase):
    def test_one_continuous_stretch_is_the_window(self):
        times = [T0, T0 + 3600, T0 + 7200, T0 + 4 * 3600]
        self.assertEqual(_newest_window(times), (T0, T0 + 4 * 3600))

    def test_a_gap_over_six_hours_starts_a_new_window_and_only_the_newest_counts(self):
        times = [T0, T0 + 3600, T0 + 3 * 3600,                      # an older stretch of 3 h ...
                 T0 + 10 * 3600, T0 + 11 * 3600, T0 + 14 * 3600]     # ... a 7 h gap, then the newest, 4 h long
        self.assertEqual(_newest_window(times), (T0 + 10 * 3600, T0 + 14 * 3600))

    def test_a_gap_of_exactly_six_hours_still_belongs_to_the_window(self):
        self.assertEqual(_newest_window([T0, T0 + 6 * 3600, T0 + 12 * 3600]), (T0, T0 + 12 * 3600))

    def test_a_stretch_shorter_than_three_hours_is_no_window(self):
        self.assertIsNone(_newest_window([T0, T0 + 3 * 3600 - 1]))
        self.assertEqual(_newest_window([T0, T0 + 3 * 3600]), (T0, T0 + 3 * 3600))
        self.assertIsNone(_newest_window([T0]))
        self.assertIsNone(_newest_window([]))

    def test_the_input_order_does_not_matter(self):
        self.assertEqual(_newest_window([T0 + 4 * 3600, T0, T0 + 2 * 3600]), (T0, T0 + 4 * 3600))


class ObservedRateTests(unittest.TestCase):
    """What bots REALLY took in the newest log window, next to what they would take running non-stop."""

    def _station(self, extra_rows=()):
        # 5 docks a minute apart buying 100 each, then nothing until a player docks 4 hours later: the log window is
        # 4 hours long and holds 500 units.
        rows = _burst("7", "Ore", "TB-No U", T0) + [_log("7", T0 + 4 * 3600, "ExamplePlayer docked.")] + list(extra_rows)
        return bot_activity_summary(rows, colony_scan_rows=[_extractors()])["stations"]["7"]

    def test_the_window_is_reported_in_hours(self):
        self.assertEqual(self._station()["log_window"], {"start": T0, "end": T0 + 4 * 3600, "hours": 4.0})

    def test_observed_is_what_was_taken_over_the_window_and_non_stop_is_the_loop_extrapolation(self):
        balance = self._station()["resource_balance"]["metal_ore"]
        self.assertAlmostEqual(balance["bought_observed_per_day"], 3000.0, places=1)       # 500 in 4 h
        self.assertAlmostEqual(balance["bought_per_day"], 144000.0, places=1)              # 100 a dock, 1,440 docks/day
        self.assertAlmostEqual(balance["pct_observed"], 3.5, places=1)                     # 3,000 of 86,400 made
        self.assertAlmostEqual(balance["pct_satisfied"], 166.7, places=1)

    def test_the_station_wide_percentages_come_both_ways(self):
        st = self._station()
        self.assertAlmostEqual(st["overall_pct_observed"], 3.5, places=1)
        self.assertAlmostEqual(st["overall_pct_satisfied"], 166.7, places=1)

    def test_taking_nothing_in_a_window_is_a_fact_and_not_unknown(self):
        rows = [_log("7", T0, "ExamplePlayer docked."), _log("7", T0 + 4 * 3600, "ExamplePlayer docked.")]
        balance = bot_activity_summary(rows, colony_scan_rows=[_extractors()])["stations"]["7"]["resource_balance"]["metal_ore"]
        self.assertEqual((balance["bought_observed_per_day"], balance["pct_observed"]), (0.0, 0.0))
        self.assertIsNone(balance["bought_per_day"])           # nothing to extrapolate a loop time from

    def test_without_a_window_the_observed_figures_are_unknown_and_the_non_stop_ones_stay(self):
        rows = _burst("7", "Ore", "TB-No U", T0)                 # 4 minutes of log
        st = bot_activity_summary(rows, colony_scan_rows=[_extractors()])["stations"]["7"]
        balance = st["resource_balance"]["metal_ore"]
        self.assertIsNone(st["log_window"])
        self.assertIsNone(balance["bought_observed_per_day"])
        self.assertIsNone(balance["sold_observed_per_day"])
        self.assertIsNone(balance["pct_observed"])
        self.assertIsNone(st["overall_pct_observed"])
        self.assertIsNotNone(balance["bought_per_day"])

    def test_an_older_stretch_of_log_is_not_counted(self):
        older = _burst("7", "Ore", "TB-No U", T0 - 20 * 3600, docks=5, qty=1_000_000)     # a huge haul, a day ago
        balance = self._station(older)["resource_balance"]["metal_ore"]
        self.assertAlmostEqual(balance["bought_observed_per_day"], 3000.0, places=1)

    def test_what_a_bot_sells_in_counts_as_supply_in_the_observed_percentage_too(self):
        rows = _burst("7", "Ore", "TB-No U", T0) + [_log("7", T0 + 4 * 3600, "Ore (TB-No U) sold 500 Metal Ore for 1 credits.")]
        balance = bot_activity_summary(rows, colony_scan_rows=[_extractors()])["stations"]["7"]["resource_balance"]["metal_ore"]
        self.assertAlmostEqual(balance["sold_observed_per_day"], 3000.0, places=1)
        # supply 86,400 made + 3,000 sold in; offtake 3,000 bought
        self.assertAlmostEqual(balance["pct_observed"], 100 * 3000 / 89400, places=1)

    def test_a_bot_reports_its_loop_load_and_both_rates(self):
        bot = self._station()["bot_breakdown"]["Ore"]
        self.assertEqual((bot["loop_seconds"], bot["window_docks"], bot["docks"]), (60, 5, 5))
        self.assertEqual(bot["resource_rates"]["metal_ore"],
                         {"per_dock": 100, "running_per_day": 144000, "observed_per_day": 3000})

    def test_a_bot_that_docked_before_the_window_has_a_rate_but_nothing_observed(self):
        rows = (_burst("7", "Old", "TB-1", T0 - 20 * 3600) + _burst("7", "Ore", "TB-2", T0)
                + [_log("7", T0 + 4 * 3600, "ExamplePlayer docked.")])
        bots = bot_activity_summary(rows)["stations"]["7"]["bot_breakdown"]
        self.assertEqual((bots["Old"]["window_docks"], bots["Old"]["resource_rates"]["metal_ore"]["observed_per_day"]), (0, 0))
        self.assertEqual(bots["Old"]["resource_rates"]["metal_ore"]["running_per_day"], 144000)

    def test_a_bot_remembers_when_it_last_docked_at_the_station(self):
        rows = _burst("7", "Ore", "TB-1", T0) + _burst("7", "Ore", "TB-1", T0 + 10 * 3600, docks=2)
        self.assertEqual(bot_activity_summary(rows)["stations"]["7"]["bot_breakdown"]["Ore"]["last_dock"], T0 + 10 * 3600 + 60)
        txn_only = [_log("7", T0, "Ore (TB-1) bought 5 Wood for 5 credits.")]
        self.assertIsNone(bot_activity_summary(txn_only)["stations"]["7"]["bot_breakdown"]["Ore"]["last_dock"])

    def test_a_bot_with_one_dock_has_a_load_but_no_loop(self):
        rows = _burst("7", "Ore", "TB-1", T0, docks=1) + [_log("7", T0 + 4 * 3600, "ExamplePlayer docked.")]
        bot = bot_activity_summary(rows)["stations"]["7"]["bot_breakdown"]["Ore"]
        self.assertIsNone(bot["loop_seconds"])
        self.assertEqual(bot["resource_rates"]["metal_ore"]["per_dock"], 100)
        self.assertIsNone(bot["resource_rates"]["metal_ore"]["running_per_day"])
        self.assertEqual(bot["resource_rates"]["metal_ore"]["observed_per_day"], 600)

    def test_the_summary_stays_json_serialisable(self):
        import json
        json.dumps(bot_activity_summary(_burst("7", "Ore", "TB-1", T0) + [_log("7", T0 + 4 * 3600, "ExamplePlayer docked.")]))

    def test_a_bot_unloading_is_measured_like_one_loading(self):
        rows = []
        for i in range(5):
            rows.append(_log("7", T0 + i * 60, "Ore (TB-1) docked."))
            rows.append(_log("7", T0 + i * 60, "Ore (TB-1) deposited 100 Metal Ore."))
        rows.append(_log("7", T0 + 4 * 3600, "ExamplePlayer docked."))
        rates = bot_activity_summary(rows)["stations"]["7"]["bot_breakdown"]["Ore"]["resource_rates"]["metal_ore"]
        self.assertEqual(rates, {"per_dock": 100, "running_per_day": 144000, "observed_per_day": 3000})

    def _shuttle(self, hub_id, target_id, key):
        return {"source": "shuttle_network", "key": hub_id, "captured_at": 1, "context": {},
                "data": {"transfer_limit": 1_000_000, "transfer_interval_seconds": 10.0,
                         "routes": [{"direction": "import", "enabled": True, "target_station_id": target_id,
                                     "resource_key": key}]}}

    def test_a_shuttle_counts_in_the_observed_percentage_at_both_ends(self):
        # Feeder 9 makes 86,400 Metal Ore a day and no bot docks there; the hub 1 imports all of it by shuttle and a bot
        # took 3,000 a day from the hub in the newest window.
        rows = ([_log("9", T0, "ExamplePlayer docked."), _log("9", T0 + 4 * 3600, "ExamplePlayer docked.")]
                + _burst("1", "Ore", "TB-1", T0) + [_log("1", T0 + 4 * 3600, "ExamplePlayer docked.")])
        out = bot_activity_summary(rows, colony_scan_rows=[_extractors("9"), self._shuttle("1", 9, "metal_ore")],
                                   station_names={"1": "Hub", "9": "Feeder"})["stations"]
        feeder = out["Feeder"]["resource_balance"]["metal_ore"]
        self.assertAlmostEqual(feeder["pct_observed"], 100.0, places=1)          # all of it leaves by shuttle
        hub = out["Hub"]["resource_balance"]["metal_ore"]
        self.assertAlmostEqual(hub["pct_observed"], 100 * 3000 / 86400, places=1)  # 3,000 of the 86,400 that arrives


class SharedNicknameTests(unittest.TestCase):
    """Several accounts each have a bot called "3" (or "Metal Ore"): told apart by their account tag, never merged."""

    def _rows(self):
        rows = []
        for t in (0, 100, 200):
            rows.append(_log("7", T0 + t, "3 (TB-111) docked."))
            rows.append(_log("7", T0 + t + 50, "3 (TB-222) docked."))
        rows.append(_log("7", T0 + 10, "Solo (TB-111) docked."))
        return rows

    def test_a_shared_nickname_is_keyed_with_its_account_a_unique_one_is_not(self):
        out = bot_activity_summary(self._rows())
        self.assertEqual(sorted(out["stations"]["7"]["bot_breakdown"]), ["3 (TB-111)", "3 (TB-222)", "Solo"])
        self.assertEqual(sorted(out["bots"]), ["3 (TB-111)", "3 (TB-222)", "Solo"])
        self.assertEqual(out["bots"]["3 (TB-222)"]["nickname"], "3")
        self.assertEqual(out["bots"]["Solo"]["nickname"], "Solo")

    def test_each_account_gets_its_own_docks_loop_and_unique_bot_count(self):
        st = bot_activity_summary(self._rows())["stations"]["7"]
        self.assertEqual(st["unique_bots"], 3)
        for key, tag in (("3 (TB-111)", "TB-111"), ("3 (TB-222)", "TB-222")):
            self.assertEqual((st["bot_breakdown"][key]["docks"], st["bot_breakdown"][key]["tag"]), (3, tag))
            self.assertEqual(st["bot_breakdown"][key]["loop_seconds"], 100)    # merged they would read 50

    def test_the_fleet_scan_match_needs_the_account_as_well_as_the_nickname(self):
        fleet = [{"key": "1", "captured_at": 1, "context": {"username": "111"},
                  "data": {"nickname": "3", "ship_type": "Caravan", "state": "hauling", "bot_cargo_cap": 9}}]
        bots = bot_activity_summary(self._rows(), bot_fleet_rows=fleet)["bots"]
        self.assertTrue(bots["3 (TB-111)"]["roster_known"])
        self.assertEqual(bots["3 (TB-111)"]["cargo_cap"], 9)
        self.assertFalse(bots["3 (TB-222)"]["roster_known"])
        self.assertFalse(bots["Solo"]["roster_known"])

    def test_a_newer_fleet_row_of_the_same_bot_wins_whatever_the_order(self):
        fleet = [{"key": "2", "captured_at": 2, "context": {"username": "111"}, "data": {"nickname": "3", "state": "hauling"}},
                 {"key": "1", "captured_at": 1, "context": {"username": "111"}, "data": {"nickname": "3", "state": "idle"}}]
        self.assertEqual(bot_activity_summary(self._rows(), bot_fleet_rows=fleet)["bots"]["3 (TB-111)"]["state"], "hauling")

    def test_a_fleet_row_without_a_username_still_matches_by_nickname(self):
        fleet = [{"key": "1", "captured_at": 1, "context": {}, "data": {"nickname": "Solo", "state": "hauling"}}]
        self.assertTrue(bot_activity_summary(self._rows(), bot_fleet_rows=fleet)["bots"]["Solo"]["roster_known"])

    def test_two_accounts_with_a_bot_of_one_name_keep_their_own_fleet_rows(self):
        fleet = [{"key": "1", "captured_at": 1, "context": {"username": "111"}, "data": {"nickname": "3", "state": "idle"}},
                 {"key": "2", "captured_at": 2, "context": {"username": "222"}, "data": {"nickname": "3", "state": "hauling"}}]
        bots = bot_activity_summary(self._rows(), bot_fleet_rows=fleet)["bots"]
        self.assertEqual((bots["3 (TB-111)"]["state"], bots["3 (TB-222)"]["state"]), ("idle", "hauling"))


if __name__ == "__main__":
    unittest.main()

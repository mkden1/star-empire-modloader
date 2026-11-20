"""prefab_extras: docking / management permissions, storage caps and trade entries kept with a station prefab and
re-sent after a load, plus the contents viewer."""
import modloader
import os
import tempfile
import threading
import time
import unittest
from collections import OrderedDict
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame

import prefab_extras as pfx
import prefabs

EXTRAS = {"version": 1, "docking": "officer", "management": "owner",
          "resource_caps": {"item:resource:metal": 5000},
          "trades": [{"category": "Resources", "item_type": "metal", "sell_price": 12, "buy_price": 10,
                      "min_qty": 0, "max_qty": 500}]}


def sample(extras=True, name="dps"):
    prefab = {"name": name,
              "cargo": [{"item_key": "item:station:station_beam_cannon_mk3", "item_type": "station_beam_cannon_mk3",
                         "display_name": "Station Beam Cannon Mk III", "quantity": 1},
                        {"item_key": prefabs.VACUSKIN_ITEM_KEY, "item_type": "vacuskin",
                         "display_name": "VacuSkin", "quantity": 1},
                        {"item_key": "workers", "item_type": "workers", "display_name": "Workers", "quantity": 7}],
              "equipped_modules": [{"module_type": "station_beam_cannon_mk3", "quantity": 1, "position": 1},
                                   {"module_type": "station_expander_gamma", "quantity": 1, "position": 0}],
              "plugins": [], "resource_caps": {}}
    if extras:
        prefab[pfx.EXTRAS_KEY] = {**EXTRAS, "trades": [dict(row) for row in EXTRAS["trades"]]}
    return prefab


class Fake:
    """The parts of SolarSystemWindow the extras touch, with the game's own method names."""

    def __init__(self):
        self.sent, self.log, self.saved, self.refreshed = [], [], [], 0
        self._lock = threading.RLock()
        self._station_overlay = {"kind": "player_station", "data": {
            "attached_station_id": 7, "public_dock": False, "dock_min_rank": "officer",
            "management_min_rank": ""}}
        self._ps_resource_caps = {"item:resource:metal": 5000, "item:resource:ice": 100}
        self._ps_trade_mgr = {"station_id": 7, "import": None, "config": {
            ("Resources", "metal"): {"buy_price": 10, "sell_price": 12, "min_qty": 0, "max_qty": 500},
            ("Resources", "ice"): {"buy_price": 1, "sell_price": 2, "min_qty": 5, "max_qty": 50}}}
        self._ps_prefab_can_load = True
        self._coalition_state = {"coalition_id": 3}
        self._PS_TRADE_IMPORT_PART_BYTES = 1024

    @staticmethod
    def _ps_trade_import_parts(rows, limit):
        return [list(rows)]

    def _ps_trade_send_part(self):
        imp = self._ps_trade_mgr["import"]
        self.sent.append(("PS_TRADE_IMPORT", {"station_id": imp["station_id"], "rows": imp["parts"][0]}))

    def _send_json(self, cmd, payload):
        self.sent.append((cmd, payload))

    def chat_log(self, text, colour=None):
        self.log.append(text)


def make_class():
    """A fresh window class with the game's wrapped methods (each test installs on its own)."""
    class Window(Fake):
        def _send_json(self, cmd, payload):
            self.sent.append((cmd, payload))

        def request_ps_trade_manager(self):
            self.sent.append(("PS_TRADE_MANAGER_REQUEST", None))

        def set_ps_prefab_snapshot(self, data):
            self.saved.append(data)

        def set_ps_station_cargo(self, data):
            if "prefab_can_load" in data:
                self._ps_prefab_can_load = bool(data["prefab_can_load"])

        def _refresh_ps_prefab_dialog(self):
            self.refreshed += 1

        def _draw_ps_prefab_dialog(self, ctx):
            self.drawn = getattr(self, "drawn", 0) + 1

        def apply_ps_trade_import_result(self, data):
            imp = self._ps_trade_mgr["import"]
            if imp and data.get("station_id") == imp["station_id"]:
                self._ps_trade_mgr["import"] = None
    return Window


class PolicyTests(unittest.TestCase):
    def test_the_policies_read_the_way_the_permission_dialogs_read_them(self):
        self.assertEqual(pfx.current_policies({"public_dock": True, "dock_min_rank": "officer"}),
                         {"docking": "public", "management": "owner"})
        self.assertEqual(pfx.current_policies({"dock_min_rank": " Officer ", "management_min_rank": "Leader"}),
                         {"docking": "officer", "management": "leader"})
        self.assertEqual(pfx.current_policies({}), {"docking": "owner", "management": "owner"})
        self.assertEqual(pfx.current_policies(None), {"docking": "owner", "management": "owner"})

    def test_labels(self):
        self.assertEqual(pfx.policy_label("public"), "Public (anyone)")
        self.assertEqual(pfx.policy_label("owner"), "Owner only")
        self.assertEqual(pfx.policy_label(""), "Owner only")
        self.assertEqual(pfx.policy_label("senior_officer"), "Senior Officer & above")


class CaptureTests(unittest.TestCase):
    def test_everything_is_read_from_the_live_state(self):
        extras = pfx.capture(Fake(), 7)
        self.assertEqual(extras["version"], 1)
        self.assertEqual((extras["docking"], extras["management"]), ("officer", "owner"))
        self.assertEqual(extras["resource_caps"], {"item:resource:ice": 100, "item:resource:metal": 5000})
        self.assertEqual([(r["item_type"], r["buy_price"], r["sell_price"], r["min_qty"], r["max_qty"])
                          for r in extras["trades"]], [("ice", 1, 2, 5, 50), ("metal", 10, 12, 0, 500)])

    def test_a_trade_list_that_belongs_to_another_station_is_not_taken(self):
        window = Fake()
        window._ps_trade_mgr["station_id"] = 99
        self.assertIsNone(pfx.capture(window, 7)["trades"])

    def test_no_trade_data_at_all_is_not_an_empty_list(self):
        window = Fake()
        window._ps_trade_mgr = {"station_id": None, "config": {}}
        self.assertIsNone(pfx.capture(window, 7)["trades"])
        window._ps_trade_mgr = {"station_id": 7, "config": {}}
        self.assertEqual(pfx.capture(window, 7)["trades"], [])          # known, and empty

    def test_the_policies_of_another_stations_overlay_are_not_taken(self):
        window = Fake()
        window._station_overlay["data"]["attached_station_id"] = 99
        extras = pfx.capture(window, 7)
        self.assertNotIn("docking", extras)
        self.assertNotIn("management", extras)

    def test_no_overlay_and_odd_caps_do_not_break_it(self):
        window = Fake()
        window._station_overlay = None
        window._ps_resource_caps = {"bad key\x01": 5}
        extras = pfx.capture(window, 7)
        self.assertNotIn("docking", extras)
        self.assertEqual(extras["resource_caps"], {})

    def test_strip_gives_back_the_servers_snapshot_without_touching_the_original(self):
        prefab = sample()
        clean = pfx.strip(prefab)
        self.assertNotIn(pfx.EXTRAS_KEY, clean)
        self.assertIn(pfx.EXTRAS_KEY, prefab)
        self.assertEqual({k: v for k, v in prefab.items() if k != pfx.EXTRAS_KEY}, clean)


class ApplyTests(unittest.TestCase):
    def test_every_part_is_sent_with_the_command_the_dialogs_use(self):
        window = Fake()
        lines = pfx.apply(window, EXTRAS, 7)
        self.assertEqual([cmd for cmd, _ in window.sent],
                         ["SET_PLAYER_STATION_DOCK_PERMS", "SET_PLAYER_STATION_MANAGEMENT_PERMS",
                          "PS_MERGE_RESOURCE_CAPS", "PS_TRADE_IMPORT"])
        self.assertEqual(window.sent[0][1], {"station_id": 7, "policy": "officer"})
        self.assertEqual(window.sent[1][1], {"station_id": 7, "policy": "owner"})
        self.assertEqual(window.sent[2][1], {"station_id": 7, "resource_caps": {"item:resource:metal": 5000}})
        self.assertEqual(window.sent[3][1]["rows"], EXTRAS["trades"])
        self.assertEqual(window._ps_trade_mgr["import"]["prefab"], True)
        self.assertEqual(window._ps_trade_mgr["import"]["station_id"], 7)
        self.assertEqual(lines, ["Prefab: applied docking Officer & above, management Owner only, 1 storage cap, "
                                 "1 trade entry."])

    def test_a_rank_policy_needs_a_coalition(self):
        window = Fake()
        window._coalition_state = {}
        lines = pfx.apply(window, {"docking": "officer", "management": "owner"}, 7)
        self.assertEqual(window.sent, [("SET_PLAYER_STATION_MANAGEMENT_PERMS", {"station_id": 7, "policy": "owner"})])
        self.assertEqual(len(lines), 2)
        self.assertIn("skipped docking Officer & above (you are not in a coalition)", lines[1])

    def test_public_and_owner_need_no_coalition_and_management_is_never_public(self):
        window = Fake()
        window._coalition_state = {}
        pfx.apply(window, {"docking": "public", "management": "public"}, 7)
        self.assertEqual(window.sent, [("SET_PLAYER_STATION_DOCK_PERMS", {"station_id": 7, "policy": "public"})])

    def test_nothing_saved_sends_nothing(self):
        window = Fake()
        self.assertEqual(pfx.apply(window, {"version": 1, "resource_caps": {}, "trades": None}, 7), [])
        self.assertEqual(pfx.apply(window, {"version": 1, "trades": []}, 7), [])       # known-empty: leave the list alone
        self.assertEqual(window.sent, [])

    def test_a_running_import_is_not_started_over(self):
        window = Fake()
        window._ps_trade_mgr["import"] = {"station_id": 7, "sent": 1, "parts": [[]]}
        lines = pfx.apply(window, {"trades": EXTRAS["trades"]}, 7)
        self.assertEqual(window.sent, [])
        self.assertIn("an import is already running", lines[0])


class LoadFlowTests(unittest.TestCase):
    def setUp(self):
        pfx.reset_for_tests()
        self.window = make_class()()

    def payload(self, extras=True):
        return {"station_id": 7, "name": "dps", "prefab": sample(extras)}

    def test_the_server_gets_the_snapshot_without_our_part(self):
        sent = pfx.begin_load(self.window, self.payload())
        self.assertNotIn(pfx.EXTRAS_KEY, sent["prefab"])
        self.assertEqual(sent["station_id"], 7)
        self.assertEqual(self.window._pfx_pending["extras"], EXTRAS)

    def test_a_prefab_without_extras_is_sent_as_it_is_and_nothing_waits(self):
        payload = self.payload(extras=False)
        self.assertIs(pfx.begin_load(self.window, payload), payload)
        self.assertNotIn("_pfx_pending", self.window.__dict__)

    def test_the_extras_go_out_when_the_station_reports_it_took_the_load(self):
        pfx.begin_load(self.window, self.payload())
        pfx.on_cargo(self.window, {"station_id": 7})                  # still loadable: not yet
        self.assertEqual(self.window.sent, [])
        self.window._ps_prefab_can_load = False
        pfx.on_cargo(self.window, {"station_id": 7})
        self.assertEqual(len(self.window.sent), 4)
        self.assertIsNone(self.window._pfx_pending)
        self.assertTrue(self.window.log[0].startswith("Prefab: applied"))
        pfx.on_cargo(self.window, {"station_id": 7})                  # only once
        self.assertEqual(len(self.window.sent), 4)

    def test_a_cargo_push_for_another_station_does_not_count(self):
        pfx.begin_load(self.window, self.payload())
        self.window._ps_prefab_can_load = False
        pfx.on_cargo(self.window, {"station_id": 99})
        self.assertEqual(self.window.sent, [])
        self.assertIsNotNone(self.window._pfx_pending)

    def test_an_unconfirmed_load_gives_up_and_says_so(self):
        pfx.begin_load(self.window, self.payload())
        pfx.tick(self.window, time.monotonic() + pfx.LOAD_WAIT_S - 1)
        self.assertIsNotNone(self.window._pfx_pending)
        pfx.tick(self.window, time.monotonic() + pfx.LOAD_WAIT_S + 1)
        self.assertIsNone(self.window._pfx_pending)
        self.assertEqual(self.window.sent, [])
        self.assertIn('Prefab "dps": the load was not confirmed', self.window.log[0])
        self.window._ps_prefab_can_load = False
        pfx.on_cargo(self.window, {"station_id": 7})                  # a late push applies nothing
        self.assertEqual(self.window.sent, [])


class SectionsTests(unittest.TestCase):
    def lines(self, prefab):
        return pfx.sections(prefab)

    def texts(self, prefab):
        return [(kind, left, right) for kind, left, right in self.lines(prefab)]

    def test_every_part_of_a_prefab_with_extras_is_listed(self):
        rows = self.texts(sample())
        self.assertEqual(rows[0], ("title", "dps", ""))
        self.assertEqual(rows[1][1], "8 items  /  2 equipped  /  0 plugins")      # the dialog's own count: workers too
        self.assertEqual(rows[2], ("sub", "Also saved: permissions, 1 storage cap, 1 trade entry.", ""))
        self.assertIn(("row", "Station Beam Cannon Mk III", "x1"), rows)
        self.assertIn(("dim", "VacuSkin (built in)", "x1"), rows)
        self.assertIn(("row", "Workers", "x7"), rows)
        self.assertIn(("row", "1. Station Expander Gamma", ""), rows)       # position order, name from the cargo row
        self.assertIn(("row", "2. Station Beam Cannon Mk III", ""), rows)
        self.assertIn(("row", "Metal", "5,000"), rows)
        self.assertIn(("row", "Docking", "Officer & above"), rows)
        self.assertIn(("row", "Management", "Owner only"), rows)
        trade = next(row for row in rows if row[1] == "Metal" and "buy" in row[2])
        self.assertEqual(trade[2], "buy 10 / sell 12   0-500")
        self.assertEqual([r[1] for r in rows if r[0] == "head"],
                         ["STATION CONTENTS", "EQUIPPED MODULES", "PLUGINS", "STORAGE CAPS", "PERMISSIONS",
                          "TRADE BAY", "WHEN LOADED"])
        self.assertIn("docking / management permissions, storage caps, trade entries", rows[-1][1])

    def test_an_old_prefab_says_what_it_does_not_have(self):
        rows = self.texts(sample(extras=False))
        self.assertIn(("dim", "Not saved (this prefab was made before they were kept).", ""), rows)
        self.assertIn(("dim", "Not saved.", ""), rows)
        self.assertIn(("dim", "The server restores the station contents only.", ""), rows)
        self.assertEqual(rows[2][1], "No permissions, storage caps or trade entries saved (made before they were kept).")

    def test_trades_not_captured_say_how_to_fix_it(self):
        prefab = sample()
        prefab[pfx.EXTRAS_KEY]["trades"] = None
        rows = self.texts(prefab)
        self.assertTrue(any(row[1].startswith("Not captured") for row in rows))
        self.assertEqual(rows[2][1], "Also saved: permissions, 1 storage cap, trade entries NOT captured.")
        self.assertNotIn("trade entries", rows[-1][1])

    def test_the_servers_own_resource_caps_are_listed_too(self):
        prefab = sample(extras=False)
        prefab["resource_caps"] = {"item:resource:ice": 300}
        self.assertIn(("row", "Ice", "300"), self.texts(prefab))

    def test_plugins_and_an_unreadable_prefab(self):
        prefab = sample()
        prefab["plugins"] = [{"plugin_id": "refined_speed", "display_name": "Refined Speed"}, "odd_thing"]
        rows = self.texts(prefab)
        self.assertIn(("row", "Refined Speed", ""), rows)
        self.assertIn(("row", "Odd Thing", ""), rows)
        self.assertEqual(pfx.sections(None), [("warn", "That prefab could not be read.", "")])


class HookTests(unittest.TestCase):
    def setUp(self):
        pfx.reset_for_tests()
        self.cls = make_class()
        self.assertTrue(pfx.install(self.cls))
        self.window = self.cls()

    def test_install_is_idempotent_and_refuses_a_class_without_the_methods(self):
        self.assertFalse(pfx.install(self.cls))
        with self.assertLogs(pfx.logger, "WARNING"):
            self.assertFalse(pfx.install(type("Bare", (), {})))

    def test_saving_asks_for_the_trade_list_first_then_stores_the_extras_with_the_snapshot(self):
        self.window._send_json("PS_PREFAB_SAVE", {"station_id": 7, "name": "dps"})
        self.assertEqual([cmd for cmd, _ in self.window.sent], ["PS_TRADE_MANAGER_REQUEST", "PS_PREFAB_SAVE"])
        self.window.set_ps_prefab_snapshot({"station_id": 7, "name": "dps", "prefab": {"cargo": [], "plugins": []}})
        saved = self.window.saved[0]
        self.assertEqual(saved["name"], "dps")
        self.assertEqual(saved["prefab"]["cargo"], [])
        self.assertEqual(saved["prefab"][pfx.EXTRAS_KEY]["docking"], "officer")
        self.assertEqual(len(saved["prefab"][pfx.EXTRAS_KEY]["trades"]), 2)

    def test_the_servers_dict_is_not_changed_in_place(self):
        data = {"station_id": 7, "name": "dps", "prefab": {"cargo": []}}
        self.window.set_ps_prefab_snapshot(data)
        self.assertNotIn(pfx.EXTRAS_KEY, data["prefab"])

    def test_a_malformed_snapshot_reaches_the_game_untouched(self):
        for data in ({"station_id": 7, "name": "x", "prefab": "nope"}, {"prefab": None}, "junk"):
            self.window.set_ps_prefab_snapshot(data)
        self.assertEqual(len(self.window.saved), 3)
        self.assertTrue(pfx.enabled())

    def test_loading_sends_the_snapshot_without_extras_and_applies_them_when_the_cargo_says_it_took(self):
        self.window._send_json("PS_PREFAB_LOAD", {"station_id": 7, "name": "dps", "prefab": sample()})
        cmd, payload = self.window.sent[-1]
        self.assertEqual(cmd, "PS_PREFAB_LOAD")
        self.assertNotIn(pfx.EXTRAS_KEY, payload["prefab"])
        self.window.set_ps_station_cargo({"station_id": 7, "prefab_can_load": True})
        self.assertEqual(len(self.window.sent), 1)
        self.window.set_ps_station_cargo({"station_id": 7, "prefab_can_load": False})
        self.assertEqual([c for c, _ in self.window.sent[1:]],
                         ["SET_PLAYER_STATION_DOCK_PERMS", "SET_PLAYER_STATION_MANAGEMENT_PERMS",
                          "PS_MERGE_RESOURCE_CAPS", "PS_TRADE_IMPORT"])

    def test_other_commands_pass_straight_through(self):
        self.window._send_json("PS_SET_MOTD", {"station_id": 7})
        self.assertEqual(self.window.sent, [("PS_SET_MOTD", {"station_id": 7})])

    def test_the_trade_import_result_of_a_prefab_load_is_reported(self):
        self.window._ps_trade_mgr["import"] = {"station_id": 7, "sent": 1, "parts": [[]], "prefab": True}
        self.window.apply_ps_trade_import_result({"station_id": 7, "ok": True, "count": 2})
        self.assertEqual(self.window.log, ["Prefab: the station took 2 trade entries."])
        self.window._ps_trade_mgr["import"] = {"station_id": 7, "sent": 1, "parts": [[]], "prefab": True}
        self.window.apply_ps_trade_import_result({"station_id": 7, "ok": False, "reason": "list full"})
        self.assertIn("refused (list full)", self.window.log[1])

    def test_a_manual_import_is_left_alone(self):
        self.window._ps_trade_mgr["import"] = {"station_id": 7, "sent": 1, "parts": [[]]}
        self.window.apply_ps_trade_import_result({"station_id": 7, "ok": True, "count": 2})
        self.assertEqual(self.window.log, [])
        self.assertIsNone(self.window._ps_trade_mgr["import"])

    def test_refreshing_the_dialog_makes_the_viewer_read_the_prefab_again(self):
        pfx._view(self.window)["name"] = "dps"
        self.window._refresh_ps_prefab_dialog()
        self.assertEqual(self.window.refreshed, 1)
        self.assertIsNone(pfx._view(self.window)["name"])

    def test_a_failure_switches_it_off_and_the_game_still_runs(self):
        with mock.patch.object(pfx, "capture", side_effect=RuntimeError("boom")), \
                self.assertLogs(pfx.logger, "ERROR"):
            self.window.set_ps_prefab_snapshot({"station_id": 7, "name": "x", "prefab": {"cargo": []}})
        self.assertEqual(len(self.window.saved), 1)
        self.assertNotIn(pfx.EXTRAS_KEY, self.window.saved[0]["prefab"])
        self.assertFalse(pfx.enabled())
        self.window._send_json("PS_PREFAB_LOAD", {"station_id": 7, "name": "dps", "prefab": sample()})
        self.assertIn(pfx.EXTRAS_KEY, self.window.sent[-1][1]["prefab"])      # off: the payload is not touched

    def test_the_environment_switch(self):
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_NO_PREFAB_EXTRAS": "1"}):
            self.window.set_ps_prefab_snapshot({"station_id": 7, "name": "x", "prefab": {"cargo": []}})
        self.assertNotIn(pfx.EXTRAS_KEY, self.window.saved[0]["prefab"])


class ClientHookTests(unittest.TestCase):
    def test_client_py_installs_it_and_feeds_it_events(self):
        from pathlib import Path
        source = modloader.game_source("Client.py")
        self.assertIn("\nprefab_extras.install(SolarSystemWindow, _render_mixin_module)\n", source)
        self.assertIn("_frame_events = prefab_extras.filter_events(self, _frame_events)", source)

    def test_the_real_window_has_every_method_it_wraps(self):
        from Client import SolarSystemWindow
        self.assertTrue(SolarSystemWindow.__dict__.get("_prefab_extras_installed"))
        for name in ("_send_json", "set_ps_prefab_snapshot", "set_ps_station_cargo", "_refresh_ps_prefab_dialog",
                     "_draw_ps_prefab_dialog", "apply_ps_trade_import_result"):
            self.assertTrue(hasattr(getattr(SolarSystemWindow, name), "__wrapped__"), name)

    def test_the_game_still_sends_what_the_wrapper_forwards(self):
        """The real _send_json, wrapped: a load sends the snapshot without extras as one PS_PREFAB_LOAD line."""
        from Client import SolarSystemWindow
        window = SolarSystemWindow.__new__(SolarSystemWindow)
        window._ps_trade_mgr = {"station_id": 7, "config": {}, "import": None}
        lines = []
        window._send_fn = lines.append
        window._send_json("PS_PREFAB_LOAD", {"station_id": 7, "name": "dps", "prefab": sample()})
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("PS_PREFAB_LOAD {"))
        self.assertNotIn(pfx.EXTRAS_KEY, lines[0])
        self.assertIn('"workers"', lines[0])
        self.assertIsNotNone(window._pfx_pending)


class RealViewerTests(unittest.TestCase):
    """The game's own prefab dialog on a real SolarSystemWindow, with the viewer beside it."""

    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode((1280, 720))

    def setUp(self):
        pfx.reset_for_tests()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = prefabs.LauncherPrefabStore(os.path.join(self._tmp.name, "Prefabs.txt"))
        self.store.save("dps", sample())
        self.store.save("old", sample(extras=False, name="old"))
        patcher = mock.patch.object(pfx, "_store", lambda: self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch("Client.PREFAB_STORE", self.store)       # the game's refresh reads its own store
        patcher.start()
        self.addCleanup(patcher.stop)

    def window(self, scale=1.0):
        from Client import SolarSystemWindow
        window = SolarSystemWindow.__new__(SolarSystemWindow)
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
        window._ps_prefab_dialog = {
            "station_id": 7, "prefabs": [{"name": "dps", "item_count": 8}, {"name": "old", "item_count": 8}],
            "selected": "dps", "dropdown_open": False, "list_scroll": 0, "panel_rect": None, "close_rect": None,
            "header_rect": None, "row_rects": [], "delete_rects": [], "save_rect": None, "load_rect": None,
            "name_input": None, "delete_confirm": None}
        return window

    def draw(self, window, width=1280, height=720):
        from render_mixin import RenderFrame
        screen = pygame.Surface((width, height), pygame.SRCALPHA)
        screen.fill((6, 10, 20, 255))
        window._draw_ps_prefab_dialog(RenderFrame(screen, width, height, lambda *a: a, 1.0, {}, 1, 1, 1 / 60))
        return screen

    def test_the_viewer_sits_beside_the_dialog_and_the_dialog_covers_both(self):
        window = self.window()
        screen = self.draw(window)
        dialog = window._ps_prefab_dialog
        view = window._pfx_view
        base = pygame.Rect(390, 165, 500, 390)
        self.assertEqual(view["rect"].left, base.right + 8)
        self.assertLessEqual(view["rect"].right, 1280 - 8)                         # narrowed to the room there is
        self.assertEqual((view["rect"].top, view["rect"].height), (base.top, base.height))
        self.assertEqual(dialog["panel_rect"], base.union(view["rect"]))
        self.assertTrue(dialog["panel_rect"].collidepoint(view["rect"].center))     # a click on it is inside the dialog
        # something is drawn there (the plate), where the dialog alone leaves the screen untouched
        self.assertNotEqual(screen.get_at(view["rect"].center)[:3], (6, 10, 20))

    def test_the_placement_picks_the_side_with_room_and_narrows_to_fit(self):
        def place(panel, win_w):
            return pfx.viewer_rect(pygame.Rect(*panel), win_w, lambda value: value)

        self.assertEqual(place((710, 345, 500, 390), 1920), pygame.Rect(1218, 345, 400, 390))   # roomy: right, full width
        self.assertEqual(place((700, 100, 500, 390), 1280), pygame.Rect(292, 100, 400, 390))    # only the left has room
        self.assertEqual(place((250, 0, 500, 390), 1000), pygame.Rect(758, 0, 234, 390))        # narrowed
        self.assertEqual(place((30, 0, 500, 390), 560), pygame.Rect(152, 0, 400, 390))          # no room: over the edge

    def test_no_selection_a_prompt_or_no_dialog_means_no_viewer(self):
        window = self.window()
        self.draw(window)
        self.assertIsNotNone(window._pfx_view["rect"])
        window._ps_prefab_dialog["selected"] = None
        self.draw(window)
        self.assertIsNone(window._pfx_view["rect"])
        window._ps_prefab_dialog["selected"] = "dps"
        window._ps_prefab_dialog["name_input"] = {"text": ""}
        self.draw(window)
        self.assertIsNone(window._pfx_view["rect"])
        window._ps_prefab_dialog = None
        self.draw(window)

    def test_the_dialog_without_the_viewer_is_what_the_game_draws(self):
        window = self.window()
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_NO_PREFAB_EXTRAS": "1"}):
            self.draw(window)
        self.assertEqual(window._ps_prefab_dialog["panel_rect"], pygame.Rect(390, 165, 500, 390))

    def test_the_selected_prefab_is_read_once_and_again_after_a_refresh(self):
        window = self.window()
        with mock.patch.object(self.store, "get", wraps=self.store.get) as get:
            self.draw(window)
            self.draw(window)
            self.assertEqual(get.call_count, 1)
            window._ps_prefab_dialog["selected"] = "old"
            self.draw(window)
            self.assertEqual(get.call_count, 2)
            window._refresh_ps_prefab_dialog()
            self.draw(window)
            self.assertEqual(get.call_count, 3)
        self.assertEqual(window._pfx_view["lines"][0][1], "old")

    def test_a_prefab_that_vanished_shows_a_message_not_a_crash(self):
        window = self.window()
        window._ps_prefab_dialog["selected"] = "gone"
        self.draw(window)
        self.assertEqual(window._pfx_view["lines"], [("warn", "That prefab could not be read.", "")])
        self.assertTrue(pfx.enabled())

    def test_the_wheel_over_the_viewer_scrolls_it_and_is_not_passed_on(self):
        window = self.window()
        trades = [{"category": "Resources", "item_type": f"thing_{i}", "sell_price": i, "buy_price": i,
                   "min_qty": 0, "max_qty": 9} for i in range(60)]
        big = sample()
        big[pfx.EXTRAS_KEY]["trades"] = trades
        self.store.save("dps", big)
        self.draw(window)
        view = window._pfx_view
        self.assertGreater(view["max_scroll"], 0)
        wheel = pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-1)
        key = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a)
        with mock.patch("pygame.mouse.get_pos", return_value=view["rect"].center):
            kept = pfx.filter_events(window, [wheel, key])
        self.assertEqual(kept, [key])
        self.assertEqual(view["scroll"], min(view["step"], view["max_scroll"]))
        before = view["scroll"]
        with mock.patch("pygame.mouse.get_pos", return_value=(5, 5)):             # elsewhere: the game gets it
            kept = pfx.filter_events(window, [wheel])
        self.assertEqual(kept, [wheel])
        self.assertEqual(view["scroll"], before)
        with mock.patch("pygame.mouse.get_pos", return_value=view["rect"].center):
            pfx.filter_events(window, [pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=500)])
        self.assertEqual(view["scroll"], 0)                                         # clamped at the top
        with mock.patch("pygame.mouse.get_pos", return_value=view["rect"].center):
            pfx.filter_events(window, [pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-500)])
        self.assertEqual(view["scroll"], view["max_scroll"])

    def test_scrolling_changes_what_is_drawn_and_a_new_selection_starts_at_the_top(self):
        window = self.window()
        big = sample()
        big[pfx.EXTRAS_KEY]["trades"] = [{"category": "Resources", "item_type": f"thing_{i}", "sell_price": i,
                                         "buy_price": i, "min_qty": 0, "max_qty": 9} for i in range(60)]
        self.store.save("dps", big)
        first = self.draw(window)
        window._pfx_view["scroll"] = 120
        second = self.draw(window)
        rect = window._pfx_view["rect"]
        self.assertNotEqual(pygame.image.tostring(first.subsurface(rect), "RGB"),
                            pygame.image.tostring(second.subsurface(rect), "RGB"))
        window._ps_prefab_dialog["selected"] = "old"
        self.draw(window)
        self.assertEqual(window._pfx_view["scroll"], 0)

    def test_nothing_is_drawn_outside_the_viewer_when_the_content_is_longer_than_it(self):
        window = self.window()
        big = sample()
        big[pfx.EXTRAS_KEY]["trades"] = [{"category": "Resources", "item_type": f"thing_{i}", "sell_price": i,
                                         "buy_price": i, "min_qty": 0, "max_qty": 9} for i in range(80)]
        self.store.save("dps", big)
        small = self.draw(window)
        rect = window._pfx_view["rect"]
        huge_screen = self.draw(self.window(), height=720)
        # the area under the viewer (inside the screen, outside its rect) is the background
        below = pygame.Rect(rect.left, rect.bottom + 2, rect.width, 40)
        self.assertEqual(pygame.image.tostring(small.subsurface(below), "RGB"),
                         pygame.image.tostring(huge_screen.subsurface(below), "RGB"))

    def test_at_one_and_a_half_times_the_size(self):
        window = self.window(scale=1.5)
        window._s = lambda value: int(round(value * 1.5))
        self.draw(window, width=1920, height=1080)
        rect = window._pfx_view["rect"]
        self.assertEqual(rect.height, window._ps_prefab_dialog["panel_rect"].height)
        self.assertTrue(pygame.Rect(0, 0, 1920, 1080).contains(rect))


if __name__ == "__main__":
    unittest.main()

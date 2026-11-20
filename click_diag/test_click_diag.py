"""click_diag.py: one log line per left click saying what was in range and what the picker chose.

Diagnostics only - these tests also pin that the hooks are transparent: with the switch off, or when
the diagnostics fail, every wrapped method returns exactly what it would have."""
import modloader
import logging
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame

import capture_settings
import click_diag
import scan_writer
import target_picking

SCREEN = (1280, 720)


class _Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.display.init()
        pygame.display.set_mode(SCREEN)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(scan_writer.flush)
        capture_settings.configure_for_tests(os.path.join(self._tmp.name, "capture_settings.json"))
        self.addCleanup(capture_settings.configure_for_tests, None)
        click_diag.reset_for_tests()
        self.addCleanup(click_diag.reset_for_tests)


class FakeWindow:
    """The attributes and helpers the diagnostics read, with simple world data."""

    def __init__(self, zoom=0.05):
        self._zoom = zoom
        self._cam_x = self._cam_y = 50000.0
        self._lock = threading.RLock()
        self._layout = {"warp_gates": [], "planets": [], "sun": {}}
        self._npc_entities, self._in_dungeon = {}, False
        self._npc_stations, self._deployed_stations = [], []
        self._asteroids, self._cargo_boxes, self._dungeon_item_boxes = {}, {}, {}
        self._username, self._rc_bots = "me", {}
        self.entities = []
        self.begin_calls, self.pick_result = [], (None, None, None)
        self._gui_owner, self._esc_menu_open, self._options_open = None, False, False
        self._menu_open = self._galaxy_map_open = False
        self._station_overlay, self._mm_rect = None, None
        self._gui_windows = SimpleNamespace(
            route_pointer=lambda pos: SimpleNamespace(owner=self._gui_owner))
        self.steps = 0

    # screen position of a world point under the current camera
    def at(self, px, py):
        w, h = SCREEN
        return ((px - w * 0.5) / self._zoom + self._cam_x, (py - h * 0.5) / self._zoom + self._cam_y)

    def _find_my_entity(self):
        return {"effective_detection": 1.0}

    def _get_interpolated_entities(self):
        return list(self.entities)

    def _visible_to_local(self, entity, detection):
        return not entity.get("hidden", False)

    def _planet_body_world_pos(self, body, bodies):
        return body["x"], body["y"]

    def _deployed_station_world_pos(self, dep):
        return dep["x"], dep["y"]

    def _asteroid_world_pos(self, ast):
        return ast["x"], ast["y"]

    def _thr_begin_drag(self, mx, my):
        self.begin_calls.append((mx, my))

    def _pick_ship_click(self, click_pos, entities, world_to_screen):
        return self.pick_result

    def _step_local_physics(self, dt):
        self.steps += 1
        return "stepped"


class SnapshotTests(_Fixture):
    def test_what_is_under_a_click_is_listed_by_type_with_its_click_radius(self):
        win = FakeWindow(zoom=0.05)
        click = (640, 360)
        gx, gy = win.at(640, 360)                                     # a gate exactly under the click
        px, py = win.at(640 + 15, 360)                                # a planet 15 px to the side
        sx, sy = win.at(640 + 100, 360)                               # a station 100 px away
        win._layout = {
            "warp_gates": [{"id": 7, "x": gx, "y": gy, "radius": 40}],
            "planets": [{"id": 3, "x": px, "y": py, "radius": 80}],
            "sun": {"x": win.at(2000, 2000)[0], "y": win.at(2000, 2000)[1], "radius": 180}}
        win._npc_stations = [{"station_id": 9, "x": sx, "y": sy, "radius": 36}]
        bx, by = win.at(640 + 40, 360 + 10)
        win.entities = [{"player_id": 5, "username": "x", "x": bx, "y": by, "radius": 8}]
        win._rc_bots = {5: {}}
        snap = click_diag.snapshot(win, *click)
        self.assertEqual(snap["zoom"], 0.05)
        self.assertEqual(snap["gates"], "gate-7 0/24*")               # at zoom 0.05 a gate still has a 24 px radius
        self.assertEqual(snap["planets"], "planet-3 15/20*")
        self.assertEqual(snap["stations"], "station-9 100/30")        # in reach to list, outside its 30 px radius
        self.assertEqual(snap["ships"], "bot-5 41/5")                 # a bot 41 px away; hull radius is only 5 px here
        self.assertEqual(snap["sun"], "-")

    def test_asteroids_are_counted_and_a_hidden_ship_is_labelled(self):
        win = FakeWindow(zoom=0.05)
        win._asteroids = {i: {"x": win.at(640 + i * 3, 360)[0], "y": win.at(640 + i * 3, 360)[1], "radius": 30}
                          for i in range(4)}
        win._asteroids[99] = {"x": win.at(1200, 700)[0], "y": win.at(1200, 700)[1], "radius": 30}
        hx, hy = win.at(640 + 4, 360)
        win.entities = [{"player_id": 6, "username": "y", "x": hx, "y": hy, "radius": 8, "hidden": True}]
        snap = click_diag.snapshot(win, 640, 360)
        self.assertTrue(snap["asteroids"].startswith("4 in reach, nearest 0/18* of 5"), snap["asteroids"])
        self.assertEqual(snap["ships"], "ship-6(unseen) 4/5*")

    def test_the_click_radii_follow_the_zoom(self):
        for zoom, gate_px in ((0.4, 24 + 0), (0.05, 24)):
            win = FakeWindow(zoom=zoom)
            gx, gy = win.at(640, 360)
            win._layout = {"warp_gates": [{"id": 1, "x": gx, "y": gy, "radius": 40}], "planets": [], "sun": {}}
            hit = click_diag.snapshot(win, 640, 360)["gates"]
            expected = max(max(8, int(40 * zoom * click_diag._WARP_VIS_SCALE)) + 8, 24)
            self.assertEqual(hit, f"gate-1 0/{expected}*")

    def test_nothing_in_reach_is_a_dash(self):
        win = FakeWindow()
        snap = click_diag.snapshot(win, 640, 360)
        for key in ("ships", "gates", "planets", "stations", "boxes", "sun", "asteroids"):
            self.assertEqual(snap[key], "-", key)


class HookTests(_Fixture):
    def setUp(self):
        super().setUp()
        self.cls = type("DiagWin", (FakeWindow,), {})
        self.assertTrue(click_diag.install(self.cls))
        self.module = {"_cycle_overlapping_target": target_picking.cycle_overlapping_target}    # Client.py's globals
        self.assertTrue(click_diag.install_picker(self.module))
        self.win = self.cls(zoom=0.05)
        gx, gy = self.win.at(640, 360)
        self.win._layout = {"warp_gates": [{"id": 7, "x": gx, "y": gy, "radius": 40}], "planets": [], "sun": {}}

    def click(self, candidates, current=None, pos=(640, 360), pick=(None, None, None)):
        self.win.pick_result = pick
        self.win._thr_begin_drag(*pos)
        self.win._pick_ship_click(pos, [], None)
        return self.module["_cycle_overlapping_target"](candidates, current)

    def test_a_click_is_logged_with_its_hits_the_choice_and_the_geometry(self):
        with self.assertLogs(click_diag.logger, "INFO") as logs:
            result = self.click([("_targeted_gate_id", 7)], pick=(None, None, ("_targeted_pid", 5)))
        self.assertEqual(result, ("_targeted_gate_id", 7))
        line = logs.output[0]
        for fragment in ("zoom=0.050", "click=(640,360)", "previous=None", "hits=[_targeted_gate_id:7]",
                         "-> ('_targeted_gate_id', 7)", "ship-pick=(None, None, ('_targeted_pid', 5))",
                         "gates: gate-7 0/24*"):
            self.assertIn(fragment, line)
        self.assertEqual(self.win.begin_calls, [(640, 360)])        # the wrapped methods still ran

    def test_cycling_and_an_empty_result_are_logged_as_they_are(self):
        with self.assertLogs(click_diag.logger, "INFO") as logs:
            first = self.click([("_targeted_planet_id", 3), ("_targeted_gate_id", 7)])
            second = self.click([("_targeted_planet_id", 3), ("_targeted_gate_id", 7)],
                                current=("_targeted_planet_id", 3))
            nothing = self.click([])
        self.assertEqual((first, second, nothing), (("_targeted_planet_id", 3), ("_targeted_gate_id", 7), None))
        self.assertIn("-> None", logs.output[2])
        self.assertIn("previous=('_targeted_planet_id', 3)", logs.output[1])

    def test_the_picker_gets_exactly_the_arguments_and_returns_exactly_the_answer(self):
        calls = []

        def original(candidates, current):
            calls.append((candidates, current))
            return ("x", 1)
        module = {"_cycle_overlapping_target": original}
        click_diag.install_picker(module)
        self.assertEqual(module["_cycle_overlapping_target"](iter([("a", 1)]), None), ("x", 1))
        self.assertEqual(calls, [([("a", 1)], None)])               # a one-shot iterator still works

    def test_with_the_switch_off_nothing_is_logged_and_nothing_changes(self):
        capture_settings.set_enabled(capture_settings.CLICK_DIAG, False)
        with self.assertNoLogs(click_diag.logger, "INFO"):
            result = self.click([("_targeted_gate_id", 7)])
        self.assertEqual(result, ("_targeted_gate_id", 7))
        self.assertEqual(self.win._step_local_physics(0.016), "stepped")
        self.assertEqual(self.win.begin_calls, [(640, 360)])

    def test_a_failure_in_the_diagnostics_is_contained_and_turns_them_off(self):
        del self.win._npc_entities                                  # the snapshot cannot read the window
        with self.assertLogs(click_diag.logger, "ERROR"):
            result = self.click([("_targeted_gate_id", 7)])
        self.assertEqual(result, ("_targeted_gate_id", 7))          # the click itself was unaffected
        self.assertTrue(click_diag.has_failed())
        with self.assertNoLogs(click_diag.logger, "INFO"):
            self.assertEqual(self.click([("_targeted_gate_id", 7)]), ("_targeted_gate_id", 7))

    def test_installing_twice_is_harmless(self):
        self.assertFalse(click_diag.install(self.cls))
        self.assertFalse(click_diag.install_picker(self.module))


class SwallowedClickTests(_Fixture):
    def setUp(self):
        super().setUp()
        self.cls = type("SwWin", (FakeWindow,), {})
        click_diag.install(self.cls)
        self.win = self.cls()

    def press(self, pos, button=1):
        return pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": pos, "button": button})

    def step_and_collect(self):
        with self.assertLogs(click_diag.logger, "INFO") as logs:
            logging.getLogger(click_diag.logger.name).info("(marker)")
            self.win._step_local_physics(0.016)
        return [line for line in logs.output if "(marker)" not in line]

    def test_a_press_the_world_picker_never_saw_is_reported(self):
        click_diag.note_events([self.press((300, 200))])
        lines = self.step_and_collect()
        self.assertEqual(len(lines), 1)
        self.assertIn("click at (300,200)", lines[0])
        self.assertIn("not given to the world picker", lines[0])
        self.assertEqual(self.step_and_collect(), [])               # reported once

    def test_a_press_that_reached_the_world_picker_is_not_reported(self):
        click_diag.note_events([self.press((300, 200))])
        self.win._thr_begin_drag(300, 200)
        self.assertEqual(self.step_and_collect(), [])

    def test_presses_a_panel_menu_or_the_minimap_took_are_expected(self):
        for setup in (lambda w: setattr(w, "_gui_owner", "panel:chat"),
                      lambda w: setattr(w, "_esc_menu_open", True),
                      lambda w: setattr(w, "_station_overlay", {"kind": "npc"}),
                      lambda w: setattr(w, "_mm_rect", pygame.Rect(250, 150, 200, 200))):
            with self.subTest():
                self.win = self.cls()
                setup(self.win)
                click_diag.note_events([self.press((300, 200))])
                self.assertEqual(self.step_and_collect(), [])

    def test_only_left_button_presses_are_remembered_and_only_the_latest_few(self):
        click_diag.note_events([self.press((1, 1), button=3), pygame.event.Event(pygame.MOUSEMOTION, {"pos": (2, 2)}),
                                pygame.event.Event(pygame.MOUSEBUTTONUP, {"pos": (3, 3), "button": 1})])
        self.assertEqual(click_diag._pending, [])
        click_diag.note_events([self.press((i, i)) for i in range(40)])
        self.assertEqual(len(click_diag._pending), click_diag._PENDING_MAX)
        self.assertEqual(click_diag._pending[-1][1], (39, 39))

    def test_with_the_switch_off_presses_are_not_even_remembered(self):
        capture_settings.set_enabled(capture_settings.CLICK_DIAG, False)
        click_diag.note_events([self.press((300, 200))])
        self.assertEqual(click_diag._pending, [])


class SourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = Path(__file__).parent                    # click_diag's own folder; pump_watch's module is in its mod's
        cls.client = modloader.game_source("Client.py")
        cls.pump = (modloader.MODS_DIR / "pump_watch" / "pump_watch.py").read_text(encoding="utf-8")

    def test_the_hooks_are_installed_after_the_window_class_and_before_the_entry_point(self):
        cls = self.client.index("\nclass SolarSystemWindow")
        install = self.client.index("click_diag.install(SolarSystemWindow)")
        picker = self.client.index("click_diag.install_picker(globals())")
        entry = self.client.index('if __name__ == "__main__":')
        self.assertTrue(cls < install < picker < entry)

    def test_vanilla_still_calls_what_we_wrap_exactly_where_we_expect(self):
        self.assertEqual(self.client.count("_cycle_overlapping_target("), 1)       # the single call site
        self.assertEqual(self.client.count("self._thr_begin_drag(mx, my)"), 1)      # every world click passes it
        self.assertEqual(self.client.count("self._pick_ship_click("), 1)
        self.assertLess(self.client.index("self._thr_begin_drag(mx, my)"),
                        self.client.index("self._pick_ship_click("))

    def test_the_event_pump_feeds_the_diagnostics(self):
        self.assertIn("click_diag.note_events(events)", self.pump)

    def test_the_real_window_and_module_carry_the_wrappers(self):
        import sys
        from test_locked_gate_autopilot import SolarSystemWindow
        self.assertTrue(SolarSystemWindow.__dict__.get("_click_diag_installed"))
        self.assertTrue(getattr(vars(sys.modules["Client"])["_cycle_overlapping_target"], "_click_diag_wrapped", False))

    def test_the_switch_is_on_the_data_capture_page(self):
        import capture_settings_panel
        self.assertIn(capture_settings.CLICK_DIAG, [k for k, _label in capture_settings_panel._rows()])


class LaunchPathTests(unittest.TestCase):
    """custom_build/run_client.py does not import Client.py: it exec()s it in a fresh dict as ``__main__``
    while sys.modules['__main__'] is run_client.py. Everything that runs at the bottom of Client.py (the
    mod hooks) has to work there too. A hook written for an imported module - ``sys.modules[__name__]``
    - killed the launch silently (pythonw has no console) and no test that imports Client could see it,
    so this one runs the real file the way the launcher does, in its own process."""

    CODE = r'''
import os, sys
root = %r
internal = os.path.join(root, "_internal")
os.add_dll_directory(internal)
sys.path.insert(0, internal)
os.chdir(root)
import moderngl_compat
moderngl_compat.install()                               # what run_client.py does before the mods: the runtime's shim
import modloader
info = modloader.install(interactive=False)             # then the mods, which are patches
path = os.path.join(internal, "Client.py")
# the real entry block would start the game; everything above it, the hooks included, runs as-is
src = info.result.sources.get("Client.py") or open(path, "rb").read()
src = src.replace(b'if __name__ == "__main__":', b'if False:', 1)
ns = {"__name__": "__main__", "__file__": path}
exec(compile(src, path, "exec"), ns)
import render_mixin
import Audio
import protocol
import moderngl.program_members.uniform as _mgl_uniform
win = ns["SolarSystemWindow"]
print("LAUNCH-PATH", not info.result.failed and bool(info.result.applied),
      win.__dict__.get("_click_diag_installed"),
      getattr(ns["_cycle_overlapping_target"], "_click_diag_wrapped", False),
      getattr(render_mixin._format_autopilot_status, "_ap_eta_wrapped", False),
      hasattr(Audio.AudioManager.__init__, "__wrapped__"), hasattr(Audio._Sound._load, "__wrapped__"),
      getattr(protocol.ProtocolMixin.__dict__["_on_ps_construction_bay"], "_construction_diag", False),
      win.__dict__.get("_construction_eta_installed"), win.__dict__.get("_construction_tooltip_installed"),
      hasattr(win.set_ps_station_cargo, "__wrapped__"),
      win.__dict__.get("_galaxy_map_window_installed"), ns["GuiWindowManager"].__dict__.get("_gm_window_installed"),
      win.__dict__.get("_ship_layouts_installed"), hasattr(ns["_flight_text_entry_active"], "__wrapped__"),
      win.__dict__.get("_range_rings_installed"),
      getattr(_mgl_uniform.Uniform.__dict__["value"].fset, "_moderngl_compat", False),
      win.__dict__.get("_inventory_filter_installed"), getattr(win._ap_fly, "_ap_eta_wrapped", False))
'''

    def test_client_py_runs_to_the_entry_point_the_way_the_launcher_runs_it(self):
        import subprocess
        import sys
        root = str(modloader.REPO_DIR)
        env = {**os.environ, "SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy", "PYTHONUTF8": "1"}
        result = subprocess.run([sys.executable, "-c", self.CODE % root], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", env=env, timeout=300, cwd=root)
        self.assertEqual(result.returncode, 0, result.stderr[-2500:])
        self.assertIn("LAUNCH-PATH True True True True True True True True True True True True True True True True True True", result.stdout, result.stderr[-1500:])

    def test_no_hook_assumes_client_py_is_an_imported_module(self):
        source = modloader.game_source("Client.py")
        code_lines = [line for line in source.splitlines()
                      if "sys.modules[__name__]" in line and not line.lstrip().startswith("#")
                      and "# globals(), not sys.modules[__name__]" not in line]
        self.assertEqual(code_lines, [])


if __name__ == "__main__":
    unittest.main()

"""Tests for galaxy_map_window.py - the galaxy map as a movable, resizable window.

Three layers: the pure geometry, the hooks and the event handling on a light stand-in window, and the
vanilla map drawing itself on a real SolarSystemWindow (full-screen geometry must be exactly what it was; the
window must leave the game undimmed, have no tabs / VIEW SCANS, and keep every control inside itself).
"""

from __future__ import annotations
import modloader

import json
import os
import tempfile
import threading
import unittest
from collections import OrderedDict
from pathlib import Path
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

import galaxy_map_window as gmw
import scan_writer
from gui_window_manager import GuiLayer, GuiWindowManager, PointerRoute

SCREEN = (1280, 720)
gmw._install_manager(GuiWindowManager)          # what Client.py does at start-up (idempotent)


class Base(unittest.TestCase):
    """Every test works on a temporary settings file, never the real galaxy_map_window.json."""

    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode(SCREEN)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = os.path.join(self._tmp.name, "galaxy_map_window.json")
        gmw.reset_for_tests()
        gmw.configure_for_tests(self.settings)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        scan_writer.flush()
        gmw.reset_for_tests()
        gmw.configure_for_tests(None)
        self._tmp.cleanup()

    def stored(self):
        scan_writer.flush()
        with open(self.settings, encoding="utf-8") as handle:
            return json.load(handle)


class Light:
    """The parts of SolarSystemWindow the module touches, without the game."""

    def __init__(self, scale=1.0):
        self._uk = scale
        self._lock = threading.RLock()
        self._layout = {"system_name": "Erebus"}
        self._gui_windows = GuiWindowManager()
        self._galaxy_map_open = True
        self._galaxy_map_view = "coalitions"
        self._galaxy_map_autocenter = True
        self._galaxy_map_recenter_pending = False
        self._galaxy_map_dragging = False
        self._galaxy_map_zoom = 1.0
        self._galaxy_map_offset_x = 0.0
        self._galaxy_map_offset_y = 0.0
        self._gm_survey_selected = set()
        self._galaxy_map_info_system = None
        self._galaxy_map_info_rect = None
        self._galaxy_map_info_favorite_rect = None
        self._galaxy_map_csw_info_quarter = None
        self._galaxy_map_csw_info_anchor = None
        self._galaxy_map_csw_info_rect = None
        self._gm_favorites_open = False
        self._gm_favorite_item_rects = []
        self._gm_favorite_remove_rects = []
        self._gm_favorites_dropdown_rect = None
        self._gm_search_text = ""
        self._gm_search_focused = False
        self._gm_search_item_rects = []
        self._gm_search_rect = None
        self._gm_favorites_btn_rect = None
        self._galaxy_map_close_rect = None
        self._galaxy_map_panel_rect = None

    def _s(self, px):
        return px if self._uk == 1.0 else round(px * self._uk)

    def _gm_reset_search(self):
        self._gm_search_text = ""
        self._gm_search_focused = False
        self._gm_search_item_rects = []

    def _galaxy_map_apply_autocenter(self):
        if self._galaxy_map_autocenter:
            self._galaxy_map_recenter_pending = True


def publish(win, w=SCREEN[0], h=SCREEN[1]):
    """What one map draw leaves behind: the panel rectangle and the header controls' rectangles."""
    px, py, pw, ph = gmw.panel_geometry(win, w, h, win._s(28))
    win._galaxy_map_panel_rect = pygame.Rect(px, py, pw, ph)
    win._galaxy_map_close_rect = pygame.Rect(px + pw - 55, py + 13, 22, 20)
    if gmw.windowed(win):
        win._gm_win_toggle_rect = pygame.Rect(px + pw - 82, py + 13, 22, 20)
        win._gm_search_rect = pygame.Rect(px + 190, py + 6, 150, 24)
        win._gm_favorites_btn_rect = pygame.Rect(px + 348, py + 6, 150, 24)
    return win._galaxy_map_panel_rect


def frame(win, extra=()):
    """One pass through the retained window manager, the way the main loop queues the map."""
    manager = win._gui_windows
    manager.begin_frame()
    manager.queue_draw(gmw.MAP_ID, lambda: None, lambda: pygame.Rect(0, 0, *SCREEN),
                       layer=GuiLayer.MODAL, modal=True, captures_outside=True, focusable=False,
                       visible=lambda: win._galaxy_map_open)
    for window_id, rect, layer in extra:
        manager.register(window_id, rect, layer=layer)
    manager.flush_draw_queue()
    manager.end_frame()


def windowed_light(scale=1.0, **kwargs):
    win = Light(scale)
    gmw._ensure(win)
    win._gm_windowed = True
    win._gui_windows._gm_owner = __import__("weakref").ref(win)
    publish(win)
    frame(win)
    return win


def down(pos, button=1):
    return pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=button)


def up(pos, button=1):
    return pygame.event.Event(pygame.MOUSEBUTTONUP, pos=pos, button=button)


def motion(pos, held=True):
    return pygame.event.Event(pygame.MOUSEMOTION, pos=pos, rel=(0, 0), buttons=(int(held), 0, 0))


# ── geometry ─────────────────────────────────────────────────────────────────

class GeometryReexportTests(unittest.TestCase):
    """The helpers live in core's window_geometry.py (shared with the Ship Layouts window); this module keeps the names,
    and the render_mixin patch still calls galaxy_map_window.draw_plate."""

    def test_the_old_names_are_the_cores_functions(self):
        import window_geometry as wg
        for name in ("EDGE_L", "EDGE_T", "EDGE_R", "EDGE_B", "clamp_rect", "resize_zone", "resized_rect", "moved_rect",
                     "plate_chamfer", "draw_plate"):
            with self.subTest(name):
                self.assertIs(getattr(gmw, name), getattr(wg, name))


# ── settings file ────────────────────────────────────────────────────────────

class PersistenceTests(Base):
    def write(self, text):
        with open(self.settings, "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_missing_file_means_full_screen_and_never_placed(self):
        self.assertEqual(gmw.load(self.settings), {"windowed": False, "rect": None})

    def test_a_good_file_is_read(self):
        self.write(json.dumps({"windowed": True, "rect": [10, 20, 600, 400]}))
        self.assertEqual(gmw.load(self.settings), {"windowed": True, "rect": [10, 20, 600, 400]})

    def test_only_a_real_true_counts(self):
        for value in ("true", 1, "yes", None):
            self.write(json.dumps({"windowed": value}))
            self.assertFalse(gmw.load(self.settings)["windowed"], value)

    def test_a_rect_must_be_four_integers(self):
        for rect in ([1, 2, 3], [1, 2, 3, 4, 5], [1, 2, 3, "4"], [1, 2, 3, 4.5], [True, 2, 3, 4], "1,2,3,4", None):
            self.write(json.dumps({"windowed": True, "rect": rect}))
            state = gmw.load(self.settings)
            self.assertIsNone(state["rect"], rect)
            self.assertTrue(state["windowed"])

    def test_a_broken_file_means_defaults(self):
        for text in ("not json", "[1, 2]", "", '{"windowed": '):
            self.write(text)
            self.assertEqual(gmw.load(self.settings), {"windowed": False, "rect": None}, text)

    def test_mode_and_rectangle_survive_a_restart(self):
        win = Light()
        gmw.set_windowed(win, True)
        win._gm_win_rect = pygame.Rect(40, 50, 700, 400)
        gmw._save(win)
        self.assertEqual(self.stored(), {"windowed": True, "rect": [40, 50, 700, 400]})
        gmw.load(self.settings)
        again = Light()
        self.assertTrue(gmw.windowed(again))
        self.assertEqual(gmw.window_rect(again, *SCREEN), pygame.Rect(40, 50, 700, 400))

    def test_leaving_the_window_keeps_its_place_for_next_time(self):
        win = Light()
        gmw.set_windowed(win, True)
        win._gm_win_rect = pygame.Rect(40, 50, 700, 400)
        gmw.set_windowed(win, False)
        self.assertEqual(self.stored(), {"windowed": False, "rect": [40, 50, 700, 400]})

    def test_the_save_happens_on_the_writer_thread(self):
        with mock.patch.object(gmw, "submit") as submit:
            win = Light()
            gmw.set_windowed(win, True)
        self.assertEqual(submit.call_count, 1)
        self.assertIs(submit.call_args.args[0], gmw._write)
        self.assertEqual(submit.call_args.args[1], self.settings)


# ── mode ─────────────────────────────────────────────────────────────────────

class ModeTests(Base):
    def test_full_screen_is_the_default_and_nothing_changes(self):
        win = Light()
        self.assertFalse(gmw.windowed(win))
        self.assertTrue(gmw.modal_open(win))
        self.assertEqual(gmw.panel_geometry(win, 1280, 720, 28), (28, 28, 1224, 664))
        self.assertEqual(gmw.origin(win, 28), (28, 28))
        self.assertEqual(gmw.view_centre(win, 1281, 721, pygame.Rect(0, 0, 10, 10)), (640, 360))
        items = ["a", "b", "c"]
        self.assertIs(gmw.fit_rows(win, items, 700, 24), items)
        self.assertTrue(gmw.keys_zoom_map(win))

    def test_a_closed_map_is_not_modal(self):
        win = Light()
        win._galaxy_map_open = False
        self.assertFalse(gmw.modal_open(win))

    def test_the_window_is_not_modal(self):
        win = Light()
        gmw.set_windowed(win, True)
        self.assertTrue(gmw.windowed(win))
        self.assertFalse(gmw.modal_open(win))

    def test_first_placement_is_top_right_and_big_enough(self):
        win = Light()
        gmw.set_windowed(win, True)
        self.assertEqual(gmw.panel_geometry(win, 1280, 720, 28), (672, 28, 580, 324))
        again = Light()
        gmw.set_windowed(again, True)
        self.assertEqual(gmw.panel_geometry(again, 1920, 1080, 28), (1920 - 28 - 768, 28, 768, 486))

    def test_once_placed_the_window_stays_where_it_is(self):
        win = Light()
        gmw.set_windowed(win, True)
        first = gmw.panel_geometry(win, 1280, 720, 28)
        self.assertEqual(gmw.panel_geometry(win, 1280, 720, 28), first)
        self.assertEqual(gmw.panel_geometry(win, 1280, 720, 28), first)

    def test_the_window_scales_with_the_ui_scale(self):
        win = Light(1.5)
        gmw.set_windowed(win, True)
        _x, _y, w, h = gmw.panel_geometry(win, 1920, 1080, 42)
        self.assertGreaterEqual(w, round(580 * 1.5))
        self.assertGreaterEqual(h, round(300 * 1.5))

    def test_the_window_is_pulled_back_on_screen_when_the_screen_shrinks(self):
        win = windowed_light()
        win._gm_win_rect = pygame.Rect(1000, 600, 600, 400)
        x, y, w, h = gmw.panel_geometry(win, 1280, 720, 28)
        self.assertEqual((x + w, y + h), (1280, 720))
        self.assertEqual(win._gm_win_rect, pygame.Rect(x, y, w, h))

    def test_the_view_centre_is_the_middle_of_the_map_area(self):
        win = windowed_light()
        canvas = pygame.Rect(700, 90, 561, 215)
        self.assertEqual(gmw.view_centre(win, 1280, 720, canvas), (canvas.centerx, canvas.centery))
        self.assertEqual(gmw.zoom_centre(win, pygame.Surface((10, 10))), (canvas.centerx, canvas.centery))

    def test_going_to_the_window_forces_the_coalition_view_and_going_back_restores_the_tab(self):
        win = Light()
        win._galaxy_map_view = "survey"
        win._gm_survey_selected = {"Erebus"}
        win._galaxy_map_csw_info_quarter = "north"
        gmw.set_windowed(win, True)
        self.assertEqual(win._galaxy_map_view, "coalitions")
        self.assertEqual(win._gm_survey_selected, set())
        self.assertIsNone(win._galaxy_map_csw_info_quarter)
        gmw.set_windowed(win, False)
        self.assertEqual(win._galaxy_map_view, "survey")
        self.assertIsNone(win._gm_win_saved_view)

    def test_the_stashed_tab_is_not_overwritten_by_later_frames(self):
        win = Light()
        win._galaxy_map_view = "territory"
        gmw.set_windowed(win, True)
        gmw._frame(win)
        gmw._frame(win)
        self.assertEqual(win._gm_win_saved_view, "territory")
        gmw.set_windowed(win, False)
        self.assertEqual(win._galaxy_map_view, "territory")

    def test_a_stashed_tab_is_kept_if_another_one_turns_up_while_windowed(self):
        win = Light()
        win._galaxy_map_view = "territory"
        gmw.set_windowed(win, True)
        win._galaxy_map_view = "survey"
        gmw._frame(win)
        self.assertEqual(win._galaxy_map_view, "coalitions")
        gmw.set_windowed(win, False)
        self.assertEqual(win._galaxy_map_view, "territory")

    def test_a_tab_picked_somehow_while_windowed_is_put_back_by_the_next_frame(self):
        win = Light()
        gmw.set_windowed(win, True)
        win._galaxy_map_view = "survey"
        gmw._frame(win)
        self.assertEqual(win._galaxy_map_view, "coalitions")
        gmw.set_windowed(win, False)
        self.assertEqual(win._galaxy_map_view, "survey")

    def test_a_map_that_opens_windowed_over_a_remembered_tab_shows_coalitions(self):
        gmw.load(self.settings)
        win = Light()
        win._galaxy_map_view = "survey"                   # last used full screen
        gmw._ensure(win)
        win._gm_windowed = True
        gmw._frame(win)
        self.assertEqual(win._galaxy_map_view, "coalitions")
        self.assertEqual(win._gm_win_saved_view, "survey")

    def test_switching_closes_popups_and_search(self):
        win = Light()
        win._galaxy_map_info_system = "Erebus"
        win._gm_favorites_open = True
        win._gm_favorites_dropdown_rect = pygame.Rect(0, 0, 5, 5)
        win._gm_search_text = "er"
        win._gm_search_focused = True
        gmw.set_windowed(win, True)
        self.assertIsNone(win._galaxy_map_info_system)
        self.assertFalse(win._gm_favorites_open)
        self.assertIsNone(win._gm_favorites_dropdown_rect)
        self.assertEqual((win._gm_search_text, win._gm_search_focused), ("", False))

    def test_switching_recentres_on_the_player_only_with_autocenter(self):
        win = Light()
        gmw.set_windowed(win, True)
        self.assertTrue(win._galaxy_map_recenter_pending)
        other = Light()
        other._galaxy_map_autocenter = False
        gmw.set_windowed(other, True)
        self.assertFalse(other._galaxy_map_recenter_pending)

    def test_setting_the_mode_it_already_has_does_nothing(self):
        win = Light()
        with mock.patch.object(gmw, "submit") as submit:
            gmw.set_windowed(win, False)
            self.assertEqual(submit.call_count, 0)
            gmw.set_windowed(win, True)
            gmw.set_windowed(win, True)
            self.assertEqual(submit.call_count, 1)

    def test_toggle_flips(self):
        win = Light()
        gmw.toggle_windowed(win)
        self.assertTrue(gmw.windowed(win))
        gmw.toggle_windowed(win)
        self.assertFalse(gmw.windowed(win))

    def test_plus_minus_zoom_the_window_only_with_the_pointer_over_it(self):
        win = windowed_light()
        inside = (win._galaxy_map_panel_rect.centerx, win._galaxy_map_panel_rect.centery)
        with mock.patch("pygame.mouse.get_pos", return_value=inside):
            self.assertTrue(gmw.keys_zoom_map(win))
        with mock.patch("pygame.mouse.get_pos", return_value=(10, 10)):
            self.assertFalse(gmw.keys_zoom_map(win))
        win._galaxy_map_open = False
        with mock.patch("pygame.mouse.get_pos", return_value=inside):
            self.assertFalse(gmw.keys_zoom_map(win))

    def test_dropdown_rows_stop_at_the_bottom_of_the_screen_in_the_window(self):
        win = windowed_light()
        rows = [f"S{i}" for i in range(25)]
        self.assertEqual(len(gmw.fit_rows(win, rows, 100, 24)), 25)           # 620 px left: all fit
        self.assertEqual(len(gmw.fit_rows(win, rows, 600, 24)), 5)            # 120 px left
        self.assertEqual(len(gmw.fit_rows(win, rows, 719, 24)), 1)            # always at least one
        self.assertEqual(gmw.fit_rows(win, [], 719, 24), [])

    def test_a_window_never_bakes_a_plate_for_the_shared_cache(self):
        win = windowed_light()
        shared = []
        win._hud_plate_layers = lambda *a, **k: shared.append(a) or {"plate": None, "chamfer": 0}
        with mock.patch("render_cache.build_hud_panel_plate", side_effect=AssertionError("baked")):
            for width in range(580, 600):
                layers = gmw.plate_layers(win, width, 324, 46, 28)
                self.assertEqual(layers["chamfer"], gmw.plate_chamfer(324, 28))
        self.assertEqual(shared, [])
        self.assertEqual(win._gm_win_chamfer, layers["chamfer"])
        gmw.set_windowed(win, False)
        gmw.plate_layers(win, 600, 324, 46, 28)
        self.assertEqual(len(shared), 1)                   # full screen uses the shared cache as before

    def test_the_baked_plate_is_only_built_when_asked_for_and_once(self):
        from render_cache import build_hud_panel_plate
        win = windowed_light()
        layers = gmw.plate_layers(win, 600, 330, 46, 28)
        self.assertNotIn("plate", layers)
        plate = layers["plate"]
        self.assertEqual(plate.get_size(), (600, 330))
        self.assertIs(layers["plate"], plate)
        with self.assertRaises(KeyError):
            layers["nothing"]



class KillSwitchTests(Base):
    def test_env_switch_restores_the_vanilla_map(self):
        win = windowed_light()
        events = [down((700, 40))]
        with mock.patch.dict(os.environ, {gmw.ENV_OFF: "1"}):
            self.assertFalse(gmw.windowed(win))
            self.assertTrue(gmw.modal_open(win))
            self.assertEqual(gmw.panel_geometry(win, 1280, 720, 28), (28, 28, 1224, 664))
            self.assertIs(gmw.filter_events(win, events), events)
            self.assertEqual(gmw.draw_toggle(win, None, None, pygame.Rect(1197, 41, 22, 20)), 1197)
            self.assertIsNone(win._gm_win_toggle_rect)
            self.assertTrue(gmw.keys_zoom_map(win))

    def test_an_error_switches_the_feature_off_for_the_session_not_the_game(self):
        win = windowed_light()
        with mock.patch.object(gmw, "_ensure", side_effect=RuntimeError("boom")):
            with self.assertLogs("galaxy_map_window", level="ERROR"):
                self.assertFalse(gmw.windowed(win))
        self.assertTrue(gmw._failed)
        self.assertFalse(gmw.windowed(win))                # stays off even though _ensure works again
        self.assertEqual(gmw.panel_geometry(win, 1280, 720, 28), (28, 28, 1224, 664))
        gmw.reset_for_tests()
        self.assertTrue(gmw.windowed(win))

    def test_an_error_in_the_event_filter_returns_the_events_untouched(self):
        win = windowed_light()
        events = [down((700, 40))]
        with mock.patch.object(gmw, "_handle", side_effect=RuntimeError("boom")):
            with self.assertLogs("galaxy_map_window", level="ERROR"):
                self.assertIs(gmw.filter_events(win, events), events)
        self.assertTrue(gmw._failed)

    def test_an_error_in_panel_geometry_gives_the_full_screen_box(self):
        win = windowed_light()
        with mock.patch.object(gmw, "window_rect", side_effect=RuntimeError("boom")):
            with self.assertLogs("galaxy_map_window", level="ERROR"):
                self.assertEqual(gmw.panel_geometry(win, 1280, 720, 28), (28, 28, 1224, 664))


# ── following the ship ───────────────────────────────────────────────────────

class FollowTests(Base):
    def test_a_jump_recentres_the_window_with_autocenter(self):
        win = windowed_light()
        gmw._frame(win)
        win._galaxy_map_recenter_pending = False
        win._layout = {"system_name": "Chargus"}
        gmw._frame(win)
        self.assertTrue(win._galaxy_map_recenter_pending)

    def test_the_same_system_does_not(self):
        win = windowed_light()
        gmw._frame(win)
        win._galaxy_map_recenter_pending = False
        gmw._frame(win)
        gmw._frame(win)
        self.assertFalse(win._galaxy_map_recenter_pending)

    def test_the_first_frame_does_not(self):
        win = windowed_light()
        win._galaxy_map_recenter_pending = False
        win._gm_win_last_system = None
        gmw._frame(win)
        self.assertFalse(win._galaxy_map_recenter_pending)
        self.assertEqual(win._gm_win_last_system, "Erebus")

    def test_without_autocenter_the_map_stays_where_the_player_put_it(self):
        win = windowed_light()
        gmw._frame(win)
        win._galaxy_map_autocenter = False
        win._layout = {"system_name": "Chargus"}
        gmw._frame(win)
        self.assertFalse(win._galaxy_map_recenter_pending)

    def test_not_while_panning_or_moving_the_window(self):
        for attr, value in (("_galaxy_map_dragging", True), ("_gm_win_drag", {"kind": "move"})):
            win = windowed_light()
            gmw._frame(win)
            win._galaxy_map_recenter_pending = False
            setattr(win, attr, value)
            win._layout = {"system_name": "Chargus"}
            gmw._frame(win)
            self.assertFalse(win._galaxy_map_recenter_pending, attr)

    def test_the_full_screen_map_is_left_alone(self):
        win = Light()
        gmw._frame(win)
        win._layout = {"system_name": "Chargus"}
        gmw._frame(win)
        self.assertFalse(win._galaxy_map_recenter_pending)
        self.assertIsNone(win._gm_win_last_system)

    def test_leaving_the_window_forgets_the_last_system(self):
        win = windowed_light()
        gmw._frame(win)
        self.assertEqual(win._gm_win_last_system, "Erebus")
        gmw.set_windowed(win, False)
        gmw._frame(win)
        self.assertIsNone(win._gm_win_last_system)

    def test_a_missing_layout_is_tolerated(self):
        win = windowed_light()
        win._layout = None
        gmw._frame(win)
        self.assertIsNone(win._gm_win_last_system)


# ── the window manager and the autopilot ────────────────────────────────────

class ManagerTests(Base):
    def setUp(self):
        super().setUp()
        self.win = windowed_light()
        self.manager = self.win._gui_windows

    def test_the_window_is_a_non_modal_popover_over_its_panel(self):
        registered = self.manager._windows[gmw.MAP_ID]
        self.assertFalse(registered.modal)
        self.assertFalse(registered.captures_outside)
        self.assertEqual(registered.layer, int(GuiLayer.POPOVER))
        self.assertFalse(registered.focusable)

    def test_full_screen_is_still_the_modal_that_owns_the_whole_screen(self):
        win = Light()
        win._gui_windows._gm_owner = __import__("weakref").ref(win)
        publish(win)
        frame(win)
        registered = win._gui_windows._windows[gmw.MAP_ID]
        self.assertTrue(registered.modal)
        self.assertTrue(registered.captures_outside)
        self.assertEqual(registered.layer, int(GuiLayer.MODAL))
        route = win._gui_windows.route_pointer((5, 5))
        self.assertEqual((route.owner, route.blocked), (gmw.MAP_ID, True))

    def test_inside_the_window_it_owns_the_pointer(self):
        route = self.manager.route_pointer(self.win._galaxy_map_panel_rect.center)
        self.assertEqual((route.owner, route.blocked), (gmw.MAP_ID, True))
        self.assertTrue(route.allows(gmw.MAP_ID))
        self.assertFalse(route.allows("panel:turrets"))

    def test_outside_the_window_the_map_gets_nothing_and_the_game_everything(self):
        route = self.manager.route_pointer((100, 400))
        self.assertFalse(route.blocked)
        self.assertIsNone(route.owner)
        self.assertFalse(route.allows(gmw.MAP_ID))
        for other in ("chat", "panel:turrets", "station_overlay", "minimap_launcher"):
            self.assertTrue(route.allows(other), other)

    def test_a_window_in_front_still_wins_outside(self):
        win = self.win
        frame(win, extra=[("chat", pygame.Rect(0, 300, 400, 400), GuiLayer.WINDOW)])
        route = win._gui_windows.route_pointer((100, 400))
        self.assertEqual(route.owner, "chat")
        self.assertFalse(route.allows(gmw.MAP_ID))
        self.assertTrue(route.allows("chat"))

    def test_the_map_window_is_in_front_of_ordinary_windows_where_they_overlap(self):
        win = self.win
        inside = win._galaxy_map_panel_rect.center
        frame(win, extra=[("chat", win._galaxy_map_panel_rect.inflate(40, 40), GuiLayer.WINDOW)])
        win._gui_windows.focus("chat")                       # focused after the map: still behind it
        self.assertEqual(win._gui_windows.route_pointer(inside).owner, gmw.MAP_ID)

    def test_a_modal_dialog_takes_everything_from_the_window(self):
        win = self.win
        win._gui_windows.begin_frame()
        win._gui_windows.queue_draw(gmw.MAP_ID, lambda: None, lambda: pygame.Rect(0, 0, *SCREEN),
                                    layer=GuiLayer.MODAL, modal=True, captures_outside=True, focusable=False,
                                    visible=lambda: True)
        win._gui_windows.queue_draw("escape_menu", lambda: None, lambda: pygame.Rect(0, 0, *SCREEN),
                                    layer=GuiLayer.MODAL, modal=True, captures_outside=True, focusable=False,
                                    visible=lambda: True)
        win._gui_windows.flush_draw_queue()
        inside = win._galaxy_map_panel_rect.center
        route = win._gui_windows.route_pointer(inside)
        self.assertEqual(route.owner, "escape_menu")
        self.assertFalse(route.allows(gmw.MAP_ID))

    def test_panning_keeps_the_pointer_with_the_map_when_it_leaves_the_window(self):
        win = self.win
        win._galaxy_map_dragging = True
        route = win._gui_windows.route_pointer((100, 400))
        self.assertEqual((route.owner, route.blocked), (gmw.MAP_ID, True))
        self.assertTrue(route.allows(gmw.MAP_ID))
        win._galaxy_map_dragging = False
        self.assertFalse(win._gui_windows.route_pointer((100, 400)).allows(gmw.MAP_ID))

    def test_panning_does_not_override_a_modal_dialog(self):
        win = self.win
        win._galaxy_map_dragging = True
        win._gui_windows.begin_frame()
        win._gui_windows.queue_draw(gmw.MAP_ID, lambda: None, lambda: pygame.Rect(0, 0, *SCREEN),
                                    layer=GuiLayer.MODAL, modal=True, captures_outside=True, focusable=False,
                                    visible=lambda: True)
        win._gui_windows.queue_draw("escape_menu", lambda: None, lambda: pygame.Rect(0, 0, *SCREEN),
                                    layer=GuiLayer.MODAL, modal=True, captures_outside=True, focusable=False,
                                    visible=lambda: True)
        win._gui_windows.flush_draw_queue()
        self.assertEqual(win._gui_windows.route_pointer((100, 400)).owner, "escape_menu")

    def test_the_route_follows_the_window_the_moment_it_moves(self):
        win = self.win
        old_centre = win._galaxy_map_panel_rect.center
        win._galaxy_map_panel_rect = pygame.Rect(50, 300, 580, 324)           # what a drag publishes
        self.assertEqual(win._gui_windows.route_pointer((300, 450)).owner, gmw.MAP_ID)
        self.assertFalse(win._gui_windows.route_pointer(old_centre).allows(gmw.MAP_ID))

    def test_dropdowns_hanging_below_the_window_belong_to_the_map(self):
        win = self.win
        panel = win._galaxy_map_panel_rect
        win._gm_favorites_dropdown_rect = pygame.Rect(panel.x + 100, panel.bottom - 10, 280, 200)
        win._gm_search_item_rects = [(pygame.Rect(panel.x + 400, panel.bottom + 5, 150, 24), "Erebus")]
        below = (panel.x + 150, panel.bottom + 100)
        self.assertEqual(win._gui_windows.route_pointer(below).owner, gmw.MAP_ID)
        self.assertEqual(win._gui_windows.route_pointer((panel.x + 450, panel.bottom + 10)).owner, gmw.MAP_ID)
        self.assertFalse(win._gui_windows.route_pointer((panel.x + 20, panel.bottom + 100)).allows(gmw.MAP_ID))

    def test_a_closed_map_leaves_the_manager_alone(self):
        win = self.win
        win._galaxy_map_open = False
        frame(win)
        self.assertNotIn(gmw.MAP_ID, win._gui_windows._windows)
        self.assertTrue(win._gui_windows.route_pointer((100, 400)).allows(gmw.MAP_ID))   # as before: no map, no say

    def test_other_windows_are_queued_exactly_as_before(self):
        manager = self.manager
        manager.begin_frame()
        manager.queue_draw("squad", lambda: None, lambda: pygame.Rect(0, 0, 10, 10), layer=GuiLayer.WINDOW,
                           visible=True)
        manager.flush_draw_queue()
        registered = manager._windows["squad"]
        self.assertEqual((registered.layer, registered.modal), (int(GuiLayer.WINDOW), False))

    def test_a_manager_without_an_owner_behaves_like_vanilla(self):
        manager = GuiWindowManager()
        manager.begin_frame()
        manager.queue_draw(gmw.MAP_ID, lambda: None, lambda: pygame.Rect(0, 0, *SCREEN), layer=GuiLayer.MODAL,
                           modal=True, captures_outside=True, focusable=False, visible=True)
        manager.flush_draw_queue()
        self.assertTrue(manager._windows[gmw.MAP_ID].modal)

    def test_a_dialog_over_the_full_screen_map_routes_exactly_as_vanilla(self):
        win = Light()
        win._gui_windows._gm_owner = __import__("weakref").ref(win)
        publish(win)
        manager = win._gui_windows
        manager.begin_frame()
        for window_id in (gmw.MAP_ID, "escape_menu"):
            manager.queue_draw(window_id, lambda: None, lambda: pygame.Rect(0, 0, *SCREEN), layer=GuiLayer.MODAL,
                               modal=True, captures_outside=True, focusable=False, visible=True)
        manager.flush_draw_queue()
        route = manager.route_pointer((100, 100))
        self.assertIs(type(route), PointerRoute)
        self.assertEqual(route.owner, "escape_menu")

    def test_installing_the_manager_hooks_twice_wraps_once(self):
        class Manager(GuiWindowManager):
            pass

        self.assertTrue(gmw._install_manager(Manager))
        self.assertFalse(gmw._install_manager(Manager))
        self.assertFalse(hasattr(Manager.__dict__["queue_draw"].__wrapped__, "__wrapped__")
                         and Manager.__dict__["queue_draw"].__wrapped__.__dict__.get("_gm_window_installed"))
        self.assertIs(Manager.__dict__["route_pointer"].__wrapped__, GuiWindowManager.route_pointer)

    def test_a_failure_while_routing_is_survived(self):
        with mock.patch.object(gmw, "_windowed_map_registered", side_effect=RuntimeError("boom")):
            with self.assertLogs("galaxy_map_window", level="ERROR"):
                route = self.manager.route_pointer((100, 400))
        self.assertIsNone(route.owner)
        self.assertTrue(gmw._failed)


class EngageTests(Base):
    """A route set from the window must not close it; the full-screen map still closes itself."""

    class Stub:
        calls = []

        def _draw_galaxy_map(self, screen):
            return "drawn"

        def _engage_route_autopilot_to(self, dest_system, *, close_map=False, announce_same_system=False,
                                       preview_out_of_range=False, allow_unexplored=False):
            self.calls.append((dest_system, close_map, announce_same_system, preview_out_of_range,
                               allow_unexplored))
            return "engaged"

    class Manager:
        def queue_draw(self, *a, **k):
            pass

        def route_pointer(self, *a, **k):
            pass

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        class Window(cls.Stub):
            pass
        cls.Window = Window
        gmw.install(Window, None, manager_cls=type("Mgr", (cls.Manager,), {}))

    def setUp(self):
        super().setUp()
        self.Window.calls = []
        self.win = self.Window()
        self.win._galaxy_map_open = True

    def engage(self, **kwargs):
        return self.win._engage_route_autopilot_to("Chargus", **kwargs)

    def test_windowed_map_stays_open(self):
        gmw._ensure(self.win)
        self.win._gm_windowed = True
        self.assertEqual(self.engage(close_map=True, announce_same_system=False, preview_out_of_range=True,
                                     allow_unexplored=True), "engaged")
        self.assertEqual(self.Window.calls, [("Chargus", False, False, True, True)])

    def test_full_screen_map_still_closes(self):
        gmw._ensure(self.win)
        self.engage(close_map=True)
        self.assertEqual(self.Window.calls[0][1], True)

    def test_chat_command_is_untouched(self):
        gmw._ensure(self.win)
        self.win._gm_windowed = True
        self.win._galaxy_map_open = False
        self.engage(announce_same_system=True)
        self.assertEqual(self.Window.calls, [("Chargus", False, True, False, False)])

    def test_close_map_is_only_cleared_while_the_map_is_open(self):
        gmw._ensure(self.win)
        self.win._gm_windowed = True
        self.win._galaxy_map_open = False
        self.engage(close_map=True)
        self.assertEqual(self.Window.calls[0][1], True)

    def test_installed_twice_wraps_once(self):
        self.assertFalse(gmw.install(self.Window, None, manager_cls=type("Mgr", (self.Manager,), {})))
        self.assertTrue(hasattr(self.Window._engage_route_autopilot_to, "__wrapped__"))
        self.assertFalse(hasattr(self.Window._engage_route_autopilot_to.__wrapped__, "__wrapped__"))

    def test_drawing_still_returns_what_it_returned(self):
        self.assertEqual(self.win._draw_galaxy_map(None), "drawn")

    def test_an_error_in_the_frame_bookkeeping_does_not_stop_the_draw(self):
        with mock.patch.object(gmw, "_frame", side_effect=RuntimeError("boom")):
            with self.assertLogs("galaxy_map_window", level="ERROR"):
                self.assertEqual(self.win._draw_galaxy_map(None), "drawn")


# ── mouse ────────────────────────────────────────────────────────────────────

class EventTests(Base):
    def setUp(self):
        super().setUp()
        self.win = windowed_light()
        self.panel = self.win._galaxy_map_panel_rect
        self.title = (self.panel.x + 60, self.panel.y + 20)

    def run_events(self, *events):
        self.win._gui_windows.begin_frame()
        frame(self.win)
        return gmw.filter_events(self.win, list(events))

    def test_a_press_on_the_header_is_taken_and_starts_a_move(self):
        kept = self.run_events(down(self.title))
        self.assertEqual(kept, [])
        self.assertEqual(self.win._gm_win_drag["kind"], "move")

    def test_moving_takes_the_motion_and_moves_the_window(self):
        start = pygame.Rect(self.panel)
        kept = self.run_events(down(self.title), motion((self.title[0] - 100, self.title[1] + 80)))
        self.assertEqual(kept, [])
        self.assertEqual(self.win._gm_win_rect, pygame.Rect(start.x - 100, start.y + 80, start.w, start.h))
        self.assertEqual(self.win._galaxy_map_panel_rect, self.win._gm_win_rect)

    def test_the_window_cannot_be_dragged_off_screen(self):
        self.run_events(down(self.title), motion((9000, 9000)))
        self.assertEqual(self.win._gm_win_rect.bottomright, SCREEN)
        self.run_events(motion((-9000, -9000)))
        self.assertEqual(self.win._gm_win_rect.topleft, (0, 0))

    def test_release_ends_the_drag_takes_the_release_and_saves(self):
        end = (self.title[0] - 50, self.title[1] + 40)
        kept = self.run_events(down(self.title), motion(end), up(end))
        self.assertEqual(kept, [])
        self.assertIsNone(self.win._gm_win_drag)
        rect = self.win._gm_win_rect
        self.assertEqual(self.stored(), {"windowed": True, "rect": [rect.x, rect.y, rect.w, rect.h]})

    def test_a_second_press_during_a_drag_is_not_ours_and_does_not_restart_it(self):
        self.run_events(down(self.title))
        origin = self.win._gm_win_drag["origin"]
        again = down((self.title[0] + 30, self.title[1] + 5))
        self.assertEqual(self.run_events(again), [again])
        self.assertEqual(self.win._gm_win_drag["origin"], origin)

    def test_the_release_counts_where_it_lands(self):
        self.run_events(down(self.title), up((self.title[0] - 70, self.title[1] + 10)))
        self.assertEqual(self.win._gm_win_rect.x, self.panel.x - 70)

    def test_a_motion_after_the_button_was_released_elsewhere_ends_the_drag(self):
        self.run_events(down(self.title))
        kept = self.run_events(motion((10, 10), held=False))
        self.assertEqual(len(kept), 1)                      # not ours any more
        self.assertIsNone(self.win._gm_win_drag)
        self.assertEqual(self.win._gm_win_rect, self.panel)  # and it did not move

    def test_header_controls_are_not_taken(self):
        win = self.win
        for rect in (win._galaxy_map_close_rect, win._gm_search_rect, win._gm_favorites_btn_rect):
            event = down(rect.center)
            self.assertEqual(self.run_events(event), [event], rect)
            self.assertIsNone(win._gm_win_drag)

    def test_the_mode_button_switches_to_full_screen(self):
        win = self.win
        self.assertEqual(self.run_events(down(win._gm_win_toggle_rect.center)), [])
        self.assertFalse(gmw.windowed(win))
        self.assertEqual(self.stored()["windowed"], False)

    def test_a_press_in_the_map_area_goes_to_the_map(self):
        event = down(self.panel.center)
        self.assertEqual(self.run_events(event), [event])
        self.assertIsNone(self.win._gm_win_drag)

    def test_a_press_in_the_header_below_the_controls_row_still_moves(self):
        self.assertEqual(self.run_events(down((self.panel.x + 400, self.panel.y + 40))), [])
        self.assertEqual(self.win._gm_win_drag["kind"], "move")

    def test_the_header_ends_where_the_map_area_begins(self):
        event = down((self.panel.x + 300, self.panel.y + 46))
        self.assertEqual(self.run_events(event), [event])

    def test_edges_and_corners_resize(self):
        cases = ((self.panel.x + 2, self.panel.centery, gmw.EDGE_L),
                 (self.panel.right - 3, self.panel.centery, gmw.EDGE_R),
                 (self.panel.centerx, self.panel.bottom - 2, gmw.EDGE_B),
                 (self.panel.x + 3, self.panel.bottom - 3, gmw.EDGE_L | gmw.EDGE_B),
                 (self.panel.right - 3, self.panel.bottom - 3, gmw.EDGE_R | gmw.EDGE_B),
                 (self.panel.x + 3, self.panel.y + 3, gmw.EDGE_L | gmw.EDGE_T))
        for x, y, edges in cases:
            win = windowed_light()
            win._gui_windows.begin_frame()
            self.assertEqual(gmw.filter_events(win, [down((x, y))]), [], (x, y))
            self.assertEqual((win._gm_win_drag["kind"], win._gm_win_drag["edges"]), ("resize", edges))

    def test_resizing_from_the_bottom_right_corner(self):
        start = pygame.Rect(self.panel)
        corner = (start.right - 3, start.bottom - 3)
        self.run_events(down(corner), motion((corner[0] + 120, corner[1] + 90)), up((corner[0] + 120, corner[1] + 90)))
        self.assertEqual(self.win._gm_win_rect, pygame.Rect(start.x, start.y, start.w + 120, start.h + 90)
                         .clip(pygame.Rect(0, 0, *SCREEN)))

    def test_resizing_stops_at_the_minimum_size(self):
        start = pygame.Rect(self.panel)
        corner = (start.x + 3, start.bottom - 3)
        self.run_events(down(corner), motion((corner[0] + 900, corner[1] - 900)))
        rect = self.win._gm_win_rect
        self.assertEqual((rect.w, rect.h), gmw.min_size(self.win))
        self.assertEqual(rect.right, start.right)              # the left edge stopped, the right one never moved
        self.assertEqual(rect.y, start.y)                      # the bottom edge came up to the minimum height

    def test_resizing_a_window_that_was_moved_first_uses_its_new_place(self):
        self.run_events(down(self.title), motion((self.title[0] - 300, self.title[1] + 100)),
                        up((self.title[0] - 300, self.title[1] + 100)))
        moved = pygame.Rect(self.win._gm_win_rect)
        edge = (moved.right - 2, moved.centery)
        self.run_events(down(edge), motion((edge[0] + 40, edge[1])), up((edge[0] + 40, edge[1])))
        self.assertEqual(self.win._gm_win_rect.right, moved.right + 40)
        self.assertEqual(self.win._gm_win_rect.x, moved.x)

    def test_a_press_that_starts_a_drag_cancels_a_pan(self):
        self.win._galaxy_map_dragging = True
        self.run_events(down(self.title))
        self.assertFalse(self.win._galaxy_map_dragging)

    def test_a_press_outside_goes_to_the_game_and_drops_the_search_and_dropdown(self):
        win = self.win
        win._gm_search_focused = True
        win._gm_search_text = "ere"
        win._gm_favorites_open = True
        event = down((100, 500))
        self.assertEqual(self.run_events(event), [event])
        self.assertEqual((win._gm_search_focused, win._gm_search_text, win._gm_favorites_open), (False, "", False))

    def test_each_of_search_text_and_dropdown_alone_is_dropped_by_a_press_outside(self):
        for attr, value in (("_gm_search_text", "ere"), ("_gm_search_focused", True), ("_gm_favorites_open", True)):
            win = windowed_light()
            setattr(win, attr, value)
            win._gui_windows.begin_frame()
            event = down((100, 500))
            self.assertEqual(gmw.filter_events(win, [event]), [event])
            self.assertEqual((win._gm_search_text, win._gm_search_focused, win._gm_favorites_open),
                             ("", False, False), attr)

    def test_a_press_outside_with_nothing_open_changes_nothing(self):
        event = down((100, 500))
        self.assertEqual(self.run_events(event), [event])
        self.assertIsNone(self.win._gm_win_drag)

    def test_a_press_on_a_dropdown_below_the_window_is_not_a_press_outside(self):
        win = self.win
        win._gm_favorites_open = True
        win._gm_favorites_dropdown_rect = pygame.Rect(self.panel.x + 100, self.panel.bottom - 10, 280, 200)
        event = down((self.panel.x + 150, self.panel.bottom + 100))
        self.assertEqual(self.run_events(event), [event])
        self.assertTrue(win._gm_favorites_open)

    def test_a_window_in_front_gets_the_press_and_the_map_window_does_not_move(self):
        win = self.win
        win._gui_windows.begin_frame()
        frame(win, extra=[("popup", pygame.Rect(self.title[0] - 5, self.title[1] - 5, 10, 10), GuiLayer.SYSTEM)])
        event = down(self.title)
        self.assertEqual(gmw.filter_events(win, [event]), [event])
        self.assertIsNone(win._gm_win_drag)

    def test_only_the_left_button_drags(self):
        for button in (2, 3):
            event = down(self.title, button)
            self.assertEqual(self.run_events(event), [event])
            self.assertIsNone(self.win._gm_win_drag)

    def test_other_events_pass_and_order_is_kept(self):
        key = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a, mod=0, unicode="a")
        wheel = pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=1)
        kept = self.run_events(key, down(self.title), wheel, motion((self.title[0] + 5, self.title[1])), key)
        self.assertEqual(kept, [key, wheel, key])

    def test_nothing_taken_returns_the_same_list(self):
        events = [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a, mod=0, unicode="a")]
        self.assertIs(gmw.filter_events(self.win, events), events)

    def test_a_closed_map_takes_nothing_and_forgets_a_drag(self):
        win = self.win
        self.run_events(down(self.title))
        win._galaxy_map_open = False
        event = motion((50, 50))
        self.assertEqual(gmw.filter_events(win, [event]), [event])
        self.assertIsNone(win._gm_win_drag)

    def test_the_filter_registers_the_window_as_the_managers_owner(self):
        manager = GuiWindowManager()
        win = Light()
        win._gui_windows = manager
        gmw.filter_events(win, [])
        self.assertIs(manager._gm_owner(), win)

    def test_full_screen_only_takes_the_mode_button(self):
        win = Light()
        win._gui_windows._gm_owner = __import__("weakref").ref(win)
        publish(win)
        win._gm_win_toggle_rect = pygame.Rect(1170, 41, 22, 20)
        frame(win)
        header = down((300, 40))
        self.assertEqual(gmw.filter_events(win, [header]), [header])
        self.assertIsNone(win._gm_win_drag)
        edge = down((29, 300))
        self.assertEqual(gmw.filter_events(win, [edge]), [edge])
        self.assertEqual(gmw.filter_events(win, [down(win._gm_win_toggle_rect.center)]), [])
        self.assertTrue(gmw.windowed(win))

    def test_the_mode_button_does_nothing_under_a_dialog(self):
        win = Light()
        win._gui_windows._gm_owner = __import__("weakref").ref(win)
        publish(win)
        win._gm_win_toggle_rect = pygame.Rect(1170, 41, 22, 20)
        manager = win._gui_windows
        manager.begin_frame()
        for window_id in (gmw.MAP_ID, "escape_menu"):             # the menu was queued after the map: in front
            manager.queue_draw(window_id, lambda: None, lambda: pygame.Rect(0, 0, *SCREEN),
                               layer=GuiLayer.MODAL, modal=True, captures_outside=True, focusable=False,
                               visible=True)
        manager.flush_draw_queue()
        event = down(win._gm_win_toggle_rect.center)
        self.assertEqual(gmw.filter_events(win, [event]), [event])
        self.assertFalse(gmw.windowed(win))


# ── the real map drawing ─────────────────────────────────────────────────────

class Batch:
    def __init__(self):
        self.calls = []
        self.clip_rect = None

    def add(self, *args, **kwargs):
        self.calls.append((args, kwargs))

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: None


class FakeGl:
    def __init__(self):
        # v0.5.86's map labels are an atlas the map rebuilds, clips and fills in two layers (``tags`` and ``names``);
        # the game's own map tests stand it in with a mock too
        self.batches = {"map_labels": mock.MagicMock(generation=0)}

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return self.batches.setdefault(name, Batch())


class RealMapTests(Base):
    BACKDROP = (20, 60, 30, 255)

    def setUp(self):
        super().setUp()
        import Client
        self.Client = Client

    def window(self, scale=1.0, size=SCREEN):
        from Client import SolarSystemWindow
        win = SolarSystemWindow.__new__(SolarSystemWindow)
        win._lock = threading.RLock()
        win._uk = scale
        fonts = {}

        def font(px, bold=False, *args, **kwargs):
            key = (px, bold)
            if key not in fonts:
                fonts[key] = pygame.font.SysFont("consolas", max(6, round(px * scale)), bold=bold)
            return fonts[key]

        win._F = win._inventory_font = win._instrument_font = font
        win._hud_plate_cache = {}
        win._fill_surf_cache = {}
        win._font_cache = {}
        win._label_cache = OrderedDict()
        win._autopilot_route = []
        win._autopilot_route_gate_id = None
        win._elapsed = 0.0
        win._layout = {"system_name": "Erebus", "world_w": 250000, "world_h": 250000}
        win._init_galaxy_map()
        win._init_system_details()          # v0.5.86: the map draws the System Details window's state too
        win._text_cursors = {}
        names = ["Erebus", "Chargus", "Talreis", "Syldeus"]
        coords = [(0, 0), (2, -1), (4, 1), (2, 3)]
        win._galaxy_map_positions = {n: {"coord_x": x, "coord_y": y} for n, (x, y) in zip(names, coords)}
        links = {"Erebus": ["Chargus"], "Chargus": ["Erebus", "Talreis"], "Talreis": ["Chargus", "Syldeus"],
                 "Syldeus": ["Talreis"]}
        win._visited_systems = {
            n: {"name": n, "system_id": str(i), "warp_gates": [
                {"name": "g", "x": 1, "y": 1, "target_system": t, "target_gate": "g"} for t in links[n]]}
            for i, n in enumerate(names)}
        win._galaxy_map_open = True
        win._galaxy_map_autocenter = False
        win._gui_windows = GuiWindowManager()
        win._gui_windows._gm_owner = __import__("weakref").ref(win)
        win._screen_size = size
        return win

    def render(self, win, mouse=(5, 5)):
        size = win._screen_size
        screen = pygame.Surface(size, pygame.SRCALPHA)
        screen.fill(self.BACKDROP)
        win._gl = FakeGl()
        with mock.patch("pygame.mouse.get_pos", return_value=mouse):
            win._gui_windows.begin_frame()
            win._gui_windows.queue_draw(
                gmw.MAP_ID, lambda: win._draw_galaxy_map(screen), lambda: pygame.Rect(0, 0, *size),
                layer=GuiLayer.MODAL, modal=True, captures_outside=True, focusable=False,
                visible=lambda: win._galaxy_map_open)
            win._gui_windows.flush_draw_queue()
            win._gui_windows.end_frame()
        overlay = []
        for args, _kwargs in win._gl.overlay_sprites.calls:
            key, surface, cx, cy, w, h = args[:6]
            rect = pygame.Rect(0, 0, w, h)
            rect.center = (cx, cy)
            overlay.append((key, rect))
            screen.blit(surface, rect.topleft)
        win._overlay = overlay
        return screen

    def overlay_keys(self, win):
        return [key[0] if isinstance(key, tuple) else key for key, _rect in win._overlay]

    # -- full screen is exactly what it was ---------------------------------

    def test_full_screen_geometry_is_unchanged(self):
        for scale, pad in ((1.0, 28), (1.5, 42)):
            win = self.window(scale)
            self.render(win)
            panel = win._galaxy_map_panel_rect
            self.assertEqual(panel, pygame.Rect(pad, pad, 1280 - 2 * pad, 720 - 2 * pad), scale)
            chrome = round(46 * scale) if scale != 1.0 else 46
            chamfer = max(8, panel.h // 7)
            chamfer = min(chamfer, round(28 * scale) if scale != 1.0 else 28)
            self.assertEqual(win._galaxy_map_close_rect.right,
                             panel.right - chamfer - round(5 * scale) if scale != 1.0 else panel.right - chamfer - 5)
            self.assertEqual(win._galaxy_map_close_rect.y, panel.y + (chrome - win._galaxy_map_close_rect.h) // 2)

    def test_full_screen_centres_the_map_on_the_screen_and_dims_the_game(self):
        win = self.window()
        screen = self.render(win)
        erebus = win._galaxy_map_node_rects["Erebus"]
        self.assertLessEqual(abs(erebus.centerx - 640), 1)
        self.assertLessEqual(abs(erebus.centery - 360), 1)
        corner = screen.get_at((2, 2))
        self.assertLess(corner[1], self.BACKDROP[1] // 2)                # dimmed

    def test_full_screen_still_has_the_tabs_scans_and_a_mode_button(self):
        win = self.window()
        self.render(win)
        self.assertEqual(set(win._galaxy_map_view_tab_rects), {"coalitions", "territory", "survey"})
        self.assertIsNotNone(win._gm_view_scans_btn_rect)
        self.assertIsNotNone(win._gm_win_toggle_rect)
        self.assertEqual(win._gm_win_toggle_rect.right + 5, win._galaxy_map_close_rect.x)
        self.assertLessEqual(win._gm_view_scans_btn_rect.right, win._gm_win_toggle_rect.x)

    # -- the window ----------------------------------------------------------

    def windowed(self, scale=1.0, rect=None, size=SCREEN):
        win = self.window(scale, size)
        gmw.set_windowed(win, True)
        if rect is not None:
            win._gm_win_rect = pygame.Rect(rect)
        return win

    def test_window_panel_is_the_window_rectangle_and_the_game_is_not_dimmed(self):
        win = self.windowed(rect=(300, 200, 640, 360))
        screen = self.render(win)
        self.assertEqual(win._galaxy_map_panel_rect, pygame.Rect(300, 200, 640, 360))
        for point in ((2, 2), (299, 400), (941, 400), (600, 199), (600, 561), (1279, 719)):
            self.assertEqual(tuple(screen.get_at(point)), self.BACKDROP, point)
        self.assertNotEqual(tuple(screen.get_at((600, 400))), self.BACKDROP)

    def test_window_has_no_tabs_and_no_view_scans(self):
        win = self.windowed()
        self.render(win)
        self.assertEqual(win._galaxy_map_view_tab_rects, {})
        self.assertIsNone(win._gm_view_scans_btn_rect)
        keys = self.overlay_keys(win)
        self.assertNotIn("__gmviewtab__", keys)
        self.assertNotIn("__gmscansbtn__", keys)
        self.assertNotIn("__gmviewtabtext__", keys)

    def test_window_keeps_the_search_favorites_mode_button_and_close(self):
        win = self.windowed()
        self.render(win)
        panel = win._galaxy_map_panel_rect
        controls = [win._gm_search_rect, win._gm_favorites_btn_rect, win._gm_win_toggle_rect,
                    win._galaxy_map_close_rect]
        for rect in controls:
            self.assertTrue(panel.contains(rect), rect)
            self.assertLess(rect.bottom, panel.y + 46, rect)                   # in the header band
        for a, b in zip(controls, controls[1:]):
            self.assertLessEqual(a.right, b.x, (a, b))                        # left to right, never overlapping
        self.assertGreaterEqual(win._gm_search_rect.w, 100)

    def test_the_mode_button_is_a_picture_of_what_it_does(self):
        full = self.window()
        self.render(full)
        keys = [key for key, _rect in full._overlay if isinstance(key, tuple) and key[0] == "__gmwintoggle__"]
        self.assertFalse(keys[0][1])
        win = self.windowed()
        self.render(win)
        keys = [key for key, _rect in win._overlay if isinstance(key, tuple) and key[0] == "__gmwintoggle__"]
        self.assertEqual(len(keys), 1)
        self.assertTrue(keys[0][1])                                            # shown while windowed

    def test_the_mode_button_picture_is_the_one_for_the_other_mode(self):
        calls = []
        real = gmw._icon

        def record(win, to_window, *args):
            calls.append(to_window)
            return real(win, to_window, *args)

        full = self.window()
        with mock.patch.object(gmw, "_icon", side_effect=record):
            self.render(full)
            self.assertEqual(calls, [True])                  # full screen shows "go to the window"
            win = self.windowed()
            calls.clear()
            self.render(win)
            self.assertEqual(calls, [False])

    def test_the_two_mode_pictures_differ_as_described(self):
        win = self.window()
        to_window = gmw._icon(win, True, False, 22, 20)
        to_full = gmw._icon(win, False, False, 22, 20)
        self.assertEqual(to_window.get_at((to_window.get_width() - 1, 0))[3], 255)    # the back frame, top right
        self.assertEqual(to_window.get_at((0, 0))[3], 0)
        self.assertEqual(to_full.get_at((0, 0))[3], 255)                               # one big frame

    def test_switching_from_full_screen_drops_the_tabs_and_scans_that_were_there(self):
        win = self.window()
        self.render(win)
        self.assertTrue(win._galaxy_map_view_tab_rects)
        self.assertIsNotNone(win._gm_view_scans_btn_rect)
        gmw.set_windowed(win, True)
        self.render(win)
        self.assertEqual(win._galaxy_map_view_tab_rects, {})
        self.assertIsNone(win._gm_view_scans_btn_rect)
        gmw.set_windowed(win, False)
        self.render(win)
        self.assertTrue(win._galaxy_map_view_tab_rects)
        self.assertIsNotNone(win._gm_view_scans_btn_rect)

    def test_moving_the_window_moves_its_picture_not_just_its_nodes(self):
        win = self.windowed(rect=(100, 300, 600, 380))
        screen = self.render(win)
        self.assertNotEqual(tuple(screen.get_at((400, 500))), self.BACKDROP)
        win._gm_win_rect = pygame.Rect(700, 100, 600, 380)
        screen = self.render(win)
        self.assertEqual(tuple(screen.get_at((400, 500))), self.BACKDROP)
        self.assertNotEqual(tuple(screen.get_at((1000, 300))), self.BACKDROP)

    def test_changing_the_mode_of_the_same_window_redraws_the_picture(self):
        win = self.window()
        self.render(win)
        gmw.set_windowed(win, True)
        screen = self.render(win)
        self.assertEqual(tuple(screen.get_at((5, 5))), self.BACKDROP)       # not the full screen's dimmed base
        gmw.set_windowed(win, False)
        screen = self.render(win)
        self.assertNotEqual(tuple(screen.get_at((5, 5))), self.BACKDROP)

    def test_the_footer_divider_is_at_the_foot_of_the_window(self):
        steel_dim = (45, 80, 130)
        full = self.window()
        screen = self.render(full)
        self.assertEqual(tuple(screen.get_at((500, 28 + 664 - 46)))[:3], steel_dim)
        win = self.windowed(rect=(300, 200, 640, 360))
        screen = self.render(win)
        y = 200 + 360 - 46
        self.assertEqual(tuple(screen.get_at((500, y)))[:3], steel_dim)
        self.assertNotEqual(tuple(screen.get_at((500, y - 3)))[:3], steel_dim)

    def test_the_search_box_gets_the_room_the_hidden_scans_button_left(self):
        for scale in (1.0, 1.5):
            size = (int(1280 * scale), int(720 * scale))
            win = self.window(scale, size)
            gmw.set_windowed(win, True)
            win._gm_win_rect = pygame.Rect(0, 0, 10, 10)
            self.render(win)
            self.assertGreater(win._gm_search_rect.w, win._s(100), scale)

    def test_the_header_fits_at_the_smallest_window_at_every_ui_scale(self):
        for scale in (1.0, 1.25, 1.5, 2.0):
            size = (int(1280 * scale), int(720 * scale))
            win = self.window(scale, size)
            gmw.set_windowed(win, True)
            win._gm_win_rect = pygame.Rect(0, 0, 10, 10)                       # clamped up to the minimum
            self.render(win)
            panel = win._galaxy_map_panel_rect
            self.assertEqual((panel.w, panel.h), gmw.min_size(win), scale)
            controls = [win._gm_search_rect, win._gm_favorites_btn_rect, win._gm_win_toggle_rect,
                        win._galaxy_map_close_rect]
            for rect in controls:
                self.assertTrue(panel.contains(rect), (scale, rect))
            for a, b in zip(controls, controls[1:]):
                self.assertLessEqual(a.right, b.x, (scale, a, b))
            self.assertGreaterEqual(win._gm_search_rect.w, win._s(100), scale)

    def test_map_is_centred_on_the_window_not_the_screen(self):
        win = self.windowed(rect=(100, 300, 600, 380))
        self.render(win)
        canvas_centre = (100 + 300, 300 + 190)
        erebus = win._galaxy_map_node_rects["Erebus"]
        self.assertLessEqual(abs(erebus.centerx - canvas_centre[0]), 1)
        self.assertLessEqual(abs(erebus.centery - canvas_centre[1]), 1)

    def test_panning_offsets_are_measured_from_the_window_centre(self):
        win = self.windowed(rect=(100, 300, 600, 380))
        win._galaxy_map_offset_x, win._galaxy_map_offset_y = 37.0, -21.0
        self.render(win)
        erebus = win._galaxy_map_node_rects["Erebus"]
        self.assertLessEqual(abs(erebus.centerx - (400 + 37)), 1)
        self.assertLessEqual(abs(erebus.centery - (490 - 21)), 1)

    def test_moving_the_window_moves_the_map_with_it(self):
        win = self.windowed(rect=(100, 300, 600, 380))
        self.render(win)
        before = win._galaxy_map_node_rects["Erebus"].center
        win._gm_win_rect = pygame.Rect(260, 120, 600, 380)
        self.render(win)
        after = win._galaxy_map_node_rects["Erebus"].center
        self.assertEqual((after[0] - before[0], after[1] - before[1]), (160, -180))

    def test_resizing_keeps_the_map_in_the_middle(self):
        win = self.windowed(rect=(100, 100, 600, 380))
        self.render(win)
        win._gm_win_rect = pygame.Rect(100, 100, 900, 500)
        self.render(win)
        erebus = win._galaxy_map_node_rects["Erebus"]
        self.assertLessEqual(abs(erebus.centerx - 550), 1)
        self.assertLessEqual(abs(erebus.centery - 350), 1)

    def test_a_window_draw_never_paints_outside_its_rectangle(self):
        win = self.windowed(rect=(300, 200, 640, 360))
        win._gm_favorites_open = True
        screen = self.render(win)
        panel = win._galaxy_map_panel_rect
        for x in range(0, 1280, 7):
            for y in range(0, 720, 7):
                if not panel.collidepoint((x, y)):
                    self.assertEqual(tuple(screen.get_at((x, y))), self.BACKDROP, (x, y))

    def test_dropdowns_stay_on_screen_even_from_a_window_near_the_bottom(self):
        win = self.windowed(rect=(200, 420, 640, 300))
        win._gm_favorites = [f"System {i}" for i in range(25)]
        win._gm_favorites_open = True
        self.render(win)
        rows = win._gm_favorite_item_rects
        self.assertGreater(len(rows), 0)
        self.assertLess(len(rows), 25)
        for rect, _name in rows:
            self.assertLessEqual(rect.bottom, 720)
        self.assertLessEqual(win._gm_favorites_dropdown_rect.bottom, 720)
        # and the whole dropdown is the map's, even below the window
        below = win._gm_favorites_dropdown_rect.center
        self.assertEqual(win._gui_windows.route_pointer(below).owner, gmw.MAP_ID)

    def test_search_results_stay_on_screen_from_a_window_near_the_bottom(self):
        win = self.windowed(rect=(200, 420, 640, 300))
        win._gm_search_focused = True
        win._gm_search_text = "s"
        win._gm_search_matches = [f"System {i}" for i in range(8)]
        self.render(win)
        self.assertGreater(len(win._gm_search_item_rects), 0)
        for rect, _name in win._gm_search_item_rects:
            self.assertLessEqual(rect.bottom, 720)

    def test_favorites_dropdown_does_not_leave_the_window_on_the_left(self):
        win = self.windowed(rect=(300, 100, 580, 330))
        win._gm_favorites = ["Erebus"]
        win._gm_favorites_open = True
        self.render(win)
        self.assertGreaterEqual(win._gm_favorites_dropdown_rect.x, win._galaxy_map_panel_rect.x + 10)

    def test_the_hint_is_dropped_when_it_does_not_fit_but_kept_in_full_screen(self):
        preview = self.Client._build_route_range_preview("Erebus", ["Chargus", "Talreis", "Syldeus"], 1)
        def too_wide(win):
            ordinary = type(win)._gm_text

            def gm_text(font, text, color, key):
                if key[0] == "__gmhinthud__":                 # a hint far longer than any real one
                    return pygame.Surface((900, 12), pygame.SRCALPHA)
                return ordinary(win, font, text, color, key)
            win._gm_text = gm_text

        full = self.window()
        full._galaxy_map_route_preview = preview
        too_wide(full)
        self.render(full)
        self.assertIn("__gmhinthud__", self.overlay_keys(full))      # full screen draws it however wide it is
        win = self.windowed(rect=(0, 0, 580, 300))
        win._galaxy_map_route_preview = preview
        too_wide(win)
        self.render(win)
        self.assertNotIn("__gmhinthud__", self.overlay_keys(win))
        real = self.windowed(rect=(0, 0, 580, 300))
        real._galaxy_map_route_preview = preview
        self.render(real)
        self.assertIn("__gmhinthud__", self.overlay_keys(real))     # the real longest hint fits the smallest window

    def test_the_ordinary_hint_fits_a_small_window(self):
        win = self.windowed(rect=(0, 0, 580, 300))
        self.render(win)
        hints = [rect for key, rect in win._overlay if isinstance(key, tuple) and key[0] == "__gmhinthud__"]
        self.assertEqual(len(hints), 1)
        self.assertGreaterEqual(hints[0].x, win._galaxy_map_panel_rect.x + 16)
        self.assertLessEqual(hints[0].right, win._gm_win_toggle_rect.x)

    def test_resize_grip_and_hovered_edge_are_drawn_on_the_window_only(self):
        full = self.window()
        self.render(full, mouse=(29, 300))
        self.assertNotIn("__gmwingrip__", self.overlay_keys(full))
        self.assertNotIn("__gmwinedge__", self.overlay_keys(full))
        win = self.windowed(rect=(300, 200, 640, 360))
        self.render(win)
        self.assertIn("__gmwingrip__", self.overlay_keys(win))
        self.assertNotIn("__gmwinedge__", self.overlay_keys(win))
        self.render(win, mouse=(301, 380))                                    # on the left edge
        self.assertIn("__gmwinedge__", self.overlay_keys(win))

    def test_the_grip_sits_in_the_bottom_right_corner(self):
        win = self.windowed(rect=(300, 200, 640, 360))
        self.render(win)
        grip = [rect for key, rect in win._overlay if key and key[0] == "__gmwingrip__"]
        self.assertEqual(len(grip), 1)
        self.assertEqual((grip[0].right, grip[0].bottom), (940, 560))

    def test_each_hovered_edge_is_lit_along_that_edge(self):
        panel = pygame.Rect(300, 200, 640, 360)
        cases = (((301, 380), lambda r: r.x == 300 and r.w <= 3 and r.h > 100),
                 ((938, 380), lambda r: r.right == 940 and r.w <= 3 and r.h > 100),
                 ((600, 201), lambda r: r.y == 200 and r.h <= 3 and r.w > 100),
                 ((600, 558), lambda r: r.bottom == 560 and r.h <= 3 and r.w > 100))
        for mouse, matches in cases:
            win = self.windowed(rect=tuple(panel))
            self.render(win, mouse=mouse)
            lit = [rect for key, rect in win._overlay if key and key[0] == "__gmwinedge__"]
            self.assertEqual(len(lit), 1, mouse)
            self.assertTrue(matches(lit[0]), (mouse, lit[0]))

    def test_the_mode_button_says_what_it_does_when_the_pointer_is_on_it(self):
        full = self.window()
        self.render(full)
        self.assertNotIn("__gmwintip__", self.overlay_keys(full))
        self.render(full, mouse=full._gm_win_toggle_rect.center)
        tips = [key for key, _rect in full._overlay if key and key[0] == "__gmwintip__"]
        self.assertEqual([key[1] for key in tips], ["WINDOW"])
        win = self.windowed()
        self.render(win)
        self.render(win, mouse=win._gm_win_toggle_rect.center)
        tips = [key for key, _rect in win._overlay if key and key[0] == "__gmwintip__"]
        self.assertEqual([key[1] for key in tips], ["FULL SCREEN"])
        tip = [rect for key, rect in win._overlay if key and key[0] == "__gmwintipplate__"][0]
        self.assertGreaterEqual(tip.y, win._gm_win_toggle_rect.bottom)
        self.assertLessEqual(tip.right, win._galaxy_map_close_rect.right)

    def test_the_edge_highlight_follows_an_active_resize(self):
        win = self.windowed(rect=(300, 200, 640, 360))
        win._gm_win_drag = {"kind": "resize", "edges": gmw.EDGE_R | gmw.EDGE_B, "start": pygame.Rect(0, 0, 1, 1),
                            "origin": (0, 0)}
        self.render(win, mouse=(10, 10))
        edges = [key for key in self.overlay_keys(win) if key == "__gmwinedge__"]
        self.assertEqual(len(edges), 2)

    def test_a_remembered_tab_is_not_drawn_in_the_window(self):
        win = self.window()
        win._galaxy_map_view = "survey"
        gmw.set_windowed(win, True)
        self.render(win)
        self.assertEqual(win._galaxy_map_view, "coalitions")
        gmw.set_windowed(win, False)
        self.render(win)
        self.assertEqual(win._galaxy_map_view, "survey")

    def test_plus_minus_zoom_keeps_the_point_under_the_pointer_still(self):
        for windowed in (False, True):
            win = self.windowed(rect=(100, 300, 600, 380)) if windowed else self.window()
            win._galaxy_map_offset_x, win._galaxy_map_offset_y = 25.0, -15.0
            self.render(win)
            centre = gmw.zoom_centre(win, pygame.Surface(SCREEN))
            mouse = (333, 444)
            zoom = win._galaxy_map_zoom
            wx = (mouse[0] - centre[0] - win._galaxy_map_offset_x) / zoom
            wy = (mouse[1] - centre[1] - win._galaxy_map_offset_y) / zoom
            with mock.patch("pygame.mouse.get_pos", return_value=mouse):
                self.Client.SolarSystemWindow._galaxy_map_zoom_step(win, 3, pygame.Surface(SCREEN))
            self.assertNotEqual(win._galaxy_map_zoom, zoom)
            self.assertAlmostEqual(centre[0] + win._galaxy_map_offset_x + wx * win._galaxy_map_zoom, mouse[0], 5)
            self.assertAlmostEqual(centre[1] + win._galaxy_map_offset_y + wy * win._galaxy_map_zoom, mouse[1], 5)

    def test_full_screen_zoom_centre_is_what_it_always_was(self):
        win = self.window()
        self.render(win)
        for size in ((1280, 720), (1281, 721), (1919, 1079)):
            self.assertEqual(gmw.zoom_centre(win, pygame.Surface(size)), (size[0] // 2, size[1] // 2))

    def test_survey_ctrl_click_and_territory_hit_tests_have_nothing_to_hit_in_the_window(self):
        win = self.windowed()
        win._galaxy_map_view = "territory"
        self.render(win)
        self.assertEqual(win._galaxy_map_view, "coalitions")
        self.assertIsNone(win._galaxy_map_csw_hit_geometry)

    def test_auto_center_checkbox_and_member_filters_stay_inside_the_window(self):
        win = self.windowed(rect=(100, 100, 580, 300))
        win._coalition_state = {"coalition_id": 1}
        win._squad_state = {"squad_id": 1}
        self.render(win)
        panel = win._galaxy_map_panel_rect
        for rect in (win._galaxy_map_autocenter_rect, win._galaxy_map_squad_locations_rect,
                     win._galaxy_map_coalition_locations_rect):
            self.assertIsNotNone(rect)
            self.assertTrue(panel.contains(rect), rect)

    def test_rendering_while_resizing_every_frame_works(self):
        win = self.windowed(rect=(100, 100, 600, 380))
        for width in range(580, 700, 10):
            win._gm_win_rect = pygame.Rect(100, 100, width, 380)
            self.render(win)
            self.assertEqual(win._galaxy_map_panel_rect.w, width)

    def test_the_window_draws_in_the_empty_map_case_too(self):
        win = self.windowed(rect=(300, 200, 640, 360))
        win._galaxy_map_positions = {}
        win._visited_systems = {}
        screen = self.render(win)
        self.assertEqual(tuple(screen.get_at((5, 5))), self.BACKDROP)
        self.assertIsNotNone(win._gm_win_toggle_rect)
        self.assertIn("__gmwingrip__", self.overlay_keys(win))

    def test_the_gpu_style_screen_path_leaves_the_game_undimmed_too(self):
        class Gpu(pygame.Surface):
            def fork(self, size):
                return pygame.Surface(size, pygame.SRCALPHA)

        full = self.window()
        screen = Gpu(SCREEN, pygame.SRCALPHA)
        screen.fill(self.BACKDROP)
        full._gl = FakeGl()
        with mock.patch("pygame.mouse.get_pos", return_value=(5, 5)):
            full._draw_galaxy_map(screen)
        self.assertNotEqual(tuple(screen.get_at((5, 5))), self.BACKDROP)
        win = self.windowed(rect=(300, 200, 640, 360))
        screen = Gpu(SCREEN, pygame.SRCALPHA)
        screen.fill(self.BACKDROP)
        win._gl = FakeGl()
        with mock.patch("pygame.mouse.get_pos", return_value=(5, 5)),                 mock.patch("render_cache.build_hud_panel_plate", side_effect=AssertionError("baked a plate")):
            win._draw_galaxy_map(screen)                  # the window's plate is drawn, never baked
        self.assertEqual(tuple(screen.get_at((5, 5))), self.BACKDROP)
        self.assertEqual(tuple(screen.get_at((300 + 100, 200 + 100)))[:3], (5, 9, 18))   # the canvas is there
        self.assertEqual(tuple(screen.get_at((300 + 20, 200 + 30)))[:3], (13, 21, 38))   # and the header band


# ── the vanilla lines that ask this module ──────────────────────────────────

class VanillaHookTests(unittest.TestCase):
    """If an update merge drops one of the marked lines, the window silently half-works: say so here."""

    def source(self, name):
        return modloader.game_source(name)

    def test_client_py_asks_the_module(self):
        source = self.source("Client.py")
        self.assertEqual(source.count("galaxy_map_window.modal_open(self)"), 3)      # Enter, "/", mouse aim
        self.assertEqual(source.count("galaxy_map_window.keys_zoom_map(self)"), 1)
        self.assertEqual(source.count("galaxy_map_window.zoom_centre(self, screen)"), 1)
        self.assertEqual(source.count("galaxy_map_window.filter_events(self, _frame_events)"), 1)
        self.assertEqual(source.count("galaxy_map_window.install(SolarSystemWindow, _render_mixin_module)"), 1)
        self.assertEqual(source.count("import galaxy_map_window"), 1)
        self.assertLess(source.index("pump_watch.get_events()"),
                        source.index("galaxy_map_window.filter_events(self, _frame_events)"))

    def test_the_event_filter_runs_before_the_frames_events_are_used(self):
        source = self.source("Client.py")
        # the game's loop over them reads `for event in _frame_events:` (v0.5.84) or `enumerate(_frame_events)` (v0.5.86)
        loop = min(i for i in (source.find("for event in _frame_events:"),
                               source.find("for _index, event in enumerate(_frame_events):")) if i >= 0)
        self.assertLess(source.index("galaxy_map_window.filter_events(self, _frame_events)"), loop)

    def test_render_mixin_asks_the_module(self):
        source = self.source("render_mixin.py")
        for needle, count in (("galaxy_map_window.panel_geometry(", 1), ("galaxy_map_window.plate_layers(", 1),
                              ("galaxy_map_window.view_centre(", 1), ("galaxy_map_window.origin(self, _PAD)", 2),
                              ("galaxy_map_window.draw_toggle(", 1), ("galaxy_map_window.draw_chrome(", 1),
                              ("galaxy_map_window.fit_rows(", 2),
                              ("galaxy_map_window.modal_open(self)", 2), ("galaxy_map_window.windowed(self)", 2),
                              ("import galaxy_map_window", 1)):
            self.assertEqual(source.count(needle), count, needle)

    def test_the_map_draw_no_longer_assumes_a_full_screen_panel(self):
        source = self.source("render_mixin.py")
        start = source.index("    def _draw_galaxy_map(self, screen")
        end = source.index("    # ── Text / fill / popup render helpers", start)
        body = source[start:end]
        self.assertNotIn("win_w // 2", body)
        self.assertNotIn("win_h // 2", body)
        self.assertNotIn("_PAD + _pw", body)
        self.assertNotIn("(_PAD, _PAD)", body)

    def test_the_header_and_footer_draw_from_the_panel_origin(self):
        source = self.source("render_mixin.py")
        for name, nxt in (("    def _gm_draw_header(", "    _GM_VIEW_TABS"),
                          ("    def _gm_draw_footer(", "    def _gm_system_info_surface")):
            start = source.index(name)
            body = source[start:source.index(nxt, start)]
            self.assertNotIn("_PAD +", body, name)


if __name__ == "__main__":
    unittest.main()

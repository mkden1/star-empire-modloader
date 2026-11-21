"""Tests for range_rings.py - the weapon-range ring and the configurable ring around the ship."""

from __future__ import annotations
import modloader

import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

import range_rings as rr
import scan_writer

SCREEN = (1280, 720)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode(SCREEN)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        rr.reset_for_tests()
        rr.configure_for_tests(os.path.join(self._tmp.name, "range_rings.json"))
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        scan_writer.flush()
        rr.reset_for_tests()
        rr.configure_for_tests(None)
        self._tmp.cleanup()


class FakeWindow:
    def __init__(self, entity=None, x=1000.0, y=1000.0, docked=False):
        self._entity = entity if entity is not None else {"active_weapon_range": 400.0, "effective_visibility": 2000.0}
        self._local_x, self._local_y = x, y
        self._station_overlay = {"kind": "npc"} if docked else None
        self.chat: list = []
        self._fonts: dict = {}

    def _find_my_entity(self):
        return self._entity

    def _F(self, size, bold=False, name="consolas"):
        if (size, bold) not in self._fonts:
            self._fonts[(size, bold)] = pygame.font.SysFont("consolas", size, bold=bold)
        return self._fonts[(size, bold)]

    def chat_log(self, text, colour=None):
        self.chat.append(text)


def make_ctx(zoom=0.5, cam=(1000.0, 1000.0), size=SCREEN):
    screen = pygame.Surface(size, pygame.SRCALPHA)
    screen.fill((0, 0, 0, 0))
    half = (size[0] * 0.5, size[1] * 0.5)

    def w2s(wx, wy):
        return (wx - cam[0]) * zoom + half[0], (wy - cam[1]) * zoom + half[1]

    return SimpleNamespace(screen=screen, w2s=w2s, win_h=size[1], win_w=size[0])


def colour_near(screen, x, y):
    """The first painted pixel in the 5x5 box around (x, y), or None (a ring is 2 px wide: its exact pixel varies)."""
    for dx in range(-2, 3):
        for dy in range(-2, 3):
            pixel = screen.get_at((x + dx, y + dy))
            if pixel[3]:
                return tuple(pixel)
    return None


def painted(screen, rect) -> bool:
    return any(screen.get_at((x, y))[3] for x in range(rect.left, rect.right) for y in range(rect.top, rect.bottom)
               if 0 <= x < screen.get_width() and 0 <= y < screen.get_height())


# ── settings ─────────────────────────────────────────────────────────────────

class SettingsTests(Base):
    def test_the_rings_start_off_with_the_weapon_ring_ready(self):
        self.assertEqual(rr.settings, {"enabled": False, "weapon": True, "custom": False, "mode": "radius",
                                       "radius": 0.0, "multiplier": 1.0, "label": ""})

    def test_clean_keeps_good_values_and_defaults_the_rest(self):
        state = rr.clean({"enabled": False, "weapon": False, "custom": True, "mode": "vis", "radius": 25000,
                          "multiplier": 2.5, "label": "  boss   radar "})
        self.assertEqual(state, {"enabled": False, "weapon": False, "custom": True, "mode": "vis", "radius": 25000.0,
                                 "multiplier": 2.5, "label": "boss radar"})

    def test_clean_rejects_nonsense(self):
        for raw in (None, [], "text", 5):
            self.assertEqual(rr.clean(raw), rr._defaults())
        state = rr.clean({"enabled": "yes", "weapon": 1, "custom": None, "mode": "sideways", "radius": "far",
                          "multiplier": float("nan"), "label": 7})
        self.assertEqual(state, rr._defaults())

    def test_numbers_are_clamped_and_infinity_is_refused(self):
        self.assertEqual(rr.clean({"radius": -5})["radius"], 0.0)
        self.assertEqual(rr.clean({"radius": 10 ** 12})["radius"], rr.MAX_RADIUS)
        self.assertEqual(rr.clean({"radius": float("inf")})["radius"], 0.0)
        self.assertEqual(rr.clean({"multiplier": 10 ** 6})["multiplier"], rr.MAX_MULTIPLIER)
        self.assertEqual(rr.clean({"multiplier": float("-inf")})["multiplier"], 1.0)

    def test_the_label_is_cut(self):
        self.assertEqual(len(rr.clean({"label": "x" * 100})["label"]), rr.MAX_LABEL_LEN)

    def test_a_missing_file_means_the_defaults(self):
        self.assertEqual(rr.load(os.path.join(self._tmp.name, "nothing.json")), rr._defaults())

    def test_a_broken_file_means_the_defaults(self):
        for text in ("", "{not json", "[1, 2]", '"text"'):
            with open(rr._path, "w", encoding="utf-8") as handle:
                handle.write(text)
            self.assertEqual(rr.load(rr._path), rr._defaults(), text)

    def test_the_settings_survive_a_restart(self):
        win = FakeWindow()
        rr.handle_command(win, "/ring 25k boss radar")
        scan_writer.flush()
        rr.settings = rr._defaults()
        rr.load(rr._path)
        self.assertEqual((rr.settings["radius"], rr.settings["custom"], rr.settings["label"]),
                         (25000.0, True, "boss radar"))

    def test_saving_goes_through_the_writer_thread(self):
        with mock.patch.object(rr, "submit") as submit:
            rr.save()
        self.assertIs(submit.call_args.args[0], rr._write)
        self.assertEqual(submit.call_args.args[1], rr._path)

    def test_no_temp_file_is_left_behind(self):
        rr.save()
        scan_writer.flush()
        self.assertEqual(os.listdir(self._tmp.name), ["range_rings.json"])


# ── distances ────────────────────────────────────────────────────────────────

class DistanceTests(unittest.TestCase):
    def test_the_forms_people_type(self):
        for text, expected in (("25000", 25000.0), ("25,000", 25000.0), ("25k", 25000.0), ("25K", 25000.0),
                               ("2.5k", 2500.0), (".5k", 500.0), ("1m", 1_000_000.0), ("  8000  ", 8000.0),
                               ("1,500", 1500.0), ("12.5", 12.5)):
            self.assertEqual(rr.parse_distance(text), expected, text)

    def test_what_is_not_a_distance(self):
        for text in ("0", "0k", "-5", "", "abc", "1e5", "25 k x", "k", "10000001", "5%", "weapon"):
            self.assertIsNone(rr.parse_distance(text), text)

    def test_units_are_shown_with_separators(self):
        self.assertEqual(rr.format_units(2131.4), "2,131")
        self.assertEqual(rr.format_units(25000), "25,000")


# ── which rings ──────────────────────────────────────────────────────────────

class RingsTests(Base):
    def state(self, **kwargs):
        return {**rr._defaults(), "enabled": True, **kwargs}                  # the rings themselves, not the default switch

    def test_the_weapon_ring_is_the_servers_effective_range(self):
        self.assertEqual(rr.rings(self.state(), {"active_weapon_range": 2131.0}), [rr.Ring("weapon", 2131.0, "WEAPON")])

    def test_no_range_from_the_server_means_no_ring(self):
        for entity in (None, {}, {"active_weapon_range": 0}, {"active_weapon_range": -5}, {"active_weapon_range": None},
                       {"active_weapon_range": "far"}, {"active_weapon_range": float("nan")},
                       {"active_weapon_range": float("inf")}):
            self.assertEqual(rr.rings(self.state(), entity), [], entity)

    def test_a_range_given_as_text_is_read(self):
        self.assertEqual(rr.rings(self.state(), {"active_weapon_range": "850"})[0].radius, 850.0)

    def test_switches(self):
        entity = {"active_weapon_range": 500.0}
        self.assertEqual(rr.rings(self.state(enabled=False, custom=True, radius=900.0), entity), [])
        self.assertEqual(rr.rings(self.state(weapon=False), entity), [])
        self.assertEqual([r.kind for r in rr.rings(self.state(custom=True, radius=900.0), entity)],
                         ["weapon", "custom"])
        self.assertEqual([r.kind for r in rr.rings(self.state(custom=False, radius=900.0), entity)], ["weapon"])

    def test_the_custom_ring_is_the_distance_set(self):
        ring = rr.rings(self.state(weapon=False, custom=True, radius=25000.0), {})[0]
        self.assertEqual((ring.kind, ring.radius, ring.label), ("custom", 25000.0, "CUSTOM"))

    def test_an_unset_custom_ring_draws_nothing(self):
        self.assertEqual(rr.rings(self.state(weapon=False, custom=True, radius=0.0), {}), [])

    def test_the_visibility_ring_follows_the_ships_own_visibility(self):
        state = self.state(weapon=False, custom=True, mode="vis", multiplier=2.5)
        ring = rr.rings(state, {"effective_visibility": 80.0})[0]
        self.assertEqual((ring.radius, ring.label), (200.0, "VIS x2.5"))
        self.assertEqual(rr.rings(state, {"effective_visibility": 4000.0})[0].radius, 10000.0)    # it follows a change
        self.assertEqual(rr.rings(state, {}), [])
        self.assertEqual(rr.rings(state, {"effective_visibility": 0}), [])

    def test_labels(self):
        state = self.state(weapon=False, custom=True, radius=900.0, label="boss radar")
        self.assertEqual(rr.rings(state, {})[0].label, "boss radar")
        self.assertEqual(rr.custom_label(self.state(mode="vis", multiplier=1.0)), "VIS x1")
        self.assertEqual(rr.custom_label(self.state(mode="vis", multiplier=1.5, label="x")), "x")

    def test_a_ring_beyond_any_real_distance_is_not_drawn(self):
        state = self.state(weapon=False, custom=True, mode="vis", multiplier=1000.0)
        self.assertEqual(rr.rings(state, {"effective_visibility": 10 ** 9}), [])


# ── the chat commands ────────────────────────────────────────────────────────

class CommandTests(Base):
    def run_command(self, text, **state):
        return rr.apply_command({**rr._defaults(), **state}, text)

    def test_rings_switches_everything(self):
        state, message, changed = self.run_command("/rings")                   # they start off
        self.assertEqual((state["enabled"], message, changed), (True, "Range rings on.", True))
        state, message, _ = rr.apply_command(state, "/rings")
        self.assertEqual((state["enabled"], message), (False, "Range rings off."))

    def test_a_bare_ring_command_says_the_rings_are_off_until_they_are_switched_on(self):
        _state, message, changed = self.run_command("/ring")
        self.assertFalse(changed)
        self.assertTrue(message.startswith("Range rings are off (/rings turns them on)."), message)

    def test_a_bare_ring_command_reports_and_changes_nothing(self):
        state, message, changed = self.run_command("/ring", enabled=True)
        self.assertFalse(changed)
        self.assertEqual(state, {**rr._defaults(), "enabled": True})
        self.assertIn("weapon ring on", message)
        self.assertIn("custom ring not set (off)", message)
        self.assertIn("/ring <distance>", message)

    def test_the_report_says_what_is_set(self):
        text = rr.describe({**rr._defaults(), "enabled": True, "custom": True, "radius": 25000.0, "label": "boss"})
        self.assertIn("custom ring 25,000 units 'boss' (on)", text)
        text = rr.describe({**rr._defaults(), "enabled": True, "custom": True, "mode": "vis", "multiplier": 2.0})
        self.assertIn("your visibility x2 (on)", text)
        self.assertEqual(rr.describe({**rr._defaults(), "enabled": False}), "Range rings are off (/rings turns them on).")

    def test_weapon_switches(self):
        state, message, _ = self.run_command("/ring weapon")
        self.assertEqual((state["weapon"], message), (False, "Weapon ring off."))
        state, _m, _c = self.run_command("/ring weapon on", weapon=False)
        self.assertTrue(state["weapon"])
        state, _m, _c = self.run_command("/ring weapon off")
        self.assertFalse(state["weapon"])
        state, message, changed = self.run_command("/ring weapon maybe")
        self.assertEqual((state["weapon"], changed), (True, False))
        self.assertIn("Usage", message)

    def test_switching_the_weapon_ring_on_also_turns_the_master_on(self):
        state, _m, _c = self.run_command("/ring weapon on", enabled=False, weapon=False)
        self.assertEqual((state["enabled"], state["weapon"]), (True, True))
        state, _m, _c = self.run_command("/ring weapon off", enabled=False, weapon=True)
        self.assertFalse(state["enabled"])                                   # switching off leaves the master alone

    def test_a_distance_sets_and_shows_the_custom_ring(self):
        state, message, changed = self.run_command("/ring 25000", enabled=False)
        self.assertEqual((state["radius"], state["mode"], state["custom"], state["enabled"], changed),
                         (25000.0, "radius", True, True, True))
        self.assertEqual(message, "Custom ring at 25,000 units.")

    def test_a_distance_with_a_label(self):
        state, _m, _c = self.run_command("/ring 25k boss radar range")
        self.assertEqual((state["radius"], state["label"]), (25000.0, "boss radar range"))
        state, _m, _c = self.run_command("/ring 5000 " + "y" * 60)
        self.assertEqual(len(state["label"]), rr.MAX_LABEL_LEN)

    def test_a_new_distance_replaces_the_old_label(self):
        state, _m, _c = self.run_command("/ring 9000", label="old", mode="vis", multiplier=3.0)
        self.assertEqual((state["label"], state["mode"], state["radius"]), ("", "radius", 9000.0))

    def test_what_is_not_a_distance_is_refused(self):
        state, message, changed = self.run_command("/ring banana")
        self.assertEqual((state, changed), (rr._defaults(), False))
        self.assertTrue(message.startswith("Not a distance: banana."))
        self.assertEqual(self.run_command("/ring 0")[2], False)
        self.assertEqual(self.run_command("/ring -5")[2], False)

    def test_on_and_off_for_the_custom_ring(self):
        state, message, _ = self.run_command("/ring off", custom=True, radius=900.0)
        self.assertEqual((state["custom"], state["radius"], message), (False, 900.0, "Custom ring off."))
        state, message, _ = self.run_command("/ring on", custom=False, radius=900.0)
        self.assertEqual((state["custom"], message), (True, "Custom ring on."))

    def test_on_without_anything_set_says_so(self):
        state, message, changed = self.run_command("/ring on")
        self.assertEqual((state["custom"], changed), (False, False))
        self.assertIn("No custom ring set yet", message)

    def test_on_works_for_a_visibility_ring_without_a_radius(self):
        state, _m, changed = self.run_command("/ring on", mode="vis", radius=0.0)
        self.assertEqual((state["custom"], changed), (True, True))

    def test_turning_the_custom_ring_on_turns_the_master_on(self):
        state, _m, _c = self.run_command("/ring on", enabled=False, radius=900.0)
        self.assertTrue(state["enabled"])
        state, _m, _c = self.run_command("/ring off", enabled=False, custom=True, radius=900.0)
        self.assertFalse(state["enabled"])

    def test_the_visibility_ring(self):
        state, message, changed = self.run_command("/ring vis")
        self.assertEqual((state["mode"], state["multiplier"], state["custom"], changed), ("vis", 1.0, True, True))
        self.assertIn("starts to see you", message)
        for text, expected in (("/ring vis 2.5", 2.5), ("/ring vis x2.5", 2.5), ("/ring vis ×3", 3.0),
                               ("/ring vis 2,5", 2.5), ("/ring vis .5", 0.5), ("/ring vis 2", 2.0)):
            self.assertEqual(self.run_command(text)[0]["multiplier"], expected, text)

    def test_the_visibility_ring_takes_a_label_with_or_without_a_multiplier(self):
        state, _m, _c = self.run_command("/ring vis 2.5 boss radar")
        self.assertEqual((state["multiplier"], state["label"]), (2.5, "boss radar"))
        state, _m, _c = self.run_command("/ring vis boss radar")
        self.assertEqual((state["multiplier"], state["label"]), (1.0, "boss radar"))

    def test_a_visibility_ring_label_is_cut(self):
        state, _m, _c = self.run_command("/ring vis 2 " + "y" * 60)
        self.assertEqual(len(state["label"]), rr.MAX_LABEL_LEN)

    def test_a_zero_multiplier_is_refused_and_a_huge_one_clamped(self):
        state, message, changed = self.run_command("/ring vis 0")
        self.assertEqual((state, changed), (rr._defaults(), False))
        self.assertIn("more than 0", message)
        self.assertEqual(self.run_command("/ring vis 99999")[0]["multiplier"], rr.MAX_MULTIPLIER)

    def test_commands_are_case_insensitive_and_the_slash_is_optional(self):
        self.assertEqual(self.run_command("/RING WEAPON OFF")[0]["weapon"], False)
        self.assertEqual(self.run_command("ring 25K")[0]["radius"], 25000.0)
        self.assertEqual(self.run_command("/Rings")[0]["enabled"], True)

    def test_the_input_state_is_never_changed_in_place(self):
        original = rr._defaults()
        rr.apply_command(original, "/ring 9000")
        self.assertEqual(original, rr._defaults())

    def test_handling_a_command_applies_saves_and_answers(self):
        win = FakeWindow()
        rr.handle_command(win, "/ring 25000 boss")
        self.assertEqual((rr.settings["radius"], rr.settings["label"], rr.settings["custom"]), (25000.0, "boss", True))
        self.assertEqual(win.chat, ["Custom ring at 25,000 units."])
        scan_writer.flush()
        with open(rr._path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["radius"], 25000.0)

    def test_a_command_that_changes_nothing_saves_nothing(self):
        win = FakeWindow()
        with mock.patch.object(rr, "submit") as submit:
            rr.handle_command(win, "/ring")
            rr.handle_command(win, "/ring nonsense")
        self.assertEqual(submit.call_count, 0)
        self.assertEqual(len(win.chat), 2)

    def test_the_kill_switch(self):
        win = FakeWindow()
        with mock.patch.dict(os.environ, {rr.ENV_OFF: "1"}):
            rr.handle_command(win, "/ring 9000")
        self.assertEqual(rr.settings["radius"], 0.0)
        self.assertIn("switched off", win.chat[0])

    def test_an_error_switches_the_feature_off_for_the_session(self):
        win = FakeWindow()
        with mock.patch.object(rr, "apply_command", side_effect=RuntimeError("boom")):
            with self.assertLogs("range_rings", level="ERROR"):
                rr.handle_command(win, "/ring 9000")
        self.assertTrue(rr._failed)


# ── geometry ─────────────────────────────────────────────────────────────────

class GeometryTests(unittest.TestCase):
    def test_a_distance_becomes_pixels_through_the_games_own_transform(self):
        ctx = make_ctx(zoom=0.5)
        self.assertEqual(rr.screen_radius(ctx.w2s, (1000.0, 1000.0), 400.0), 200.0)
        ctx = make_ctx(zoom=2.0)
        self.assertEqual(rr.screen_radius(ctx.w2s, (1000.0, 1000.0), 400.0), 800.0)

    def test_a_scaling_that_is_not_plain_still_works(self):
        def stretched(x, y):
            return x * 0.3, y * 0.3
        self.assertAlmostEqual(rr.screen_radius(stretched, (10.0, 10.0), 100.0), 30.0)

    def test_a_turning_transform_gives_the_same_radius_in_every_direction(self):
        angle = math.radians(30)

        def turned(x, y):
            return (x * math.cos(angle) - y * math.sin(angle)) * 0.5, (x * math.sin(angle) + y * math.cos(angle)) * 0.5
        self.assertAlmostEqual(rr.screen_radius(turned, (10.0, 20.0), 400.0), 200.0)

    def test_which_rings_cross_the_screen(self):
        w, h = SCREEN
        centre = (640.0, 360.0)
        self.assertTrue(rr.ring_on_screen(centre, 200, w, h))
        self.assertTrue(rr.ring_on_screen(centre, 600, w, h))                      # off the sides, still on the corners
        self.assertFalse(rr.ring_on_screen(centre, 800, w, h))                     # beyond every corner
        self.assertFalse(rr.ring_on_screen((5000.0, 360.0), 200, w, h))            # ship far off screen, small ring
        self.assertTrue(rr.ring_on_screen((5000.0, 360.0), 4500, w, h))            # a big ring that reaches in
        self.assertFalse(rr.ring_on_screen((5000.0, 360.0), 9000, w, h))

    def test_the_label_goes_top_right_when_it_can(self):
        x, y = rr.label_anchor((640.0, 360.0), 200.0, 1280, 720, 6, (100, 14))
        self.assertGreater(x, 640)
        self.assertLess(y, 360)

    def test_the_label_sits_above_the_point_of_the_ring_it_names(self):
        centre, radius, size = (640.0, 360.0), 200.0, (100, 14)
        x, y = rr.label_anchor(centre, radius, 1280, 720, 6, size)
        ring_y = 360 - 200 * math.sin(math.radians(45))
        self.assertLessEqual(y + size[1], ring_y)                                  # clear of the line it labels
        self.assertAlmostEqual(x + size[0] / 2, 640 + 200 * math.cos(math.radians(45)), delta=1)

    def test_the_label_keeps_its_margin_from_the_screen_edge(self):
        size = (100, 14)
        # put the first spot (45 degrees) so its label would start 4 px from the left edge
        cx = 4 + size[0] / 2 - 200 * math.cos(math.radians(45))
        free = rr.label_anchor((cx, 360.0), 200.0, 1280, 720, 0, size)
        kept = rr.label_anchor((cx, 360.0), 200.0, 1280, 720, 6, size)
        self.assertEqual(free[0], 4)                                               # no margin: it fits at 4 px
        self.assertNotEqual(kept, free)                                            # a 6 px margin: another spot
        self.assertGreaterEqual(kept[0], 6)

    def test_the_label_moves_when_the_first_spot_is_off_screen(self):
        # ship near the top right corner: 45 degrees is off screen, so another direction is used
        anchor = rr.label_anchor((1250.0, 30.0), 200.0, 1280, 720, 6, (100, 14))
        self.assertIsNotNone(anchor)
        self.assertTrue(6 <= anchor[0] and anchor[0] + 100 <= 1280 - 6)
        self.assertTrue(6 <= anchor[1] and anchor[1] + 14 <= 720 - 6)

    def test_no_label_when_no_part_of_the_ring_with_room_is_on_screen(self):
        self.assertIsNone(rr.label_anchor((640.0, 360.0), 5000.0, 1280, 720, 6, (100, 14)))
        self.assertIsNone(rr.label_anchor((9000.0, 9000.0), 100.0, 1280, 720, 6, (100, 14)))


# ── drawing ──────────────────────────────────────────────────────────────────

class DrawTests(Base):
    def setUp(self):
        super().setUp()
        rr.settings["enabled"] = True                      # the rings start off; these tests are about drawing them

    def draw(self, window=None, ctx=None):
        window = window or FakeWindow()
        ctx = ctx or make_ctx()
        rr.draw(window, ctx)
        return ctx.screen

    def test_the_weapon_ring_is_a_circle_of_the_right_size_around_the_ship(self):
        screen = self.draw()                                    # range 400 at zoom 0.5: 200 px around (640, 360)
        for dx, dy in ((200, 0), (-200, 0), (0, 200), (0, -200)):
            self.assertTrue(painted(screen, pygame.Rect(640 + dx - 2, 360 + dy - 2, 5, 5)), (dx, dy))
        self.assertFalse(painted(screen, pygame.Rect(640 - 3, 360 - 3, 7, 7)))             # not at the ship
        self.assertFalse(painted(screen, pygame.Rect(640 + 100 - 3, 360 - 3, 7, 7)))       # nor inside
        self.assertFalse(painted(screen, pygame.Rect(640 + 230 - 3, 360 - 3, 7, 7)))       # nor outside
        red, green, blue, alpha = colour_near(screen, 840, 360)
        self.assertGreater(red, green)                                                       # amber
        self.assertGreater(green, blue)

    def test_the_ring_follows_the_ship_and_the_zoom(self):
        window = FakeWindow(x=1100.0, y=900.0)                  # 100 right, 100 up from where the camera looks
        ctx = make_ctx(zoom=1.0, cam=(1000.0, 1000.0))
        screen = self.draw(window, ctx)
        self.assertTrue(painted(screen, pygame.Rect(640 + 100 + 400 - 2, 360 - 100 - 2, 5, 5)))
        self.assertFalse(painted(screen, pygame.Rect(640 + 400 - 3, 360 - 3, 7, 7)))

    def test_the_custom_ring_is_red_and_the_weapon_ring_stays(self):
        rr.settings.update(custom=True, radius=1000.0)          # 500 px at zoom 0.5
        screen = self.draw()
        self.assertTrue(painted(screen, pygame.Rect(640 + 200 - 2, 360 - 2, 5, 5)))
        self.assertTrue(painted(screen, pygame.Rect(640 + 500 - 2, 360 - 2, 5, 5)))
        red, green, blue, _alpha = colour_near(screen, 1140, 360)
        self.assertGreater(red, 200)
        self.assertLess(green, 150)

    def test_a_custom_ring_alone(self):
        rr.settings.update(weapon=False, custom=True, radius=1000.0)
        screen = self.draw()
        self.assertFalse(painted(screen, pygame.Rect(640 + 200 - 3, 360 - 3, 7, 7)))
        self.assertTrue(painted(screen, pygame.Rect(640 + 500 - 2, 360 - 2, 5, 5)))

    def test_the_visibility_ring_is_drawn_from_the_ships_visibility(self):
        rr.settings.update(weapon=False, custom=True, mode="vis", multiplier=2.0)
        window = FakeWindow(entity={"effective_visibility": 1000.0})                      # 2000 u = 1000 px
        screen = self.draw(window)
        self.assertFalse(painted(screen, pygame.Rect(640 + 500 - 3, 360 - 3, 7, 7)))
        window = FakeWindow(entity={"effective_visibility": 400.0})                       # 800 u = 400 px
        screen = self.draw(window)
        self.assertTrue(painted(screen, pygame.Rect(640 + 400 - 2, 360 - 2, 5, 5)))

    def test_each_ring_is_labelled_with_its_name_and_distance(self):
        rr.settings.update(custom=True, radius=1000.0, label="boss")
        surfaces = []
        original = rr._label_surface

        def spy(win, text, colour):
            surfaces.append(text)
            return original(win, text, colour)

        with mock.patch.object(rr, "_label_surface", spy):
            self.draw()
        self.assertEqual(surfaces, ["WEAPON  400", "boss  1,000"])

    def test_a_label_is_drawn_next_to_the_ring(self):
        screen = self.draw()
        self.assertTrue(painted(screen, pygame.Rect(640 + 100, 360 - 200, 140, 30)))       # top right of the ring

    def test_nothing_while_docked(self):
        screen = self.draw(FakeWindow(docked=True))
        self.assertFalse(painted(screen, screen.get_rect()))

    def test_nothing_before_the_ship_is_known(self):
        window = FakeWindow()
        window._entity = None
        window._find_my_entity = lambda: None
        screen = self.draw(window)
        self.assertFalse(painted(screen, screen.get_rect()))

    def test_a_fixed_custom_ring_waits_for_the_ship_too(self):
        rr.settings.update(weapon=False, custom=True, radius=1000.0)
        window = FakeWindow()
        window._find_my_entity = lambda: None
        screen = self.draw(window)
        self.assertFalse(painted(screen, screen.get_rect()))

    def test_a_ring_that_cannot_reach_the_screen_costs_no_drawing_at_all(self):
        rr.settings.update(custom=True, radius=5_000_000.0)                     # 2.5 million px: nowhere near the screen
        with mock.patch.object(pygame.draw, "circle", wraps=pygame.draw.circle) as circle:
            self.draw()
        self.assertEqual(circle.call_count, 1)                                   # only the weapon ring

    def test_the_label_really_is_blitted(self):
        marker = pygame.Surface((40, 12), pygame.SRCALPHA)
        marker.fill((255, 0, 255, 255))
        with mock.patch.object(rr, "_label_surface", return_value=marker):
            screen = self.draw()
        found = [(x, y) for x in range(640, 1000) for y in range(100, 360) if tuple(screen.get_at((x, y))) == (255, 0, 255, 255)]
        self.assertEqual(len(found), 40 * 12)

    def test_nothing_when_switched_off_or_without_a_range(self):
        rr.settings["enabled"] = False
        self.assertFalse(painted(self.draw(), pygame.Rect(0, 0, *SCREEN)))
        rr.settings["enabled"] = True
        window = FakeWindow(entity={"active_weapon_range": 0})
        self.assertFalse(painted(self.draw(window), pygame.Rect(0, 0, *SCREEN)))

    def test_a_ring_bigger_than_the_screen_is_skipped_without_fuss(self):
        rr.settings.update(custom=True, radius=5_000_000.0)
        screen = self.draw()
        self.assertFalse(painted(screen, pygame.Rect(640 + 700, 360, 40, 5)))               # only the weapon ring exists

    def test_a_tiny_ring_is_skipped(self):
        window = FakeWindow(entity={"active_weapon_range": 4.0})                            # 2 px
        screen = self.draw(window)
        self.assertFalse(painted(screen, pygame.Rect(0, 0, *SCREEN)))

    def test_a_ring_partly_off_screen_still_draws_its_visible_part(self):
        window = FakeWindow(x=1000.0 + 1000.0, y=1000.0)                                    # ship 500 px right of centre
        screen = self.draw(window, make_ctx(zoom=0.5))
        self.assertTrue(painted(screen, pygame.Rect(0, 0, *SCREEN)))
        self.assertTrue(painted(screen, pygame.Rect(1140 - 200 - 2, 360 - 2, 5, 5)))        # its left edge, on screen

    def test_label_surfaces_are_cached_and_the_cache_is_bounded(self):
        window = FakeWindow()
        first = rr._label_surface(window, "WEAPON  400", (255, 180, 90))
        self.assertIs(rr._label_surface(window, "WEAPON  400", (255, 180, 90)), first)
        for i in range(100):
            rr._label_surface(window, f"label {i}", (1, 2, 3))
        self.assertLessEqual(len(window._rr_labels), 64)


# ── the hook ─────────────────────────────────────────────────────────────────

class InstallTests(Base):
    def make_class(self):
        calls = []

        class Window(FakeWindow):
            def _draw_turret_aim_overlay(self, ctx):
                calls.append("original")
                return "result"

        return Window, calls

    def test_the_rings_are_drawn_after_the_original_which_still_returns_its_result(self):
        Window, calls = self.make_class()
        self.assertTrue(rr.install(Window))
        window = Window()
        ctx = make_ctx()
        with mock.patch.object(rr, "draw", side_effect=lambda w, c: calls.append("rings")):
            self.assertEqual(window._draw_turret_aim_overlay(ctx), "result")
        self.assertEqual(calls, ["original", "rings"])

    def test_installing_twice_wraps_once(self):
        Window, _calls = self.make_class()
        self.assertTrue(rr.install(Window))
        self.assertFalse(rr.install(Window))
        self.assertFalse(hasattr(Window._draw_turret_aim_overlay.__wrapped__, "__wrapped__"))

    def test_an_error_while_drawing_switches_the_rings_off_not_the_game(self):
        Window, calls = self.make_class()
        rr.install(Window)
        window = Window()
        with mock.patch.object(rr, "draw", side_effect=RuntimeError("boom")):
            with self.assertLogs("range_rings", level="ERROR"):
                self.assertEqual(window._draw_turret_aim_overlay(make_ctx()), "result")
        self.assertTrue(rr._failed)
        with mock.patch.object(rr, "draw", side_effect=AssertionError("drew again")):
            window._draw_turret_aim_overlay(make_ctx())                # stays off

    def test_the_kill_switch_leaves_only_the_original(self):
        Window, calls = self.make_class()
        rr.install(Window)
        with mock.patch.dict(os.environ, {rr.ENV_OFF: "1"}), mock.patch.object(rr, "draw") as draw:
            self.assertEqual(Window()._draw_turret_aim_overlay(make_ctx()), "result")
        self.assertEqual(draw.call_count, 0)
        self.assertFalse(rr._failed)

    def test_install_reads_the_settings_file(self):
        with open(rr._path, "w", encoding="utf-8") as handle:
            json.dump({"custom": True, "radius": 7000, "label": "saved"}, handle)
        Window, _calls = self.make_class()
        rr.install(Window)
        self.assertEqual((rr.settings["radius"], rr.settings["label"]), (7000.0, "saved"))


class VanillaHookTests(unittest.TestCase):
    def source(self, name):
        return modloader.game_source(name)

    def test_client_py_asks_the_module(self):
        source = self.source("Client.py")
        self.assertEqual(source.count("import range_rings"), 1)
        self.assertEqual(source.count('elif _cmd in ("ring", "rings"):'), 1)
        self.assertEqual(source.count("range_rings.handle_command(self, _msg)"), 1)
        self.assertEqual(source.count("range_rings.install(SolarSystemWindow)"), 1)

    def test_the_game_still_calls_the_overlay_the_rings_ride_on_once_per_frame(self):
        source = self.source("Client.py")
        self.assertEqual(source.count("self._draw_turret_aim_overlay(_frame)"), 1)
        draw = source.index("self._draw_turret_aim_overlay(_frame)")
        self.assertLess(source.index("self._draw_ships(_frame"), draw)               # after the ships, like the aim lines
        self.assertGreater(source.index("self._draw_hud_top_panel(screen)"), draw)    # and under the HUD


class RealWindowTests(Base):
    def setUp(self):
        super().setUp()
        rr.settings["enabled"] = True                      # the rings start off; these tests are about drawing them

    def test_the_hook_is_on_the_real_window_and_draws_through_the_real_overlay(self):
        import Client
        cls = Client.SolarSystemWindow
        self.assertTrue(cls.__dict__.get("_range_rings_installed"))
        self.assertTrue(hasattr(cls._draw_turret_aim_overlay, "__wrapped__"))
        window = cls.__new__(cls)
        fake = FakeWindow()
        window._show_turret_overlay = False                   # the game's own aim lines are off: only the rings draw
        window._station_overlay = None
        window._local_x, window._local_y = 1000.0, 1000.0
        window._find_my_entity = fake._find_my_entity
        window._F = fake._F
        ctx = make_ctx()
        window._draw_turret_aim_overlay(ctx)
        self.assertTrue(painted(ctx.screen, pygame.Rect(640 + 200 - 2, 360 - 2, 5, 5)))


if __name__ == "__main__":
    unittest.main()

"""Tests for the zoom_floor mod: the wheel's stops (zoom_floor.py) and the patched game's zoom limits."""

from __future__ import annotations
import modloader

import unittest
from types import SimpleNamespace

import zoom_floor

FLOOR, CEILING = 0.025, 0.40


def near(testcase, got, expected):
    testcase.assertAlmostEqual(got, expected, places=9)


class StopsTests(unittest.TestCase):
    def test_the_stops_are_the_floor_and_the_multiples_of_0_05(self):
        got = zoom_floor.stops(FLOOR, CEILING)
        expected = [0.025, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40]
        self.assertEqual(len(got), len(expected))
        for g, e in zip(got, expected):
            near(self, g, e)

    def test_a_floor_on_the_grid_gives_the_games_own_ladder(self):
        for floor, first in ((0.05, 0.05), (0.10, 0.10)):
            got = zoom_floor.stops(floor, CEILING)
            near(self, got[0], first)
            self.assertEqual(len(got), int(round((CEILING - floor) / 0.05)) + 1)

    def test_a_ceiling_off_the_grid_is_a_stop_too(self):
        got = zoom_floor.stops(0.10, 0.42)
        near(self, got[-1], 0.42)
        near(self, got[-2], 0.40)

    def test_walking_out_one_notch_at_a_time_ends_at_the_floor_and_stays_there(self):
        zoom, seen = CEILING, [CEILING]
        for _ in range(20):
            zoom = zoom_floor.step(zoom, -1, FLOOR, CEILING)
            seen.append(zoom)
        near(self, seen[1], 0.35)
        near(self, seen[7], 0.05)
        near(self, seen[8], 0.025)
        self.assertEqual(seen[8:], [seen[8]] * len(seen[8:]))

    def test_walking_back_in_retraces_the_stops(self):
        zoom = FLOOR
        for expected in (0.05, 0.10, 0.15, 0.20):
            zoom = zoom_floor.step(zoom, 1, FLOOR, CEILING)
            near(self, zoom, expected)
        for _ in range(20):
            zoom = zoom_floor.step(zoom, 1, FLOOR, CEILING)
        near(self, zoom, CEILING)

    def test_a_fast_scroll_moves_one_stop_per_notch(self):
        near(self, zoom_floor.step(0.40, -3, FLOOR, CEILING), 0.25)
        near(self, zoom_floor.step(0.10, -5, FLOOR, CEILING), FLOOR)
        near(self, zoom_floor.step(FLOOR, 2, FLOOR, CEILING), 0.10)
        near(self, zoom_floor.step(0.20, 99, FLOOR, CEILING), CEILING)

    def test_no_notch_leaves_the_current_zoom_but_clamps_it(self):
        near(self, zoom_floor.step(0.20, 0, FLOOR, CEILING), 0.20)
        near(self, zoom_floor.step(0.01, 0, FLOOR, CEILING), FLOOR)
        near(self, zoom_floor.step(0.90, 0, FLOOR, CEILING), CEILING)

    def test_a_zoom_between_stops_goes_to_the_next_stop_in_the_direction_asked(self):
        near(self, zoom_floor.step(0.075, 1, FLOOR, CEILING), 0.10)
        near(self, zoom_floor.step(0.075, -1, FLOOR, CEILING), 0.05)
        near(self, zoom_floor.step(0.03, -1, FLOOR, CEILING), FLOOR)
        near(self, zoom_floor.step(0.03, 1, FLOOR, CEILING), 0.05)
        near(self, zoom_floor.step(0.9, -1, FLOOR, CEILING), CEILING)       # past the ceiling: the top stop
        near(self, zoom_floor.step(0.001, 1, FLOOR, CEILING), FLOOR)        # below the floor: the floor

    def test_above_the_floor_it_moves_exactly_as_the_game_does(self):
        """The game's own step is ``zoom + direction * 0.05``, clamped. On the stops above the floor the ladder agrees."""
        for zoom in zoom_floor.stops(0.05, CEILING):
            for direction in range(-3, 4):
                expected = max(0.05, min(CEILING, zoom + direction * 0.05))
                near(self, zoom_floor.step(zoom, direction, 0.05, CEILING), expected)


class PatchedGameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import Client
        cls.window = Client.SolarSystemWindow

    def stand_in(self, zoom):
        return SimpleNamespace(_zoom=zoom, CAM_MIN_ZOOM=self.window.CAM_MIN_ZOOM, CAM_MAX_ZOOM=self.window.CAM_MAX_ZOOM)

    def test_the_game_zooms_out_to_the_floor(self):
        near(self, self.window.CAM_MIN_ZOOM, FLOOR)
        near(self, self.window.CAM_MAX_ZOOM, CEILING)

    def test_the_comet_layout_is_calibrated_to_the_same_floor(self):
        """render_mixin keeps its own copy of the number (a class attribute cannot read the window's); they must agree."""
        near(self, self.window._COMET_REF_ZOOM, self.window.CAM_MIN_ZOOM)

    def test_the_wheel_walks_the_stops_through_the_games_own_method(self):
        win = self.stand_in(0.10)
        self.window._camera_zoom_step(win, -1)
        near(self, win._zoom, 0.05)
        self.window._camera_zoom_step(win, -1)
        near(self, win._zoom, FLOOR)
        self.window._camera_zoom_step(win, -1)
        near(self, win._zoom, FLOOR)
        self.window._camera_zoom_step(win, 1)
        near(self, win._zoom, 0.05)
        self.window._camera_zoom_step(win, 40)
        near(self, win._zoom, CEILING)

    def test_every_stop_is_a_fixed_point_of_the_sizes_rounding_and_never_zero(self):
        """What a size or a cache key is made at is the rounded zoom; at every stop it must be the zoom itself."""
        win = self.stand_in(FLOOR)
        for zoom in zoom_floor.stops(self.window.CAM_MIN_ZOOM, self.window.CAM_MAX_ZOOM):
            rounded = self.window._zoom_bucket(win, zoom)
            self.assertGreater(rounded, 0.0)
            near(self, rounded, zoom)

    def test_the_frame_rounds_its_zoom_the_same_way(self):
        source = modloader.game_source("Client.py")
        self.assertEqual(source.count("_zoom_b = max(self.CAM_MIN_ZOOM, round(self._zoom * 20) / 20)"), 1)
        self.assertNotIn("_zoom_b = round(self._zoom * 20) / 20", source)

    def test_no_copy_of_the_games_old_zoom_range_is_left_in_the_code_that_reads_it(self):
        source = modloader.game_source("Client.py")
        self.assertNotIn("max(0.10, min(0.40, _cur_zb))", source)
        self.assertNotIn("_ALL_ZOOM_BUCKETS", source)
        self.assertNotIn("(self._zoom - 0.10) * (0.5 / 0.30)", source)
        self.assertNotIn("(ctx.zoom_b - 0.10) / 0.30", modloader.game_source("render_mixin.py"))


if __name__ == "__main__":
    unittest.main()

"""Tests for the Galaxy Map Survey view's terra-planet traffic light."""

from __future__ import annotations

import unittest

from galaxy_survey import (DEFAULT_MAX_TERRA, TERRA_GREEN, TERRA_RED,
                           TERRA_YELLOW, lerp_rgb, terra_traffic_light_color)


class LerpRgbTests(unittest.TestCase):
    def test_t_zero_returns_the_first_color(self):
        self.assertEqual(lerp_rgb((10, 20, 30), (0, 0, 0), 0.0), (10, 20, 30))

    def test_t_one_returns_the_second_color(self):
        self.assertEqual(lerp_rgb((10, 20, 30), (0, 0, 0), 1.0), (0, 0, 0))

    def test_midpoint_averages_the_two_colors(self):
        self.assertEqual(lerp_rgb((0, 0, 0), (10, 20, 30), 0.5), (5, 10, 15))

    def test_out_of_range_t_is_clamped(self):
        self.assertEqual(lerp_rgb((0, 0, 0), (10, 10, 10), -5.0), (0, 0, 0))
        self.assertEqual(lerp_rgb((0, 0, 0), (10, 10, 10), 5.0), (10, 10, 10))


class TerraTrafficLightColorTests(unittest.TestCase):
    def test_zero_terra_is_pure_red(self):
        self.assertEqual(terra_traffic_light_color(0, 8), TERRA_RED)

    def test_midpoint_count_is_pure_yellow(self):
        self.assertEqual(terra_traffic_light_color(4, 8), TERRA_YELLOW)

    def test_max_count_is_pure_green(self):
        self.assertEqual(terra_traffic_light_color(8, 8), TERRA_GREEN)

    def test_default_max_terra_is_eight(self):
        self.assertEqual(DEFAULT_MAX_TERRA, 8)
        self.assertEqual(terra_traffic_light_color(8), TERRA_GREEN)

    def test_every_count_up_to_max_gets_a_visibly_distinct_shade(self):
        # The whole point of the gradient: "2" and "8" must no longer look
        # the same, which a flat 0/1/2+ tier split used to collapse.
        colors = [terra_traffic_light_color(n, 8) for n in range(9)]
        self.assertEqual(len(colors), len(set(colors)))

    def test_count_above_max_clamps_to_the_max_color(self):
        self.assertEqual(
            terra_traffic_light_color(20, 8), terra_traffic_light_color(8, 8))

    def test_negative_count_clamps_to_red(self):
        self.assertEqual(terra_traffic_light_color(-3, 8), TERRA_RED)

    def test_zero_max_terra_does_not_divide_by_zero(self):
        self.assertEqual(terra_traffic_light_color(0, 0), TERRA_RED)
        self.assertEqual(terra_traffic_light_color(5, 0), TERRA_RED)

    def test_gradient_is_monotonic_toward_green(self):
        # Higher terra counts should never read as a "worse" (more red)
        # color than a lower count.
        def greenness(rgb):
            r, g, b = rgb
            return g - r

        counts = list(range(9))
        greens = [greenness(terra_traffic_light_color(n, 8)) for n in counts]
        self.assertEqual(greens, sorted(greens))


if __name__ == "__main__":
    unittest.main()

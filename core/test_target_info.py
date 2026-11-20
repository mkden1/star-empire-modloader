"""target_info.py: the planet orbit figures the target panel shows."""
import math
import unittest

import target_info

from target_info import (
    format_orbit_period,
    planet_orbit_period_seconds,
    planet_orbit_progress_pct,
)


class PlanetOrbitDisplayTests(unittest.TestCase):
    def test_period_uses_full_circle_over_angular_speed(self):
        self.assertAlmostEqual(
            planet_orbit_period_seconds(math.pi / 100), 200.0)

    def test_period_is_none_for_a_stationary_body(self):
        self.assertIsNone(planet_orbit_period_seconds(0.0))

    def test_period_ignores_sign_of_orbit_speed(self):
        self.assertAlmostEqual(
            planet_orbit_period_seconds(-math.pi / 100), 200.0)

    def test_format_period_picks_the_coarsest_useful_units(self):
        self.assertEqual(format_orbit_period(45.0), "45s")
        self.assertEqual(format_orbit_period(125.0), "2m 05s")
        self.assertEqual(format_orbit_period(3723.0), "1h 02m")

    def test_format_period_reports_stationary_bodies(self):
        self.assertEqual(format_orbit_period(None), "Stationary")

    def test_progress_pct_at_key_points_of_the_lap(self):
        self.assertAlmostEqual(planet_orbit_progress_pct(0.0), 0.0)
        self.assertAlmostEqual(planet_orbit_progress_pct(math.pi), 50.0)
        self.assertAlmostEqual(
            planet_orbit_progress_pct(1.5 * math.pi), 75.0)

    def test_progress_pct_wraps_an_unwrapped_angle(self):
        self.assertAlmostEqual(
            planet_orbit_progress_pct(2.0 * math.pi + math.pi / 2), 25.0)

    def test_the_client_uses_these_and_the_games_target_display_is_untouched(self):
        import modloader
        self.assertIn("from target_info import (format_orbit_period", modloader.game_source("Client.py"))
        self.assertNotIn("orbit_period", modloader.game_source("target_display.py"))
        self.assertEqual(modloader.missing_globals(__import__("target_info")), set())


class OrbitFiguresTests(unittest.TestCase):
    """planet_orbit_figures / gate_sun_figures: what the target panel's planet and gate branches show (they were blocks of
    patch text inside the dev's run loop)."""

    def test_a_planets_figures(self):
        import math
        period, angle, pct, radius, bearing = target_info.planet_orbit_figures(
            {"id": 7, "orbit_speed": 0.01, "orbit_radius": 500}, {7: math.pi / 2})
        self.assertEqual(period, target_info.planet_orbit_period_seconds(0.01))
        self.assertEqual((angle, radius), (math.pi / 2, 500.0))
        self.assertAlmostEqual(pct, 25.0)
        self.assertAlmostEqual(bearing, 90.0)

    def test_a_body_with_no_known_angle_or_fields_is_at_zero(self):
        period, angle, pct, radius, bearing = target_info.planet_orbit_figures({}, {})
        self.assertEqual((angle, pct, radius, bearing), (0.0, 0.0, 0.0, 0.0))

    def test_a_moons_radius_is_from_its_parent_the_same_field(self):
        self.assertEqual(target_info.planet_orbit_figures({"id": 2, "is_moon": True, "orbit_radius": "12.5"}, {})[3], 12.5)

    def test_a_gates_distance_and_bearing_from_the_sun(self):
        dist, bearing = target_info.gate_sun_figures({"sun": {"x": 100.0, "y": 100.0}}, 100.0, 300.0)
        self.assertEqual((dist, bearing), (200.0, 90.0))
        dist, bearing = target_info.gate_sun_figures({"sun": {"x": 0.0, "y": 0.0}}, -50.0, 0.0)
        self.assertEqual((dist, bearing), (50.0, 180.0))

    def test_an_unknown_layout_puts_the_sun_at_the_origin(self):
        for layout in (None, {}, {"sun": {}}):
            with self.subTest(layout=layout):
                self.assertEqual(target_info.gate_sun_figures(layout, 3.0, 4.0)[0], 5.0)

    def test_the_target_panel_in_the_loop_calls_them_instead_of_carrying_the_maths(self):
        import modloader
        source = modloader.game_source("Client.py")
        self.assertEqual(source.count("_planet_orbit_figures("), 1)
        self.assertEqual(source.count("_gate_sun_figures(_gt_layout, _gx, _gy)"), 1)
        self.assertNotIn("math.degrees(_pangle)", source)
        self.assertNotIn("_gsun_x", source)


if __name__ == "__main__":
    unittest.main()

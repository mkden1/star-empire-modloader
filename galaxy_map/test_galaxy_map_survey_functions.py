"""The galaxy map's Survey readouts that used to be inline blocks of patch text in render_mixin.py: the traffic-light node
fill, and the scanned / selected rings. (The system info popup's scanned counts and resource rows are gone: since v0.5.86
the game's own DETAILS button opens a System Details window with them, and the popup is a single ORBITAL BODIES row.)"""
import unittest

import galaxy_map_render as gmr
import modloader
from render_mixin import RenderMixin


class Window:
    """A stand-in for the game window with the scan state the readouts read."""
    _s = staticmethod(lambda v: int(v * 2))
    _gm_resource_totals = RenderMixin._gm_resource_totals

    def __init__(self, planets=None, moons=None, resources=None, selected=()):
        self._scanned_by_system = planets or {}
        self._scanned_moons_by_system = moons or {}
        self._scanned_resources_by_system = resources or {}
        self._gm_survey_selected = set(selected)


class Rings:
    def __init__(self):
        self.rings = []

    def add_ring(self, *args):
        self.rings.append(args)


class TerraFillTests(unittest.TestCase):
    def test_the_fill_follows_the_terra_count_on_the_surveys_gradient(self):
        for terra in (0, 1, 3, gmr._SURVEY_TERRA_MAX, 99):
            with self.subTest(terra=terra):
                self.assertEqual(gmr.terra_fill({"planet_types": {"Terra": terra}}),
                                 gmr._terra_color(terra, gmr._SURVEY_TERRA_MAX))

    def test_a_system_with_no_planet_types_is_the_zero_colour(self):
        zero = gmr._terra_color(0, gmr._SURVEY_TERRA_MAX)
        for entry in ({}, {"planet_types": None}, {"planet_types": {}}, {"planet_types": {"Terra": None}}):
            with self.subTest(entry=entry):
                self.assertEqual(gmr.terra_fill(entry), zero)


class SurveyRingsTests(unittest.TestCase):
    def rings(self, window, entry, sname="Sol", base_r=14.0, zoom=1.0):
        out = Rings()
        gmr.survey_rings(window, out, entry, sname, 100, 200, base_r, zoom)
        return out.rings

    def test_a_fully_scanned_system_gets_a_bright_ring_just_outside_the_ownership_band(self):
        w = Window({"Sol": {"a": "Terra", "b": "Arid"}})
        (ring,) = self.rings(w, {"planet_types": {"Terra": 1, "Arid": 1}})
        # x, y, radius (base + ring width 3 + 2*2 px), colour, alpha, width
        self.assertEqual(ring, (100, 200, 14.0 + 3 + 4, gmr._GM_SUCCESS, 255, 3))

    def test_a_partly_scanned_system_gets_the_dim_ring(self):
        w = Window({"Sol": {"a": "Terra"}})
        (ring,) = self.rings(w, {"planet_types": {"Terra": 1, "Arid": 1}})
        self.assertEqual(ring[3], gmr._GM_SURVEY_UNSCANNED)

    def test_a_system_with_no_known_planets_is_never_fully_scanned(self):
        (ring,) = self.rings(Window(), {"planet_types": {}})
        self.assertEqual(ring[3], gmr._GM_SURVEY_UNSCANNED)

    def test_the_ring_width_follows_the_zoom_but_never_below_two(self):
        self.assertEqual(self.rings(Window(), {}, zoom=0.1)[0][5], 2)
        self.assertEqual(self.rings(Window(), {}, zoom=3.0)[0][5], 9)

    def test_a_selected_system_also_gets_the_highlight_ring_one_further_out(self):
        w = Window({"Sol": {"a": "Terra"}}, selected=("Sol",))
        scanned, highlight = self.rings(w, {"planet_types": {"Terra": 1}})
        self.assertEqual(highlight, (100, 200, 14.0 + 3 * 2 + 8, gmr._GM_ACCENT, 255, 4))
        self.assertGreater(highlight[2], scanned[2])
        self.assertEqual(len(self.rings(w, {"planet_types": {"Terra": 1}}, sname="Vega")), 1)


class RenderMixinWiringTests(unittest.TestCase):
    def test_the_patched_render_mixin_calls_them_and_no_longer_carries_the_blocks(self):
        source = modloader.game_source("render_mixin.py")
        for call in ("galaxy_map_render.terra_fill(visited[sname])",
                     "galaxy_map_render.survey_rings(self, _mn, visited.get(sname, {}), sname, inx, iny,"):
            with self.subTest(call):
                self.assertEqual(source.count(call), 1)
        for gone in ("def _gm_resource_totals", "_sv_full", "_scanned_type_counts: dict = {}", "_gm_terra_color("):
            with self.subTest(gone):
                self.assertNotIn(gone, source)

    def test_the_resource_totals_method_is_the_modules_on_the_games_class(self):
        self.assertIs(RenderMixin.__dict__["_gm_resource_totals"], gmr.RenderMethods.__dict__["_gm_resource_totals"])


if __name__ == "__main__":
    unittest.main()

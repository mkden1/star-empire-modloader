"""Tests for RenderMixin._gm_resource_totals -- the shared aggregation
behind both the single-system info popup's RESOURCES section and the
Survey view's Ctrl-click multi-select comparison overlay.

A scan reports extractor slots PER STATION; a planet can hold 3 stations and a
moon 1, so each body contributes its slots x that (scan_logger.stations_for_body).
"""

from __future__ import annotations

import unittest

from render_mixin import RenderMixin


class GmResourceTotalsTests(unittest.TestCase):
    @staticmethod
    def _owner(scanned_resources_by_system=None, scanned_moons_by_system=None):
        owner = RenderMixin.__new__(RenderMixin)
        owner._scanned_resources_by_system = scanned_resources_by_system or {}
        owner._scanned_moons_by_system = scanned_moons_by_system or {}
        return owner

    def test_sums_across_every_body_in_one_system(self):
        owner = self._owner({
            "Sys": {
                "Alpha": {"metal_ore": 10, "gold": 2},
                "Beta": {"metal_ore": 6},
            },
        })

        # two planets, 3 stations each
        self.assertEqual(
            owner._gm_resource_totals(("Sys",)),
            {"metal_ore": 48, "gold": 6})

    def test_a_planet_counts_three_stations_and_a_moon_one(self):
        owner = self._owner(
            {"Sys": {"Planet": {"metal_ore": 10}, "Moon": {"metal_ore": 4}}},
            {"Sys": {"Moon"}})

        self.assertEqual(
            owner._gm_resource_totals(("Sys",)), {"metal_ore": 10 * 3 + 4 * 1})

    def test_promethium_slots_are_counted_like_any_other_resource(self):
        owner = self._owner({"Sys": {"Giant": {"promethium": 4}}})

        self.assertEqual(owner._gm_resource_totals(("Sys",)), {"promethium": 12})

    def test_a_moon_is_recognised_per_system_not_by_name_alone(self):
        owner = self._owner(
            {"Sys A": {"Rock": {"copper": 2}}, "Sys B": {"Rock": {"copper": 2}}},
            {"Sys B": {"Rock"}})        # only Sys B's Rock is a moon

        self.assertEqual(owner._gm_resource_totals(("Sys A",)), {"copper": 6})
        self.assertEqual(owner._gm_resource_totals(("Sys B",)), {"copper": 2})

    def test_sums_across_multiple_selected_systems(self):
        owner = self._owner({
            "Sys A": {"Alpha": {"metal_ore": 10}},
            "Sys B": {"Beta": {"metal_ore": 6, "silicon": 3}},
        })

        self.assertEqual(
            owner._gm_resource_totals(("Sys A", "Sys B")),
            {"metal_ore": 48, "silicon": 9})

    def test_unselected_systems_do_not_contribute(self):
        owner = self._owner({
            "Sys A": {"Alpha": {"metal_ore": 10}},
            "Sys B": {"Beta": {"metal_ore": 999}},
        })

        self.assertEqual(
            owner._gm_resource_totals(("Sys A",)), {"metal_ore": 30})

    def test_unknown_or_unscanned_system_contributes_nothing(self):
        owner = self._owner({})

        self.assertEqual(owner._gm_resource_totals(("Nowhere",)), {})

    def test_empty_system_selection_returns_empty(self):
        owner = self._owner({"Sys": {"Alpha": {"metal_ore": 10}}})

        self.assertEqual(owner._gm_resource_totals(()), {})

    def test_zero_slot_entries_are_dropped_not_summed_as_zero(self):
        owner = self._owner({
            "Sys": {"Alpha": {"copper": 0, "metal_ore": 5}},
        })

        self.assertEqual(
            owner._gm_resource_totals(("Sys",)), {"metal_ore": 15})

    def test_an_owner_that_never_tracked_moons_treats_every_body_as_a_planet(self):
        owner = RenderMixin.__new__(RenderMixin)
        owner._scanned_resources_by_system = {"Sys": {"Alpha": {"oil": 2}}}

        self.assertEqual(owner._gm_resource_totals(("Sys",)), {"oil": 6})


if __name__ == "__main__":
    unittest.main()

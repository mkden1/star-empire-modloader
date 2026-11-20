"""inventory_filter without the Ship Layouts mod: the LOADOUT section and loadout matching are simply absent, nothing fails.

Mods are picked one by one; the filter's loadout feature needs saved layouts, but its sort / type / rarity filtering does
not, so a missing ``ship_layouts_window`` must not break it."""
import sys
import unittest
from unittest import mock

import inventory_filter as inf
import inventory_filter_model as m


def without_layouts():
    """``import ship_layouts_window`` raises ImportError, as when the mod's folder is not there."""
    return mock.patch.dict(sys.modules, {"ship_layouts_window": None})


class WithoutShipLayoutsTests(unittest.TestCase):
    def test_there_is_no_store_to_ask(self):
        with without_layouts():
            self.assertIsNone(inf._layouts())

    def test_a_loadout_filter_matches_nothing_instead_of_raising(self):
        spec = m.FilterSpec().with_loadout("some-layout-id")
        with without_layouts():
            self.assertIsNone(inf.wanted_for(object(), spec))

    def test_the_loadout_section_draws_nothing_and_takes_no_height(self):
        with without_layouts():
            y = inf._draw_loadout(object(), None, None, m.FilterSpec(), 10, 123, 200, [], (0, 0))
        self.assertEqual(y, 123)

    def test_the_open_ship_layouts_button_does_nothing(self):
        with without_layouts():
            inf._act(mock.Mock(), ("layouts",))

    def test_with_the_mod_present_there_is_a_store(self):
        self.assertIsNotNone(inf._layouts())


if __name__ == "__main__":
    unittest.main()

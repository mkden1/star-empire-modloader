"""The station Storage tab, drawn for real, with the FILTER button and filter hooks in render_mixin.py (the three
``# MOD`` lines): the button sits right of the search box, the rows go through the filter, and a stale hook fails here.

The window is a real SolarSystemWindow with exactly the state the Storage tab's draw reads. If an update makes the
draw read something new, the AttributeError names it: add it below."""
import threading
import types
import unittest
from collections import OrderedDict
from unittest import mock

import pygame

for _name, _fallback in (("soundfile", types.SimpleNamespace(read=None)),):
    try:
        __import__(_name)
    except ModuleNotFoundError:
        import sys
        sys.modules[_name] = _fallback

import inventory_filter as inf
import inventory_filter_model as m
import ship_layouts_window as slw
from Client import SolarSystemWindow
from gl_ui_renderer import install_draw_dispatch
from gui_window_manager import GuiWindowManager
from render_mixin import RenderFrame
from test_inventory_filter_model import LAYOUT

CARGO = [
    {"item_key": "item:weapon:blaze_cannon", "item_type": "blaze_cannon", "display_name": "Blaze Cannon",
     "rarity_color": "epic", "quantity": 1, "cargo_size": 10, "row_id": 1},
    {"item_key": "item:shield:aegis_bank_mk1", "item_type": "aegis_bank_mk1", "display_name": "Aegis Bank Mk I",
     "rarity_color": "rare", "quantity": 4, "cargo_size": 10, "row_id": 2},
    {"item_key": "item:energy:star_heart", "item_type": "star_heart", "display_name": "Star Heart",
     "rarity_color": "legendary", "quantity": 1, "cargo_size": 10, "row_id": 3},
    {"item_key": "metal", "item_type": "metal", "display_name": "Metal", "cargo_size": 900, "row_id": 4},
]


def storage_window():
    window = SolarSystemWindow.__new__(SolarSystemWindow)
    window._lock = threading.RLock()
    window._uk = 1.0
    window._username = "Owner"
    window._elapsed = 0.0
    window._label_cache = OrderedDict()
    window._hud_plate_cache = {}
    fonts = {}

    def font(size, bold=False):
        if (size, bold) not in fonts:
            fonts[(size, bold)] = pygame.font.Font(None, size)
            fonts[(size, bold)].set_bold(bold)
        return fonts[(size, bold)]

    window._F = window._inventory_font = window._instrument_font = font
    window._station_overlay = {"kind": "player_station", "x": 300, "y": 80,
                               "data": {"attached_station_id": 17, "owner_name": "Owner", "is_owner": True,
                                        "station_name": "Storage Test"}}
    window._deployed_stations = [{"id": 17, "shields": 100, "max_shields": 1000, "energy": 500,
                                  "max_energy": 1000}]
    window._ps_tab = "Storage"
    window._ps_equipped_module_counts = {"aegis_bank_mk1": 1}
    window._ps_equipped_module_types = []
    window._ps_station_credits = 100
    window._ps_station_cargo = list(CARGO)
    window._ps_station_plugins = []
    window._ps_station_plugin_slots = 0
    window._ps_workers_used = 0
    window._ps_resource_caps = {}
    window._ps_resource_reserves = {}
    window._ps_storage_search = ""
    window._ps_storage_search_focus = False
    window._ps_storage_scroll = 0
    window._ps_storage_version = 1
    window._ps_storage_rows_cache_key = None
    window._ps_storage_rows_cache = None
    window._ps_storage_filter_cache_key = None
    window._ps_storage_filter_cache = None
    window._ps_hull_space = 1000
    window._ps_hull_used = 100
    window._psx_selected_keys = set()
    window._ps_cargo_popup = None
    window._gui_windows = GuiWindowManager()
    window._npc_market_watch_sent = None          # the dev's NPC market watch (v0.5.80) reads it every draw
    window._stat_bar_cache = {}
    return window


class StorageRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        install_draw_dispatch()

    def setUp(self):
        inf.reset_for_tests()
        for patcher in (mock.patch.object(slw.STORE, "layouts", (LAYOUT,)), mock.patch.object(slw.STORE, "refresh"),
                        mock.patch.object(slw.STORE, "loaded", True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.window = storage_window()
        inf._ensure(self.window)
        self.screen = pygame.Surface((1280, 800), pygame.SRCALPHA)
        self.context = RenderFrame(self.screen, 1280, 800, lambda *args: args, 1.0, {}, 1, 1, 1 / 90,
                                   screen_above=self.screen)

    def draw(self):
        self.screen.fill((10, 14, 22, 255))
        self.window._draw_station_overlay(self.context)

    def shown(self):
        return [row.get("item_type") or row.get("_kind") for row in self.window._psx_display_rows]

    def test_the_button_sits_right_of_the_search_box_and_inside_the_overlay(self):
        self.draw()
        button = self.window._if_buttons[inf.STORAGE]
        search = self.window._ps_storage_search_rect
        self.assertGreaterEqual(button.left, search.right)
        self.assertEqual(button.top, search.top)
        self.assertEqual(inf.owner_rect(self.window, inf.STORAGE), pygame.Rect(300, 80, 650, 600))
        self.assertTrue(inf.owner_rect(self.window, inf.STORAGE).contains(button))

    def test_every_row_shows_until_a_filter_is_set(self):
        self.draw()
        self.assertEqual(sorted(self.shown()), ["aegis_bank_mk1", "aegis_bank_mk1", "blaze_cannon", "metal",
                                                "star_heart"])

    def test_a_type_filter_narrows_the_real_list_and_clearing_restores_it(self):
        inf.set_spec(self.window, inf.STORAGE, m.FilterSpec().toggle_type("shield"))
        self.draw()
        self.assertEqual(self.shown(), ["aegis_bank_mk1", "aegis_bank_mk1"])
        inf.set_spec(self.window, inf.STORAGE, m.FilterSpec())
        self.draw()
        self.assertEqual(len(self.shown()), 5)

    def test_a_loadout_lists_just_its_parts_and_the_search_box_still_narrows_further(self):
        inf.set_spec(self.window, inf.STORAGE, m.FilterSpec().with_loadout("123-hunter"))
        self.draw()
        self.assertEqual(sorted(self.shown()), ["blaze_cannon", "star_heart"])
        self.window._ps_storage_search = "star"
        self.draw()
        self.assertEqual(self.shown(), ["star_heart"])

    def test_the_tab_opens_beside_the_overlay_and_a_click_on_the_button_toggles_it(self):
        self.draw()
        button = self.window._if_buttons[inf.STORAGE]
        press = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=button.center, button=1)
        self.assertEqual(inf.filter_events(self.window, [press]), [])
        self.assertEqual(self.window._if_owner, inf.STORAGE)
        self.draw()
        inf.draw_tab(self.window, self.screen)
        self.assertEqual(self.window._if_rect.left, 300 + 650 + 2)

    def test_sorting_by_quantity_puts_the_metal_first_in_the_real_list(self):
        inf.set_spec(self.window, inf.STORAGE, m.FilterSpec().pick_sort(m.SORT_QUANTITY))
        self.draw()
        self.assertEqual(self.shown()[0], "metal")

    def test_the_off_switch_restores_the_vanilla_tab(self):
        with mock.patch.dict("os.environ", {inf.ENV_OFF: "1"}):
            inf.set_spec(self.window, inf.STORAGE, m.FilterSpec().toggle_type("shield"))
            self.draw()
            self.assertEqual(len(self.shown()), 5)
            self.assertNotIn(inf.STORAGE, self.window._if_buttons)
            self.assertEqual(self.window._ps_storage_search_rect.width, 650 - 40)


if __name__ == "__main__":
    unittest.main()

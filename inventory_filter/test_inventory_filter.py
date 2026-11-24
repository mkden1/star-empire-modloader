"""The FILTER buttons, their tab and the hooks that make the ship inventories and Storage use them
(inventory_filter.py), on the real SolarSystemWindow class with just the state these paths read."""
import os
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import pygame

import hangar_inventory
import inventory_filter as inf
import inventory_filter_model as m
import ship_layouts_window as slw
from gui_window_manager import GuiWindowManager
from ship_inventory_rows import build_ship_inventory_rows
from test_inventory_filter_model import ITEMS, LAYOUT, STORAGE

SCREEN = (1280, 800)
PLAYER_RECT = pygame.Rect(40, 60, 340, 520)
SHIP_RECT = pygame.Rect(420, 60, 340, 520)


def setUpModule():
    pygame.init()
    pygame.display.set_mode(SCREEN)


def hits(win, kind):
    return [(rect, action) for rect, action, _scrolls in win._if_hits if action[0] == kind]


def rows_of(items=None):
    return build_ship_inventory_rows(items or ITEMS, dim=(90, 90, 90), cargo_used=300, cargo_capacity=900)[0]


def texts(view):
    return [row[1].replace("●", "").replace("○", "").strip() for row in view.rows if not row[0].startswith("hdr_")]


class _Window:
    """A real SolarSystemWindow, built without its init: only what the filter paths read."""

    @staticmethod
    def make():
        import Client
        win = Client.SolarSystemWindow.__new__(Client.SolarSystemWindow)
        win._lock = threading.RLock()
        win._uk = 1.0
        win._s = lambda value: int(value)
        fonts = {}
        win._instrument_font = lambda size, bold=False: fonts.setdefault(
            (size, bold), pygame.font.SysFont("consolas", size, bold=bold))
        win._gui_windows = GuiWindowManager()
        win._font_cache, win._label_cache, win._fill_surf_cache, win._hud_plate_cache = {}, {}, {}, {}
        win._hangar_open = True
        win._current_station_id = "station-a"
        win._panels = {"inventory": {"x": 40, "y": 60, "minimized": False, "scroll": 0, "hidden": False}}
        win._panel_header_rects, win._panel_toggle_rects = {}, {}
        win._panel_full_rects = {"inventory": pygame.Rect(PLAYER_RECT)}
        win._hiv_init_state()
        win._inv_selected_keys = set()
        win._inv_collapsed_sections = set()
        win._inv_open_groups = set()   # rank groups opened by the player (v0.5.89.1: every ship window shares it)
        win._init_inventory_search()
        win._inv_popup = None
        win._inv_last_click_key = None
        win._inv_drag_pending = win._inv_drag_active = False
        win._inv_rows_cache = rows_of()
        win._inv_display_cache_key = "stale"
        win._inv_display_cache_source = win._inv_rows_cache
        win._inv_display_click_map_cache = {}
        win._ps_storage_scroll = 0
        win._ps_cargo_popup = None
        win._psx_selected_keys = set()
        inf._ensure(win)
        return win

    @staticmethod
    def with_ship(win, items=None, ship_id=7):
        win._hinv_apply_payload({"open": True, "ship_id": ship_id, "ship_index": -1, "station_id": "station-a",
                                 "display_name": "Atlas", "items": items or ITEMS})
        sw = win._ship_windows[f"ship_inventory:{ship_id}"]
        win._panel_full_rects[sw.panel_id] = pygame.Rect(SHIP_RECT)
        win._panels[sw.panel_id]["minimized"] = False
        return sw


class Base(unittest.TestCase):
    def setUp(self):
        import Client
        self.Client = Client
        inf.reset_for_tests()
        self.layouts = mock.patch.object(slw.STORE, "layouts", (LAYOUT,))
        self.refresh = mock.patch.object(slw.STORE, "refresh")
        self.loaded = mock.patch.object(slw.STORE, "loaded", True)
        for patcher in (self.layouts, self.refresh, self.loaded):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.win = _Window.make()
        self.screen = pygame.Surface(SCREEN)

    def player_view(self, query="", collapsed=()):
        return self.Client.ship_inventory_view(self.win._inv_rows_cache, query, set(collapsed))

    def ship_view(self, sw):
        return self.win._hinv_view(sw)

    def draw(self):
        inf.draw_tab(self.win, self.screen)

    def press(self, rect_or_pos, button=1, kind=pygame.MOUSEBUTTONDOWN):
        pos = rect_or_pos.center if isinstance(rect_or_pos, pygame.Rect) else rect_or_pos
        return pygame.event.Event(kind, pos=pos, button=button)

    def route(self):
        """What the frame loop does: queue the tab so the window manager knows where it is."""
        self.win._gui_windows.begin_frame() if hasattr(self.win._gui_windows, "begin_frame") else None
        inf.queue(self.win, SimpleNamespace(screen=self.screen))


class HookTests(Base):
    def test_the_hooks_are_installed_on_the_real_window(self):
        import Client
        self.assertTrue(Client.SolarSystemWindow.__dict__.get("_inventory_filter_installed"))
        self.assertTrue(hasattr(Client.ship_inventory_view, "__wrapped__"))
        self.assertTrue(hasattr(hangar_inventory.ship_inventory_view, "__wrapped__"))
        self.assertTrue(hasattr(Client.SolarSystemWindow._draw_inv_search_bar, "__wrapped__"))
        self.assertTrue(hasattr(Client.SolarSystemWindow._draw_hinv_search_bar, "__wrapped__"))
        self.assertTrue(hasattr(Client.SolarSystemWindow._inv_search_layer_key, "__wrapped__"))

    def test_install_twice_changes_nothing(self):
        import Client
        self.assertFalse(inf.install(Client.SolarSystemWindow, None, Client.__dict__))

    def test_the_view_wrapper_passes_on_arguments_it_does_not_know(self):
        """v0.5.89.1 gave the view a fourth argument (open_groups); a wrapper naming only three crashed the client on login."""
        seen = []

        def original(rows, query, collapsed, open_groups=frozenset(), **named):
            seen.append((open_groups, named))
            return "view"

        wrapped = inf._view_wrapper(original)
        self.assertEqual(wrapped([], "", set(), frozenset({"g"})), "view")
        self.assertEqual(wrapped([], "", set(), open_groups=frozenset({"g"}), later=1), "view")
        self.assertEqual(seen, [(frozenset({"g"}), {}), (frozenset({"g"}), {"later": 1})])

    def test_the_real_view_takes_the_games_own_call_shape(self):
        """The two real callers (Client's frame loop, hangar_inventory) pass the open rank groups as a fourth argument."""
        rows = self.win._inv_rows_cache
        view = self.Client.ship_inventory_view(rows, "", set(), frozenset())
        self.assertEqual(len(view.rows), len(rows))
        view = hangar_inventory.ship_inventory_view(rows, "", set(), frozenset())
        self.assertEqual(len(view.rows), len(rows))

    def test_with_nothing_set_the_view_is_the_games_own(self):
        view = self.player_view()
        self.assertEqual(len(view.rows), len(self.win._inv_rows_cache))

    def test_the_layer_key_changes_with_the_filter_and_the_open_tab(self):
        key = self.win._inv_search_layer_key()
        inf.set_spec(self.win, inf.PLAYER, m.FilterSpec().toggle_type("weapon"))
        filtered = self.win._inv_search_layer_key()
        self.assertNotEqual(key, filtered)
        inf.open_tab(self.win, inf.PLAYER)
        self.assertNotEqual(filtered, self.win._inv_search_layer_key())


class PlayerInventoryTests(Base):
    def test_a_filter_narrows_your_own_inventory_and_not_other_lists(self):
        inf.set_spec(self.win, inf.PLAYER, m.FilterSpec().toggle_type("weapon"))
        self.assertEqual(texts(self.player_view()), ["Blaze Cannon", "Blaze Cannon", "Wisplight Laser x4"])
        # Rows that are not the player's list are never touched.
        other = rows_of()
        self.assertEqual(len(self.Client.ship_inventory_view(other, "", set()).rows), len(other))

    def test_the_text_search_works_on_what_the_filter_left(self):
        inf.set_spec(self.win, inf.PLAYER, m.FilterSpec().toggle_type("weapon"))
        self.assertEqual(texts(self.player_view("wisp")), ["Wisplight Laser x4"])
        self.assertEqual(texts(self.player_view("aegis")), [])

    def test_folded_sections_still_fold(self):
        inf.set_spec(self.win, inf.PLAYER, m.FilterSpec().toggle_type("weapon"))
        view = self.player_view(collapsed={"hdr_cargo"})
        self.assertEqual(texts(view), ["Blaze Cannon"])

    def test_changing_the_filter_drops_the_cached_view_and_what_was_picked(self):
        self.win._inv_selected_keys.add("cargo_item_0")
        self.win._panels["inventory"]["scroll"] = 90
        inf.set_spec(self.win, inf.PLAYER, m.FilterSpec().toggle_rarity("epic"))
        self.assertIsNone(self.win._inv_display_cache_key)
        self.assertEqual(self.win._inv_selected_keys, set())
        self.assertEqual(self.win._panels["inventory"]["scroll"], 0)

    def test_a_loadout_lists_the_parts_of_a_saved_layout(self):
        inf.set_spec(self.win, inf.PLAYER, m.FilterSpec().with_loadout("123-hunter"))
        self.assertEqual(sorted(texts(self.player_view())), sorted(
            ["Blaze Cannon", "Blaze Cannon", "Wisplight Laser x4", "Star Heart", "Speed Plugin", "Firing Plugin"]))

    def test_a_loadout_whose_layout_is_gone_shows_nothing(self):
        inf.set_spec(self.win, inf.PLAYER, m.FilterSpec().with_loadout("deleted"))
        self.assertEqual(texts(self.player_view()), [])

    def test_clearing_returns_the_whole_inventory(self):
        inf.set_spec(self.win, inf.PLAYER, m.FilterSpec().toggle_type("weapon"))
        inf.set_spec(self.win, inf.PLAYER, m.FilterSpec())
        self.assertEqual(len(self.player_view().rows), len(self.win._inv_rows_cache))
        self.assertNotIn(inf.PLAYER, inf._specs)


class ShipWindowTests(Base):
    def test_a_stored_ships_window_filters_on_its_own(self):
        sw = _Window.with_ship(self.win)
        inf.set_spec(self.win, sw.panel_id, m.FilterSpec().toggle_type("shield"))
        self.assertEqual(sorted(texts(self.ship_view(sw))), ["Aegis Bank Mk I x2", "Shield: Aegis Bank Mk I"])
        # Your own inventory and a second ship are untouched.
        self.assertEqual(len(self.player_view().rows), len(self.win._inv_rows_cache))
        other = _Window.with_ship(self.win, ship_id=8)
        self.assertEqual(len(self.ship_view(other).rows), len(self.win._hinv_model(other)[1]))

    def test_the_view_is_rebuilt_when_the_filter_changes(self):
        sw = _Window.with_ship(self.win)
        before = self.ship_view(sw)
        inf.set_spec(self.win, sw.panel_id, m.FilterSpec().toggle_rarity("legendary"))
        after = self.ship_view(sw)
        self.assertIsNot(before, after)
        self.assertEqual(sorted(texts(after)), ["Power: Transcendence Core", "Star Heart"])
        self.assertEqual(set(sw.row_keys), {row[0] for row in after.rows})

    def test_a_filter_drops_that_windows_picked_rows(self):
        sw = _Window.with_ship(self.win)
        sw.selected_keys = {"cargo_item_1"}
        inf.set_spec(self.win, sw.panel_id, m.FilterSpec().toggle_type("weapon"))
        self.assertEqual(sw.selected_keys, set())


class FooterButtonTests(Base):
    def draw_footer(self, owner=inf.PLAYER, **kwargs):
        rect = pygame.Rect(PLAYER_RECT.x, PLAYER_RECT.bottom - 26, PLAYER_RECT.w, 26)
        self.win._panels["inventory"]["_footer_rect"] = rect
        zones = []
        if owner == inf.PLAYER:
            self.win._draw_inv_search_bar(self.screen, rect, zones.append, (0, 0), **kwargs)
        else:
            sw = self.win._ship_windows[owner]
            self.win._draw_hinv_search_bar(sw, self.screen, pygame.Rect(SHIP_RECT.x, SHIP_RECT.bottom - 26,
                                                                        SHIP_RECT.w, 26), zones.append, (0, 0), **kwargs)
        return rect, zones

    def test_the_games_fade_is_accepted_and_only_the_buttons_label_stays_solid(self):
        # v0.5.88: the game hands its footer draw `fade` (the inventory's Edit UI opacity), and the extra ship windows pass
        # it on EVERY call, even as None. A wrapper without the keyword raised TypeError there every frame.
        class Fade:
            entered = 0

            def solid(self):
                fade = self

                class Context:
                    def __enter__(self):
                        fade.entered += 1

                    def __exit__(self, *exc):
                        return False
                return Context()

        sw = _Window.with_ship(self.win)
        for owner in (inf.PLAYER, sw.panel_id):
            with self.subTest(owner=owner):
                self.draw_footer(owner, fade=None)
                fade = Fade()
                self.draw_footer(owner, fade=fade)
                self.assertGreaterEqual(fade.entered, 2)         # the game's search text and our label
                self.assertFalse(inf._failed)

    def test_the_footer_keeps_its_search_field_and_gains_a_button(self):
        rect, zones = self.draw_footer()
        button = self.win._if_buttons[inf.PLAYER]
        self.assertTrue(rect.contains(button))
        self.assertGreater(button.w, 40)
        field, _ = inf.split_footer(self.win, rect)
        self.assertLessEqual(field.right, button.left)
        self.assertIn(button, zones)                     # the layer repaints when the pointer enters it

    def test_a_stored_ships_footer_gets_one_too(self):
        sw = _Window.with_ship(self.win)
        self.draw_footer(sw.panel_id)
        self.assertIn(sw.panel_id, self.win._if_buttons)

    def test_a_narrow_footer_shrinks_the_button_instead_of_the_search_field_to_nothing(self):
        field, button = inf.split_footer(self.win, pygame.Rect(0, 0, 90, 26))
        self.assertGreaterEqual(button.w, 20)
        self.assertGreater(field.w, 40)

    def test_the_button_shows_how_many_things_are_set(self):
        inf.set_spec(self.win, inf.PLAYER,
                     m.FilterSpec().toggle_type("weapon").toggle_rarity("epic").pick_sort(m.SORT_NAME))
        self.assertEqual(inf.spec_for(inf.PLAYER).count(), 3)
        self.draw_footer()                               # draws without error with a badge

    def test_an_error_in_the_button_draw_switches_the_feature_off_and_draws_the_plain_bar(self):
        with mock.patch.object(inf, "draw_button", side_effect=RuntimeError("boom")):
            with self.assertLogs("inventory_filter", level="ERROR"):
                self.draw_footer()
        self.assertTrue(inf._failed)
        self.assertEqual(len(self.player_view().rows), len(self.win._inv_rows_cache))


class TabTests(Base):
    def open(self, owner=inf.PLAYER):
        inf.open_tab(self.win, owner)
        self.draw()

    def test_it_docks_beside_its_window_right_first_then_left(self):
        self.open()
        rect = inf.tab_rect(self.win, *SCREEN)
        self.assertEqual((rect.left, rect.top), (PLAYER_RECT.right + 2, PLAYER_RECT.top))
        self.win._panel_full_rects["inventory"] = pygame.Rect(SCREEN[0] - 340, 60, 340, 520)
        rect = inf.tab_rect(self.win, *SCREEN)
        self.assertEqual(rect.right, SCREEN[0] - 340 - 2)

    def test_it_closes_when_its_window_folds_or_goes(self):
        self.open()
        self.win._panels["inventory"]["minimized"] = True
        events = [self.press((5, 5))]
        self.assertEqual(inf.filter_events(self.win, events), events)
        self.assertIsNone(self.win._if_owner)

    def test_every_control_is_there_for_a_ship_with_gear_of_all_kinds(self):
        self.open()
        self.assertEqual([a[1] for _r, a in hits(self.win, "sort")], ["name", "type", "rarity", "quantity"])
        self.assertEqual([a[1] for _r, a in hits(self.win, "source")], ["all", "fitted", "hold"])
        types = {a[1] for _r, a in hits(self.win, "type")}
        self.assertTrue({"weapon", "energy", "shield", "resource", "ship_plugin"} <= types)
        rarities = [a[1] for _r, a in hits(self.win, "rarity")]
        self.assertEqual(rarities, ["legendary", "epic", "rare", "uncommon", "common"])      # best first
        self.assertEqual([a[1] for _r, a in hits(self.win, "loadout")], ["123-hunter"])
        self.assertTrue(hits(self.win, "close") and hits(self.win, "select") and hits(self.win, "clear"))

    def click(self, kind, value=None):
        for rect, action in hits(self.win, kind):
            if value is None or action[1] == value:
                inf._click(self.win, rect.center)
                self.draw()
                return
        self.fail(f"no {kind} {value} button")

    def test_clicking_a_type_chip_narrows_the_list_and_again_widens_it(self):
        self.open()
        self.click("type", "weapon")
        self.assertEqual(inf.spec_for(inf.PLAYER).types, {"weapon"})
        self.assertEqual(texts(self.player_view()), ["Blaze Cannon", "Blaze Cannon", "Wisplight Laser x4"])
        self.click("type", "weapon")
        self.assertNotIn(inf.PLAYER, inf._specs)

    def test_a_ticked_type_with_nothing_left_stays_listed_so_it_can_be_unticked(self):
        self.open()
        self.click("type", "weapon")
        inf.set_spec(self.win, inf.PLAYER, inf.spec_for(inf.PLAYER).toggle_type("sensor"))
        self.draw()
        self.assertIn("sensor", {a[1] for _r, a in hits(self.win, "type")})

    def test_sort_buttons_step_through_both_directions_and_off(self):
        self.open()
        self.click("sort", "rarity")
        self.assertEqual((inf.spec_for(inf.PLAYER).sort, inf.spec_for(inf.PLAYER).reverse), ("rarity", False))
        self.click("sort", "rarity")
        self.assertTrue(inf.spec_for(inf.PLAYER).reverse)
        self.click("sort", "rarity")
        self.assertNotIn(inf.PLAYER, inf._specs)

    def test_show_buttons(self):
        self.open()
        self.click("source", "hold")
        self.assertEqual(inf.spec_for(inf.PLAYER).source, "hold")
        self.assertEqual(len(texts(self.player_view())), 7)
        self.click("source", "all")
        self.assertNotIn(inf.PLAYER, inf._specs)

    def test_picking_a_layout_filters_to_it_and_lists_its_parts_with_what_is_here(self):
        self.open()
        self.click("loadout", "123-hunter")
        self.assertEqual(inf.spec_for(inf.PLAYER).loadout, "123-hunter")
        progress = {need.item_type: (hold, fitted) for need, hold, fitted in
                    m.loadout_progress(inf.owner_items(self.win, inf.PLAYER), inf.wanted_for(
                        self.win, inf.spec_for(inf.PLAYER)))}
        self.assertEqual(progress["blaze_cannon"], (1, 1))
        self.assertEqual(progress["wasp"], (0, 0))
        self.click("loadout", "123-hunter")                 # again: off
        self.assertNotIn(inf.PLAYER, inf._specs)

    def test_clear_all_resets_everything(self):
        self.open()
        self.click("type", "weapon")
        self.click("sort", "name")
        self.click("clear")
        self.assertNotIn(inf.PLAYER, inf._specs)

    def test_select_shown_picks_the_hold_rows_on_screen(self):
        inf.set_spec(self.win, inf.PLAYER, m.FilterSpec().toggle_type("weapon"))
        self.win._inv_display_click_map_cache = self.player_view().click_map
        self.open()
        self.click("select")
        shown = {key: item for key, item in self.player_view().click_map.items() if isinstance(item, dict)
                 and item.get("item_class") == "cargo_item"}
        self.assertEqual(self.win._inv_selected_keys, set(shown))
        self.assertEqual(len(shown), 2)                     # the carried Blaze Cannon and Wisplight Laser
        self.assertIn("Picked 2 rows", self.win._if_status[0])

    def test_select_shown_on_a_stored_ship_picks_that_windows_rows(self):
        sw = _Window.with_ship(self.win)
        inf.set_spec(self.win, sw.panel_id, m.FilterSpec().with_loadout("123-hunter"))
        self.ship_view(sw)
        self.open(sw.panel_id)
        self.click("select")
        # Blaze Cannon, Wisplight Laser, Star Heart and Speed Plugin: the fitted parts are not hold rows.
        self.assertEqual(len(sw.selected_keys), 4)
        self.assertTrue(all(key.startswith("cargo_item") for key in sw.selected_keys))

    def test_nothing_to_pick_says_so(self):
        inf.set_spec(self.win, inf.PLAYER, m.FilterSpec().toggle_type("sensor"))
        self.win._inv_display_click_map_cache = self.player_view().click_map
        self.open()
        self.click("select")
        self.assertIn("Nothing in the hold", self.win._if_status[0])

    def test_open_ship_layouts_when_there_are_none_saved(self):
        with mock.patch.object(slw.STORE, "layouts", ()):
            self.open()
            self.assertEqual(hits(self.win, "loadout"), [])
            with mock.patch.object(slw, "open_window") as opened:
                self.click("layouts")
            opened.assert_called_once_with(self.win)

    def test_the_tab_scrolls_when_the_content_is_taller_than_it(self):
        many = tuple(dict(LAYOUT, id=f"id-{i}", name=f"layout {i}") for i in range(30))
        with mock.patch.object(slw.STORE, "layouts", many):
            self.open()
            self.assertGreater(self.win._if_max_scroll, 0)
            self.win._if_scroll = 10 ** 6
            self.draw()
            self.assertEqual(self.win._if_scroll, self.win._if_max_scroll)

    def test_the_tab_draws_for_a_ship_and_for_a_loadout_with_every_state_of_part(self):
        sw = _Window.with_ship(self.win)
        inf.set_spec(self.win, sw.panel_id, m.FilterSpec().with_loadout("123-hunter").toggle_rarity("epic")
                     .pick_sort(m.SORT_RARITY))
        self.open(sw.panel_id)
        self.assertGreater(len(self.win._if_hits), 10)


class EventTests(Base):
    def setUp(self):
        super().setUp()
        self.win._panels["inventory"]["_footer_rect"] = pygame.Rect(PLAYER_RECT.x, PLAYER_RECT.bottom - 26,
                                                                    PLAYER_RECT.w, 26)
        self.win._draw_inv_search_bar(self.screen, self.win._panels["inventory"]["_footer_rect"],
                                      lambda rect: None, (0, 0))
        self.button = self.win._if_buttons[inf.PLAYER]

    def test_a_click_on_the_button_opens_the_tab_and_never_reaches_the_game(self):
        event = self.press(self.button)
        self.assertEqual(inf.filter_events(self.win, [event]), [])
        self.assertEqual(self.win._if_owner, inf.PLAYER)
        inf.filter_events(self.win, [self.press(self.button)])
        self.assertIsNone(self.win._if_owner)                 # a second click folds it

    def test_other_clicks_and_keys_pass_through(self):
        events = [self.press((5, 5)), pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a, mod=0, unicode="a"),
                  pygame.event.Event(pygame.MOUSEMOTION, pos=self.button.center, rel=(0, 0), buttons=(0, 0, 0))]
        self.assertEqual(inf.filter_events(self.win, events), events)

    def test_a_right_click_on_the_button_clears_the_filters(self):
        inf.set_spec(self.win, inf.PLAYER, m.FilterSpec().toggle_type("weapon"))
        self.assertEqual(inf.filter_events(self.win, [self.press(self.button, button=3)]), [])
        self.assertNotIn(inf.PLAYER, inf._specs)
        self.assertIsNone(self.win._if_owner)

    def test_a_button_left_behind_by_a_folded_window_is_dead(self):
        self.win._panels["inventory"]["minimized"] = True
        events = [self.press(self.button)]
        self.assertEqual(inf.filter_events(self.win, events), events)
        self.assertIsNone(self.win._if_owner)

    def test_a_button_that_the_window_moved_away_from_is_dead(self):
        self.win._panel_full_rects["inventory"] = pygame.Rect(600, 300, 340, 400)
        events = [self.press(self.button)]
        self.assertEqual(inf.filter_events(self.win, events), events)

    def test_a_window_in_front_takes_the_click(self):
        manager = self.win._gui_windows
        manager.register("panel:inventory", PLAYER_RECT, layer=200, rect_getter=lambda: PLAYER_RECT,
                         visible=lambda: True)
        manager.register("other", pygame.Rect(0, 0, 1280, 800), layer=300, rect_getter=lambda: pygame.Rect(
            0, 0, 1280, 800), visible=lambda: True)
        events = [self.press(self.button)]
        self.assertEqual(inf.filter_events(self.win, events), events)

    def test_clicks_in_the_tab_are_its_own_and_the_wheel_scrolls_it(self):
        inf.open_tab(self.win, inf.PLAYER)
        self.draw()
        self.route()
        rect = self.win._if_rect
        sort_rect = hits(self.win, "sort")[0][0]
        self.assertEqual(inf.filter_events(self.win, [self.press(sort_rect)]), [])
        self.assertEqual(inf.spec_for(inf.PLAYER).sort, "name")
        self.assertEqual(inf.filter_events(self.win, [self.press(sort_rect, kind=pygame.MOUSEBUTTONUP)]), [])
        outside = (rect.right + 50, rect.centery)
        events = [self.press(outside)]
        self.assertEqual(inf.filter_events(self.win, events), events)

    def test_the_close_button_closes_the_tab(self):
        inf.open_tab(self.win, inf.PLAYER)
        self.draw()
        self.route()
        close = hits(self.win, "close")[0][0]
        inf.filter_events(self.win, [self.press(close)])
        self.assertIsNone(self.win._if_owner)

    def test_a_broken_handler_gives_the_events_back_and_switches_off(self):
        events = [self.press(self.button)]
        with mock.patch.object(inf, "_handle", side_effect=RuntimeError("boom")):
            with self.assertLogs("inventory_filter", level="ERROR"):
                self.assertEqual(inf.filter_events(self.win, events), events)
        self.assertTrue(inf._failed)
        self.assertEqual(inf.filter_events(self.win, events), events)

    def test_the_off_switch(self):
        with mock.patch.dict(os.environ, {inf.ENV_OFF: "1"}):
            events = [self.press(self.button)]
            self.assertEqual(inf.filter_events(self.win, events), events)
            inf.set_spec(self.win, inf.PLAYER, m.FilterSpec().toggle_type("weapon"))
            self.assertEqual(len(self.player_view().rows), len(self.win._inv_rows_cache))


class StorageTests(Base):
    def test_storage_rows_pass_through_untouched_until_a_filter_is_set(self):
        self.assertIs(inf.storage_rows(self.win, STORAGE), STORAGE)

    def test_storage_rows_are_filtered_and_the_answer_is_cached(self):
        inf.set_spec(self.win, inf.STORAGE, m.FilterSpec().toggle_type("shield"))
        first = inf.storage_rows(self.win, STORAGE)
        self.assertEqual([r["item_type"] for r in first], ["aegis_bank_mk1"] * 2)
        self.assertIs(inf.storage_rows(self.win, STORAGE), first)
        inf.set_spec(self.win, inf.STORAGE, m.FilterSpec().toggle_type("weapon"))
        self.assertEqual([r["item_type"] for r in inf.storage_rows(self.win, STORAGE)], ["blaze_cannon"])

    def test_storage_loadout(self):
        inf.set_spec(self.win, inf.STORAGE, m.FilterSpec().with_loadout("123-hunter"))
        self.assertEqual([r["item_type"] for r in inf.storage_rows(self.win, STORAGE)],
                         ["blaze_cannon", "star_heart"])

    def test_the_storage_button_is_only_live_while_its_tab_is_drawn(self):
        self.win._station_overlay = {"kind": "player_station"}
        self.win._ps_tab = "Storage"
        self.win._ps_storage_rows_cache = STORAGE
        overlay = pygame.Rect(300, 100, 650, 600)
        search = pygame.Rect(overlay.x + 20, overlay.y + 120, overlay.w - 40 - inf.storage_button_width(self.win), 24)
        inf.draw_storage_button(self.win, self.screen, search, overlay)
        button = self.win._if_buttons[inf.STORAGE]
        self.assertFalse(search.colliderect(button))
        self.assertEqual(inf.owner_rect(self.win, inf.STORAGE), overlay)
        self.assertEqual(inf.filter_events(self.win, [self.press(button)]), [])
        self.assertEqual(self.win._if_owner, inf.STORAGE)
        self.win._ps_tab = "Construction"                      # another tab: no button, no tab
        self.assertIsNone(inf.owner_rect(self.win, inf.STORAGE))

    def test_select_shown_in_storage_picks_the_stored_rows(self):
        self.win._psx_display_rows = STORAGE
        self.win._psx_row_key = lambda row: f"{row.get('item_key')}:{row.get('_display_equipped')}"
        self.assertEqual(inf.select_shown(self.win, inf.STORAGE), 4)
        self.assertNotIn("item:shield:aegis_bank_mk1:True", self.win._psx_selected_keys)

    def test_the_tab_for_storage_uses_storage_words(self):
        self.win._station_overlay = {"kind": "player_station"}
        self.win._ps_tab = "Storage"
        self.win._ps_storage_rows_cache = STORAGE
        inf.draw_storage_button(self.win, self.screen, pygame.Rect(320, 220, 400, 24), pygame.Rect(300, 100, 650, 600))
        inf.open_tab(self.win, inf.STORAGE)
        self.draw()
        labels = [a[1] for _r, a in hits(self.win, "source")]
        self.assertEqual(labels, ["all", "fitted", "hold"])
        self.assertEqual(inf.owner_title(self.win, inf.STORAGE), "STATION STORAGE")


if __name__ == "__main__":
    unittest.main()

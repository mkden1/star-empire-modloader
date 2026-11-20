"""construction_tooltip.py: what a selected Construction Bay blueprint costs against what the station holds.

The arithmetic is pinned on the real recorded blueprint (Station Expander Alpha), the text on the game's own
blueprint-popup conventions, and the hook against the game's REAL popup drawing (_draw_cb_side_popup, shared with
its selected-project detail popup): the blueprint popup gets the stock and a verdict, the project popup is left
exactly as the game draws it, and nothing the game keeps (the click state) is touched."""
import os
import threading
import unittest
from collections import OrderedDict
from types import SimpleNamespace
from unittest import mock

import pygame

import construction_tooltip as ct

PALETTE = ct._palette(None)


class _Fixture(unittest.TestCase):
    def setUp(self):
        ct.reset_for_tests()
        self.addCleanup(ct.reset_for_tests)
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(ct.ENV_OFF, None)


class AmountTests(unittest.TestCase):
    def test_amounts_are_shown_rounded_up_with_thousands_separators(self):
        self.assertEqual(ct._amount(1_460_483.3), "1,460,484")
        self.assertEqual(ct._amount(3.49), "4")
        self.assertEqual(ct._amount(0.2), "<1")
        self.assertEqual(ct._amount(1000.0), "1,000")
        self.assertEqual(ct._amount(584_193.0000000001), "584,193")


SEP = ct._SEPARATOR


def alpha():
    """The Station Expander Alpha blueprint exactly as the server sent it in the recorded session."""
    return {"id": "station_expander_alpha", "display_name": "Station Expander Alpha", "manhours": 10,
            "cost_credits": 1000, "cost_metal": 50, "ingredients": [], "uses": -1, "uses_remaining": -1,
            "description": "A prefabricated hull extension module. Increases station cargo capacity.",
            "resource_costs": [{"amount": 50, "display_name": "Metal", "resource": "metal", "timing": "periodic"}]}


def beam_cannon(uses=5):
    """A made-up blueprint with every kind of cost: an initial resource, an item, credits, periodic resources."""
    return {"id": "beam_cannon", "display_name": "Beam Cannon", "manhours": 400, "cost_credits": 25_000, "uses": uses,
            "uses_remaining": uses,
            "ingredients": [{"item_type": "power_core", "display_name": "Power Core", "amount": 2}],
            "resource_costs": [
                {"resource": "silicon", "display_name": "Silicon", "amount": 300, "timing": "initial"},
                {"resource": "metal", "display_name": "Metal", "amount": 2_000, "timing": "periodic"},
                {"resource": "titanium", "display_name": "Titanium", "amount": 120, "timing": "periodic"}]}


RICH = {"metal": 18_171.0, "titanium": 500.0, "silicon": 987_459.0}


class BlueprintCostTests(unittest.TestCase):
    def test_credits_come_first_and_the_timing_splits_the_rest(self):
        initial, periodic = ct.cost_groups(beam_cannon())
        self.assertEqual(initial, [("resource", "silicon", "Silicon", 300.0), ("item", "power_core", "Power Core", 2.0)])
        self.assertEqual(periodic, [("credits", "credits", "Credits", 25_000.0), ("resource", "metal", "Metal", 2_000.0),
                                    ("resource", "titanium", "Titanium", 120.0)])

    def test_an_older_server_has_only_the_fixed_cost_fields_in_the_games_order(self):
        initial, periodic = ct.cost_groups({"cost_oil": 3, "cost_credits": 10, "cost_metal": 7, "cost_rations": 1})
        self.assertEqual(initial, [])
        self.assertEqual([entry[1] for entry in periodic], ["credits", "metal", "oil", "rations"])

    def test_an_empty_resource_list_is_an_answer_not_a_missing_one(self):
        _initial, periodic = ct.cost_groups({"cost_metal": 7, "resource_costs": []})
        self.assertEqual(periodic, [])

    def test_nothing_that_costs_nothing_is_listed_and_odd_data_is_survived(self):
        bp = {"cost_credits": 0, "resource_costs": [
            {"resource": "gold", "amount": 0, "timing": "periodic"}, {"resource": "oil", "amount": -4},
            {"resource": "wood", "amount": "many"}, {"amount": 5}, "junk", None,
            {"resource": "copper", "amount": 2.5, "timing": "initial"}],
            "ingredients": [None, "x", {"item_type": "gear", "amount": 0}]}
        initial, periodic = ct.cost_groups(bp)
        self.assertEqual(periodic, [])
        self.assertEqual(initial, [("resource", "copper", "Copper", 2.5)])
        self.assertEqual(ct.cost_groups({"resource_costs": "oops", "ingredients": 5}), ([], []))

    def test_a_cost_without_a_resource_key_is_listed_by_name_but_has_no_stock_to_compare(self):
        initial, periodic = ct.cost_groups({"resource_costs": [{"display_name": "Mystery", "amount": 4}]})
        self.assertEqual(periodic, [("resource", "", "Mystery", 4.0)])
        self.assertIsNone(ct._held(periodic[0], {"": 9.0}, 5, {}))

    def test_labels_fall_back_to_a_readable_name_and_an_ingredient_defaults_to_one(self):
        initial, periodic = ct.cost_groups({"resource_costs": [{"resource": "space_corn", "amount": 3}],
                                            "ingredients": [{"item_type": "power_core"}]})
        self.assertEqual(periodic, [("resource", "space_corn", "Space Corn", 3.0)])
        self.assertEqual(initial, [("item", "power_core", "Power Core", 1.0)])


class ItemStockTests(unittest.TestCase):
    def test_the_quantities_of_one_type_add_up_and_equipped_modules_are_not_available(self):
        cargo = [{"item_key": "item:station_plugin:solar:1", "item_type": "solar", "quantity": 3},
                 {"item_key": "item:station_plugin:solar:2", "item_type": "solar", "quantity": 2},
                 {"item_key": "item:component:power_core:9", "item_type": "power_core", "quantity": 4},
                 {"item_category": "resource", "item_key": "metal", "cargo_size": 99}]
        self.assertEqual(ct.items_from_cargo(cargo, {"solar": 2}), {"solar": 3.0, "power_core": 4.0})
        self.assertEqual(ct.items_from_cargo(cargo), {"solar": 5.0, "power_core": 4.0})

    def test_equipped_cannot_make_it_negative(self):
        cargo = [{"item_key": "item:a:b:1", "item_type": "b", "quantity": 1}]
        self.assertEqual(ct.items_from_cargo(cargo, {"b": 7}), {"b": 0.0})

    def test_a_resource_row_is_not_an_item_even_with_an_item_type(self):
        cargo = [{"item_category": "resource", "item_key": "metal", "item_type": "metal", "cargo_size": 99}]
        self.assertEqual(ct.items_from_cargo(cargo), {})

    def test_a_negative_quantity_does_not_take_items_away(self):
        cargo = [{"item_key": "item:a:c:1", "item_type": "c", "quantity": -5},
                 {"item_key": "item:a:c:2", "item_type": "c", "quantity": 2}]
        self.assertEqual(ct.items_from_cargo(cargo), {"c": 2.0})

    def test_the_type_can_come_from_the_key_and_a_row_without_quantity_is_one(self):
        self.assertEqual(ct.items_from_cargo([{"item_key": "item:component:power_core:9"}]), {"power_core": 1.0})

    def test_unknown_cargo_is_none_not_empty(self):
        self.assertIsNone(ct.items_from_cargo(None))
        self.assertEqual(ct.items_from_cargo([]), {})
        self.assertEqual(ct.items_from_cargo([5, None, {"item_key": 3}]), {})

    def test_what_is_held_is_never_guessed(self):
        credits = ("credits", "credits", "Credits", 5.0)
        resource = ("resource", "metal", "Metal", 5.0)
        item = ("item", "power_core", "Power Core", 1.0)
        self.assertIsNone(ct._held(credits, {}, None, {}))
        self.assertEqual(ct._held(credits, None, 12, None), 12.0)
        self.assertIsNone(ct._held(resource, None, 1, {}))
        self.assertEqual(ct._held(resource, {}, 1, {}), 0.0)                    # known cargo, no row: none
        self.assertEqual(ct._held(resource, {"metal": 7.0}, 1, {}), 7.0)
        self.assertIsNone(ct._held(item, {}, 1, None))
        self.assertEqual(ct._held(item, {}, 1, {}), 0.0)
        self.assertEqual(ct._held(item, {}, 1, {"power_core": 3.0}), 3.0)
        self.assertIsNone(ct._held(("item", "", "?", 1.0), {}, 1, {"": 3.0}))


class BlueprintTextTests(unittest.TestCase):
    def lines(self, bp, stock=None, credits=None, items=None):
        return ct.blueprint_lines(bp, stock, credits, items, PALETTE)

    def texts(self, *args, **kwargs):
        return [text for text, _colour in self.lines(*args, **kwargs)]

    def test_the_recorded_blueprint_against_a_station_with_plenty(self):
        texts = self.texts(alpha(), {"metal": 18_171.0}, 122_402_974.0, {})
        self.assertEqual(texts[:9], [
            "Station Expander Alpha", SEP, "  Manhours: 10", SEP, "  PERIODIC COST",
            "  Credits: 1,000   (in stock: 122,402,974)", "  Metal: 50   (in stock: 18,171)", SEP,
            "  Ready: stock covers 363 builds (Metal)"])

    def test_the_description_is_wrapped_where_the_games_popup_wraps_it(self):
        texts = self.texts(alpha(), {"metal": 18_171.0}, 122_402_974.0, {})
        self.assertEqual(texts[9:], [SEP, "  A prefabricated hull extension", "  module. Increases station cargo",
                                     "  capacity."])
        colours = [colour for _text, colour in self.lines(alpha(), {"metal": 18_171.0}, 122_402_974.0, {})[10:]]
        self.assertEqual(colours, [PALETTE["desc"]] * 3)                        # every wrapped line, not just the last

    def test_an_overlong_word_does_not_leave_a_blank_line(self):
        out = self.texts({"display_name": "X", "description": "w" * 40 + " short"})
        self.assertEqual(out, ["X", SEP, "  " + "w" * 40, "  short"])

    def test_the_headings_come_from_the_games_labels(self):
        texts = self.texts(beam_cannon(), RICH, 1e9, {"power_core": 9.0})
        self.assertIn("  INITIAL COST", texts)
        self.assertIn("  PERIODIC COST", texts)
        fake = ct._palette(SimpleNamespace(_INITIAL_COST_LABEL="Up front", _PERIODIC_COST_LABEL="As built"))
        out = [t for t, _ in ct.blueprint_lines(beam_cannon(), RICH, 1e9, {}, fake)]
        self.assertIn("  UP FRONT", out)
        self.assertIn("  AS BUILT", out)

    def test_initial_costs_come_before_periodic_ones_and_an_item_is_written_with_an_x(self):
        texts = self.texts(beam_cannon(), RICH, 1e9, {"power_core": 2.0})
        self.assertEqual(texts[3:12], [
            SEP, "  INITIAL COST", "  Silicon: 300   (in stock: 987,459)", "  Power Core: x2   (in stock: 2)", SEP,
            "  PERIODIC COST", "  Credits: 25,000   (in stock: 1,000,000,000)", "  Metal: 2,000   (in stock: 18,171)",
            "  Titanium: 120   (in stock: 500)"])

    def test_a_missing_item_is_red_and_named_and_nothing_says_ready(self):
        lines = self.lines(beam_cannon(), RICH, 1e9, {"power_core": 1.0})
        colours = {text: colour for text, colour in lines}
        texts = [text for text, _ in lines]
        self.assertEqual(colours["  Power Core: x2   (in stock: 1)"], PALETTE["short"])
        self.assertEqual(colours["  Silicon: 300   (in stock: 987,459)"], PALETTE["body"])
        self.assertEqual(colours["  Missing up front:"], PALETTE["short"])
        self.assertEqual(colours["    Power Core: need 2, have 1"], PALETTE["short"])
        self.assertFalse([t for t in texts if "Ready" in t])
        self.assertNotIn("  Can start, but will wait for:", texts)

    def test_a_short_periodic_resource_is_amber_and_the_build_can_still_start(self):
        lines = self.lines(beam_cannon(), dict(RICH, titanium=80.0), 1e9, {"power_core": 2.0})
        colours = {text: colour for text, colour in lines}
        texts = [text for text, _ in lines]
        self.assertEqual(colours["  Titanium: 120   (in stock: 80)"], PALETTE["warn"])
        self.assertEqual(colours["  Can start, but will wait for:"], PALETTE["warn"])
        self.assertEqual(colours["    Titanium: need 120, have 80"], PALETTE["warn"])
        self.assertEqual(colours["  Metal: 2,000   (in stock: 18,171)"], PALETTE["body"])
        self.assertNotIn("  Missing up front:", texts)
        self.assertFalse([t for t in texts if "Ready" in t])

    def test_a_resource_that_is_not_in_the_cargo_at_all_reads_none(self):
        stock = {"metal": 18_171.0, "silicon": 987_459.0}
        texts = self.texts(beam_cannon(), stock, 1e9, {"power_core": 2.0})
        self.assertIn("  Titanium: 120   (in stock: none)", texts)
        self.assertIn("    Titanium: need 120, have none", texts)

    def test_credits_are_a_cost_like_the_others(self):
        texts = self.texts(beam_cannon(), RICH, 100.0, {"power_core": 2.0})
        self.assertIn("  Credits: 25,000   (in stock: 100)", texts)
        self.assertIn("    Credits: need 25,000, have 100", texts)

    def test_both_kinds_of_shortage_are_listed_up_front_first(self):
        texts = self.texts(beam_cannon(), dict(RICH, titanium=0.0), 1e9, {})
        self.assertLess(texts.index("  Missing up front:"), texts.index("  Can start, but will wait for:"))
        self.assertIn("    Power Core: need 2, have none", texts)
        self.assertIn("    Titanium: need 120, have none", texts)

    def test_with_the_cargo_not_known_no_stock_is_claimed_and_nothing_is_ready(self):
        texts = self.texts(beam_cannon(), None, 5.0, None)
        for text in texts:
            if text.startswith("  ") and ":" in text and "Credits" not in text:
                self.assertNotIn("in stock", text)
        self.assertIn("  Credits: 25,000   (in stock: 5)", texts)                # credits are known on their own
        self.assertFalse([t for t in texts if "Ready" in t])
        self.assertNotIn("  Missing up front:", texts)

    def test_with_the_credits_not_known_nothing_is_ready_either(self):
        texts = self.texts(alpha(), {"metal": 18_171.0}, None, {})
        self.assertIn("  Metal: 50   (in stock: 18,171)", texts)
        self.assertIn("  Credits: 1,000", texts)
        self.assertFalse([t for t in texts if "Ready" in t])

    def test_exactly_enough_is_enough_and_one_short_is_not(self):
        enough = self.texts(alpha(), {"metal": 50.0}, 1000.0, {})
        self.assertIn("  Ready: stock covers 1 build (Credits)", enough)           # singular; the first of a tie
        short = self.texts(alpha(), {"metal": 49.0}, 1000.0, {})
        self.assertIn("    Metal: need 50, have 49", short)
        self.assertFalse([t for t in short if "Ready" in t])

    def test_the_resource_that_runs_out_first_is_named(self):
        texts = self.texts(alpha(), {"metal": 500.0}, 1_000_000.0, {})
        self.assertIn("  Ready: stock covers 10 builds (Metal)", texts)
        texts = self.texts(alpha(), {"metal": 500_000.0}, 3_500.0, {})
        self.assertIn("  Ready: stock covers 3 builds (Credits)", texts)

    def test_a_blueprint_with_few_uses_left_is_limited_by_them(self):
        bp = dict(alpha(), uses=9, uses_remaining=2)
        self.assertIn("  Ready: stock covers 2 builds (blueprint uses)", self.texts(bp, {"metal": 5000.0}, 1e9, {}))
        bp = dict(alpha(), uses=9, uses_remaining=-1)
        self.assertIn("  Ready: stock covers 100 builds (Metal)", self.texts(bp, {"metal": 5000.0}, 1e9, {}))
        bp = dict(alpha(), uses=3)
        del bp["uses_remaining"]
        self.assertIn("  Ready: stock covers 3 builds (blueprint uses)", self.texts(bp, {"metal": 5000.0}, 1e9, {}))

    def test_uses_that_just_match_what_the_stock_covers_do_not_take_the_blame(self):
        bp = dict(alpha(), uses=100, uses_remaining=100)
        self.assertIn("  Ready: stock covers 100 builds (Metal)", self.texts(bp, {"metal": 5000.0}, 1e9, {}))

    def test_a_flag_is_not_a_number_of_uses(self):
        for flag in (True, False):
            bp = dict(alpha(), uses=flag, uses_remaining=flag)
            texts = self.texts(bp, {"metal": 5000.0}, 1e9, {})
            self.assertIn("  Ready: stock covers 100 builds (Metal)", texts)
            self.assertNotIn("  No uses left on this blueprint", texts)

    def test_the_wrap_breaks_exactly_where_the_games_does(self):
        stays = self.texts({"display_name": "X", "description": "a" * 15 + " " + "b" * 15})
        self.assertEqual(stays, ["X", SEP, "  " + "a" * 15 + " " + "b" * 15])           # 16 + 15 + 1 == 32: fits
        breaks = self.texts({"display_name": "X", "description": "a" * 16 + " " + "b" * 15})
        self.assertEqual(breaks, ["X", SEP, "  " + "a" * 16, "  " + "b" * 15])           # 17 + 15 + 1 == 33: breaks

    def test_a_blueprint_with_no_uses_left_says_so(self):
        texts = self.texts(dict(alpha(), uses=0, uses_remaining=0), {"metal": 5000.0}, 1e9, {})
        self.assertIn("  No uses left on this blueprint", texts)
        self.assertFalse([t for t in texts if "Ready" in t])

    def test_a_fraction_of_a_unit_in_stock_is_not_none_and_rounding_error_is_not_a_shortage(self):
        bp = {"display_name": "T", "resource_costs": [{"resource": "metal", "amount": 0.1 + 0.2}]}
        texts = self.texts(bp, {"metal": 0.3}, 0.0, {})
        self.assertIn("  Metal: 0.3   (in stock: <1)", texts)
        self.assertIn("  Ready: stock covers 1 build (Metal)", texts)

    def test_something_free_has_only_a_title_and_a_nameless_one_a_question_mark(self):
        self.assertEqual(self.texts({"display_name": "Free"}, {}, 0.0, {}), ["Free"])
        self.assertEqual(self.texts({}, {}, 0.0, {}), ["?"])

    def test_the_title_and_the_rules_use_the_palettes_colours(self):
        lines = self.lines(alpha(), {"metal": 18_171.0}, 1e9, {})
        self.assertEqual(lines[0][1], PALETTE["title"])
        self.assertEqual(dict(lines)[SEP], PALETTE["rule"])
        self.assertEqual(dict(lines)["  PERIODIC COST"], PALETTE["section"])
        self.assertEqual(dict(lines)["  Ready: stock covers 363 builds (Metal)"], PALETTE["ok"])
        self.assertEqual(dict(lines)["  capacity."], PALETTE["desc"])

    def test_the_palette_takes_the_games_warn_and_success_colours(self):
        palette = ct._palette(SimpleNamespace(_PS_WARN=(1, 2, 3), _PS_SUCCESS=(4, 5, 6)))
        self.assertEqual(palette["short"], (1, 2, 3, 255))
        self.assertEqual(palette["ok"], (4, 5, 6, 255))


class _RealRender(_Fixture):
    """Helpers for rendering the game's own Construction Bay (a real SolarSystemWindow)."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        pygame.init()
        pygame.display.set_mode((1280, 720))

    def window(self, scale=1.0, width=1280):
        import Client
        from Client import SolarSystemWindow
        window = SolarSystemWindow.__new__(SolarSystemWindow)
        window._lock = threading.RLock()
        window._uk = scale
        window._username = "Owner"
        window._elapsed = 0.0
        window._label_cache = OrderedDict()
        window._hud_plate_cache = {}
        window._npc_market_watch_sent = None  # v0.5.70: _draw_station_overlay now syncs the NPC market watch every frame
        fonts = {}

        def font(size, bold=False, *args, **kwargs):
            key = (size, bold)
            if key not in fonts:
                fonts[key] = pygame.font.SysFont("consolas", max(6, round(size * scale)), bold=bold)
            return fonts[key]

        window._F = window._inventory_font = window._instrument_font = font
        queue = []
        window._station_overlay = {"kind": "player_station", "x": 20, "y": 20, "data": {
            "attached_station_id": 2105, "owner_name": "Owner", "is_owner": True, "station_name": "Test Station",
            "management_access": True, "construction_enabled": True, "construction_projects": queue,
            "blueprints": []}}
        window._deployed_stations = [{"id": 2105, "shields": 100, "max_shields": 1000, "energy": 500,
                                      "max_energy": 1000}]
        window._ps_tab = "Construction Bay"
        for name, value in dict(
                _ps_control_scroll=0, _ps_control_sb_dragging=False, _ps_equipped_module_counts={},
                _ps_station_credits=122_402_974, _ps_workers_used=0, _ps_rename_dialog=None, _ps_motd_dialog=None,
                _ps_dock_perms_dialog=None, _ps_management_perms_dialog=None,
                _ps_station_cargo=[{"item_category": "resource", "item_key": "metal", "cargo_size": 18_171}],
                _ps_cb_search="", _ps_cb_search_focus=False, _ps_cb_search_rect=None, _ps_cb_selected_proj=None,
                _ps_cb_cancel_confirm=None, _ps_cb_proj_scroll=0, _ps_cb_bp_scroll=0, _ps_cb_proj_panel_rect=None,
                _ps_cb_bp_panel_rect=None, _ps_cb_proj_sb_dragging=False, _ps_cb_proj_sb_drag_oy=0,
                _ps_cb_proj_sb_track_rect=None, _ps_cb_proj_sb_thumb_rect=None, _ps_cb_proj_sb_thumb_travel=0,
                _ps_cb_proj_sb_max_scroll=0, _ps_cb_bp_sb_dragging=False, _ps_cb_bp_sb_drag_oy=0,
                _ps_cb_bp_sb_track_rect=None, _ps_cb_bp_sb_thumb_rect=None, _ps_cb_bp_sb_thumb_travel=0,
                _ps_cb_bp_sb_max_scroll=0, _ps_cb_proj_rects=[], _ps_cb_bp_rects=[], _ps_cb_pause_rect=None,
                _ps_cb_cancel_rect=None, _ps_cb_bp_popup=None, _ps_cb_ctx_menu=None).items():
            setattr(window, name, value)
        return window

    def render(self, window, width=1280):
        from render_mixin import RenderFrame
        screen = pygame.Surface((width, 720), pygame.SRCALPHA)
        screen.fill((6, 10, 20, 255))
        window._draw_station_overlay(RenderFrame(screen, width, 720, lambda *a: a, 1.0, {}, 1, 1, 1 / 60))
        return screen

    def changed_box(self, a, b):
        left = top = 10 ** 6
        right = bottom = -1
        for y in range(a.get_height()):
            for x in range(a.get_width()):
                if a.get_at((x, y)) != b.get_at((x, y)):
                    left, right = min(left, x), max(right, x)
                    top, bottom = min(top, y), max(bottom, y)
        return None if right < 0 else pygame.Rect(left, top, right - left + 1, bottom - top + 1)


class PopupHookTests(_Fixture):
    """The wrapper around the game's _draw_cb_side_popup, on a stand-in class."""

    POPUP = {"idx": 0, "bp": {"id": "station_expander_alpha", "display_name": "Station Expander Alpha"}, "y": 300}

    def make_class(self):
        calls = []

        class Window:
            def __init__(self, popup=None, tab="Construction Bay", kind="player_station", rects=None):
                self._ps_cb_bp_popup = popup
                self._ps_tab = tab
                self._station_overlay = {"kind": kind}
                self._ps_cb_bp_rects = rects or []
                self._ps_station_cargo = [{"item_category": "resource", "item_key": "metal", "cargo_size": 18_171}]
                self._ps_station_credits = 122_402_974.0
                self._ps_equipped_module_counts = {}

            def _draw_cb_side_popup(self, screen, lines, anchor_y, ov_rect, win_w, title_font, body_font):
                calls.append((screen, lines, anchor_y, ov_rect, win_w, title_font, body_font))
                return "drawn"

        return Window, calls

    def game_lines(self, title="Station Expander Alpha"):
        return [(title, (1, 2, 3, 255)), ("------------------", (4, 5, 6, 255)), ("  Manhours: 10", (7, 8, 9, 255))]

    def call(self, window, lines=None, anchor=300):
        lines = lines if lines is not None else self.game_lines()
        return window._draw_cb_side_popup("screen", lines, anchor, "rect", 1280, "title font", "body font")

    def test_install_wraps_once_and_the_original_gets_every_other_argument_and_gives_the_result(self):
        cls, calls = self.make_class()
        self.assertTrue(ct.install(cls))
        self.assertFalse(ct.install(cls))
        self.assertTrue(hasattr(cls._draw_cb_side_popup, "__wrapped__"))
        self.assertEqual(self.call(cls(self.POPUP)), "drawn")
        self.assertEqual(len(calls), 1)
        screen, _lines, _anchor, ov_rect, win_w, title_font, body_font = calls[0]
        self.assertEqual((screen, ov_rect, win_w, title_font, body_font), ("screen", "rect", 1280, "title font", "body font"))

    def test_the_blueprint_popup_gets_our_lines_and_the_blueprints_row(self):
        cls, calls = self.make_class()
        ct.install(cls)
        live = dict(alpha(), uses_remaining=-1)
        window = cls(self.POPUP, rects=[(4, pygame.Rect(32, 330, 600, 24), live)])
        self.call(window)
        lines, anchor = calls[0][1], calls[0][2]
        self.assertEqual(anchor, 330)                                              # the row as it is now
        texts = [text for text, _colour in lines]
        self.assertEqual(texts[0], "Station Expander Alpha")
        self.assertIn("  Metal: 50   (in stock: 18,171)", texts)
        self.assertIn("  Credits: 1,000   (in stock: 122,402,974)", texts)
        self.assertIn("  Ready: stock covers 363 builds (Metal)", texts)

    def test_another_blueprint_in_the_list_is_not_taken_for_it(self):
        cls, calls = self.make_class()
        ct.install(cls)
        window = cls(dict(self.POPUP, bp=alpha()), rects=[(0, pygame.Rect(32, 330, 600, 24), beam_cannon())])
        self.call(window, anchor=300)
        self.assertEqual(calls[0][2], 300)                                          # not the other blueprint's row
        self.assertIn("  Metal: 50   (in stock: 18,171)", [text for text, _ in calls[0][1]])
        self.assertFalse([text for text, _ in calls[0][1] if "Titanium" in text])

    def test_a_blueprint_without_an_id_keeps_what_it_was_clicked_with(self):
        cls, calls = self.make_class()
        ct.install(cls)
        plain, other = {"display_name": "Plain", "cost_credits": 5}, {"display_name": "Other", "cost_credits": 9}
        window = cls(dict(self.POPUP, bp=plain), rects=[(0, pygame.Rect(32, 330, 600, 24), other)])
        self.call(window, lines=self.game_lines("Plain"), anchor=300)
        self.assertEqual(calls[0][2], 300)
        self.assertIn("  Credits: 5   (in stock: 122,402,974)", [text for text, _ in calls[0][1]])

    def test_the_stock_of_items_is_the_cargo_less_the_equipped_modules(self):
        cls, calls = self.make_class()
        ct.install(cls)
        window = cls(dict(self.POPUP, bp=beam_cannon()))
        window._ps_station_cargo = [{"item_category": "resource", "item_key": "metal", "cargo_size": 18_171},
                                    {"item_key": "item:component:power_core:9", "item_type": "power_core",
                                     "quantity": 3}]
        window._ps_equipped_module_counts = {"power_core": 1}
        self.call(window, lines=self.game_lines("Beam Cannon"))
        self.assertIn("  Power Core: x2   (in stock: 2)", [text for text, _ in calls[0][1]])

    def test_a_blueprint_that_is_not_on_the_list_keeps_the_games_anchor(self):
        cls, calls = self.make_class()
        ct.install(cls)
        window = cls(dict(self.POPUP, bp=dict(alpha())))
        self.call(window, anchor=412)
        self.assertEqual(calls[0][2], 412)
        self.assertIn("  Metal: 50   (in stock: 18,171)", [text for text, _ in calls[0][1]])

    def test_the_live_blueprint_is_what_is_drawn_not_the_one_clicked(self):
        cls, calls = self.make_class()
        ct.install(cls)
        live = dict(alpha(), uses_remaining=0)
        window = cls(dict(self.POPUP, bp=dict(alpha())), rects=[(0, pygame.Rect(32, 250, 600, 24), live)])
        self.call(window)
        self.assertIn("  No uses left on this blueprint", [text for text, _ in calls[0][1]])

    def test_another_popup_passes_through_unchanged(self):
        # the game's selected-project popup (its title is the project's name) while a blueprint popup is not open
        cls, calls = self.make_class()
        ct.install(cls)
        project_lines = self.game_lines("Packaged Oakheart")
        self.call(cls(None), lines=project_lines)
        self.call(cls(self.POPUP), lines=project_lines)                            # a title that is not the blueprint's
        self.assertIs(calls[0][1], project_lines)
        self.assertIs(calls[1][1], project_lines)
        self.assertEqual((calls[0][2], calls[1][2]), (300, 300))
        self.assertFalse(ct._failed)                                                # passing through is not an error

    def test_it_is_not_ours_to_change_on_other_tabs_stations_or_with_a_popup_that_has_no_blueprint(self):
        cases = {"another tab": dict(popup=self.POPUP, tab="Storage"),
                 "an NPC station": dict(popup=self.POPUP, kind="npc"),
                 "a popup with no blueprint": dict(popup={"idx": 0, "y": 5}),
                 "a blueprint that is not a dict": dict(popup={"idx": 0, "bp": None, "y": 5}),
                 "a popup that is not a dict": dict(popup="oops")}
        for name, kwargs in cases.items():
            cls, calls = self.make_class()
            ct.install(cls)
            lines = self.game_lines()
            self.call(cls(**kwargs), lines=lines)
            self.assertIs(calls[0][1], lines, name)
            self.assertFalse(ct._failed, name)                                      # and none of it is an error

    def test_an_empty_or_short_call_passes_through(self):
        cls, calls = self.make_class()
        ct.install(cls)
        window = cls(self.POPUP)
        self.call(window, lines=[])
        self.assertEqual(calls[0][1], [])
        window._draw_cb_side_popup("screen", self.game_lines(), 300, "rect", 1280, "t", "b")      # the game's own call shape
        self.assertEqual(len(calls), 2)
        self.assertFalse(ct._failed)                                                # none of that was an error

    def test_keyword_arguments_reach_the_game_too(self):
        cls, calls = self.make_class()
        ct.install(cls)
        cls(dict(self.POPUP, bp=alpha()))._draw_cb_side_popup("screen", self.game_lines(), 300, "rect", 1280,
                                                              title_font="t", body_font="b")
        self.assertEqual(calls[0][5:], ("t", "b"))
        self.assertIn("  Metal: 50   (in stock: 18,171)", [text for text, _ in calls[0][1]])

    def test_it_never_touches_the_click_state(self):
        cls, _calls = self.make_class()
        ct.install(cls)
        popup = dict(self.POPUP)
        window = cls(popup)
        self.call(window)
        self.assertIs(window._ps_cb_bp_popup, popup)
        self.assertEqual(popup, self.POPUP)

    def test_switched_off_by_the_environment_the_games_lines_are_drawn(self):
        os.environ[ct.ENV_OFF] = "1"
        cls, calls = self.make_class()
        ct.install(cls)
        lines = self.game_lines()
        self.call(cls(self.POPUP), lines=lines)
        self.assertIs(calls[0][1], lines)

    def test_an_error_switches_it_off_and_the_game_still_draws_its_own_lines_once(self):
        cls, calls = self.make_class()
        ct.install(cls)
        lines = self.game_lines()
        with mock.patch.object(ct, "blueprint_lines", side_effect=ValueError("bad")):
            with self.assertLogs(ct.logger, "ERROR"):
                self.assertEqual(self.call(cls(self.POPUP), lines=lines), "drawn")
        self.assertTrue(ct._failed)
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][1], lines)
        self.call(cls(self.POPUP), lines=lines)                                    # and stays off
        self.assertIs(calls[1][1], lines)

    def test_an_error_in_the_games_drawing_is_not_swallowed(self):
        cls, _calls = self.make_class()

        def boom(self, *args):
            raise RuntimeError("frame failed")
        cls._draw_cb_side_popup = boom
        ct.install(cls)
        with self.assertRaises(RuntimeError):
            self.call(cls(self.POPUP))
        self.assertFalse(ct._failed)

    def test_a_game_without_the_popup_drawing_is_left_alone(self):
        class Old:
            pass
        with self.assertLogs(ct.logger, "WARNING"):
            self.assertFalse(ct.install(Old))
        self.assertNotIn("_construction_tooltip_installed", Old.__dict__)


class RealPopupTests(_RealRender):
    """Against the game's own Construction Bay drawing: its blueprint popup gets the stock, its project popup
    (built from the server's estimates) is the game's alone."""

    def project(self, **extra):
        row = {"project_id": 9774, "name": "Packaged Oakheart", "qty": 1, "manhours": 75_000_000,
               "manhours_total": 75_000_000, "manhours_done": 74_561_855, "station_id": 2105, "queue_order": 1,
               "progress": 0.9941, "paused": False, "build_state": "building", "eta_seconds": 840.0,
               "workers_assigned": 12.5, "manhours_left": 438_145.0, "waiting_for": [],
               "remaining_costs": [{"resource": "metal", "display_name": "Metal", "amount": 584_194, "available": 18_171}]}
        row.update(extra)
        return row

    def bay(self, scale=1.0, blueprints=None, select_project=False, popup_index=None):
        import time
        window = self.window(scale)
        window._station_overlay["data"]["construction_projects"] = [self.project()]
        window._station_overlay["data"]["blueprints"] = blueprints or [alpha(), beam_cannon()]
        window._ps_cb_received_mono = time.monotonic()
        window._text_cursors = {}
        with mock.patch.object(ct, "_enabled", return_value=False):
            self.render(window)                                                    # publishes the rows
        if select_project:
            window._ps_cb_selected_proj = 9774
        if popup_index is not None:
            idx, row, bp = window._ps_cb_bp_rects[popup_index]
            window._ps_cb_bp_popup = {"idx": idx, "bp": bp, "y": row.top}
        return window

    def popup_box(self, window):
        """The popup as the game draws it and as drawn with this module on: (the changed box of each)."""
        popup, project = window._ps_cb_bp_popup, window._ps_cb_selected_proj
        window._ps_cb_bp_popup, window._ps_cb_selected_proj = None, None
        with mock.patch.object(ct, "_enabled", return_value=False):
            nothing = self.render(window)
        window._ps_cb_bp_popup, window._ps_cb_selected_proj = popup, project
        with mock.patch.object(ct, "_enabled", return_value=False):
            games = self.render(window)
        ours = self.render(window)
        return nothing, games, ours

    TINY = {"id": "tiny", "display_name": "Tiny", "manhours": 5, "cost_credits": 100, "resource_costs": []}

    def test_the_blueprint_popup_gets_the_stock_in_the_games_own_spot(self):
        window = self.bay(blueprints=[dict(self.TINY), alpha()], popup_index=0)
        nothing, games, ours = self.popup_box(window)
        games_box, our_box = self.changed_box(nothing, games), self.changed_box(nothing, ours)
        self.assertIsNotNone(games_box)
        self.assertEqual((our_box.x, our_box.y), (games_box.x, games_box.y))      # the same placing
        self.assertGreater(our_box.width, games_box.width)                        # "(in stock: ...)" is added
        self.assertGreater(our_box.height, games_box.height)                      # and the verdict
        self.assertFalse(ct._failed)

    def test_the_real_stock_and_credits_end_up_in_the_words(self):
        window = self.bay(popup_index=0)
        captured = []
        real = ct.blueprint_lines

        def spy(*args, **kwargs):
            captured.append(real(*args, **kwargs))
            return captured[-1]
        with mock.patch.object(ct, "blueprint_lines", spy):
            self.render(window)
        texts = [text for text, _colour in captured[-1]]
        self.assertIn("  Credits: 1,000   (in stock: 122,402,974)", texts)
        self.assertIn("  Metal: 50   (in stock: 18,171)", texts)
        self.assertIn("  Ready: stock covers 363 builds (Metal)", texts)

    def test_the_popup_follows_the_blueprints_row_where_the_game_keeps_the_click_position(self):
        window = self.bay(blueprints=[dict(self.TINY), alpha()], popup_index=0)
        window._ps_cb_bp_popup["y"] = 0                                           # as if clicked before the list scrolled
        nothing, games, ours = self.popup_box(window)
        row = window._ps_cb_bp_rects[0][1]
        self.assertEqual(self.changed_box(nothing, ours).y, row.top)              # at the row now
        self.assertNotEqual(self.changed_box(nothing, games).y, row.top)          # the game's popup stayed where it was

    def test_a_tall_popup_stays_inside_the_station_window_like_the_games(self):
        window = self.bay(blueprints=[alpha(), beam_cannon()], popup_index=1)
        nothing, _games, ours = self.popup_box(window)
        box, body = self.changed_box(nothing, ours), window._station_overlay["body_rect"]
        self.assertGreaterEqual(box.y, body.top)
        self.assertLessEqual(box.bottom, body.bottom)

    def test_the_games_project_popup_is_untouched(self):
        window = self.bay(select_project=True)
        nothing, games, ours = self.popup_box(window)
        self.assertIsNotNone(self.changed_box(nothing, games))                    # the game does draw one
        self.assertIsNone(self.changed_box(games, ours))                          # and we change nothing of it

    def test_with_a_blueprint_popup_open_only_that_popup_is_drawn(self):
        window = self.bay(select_project=True, popup_index=0)
        nothing, _games, ours = self.popup_box(window)
        row = window._ps_cb_proj_rects[0][1]
        probe = (window._ps_cb_proj_panel_rect.right + 40, row.top + 10)           # where the project popup would be
        self.assertEqual(ours.get_at(probe), nothing.get_at(probe))
        window._ps_cb_bp_popup = None                                              # sanity: with no blueprint popup it is there
        with_project = self.render(window)
        self.assertNotEqual(with_project.get_at(probe), nothing.get_at(probe))

    def test_it_never_touches_the_click_state(self):
        window = self.bay(select_project=True, popup_index=0)
        popup = window._ps_cb_bp_popup
        self.render(window)
        self.assertIs(window._ps_cb_bp_popup, popup)
        self.assertEqual(window._ps_cb_selected_proj, 9774)

    def test_switched_off_the_games_own_popup_is_back_unchanged(self):
        window = self.bay(popup_index=0)
        with mock.patch.object(ct, "_enabled", return_value=False):
            games = self.render(window)
        os.environ[ct.ENV_OFF] = "1"
        self.assertIsNone(self.changed_box(games, self.render(window)))

    def test_the_real_class_is_hooked_by_importing_the_client(self):
        import Client
        self.assertTrue(Client.SolarSystemWindow.__dict__.get("_construction_tooltip_installed"))
        self.assertTrue(hasattr(Client.SolarSystemWindow._draw_cb_side_popup, "__wrapped__"))


if __name__ == "__main__":
    unittest.main()

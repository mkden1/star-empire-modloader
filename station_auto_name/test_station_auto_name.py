"""The AUTO NAME button on the station rename dialog: the naming rules, and the real dialog drawn and pressed."""

from collections import OrderedDict
import os
import sys
import threading
import types
import unittest
from types import SimpleNamespace
from unittest import mock

import pygame

try:
    import soundfile  # noqa: F401
except ModuleNotFoundError:
    sys.modules["soundfile"] = types.SimpleNamespace(read=None)

import station_auto_name
from station_auto_name import next_name
from text_field import TextField


def named(*names):
    return [{"display_name": name, "attached_station_id": 100 + i} for i, name in enumerate(names)]


class NextNameTests(unittest.TestCase):
    def test_first_station_on_a_planet_is_one(self):
        self.assertEqual("Casashius - 1", next_name("Casashius", []))
        self.assertEqual("Casashius - 1", next_name("Casashius", None))

    def test_follows_the_numbers_already_used(self):
        self.assertEqual("Casashius - 3", next_name("Casashius", named("Casashius - 1", "Casashius - 2")))

    def test_a_gap_is_filled_before_a_new_number_is_started(self):
        self.assertEqual("Casashius - 2", next_name("Casashius", named("Casashius - 1", "Casashius - 3")))
        self.assertEqual("Casashius - 1", next_name("Casashius", named("Casashius - 2", "Casashius - 10")))

    def test_the_station_being_renamed_does_not_count(self):
        stations = named("Casashius - 1", "Casashius - 2", "Casashius - 3")   # ids 100, 101, 102
        self.assertEqual("Casashius - 2", next_name("Casashius", stations, own_station_id=101))
        self.assertEqual("Casashius - 4", next_name("Casashius", stations, own_station_id=999))
        self.assertEqual("Casashius - 4", next_name("Casashius", stations))

    def test_own_station_is_matched_by_text_id_and_by_the_plain_id_key(self):
        stations = [{"display_name": "Casashius - 1", "id": 17}, {"display_name": "Casashius - 2", "id": 18}]
        self.assertEqual("Casashius - 1", next_name("Casashius", stations, own_station_id="17"))
        self.assertEqual("Casashius - 2", next_name("Casashius", stations, own_station_id=18))

    def test_other_planets_and_other_name_shapes_do_not_count(self):
        stations = named("Crylolmarwyn - 1", "Casashius Prime - 1", "Old Casashius - 1", "Casashius - 1b",
                         "Casashius - 1 (old)", "Casashius", "Casashius - ", "Casashius - -1", "222's Station", "")
        self.assertEqual("Casashius - 1", next_name("Casashius", stations))

    def test_spacing_and_case_of_an_existing_name_are_forgiven(self):
        self.assertEqual("Casashius - 4", next_name("Casashius", named("casashius-1", "Casashius  -  2", " Casashius - 3 ")))
        self.assertEqual("CASASHIUS - 3", next_name("Casashius", named("CASASHIUS  -  1", "CASASHIUS-2")))

    def test_new_name_takes_the_spelling_the_existing_ones_use(self):
        self.assertEqual("rylvyron - 3", next_name("Rylvyron", named("rylvyron - 1", "rylvyron - 2")))
        # the commonest spelling wins; a tie goes to the one met first
        self.assertEqual("rylvyron - 4", next_name("Rylvyron", named("rylvyron - 1", "rylvyron - 2", "Rylvyron - 3")))
        self.assertEqual("Rylvyron - 3", next_name("Rylvyron", named("Rylvyron - 1", "rylvyron - 2")))

    def test_a_station_of_another_player_counts(self):
        stations = [{"display_name": "Casashius - 1", "attached_station_id": 5, "owner_name": "someone else"}]
        self.assertEqual("Casashius - 2", next_name("Casashius", stations))

    def test_unknown_planet_gives_nothing(self):
        for planet in (None, "", "   "):
            self.assertIsNone(next_name(planet, named("Casashius - 1")))

    def test_planet_names_are_matched_literally(self):
        self.assertEqual("Z.1 - 1", next_name("Z.1", named("ZX1 - 1")))
        self.assertEqual("Kepler (b) - 2", next_name("Kepler (b)", named("Kepler (b) - 1")))

    def test_junk_rows_are_skipped(self):
        stations = [None, "Casashius - 1", 7, {}, {"display_name": None}, {"display_name": "Casashius - 1"}]
        self.assertEqual("Casashius - 2", next_name("Casashius", stations))

    def test_name_never_outgrows_the_field(self):
        name = next_name("A" * 60, [])
        self.assertEqual(station_auto_name.NAME_MAX, len(name))
        self.assertTrue(name.endswith(" - 1"))
        self.assertEqual("A" * 36 + " - 1", name)
        self.assertEqual("Ab - 12", next_name("Ab", named(*[f"Ab - {n}" for n in range(1, 12)]), max_len=7))
        self.assertIsNone(next_name("Ab", [], max_len=3))

    def test_digits_other_than_ascii_are_not_numbers(self):
        self.assertEqual("Casashius - 1", next_name("Casashius", named("Casashius - ٣")))


def window_with(deployed, *, planet_id=9, planets=None, my_stations=None, kind="player_station", with_overlay=True):
    window = SimpleNamespace()
    window._lock = threading.RLock()
    window._station_overlay = ({"kind": kind, "data": {"attached_station_id": 17, "planet_id": planet_id,
                                                         "station_name": "Old Name"}}
                               if with_overlay else None)
    window._layout = {"planets": planets if planets is not None else [{"id": 9, "name": "Casashius"}]}
    window._my_stations_data = my_stations or []
    window._deployed_stations = deployed
    return window


def dialog_for(rect=None, cursor=None, **extra):
    # v0.5.73: the game's rename dialog holds its name box as a TextField (Client.py opens it with max_length=40)
    dialog = {"station_id": 17, "field": TextField("Old Name", max_length=40, cursor=cursor),
              "confirm_rect": None, "cancel_rect": None, "auto_rect": rect}
    dialog.update(extra)
    return dialog


class SuggestionTests(unittest.TestCase):
    def setUp(self):
        station_auto_name.reset_for_tests()
        self.addCleanup(station_auto_name.reset_for_tests)
        self.deployed = [
            {"display_name": "Old Name", "attached_station_id": 17, "planet_id": 9},
            {"display_name": "Casashius - 1", "attached_station_id": 18, "planet_id": 9},
            {"display_name": "Casashius - 2", "attached_station_id": 19, "planet_id": 9},
            {"display_name": "Crylolmarwyn - 1", "attached_station_id": 20, "planet_id": 10},
        ]

    def test_names_the_next_station_on_the_docked_stations_planet(self):
        self.assertEqual("Casashius - 3", station_auto_name.suggestion(window_with(self.deployed), dialog_for()))

    def test_the_docked_station_does_not_count_even_when_it_already_has_such_a_name(self):
        deployed = [
            {"display_name": "Casashius - 2", "attached_station_id": 17},   # the station being renamed
            {"display_name": "Casashius - 1", "attached_station_id": 18},
            {"display_name": "Casashius - 3", "attached_station_id": 19},
        ]
        self.assertEqual("Casashius - 2", station_auto_name.suggestion(window_with(deployed), dialog_for()))

    def test_a_moon_is_named_for_the_moon(self):
        planets = [{"id": 4, "name": "Zephrinax"}, {"id": 9, "name": "Tote", "is_moon": True, "parent_planet_id": 4}]
        deployed = [{"display_name": "Tote - 1", "attached_station_id": 30}]
        self.assertEqual("Tote - 2", station_auto_name.suggestion(window_with(deployed, planets=planets), dialog_for()))

    def test_the_possessions_list_names_the_body_when_the_layout_cannot(self):
        mine = [{"station_id": 17, "body_name": "Casashius", "body_kind": "planet"}]
        window = window_with(self.deployed, planets=[], my_stations=mine)
        self.assertEqual("Casashius - 3", station_auto_name.suggestion(window, dialog_for()))

    def test_nothing_when_the_planet_is_not_known(self):
        self.assertIsNone(station_auto_name.suggestion(window_with(self.deployed, planet_id=77), dialog_for()))
        self.assertIsNone(station_auto_name.suggestion(window_with(self.deployed, planets=[]), dialog_for()))

    def test_nothing_when_not_docked_at_a_player_station(self):
        self.assertIsNone(station_auto_name.suggestion(window_with(self.deployed, with_overlay=False), dialog_for()))
        self.assertIsNone(station_auto_name.suggestion(window_with(self.deployed, kind="npc_station"), dialog_for()))

    def test_a_failure_switches_the_button_off_and_nothing_else(self):
        window = window_with(self.deployed)
        window._lock = mock.MagicMock()
        window._lock.__enter__.side_effect = RuntimeError("boom")
        with self.assertLogs("station_auto_name", level="ERROR"):
            self.assertIsNone(station_auto_name.suggestion(window, dialog_for()))
        self.assertFalse(station_auto_name.enabled())
        self.assertIsNone(station_auto_name.suggestion(window_with(self.deployed), dialog_for()))   # no second log, no name
        station_auto_name.reset_for_tests()
        self.assertTrue(station_auto_name.enabled())

    def test_kill_switch(self):
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_NO_STATION_AUTO_NAME": "1"}):
            self.assertFalse(station_auto_name.enabled())
            self.assertIsNone(station_auto_name.suggestion(window_with(self.deployed), dialog_for()))
            self.assertFalse(station_auto_name.click(window_with(self.deployed), dialog_for(pygame.Rect(0, 0, 9, 9)), (1, 1)))


class ClickTests(unittest.TestCase):
    def setUp(self):
        station_auto_name.reset_for_tests()
        self.addCleanup(station_auto_name.reset_for_tests)
        self.window = window_with([{"display_name": "Casashius - 1", "attached_station_id": 18}])

    def test_press_on_the_button_fills_the_field_and_puts_the_caret_at_its_end(self):
        dialog = dialog_for(pygame.Rect(10, 10, 80, 28), cursor=2)
        self.assertTrue(station_auto_name.click(self.window, dialog, (20, 20)))
        field = dialog["field"]
        self.assertEqual("Casashius - 2", field.text)
        self.assertEqual(len("Casashius - 2"), field.cursor)

    def test_a_selection_in_the_field_is_replaced_not_kept(self):
        dialog = dialog_for(pygame.Rect(10, 10, 80, 28))
        dialog["field"].select_all()
        self.assertIsNotNone(dialog["field"].selection)
        self.assertTrue(station_auto_name.click(self.window, dialog, (20, 20)))
        self.assertIsNone(dialog["field"].selection)
        self.assertEqual("Casashius - 2", dialog["field"].text)

    def test_press_elsewhere_changes_nothing(self):
        dialog = dialog_for(pygame.Rect(10, 10, 80, 28), cursor=2)
        self.assertFalse(station_auto_name.click(self.window, dialog, (200, 200)))
        self.assertEqual(("Old Name", 2), (dialog["field"].text, dialog["field"].cursor))

    def test_a_dialog_without_the_button_is_left_alone(self):
        self.assertFalse(station_auto_name.click(self.window, dialog_for(None), (20, 20)))
        self.assertFalse(station_auto_name.click(self.window, {"station_id": 17, "field": TextField("x")}, (20, 20)))

    def test_a_press_when_the_name_has_become_unknown_keeps_the_text(self):
        window = window_with([], planets=[])
        dialog = dialog_for(pygame.Rect(10, 10, 80, 28))
        self.assertTrue(station_auto_name.click(window, dialog, (20, 20)))
        self.assertEqual("Old Name", dialog["field"].text)


class _Renderer:
    def _place_surface(self, surface):
        width, height = surface.get_size()
        return types.SimpleNamespace(page=0, width=width, height=height, u0=0.0, v0=0.0, u1=1.0, v1=1.0)


class RenameDialogTests(unittest.TestCase):
    """The real Control tab and rename dialog, drawn on a software surface and pressed with real events."""

    @classmethod
    def setUpClass(cls):
        pygame.init()
        from gl_ui_renderer import install_draw_dispatch
        install_draw_dispatch()

    def setUp(self):
        from Client import SolarSystemWindow
        station_auto_name.reset_for_tests()
        self.addCleanup(station_auto_name.reset_for_tests)
        self.window = window = SolarSystemWindow.__new__(SolarSystemWindow)
        window._lock = threading.RLock()
        window._uk = 1.0
        window._username = "Owner"
        window._elapsed = 0.0
        window._label_cache = OrderedDict()
        window._hud_plate_cache = {}
        window._npc_market_watch_sent = None  # v0.5.70: _draw_station_overlay now syncs the NPC market watch every frame
        fonts = {}

        def font(size, bold=False):
            if (size, bold) not in fonts:
                fonts[(size, bold)] = pygame.font.Font(None, size)
                fonts[(size, bold)].set_bold(bold)
            return fonts[(size, bold)]

        window._F = window._inventory_font = window._instrument_font = font
        window._station_overlay = {
            "kind": "player_station", "x": 20, "y": 20,
            "data": {"attached_station_id": 17, "owner_name": "Owner", "is_owner": True,
                     "station_name": "Old Name", "planet_id": 9},
        }
        window._layout = {"planets": [{"id": 9, "name": "Casashius"}]}
        window._my_stations_data = []
        window._deployed_stations = [
            {"id": 17, "display_name": "Old Name", "shields": 100, "max_shields": 1000, "energy": 500,
             "max_energy": 1000},
            {"id": 18, "attached_station_id": 18, "display_name": "Casashius - 1"},
            {"id": 19, "attached_station_id": 19, "display_name": "Casashius - 2"},
        ]
        window._ps_tab = "Control"
        window._ps_control_scroll = 0
        window._ps_control_sb_dragging = False
        window._ps_equipped_module_counts = {}
        window._ps_station_credits = 100
        window._ps_station_cargo = []
        window._ps_workers_used = 0
        window._ps_rename_dialog = {"station_id": 17, "field": TextField("Old Name", max_length=40),
                                    "confirm_rect": None, "cancel_rect": None}
        window._ps_rename_swallow_mouseup = False
        window._ps_motd_dialog = None
        window._ps_dock_perms_dialog = None
        window._ps_management_perms_dialog = None
        window.sent = []
        window._send_json = lambda command, payload: window.sent.append((command, payload))

    def draw(self):
        from render_mixin import RenderFrame
        canvas = pygame.Surface((800, 700), pygame.SRCALPHA)
        context = RenderFrame(canvas, 800, 700, lambda *args: args, 1.0, {}, 1, 1, 1 / 90, screen_above=canvas)
        self.window._draw_station_overlay(context)
        return canvas

    def press(self, rect):
        down = SimpleNamespace(type=pygame.MOUSEBUTTONDOWN, button=1, pos=rect.center)
        up = SimpleNamespace(type=pygame.MOUSEBUTTONUP, button=1, pos=rect.center)
        self.assertTrue(self.window._handle_ps_rename_pointer_event(down))
        self.assertTrue(self.window._handle_ps_rename_pointer_event(up))

    def test_button_sits_in_the_dialog_beside_confirm_and_cancel(self):
        self.draw()
        dialog = self.window._ps_rename_dialog
        auto, confirm, cancel = dialog["auto_rect"], dialog["confirm_rect"], dialog["cancel_rect"]
        self.assertIsInstance(auto, pygame.Rect)
        self.assertEqual(confirm.y, auto.y)
        self.assertEqual(confirm.height, auto.height)
        self.assertGreater(auto.left, cancel.right)
        self.assertFalse(auto.colliderect(confirm) or auto.colliderect(cancel))
        # the dialog is 380 wide, centred on the 800 px screen (x 210..590), with a 20 px margin: the CONFIRM button
        # starts at 230 and AUTO NAME ends at 570
        self.assertEqual(230, confirm.left)
        self.assertEqual(570, auto.right)

    def test_pressing_it_fills_the_field_and_confirm_sends_that_name(self):
        self.draw()
        dialog = self.window._ps_rename_dialog
        self.press(dialog["auto_rect"])
        self.assertIs(dialog, self.window._ps_rename_dialog)   # still open, nothing sent yet
        self.assertEqual([], self.window.sent)
        self.assertEqual("Casashius - 3", dialog["field"].text)
        self.draw()   # the new text is drawn and the buttons stay where they were
        self.press(dialog["confirm_rect"])
        self.assertIsNone(self.window._ps_rename_dialog)
        self.assertEqual([("PS_RENAME_STATION", {"station_id": 17, "name": "Casashius - 3"})], self.window.sent)

    def test_the_suggestion_can_be_edited_with_the_keyboard_afterwards(self):
        self.draw()
        dialog = self.window._ps_rename_dialog
        self.press(dialog["auto_rect"])
        # the game's own key handler for the open rename box (Esc/Enter aside, every key edits the TextField)
        self.window._handle_ps_rename_key(SimpleNamespace(type=pygame.KEYDOWN, key=pygame.K_BACKSPACE, mod=0, unicode=""))
        self.assertEqual("Casashius - ", dialog["field"].text)
        self.assertIs(dialog, self.window._ps_rename_dialog)   # editing does not close or send anything
        self.assertEqual([], self.window.sent)

    def test_pressing_cancel_is_unchanged(self):
        self.draw()
        self.press(self.window._ps_rename_dialog["cancel_rect"])
        self.assertIsNone(self.window._ps_rename_dialog)
        self.assertEqual([], self.window.sent)

    def test_button_is_greyed_and_dead_when_the_planet_is_unknown(self):
        self.window._layout = {"planets": []}
        self.draw()
        dialog = self.window._ps_rename_dialog
        self.assertIsNone(dialog["auto_rect"])
        down = SimpleNamespace(type=pygame.MOUSEBUTTONDOWN, button=1, pos=(560, 390))   # where the button would be
        self.assertTrue(self.window._handle_ps_rename_pointer_event(down))   # still consumed by the modal
        self.assertEqual("Old Name", dialog["field"].text)

    def test_unknown_planet_greys_the_button_out(self):
        canvas = self.draw()
        rect = self.window._ps_rename_dialog["auto_rect"]
        probe = (rect.x + 3, rect.centery)   # inside the border, left of the label: the button's fill
        live = tuple(canvas.get_at(probe))
        self.window._layout = {"planets": []}
        canvas = self.draw()
        self.assertIsNone(self.window._ps_rename_dialog["auto_rect"])
        self.assertNotEqual(live, tuple(canvas.get_at(probe)))
        # CANCEL, beside it, is drawn the same either way
        cancel = self.window._ps_rename_dialog["cancel_rect"]
        self.assertEqual(tuple(self.draw().get_at((cancel.x + 3, cancel.centery))), tuple(canvas.get_at((cancel.x + 3, cancel.centery))))

    def test_button_is_not_drawn_at_all_when_switched_off(self):
        self.draw()
        rect = self.window._ps_rename_dialog["auto_rect"]
        probe = (rect.x + 3, rect.centery)
        self.window._layout = {"planets": []}
        greyed = tuple(self.draw().get_at(probe))   # unknown planet: the button is there, greyed out
        station_auto_name._failed = True
        gone = tuple(self.draw().get_at(probe))
        self.assertIsNone(self.window._ps_rename_dialog["auto_rect"])
        self.assertNotEqual(greyed, gone)

    def test_dialog_still_draws_for_a_station_with_no_other_names(self):
        self.window._deployed_stations = []
        self.draw()
        self.press(self.window._ps_rename_dialog["auto_rect"])
        self.assertEqual("Casashius - 1", self.window._ps_rename_dialog["field"].text)


if __name__ == "__main__":
    unittest.main()

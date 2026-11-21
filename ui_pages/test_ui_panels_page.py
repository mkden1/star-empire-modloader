"""ui_panels_page.py: the ESC menu's UI PANELS page - what it draws, what each click does, how the Data Capture and Mods pages
that share its flag are routed to. (The draw and click blocks used to sit inside the game's _draw_esc_menu and run loop.)"""
import inspect
import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame

import modloader
import ui_panels_page
from test_capture_settings_panel import make_window

SCREEN = (1280, 720)
PANEL_ROWS = ("inventory", "target", "turrets", "fighter_control", "squad_hud", "fighter_hud")


def stand_in(**kw):
    win = SimpleNamespace(
        _panels={name: {"hidden": False} for name in PANEL_ROWS}, _chat_panel={"hidden": False},
        _mm_open=True, _coalition_open=False, _friends_open=False, _combat_log_open=True,
        _ui_panels_settings_open=True, _capture_settings_open=False, _mods_page_open=False,
        _opt_rects={}, _save_options_fn=mock.Mock(), _panel_layout_dict=mock.Mock(return_value={"x": 1}),
        _toggle_coalition_panel=mock.Mock(), _toggle_friends_panel=mock.Mock())
    for key, value in kw.items():
        setattr(win, key, value)
    return win


def rect(x, y, w=100, h=20):
    return pygame.Rect(x, y, w, h)


def with_rects(win, **named):
    """Give the window the rects draw() would have left: uip_panel around everything, one button per name."""
    win._opt_rects = {"uip_panel": rect(100, 100, 400, 600)}
    for i, (name, r) in enumerate(named.items()):
        win._opt_rects[name] = r
    return win


class DrawTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.font.init()

    def draw(self, **flags):
        screen = pygame.Surface(SCREEN, pygame.SRCALPHA)
        window = make_window(_ui_panels_settings_open=True, **flags)
        window._panels = {name: {"hidden": False} for name in PANEL_ROWS}
        window._chat_panel = {"hidden": False}
        window._mm_open, window._coalition_open, window._friends_open, window._combat_log_open = True, False, False, True
        window._draw_esc_menu(SimpleNamespace(screen=screen))
        return window, screen

    def test_without_the_combat_log_mod_the_page_has_no_combat_log_row_and_still_draws(self):
        """The Combat Log window is the combat_log mod's (it defines ``_combat_log_open``); ui_pages needs only core."""
        screen = pygame.Surface(SCREEN, pygame.SRCALPHA)
        window = make_window(_ui_panels_settings_open=True)
        window._panels = {name: {"hidden": False} for name in PANEL_ROWS}
        window._chat_panel = {"hidden": False}
        window._mm_open, window._coalition_open, window._friends_open = True, False, False
        window.__dict__.pop("_combat_log_open", None)
        self.assertFalse(hasattr(window, "_combat_log_open"))
        window._draw_esc_menu(SimpleNamespace(screen=screen))
        self.assertNotIn("uip_combat_log", window._opt_rects)
        self.assertIn("uip_friends", window._opt_rects)
        self.assertIn("uip_close", window._opt_rects)

    def test_without_the_squad_hud_mod_the_page_has_no_squad_or_fighter_rows_and_the_rest_stay_in_order(self):
        """The rosters' panels are registered by the squad_hud mod; a row for a panel that is not there would read ON."""
        screen = pygame.Surface(SCREEN, pygame.SRCALPHA)
        window = make_window(_ui_panels_settings_open=True)
        window._panels = {name: {"hidden": False} for name in PANEL_ROWS if name not in ("squad_hud", "fighter_hud")}
        window._chat_panel = {"hidden": False}
        window._mm_open, window._coalition_open, window._friends_open, window._combat_log_open = True, False, False, True
        window._draw_esc_menu(SimpleNamespace(screen=screen))
        self.assertNotIn("uip_squad_hud", window._opt_rects)
        self.assertNotIn("uip_fighter_hud", window._opt_rects)
        order = sorted((r.y, k) for k, r in window._opt_rects.items() if k.startswith("uip_") and k != "uip_panel")
        self.assertEqual([k for _y, k in order],
                         ["uip_inventory", "uip_target", "uip_turrets", "uip_fighter_control", "uip_chat", "uip_minimap",
                          "uip_coalition", "uip_friends", "uip_combat_log", "uip_close"])

    def test_with_the_combat_log_mod_the_row_is_the_last_toggle(self):
        window, _screen = self.draw()
        self.assertIn("uip_combat_log", window._opt_rects)
        self.assertGreater(window._opt_rects["uip_combat_log"].y, window._opt_rects["uip_friends"].y)

    def test_every_panel_has_a_button_inside_the_screen_and_the_panel_rect_encloses_them(self):
        window, screen = self.draw()
        rects = window._opt_rects
        keys = {"uip_" + n for n in PANEL_ROWS} | {"uip_chat", "uip_minimap", "uip_coalition", "uip_friends",
                                                    "uip_combat_log", "uip_close", "uip_panel"}
        self.assertEqual(keys, {k for k in rects if k.startswith("uip_")})
        for key in keys:
            with self.subTest(button=key):
                self.assertTrue(screen.get_rect().contains(rects[key]))
                self.assertTrue(rects["uip_panel"].contains(rects[key]))
        self.assertGreater(screen.get_bounding_rect().width, 0)

    def test_the_buttons_run_top_to_bottom_in_a_fixed_order(self):
        window, _screen = self.draw()
        order = sorted((r.y, k) for k, r in window._opt_rects.items() if k.startswith("uip_") and k != "uip_panel")
        self.assertEqual([k for _y, k in order],
                         ["uip_inventory", "uip_target", "uip_turrets", "uip_fighter_control", "uip_squad_hud",
                          "uip_fighter_hud", "uip_chat", "uip_minimap", "uip_coalition", "uip_friends",
                          "uip_combat_log", "uip_close"])

    def test_a_hidden_panel_reads_off_and_looks_different(self):
        _window, shown = self.draw()
        window = make_window(_ui_panels_settings_open=True)
        window._panels = {name: {"hidden": name == "target"} for name in PANEL_ROWS}
        window._chat_panel = {"hidden": False}
        window._mm_open, window._coalition_open, window._friends_open, window._combat_log_open = True, False, False, True
        hidden = pygame.Surface(SCREEN, pygame.SRCALPHA)
        window._draw_esc_menu(SimpleNamespace(screen=hidden))
        self.assertNotEqual(pygame.image.tobytes(shown, "RGBA"), pygame.image.tobytes(hidden, "RGBA"))

    def test_only_the_ui_panels_page_draws_here_when_neither_other_page_is_open(self):
        window, _screen = self.draw()
        self.assertFalse(hasattr(window, "_capture_rects"))
        self.assertFalse(hasattr(window, "_mods_page_rects"))

    def test_the_draw_function_takes_the_locals_of_the_esc_menu_it_reads(self):
        self.assertEqual(list(inspect.signature(ui_panels_page.draw).parameters),
                         ["self", "screen", "_sw", "_sh", "_cached_label", "font_med", "font_sm"])
        self.assertEqual(modloader.missing_globals(ui_panels_page), set())


class ClickTests(unittest.TestCase):
    def test_a_panel_button_flips_that_panels_hidden_flag_and_saves_the_layout(self):
        for name in PANEL_ROWS:
            with self.subTest(panel=name):
                win = with_rects(stand_in(), **{"uip_" + name: rect(120, 120)})
                ui_panels_page.click(win, (130, 130))
                self.assertTrue(win._panels[name]["hidden"])
                win._save_options_fn.assert_called_once_with(panels={"x": 1})
                ui_panels_page.click(win, (130, 130))
                self.assertFalse(win._panels[name]["hidden"])
                self.assertTrue(win._ui_panels_settings_open)

    def test_a_panel_the_game_does_not_have_is_left_alone_but_the_click_is_still_swallowed(self):
        win = with_rects(stand_in(_panels={}), uip_target=rect(120, 120))
        ui_panels_page.click(win, (130, 130))
        win._save_options_fn.assert_not_called()
        self.assertTrue(win._ui_panels_settings_open)                    # inside the page: nothing closes it

    def test_chat_minimap_and_combat_log_flip_their_own_state_and_save(self):
        win = with_rects(stand_in(), uip_chat=rect(120, 120), uip_minimap=rect(120, 150), uip_combat_log=rect(120, 180))
        ui_panels_page.click(win, (130, 130))
        ui_panels_page.click(win, (130, 160))
        ui_panels_page.click(win, (130, 190))
        self.assertEqual((win._chat_panel["hidden"], win._mm_open, win._combat_log_open), (True, False, False))
        self.assertEqual(win._save_options_fn.call_count, 3)

    def test_coalition_and_friends_use_the_games_own_toggles(self):
        win = with_rects(stand_in(), uip_coalition=rect(120, 120), uip_friends=rect(120, 150))
        ui_panels_page.click(win, (130, 130))
        ui_panels_page.click(win, (130, 160))
        win._toggle_coalition_panel.assert_called_once_with()
        win._toggle_friends_panel.assert_called_once_with()
        win._save_options_fn.assert_not_called()

    def test_close_and_a_click_outside_close_the_page_but_a_click_on_blank_panel_does_not(self):
        win = with_rects(stand_in(), uip_close=rect(120, 600))
        ui_panels_page.click(win, (130, 610))
        self.assertFalse(win._ui_panels_settings_open)
        win = with_rects(stand_in())
        ui_panels_page.click(win, (110, 300))                            # blank, inside the panel
        self.assertTrue(win._ui_panels_settings_open)
        ui_panels_page.click(win, (5, 5))                                # outside it
        self.assertFalse(win._ui_panels_settings_open)

    def test_an_open_data_capture_or_mods_page_gets_the_click_and_this_page_does_not(self):
        with mock.patch.object(ui_panels_page.capture_settings_panel, "click") as capture, \
                mock.patch.object(ui_panels_page.mods_page, "click") as mods:
            win = with_rects(stand_in(_capture_settings_open=True), uip_chat=rect(120, 120))
            ui_panels_page.click(win, (130, 130))
            capture.assert_called_once_with(win, (130, 130))
            self.assertFalse(win._chat_panel["hidden"])                  # not toggled
            win = with_rects(stand_in(_mods_page_open=True), uip_chat=rect(120, 120))
            ui_panels_page.click(win, (130, 130))
            mods.assert_called_once_with(win, (130, 130))
            self.assertFalse(win._chat_panel["hidden"])

    def test_the_click_function_takes_the_position_and_nothing_else_of_the_loops(self):
        self.assertEqual(list(inspect.signature(ui_panels_page.click).parameters), ["self", "_op"])


class HookTests(unittest.TestCase):
    def test_the_patched_game_files_call_the_module_and_do_not_carry_the_blocks(self):
        client, render = modloader.game_source("Client.py"), modloader.game_source("render_mixin.py")
        self.assertIn("ui_panels_page.click(self, event.pos)", client)
        self.assertIn("ui_panels_page.draw(self, screen, _sw, _sh, _cached_label, font_med, font_sm)", render)
        self.assertNotIn('"uip_combat_log"', client)
        self.assertNotIn('"Show or hide each movable panel"', render)


class EscMenuClickTests(unittest.TestCase):
    """esc_click: the ESC menu's Data Capture and Mods buttons (they were two ``elif`` branches in the run loop)."""

    def window(self, **rects):
        return SimpleNamespace(_esc_btn_rects=rects, _esc_menu_open=True, _ui_panels_settings_open=False,
                               _capture_settings_open=False, _mods_page_open=False)

    def test_the_data_capture_button_opens_that_page_and_closes_the_menu(self):
        win = self.window(capture=rect(10, 10), mods=rect(10, 40))
        self.assertTrue(ui_panels_page.esc_click(win, (20, 15)))
        self.assertEqual((win._esc_menu_open, win._ui_panels_settings_open, win._capture_settings_open, win._mods_page_open),
                         (False, True, True, False))

    def test_the_mods_button_opens_the_mods_page(self):
        win = self.window(capture=rect(10, 10), mods=rect(10, 40))
        self.assertTrue(ui_panels_page.esc_click(win, (20, 45)))
        self.assertEqual((win._esc_menu_open, win._ui_panels_settings_open, win._capture_settings_open, win._mods_page_open),
                         (False, True, False, True))

    def test_any_other_click_is_left_to_the_menu(self):
        for label, win, pos in (("elsewhere", self.window(capture=rect(10, 10), mods=rect(10, 40)), (500, 500)),
                                ("buttons not drawn", self.window(), (20, 15)),
                                ("one button missing", self.window(mods=rect(10, 40)), (20, 15))):
            with self.subTest(label):
                self.assertFalse(ui_panels_page.esc_click(win, pos))
                self.assertEqual((win._esc_menu_open, win._ui_panels_settings_open, win._capture_settings_open, win._mods_page_open),
                                 (True, False, False, False))

    def test_the_loop_asks_it_and_no_longer_carries_the_two_branches(self):
        source = modloader.game_source("Client.py")
        self.assertEqual(source.count("ui_panels_page.esc_click(self, _ep)"), 1)
        self.assertNotIn("self._capture_settings_open = True", source)
        self.assertNotIn("self._mods_page_open = True", source)


if __name__ == "__main__":
    unittest.main()

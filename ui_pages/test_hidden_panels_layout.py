"""The hidden flag of a HUD panel (and of the chat panel) survives the layout save and restore (tests we added to the dev's test_squad_window.py)."""
import unittest

import test_squad_window as dev


class HiddenPanelLayoutTests(unittest.TestCase):
    """_panel_layout_dict / apply_panel_layout carry each panel's hidden flag."""

    _window = staticmethod(dev.SquadWindowTests._window)

    def test_panel_layout_serializes_hidden_flag(self):
        # squad_hud/fighter_hud (a _panels-dict panel, not the big Squad
        # window above) landing on top of Ship Vitals after a resize is
        # what motivated a per-panel "hidden" toggle -- see the UI Panels
        # settings page and _draw_panel's own hidden early-return.
        window = self._window()
        window._panels = {
            "squad_hud": {"x": 10, "y": 520, "minimized": False,
                         "hidden": True, "max_h": 220, "max_w": None},
        }
        window._chat_panel = {}
        window._init_chat_channels()
        window._hotbar = []
        window._hangar_order = {}
        window._stats_pos_x = None
        window._stats_pos_y = None
        window._squad_pos_x = None
        window._squad_pos_y = None
        window._mm_zoom_level = 1
        window._mm_resize = 1.0
        window._mm_open = False
        window._mm_pos_x = None
        window._mm_pos_y = None
        window._coalition_open = False
        window._coalition_pos_x = None
        window._coalition_pos_y = None
        window._coalition_width = 300
        window._coalition_height = 420
        window._friends_open = False
        window._friends_pos_x = None
        window._friends_pos_y = None
        window._combat_log_open = True
        window._combat_log_pos_x = None
        window._combat_log_pos_y = None
        window._combat_log_width = 420
        window._combat_log_height = 320
        window._mission_detail_pos_x = None
        window._mission_detail_pos_y = None
        window._mission_detail_w = None
        window._mission_detail_h = None
        window._rc_haul_panel_pos = None
        window._rc_haul_pos_custom = False
        window._rc_dock_pos = None
        window._run_summary_pos = None
        window._gm_favorites = []

        layout = window._panel_layout_dict()

        self.assertTrue(layout["panels"]["squad_hud"]["hidden"])

    def test_panel_layout_restores_hidden_flag(self):
        window = self._window()
        window._panels = {
            "fighter_hud": {"x": 10, "y": 660, "minimized": False,
                            "hidden": False, "max_h": 220, "max_w": None},
        }
        window._chat_panel = {}
        window._hotbar = []
        window._hangar_order = {}
        window._stats_pos_x = None
        window._stats_pos_y = None
        window._squad_pos_x = None
        window._squad_pos_y = None

        window.apply_panel_layout({
            "panels": {"fighter_hud": {"hidden": True}},
        })

        self.assertTrue(window._panels["fighter_hud"]["hidden"])

    def test_panel_layout_serializes_chat_hidden_flag(self):
        # Chat lives outside self._panels (its own self._chat_panel dict,
        # serialized under the "chat_panel" key) -- added alongside the
        # squad_hud/fighter_hud hidden flag above once hiding was extended
        # to the chat window from the UI Panels settings page.
        window = self._window()
        window._panels = {}
        window._chat_panel = {"hidden": True}
        window._init_chat_channels()
        window._hotbar = []
        window._hangar_order = {}
        window._stats_pos_x = None
        window._stats_pos_y = None
        window._squad_pos_x = None
        window._squad_pos_y = None
        window._mm_zoom_level = 1
        window._mm_resize = 1.0
        window._mm_open = False
        window._mm_pos_x = None
        window._mm_pos_y = None
        window._coalition_open = False
        window._coalition_pos_x = None
        window._coalition_pos_y = None
        window._coalition_width = 300
        window._coalition_height = 420
        window._friends_open = False
        window._friends_pos_x = None
        window._friends_pos_y = None
        window._combat_log_open = True
        window._combat_log_pos_x = None
        window._combat_log_pos_y = None
        window._combat_log_width = 420
        window._combat_log_height = 320
        window._mission_detail_pos_x = None
        window._mission_detail_pos_y = None
        window._mission_detail_w = None
        window._mission_detail_h = None
        window._rc_haul_panel_pos = None
        window._rc_haul_pos_custom = False
        window._rc_dock_pos = None
        window._run_summary_pos = None
        window._gm_favorites = []

        layout = window._panel_layout_dict()

        self.assertTrue(layout["chat_panel"]["hidden"])

    def test_panel_layout_restores_chat_hidden_flag(self):
        window = self._window()
        window._panels = {}
        # Restoring a chat layout also restores the dev's chat tabs, which
        # needs the chat's whole state (the dev's chat tests build it the same way).
        window._username = "Alice"
        window._chat_font_scale = 1.0
        window._s = lambda value: int(value)
        window._init_chat()
        window._chat_panel = {"hidden": False}
        window._hotbar = []
        window._hangar_order = {}
        window._stats_pos_x = None
        window._stats_pos_y = None
        window._squad_pos_x = None
        window._squad_pos_y = None

        window.apply_panel_layout({
            "chat_panel": {"hidden": True},
        })

        self.assertTrue(window._chat_panel["hidden"])


if __name__ == "__main__":
    unittest.main()

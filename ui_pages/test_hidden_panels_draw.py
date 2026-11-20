"""A hidden HUD panel is not drawn and drops its geometry (tests we added to the dev's test_turret_mouse_ui.py)."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pygame

from test_turret_mouse_ui import PanelOwner, draw_preview


class HiddenPanelDrawTests(unittest.TestCase):
    """The retained panel key follows the hidden flag; hidden turret and fighter-control panels draw safely."""

    @classmethod
    def setUpClass(cls):
        pygame.init()

    def test_retained_panel_key_changes_with_hidden_flag(self):
        # A retained-GPU-layer cache keyed without "hidden" replays stale
        # pixels forever after toggling a panel's visibility from the UI
        # Panels settings page -- the toggle looks like a no-op. Confirms
        # _retained_panel_key's key actually changes when only "hidden" flips.
        owner = PanelOwner()
        owner._panels['turrets'] = {'x': 20, 'y': 20, 'hidden': False}
        shown_key = owner._retained_panel_key('turrets', pointer=False)
        owner._panels['turrets']['hidden'] = True
        hidden_key = owner._retained_panel_key('turrets', pointer=False)
        self.assertNotEqual(shown_key, hidden_key)

    def test_hidden_turret_panel_live_draw_does_not_crash(self):
        # Regression: _draw_panel bails out early (and drops
        # _panel_full_rects["turrets"]) once "hidden" is set, but this
        # function used to read that rect right afterwards unconditionally
        # -- a KeyError on the very next frame the live draw actually ran.
        owner, screen = draw_preview()
        owner._panels['turrets']['hidden'] = True
        owner._draw_turret_control_panel_live(
            SimpleNamespace(screen=screen, win_w=600, win_h=400))
        self.assertNotIn('turrets', owner._panel_full_rects)
        self.assertEqual(owner._turret_control_rects, {})

    def test_hidden_turret_panel_outer_wrapper_drops_stale_geometry(self):
        # The outer wrapper republishes last frame's cached hit geometry
        # every frame (so retained-GPU replay still routes clicks) -- it
        # must stop doing that once hidden, or a hidden panel keeps
        # intercepting clicks at its old position forever.
        owner = PanelOwner()
        owner._panels['turrets'] = {'x': 20, 'y': 20, 'hidden': True,
                                    'minimized': False}
        stale_rect = pygame.Rect(20, 20, 100, 60)
        owner._panel_header_rects['turrets'] = stale_rect.copy()
        owner._panel_toggle_rects['turrets'] = stale_rect.copy()
        owner._panel_full_rects['turrets'] = stale_rect.copy()
        owner._turret_control_hit_geometry = (
            stale_rect, stale_rect, stale_rect, {'power': stale_rect})
        with patch('pygame.mouse.get_pos', return_value=(0, 0)):
            owner._draw_turret_control_panel(
                SimpleNamespace(screen=pygame.Surface((600, 400)),
                                win_w=600, win_h=400))
        self.assertNotIn('turrets', owner._panel_full_rects)
        self.assertNotIn('turrets', owner._panel_header_rects)
        self.assertNotIn('turrets', owner._panel_toggle_rects)
        self.assertEqual(owner._turret_control_rects, {})
        self.assertNotIn('_turret_control_hit_geometry', owner.__dict__)

    def test_hidden_fighter_control_panel_does_not_crash(self):
        # Same crash shape as turrets, but fighter_control is drawn directly
        # every frame (no retained-layer cache in front of it), so this fires
        # on the very next frame after hiding it -- with a fabricator fitted,
        # which is the common case for a ship that flies fighters.
        owner = PanelOwner()
        owner._fabricator_charge_states = {
            'liberty_dart': {'type': 'liberty_dart', 'instances': []}}
        owner._panels['fighter_control'] = {'x': 20, 'y': 20, 'hidden': True,
                                            'minimized': False}
        owner._draw_fighter_control_panel(
            SimpleNamespace(screen=pygame.Surface((600, 400)),
                            win_w=600, win_h=400))
        self.assertNotIn('fighter_control', owner._panel_full_rects)
        self.assertEqual(owner._fighter_control_rects, {})


if __name__ == "__main__":
    unittest.main()

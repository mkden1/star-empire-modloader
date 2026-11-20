"""ui_panels_page.py - the ESC menu's UI PANELS page: a show / hide button for each HUD panel that has no other way to go.

Opened from the Options panel's "UI Panels..." button. It lists the movable HUD panels (inventory, target, turrets, fighter
fabricator, squad and fighter rosters, chat, minimap, coalition, friends, Combat Log) with an ON / OFF button each; a click
flips the panel's ``hidden`` flag (or opens / closes the panel the game's own way) and saves the layout. It was added off a
live report where a shrunk window parked two panels on top of Ship Vitals with no way to drag them clear.

``draw`` and ``click`` are the blocks that used to sit inside ``_draw_esc_menu`` (render_mixin.py) and the run loop's mouse
handling (Client.py), written as they were: ``draw`` takes the six locals of ``_draw_esc_menu`` it reads, ``click`` takes
the click position and does what the loop's branch did (the branch's ``continue`` is the caller's). The page shares the
``_ui_panels_settings_open`` flag with the Data Capture and Mods pages, so ``click`` routes to them first when one is open.
"""

import pygame

import capture_settings_panel
import mods_page


def draw(self, screen, _sw, _sh, _cached_label, font_med, font_sm) -> None:
    """Draw the UI PANELS page centred on ``screen`` and leave its rects in ``self._opt_rects`` (keys ``uip_*``)."""
    # Show/hide toggles for every movable HUD panel that has no
    # other way to get rid of it -- added directly off a live
    # report where a shrunk window parked Squad Roster and
    # Fighters/Drones on top of Ship Vitals with no way to drag
    # them clear; hiding either one here is an immediate
    # workaround alongside the actual resize-clamp fix (see
    # _draw_panel's own dragging-vs-clamp comment). Reuses
    # self._opt_rects for its own rects (keys prefixed "uip_")
    # since this page and Options are mutually exclusive -- only
    # one is ever open, so there's no stale-rect cross-talk.
    _PAD = self._s(16)
    _BH2 = self._s(28)
    _OPTION_ROW_GAP = self._s(7)
    _rows = [
        ("uip_inventory", "Ship Inventory",
         not self._panels.get("inventory", {}).get("hidden")),
        ("uip_target", "Target Panel",
         not self._panels.get("target", {}).get("hidden")),
        ("uip_turrets", "Turret Layout",
         not self._panels.get("turrets", {}).get("hidden")),
        ("uip_fighter_control", "Fighter Fabricator",
         not self._panels.get("fighter_control", {}).get("hidden")),
        ("uip_squad_hud", "Squad Roster",
         not self._panels.get("squad_hud", {}).get("hidden")),
        ("uip_fighter_hud", "Fighters / Drones",
         not self._panels.get("fighter_hud", {}).get("hidden")),
        ("uip_chat", "Chat",
         not self._chat_panel.get("hidden")),
        ("uip_minimap", "Minimap", bool(self._mm_open)),
        ("uip_coalition", "Coalition", bool(self._coalition_open)),
        ("uip_friends", "Friends", bool(self._friends_open)),
    ]
    if hasattr(self, "_combat_log_open"):       # the Combat Log window is the combat_log mod's: no row without it
        _rows.append(("uip_combat_log", "Combat Log", bool(self._combat_log_open)))
    _ROW_H = _BH2 + _OPTION_ROW_GAP
    _UPW = self._s(340)
    _UPH = self._s(54) + len(_rows) * _ROW_H + self._s(44)
    _upx = (_sw - _UPW) // 2
    _upy = (_sh - _UPH) // 2

    _dim = pygame.Surface((_sw, _sh), pygame.SRCALPHA)
    _dim.fill((0, 0, 0, 140))
    screen.blit(_dim, (0, 0))

    _panel = pygame.Surface((_UPW, _UPH), pygame.SRCALPHA)
    _panel.fill((10, 12, 28, 235))
    screen.blit(_panel, (_upx, _upy))
    pygame.draw.rect(screen, (30, 60, 160),
                     (_upx, _upy, _UPW, _UPH), 1, border_radius=6)
    self._opt_rects["uip_panel"] = pygame.Rect(_upx, _upy, _UPW, _UPH)

    _title = _cached_label("UI PANELS", (100, 160, 255), font_med)
    screen.blit(_title, (_upx + (_UPW - _title.get_width()) // 2,
                         _upy + self._s(10)))
    _subtitle = _cached_label(
        "Show or hide each movable panel", (120, 140, 190), font_sm)
    screen.blit(_subtitle, (_upx + (_UPW - _subtitle.get_width()) // 2,
                            _upy + self._s(30)))

    _uoy = _upy + self._s(54)
    for _key, _label, _on in _rows:
        _r = pygame.Rect(_upx + _PAD, _uoy, _UPW - _PAD * 2, _BH2)
        _col = (20, 60, 20) if _on else (20, 20, 50)
        _bdr = (80, 220, 80) if _on else (80, 100, 180)
        _txt = (140, 255, 140) if _on else (160, 180, 220)
        self._btn(screen, _r,
                  _cached_label(
                      f"{_label}: ON" if _on else f"{_label}: OFF",
                      _txt, font_med),
                  fill=_col, border=_bdr, border_w=1, radius=4)
        self._opt_rects[_key] = _r
        _uoy += _ROW_H

    _CR2 = pygame.Rect(_upx + _PAD, _uoy, _UPW - _PAD * 2, self._s(34))
    self._btn(screen, _CR2, _cached_label("Close  [ESC]", (120, 150, 220)),
              fill=(20, 20, 50), border=(60, 80, 160), border_w=1, radius=4)
    self._opt_rects["uip_close"] = _CR2


def click(self, _op) -> None:
    """A left click while the UI PANELS page (or the Data Capture / Mods page that shares its flag) is open."""
    _hit = False
    if self._capture_settings_open:      # MOD: Data Capture page
        capture_settings_panel.click(self, _op)
        return
    if self._mods_page_open:             # MOD: Mods page
        mods_page.click(self, _op)
        return
    for _pid, _rect_key in (
            ("inventory", "uip_inventory"),
            ("target", "uip_target"),
            ("turrets", "uip_turrets"),
            ("fighter_control", "uip_fighter_control"),
            ("squad_hud", "uip_squad_hud"),
            ("fighter_hud", "uip_fighter_hud")):
        _rect = self._opt_rects.get(_rect_key)
        if _rect and _rect.collidepoint(_op):
            _panel_state = self._panels.get(_pid)
            if _panel_state is not None:
                _panel_state["hidden"] = not _panel_state.get(
                    "hidden", False)
                self._save_options_fn(
                    panels=self._panel_layout_dict())
            _hit = True
            break
    if (not _hit and self._opt_rects.get("uip_chat")
            and self._opt_rects["uip_chat"].collidepoint(_op)):
        self._chat_panel["hidden"] = not self._chat_panel.get(
            "hidden", False)
        self._save_options_fn(panels=self._panel_layout_dict())
        _hit = True
    elif (not _hit and self._opt_rects.get("uip_minimap")
            and self._opt_rects["uip_minimap"].collidepoint(_op)):
        self._mm_open = not self._mm_open
        self._save_options_fn(panels=self._panel_layout_dict())
        _hit = True
    elif (not _hit and self._opt_rects.get("uip_coalition")
          and self._opt_rects["uip_coalition"].collidepoint(_op)):
        self._toggle_coalition_panel()
        _hit = True
    elif (not _hit and self._opt_rects.get("uip_friends")
          and self._opt_rects["uip_friends"].collidepoint(_op)):
        self._toggle_friends_panel()
        _hit = True
    elif (not _hit and self._opt_rects.get("uip_combat_log")
          and self._opt_rects["uip_combat_log"].collidepoint(_op)):
        self._combat_log_open = not self._combat_log_open
        self._save_options_fn(panels=self._panel_layout_dict())
        _hit = True
    elif (not _hit and self._opt_rects.get("uip_close")
          and self._opt_rects["uip_close"].collidepoint(_op)):
        self._ui_panels_settings_open = False
        _hit = True
    # Click-outside closes the page
    if not _hit:
        _panel_rect = self._opt_rects.get("uip_panel")
        if _panel_rect and not _panel_rect.collidepoint(_op):
            self._ui_panels_settings_open = False


def esc_click(self, pos) -> bool:
    """ESC menu: a click on the Data Capture or the Mods button opens that page (closing the menu first, the same
    transition its own "Options" button makes). Both pages share the UI Panels page's open flag, so every "a menu page is
    open" test already covers them; the page's own flag only picks which one is drawn. True when one was clicked."""
    rects = self._esc_btn_rects
    if rects.get("capture") and rects["capture"].collidepoint(pos):
        self._esc_menu_open = False
        self._ui_panels_settings_open = True
        self._capture_settings_open = True
        return True
    if rects.get("mods") and rects["mods"].collidepoint(pos):
        self._esc_menu_open = False
        self._ui_panels_settings_open = True
        self._mods_page_open = True
        return True
    return False

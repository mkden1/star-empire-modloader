"""combat_log_render.py - drawing the Combat Log window and the floating damage numbers (``RenderMixin`` methods).

``_draw_hitsplats`` draws the numbers that float up from a hit (fed by Client.py's register_hit() through
``self._hitsplats``), ``_draw_combat_log_panel`` the movable Combat Log window (``self._combat_log``). The methods of
``RenderMethods`` are written exactly as they were in render_mixin.py; ``install`` attaches them to ``RenderMixin``
from a one-line hook at the very end of that file and first borrows the few private names of render_mixin's own
(its palette and label table) that the drawing uses. The model is combat_log.py, the limits are shared with it.
"""

import time

import pygame

import modloader
from combat_log import _HITSPLAT_LIFETIME_S, _HITSPLAT_RISE_PX

# render_mixin.py's own private names that the drawing uses; install() copies them in from that module, so a rename
# in a game update is caught there (logged; the Combat Log is then missing) and not as an error at the first frame.
_BORROWED = ("_DAMAGE_TYPE_LABELS", "_GM_PLATE_BG", "_GM_STEEL", "_GM_TEXT")
_DAMAGE_TYPE_LABELS = _GM_PLATE_BG = _GM_STEEL = _GM_TEXT = None


class RenderMethods:
    """The Combat Log's drawing methods on RenderMixin."""

    # ── Hitsplats ───────────────────────────────────────────────────────────
    _HITSPLAT_DEALT_COLOR = (130, 235, 165)   # damage we dealt -- green
    _HITSPLAT_TAKEN_COLOR = (255, 105, 90)    # damage we took -- red

    def _draw_hitsplats(self, ctx) -> None:
        """Floating damage numbers, fed by register_hit() via self._hitsplats.

        Plain per-frame font.render, not the cached-label helpers: every
        splat's alpha and vertical offset change every single frame of its
        short life, and _cached_live_label's surfaces are shared/marked
        static (mutating one splat's alpha would bleed into every other
        user of that exact cached surface) -- see that helper's own
        docstring. A handful of short-lived splats on screen at once makes
        the cache's whole point (amortizing repeat renders of the SAME
        text) moot anyway, matching the existing "Warp-blocked notice"
        style transient-text precedent elsewhere in this file.
        """
        if not self._hitsplats:
            return
        now = time.monotonic()
        screen = ctx.screen_above
        w2s = ctx.w2s
        font = self._F(13, bold=True)
        kept = []
        for splat in self._hitsplats:
            age = now - splat["born_at"]
            if age < 0.0 or age >= _HITSPLAT_LIFETIME_S:
                continue
            kept.append(splat)
            t = age / _HITSPLAT_LIFETIME_S
            sx, sy = w2s(splat["wx"], splat["wy"])
            sy -= _HITSPLAT_RISE_PX * t
            color = (self._HITSPLAT_DEALT_COLOR if splat["dealt"]
                     else self._HITSPLAT_TAKEN_COLOR)
            surf = font.render(f"{splat['damage']:,.0f}", True, color)
            # Fade over the back half of the lifetime rather than linearly
            # the whole way -- a splat that's fully readable for its first
            # ~half-second and only then dissolves reads more like a real
            # damage-number pop than one that starts fading immediately.
            fade_t = max(0.0, (t - 0.5) / 0.5)
            surf.set_alpha(int(255 * (1.0 - fade_t)))
            screen.blit(surf, (sx - surf.get_width() // 2,
                               sy - surf.get_height() // 2))
        # register_hit runs on this same thread (the network queue is
        # drained once per frame, synchronously, same as everything else
        # driving this draw pass), so there's no other writer to race here
        # -- this is a plain prune-in-place, not a snapshot against
        # concurrent mutation.
        self._hitsplats = kept

    # ── Combat log panel ────────────────────────────────────────────────────
    def _draw_combat_log_panel(self, ctx) -> None:
        """Movable/resizable Combat Log window: two summary stat cards
        (DEALT / RECEIVED, each with a hit count, DPS-or-DTPS, and an
        AVG-per-hit) over a scrollable, columned history table (DIR /
        DAMAGE / TYPE / WEAPON / TARGET, newest entry at the top).

        Geometry/drag/resize follow the exact same shape as the Coalition
        HUD window (_coalition_apply_drag/_resize, same grip-at-top-left-
        keeps-bottom-right-fixed convention) -- see those methods' own
        comments. Registered with self._gui_windows via queue_draw (see
        its call site) so it participates in the same pointer-routing/
        z-order system every other movable panel does.
        """
        win_w, win_h = ctx.win_w, ctx.win_h
        screen = ctx.screen_above

        def _compact(n: float) -> str:
            """1234 -> '1.2K', 6800000 -> '6.8M' -- DPS/DTPS readouts only;
            the big total and per-row damage stay full precision."""
            n = float(n)
            sign = "-" if n < 0 else ""
            n = abs(n)
            for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
                if n >= div:
                    return f"{sign}{n / div:.1f}{suf}"
            return f"{sign}{n:.0f}"

        _W = max(320, min(900, int(self._combat_log_width)))
        _H = max(220, min(800, int(self._combat_log_height)))
        _W = min(_W, max(1, win_w - 8))
        _H = min(_H, max(1, win_h - 8))
        _HDR_H = self._s(24)
        _PAD = self._s(8)

        # Anchor above the bottom-right HUD launcher row (menu/minimap/
        # squad/galaxy-map buttons -- see _draw_hud_launchers, same
        # button_h/pad this reuses) on the very first frame; afterward the
        # persisted/dragged position takes over, matching Coalition's own
        # "None means legacy anchor once, then persistent" convention.
        if self._combat_log_pos_x is None or self._combat_log_pos_y is None:
            _launcher_top = win_h - self._s(36) - self._s(8)
            self._combat_log_pos_x = win_w - _W - self._s(10)
            self._combat_log_pos_y = _launcher_top - _H - self._s(10)
        _x = max(0, min(win_w - _W, int(self._combat_log_pos_x)))
        _y = max(0, min(win_h - _H, int(self._combat_log_pos_y)))
        self._combat_log_pos_x = _x
        self._combat_log_pos_y = _y
        self._combat_log_width = _W
        self._combat_log_height = _H
        self._combat_log_panel_rect = pygame.Rect(_x, _y, _W, _H)

        _plate = self._hud_plate_layers(_W, _H, _HDR_H, chamfer_max=14)
        screen.blit(_plate["plate"], (_x, _y))

        # ── Header: grip, title, Reset, close X ────────────────────────
        _hdr_font = self._F(12, bold=True)
        _hdr_s = self._cached_label(
            "COMBAT LOG", self._HUD_VALUE, _hdr_font)

        _close_w, _close_h = self._s(20), _HDR_H - self._s(6)
        _close_r = pygame.Rect(
            _x + _W - _plate["chamfer"] - _close_w - self._s(4),
            _y + self._s(3), _close_w, _close_h)
        self._combat_log_close_rect = _close_r
        _close_hov = _close_r.collidepoint(pygame.mouse.get_pos())
        self._btn(screen, _close_r,
                  self._F(12, bold=True).render("X", True, self._HUD_VALUE),
                  fill=(64, 22, 28) if _close_hov else _GM_PLATE_BG,
                  border=self._HUD_WARN if _close_hov else _GM_STEEL,
                  border_w=1, radius=0)

        _reset_font = self._F(10, bold=True)
        _reset_s = self._cached_label("RESET", self._HUD_CAPTION, _reset_font)
        _reset_w = _reset_s.get_width() + self._s(12)
        _reset_r = pygame.Rect(
            _close_r.x - _reset_w - self._s(6),
            _y + self._s(3), _reset_w, _close_h)
        self._combat_log_reset_rect = _reset_r
        _reset_hov = _reset_r.collidepoint(pygame.mouse.get_pos())
        self._btn(screen, _reset_r, _reset_s,
                  fill=(30, 42, 58) if _reset_hov else (18, 24, 38),
                  border=(90, 110, 150) if _reset_hov else (60, 78, 100),
                  border_w=1, radius=0)

        # Same diagonal-3-line grip icon as the Coalition window's own
        # top-left resize handle (see its comment) -- keeping the visual
        # language identical across every movable window.
        _grip_sz = self._s(14)
        _grip_r = pygame.Rect(_x, _y, _grip_sz, _grip_sz)
        self._combat_log_grip_rect = _grip_r
        _grip_hot = (_grip_r.collidepoint(pygame.mouse.get_pos())
                     or self._combat_log_resizing)
        _grip_col = _GM_TEXT if _grip_hot else _GM_STEEL
        for _gi in range(3):
            _go = self._s(3) + _gi * self._s(3)
            pygame.draw.line(screen, _grip_col,
                             (_x + _go, _y + self._s(2)),
                             (_x + self._s(2), _y + _go), 1)

        self._combat_log_header_rect = pygame.Rect(
            _x + _grip_sz + self._s(4), _y,
            max(1, _reset_r.x - (_x + _grip_sz + self._s(4)) - self._s(4)),
            _HDR_H)
        if (self._combat_log_header_rect.collidepoint(pygame.mouse.get_pos())
                or self._combat_log_dragging):
            _header_glow = pygame.Surface(
                (self._combat_log_header_rect.w, _HDR_H), pygame.SRCALPHA)
            _header_glow.fill((18, 42, 72, 150))
            screen.blit(_header_glow, self._combat_log_header_rect.topleft)
        screen.blit(_hdr_s, (
            _x + _grip_sz + self._s(8),
            _y + (_HDR_H - _hdr_s.get_height()) // 2 + 1))

        # ── Stat cards: DEALT / RECEIVED ────────────────────────────────
        _totals = self._combat_log_totals
        _elapsed = (
            max(1.0, time.monotonic() - self._combat_log_session_started_at)
            if self._combat_log_session_started_at is not None else 0.0)
        _dps = (_totals["dealt"] / _elapsed) if _elapsed > 0 else 0.0
        _dtps = (_totals["taken"] / _elapsed) if _elapsed > 0 else 0.0
        _avg_dealt = (_totals["dealt"] / _totals["dealt_hits"]
                      if _totals["dealt_hits"] else 0.0)
        _avg_taken = (_totals["taken"] / _totals["taken_hits"]
                      if _totals["taken_hits"] else 0.0)

        _card_gap = self._s(8)
        _card_w = (_W - _PAD * 2 - _card_gap) // 2
        _card_h = self._s(66)
        _card_y = _y + _HDR_H + self._s(4)
        _cap_font = self._F(10, bold=True)
        _hits_font = self._F(9)
        _num_font = self._F(19, bold=True)
        _rate_font = self._F(10)

        def _draw_stat_card(cx, label, total, hits, rate_label, rate_val,
                            avg_val, color):
            _cr = pygame.Rect(cx, _card_y, _card_w, _card_h)
            pygame.draw.rect(screen, (10, 15, 26), _cr, border_radius=3)
            pygame.draw.rect(screen, (45, 60, 84), _cr, 1, border_radius=3)
            _lbl_s = self._cached_label(label, color, _cap_font)
            screen.blit(_lbl_s, (cx + self._s(8), _card_y + self._s(6)))
            _hits_s = self._cached_live_label(
                f"{hits:,} hits", self._HUD_CAPTION, _hits_font)
            screen.blit(_hits_s, (
                cx + _card_w - _hits_s.get_width() - self._s(8),
                _card_y + self._s(7)))
            _num_s = self._cached_fitted_label(
                f"{total:,.0f}", self._HUD_VALUE, _num_font,
                _card_w - self._s(16))
            screen.blit(_num_s, (cx + self._s(8), _card_y + self._s(20)))
            _rate_s = self._cached_live_label(
                f"{rate_label} {_compact(rate_val)}   "
                f"AVG {_compact(avg_val)}",
                self._HUD_CAPTION, _rate_font)
            screen.blit(_rate_s, (
                cx + self._s(8),
                _card_y + _card_h - _rate_s.get_height() - self._s(5)))

        _draw_stat_card(
            _x + _PAD, "DEALT", _totals["dealt"], _totals["dealt_hits"],
            "DPS", _dps, _avg_dealt, self._HITSPLAT_DEALT_COLOR)
        _draw_stat_card(
            _x + _PAD + _card_w + _card_gap, "RECEIVED", _totals["taken"],
            _totals["taken_hits"], "DTPS", _dtps, _avg_taken,
            self._HITSPLAT_TAKEN_COLOR)

        # ── History table: column header + scrollable rows ─────────────
        _table_y = _card_y + _card_h + self._s(6)
        _col_font = self._F(9, bold=True)
        _row_font = self._F(10)
        _row_h = _row_font.get_linesize() + self._s(3)
        _content_x = _x + _PAD
        _content_w = _W - _PAD * 2
        _col_dir_w = self._s(28)
        _col_dmg_w = self._s(58)
        _col_type_w = self._s(56)
        _rest_w = max(self._s(60),
                      _content_w - _col_dir_w - _col_dmg_w - _col_type_w)
        _col_weapon_w = int(_rest_w * 0.45)
        _col_target_w = _rest_w - _col_weapon_w
        _dir_x = _content_x
        _dmg_x = _dir_x + _col_dir_w
        _type_x = _dmg_x + _col_dmg_w
        _weapon_x = _type_x + _col_type_w
        _target_x = _weapon_x + _col_weapon_w

        # "OPPONENT", not "TARGET" -- "if the damage is incoming, then
        # the player is technically the target, but this column is where
        # it is coming from". A single column serves both directions (an
        # OUT row's opponent is who we hit, an IN row's is who hit us),
        # and "opponent" reads correctly either way; "target" only reads
        # correctly for one of them.
        for _cap_text, _cap_x in (
                ("DIR", _dir_x), ("DAMAGE", _dmg_x), ("TYPE", _type_x),
                ("WEAPON", _weapon_x), ("OPPONENT", _target_x)):
            _cap_s = self._cached_label(
                _cap_text, self._HUD_CAPTION, _col_font)
            screen.blit(_cap_s, (_cap_x, _table_y))
        pygame.draw.line(
            screen, (45, 60, 84),
            (_content_x, _table_y + _col_font.get_linesize() + self._s(2)),
            (_content_x + _content_w,
             _table_y + _col_font.get_linesize() + self._s(2)))

        _body_y = _table_y + _col_font.get_linesize() + self._s(6)
        _body_h = max(_row_h, (_y + _H - _PAD) - _body_y)
        _visible_rows = max(1, int(_body_h // _row_h))

        _all_rows = self._combat_log[::-1]   # newest first
        _max_scroll = max(0, len(_all_rows) - _visible_rows)
        self._combat_log_scroll = max(
            0, min(_max_scroll, self._combat_log_scroll))
        _rows = _all_rows[
            self._combat_log_scroll:self._combat_log_scroll + _visible_rows]

        if not _rows:
            _empty_s = self._cached_label(
                "No hits yet.", self._HUD_CAPTION, _row_font)
            screen.blit(_empty_s, (_content_x, _body_y))
            return
        for _i, _row in enumerate(_rows):
            _ry = _body_y + _i * _row_h
            _color = (self._HITSPLAT_DEALT_COLOR if _row["dealt"]
                      else self._HITSPLAT_TAKEN_COLOR)
            _dir_s = self._cached_label(
                "OUT" if _row["dealt"] else "IN", _color, _row_font)
            screen.blit(_dir_s, (_dir_x, _ry))
            _dmg_s = self._cached_live_label(
                f"{_row['damage']:,.0f}", _color, _row_font)
            screen.blit(_dmg_s, (_dmg_x, _ry))
            _type_s = self._cached_fitted_label(
                # Already normalise_damage_type()'d at recording time (see
                # _record_combat_log_hit), so it's always a valid key here.
                _DAMAGE_TYPE_LABELS.get(
                    _row["damage_type"], _row["damage_type"]),
                self._HUD_CAPTION, _row_font, _col_type_w - self._s(4))
            screen.blit(_type_s, (_type_x, _ry))
            _weapon_s = self._cached_fitted_label(
                _row["weapon"], self._HUD_CAPTION, _row_font,
                _col_weapon_w - self._s(6))
            screen.blit(_weapon_s, (_weapon_x, _ry))
            _target_text = (
                _row["who"].capitalize() if not _row["dealt"]
                else _row["who"])
            _target_s = self._cached_fitted_label(
                _target_text, self._HUD_VALUE, _row_font,
                _col_target_w - self._s(4))
            screen.blit(_target_s, (_target_x, _ry))


def install(target) -> "list[str]":
    """Give ``RenderMixin`` (the class in render_mixin.py) these methods."""
    return modloader.install_methods(target, RenderMethods, globals(), _BORROWED)


# what test_mods_apply checks after every game update: (game module, game class, the class above)
ATTACHES = (("render_mixin", "RenderMixin", "RenderMethods"),)

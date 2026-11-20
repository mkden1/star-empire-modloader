"""galaxy_map_render.py - the galaxy map's Survey view drawing (``RenderMixin`` methods).

The Survey view's multi-select comparison panel (``_gm_survey_selection_surface`` and ``_gm_draw_survey_selection``)
and the VIEW SCANS button (``_gm_draw_view_scans_button``), written exactly as they were in render_mixin.py.
``install`` attaches them to ``RenderMixin`` from a one-line hook at the very end of that file, after copying in the
game's own palette constants the drawing uses (a rename in a game update is then caught at install and logged, not
as an error at the first frame of the map).
"""

import pygame

import modloader
from galaxy_survey import DEFAULT_MAX_TERRA as _SURVEY_TERRA_MAX
from galaxy_survey import terra_traffic_light_color as _terra_color
from scan_data import RESOURCE_LABELS as _SCAN_RESOURCE_LABELS
from scan_data import stations_for_body as _stations_for_body

# render_mixin.py's own private palette; install() copies it in from that module
_BORROWED = ("_GM_ACCENT", "_GM_CAPTION", "_GM_PLATE_BG", "_GM_STEEL", "_GM_STEEL_DIM", "_GM_SUCCESS", "_GM_TEXT",
             "_GM_VALUE", "_GM_WARN")
_GM_ACCENT = _GM_CAPTION = _GM_PLATE_BG = _GM_STEEL = _GM_STEEL_DIM = _GM_SUCCESS = _GM_TEXT = _GM_VALUE = _GM_WARN = None

# Survey view: the dim ring of a system that is not fully scanned yet. Terra-count gradient anchors/math live in
# galaxy_survey.py (pure, unit-tested there).
_GM_SURVEY_UNSCANNED = (110, 120, 140)


class RenderMethods:
    """The Survey view's drawing methods on RenderMixin."""

    def _gm_resource_totals(self, system_names) -> dict[str, int]:
        """Sum extractor-slot resource totals across every scanned body
        (planet or moon) in the given systems.

        Slot count (not a raw deposit size) is what the scan result itself
        reports as "the amount" of a resource (see
        protocol._on_planet_scan_result's own chat-log line). That count is
        PER STATION, so each body contributes it times the stations it can
        hold -- 3 on a planet, 1 on a moon (scan_logger.stations_for_body) --
        i.e. every slot you could actually put an extractor in. Shared by the
        single-system info popup and the Survey view's multi-select
        comparison overlay so the two definitions of "total resources"
        never drift apart.
        """
        totals: dict = {}
        scanned = getattr(self, "_scanned_resources_by_system", {})
        moons_by_system = getattr(self, "_scanned_moons_by_system", {})
        for _name in system_names:
            _moons = moons_by_system.get(_name, ())
            for _body, _body_res in scanned.get(_name, {}).items():
                _stations = _stations_for_body(_body in _moons)
                for _rkey, _rslots in (_body_res or {}).items():
                    if _rslots:
                        totals[_rkey] = (
                            totals.get(_rkey, 0) + int(_rslots) * _stations)
        return totals

    def _gm_survey_selection_surface(self):
        """Return a cached readout surface for the Survey view's Ctrl/Cmd-
        click multi-select: which systems are picked, and their combined
        resource totals (_gm_resource_totals over the whole set).

        Unlike _gm_system_info_surface, this is NOT hover-dismissed -- it
        stays up across mouse movement so it survives while the player
        keeps ctrl-clicking more systems onto the pile; only CLEAR ALL,
        leaving the Survey tab, or closing the map take it down (see the
        click handler and _close_galaxy_map).
        """
        _names = sorted(self._gm_survey_selected, key=str.lower)
        _combined = self._gm_resource_totals(_names)
        _combined_rows = sorted(
            ((_k, _v) for _k, _v in _combined.items() if _v > 0),
            key=lambda _item: _SCAN_RESOURCE_LABELS.get(
                _item[0], _item[0]).lower())
        _scanned_by = getattr(self, "_scanned_resources_by_system", {})

        _key = (
            "__gmsurveyselection_v1__", tuple(_names),
            tuple(_combined_rows), self._uk,
        )
        _cache = self.__dict__.setdefault("_gm_survey_selection_cache", {})
        _cached = _cache.get(_key)
        if _cached is not None:
            return _cached, _key

        _pad = self._s(14)
        _hdr_h = self._s(40)
        _footer_h = self._s(36)
        _section_h = self._s(25)
        _row_h = self._s(24)
        _w = self._s(320)
        _rows = [
            ("section", f"SELECTED SYSTEMS ({len(_names)})", 0, _GM_CAPTION),
        ]
        for _name in _names:
            _rows.append((
                "value", _name.upper(), "",
                _GM_SUCCESS if _scanned_by.get(_name) else _GM_CAPTION))
        _rows.append(("section", "COMBINED RESOURCES", 0, _GM_CAPTION))
        if _combined_rows:
            for _rkey, _rtotal in _combined_rows:
                _rows.append((
                    "value",
                    _SCAN_RESOURCE_LABELS.get(
                        _rkey, _rkey.replace("_", " ").title()).upper(),
                    _rtotal, _GM_SUCCESS))
        else:
            _rows.append(("value", "NONE SCANNED YET", "", _GM_CAPTION))

        _content_h = sum(
            _section_h if _kind == "section" else _row_h
            for _kind, _label, _value, _color in _rows
        )
        _h = _hdr_h + _pad + _content_h + _pad + _footer_h
        _surf = pygame.Surface((_w, _h), pygame.SRCALPHA)
        _plate = self._hud_plate_layers(
            _w, _h, _hdr_h, chamfer_max=self._s(16))
        _surf.blit(_plate["plate"], (0, 0))

        _font_title = self._instrument_font(15, bold=True)
        _font_section = self._instrument_font(11, bold=True)
        _font_row = self._instrument_font(13, bold=True)
        _font_hint = self._instrument_font(10)

        pygame.draw.rect(
            _surf, _GM_ACCENT,
            (_pad, (_hdr_h - self._s(18)) // 2, self._s(3), self._s(18)))
        _title = _font_title.render("SURVEY SELECTION", True, _GM_TEXT)
        _surf.blit(_title, (
            _pad + self._s(11), (_hdr_h - _title.get_height()) // 2 + 1))

        _y = _hdr_h + _pad
        _sw = max(4, self._s(5))
        for _kind, _label, _value, _color in _rows:
            if _kind == "section":
                _sec = _font_section.render(_label, True, _GM_CAPTION)
                _sec_y = _y + (_section_h - _sec.get_height()) // 2
                _surf.blit(_sec, (_pad, _sec_y))
                _line_x = _pad + _sec.get_width() + self._s(8)
                pygame.draw.line(
                    _surf, _GM_STEEL_DIM,
                    (_line_x, _y + _section_h // 2),
                    (_w - _pad, _y + _section_h // 2))
                _y += _section_h
                continue

            _sy = _y + (_row_h - _sw) // 2
            pygame.draw.rect(_surf, _color, (_pad, _sy, _sw, _sw))
            _caption = _font_row.render(_label, True, _GM_TEXT)
            _value_text = _value if isinstance(_value, str) else f"{_value:,}"
            _number = _font_row.render(_value_text, True, _color)
            _surf.blit(
                _caption,
                (_pad + _sw + self._s(7),
                 _y + (_row_h - _caption.get_height()) // 2))
            _surf.blit(
                _number,
                (_w - _pad - _number.get_width(),
                 _y + (_row_h - _number.get_height()) // 2))
            pygame.draw.line(
                _surf, (18, 26, 42),
                (_pad, _y + _row_h - 1),
                (_w - _pad, _y + _row_h - 1))
            _y += _row_h

        _clear_w = self._s(110)
        _clear_h = self._s(22)
        _clear_x = (_w - _clear_w) // 2
        _clear_y = _h - _footer_h + (_footer_h - _clear_h) // 2
        _clear_rect_local = pygame.Rect(_clear_x, _clear_y, _clear_w, _clear_h)
        _clear_hover = False
        _clear_label = self._gm_text(
            _font_hint, "CLEAR ALL", _GM_WARN,
            ("__gmsurveyclear_hud__", self._uk))
        _clear_btn = self._gm_chrome_button(
            _clear_w, _clear_h, _clear_label,
            (31, 13, 20), tuple(max(35, _c // 2) for _c in _GM_WARN),
            1, 0, _clear_hover,
            ("__gmsurveyclearbtn__", self._uk))
        _surf.blit(_clear_btn, (_clear_x, _clear_y))

        if len(_cache) >= 16:
            _cache.clear()
        _cached = (_surf, _clear_rect_local)
        _cache[_key] = _cached
        return _cached, _key

    def _gm_draw_survey_selection(self, _ov, canvas_rect):
        """Queue the Survey multi-select panel, docked to the canvas's
        top-right corner -- a fixed anchor rather than following a clicked
        node, since it's meant to stay put while the player keeps
        ctrl-clicking systems scattered across the map."""
        (_surf, _clear_rect_local), _key = self._gm_survey_selection_surface()
        _w, _h = _surf.get_size()
        _pad = self._s(12)
        _x = canvas_rect.right - _pad - _w
        _y = canvas_rect.top + _pad
        self._floor_blit(_surf, _key, _x, _y, batch=_ov)
        self._gm_survey_selection_rect = pygame.Rect(_x, _y, _w, _h)
        self._gm_survey_selection_clear_rect = pygame.Rect(
            _x + _clear_rect_local.x, _y + _clear_rect_local.y,
            _clear_rect_local.w, _clear_rect_local.h)

    def _gm_draw_view_scans_button(self, _ov, _fnt, x, y, w, h):
        """Draw and publish the top-bar VIEW SCANS control.

        Opens the local scan-data HTML dashboard (see scan_dashboard.py),
        focused on the currently-selected system readout if one is open.
        A momentary action button, not a toggle, so it has no open/checked
        state of its own — only hover.
        """
        _rect = pygame.Rect(int(x), int(y), int(w), int(h))
        _hover = _rect.collidepoint(pygame.mouse.get_pos())
        _label_col = _GM_VALUE if _hover else _GM_TEXT
        _label_key = ("__gmscansbtntext__", _label_col, self._uk)
        _label = self._gm_text(_fnt, "VIEW SCANS", _label_col, _label_key)
        _fill = (14, 29, 43) if _hover else _GM_PLATE_BG
        _border = _GM_ACCENT if _hover else _GM_STEEL
        _button_key = ("__gmscansbtn__", _rect.size, _hover, self._uk)
        _button = self._gm_chrome_button(
            _rect.w, _rect.h, _label, _fill, _border, 1, 0,
            False, _button_key)
        self._floor_blit(
            _button, _button_key, _rect.x, _rect.y, batch=_ov)
        self._gm_view_scans_btn_rect = _rect


def terra_fill(visited_entry):
    """Survey view node fill: traffic light, the colour IS the terra-planet count, on a continuous red->yellow->green
    gradient over 0..DEFAULT_MAX_TERRA (see galaxy_survey.py) so it reads at a glance with no hover needed -- it takes
    priority over route/range/ownership fills, which is the whole point of switching to this tab."""
    terra_n = int((visited_entry.get("planet_types") or {}).get("Terra", 0) or 0)
    return _terra_color(terra_n, _SURVEY_TERRA_MAX)


def survey_rings(self, mn, visited_entry, sname, inx, iny, base_r, zoom):
    """Survey view: the fully-scanned ring, and the multi-select highlight ring, around one system's node.

    A second at-a-glance indicator, independent of the terra fill colour: a bright ring once every known planet in the
    system has a matching entry in ``self._scanned_by_system`` (the exact "scanned" definition the click-to-inspect
    popup already uses), dim otherwise. ``base_r`` is the node's radius plus its ownership band, so the rings sit just
    outside it and the two never overlap. The Ctrl/Cmd-click highlight is one ring further out so it never fights the
    scanned ring just inside it for the same pixels."""
    ptypes = visited_entry.get("planet_types") or {}
    total = sum(int(v or 0) for v in ptypes.values())
    scanned = len(self._scanned_by_system.get(sname, {}))
    full = bool(total) and scanned >= total
    ring_w = max(2, int(3 * zoom))
    mn.add_ring(inx, iny, base_r + ring_w + self._s(2),
                _GM_SUCCESS if full else _GM_SURVEY_UNSCANNED, 255, ring_w)
    if sname in self._gm_survey_selected:
        mn.add_ring(inx, iny, base_r + ring_w * 2 + self._s(4), _GM_ACCENT, 255, max(2, self._s(2)))


def install(target) -> "list[str]":
    """Give ``RenderMixin`` (the class in render_mixin.py) these methods."""
    return modloader.install_methods(target, RenderMethods, globals(), _BORROWED)


# what test_mods_apply checks after every game update: (game module, game class, the class above)
ATTACHES = (("render_mixin", "RenderMixin", "RenderMethods"),)

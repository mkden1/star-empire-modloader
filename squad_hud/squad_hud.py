"""squad_hud.py - the SQUAD and FIGHTERS / DRONES roster panels of the HUD.

Two compact panels (a name on the Shields row, shield and energy bars, the exact numbers only while a row is hovered): the
squad members in this system and the drones / fighters this player has deployed. ``draw_rosters`` is the block that used
to sit in the middle of ``SolarSystemWindow._run_loop``'s HUD pass, written exactly as it was; the loop now calls it with
the six locals it reads (the frame context, the entity list, the HUD fonts and row heights and the dim text colour). It
assigns nothing the loop uses afterwards. The panels' default positions and the generic bar-row support they use
(``hide_bar_values``) stay small patches in Client.py and render_mixin.py.
"""


def draw_rosters(self, _frame, entities, _inv_fnt_hdr, _inv_fnt_body, _INV_HDR_H, _INV_ROW_H, _PNL_DIM) -> None:
    """Draw the SQUAD panel and the FIGHTERS / DRONES panel (the window's ``_draw_panel`` does the rest)."""
    self._profiler.mark("squad_hud")
    # ── Squad HUD: shields/energy for squad members visible in THIS
    # system. The server only ever broadcasts live entity state for
    # your own instance, so a squad member elsewhere in the galaxy
    # can't be shown here — only membership, not stats, is known
    # for them. Excludes the local player: SHIP VITALS already
    # covers your own shields/energy.
    _squad_members = (
        set(self._squad_state.get("members") or ()) - {self._username})
    if not _squad_members:
        _squad_lines = [(" Not in a squad", _PNL_DIM, False, 0, 0, None)]
    else:
        _squad_entities = sorted(
            (e for e in entities
             if e.get("username") in _squad_members),
            key=lambda e: str(e.get("username", "")).casefold())
        if not _squad_entities:
            _squad_lines = [
                (" No squad members in this system", _PNL_DIM,
                 False, 0, 0, None)]
        else:
            # Two bar rows per member: the name sits on the Shields
            # row (no "Shields"/"Energy" text — colour says which is
            # which), and the exact numbers only show up while that
            # row is hovered (hide_bar_values) rather than always on
            # screen. A full 10-person squad is still tall even this
            # compact, hence squad_hud's max_h/scroll (see panel
            # registration).
            # Fixed shield/energy blue+orange, matching SHIP VITALS
            # exactly (self._HUD_SH_FILL/_HUD_EN_FILL) rather than
            # the dynamic health-based shield colour used elsewhere
            # (Target panel etc.) — consistency with your own ship's
            # readout was the point here, not a low-shield warning.
            _squad_lines = []
            for _se in _squad_entities:
                _se_sh  = float(_se.get("shields", 0.0))
                _se_msh = max(1.0, float(_se.get("max_shields", 1)))
                _se_en  = float(_se.get("energy", 0.0))
                _se_men = max(1.0, float(_se.get("max_energy", 1)))
                _squad_lines.append(
                    (_se.get('username', '?'),
                     self._HUD_SH_CAP, True,
                     _se_sh, _se_msh, self._HUD_SH_FILL))
                _squad_lines.append(
                    ("", self._HUD_EN_CAP, True,
                     _se_en, _se_men, self._HUD_EN_FILL))
    _ROSTER_BAR_H = _inv_fnt_body.get_height() + self._s(4)
    self._draw_panel(
        _frame, "squad_hud", "SQUAD", _squad_lines,
        body_font=_inv_fnt_body,
        hdr_font=_inv_fnt_hdr,
        row_h=_INV_ROW_H,
        hdr_h=_INV_HDR_H,
        bar_h=_ROSTER_BAR_H,
        instrument_style=True,
        hide_bar_values=True)

    self._profiler.mark("fighter_hud")
    # ── Fighter/Drone HUD: shields/energy for every drone or fighter
    # this player currently has deployed. The game tracks "drone"
    # and "fighter" as separate entity kinds (is_drone/
    # drone_owner_id vs is_fighter/fighter_owner_id) but both show
    # together in one combined panel here.
    def _fighter_label(e: dict) -> str:
        for _key in ("display_name", "ship_display_name",
                     "drone_type", "fighter_type", "ship_type"):
            _raw = e.get(_key)
            if _raw:
                _text = str(_raw)
                return (_text.replace("_", " ").title()
                        if "_" in _text else _text)
        return "Fighter"

    _fighter_entities = [
        e for e in entities
        if ((e.get("is_drone")
             and e.get("drone_owner_id") == self._local_player_id)
            or (e.get("is_fighter")
                and e.get("fighter_owner_id") == self._local_player_id))
    ]
    if not _fighter_entities:
        _fighter_lines = [
            (" No deployed drones or fighters", _PNL_DIM,
             False, 0, 0, None)]
    else:
        _fighter_entities.sort(key=_fighter_label)
        _fe_name_totals: dict[str, int] = {}
        for _fe in _fighter_entities:
            _fe_name_totals[_fighter_label(_fe)] = (
                _fe_name_totals.get(_fighter_label(_fe), 0) + 1)
        _fe_name_seen: dict[str, int] = {}
        _fighter_lines = []
        for _fe in _fighter_entities:
            _fe_base = _fighter_label(_fe)
            if _fe_name_totals[_fe_base] > 1:
                _fe_name_seen[_fe_base] = _fe_name_seen.get(_fe_base, 0) + 1
                _fe_label = f"{_fe_base} #{_fe_name_seen[_fe_base]}"
            else:
                _fe_label = _fe_base
            _fe_sh  = float(_fe.get("shields", 0.0))
            _fe_msh = max(1.0, float(_fe.get("max_shields", 1)))
            _fe_en  = float(_fe.get("energy", 0.0))
            _fe_men = max(1.0, float(_fe.get("max_energy", 1)))
            # Same compaction as squad_hud: name on the Shields row
            # (no text label — colour says which bar is which),
            # numbers hidden until that row is hovered, and the same
            # fixed SHIP VITALS blue/orange rather than a dynamic
            # health-based shield colour. A full 7+7 drone/fighter
            # roster is still tall even this compact, hence
            # max_h/scroll on this panel too.
            _fighter_lines.append(
                (_fe_label, self._HUD_SH_CAP, True,
                 _fe_sh, _fe_msh, self._HUD_SH_FILL))
            _fighter_lines.append(
                ("", self._HUD_EN_CAP, True,
                 _fe_en, _fe_men, self._HUD_EN_FILL))
    self._draw_panel(
        _frame, "fighter_hud", "FIGHTERS / DRONES", _fighter_lines,
        body_font=_inv_fnt_body,
        hdr_font=_inv_fnt_hdr,
        row_h=_INV_ROW_H,
        hdr_h=_INV_HDR_H,
        bar_h=_ROSTER_BAR_H,
        instrument_style=True,
        hide_bar_values=True)

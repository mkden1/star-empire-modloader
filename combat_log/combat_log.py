"""combat_log.py - the Combat Log's bookkeeping on the game window (the SolarSystemWindow in Client.py).

"add damage hitsplats and a GUI that logs damage hits from me and from enemies on me." Client.py's register_hit()
(the one hook every ship-vs-ship damage message already funnels through) calls ``_record_combat_log_hit``, which
builds the log entries and the floating-number splats in ``self._combat_log`` / ``self._hitsplats``. The drawing is
in combat_log_render.py, the shooter attribution in shield_attribution.py. The methods of ``WindowMethods`` are the
window's: ``install`` attaches them (modloader.attach_methods) from a one-line hook at the end of Client.py, and the
two limits below are imported back into Client.py, which trims the lists.
"""

import logging
import math

import pygame

import modloader
from damage_types import normalise_damage_type

logger = logging.getLogger("combat_log")

# Fed from Client.py's register_hit() into self._combat_log / self._hitsplats; drawn by combat_log_render.py.
_COMBAT_LOG_MAX_LINES = 200        # trimmed history kept in memory
_COMBAT_LOG_VISIBLE_LINES = 10     # rows shown in the panel at once
_HITSPLAT_LIFETIME_S = 1.1         # how long a floating number stays up
_HITSPLAT_RISE_PX = 40.0           # total upward drift over its lifetime
_HITSPLAT_MAX = 60                 # trimmed backlog if hits arrive in a burst


class WindowMethods:
    """The Combat Log's methods on the game window (SolarSystemWindow)."""

    def _record_combat_log_hit(self, hit: dict, tid, is_my_hit: bool,
                               now: float, me: "dict | None") -> None:
        """Stage one resolved ship hit for the combat log, and pop its
        hitsplat immediately.

        "taken" (target is us) is exact -- register_hit already resolved
        that. Which specific ENTITY dealt or took a hit needs real care:
        neither SPACE_HIT nor either DUNGEON_AI_HIT wire shape carries an
        attacker id at all (confirmed against this file, the protocol
        handlers, and the real test fixtures for both), and SPACE_HIT is
        broadcast for EVERY combat event visible in the area, not just
        ones involving us (it has to be: it's what lets you SEE someone
        else's fight happening nearby, shield ripples and all).

        Two live reports narrowed this further:
          - "the current implementation is also not only showing my
            damage, its showing anyone in the systems damage" -- fixed
            earlier by requiring a dealt hit's target to be whatever WE
            currently have targeted (self._targeted_npc_id/_pid). Real,
            but not precise: it can't tell OUR shot from someone ELSE's
            shot landing on that same target at the same moment.
          - "the drone damage is all getting logged under my main
            account even if they were placed on a different account" --
            exactly that gap: an alt account's drone assisting against
            OUR current target passed the "on our target" filter and got
            counted as ours.
          - "the damage received is just logged as 'an enemy' rather
            than what it was" -- taken hits never had ANY attribution at
            all, by design, since is_my_hit alone says nothing about who
            attacked.

        All three are the same underlying problem: this method alone
        can't determine a shot's real owner from the hit event itself.
        self._shield_effects already tracks projectile/beam owner_id for
        its shield-ripple visual correlation (see its own module
        docstring) -- queue_attribution()/resolve_attributions() reuse
        that SAME matching against the SAME live data for a different
        question ("whose shot was this") instead of duplicating it. Real
        attribution takes a beat to resolve (the projectile/beam packet
        for a hit that just landed may not have arrived yet), so this
        only STAGES the hit here (using the same "on our target" filter
        as before to decide what's even worth queuing, and immediately
        popping a hitsplat off the quick provisional guess so the visual
        stays instant) -- _resolve_combat_log_attributions finishes the
        job a few frames later once the real owner is known (or times
        out and falls back to the provisional guess).
        """
        if getattr(self, "_combat_log_pending", None) is None:
            return      # a window with no combat-log state (vanilla's state-only test stubs)
        try:
            damage = float(hit.get("damage", 0.0))
        except (TypeError, ValueError):
            return
        if not math.isfinite(damage) or damage <= 0.0:
            return
        provisional_dealt = (
            not is_my_hit
            and (tid == self._targeted_npc_id or tid == self._targeted_pid))
        if not (is_my_hit or provisional_dealt):
            # Neither taken (target isn't us) nor plausibly dealt (target
            # isn't whatever we're fighting either) -- a stranger's fight
            # we happen to be able to see. Not even worth queuing for
            # attribution.
            return
        dtype = normalise_damage_type(hit.get("damage_type"))

        hx, hy = self._hit_world_pos(hit)
        if hx is not None:
            self._hitsplats.append({
                "born_at": now, "wx": hx, "wy": hy,
                "damage": damage, "dealt": provisional_dealt,
            })
            if len(self._hitsplats) > _HITSPLAT_MAX:
                del self._hitsplats[:len(self._hitsplats) - _HITSPLAT_MAX]

        self._combat_log_pending_seq += 1
        seq = self._combat_log_pending_seq
        self._combat_log_pending[seq] = {
            "t": now, "tid": tid, "is_my_hit": is_my_hit,
            "provisional_dealt": provisional_dealt,
            "damage": damage, "damage_type": dtype,
            "fallback_weapon": str((me or {}).get("active_weapon_name")
                                    or "Unknown"),
            # A dealt hit's "who" is our TARGET's own name -- entirely
            # independent of the async owner lookup below, so resolve it
            # NOW rather than a few frames from now in
            # _resolve_combat_log_attributions. That deferral was a real
            # bug, not just a missed optimization: a strong hit often IS
            # the killing blow, so by the time resolution ran the target
            # could already be gone from self._npc_entities/
            # _remote_entities (removed on death) -- live report was
            # exactly this, garbled raw ids ("8078b481") on OUT rows next
            # to correctly-named ones, because only the unlucky ones had
            # actually died in the resolution window.
            "target_name": (self._combat_log_actor_name(tid)
                            if not is_my_hit else None),
        }
        if len(self._combat_log_pending) > _COMBAT_LOG_MAX_LINES:
            # A burst that outruns resolution (queue never overflows --
            # resolve_attributions always eventually answers every item --
            # but a truly pathological volley could still pile up pending
            # entries faster than one-per-frame resolution drains them).
            # Drop the oldest half-dozen outright rather than let this
            # dict grow without bound.
            for stale_seq in sorted(self._combat_log_pending)[:6]:
                del self._combat_log_pending[stale_seq]
        self._shield_effects.queue_attribution(tid, now, seq)

    def _resolve_combat_log_attributions(self, now: float) -> None:
        """Finish staged combat-log hits once their real shooter is known
        (or resolve_attributions() has given up on this one). Called once
        per frame -- see its call site's own comment."""
        # A weapon resolved off a projectile trace (res.weapon, straight
        # from the raw wire payload -- see queue_attribution's own
        # comment) isn't guaranteed to already be display text: a live
        # report showed "quasar_cannon" here verbatim, next to properly
        # named weapons like "King's Thumper" (our own active_weapon_name,
        # which the server DOES send pre-formatted). Same
        # underscore-to-Title-Case cleanup already used everywhere else in
        # this file for a raw item/type key; a name with no underscore
        # (the common case) passes through untouched.
        def _display_weapon(raw: str) -> str:
            return raw.replace("_", " ").title() if "_" in raw else raw

        for res in self._shield_effects.resolve_attributions(now):
            pending = self._combat_log_pending.pop(res.key, None)
            if pending is None:
                continue
            tid = pending["tid"]
            is_my_hit = pending["is_my_hit"]
            owner_id = res.owner_id
            res_weapon = _display_weapon(res.weapon) if res.weapon else ""
            if is_my_hit:
                dealt = False
                who = (self._combat_log_actor_name(owner_id)
                       if owner_id is not None else "an enemy")
                weapon = (res_weapon or "Unknown") if owner_id is not None \
                    else "Unknown"
            else:
                # Real owner known -> trust it completely (this is the
                # actual fix for a hit that lands on our target but was
                # someone/something else's shot, e.g. an alt account's
                # drone). Owner unknowable -> fall back to the same
                # provisional "it landed on our target" guess used to
                # decide whether to even queue this in the first place.
                dealt = (owner_id == self._local_player_id
                         if owner_id is not None
                         else pending["provisional_dealt"])
                if not dealt:
                    continue   # someone/something else's hit -- drop it
                who = pending["target_name"]
                weapon = ((res_weapon or pending["fallback_weapon"])
                          if owner_id is not None
                          else pending["fallback_weapon"])
            if self._combat_log_session_started_at is None:
                self._combat_log_session_started_at = pending["t"]
            self._combat_log.append({
                "t": pending["t"], "dealt": dealt, "who": who,
                "damage": pending["damage"],
                "damage_type": pending["damage_type"], "weapon": weapon,
            })
            if len(self._combat_log) > _COMBAT_LOG_MAX_LINES:
                del self._combat_log[
                    :len(self._combat_log) - _COMBAT_LOG_MAX_LINES]
            _totals = self._combat_log_totals
            if dealt:
                _totals["dealt"] += pending["damage"]
                _totals["dealt_hits"] += 1
            else:
                _totals["taken"] += pending["damage"]
                _totals["taken_hits"] += 1

    def _reset_combat_log(self) -> None:
        """Clear the combat log's history, totals, and DPS clock -- the
        Reset button in its header. Hitsplats are deliberately untouched:
        they're already-in-flight transient visuals, not accumulated
        stats, so there's nothing meaningful to reset there."""
        self._combat_log.clear()
        self._combat_log_totals = {
            "dealt": 0.0, "taken": 0.0, "dealt_hits": 0, "taken_hits": 0}
        self._combat_log_session_started_at = None
        self._combat_log_scroll = 0

    def _combat_log_actor_name(self, tid) -> str:
        """Best-effort display name for a hit's target_id: an NPC's
        display_name, or a player/dungeon-AI username (both real players
        and dungeon AI live in self._remote_entities — see _resolve_target's
        own comment).

        A live report showed raw, un-prettified ids here ("-119200") that
        turned out to be a resolved shooter's owner_id -- an ATTACKER
        identity read straight off a projectile/beam trace (see
        _resolve_combat_log_attributions), a genuinely different field
        than the "npc-"-prefixed target_id strings SPACE_HIT/
        DUNGEON_AI_HIT use and this method was originally written
        against. Whether the server always shapes an NPC's owner_id the
        same "npc-<id>" way its target_id is shaped isn't something this
        client can independently confirm, so this also tries "npc-"
        prefixed as a second, best-effort key -- a no-cost fallback (one
        extra dict lookup) that can only turn a raw id into a real name,
        never the reverse. The FIRST attempt at this still showed raw ids
        after a relaunch, meaning even that guess doesn't match every
        real shape this field takes -- rather than keep guessing blind,
        an unresolved id now falls back to the same clean "an enemy" text
        the fully-unknown-attacker case already used (never a bare id in
        the visible UI again), and logs the raw value to the file so a
        real one can be read back out of it next time this happens,
        instead of another guess.
        """
        if isinstance(tid, str) and tid.startswith("npc-"):
            with self._lock:
                ent = self._npc_entities.get(tid)
            if ent is not None:
                return str(ent.get("display_name") or "Unknown")
        with self._lock:
            ent = self._remote_entities.get(tid)
        if ent is not None:
            return str(ent.get("username") or "Unknown")
        if tid is not None and not (
                isinstance(tid, str) and tid.startswith("npc-")):
            with self._lock:
                ent = self._npc_entities.get(f"npc-{tid}")
            if ent is not None:
                return str(ent.get("display_name") or "Unknown")
        if tid is None:
            return "Unknown"
        logger.info(
            "Combat log: could not resolve a display name for id "
            "%r (type %s) -- not found in _npc_entities (bare or "
            "'npc-'-prefixed) or _remote_entities.",
            tid, type(tid).__name__)
        return "an enemy"

    def _combat_log_apply_drag(self, pos, win_w: int, win_h: int) -> None:
        """Move the Combat Log window by its title bar, clamped on screen.
        Same shape as _coalition_apply_drag / _friends_apply_drag."""
        if self._combat_log_panel_rect is None:
            return
        _max_x = max(0, int(win_w) - self._combat_log_panel_rect.width)
        _max_y = max(0, int(win_h) - self._combat_log_panel_rect.height)
        self._combat_log_pos_x = max(
            0, min(_max_x, int(pos[0]) - self._combat_log_drag_dx))
        self._combat_log_pos_y = max(
            0, min(_max_y, int(pos[1]) - self._combat_log_drag_dy))

    def _combat_log_apply_resize(self, pos, win_w: int, win_h: int) -> None:
        """Resize the Combat Log window from its top-left grip, keeping the
        current bottom-right corner fixed. Same shape as
        _coalition_apply_resize; the min/max bounds are this panel's own
        (wide enough for the WEAPON/TARGET columns to stay readable, tall
        enough for the header + both stat cards even at minimum)."""
        if self._combat_log_panel_rect is None:
            return
        _br_x = self._combat_log_panel_rect.right
        _br_y = self._combat_log_panel_rect.bottom
        _max_w = max(320, min(900, int(win_w) - 8))
        _max_h = max(220, min(800, int(win_h) - 8))
        _w = max(320, min(_max_w, _br_x - int(pos[0])))
        _h = max(220, min(_max_h, _br_y - int(pos[1])))
        self._combat_log_width = _w
        self._combat_log_height = _h
        self._combat_log_pos_x = _br_x - _w
        self._combat_log_pos_y = _br_y - _h

    def _close_combat_log_panel(self) -> None:
        self._combat_log_open = False
        self._combat_log_dragging = False
        self._combat_log_resizing = False


def init_state(self) -> None:
    """The window's Combat Log state, set up right after the game's own ``_init_trails_beams_flashes`` (where the
    hit-flash and shield-ripple state it sits beside is made)."""
    # Both lists are appended to only from register_hit (never under self._lock, matching _hit_flashes: this is
    # client-only visual/log state with one writer and one reader, both on the same thread -- see register_hit's
    # comment for the "why no lock" precedent this follows).
    self._combat_log = []
    self._combat_log_totals = {"dealt": 0.0, "taken": 0.0, "dealt_hits": 0, "taken_hits": 0}
    # Monotonic time of the first hit since the last reset -- the denominator for the DPS/DTPS readouts (both sides
    # share one clock, matching "how long has this fight/session been running"). None until the first hit lands.
    self._combat_log_session_started_at = None
    self._combat_log_open = True   # toggled with Shift+L (plain L is the dev's Skills key)
    self._hitsplats = []
    # Hits awaiting real shooter attribution -- see _record_combat_log_hit's own comment for why this is a deferred,
    # two-phase process instead of deciding dealt/who/weapon on the spot. Keyed by an incrementing sequence id (the
    # same key handed to self._shield_effects.queue_attribution), consumed by _resolve_combat_log_attributions once
    # resolve_attributions() returns an answer for it.
    self._combat_log_pending = {}
    self._combat_log_pending_seq = 0

    # Movable/resizable Combat Log window -- same shape as the Coalition HUD window: None positions use a legacy
    # above-the-launcher-row anchor on the first rendered frame, then become persistent coordinates; the title bar
    # and top-left grip own movement and resizing from there.
    self._combat_log_pos_x = None
    self._combat_log_pos_y = None
    self._combat_log_width = 420
    self._combat_log_height = 320
    self._combat_log_dragging = False
    self._combat_log_resizing = False
    self._combat_log_drag_dx = 0
    self._combat_log_drag_dy = 0
    self._combat_log_panel_rect = None
    self._combat_log_header_rect = None
    self._combat_log_grip_rect = None
    self._combat_log_close_rect = None
    self._combat_log_reset_rect = None
    # Rows scrolled up from the newest entry (0 == pinned to the latest hit); clamped to available history each frame
    # in the draw pass.
    self._combat_log_scroll = 0


def load_layout(self, layout: dict) -> None:
    """Restore the Combat Log window's visibility, position and size from the saved layout, right after the game's
    ``apply_panel_layout`` has restored its own panels. Same shape as Coalition; older options files simply omit this
    object and retain the default anchor/size."""
    saved = layout.get("combat_log")
    if not isinstance(saved, dict):
        return
    # getattr defaults, as save_layout reads them -- the dev's bare test windows (test_reset_interface_layout)
    # reload a layout that carries this object without having set these.
    self._combat_log_open = bool(saved.get("visible", getattr(self, "_combat_log_open", True)))
    try:
        self._combat_log_width = max(320, min(900, int(saved.get("width", getattr(self, "_combat_log_width", 420)))))
        self._combat_log_height = max(220, min(800, int(saved.get("height", getattr(self, "_combat_log_height", 320)))))
    except (TypeError, ValueError):
        pass
    try:
        if saved.get("x") is not None and saved.get("y") is not None:
            self._combat_log_pos_x = int(saved["x"])
            self._combat_log_pos_y = int(saved["y"])
    except (TypeError, ValueError):
        self._combat_log_pos_x = None
        self._combat_log_pos_y = None


def save_layout(self, layout: dict) -> None:
    """Add the Combat Log window to the layout dict the game's ``_panel_layout_dict`` builds for options.txt. getattr
    with the real defaults, so a vanilla test whose stand-in window has no combat-log panel
    (test_bot_window_layout.py) can still build a layout."""
    layout["combat_log"] = {"visible": bool(getattr(self, "_combat_log_open", True)),
                            "x": getattr(self, "_combat_log_pos_x", None),
                            "y": getattr(self, "_combat_log_pos_y", None),
                            "width": int(getattr(self, "_combat_log_width", 420)),
                            "height": int(getattr(self, "_combat_log_height", 320))}


# ── the Combat Log's mouse handling, called from the game's event loop (Client.py's _run_loop) ─────────────────────
# Each takes what the loop's own code used (the event, the loop's ``_gui_route_allows`` closure, the window size) and
# returns True where that code did ``continue``, so the hook in the loop is a call and a ``continue``.

def wheel(self, event, route_allows) -> bool:
    """MOUSEWHEEL: scroll the history when the cursor is over the panel.

    ``route_allows`` alone is NOT "the cursor is over the panel": PointerRoute.allows() returns True whenever nothing
    else is blocking, which includes the cursor being over open space with no GUI window under it at all. Without
    the explicit collidepoint check (matching Friends/Coalition/Squad's own roster-scroll checks), this swallowed
    EVERY wheel scroll anywhere in the world the instant the panel was open (which it is by default) -- reported live
    as "scrolling in and out with the mouse wheel no longer works", since it never reached the camera zoom handler.
    The precise upper scroll bound (can't scroll past the oldest kept entry) is clamped in the draw pass, which also
    knows how many rows are currently visible."""
    if (self._combat_log_open
            and route_allows("combat_log")
            and self._combat_log_panel_rect is not None
            and self._combat_log_panel_rect.collidepoint(pygame.mouse.get_pos())):
        self._combat_log_scroll = max(0, self._combat_log_scroll - int(event.y))
        return True
    return False


def panel_hit(self, pos, route_allows) -> bool:
    """MOUSEBUTTONDOWN: is this click on the open panel (and not on a window above it)? The loop's ``elif`` for the
    panel asks this, then ``click`` for what the click does."""
    return bool(self._combat_log_open
                and route_allows("combat_log")
                and self._combat_log_panel_rect
                and self._combat_log_panel_rect.collidepoint(pos))


def click(self, pos) -> bool:
    """A click on the panel: close, reset, start a resize or start a drag. True when it was one of those (the loop
    does ``continue``). Anything else inside the panel (the history table) has no click behaviour of its own yet and
    is still absorbed by the ``elif`` so it doesn't fall through to the world or a window behind it: False."""
    if self._combat_log_close_rect and self._combat_log_close_rect.collidepoint(pos):
        self._close_combat_log_panel()
        return True
    if self._combat_log_reset_rect and self._combat_log_reset_rect.collidepoint(pos):
        self._reset_combat_log()
        return True
    if self._combat_log_grip_rect and self._combat_log_grip_rect.collidepoint(pos):
        self._combat_log_resizing = True
        return True
    if self._combat_log_header_rect and self._combat_log_header_rect.collidepoint(pos):
        self._combat_log_dragging = True
        self._combat_log_drag_dx = pos[0] - self._combat_log_panel_rect.x
        self._combat_log_drag_dy = pos[1] - self._combat_log_panel_rect.y
        return True
    return False


def release(self) -> bool:
    """MOUSEBUTTONUP: end a resize or a drag and save where the panel ended up. True when one was going."""
    if self._combat_log_resizing:
        self._combat_log_resizing = False
        self._save_options_fn(panels=self._panel_layout_dict())
        return True
    if self._combat_log_dragging:
        self._combat_log_dragging = False
        self._save_options_fn(panels=self._panel_layout_dict())
        return True
    return False


def motion(self, pos, win_w: int, win_h: int, route_allows) -> bool:
    """MOUSEMOTION: follow the pointer while the panel is being resized or dragged. True when one was going."""
    if self._combat_log_resizing and route_allows("combat_log"):
        self._combat_log_apply_resize(pos, win_w, win_h)
        return True
    if self._combat_log_dragging and route_allows("combat_log"):
        self._combat_log_apply_drag(pos, win_w, win_h)
        return True
    return False


def install(target) -> "list[str]":
    """Give the game window these methods and hook the three of the game's methods that set up, load and save the
    window's state."""
    return modloader.install_methods(
        target, WindowMethods,
        after={"_init_trails_beams_flashes": init_state, "apply_panel_layout": load_layout},
        result={"_panel_layout_dict": save_layout})


# what test_mods_apply checks after every game update: (game module, game class, the class above), and
# (game module, game class, game method, the function that runs after it)
ATTACHES = (("Client", "SolarSystemWindow", "WindowMethods"),)
HOOKS = (("Client", "SolarSystemWindow", "_init_trails_beams_flashes", "init_state"),
         ("Client", "SolarSystemWindow", "apply_panel_layout", "load_layout"),
         ("Client", "SolarSystemWindow", "_panel_layout_dict", "save_layout"))

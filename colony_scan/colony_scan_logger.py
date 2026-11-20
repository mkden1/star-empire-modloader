"""colony_scan_logger.py — persist a player-owned colony station's real, live economic state for
offline comparison against colony_tool's planned setup.

Why this exists
----------------
colony_tool (a separate, offline MILP planner — see its own repo) predicts population/profit from
a static economy model (fixed demand-curve constants, no representation of processing throughput,
resource storage caps, or the server's own growth-rate mechanics). The live client already
receives the REAL numbers behind those same concepts — via COLONY_DATA (population, food stock,
projected growth/loss, colonization index, and the server's own per-resource demand-curve
constants) and PS_STATION_CARGO (equipped modules, resource caps/reserves, current cargo stock,
workers used) — but only ever keeps the most-recently-viewed station's copy in memory
(``self._ps_colony_data`` / the various ``_ps_*`` fields), overwritten the moment a different
station's panel is opened. This module persists every station's snapshot to disk instead, so a
later comparison tool can see every owned station's real state at once, not just whichever one
happens to be open right now.

One file (scan_data/colony_scan.jsonl), five sources. The upserted ones keep one row per
(source, station_id), so re-opening the same station's panel refreshes its row rather than growing
the file unbounded; the append-only ones keep every observation:

  * ``colony_data``    — COLONY_DATA: population, food/max_food, projected_growth/loss,
    colonization index, the basket's per-resource demand-curve constants (base_value, per_capita),
    current prices/reserves, and the demand_model's elasticity/cap_mult.
  * ``station_setup``  — PS_STATION_CARGO: equipped module counts, resource caps/reserves, current
    cargo (actual stock on hand — the tell for a downstream bottleneck: resources piling up
    unconverted vs. running dry), workers used, hull, station credits.
  * ``colony_tick``     — APPEND-only: one row per distinct COLONY_DATA observation, capturing just
    population/prev_population/food/max_food/index/projected_growth/projected_loss/
    projected_starving plus that same tick's real per-resource delivered quantities (COLONY_DATA's
    own ``income_history[0]``). A single snapshot only ever shows ONE tick's growth/shrink
    (population vs. prev_population); reopening the Colony tab across many real colony ticks
    (every ~2 real hours) builds up the actual history needed to see whether population is
    trending toward a ceiling, oscillating with ration-supply volatility, or genuinely stuck.
  * ``shuttle_network`` — PS_SHUTTLE_DATA: route config (per-route resource/direction/endpoints,
    per-station reserves/caps) and the real transfer_limit/transfer_interval_seconds -- i.e. the
    orbital shuttle network's actual throughput rate, to check against colony_tool's assumed
    200-units-per-10-seconds constant. Keyed by the HUB station's id, since a shuttle-network
    dialog is always opened from one hub station. Only rewritten when the route CONFIG itself
    changes, not on every live quantity update (see ``shuttle_levels`` for that).
  * ``shuttle_levels``  — APPEND-only: one row per PS_SHUTTLE_LEVELS observation, capturing each
    route's live source_qty/destination_qty -- whether a resource is backing up at one end or
    draining at the other, over time. Split out from ``shuttle_network`` itself so the (much
    larger) route-config snapshot doesn't get rewritten on every levels poll.

Optional ``context`` (system_name, planet_id, station_name) is merged in at capture time purely so
the resulting file is self-describing without needing a separate station-id lookup.

Each record_* function is a best-effort, protocol.py-adjacent hook: it must never raise, since the
UI paths that receive this data still have to work regardless of whether logging it to disk
succeeds — same contract as combat_scan_logger.record_ship_specs / scan_logger.record_scan.

The ``capture_*`` functions at the bottom are what Client.py actually calls: each takes the
SolarSystemWindow, works out the context (system / station / planet) from it, and hands over to
the matching record_*. Client.py and render_mixin.py are merged by hand at every vanilla update, so
the hooks there are one line each and everything else lives here.

Switch and threading
---------------------
* The Data Capture page / capture_settings.json switch this file on or off as ``colony_scan`` (see
  capture_settings.py). Only the real file is gated: a call with its own ``out_path`` (a test)
  always records. The switch is checked BEFORE the dedup memory, so switching capture back on
  records the current state instead of treating it as a repeat of what was last seen.
* The message thread only does the cheap in-memory dedup and takes a private copy of the payload;
  the read-modify-write of the shared file (cross-process lock included, up to 4 client processes
  share it) runs on scan_writer's thread. The folder is created there too, never on the caller
  (see scan_paths.py for why a filesystem call on the message thread is not free).

Fixed along the way (all still guarded by tests):

1. Deduping against the WHOLE payload defeated the dedup almost every push, because PS_STATION_
   CARGO's own ``cargo``/``station_credits`` and PS_SHUTTLE_LEVELS' per-route ``source_qty``/
   ``destination_qty`` tick continuously: see ``_station_setup_identity`` /
   ``_shuttle_network_identity``, which exclude those volatile fields from the comparison while
   still storing them in the row that DOES get written.
2. The read-modify-write had no cross-process lock, and two clients writing this same file close
   together silently clobbered one another (it once went from ~70 rows to 1): ``scan_file_lock``.
3. The write itself used to run on the network thread and made docking lag: ``scan_writer``.
4. A jump gate's fuel is fed through the same PS_STATION_CARGO handler as a station's cargo; it is
   not a station and must not be recorded as one (see ``capture_station_setup``).
"""

from __future__ import annotations

import copy
import json
import logging
import os
import time

import capture_settings
import scan_paths
from scan_file_lock import locked
from scan_writer import dispatch
from scan_data import (  # core: the read side of the scan files, shared with the scan archive and the galaxy map
    load_colony_scan,
    colony_scan_path as _default_out_path,
)  # noqa: F401

logger = logging.getLogger(__name__)

# The capture_settings.py key for colony_scan.jsonl.
CAPTURE_KEY = "colony_scan"

# Sources that are appended (one row per observation) rather than upserted (one row per key,
# overwritten in place). Checked by SOURCE, not by whether a row's key is None -- unlike
# combat_scan_logger's append-only rows (which never carry a real key), colony_tick rows use a
# real key (the station_id) so a later query can still filter to one station's history; only the
# source name marks it as append-only.
_APPEND_ONLY_SOURCES = frozenset({"colony_tick", "shuttle_levels"})

# Per-process, in-memory only -- see combat_scan_logger._last_seen for why this isn't persisted:
# the goal is only ever "skip a back-to-back duplicate push while a tab stays open in ONE
# session," not deduplicating across restarts.
_last_seen: dict[tuple[str, str], object] = {}


def _capturing(out_path: str | None) -> bool:
    """Is colony capture on? Only the real file is gated (see capture_settings.py): a call with
    its own ``out_path`` -- a test -- always records."""
    return out_path is not None or capture_settings.enabled(CAPTURE_KEY)


def _snapshot(out_path: str | None, data):
    """A private copy of ``data`` for the writer thread -- the caller's dict is the live decoded
    payload (or the open shuttle dialog) and the UI may mutate it later. A test's inline write
    finishes before returning, so it can use the object as is."""
    return data if out_path is not None else copy.deepcopy(data)


def _changed_copy(cache_key: tuple[str, str], value, out_path: str | None):
    """None when ``value`` equals the last one seen for ``cache_key`` (a repeat); otherwise a
    private copy of it, which is also remembered as the new "last seen" -- the live object must
    never be what is remembered, or an in-place update to it would look like "unchanged"."""
    if cache_key in _last_seen and _last_seen[cache_key] == value:
        return None
    private = _snapshot(out_path, value)
    _last_seen[cache_key] = private
    return private


def _read_existing_rows(path: str) -> tuple[dict[tuple[str, str], dict], list[dict]]:
    """Splits the file into ``(keyed_rows, passthrough_rows)`` -- see _APPEND_ONLY_SOURCES.
    ``keyed_rows`` is every upsertable row, safely replaced in place by (source, key).
    ``passthrough_rows`` is every append-only row, kept verbatim in original file order -- these
    must never be collapsed into a single dict slot (see combat_scan_logger._read_existing_rows's
    own docstring for the real bug this exact pattern already caused once elsewhere: multiple
    distinct append-only observations silently colliding on one key and destroying all but the
    last)."""
    keyed: dict[tuple[str, str], dict] = {}
    passthrough: list[dict] = []
    if not os.path.exists(path):
        return keyed, passthrough
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(row, dict):
                continue
            if row.get("source") in _APPEND_ONLY_SOURCES:
                passthrough.append(row)
            else:
                keyed[(row.get("source", ""), row.get("key", ""))] = row
    return keyed, passthrough


def _write_rows(path: str, keyed_rows: dict[tuple[str, str], dict],
                 passthrough_rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in passthrough_rows:
            f.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        for sort_key in sorted(keyed_rows):
            f.write(json.dumps(keyed_rows[sort_key], sort_keys=True, default=str) + "\n")


def _upsert_now(source: str, key: str, data: dict, captured_at: float, context: dict,
                out_path: str | None) -> None:
    """Writer-thread side. ``captured_at`` is when the observation was made, not when the writer
    got to it."""
    path = out_path or _default_out_path()
    scan_paths.ensure_dir(path)
    with locked(path):
        keyed_rows, passthrough_rows = _read_existing_rows(path)
        keyed_rows[(source, key)] = {
            "source": source,
            "key": key,
            "captured_at": captured_at,
            "context": context,
            "data": data,
        }
        _write_rows(path, keyed_rows, passthrough_rows)


def _append_now(source: str, key: str, data: dict, captured_at: float, context: dict,
                out_path: str | None) -> None:
    path = out_path or _default_out_path()
    scan_paths.ensure_dir(path)
    with locked(path):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "source": source,
                "key": key,
                "captured_at": captured_at,
                "context": context,
                "data": data,
            }, sort_keys=True, default=str) + "\n")


def _upsert(source: str, key: str, data, context: dict | None, out_path: str | None) -> None:
    dispatch(out_path, _upsert_now, source, key, data, time.time(), dict(context or {}), out_path)


def _append(source: str, key: str, data, context: dict | None, out_path: str | None) -> None:
    dispatch(out_path, _append_now, source, key, data, time.time(), dict(context or {}), out_path)


def _record_colony_tick(station_id, colony: dict, income_history: list,
                         context: dict | None, out_path: str | None) -> None:
    """Appends one compact (population, food, growth/loss, this-tick's real resource-delivery
    quantities) sample per distinct observation -- deliberately APPEND, unlike record_colony_data's
    own upsert, since the whole point here is keeping every tick-over-tick reading rather than just
    the latest. ``colony`` is COLONY_DATA's own ``colony`` sub-dict; ``income_history``'s newest
    entry (index 0, confirmed against a real payload) is that SAME tick's actual per-resource sold
    quantities -- e.g. real rations delivered, the direct driver of growth/loss.

    Population only ever moves by ONE tick per COLONY_DATA response (``population`` vs.
    ``prev_population``), so a single snapshot can't show a growth/shrink history -- this is what
    lets a later comparison actually see it, PROVIDED the Colony tab gets reopened across multiple
    real colony ticks (every ~2 real hours, see game_rules.yaml's seconds_per_tick) over time, not
    just once. Skipped when identical to the last sample recorded for this station in THIS PROCESS
    -- reopening the same tab without a tick having passed shouldn't grow the file with a
    duplicate reading."""
    try:
        key = str(station_id)
        newest_tick = (income_history or [{}])[0] if income_history else {}
        sample = {
            "population": colony.get("population"),
            "prev_population": colony.get("prev_population"),
            "food": colony.get("food"),
            "max_food": colony.get("max_food"),
            "index": colony.get("index"),
            "projected_growth": colony.get("projected_growth"),
            "projected_loss": colony.get("projected_loss"),
            "projected_starving": colony.get("projected_starving"),
            "tick_income": newest_tick,
        }
        private = _changed_copy(("colony_tick", key), sample, out_path)
        if private is None:
            return
        _append("colony_tick", key, private, context, out_path)
    except Exception:
        logger.exception("colony_scan_logger: failed to record colony tick")


def record_colony_data(data: dict, context: dict | None = None,
                        out_path: str | None = None) -> None:
    """COLONY_DATA -- population/food/growth + the real per-resource demand-curve constants.

    ``data`` is the raw, already-JSON-decoded server payload (must carry ``station_id``, or it's
    skipped -- there's nothing to key an upsert on otherwise). Skips the write entirely when this
    payload is identical to the last one captured for this station in THIS PROCESS, same "don't
    rewrite the file for a repeat observation" dedup as combat_scan_logger (the Colony tab can
    re-push this repeatedly while it stays open)."""
    try:
        if not _capturing(out_path) or not isinstance(data, dict):
            return
        station_id = data.get("station_id")
        if station_id is None:
            return
        key = str(station_id)
        colony = data.get("colony")
        if isinstance(colony, dict):
            _record_colony_tick(station_id, colony, colony.get("income_history") or [],
                                 context, out_path)
        private = _changed_copy(("colony_data", key), data, out_path)
        if private is None:
            return
        _upsert("colony_data", key, private, context, out_path)
    except Exception:
        logger.exception("colony_scan_logger: failed to record colony data")


def _station_setup_identity(data: dict) -> dict:
    """``data`` with ``cargo``/``station_credits`` stripped -- used ONLY as the comparison value
    for the dedup, never written anywhere. ``cargo`` is a station's live resource stock, and
    ``station_credits`` its live credit balance -- both change as it buys/sells/produces even when
    nothing about its modules/caps/reserves/workers has changed; comparing the raw payload
    verbatim would defeat the whole point of deduping (see this module's own docstring for the real
    lag this caused)."""
    return {k: v for k, v in data.items() if k not in ("cargo", "station_credits")}


def record_station_setup(station_id, data: dict, context: dict | None = None,
                          out_path: str | None = None) -> None:
    """PS_STATION_CARGO -- equipped modules, resource caps/reserves, current cargo stock, workers,
    hull, station credits. ``station_id`` is resolved by the caller from the open station overlay
    (the payload itself doesn't reliably carry one) -- skipped if that's unavailable, since there's
    nothing to key an upsert on otherwise. Same identical-repeat dedup as record_colony_data."""
    try:
        if not _capturing(out_path) or station_id is None or not isinstance(data, dict):
            return
        key = str(station_id)
        if _changed_copy(("station_setup", key), _station_setup_identity(data), out_path) is None:
            return
        _upsert("station_setup", key, _snapshot(out_path, data), context, out_path)
    except Exception:
        logger.exception("colony_scan_logger: failed to record station setup")


def _shuttle_network_identity(snapshot: dict) -> dict:
    """``snapshot`` with each route's ``source_qty``/``destination_qty`` stripped -- used ONLY as
    the comparison value for the dedup, never written anywhere. Those two fields are live transfer
    levels that change on essentially every PS_SHUTTLE_LEVELS poll while the shuttle dialog stays
    open, even when the route configuration itself hasn't changed at all; comparing the raw
    snapshot verbatim would defeat the whole point of deduping (see this module's own docstring
    for the real lag this caused)."""
    routes = snapshot.get("routes")
    if not isinstance(routes, list):
        return snapshot
    stripped_routes = [
        {k: v for k, v in route.items() if k not in ("source_qty", "destination_qty")}
        if isinstance(route, dict) else route
        for route in routes
    ]
    return {**snapshot, "routes": stripped_routes}


def _record_shuttle_levels_sample(station_id, routes: list, context: dict | None,
                                   out_path: str | None) -> None:
    """Appends one compact (route id -> source_qty/destination_qty) sample per distinct
    observation -- deliberately APPEND, unlike record_shuttle_network's own upsert, since the
    whole point here is keeping a real time series of levels rather than just the latest. This is
    what lets a later tool see whether a resource is genuinely backing up or draining over time,
    now that the upsert no longer rewrites the file for a pure levels change. Skipped when
    identical to the last sample recorded for this station in THIS PROCESS (a steady-state levels
    poll with nothing actually moving shouldn't grow the file either)."""
    try:
        key = str(station_id)
        levels = [
            {"route_id": route.get("id"), "resource_key": route.get("resource_key"),
             "source_qty": route.get("source_qty"), "destination_qty": route.get("destination_qty")}
            for route in (routes or []) if isinstance(route, dict)
        ]
        private = _changed_copy(("shuttle_levels", key), levels, out_path)
        if private is None:
            return
        _append("shuttle_levels", key, private, context, out_path)
    except Exception:
        logger.exception("colony_scan_logger: failed to record shuttle levels sample")


def record_shuttle_network(station_id, dialog: dict, context: dict | None = None,
                            out_path: str | None = None) -> None:
    """PS_SHUTTLE_DATA / PS_SHUTTLE_LEVELS -- the orbital shuttle network dialog's current state
    for one hub station. ``dialog`` is the client's own ``self._ps_shuttle_dialog`` (or an
    equivalent dict) -- only the fields that matter for offline comparison are pulled out of it
    (stations, resources, routes -- including whatever source_qty/destination_qty
    set_ps_shuttle_levels has merged in -- station_limits, transfer_limit,
    transfer_interval_seconds), not the dialog's own UI/scroll/focus state.

    Called from BOTH the initial PS_SHUTTLE_DATA load and every later PS_SHUTTLE_LEVELS update.
    The route CONFIG (upserted here) is only rewritten when something other than a route's live
    quantities changes -- see ``_shuttle_network_identity`` -- to avoid rewriting the whole
    snapshot on every levels poll; the quantities themselves are still captured as a genuine time
    series via ``_record_shuttle_levels_sample``, appended every call regardless."""
    try:
        if not _capturing(out_path) or station_id is None or not isinstance(dialog, dict):
            return
        key = str(station_id)
        routes = dialog.get("routes", [])
        _record_shuttle_levels_sample(station_id, routes, context, out_path)
        snapshot = {
            "stations": dialog.get("stations", []),
            "resources": dialog.get("resources", []),
            "routes": routes,
            "station_limits": dialog.get("station_limits", {}),
            "transfer_limit": dialog.get("transfer_limit"),
            "transfer_interval_seconds": dialog.get("transfer_interval_seconds"),
        }
        if _changed_copy(("shuttle_network", key), _shuttle_network_identity(snapshot),
                         out_path) is None:
            return
        _upsert("shuttle_network", key, _snapshot(out_path, snapshot), context, out_path)
    except Exception:
        logger.exception("colony_scan_logger: failed to record shuttle network")


# ── The hooks Client.py calls ────────────────────────────────────────────────────────────────
# ``win`` is the SolarSystemWindow. Each one never raises (a logging failure must not break the
# handler it is called from) and does nothing at all while the capture switch is off.

def _layout_system(win) -> str:
    # self._layout is only ever set once a real layout has loaded; a defensive getattr keeps
    # these best-effort even in the (otherwise unreachable in live play) case that it has not.
    layout = getattr(win, "_layout", None) or {}
    return layout.get("system_name", "")


def capture_colony_data(win, data) -> None:
    """Client.set_ps_colony_data: persist a COLONY_DATA payload, stale or not -- every real one
    is worth keeping for offline comparison against colony_tool's plan, even one the UI itself is
    about to discard because another station's overlay is open now."""
    try:
        if not _capturing(None) or not isinstance(data, dict):
            return
        overlay = getattr(win, "_station_overlay", None)
        overlay_data = (overlay.get("data") or {}) if overlay is not None else {}
        context = {"system_name": _layout_system(win)}
        open_sid = overlay_data.get("attached_station_id")
        if open_sid is not None and data.get("station_id") == open_sid:
            context["station_name"] = overlay_data.get("station_name", "")
            context["planet_id"] = overlay_data.get("planet_id")
        record_colony_data(data, context)
    except Exception:
        logger.exception("colony_scan_logger: capture_colony_data failed")


def capture_station_setup(win, overlay_data: dict, data) -> None:
    """Client.set_ps_station_cargo: persist a PS_STATION_CARGO payload for the station whose
    overlay is open. ``overlay_data`` is that overlay's ``data`` dict -- the station id is resolved
    from it because the payload doesn't reliably carry one. jump_gates.py also feeds a jump gate's
    fuel through this same handler (dressed up as a one-item "cargo" list) so it can reuse the
    Storage tab's renderer; that isn't a real station and must not be recorded as one, and its
    ``attached_station_id`` is the gate's own key, not a station_id colony_scan data should ever
    be merged against."""
    try:
        if not _capturing(None) or not overlay_data or overlay_data.get("jump_gate_key"):
            return
        record_station_setup(
            overlay_data.get("attached_station_id"), data,
            {"system_name": _layout_system(win),
             "station_name": overlay_data.get("station_name", ""),
             "planet_id": overlay_data.get("planet_id")})
    except Exception:
        logger.exception("colony_scan_logger: capture_station_setup failed")


def capture_shuttle_network(win, station_id, dialog) -> None:
    """Client.set_ps_shuttle_data / set_ps_shuttle_levels: persist the open shuttle dialog's
    route config and live levels."""
    try:
        if not _capturing(None):
            return
        record_shuttle_network(station_id, dialog, {"system_name": _layout_system(win)})
    except Exception:
        logger.exception("colony_scan_logger: capture_shuttle_network failed")

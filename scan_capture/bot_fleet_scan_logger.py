"""bot_fleet_scan_logger.py — persist every owned trade bot's live state (haul orders included)
for offline auditing against colony_tool's planned bot assignments.

Why this exists
----------------
Auditing "are my bots actually running the routes the plan intended" would otherwise mean
manually target-clicking every single trade bot, one at a time, across every character/account —
exactly the slow, manual process this module exists to replace. It doesn't need to: the server
already pushes a periodic RC_BOT_STATE for EVERY bot the player owns (not just a selected one —
see protocol.py's own ``_on_rc_bot_state`` docstring), and the client already accumulates the
fullest known snapshot for each into ``self._rc_bots[bot_pid]`` (ship_type, nickname, state,
system_name, cargo, equipped, credits, stasis_phase, and -- the one that matters here --
haul_orders). This module just persists that same dict to disk instead of only keeping it in
memory, so simply having the client logged in for a short while on EACH character/account
naturally captures every bot's current orders with zero manual per-bot inspection.

One file (scan_data/bot_fleet_scan.jsonl), upserted by bot_pid so repeated pushes while the client
stays open refresh a bot's row in place rather than growing the file unbounded -- a bot's current
haul orders are a live configuration, not a time series worth keeping every reading of.

A SECOND, append-only file (scan_data/bot_leg_times.jsonl) captures actual leg durations: every
time a bot's ``system_name`` changes between two pushes, that's a real, observed arrival -- append
(bot_pid, from_system, to_system, arrived_at). The gap between two consecutive arrivals for the
same bot is that whole leg's real elapsed time (travel plus however long it stayed docked trading
before departing again) -- the one thing a single snapshot can never show, and the only way to
check colony_tool's assumed route timing against what a bot is actually achieving. This only
starts accumulating from whenever this code first runs, though -- there's no way to reconstruct a
leg that already happened before the first push this process saw.

``context`` carries the owning character (``self._username``) at capture time, since the entire
point is telling apart bots across multiple accounts/characters -- a bot_pid alone doesn't say
whose fleet it belongs to.

``record_bot_state`` is a best-effort, protocol.py-adjacent hook: it must never raise, since the
RC_BOT_STATE handler still has to work regardless of whether logging it to disk succeeds — same
contract as combat_scan_logger.record_ship_specs / colony_scan_logger.record_colony_data.

Two real bugs fixed this session, worth keeping in mind for any future recorder added here:

1. The dedup check originally compared the WHOLE payload, including ``bot_cargo_used`` and
   ``credits`` -- both of which change almost continuously while a bot is actively hauling. That
   meant the "skip if unchanged" check almost never fired in practice, so nearly every periodic
   push triggered a full read-parse-rewrite of the entire file -- synchronously, on the network
   message-handling thread. This is the exact bug combat_scan_logger.py already had to fix once
   (see its own docstring) for the identical reason (SHIP_SPECS_DATA's ticking
   ``current_energy``); a real player-reported lag spike (a profiler showing a multi-second
   ``network_drain`` stall) traced back to this. Fixed by excluding those two volatile fields from
   the identity comparison (see ``_bot_state_identity``) while still storing them in the row that
   DOES get written.
2. The read-modify-write itself had no cross-process locking, despite up to 4 separate client
   processes (one per logged-in character) all writing this same shared file whenever their own
   bots push updates. Two writes close together silently clobber one another -- proven this
   session when a sibling file (colony_scan.jsonl) went from ~70 rows to 1 this way. Fixed by
   wrapping the read-modify-write critical section in ``scan_file_lock.locked()``.
3. Even with (1) cutting write frequency down, a real write still did its lock-acquire-read-write
   cycle synchronously on the network thread -- a player reported real, felt lag specifically on
   docking (a station push, not a bot one, but the same file-write pattern). Fixed by handing the
   actual write off to scan_writer's background thread -- record_bot_state still does its cheap
   dedup check inline, but the file I/O itself never blocks the caller.
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
from scan_writer import submit
from scan_data import (  # core: the read side of the scan files, shared with the scan archive and the galaxy map
    load_bot_fleet_scan,
    bot_fleet_scan_path as _default_out_path,
    read_bot_fleet_rows as _read_existing_rows,
)  # noqa: F401

logger = logging.getLogger(__name__)

# Per-process, in-memory only -- see combat_scan_logger._last_seen for why this isn't persisted:
# the goal is only ever "skip a back-to-back duplicate push," not deduplicating across restarts.
_last_seen: dict[str, object] = {}

# Per-process, in-memory: the last system_name seen for each bot_pid, so a real transition can be
# told apart from a repeat push. Deliberately NOT persisted -- restarting the client just means the
# first push after restart establishes a fresh baseline rather than logging a (possibly huge, spanning
# the client's downtime) fake "leg" from whatever system was last known before it closed.
_last_system: dict[str, str] = {}


def _unchanged(key: str, data) -> bool:
    if key in _last_seen and _last_seen[key] == data:
        return True
    _last_seen[key] = data
    return False


def _bot_state_identity(data: dict) -> dict:
    """``data`` with ``bot_cargo_used``/``credits`` stripped -- used ONLY as the comparison value
    for _unchanged(), never written anywhere. Both tick almost every push while a bot is actively
    hauling even when nothing about its route/gear/state has changed; comparing the raw payload
    verbatim would defeat the whole point of deduping (see this module's own docstring for the
    real lag this caused)."""
    return {k: v for k, v in data.items() if k not in ("bot_cargo_used", "credits")}


def _write_rows(path: str, rows: dict[str, dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for key in sorted(rows):
            f.write(json.dumps(rows[key], sort_keys=True, default=str) + "\n")


def _default_leg_path() -> str:
    return scan_paths.path("bot_leg_times.jsonl")


def _append_leg(path: str, row: dict) -> None:
    scan_paths.ensure_dir(path)
    with locked(path):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def _record_leg_transition(key: str, system_name, context: dict | None,
                            leg_path: str | None) -> None:
    """Appends one (from_system, to_system, arrived_at) row when ``system_name`` genuinely
    changed since the last push THIS PROCESS saw for this bot. The very first sighting of a bot
    only establishes a baseline -- there's no real "from" system yet, so nothing is appended for
    it (see _last_system's own docstring for why a client restart also doesn't fake a leg)."""
    if not system_name:
        return
    prev = _last_system.get(key)
    _last_system[key] = system_name
    if prev is None or prev == system_name:
        return
    path = leg_path or _default_leg_path()
    row = {
        "bot_pid": key,
        "from_system": prev,
        "to_system": system_name,
        "arrived_at": time.time(),
        "context": dict(context or {}),
    }
    submit(_append_leg, path, row)


def _write_bot_state(path: str, key: str, data: dict, context: dict | None) -> None:
    scan_paths.ensure_dir(path)
    with locked(path):
        rows = _read_existing_rows(path)
        rows[key] = {
            "key": key,
            "captured_at": time.time(),
            "context": dict(context or {}),
            "data": data,
        }
        _write_rows(path, rows)


def record_bot_state(bot_pid, data: dict, context: dict | None = None,
                      out_path: str | None = None, leg_path: str | None = None) -> None:
    """Upsert one trade bot's current full snapshot (``sw._rc_bots[bot_pid]``), keyed by
    ``bot_pid``. Skipped if ``bot_pid`` is falsy -- nothing to key an upsert on otherwise. Skips
    the snapshot write entirely when byte-identical to the last one captured for this bot in THIS
    PROCESS (RC_BOT_STATE is a periodic push -- most of them repeat nothing new). The leg-time
    transition check (see _record_leg_transition) runs regardless of that dedup -- a system change
    always means the payload differs anyway, but the two are independent concerns.

    The Data Capture page can switch the two files off separately ("bot_fleet", "bot_legs";
    see capture_settings.py) -- only for the real files: a call with its own ``out_path`` /
    ``leg_path`` (a test) always records. While leg times are off the bot's last known system is
    still tracked, so switching them back on never logs a fake leg across the gap."""
    try:
        if not bot_pid or not isinstance(data, dict):
            return
        key = str(bot_pid)
        if leg_path is not None or capture_settings.enabled("bot_legs"):
            _record_leg_transition(key, data.get("system_name"), context, leg_path)
        elif data.get("system_name"):
            _last_system[key] = data["system_name"]
        if out_path is None and not capture_settings.enabled("bot_fleet"):
            return              # before _unchanged(): switching back on writes the current state
        if _unchanged(key, _bot_state_identity(data)):
            return
        path = out_path or _default_out_path()
        # A private copy: ``data`` is the live sw._rc_bots[bot_pid] dict, which the next push
        # updates in place while the writer thread may still be serialising it.
        submit(_write_bot_state, path, key, copy.deepcopy(data), context)
    except Exception:
        logger.exception("bot_fleet_scan_logger: failed to record bot state")


def load_bot_leg_times(path: str | None = None) -> list[dict]:
    """Every captured system-to-system leg observation, in file order. Best-effort: a missing/
    unreadable file just means nothing's been captured yet, not a crash."""
    try:
        p = path or _default_leg_path()
        if not os.path.exists(p):
            return []
        rows = []
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(row, dict):
                    rows.append(row)
        return rows
    except Exception:
        logger.exception("bot_fleet_scan_logger: failed to load bot leg times")
        return []



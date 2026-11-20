"""station_log_capture.py — persist PS_LOGS rows (the station Logs tab) for offline analysis.

Each row is a free-text, server-formatted line ("Ver-A4 (TB-ExampleAlt3) docked.",
"Ver-A3 (TB-ExampleAlt) bought 4,683 Space Corn for 4,683 credits.") tied to a stable
per-station ``id`` — see station_logs_client.py's PS_LOGS_REQUEST/set_station_logs.
The client only ever keeps the visible page in memory (capped at 1000 rows, and
wiped whenever the search query changes), so nothing durable existed before this —
``record_station_log_rows`` is the thin, best-effort I/O wrapper set_station_logs
calls on every reply, so a single visit to a station's Logs tab (auto-paginated to
its full history — see station_logs_client._station_logs_tick) permanently
captures that station's whole activity, not just whatever's on screen at the time.

A log entry is an immutable historical fact (the server never edits or deletes a
past row), so this is append-only rather than the read-modify-write-whole-file
upsert scan_logger.py/station_id_logger.py use — those cover mutable/re-scannable
data, this doesn't need to. An in-memory ``(station_id, id)`` set (populated from
the file once, then kept current) makes every repeat sighting — the auto-backfill
naturally re-requests pages, and a search reply can overlap the default view — an
O(1) skip instead of a full rewrite, which matters here since auto-pagination can
call this dozens of times in a row while backfilling one station.
"""

from __future__ import annotations

import csv
import logging
import os
import threading

import capture_settings
import scan_paths
from scan_writer import submit
from scan_data import (  # core: the read side of the scan files, shared with the scan archive and the galaxy map
    load_station_log_rows,
    station_logs_path as _default_out_path,
)  # noqa: F401

logger = logging.getLogger(__name__)

FIELDNAMES = ("station_id", "id", "time", "text")

_lock = threading.Lock()
_seen: "set[tuple[str, str]] | None" = None
_seen_path: str | None = None


def _load_seen(path: str) -> "set[tuple[str, str]]":
    if not os.path.exists(path):
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        return {(r["station_id"], r["id"]) for r in csv.DictReader(f)}


def record_station_log_rows(station_id, rows: list, out_path: str | None = None) -> None:
    """Append every not-yet-seen row in ``rows`` to the CSV, keyed by (station_id, id).

    ``rows`` is the already-JSON-decoded ``rows`` list from a PS_LOGS reply --
    ``[{"id": ..., "time": ..., "text": ...}, ...]``. Entries missing a usable
    ``id`` are skipped. Best-effort -- logs and swallows on failure, since the
    Logs tab still has to work regardless of whether persisting it succeeds.

    In production (``out_path`` None) this only copies each row's three text fields and queues
    the rest for scan_writer's thread: the first call of a session reads the whole (2 MB and
    growing) CSV to learn which rows it already has, and a freeze-watchdog dump caught the
    message thread 1.5 s inside that read. A test (explicit ``out_path``) runs it inline.
    """
    try:
        if out_path is None and not capture_settings.enabled("station_logs"):
            return
        sid = str(station_id)
        snapshot = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_id = str(row.get("id", "") or "")
            if row_id:
                snapshot.append((row_id, str(row.get("time", "") or ""),
                                 str(row.get("text", "") or "")))
        if not snapshot:
            return
        if out_path is None:
            submit(_record_now, sid, snapshot, None)
        else:
            _record_now(sid, snapshot, out_path)
    except Exception:
        logger.exception("station_log_capture: failed to record station log rows")


def _record_now(sid: str, snapshot: list, out_path: "str | None") -> None:
    global _seen, _seen_path
    try:
        path = out_path or _default_out_path()
        with _lock:
            if _seen is None or _seen_path != path:
                _seen = _load_seen(path)
                _seen_path = path
            new_rows = []
            for row_id, row_time, row_text in snapshot:
                key = (sid, row_id)
                if key in _seen:
                    continue
                _seen.add(key)
                new_rows.append({
                    "station_id": sid,
                    "id": row_id,
                    "time": row_time,
                    "text": row_text,
                })
            if not new_rows:
                return
            scan_paths.ensure_dir(path)
            write_header = not os.path.exists(path)
            with open(path, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
                if write_header:
                    writer.writeheader()
                writer.writerows(new_rows)
    except Exception:
        logger.exception("station_log_capture: failed to record station log rows")


def _reset_cache_for_tests() -> None:
    """Drop the in-memory seen-set so a test's fresh temp file gets re-read."""
    global _seen, _seen_path
    with _lock:
        _seen, _seen_path = None, None



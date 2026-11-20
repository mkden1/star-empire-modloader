"""station_id_logger.py — persist RC_BOT_DOCK_TARGETS_DATA payloads for offline analysis.

Keeps one CSV row per unique (system, station_id) in scan_data/station_ids.csv.
The bot-hauling order editor's Dock dropdown hands over every station in a
system at once (not just the one you end up docking at), so opening that
dropdown once per system you have haul orders in captures the whole set —
this just persists it so a bot-order generator can resolve real station IDs
instead of asking the player to dock-and-export each one by hand.

A station's id-to-name mapping is a stable fact, not a time series, so a
repeat sighting overwrites its row in place rather than appending a
duplicate. ``record_stations`` is the thin, best-effort I/O wrapper
protocol.py calls — same contract as scan_logger.record_scan: it must never
raise, since the dock dropdown still has to work regardless of whether
logging it to disk succeeds.
"""

from __future__ import annotations

import csv
import logging
import os

import capture_settings
import scan_paths
from scan_writer import dispatch
from scan_data import (  # core: the read side of the scan files, shared with the scan archive and the galaxy map
    load_station_names,
    load_station_systems,
    station_ids_path as _default_out_path,
)  # noqa: F401

logger = logging.getLogger(__name__)

FIELDNAMES = ("system", "station_id", "name")


def _read_existing_rows(path: str) -> dict[tuple[str, str], dict]:
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {(r["system"], r["station_id"]): r for r in csv.DictReader(f)}


def record_stations(system_name: str, stations: list,
                     out_path: str | None = None) -> None:
    """Upsert every station in ``stations`` into the CSV, keyed by (system, station_id).

    ``stations`` is the already-JSON-decoded ``stations`` list from an
    RC_BOT_DOCK_TARGETS_DATA payload — ``[{"station_id": ..., "name": ...}, ...]``.
    Entries missing a usable ``station_id`` are skipped. Best-effort — logs
    and swallows on failure. In production the (id, name) pairs are copied here and the file
    read-modify-write is queued for scan_writer's thread, so it never waits on the disk.
    """
    try:
        if out_path is None and not capture_settings.enabled("station_ids"):
            return
        entries = []
        for station in stations:
            if not isinstance(station, dict):
                continue
            station_id = str(station.get("station_id", "") or "")
            if station_id:
                entries.append((station_id, str(station.get("name", "") or "")))
        dispatch(out_path, _upsert_stations, out_path, system_name, entries)
    except Exception:
        logger.exception("station_id_logger: failed to record stations")


def _upsert_stations(out_path: str | None, system_name: str, entries: list) -> None:
    try:
        path = out_path or _default_out_path()
        scan_paths.ensure_dir(path)
        rows = _read_existing_rows(path)
        for station_id, name in entries:
            rows[(system_name, station_id)] = {
                "system": system_name,
                "station_id": station_id,
                "name": name,
            }
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            for key in sorted(rows):
                writer.writerow(rows[key])
    except Exception:
        logger.exception("station_id_logger: failed to record stations")


def load_station_ids(csv_path: str | None = None) -> dict[str, dict[str, str]]:
    """Return ``{system: {station_name: station_id}}`` from the CSV.

    Lets a bot-order generator resolve a "planet - N" style station name to
    its real in-game station_id, replacing the PLACEHOLDER_<planet>_<instance>
    convention used while an ID hadn't been captured yet. Best-effort: a
    missing/unreadable file just means nothing's been captured yet, not a
    crash.
    """
    try:
        path = csv_path or _default_out_path()
        if not os.path.exists(path):
            return {}
        by_system: dict[str, dict[str, str]] = {}
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                system = row.get("system", "")
                name = row.get("name", "")
                station_id = row.get("station_id", "")
                if system and name and station_id:
                    by_system.setdefault(system, {})[name] = station_id
        return by_system
    except Exception:
        logger.exception("station_id_logger: failed to load station id index")
        return {}



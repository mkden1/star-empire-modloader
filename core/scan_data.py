"""scan_data.py - the READ side of the scan files (core).

The loaders that read what the recorders write into scan_data/ (planet scans, colony setups, shuttle routes, station
names, logs and profiles, the bot fleet and trade bot slots), with the constants and path helpers they need. They used
to live inside the recorder mods (scan_logger.py, colony_scan_logger.py ...), so the scan archive and the galaxy map
needed all those mods installed just to read files. Here they depend on ``scan_paths`` and the standard library only.

The recorder mods import these names back under their old ones, so ``from scan_logger import load_scanned_moons_by_system``
and the like keep working, and each recorder still writes to the very file its reader here reads (the writer's
``_default_out_path`` IS the ``*_path`` function here).
"""

from __future__ import annotations

import csv
import json
import logging
import os

import scan_paths

logger = logging.getLogger(__name__)


# ── the planet scans (planet_scans.csv, one row per planet or moon): from scan_logger.py ───────────────────

RESOURCE_KEYS = (
    "metal_ore", "silicon", "copper", "titanium",
    "gold", "oil", "wood", "space_corn",
    "promethium",                   # gas giants only: the atmospheric resource Promethium Extractors take
)


# Single source of truth for display labels -- shared by the scan-result
# chat log (protocol.py) and the galaxy map's system-info popup RESOURCES
# section (render_mixin.py) so the two never drift apart.
RESOURCE_LABELS = {
    "metal_ore":  "Metal Ore",
    "silicon":    "Silicon",
    "copper":     "Copper",
    "titanium":   "Titanium",
    "gold":       "Gold",
    "oil":        "Oil",
    "wood":       "Wood",
    "space_corn": "Space Corn",
    "promethium": "Promethium",
}


# A planet can hold up to three independently-built stations, a moon exactly one (colony_tool's
# game_rules.yaml: max_stations_per_planet). A scan reports extractor slots PER STATION, so what a
# body has available in total is that times the station count. The multiplication is applied
# wherever totals are SHOWN (the galaxy map's resource readouts, the scan archive) and never
# stored: the planet_scans.csv ``*_slots`` columns stay per-station, because colony_tool reads
# them as per-station deposit counts and does its own station arithmetic.
STATIONS_PER_PLANET = 3
STATIONS_PER_MOON = 1


def stations_for_body(is_moon: bool) -> int:
    """How many stations can be built on a body: 3 on a planet, 1 on a moon."""
    return STATIONS_PER_MOON if is_moon else STATIONS_PER_PLANET


def planet_scans_path() -> str:
    # Pure string work (scan_paths): no disk access on the caller's thread.
    return scan_paths.path("planet_scans.csv")


def load_scanned_planets_by_type(
        csv_path: str | None = None) -> dict[str, dict[str, str]]:
    """Return ``{system: {planet_name: planet_type}}`` from the CSV.

    PLANETS ONLY — a row with a non-empty ``parent_planet`` is a moon and is
    excluded, so the galaxy map's "SCANNED" readout (which counts against
    ``system_data``'s planet-only totals) never counts moon scans into a
    planet total.

    Used to seed that readout at startup, and as the base a live session's
    in-memory index is built on top of (see ``Client._scanned_by_system``)
    — reading the CSV fresh on every frame the readout is visible would be
    wasteful for what's otherwise a purely in-memory-updated count.
    Best-effort: a missing/unreadable file just means nothing is scanned
    yet, not a crash.
    """
    try:
        path = csv_path or planet_scans_path()
        if not os.path.exists(path):
            return {}
        by_system: dict[str, dict[str, str]] = {}
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                system = row.get("system", "")
                planet = row.get("planet_name", "")
                if system and planet and not row.get("parent_planet"):
                    by_system.setdefault(system, {})[planet] = (
                        row.get("planet_type", ""))
        return by_system
    except Exception:
        logger.exception("scan_logger: failed to load scanned planet index")
        return {}


def load_scanned_resources_by_system(
        csv_path: str | None = None) -> dict[str, dict[str, dict[str, int]]]:
    """Return ``{system: {body_name: {resource_key: extractor slots}}}``
    from the CSV, one entry per scanned body (planet or moon alike --
    unlike load_scanned_planets_by_type, a system's total resources aren't
    scoped to planets only).

    The slot counts are PER STATION, exactly as the scan reports them;
    multiply by ``stations_for_body`` for what a body offers in total.

    Kept per-body rather than pre-summed per system so a rescan (record_scan
    upserts one row per (system, planet_name) in place, it never appends a
    duplicate) replaces that body's own contribution instead of double-
    counting it into a running total -- callers sum across the per-body
    dicts themselves (see the galaxy map system-info popup's RESOURCES
    section).

    Used to seed a live session's in-memory index at startup (see
    ``Client._scanned_resources_by_system``), matching
    load_scanned_planets_by_type's own seed-then-keep-live pattern.
    Best-effort: a missing/unreadable file just means nothing scanned yet.
    """
    try:
        path = csv_path or planet_scans_path()
        if not os.path.exists(path):
            return {}
        by_system: dict[str, dict[str, dict[str, int]]] = {}
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                system = row.get("system", "")
                body = row.get("planet_name", "")
                if not system or not body:
                    continue
                resources = {}
                for key in RESOURCE_KEYS:
                    try:
                        slots = int(row.get(f"{key}_slots") or 0)
                    except (TypeError, ValueError):
                        slots = 0
                    if slots:
                        resources[key] = slots
                by_system.setdefault(system, {})[body] = resources
        return by_system
    except Exception:
        logger.exception("scan_logger: failed to load scanned resource index")
        return {}


def load_scanned_moons_by_system(
        csv_path: str | None = None) -> dict[str, set[str]]:
    """Return ``{system: {scanned moon names}}`` from the CSV.

    The moon counterpart to ``load_scanned_planets_by_type`` — a row IS
    included here exactly when it's excluded there (non-empty
    ``parent_planet``). No per-type breakdown: the galaxy map's readout
    only ever gets an aggregate "moon_count" from the server, not a
    per-type count the way it does for planets, so there's nothing to key
    the value on beyond membership.
    """
    try:
        path = csv_path or planet_scans_path()
        if not os.path.exists(path):
            return {}
        by_system: dict[str, set[str]] = {}
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                system = row.get("system", "")
                moon = row.get("planet_name", "")
                if system and moon and row.get("parent_planet"):
                    by_system.setdefault(system, set()).add(moon)
        return by_system
    except Exception:
        logger.exception("scan_logger: failed to load scanned moon index")
        return {}


# ── the bot fleet scan: from bot_fleet_scan_logger.py ──────────────────────────────────────────────────────

def bot_fleet_scan_path() -> str:
    # Pure string work (scan_paths): this runs on the message thread, which must never wait on
    # the disk -- see scan_paths' docstring. The folder is created by the writer, below.
    return scan_paths.path("bot_fleet_scan.jsonl")


def read_bot_fleet_rows(path: str) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(row, dict) and row.get("key"):
                rows[row["key"]] = row
    return rows


def load_bot_fleet_scan(path: str | None = None) -> list[dict]:
    """Every captured bot's latest snapshot, in key order -- the read side for an offline
    comparison tool. Best-effort: a missing/unreadable file just means nothing's been captured
    yet, not a crash."""
    try:
        p = path or bot_fleet_scan_path()
        return list(read_bot_fleet_rows(p).values())
    except Exception:
        logger.exception("bot_fleet_scan_logger: failed to load bot fleet scan")
        return []


# ── the colony station setups: from colony_scan_logger.py ──────────────────────────────────────────────────

def colony_scan_path() -> str:
    # Pure string work (scan_paths): no disk access on the caller's thread.
    return scan_paths.path("colony_scan.jsonl")


def load_colony_scan(path: str | None = None) -> list[dict]:
    """Every captured row, in file order -- the read side for an offline comparison tool.
    Best-effort: a missing/unreadable file just means nothing's been captured yet, not a crash."""
    try:
        p = path or colony_scan_path()
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
        logger.exception("colony_scan_logger: failed to load colony scan")
        return []


# ── the shuttle routes: from shuttle_route_capture.py ──────────────────────────────────────────────────────

def shuttle_route_path() -> str:
    # Pure string work (scan_paths): no disk access on the caller's thread.
    return scan_paths.path("shuttle_routes.csv")


def load_shuttle_target_station_ids(csv_path: str | None = None) -> set[str]:
    """Return the set of every station_id that is some hub's shuttle target.

    An empty set means either "nothing captured yet" or "no routes configured"
    -- see load_shuttle_routes_captured() to tell those apart when it matters.
    """
    try:
        path = csv_path or shuttle_route_path()
        if not os.path.exists(path):
            return set()
        with open(path, newline="", encoding="utf-8") as f:
            return {r["target_station_id"] for r in csv.DictReader(f)
                     if r.get("target_station_id")}
    except Exception:
        logger.exception("shuttle_route_capture: failed to load shuttle targets")
        return set()


def shuttle_data_has_been_captured(csv_path: str | None = None) -> bool:
    """Whether any shuttle dialog has ever been opened and captured -- lets a
    consumer distinguish "confirmed not on any shuttle route" from "not
    checked yet" for a station absent from load_shuttle_target_station_ids()."""
    return os.path.exists(csv_path or shuttle_route_path())


# ── the station names and systems: from station_id_logger.py ───────────────────────────────────────────────

def station_ids_path() -> str:
    # Pure string work (scan_paths): no disk access on the caller's thread.
    return scan_paths.path("station_ids.csv")


def load_station_names(csv_path: str | None = None) -> dict[str, str]:
    """Return ``{station_id: name}`` -- the reverse of load_station_ids's inner mapping.

    For a consumer (e.g. bot_activity.py) that only has a bare station_id (as
    station_logs.csv rows do) and wants a human-readable label. Best-effort: a
    missing/unreadable file just means nothing's been captured yet, not a crash.
    """
    try:
        path = csv_path or station_ids_path()
        if not os.path.exists(path):
            return {}
        with open(path, newline="", encoding="utf-8") as f:
            return {row["station_id"]: row["name"] for row in csv.DictReader(f)
                     if row.get("station_id") and row.get("name")}
    except Exception:
        logger.exception("station_id_logger: failed to load station name index")
        return {}


def load_station_systems(csv_path: str | None = None) -> dict[str, str]:
    """Return ``{station_id: system}`` -- which galaxy/system each station_id is in.

    Same shape and purpose as load_station_names(), just the "system" column
    instead of "name" -- lets a consumer (e.g. bot_activity.py) show which
    galaxy a station belongs to alongside its own name. Best-effort: a
    missing/unreadable file just means nothing's been captured yet, not a crash.
    """
    try:
        path = csv_path or station_ids_path()
        if not os.path.exists(path):
            return {}
        with open(path, newline="", encoding="utf-8") as f:
            return {row["station_id"]: row["system"] for row in csv.DictReader(f)
                     if row.get("station_id") and row.get("system")}
    except Exception:
        logger.exception("station_id_logger: failed to load station system index")
        return {}


# ── the station logs: from station_log_capture.py ──────────────────────────────────────────────────────────

def station_logs_path() -> str:
    # Pure string work (scan_paths): no disk access on the caller's thread.
    return scan_paths.path("station_logs.csv")


def load_station_log_rows(csv_path: str | None = None) -> list[dict]:
    """Return every persisted row as a list of dicts -- for an analysis/dashboard consumer.

    Best-effort: a missing/unreadable file just means nothing's been captured yet.
    """
    try:
        path = csv_path or station_logs_path()
        if not os.path.exists(path):
            return []
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        logger.exception("station_log_capture: failed to load station log rows")
        return []


# ── the trade bot slots: from trade_bot_slots_capture.py ───────────────────────────────────────────────────

def trade_bot_slots_path() -> str:
    # Pure string work (scan_paths): no disk access on the caller's thread.
    return scan_paths.path("trade_bot_slots.csv")


def load_trade_bot_slots(csv_path: str | None = None) -> dict[str, dict]:
    """Return ``{username: {"slots_used": int, "slots_total": int, "updated_at": int}}``.

    Best-effort: a missing/unreadable file just means nothing's been captured yet.
    """
    try:
        path = csv_path or trade_bot_slots_path()
        if not os.path.exists(path):
            return {}
        result = {}
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                uname = row.get("username", "")
                if not uname:
                    continue
                try:
                    result[uname] = {
                        "slots_used": int(row.get("slots_used", 0) or 0),
                        "slots_total": int(row.get("slots_total", 0) or 0),
                        "updated_at": int(row.get("updated_at", 0) or 0),
                    }
                except (TypeError, ValueError):
                    continue
        return result
    except Exception:
        logger.exception("trade_bot_slots_capture: failed to load trade bot slots")
        return {}


# ── the station profiles (one JSON file per station): from station_profile.py ──────────────────────────────

PROFILE_DIR_NAME = "station_profiles"
STAT_KEYS =("Cycle Output", "Cycle Time", "Cycle Input", "Production")


def compact_cargo(rows) -> dict:
    """PS_STATION_CARGO's rows as ``{"resources": {key: stock}, "modules": [...]}``. A resource row without an
    ``item:`` key is a raw stack whose stock is ``cargo_size``; one with an ``item:`` key counts ``quantity``. Anything
    else is an equipped module: its count is ``quantity`` and its cycle stats are kept for the production maths."""
    resources: dict = {}
    modules: list = []
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        key = str(row.get("item_key") or "")
        if not key:
            continue
        if row.get("item_category") == "resource":
            field = "quantity" if key.startswith("item:") else "cargo_size"
            try:
                resources[key] = resources.get(key, 0) + int(row.get(field, 1))
            except (TypeError, ValueError):
                pass
            continue
        stats = row.get("stats") if isinstance(row.get("stats"), dict) else {}
        try:
            count = int(row.get("quantity", 0) or 0)
        except (TypeError, ValueError):
            count = 0
        modules.append({"item_key": key, "name": str(row.get("display_name") or key), "quantity": count,
                        "stats": {k: stats[k] for k in STAT_KEYS if k in stats}})
    return {"resources": resources, "modules": modules}


def load_profiles(directory: "str | None" = None) -> dict:
    """Every saved profile as ``{station id: profile}``. Unreadable files are skipped."""
    directory = directory or scan_paths.path(PROFILE_DIR_NAME)
    out = {}
    try:
        names = os.listdir(directory)
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as handle:
                profile = json.load(handle)
            out[str(profile["station_id"])] = profile
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out

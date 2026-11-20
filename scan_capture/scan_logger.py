"""scan_logger.py — persist PLANET_SCAN_RESULT payloads for offline analysis.

Keeps one CSV row per unique (system, planet) in scan_data/planet_scans.csv,
grouped for analysis via the leading ``system`` column (sort/filter/pivot on
it in a spreadsheet or ``pandas.groupby``). A planet's resources, colony
modifiers and orbit are stable facts, not a time series, so rescanning a
planet overwrites its row in place rather than appending a duplicate.

``scan_result_row`` is pure (payload dict in, CSV row dict out) and unit
tested. ``record_scan`` is the thin, best-effort I/O wrapper Client.py
calls — it must never raise, since a scan is real gameplay data the chat
log always displays regardless of whether logging it to disk succeeds.
"""

from __future__ import annotations

import csv
import logging
import os

import capture_settings
import scan_paths
from scan_writer import dispatch
from target_info import planet_orbit_period_seconds
from scan_data import (  # core: the read side of the scan files, shared with the scan archive and the galaxy map
    RESOURCE_KEYS,
    RESOURCE_LABELS,
    STATIONS_PER_PLANET,
    STATIONS_PER_MOON,
    stations_for_body,
    load_scanned_planets_by_type,
    load_scanned_resources_by_system,
    load_scanned_moons_by_system,
    planet_scans_path as _default_out_path,
)  # noqa: F401

logger = logging.getLogger(__name__)


MODIFIER_CATEGORIES = ("Gravity", "Temperature", "Atmosphere", "Geology", "Ecology")

FIELDNAMES = (
    ["system", "planet_name", "planet_type", "parent_planet",
     "colonization_index", "orbit_distance", "orbit_period_seconds",
     "orbit_speed", "orbit_angle_seed"]
    + [f"{key}_slots" for key in RESOURCE_KEYS]
    + [f"{cat.lower()}_setting" for cat in MODIFIER_CATEGORIES]
    + [f"{cat.lower()}_penalty_pct" for cat in MODIFIER_CATEGORIES]
    + ["extra_modifiers"]
)


def scan_result_row(system_name: str, data: dict,
                     orbit_radius: float | None = None,
                     orbit_speed: float | None = None,
                     orbit_angle_seed: float | None = None,
                     parent_planet: str | None = None) -> dict:
    """Build one flat CSV row from a PLANET_SCAN_RESULT payload's ``data``.

    ``data`` is the already-JSON-decoded payload passed to
    ``_on_planet_scan_result`` (before it gets turned into chat_log lines),
    so this reads the same source values the player sees on screen.
    ``orbit_radius``/``orbit_speed``/``orbit_angle_seed`` come from the
    layout's matching planet entry (the scan payload itself has no orbit
    info) — distance is the body's orbit_radius (sun for a planet, parent
    for a moon, same meaning as the TARGET panel's Orbit Dist);
    ``orbit_period_seconds`` is a convenience value derived from
    orbit_speed, while orbit_speed/orbit_angle_seed are kept raw too so a
    body's position at any moment can be reconstructed exactly via the
    server's own formula: angle(t) = orbit_angle_seed + orbit_speed * t,
    where t is server-elapsed seconds (see galaxy_export.py's exported
    ``server_elapsed`` snapshot).
    ``parent_planet`` is the name of the planet a moon orbits — blank for
    an ordinary planet.
    """
    res  = data.get("resources", {}) or {}
    ext  = data.get("extractors", {}) or {}
    mods = data.get("colonization", []) or []
    by_category = {m.get("category_label"): m for m in mods if isinstance(m, dict)}

    row = {
        "system":              system_name,
        "planet_name":         data.get("planet_name", ""),
        "planet_type":         data.get("planet_type", ""),
        "parent_planet":       parent_planet or "",
        "colonization_index":  data.get("colonization_index", ""),
        "orbit_distance":      orbit_radius if orbit_radius is not None else "",
        "orbit_period_seconds": (
            planet_orbit_period_seconds(orbit_speed)
            if orbit_speed is not None else ""),
        "orbit_speed":         orbit_speed if orbit_speed is not None else "",
        "orbit_angle_seed":    (
            orbit_angle_seed if orbit_angle_seed is not None else ""),
    }
    for key in RESOURCE_KEYS:
        row[f"{key}_slots"] = ext.get(key, 0) if res.get(key, 0) else 0

    extra = []
    for category, m in by_category.items():
        if category in MODIFIER_CATEGORIES:
            row[f"{category.lower()}_setting"] = m.get("setting_label", "")
            row[f"{category.lower()}_penalty_pct"] = m.get("penalty_pct", 0)
        else:
            extra.append(
                f"{category}: {m.get('setting_label', '')} "
                f"(-{m.get('penalty_pct', 0)}%)")
    for category in MODIFIER_CATEGORIES:
        row.setdefault(f"{category.lower()}_setting", "")
        row.setdefault(f"{category.lower()}_penalty_pct", "")
    row["extra_modifiers"] = "; ".join(extra)
    return row


def _read_existing_rows(path: str) -> dict[tuple[str, str], dict]:
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {(r["system"], r["planet_name"]): r for r in csv.DictReader(f)}


_warned_unknown: set = set()


def _warn_unknown_resources(data: dict) -> None:
    """Log, once per key, a resource a scan reports that RESOURCE_KEYS has no column for. Such a
    resource is shown in the scan's chat lines but would otherwise silently never reach the
    archive or the map -- this is how a wrongly-guessed key name (or a resource the game adds
    later) shows up in the client log instead of just looking like "0 slots"."""
    try:
        keys = set(data.get("resources") or ()) | set(data.get("extractors") or ())
        for key in sorted(keys - set(RESOURCE_KEYS) - _warned_unknown):
            _warned_unknown.add(key)
            logger.warning("scan_logger: scan result has resource %r, which has no column in "
                           "planet_scans.csv (add it to RESOURCE_KEYS)", key)
    except Exception:
        logger.exception("scan_logger: failed to check scan resource keys")


def record_scan(system_name: str, data: dict,
                 orbit_radius: float | None = None,
                 orbit_speed: float | None = None,
                 orbit_angle_seed: float | None = None,
                 parent_planet: str | None = None,
                 out_path: str | None = None) -> None:
    """Upsert one scan into the CSV, keyed by (system, planet_name).

    Best-effort — logs and swallows on failure. In production the row is built here and the
    file read-modify-write is queued for scan_writer's thread, so it never waits on the disk.
    """
    try:
        if out_path is None and not capture_settings.enabled("planet_scans"):
            return
        _warn_unknown_resources(data)
        row = scan_result_row(
            system_name, data, orbit_radius, orbit_speed,
            orbit_angle_seed, parent_planet)
        dispatch(out_path, _upsert_scan_row, out_path, row)
    except Exception:
        logger.exception("scan_logger: failed to record scan")


def _upsert_scan_row(out_path: str | None, row: dict) -> None:
    try:
        path = out_path or _default_out_path()
        scan_paths.ensure_dir(path)
        rows = _read_existing_rows(path)
        rows[(row["system"], row["planet_name"])] = row
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            for key in sorted(rows):
                writer.writerow(rows[key])
    except Exception:
        logger.exception("scan_logger: failed to record scan")



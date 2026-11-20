"""shuttle_route_capture.py — persist orbital shuttle network wiring (PS_SHUTTLE_DATA).

A station with the "orbital_shuttle_network" module equipped can auto-haul
resources to/from other owned stations without any trade bot docking there
at all (see Client.open_ps_shuttle_dialog/set_ps_shuttle_data) -- so a station
with zero bot dock activity in station_logs.csv isn't necessarily neglected,
it may be fully served by this network instead. The dialog is per-hub and
only populated when the PLAYER opens that hub station's Shuttle tab (there's
no passive push), so this file only ever covers whichever hub stations
someone has actually opened since this capture was added -- a station absent
from here just means its shuttle network (if any) hasn't been checked yet,
never "confirmed no network."

One row per (hub_station_id, target_station_id, resource) route, upserted --
a station's shuttle wiring is a config choice, not a time series.
"""

from __future__ import annotations

import csv
import logging
import os
import time

import capture_settings
import scan_paths
from scan_writer import dispatch
from scan_data import (  # core: the read side of the scan files, shared with the scan archive and the galaxy map
    load_shuttle_target_station_ids,
    shuttle_data_has_been_captured,
    shuttle_route_path as _default_out_path,
)  # noqa: F401

logger = logging.getLogger(__name__)

FIELDNAMES = ("hub_station_id", "target_station_id", "target_station_name",
              "resource", "direction", "enabled", "updated_at")


def _read_existing(path: str) -> dict[tuple, dict]:
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {(r["hub_station_id"], r["target_station_id"], r["resource"]): r
                for r in csv.DictReader(f)}


def record_shuttle_routes(hub_station_id, routes: list, out_path: str | None = None) -> None:
    """Upsert every route in ``routes`` (a PS_SHUTTLE_DATA reply's "routes" list).

    Each route dict is expected to carry a target station id (under either
    "target_station_id" or "lunar_station_id"), a resource identifier (under
    "resource_name", "resource_key" or "resource_id"), and "direction"/
    "enabled". A route missing a usable target id is skipped. Best-effort --
    logs and swallows, since the Shuttle dialog still has to work regardless
    of whether persisting it succeeds. In production the rows are built here and the file
    read-modify-write is queued for scan_writer's thread, so it never waits on the disk.
    """
    try:
        if out_path is None and not capture_settings.enabled("shuttle_routes"):
            return
        hub = str(hub_station_id)
        new_rows = {}
        for route in routes:
            if not isinstance(route, dict):
                continue
            target = route.get("target_station_id", route.get("lunar_station_id"))
            if target is None:
                continue
            target = str(target)
            resource = str(route.get("resource_name",
                           route.get("resource_key", route.get("resource_id", ""))) or "")
            new_rows[(hub, target, resource)] = {
                "hub_station_id": hub,
                "target_station_id": target,
                "target_station_name": str(route.get("target_station_name",
                                           route.get("lunar_station_name", "")) or ""),
                "resource": resource,
                "direction": str(route.get("direction", "") or ""),
                "enabled": "1" if route.get("enabled", True) else "0",
                "updated_at": str(int(time.time())),
            }
        if new_rows:
            dispatch(out_path, _upsert_routes, out_path, new_rows)
    except Exception:
        logger.exception("shuttle_route_capture: failed to record shuttle routes")


def _upsert_routes(out_path: str | None, new_rows: dict) -> None:
    try:
        path = out_path or _default_out_path()
        scan_paths.ensure_dir(path)
        rows = _read_existing(path)
        rows.update(new_rows)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            for key in sorted(rows):
                writer.writerow(rows[key])
    except Exception:
        logger.exception("shuttle_route_capture: failed to record shuttle routes")



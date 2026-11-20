"""galaxy_export.py — combined system/gate/scan JSON export for route math.

Meant to be handed directly to an LLM (attach or paste) to answer questions
like "which nearby systems have plenty of gold" or "how long is the route
from planet A to planet B" — a flat CSV/table makes a human read every row
to find that; a compact JSON with adjacency, gate positions, and each
planet's orbital elements alongside the resource data lets a reasoning
system do the cross-referencing and the trigonometry itself.

``build_export_json`` is pure (client-side snapshots in, JSON string out)
and unit tested. ``export_data`` is the thin I/O wrapper the galaxy map's
"VIEW SCANS" button calls alongside generating the HTML dashboard — it
must never raise, since it runs from a UI click handler in a live session.
"""

from __future__ import annotations

import csv
import json
import logging
import math
import os

import modloader

from scan_dashboard import planet_records

logger = logging.getLogger(__name__)

# Every system's sun sits at this fixed local-coordinate origin — see
# Client.py's _init_galaxy_map: "World center is (125000, 125000) — gate
# offsets from center give directions," also used there as the client's own
# default whenever a gate's own x/y is missing. Confirmed empirically too
# (every live-visited system's captured sun_x/sun_y comes out exactly this).
WORLD_CENTER = 125000.0


def _gate_bearing_and_distance(gate: dict, sun_x: float, sun_y: float):
    gx = float(gate.get("x", 0.0))
    gy = float(gate.get("y", 0.0))
    distance = math.hypot(gx - sun_x, gy - sun_y)
    bearing_deg = math.degrees(math.atan2(gy - sun_y, gx - sun_x)) % 360.0
    return distance, bearing_deg


def build_export_json(visited_systems: dict, planet_rows: list[dict],
                       server_elapsed: float | None = None) -> str:
    """Build the combined export.

    ``visited_systems`` is ``Client._visited_systems``-shaped: per system,
    a "sun_x"/"sun_y" position and a "warp_gates" list of
    {name, x, y, target_system}. Connectivity (``connects_to``) is derived
    from each gate's target_system — symmetric, since a gate works both
    ways, so a connection appears on both endpoints even if only one side
    has been visited (the far side just won't have its own gate list
    until visited too, only whatever connectivity was learned indirectly).

    Each entry also carries "planet_types" ({type: count}), "moon_count", and
    "hazard_rating" — populated the moment a system is entered (or restored
    from the EXPLORED_SYSTEMS login snapshot, see
    protocol._on_explored_systems), with NO scan required. Real bug found
    this session: this was captured on ``visited_systems`` but never
    actually surfaced anywhere in this export — ``payload["planets"]`` only
    ever reflected planet_scans.csv (a real in-game scan), so a system
    you'd flown through but never individually scanned had no planet data
    here at all, even though the client already knew its full type
    breakdown (and hazard rating). Surfaced below as its own
    ``explored_systems`` map (aggregate counts, not named planets — that's
    all EXPLORED_SYSTEMS/a live visit ever gives you) so a consumer can
    show "this galaxy has 2 Terra, 1 Barren, hazard 20" for every explored
    system, not just the ones that also happen to have a resource scan.

    "sun_x"/"sun_y" are only present for a system actually warped into
    THIS session; a system known only from the EXPLORED_SYSTEMS login
    snapshot (every system the account has ever visited, across all past
    sessions — see protocol._on_explored_systems) has gates/target_system
    but no captured sun position. Every system's sun sits at the same
    fixed local-coordinate origin regardless (WORLD_CENTER, confirmed both
    by Client.py's own comment and empirically), so a missing sun still
    resolves there rather than to a meaningless (0, 0) — but each system
    carries ``sun_measured`` so a consumer can tell "confirmed this
    session" apart from "assumed via the world-center convention."

    Gate distance/bearing use the exact same atan2/hypot convention as the
    TARGET panel's planet Orbit Dist/Bearing and gate Sun Dist/Bearing
    lines, so they compose directly with a planet's orbit_radius,
    orbit_speed and orbit_angle_seed (see scan_logger.py) via the server's
    own position formula — included as ``server_elapsed`` so a planet's
    position at any moment, not just scan time, can be reconstructed:
    angle(t) = orbit_angle_seed + orbit_speed * t.
    """
    systems: dict[str, dict] = {}
    connects_to: dict[str, set[str]] = {}
    explored_totals: dict[str, dict] = {}

    for name, info in (visited_systems or {}).items():
        if not isinstance(info, dict):
            continue
        types: dict[str, int] = {}
        for ptype, count in (info.get("planet_types") or {}).items():
            try:
                count_i = int(count)
            except (TypeError, ValueError):
                continue
            if count_i > 0:
                types[str(ptype)] = count_i
        try:
            moon_count = max(0, int(info.get("moon_count", 0) or 0))
        except (TypeError, ValueError):
            moon_count = 0
        try:
            hazard_rating = max(0, int(info.get("hazard_rating", 0) or 0))
        except (TypeError, ValueError):
            hazard_rating = 0
        if types or moon_count:
            explored_totals[name] = {"planet_types": dict(sorted(types.items())),
                                      "moon_count": moon_count,
                                      "hazard_rating": hazard_rating}
        # EXPLORED_SYSTEMS (the login-restore snapshot covering every system
        # this account has ever visited, not just this session — see
        # protocol._on_explored_systems) carries gate/planet-type counts but
        # NOT the sun's position; only a system actually warped into THIS
        # session gets sun_x/sun_y (Client.py's live layout capture). A
        # missing sun still resolves to WORLD_CENTER (every system's sun
        # sits there, live-measured or not), but ``sun_measured`` says
        # whether that came from this session's own data or the constant,
        # so a consumer can tell "confirmed" apart from "assumed."
        sun_x_raw = info.get("sun_x")
        sun_y_raw = info.get("sun_y")
        sun_measured = sun_x_raw is not None and sun_y_raw is not None
        sun_x = float(sun_x_raw) if sun_measured else WORLD_CENTER
        sun_y = float(sun_y_raw) if sun_measured else WORLD_CENTER
        gates = []
        for gate in info.get("warp_gates", []) or []:
            target = str(gate.get("target_system", "") or "").strip()
            gate_pos_known = (
                gate.get("x") is not None and gate.get("y") is not None)
            if gate_pos_known:
                distance, bearing_deg = _gate_bearing_and_distance(
                    gate, sun_x, sun_y)
            else:
                distance, bearing_deg = None, None
            gates.append({
                "name": gate.get("name", ""),
                "target_system": target,
                "distance_from_sun": distance,
                "bearing_deg": bearing_deg,
            })
            if target:
                connects_to.setdefault(name, set()).add(target)
                connects_to.setdefault(target, set()).add(name)
        systems[name] = {
            "sun": {"x": sun_x, "y": sun_y},
            "sun_measured": sun_measured,
            "gates": gates,
        }

    for name, neighbors in connects_to.items():
        systems.setdefault(name, {
            "sun": {"x": WORLD_CENTER, "y": WORLD_CENTER},
            "sun_measured": False,
            "gates": [],
        })
        systems[name]["connects_to"] = sorted(neighbors)
    for name, entry in systems.items():
        entry.setdefault("connects_to", [])

    payload = {
        "server_elapsed": server_elapsed,
        "systems": dict(sorted(systems.items())),
        "planets": planet_records(planet_rows),
        "explored_systems": dict(sorted(explored_totals.items())),
    }
    return json.dumps(payload, indent=2)


def _default_csv_path() -> str:
    # scan_data/ is shared with colony_tool/ (a sibling repo) and lives one level above
    # the client's folder, at the true project root.
    root_dir = str(modloader.PROJECT_DIR)
    return os.path.join(root_dir, "scan_data", "planet_scans.csv")


def _default_export_path() -> str:
    # scan_data/ is shared with colony_tool/ (a sibling repo) and lives one level above
    # the client's folder, at the true project root.
    root_dir = str(modloader.PROJECT_DIR)
    return os.path.join(root_dir, "scan_data", "export_for_claude.json")


def export_data(visited_systems: dict, server_elapsed: float | None = None,
                 csv_path: str | None = None,
                 out_path: str | None = None) -> None:
    """Write the combined JSON export. Best-effort — logs and swallows."""
    try:
        csv_path = csv_path or _default_csv_path()
        out_path = out_path or _default_export_path()
        if not os.path.exists(csv_path):
            rows = []
        else:
            with open(csv_path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        payload = build_export_json(visited_systems, rows, server_elapsed)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(payload)
    except Exception:
        logger.exception("galaxy_export: failed to write export")

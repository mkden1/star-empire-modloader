"""autopilot_planner.py - the route as gate-to-gate legs, for the autopilot banner's ETA (pure, no pygame).

The banner's "ETA m:ss" (autopilot_eta.py, simulated in autopilot_route_sim.py) needs the route broken
into legs: in each system on the way, where the ship appears (the gate paired with the one it came
through) and which gate it flies to. ``build_legs`` reads that from the client's explored systems;
``ShipStats`` carries the ship's effective numbers.

Units are the game's: px, px/s, px/s^2, degrees.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

WARP_TRANSITION_S = 0.25           # SOLAR_WARP -> new layout: median 0.20 s, p90 0.27 s over 561 client-logged warps


@dataclass(frozen=True)
class Leg:
    """One gate crossing on the route.

    ``entry`` is where the ship appears in this leg's system (the paired gate; None for the leg the
    ship is already flying), ``exit`` is the gate it flies to, and ``final`` marks the last gate of
    the route, where the autopilot stops inside the gate and then warps.
    """
    entry: "Optional[tuple[float, float]]"
    exit: "tuple[float, float]"
    final: bool
    core: float = 40.0             # the exit gate's core circle: a mid-route leg warps on entering it (ETA sim)


@dataclass(frozen=True)
class ShipStats:
    accel: float                   # effective acceleration (thrust limiter applied)
    turn_rate: float               # effective turn rate, degrees/s (turning limiter applied)
    max_speed: float


# -- route geometry -----------------------------------------------------------------------------------

def _gate_xy(gate: dict) -> "tuple[float, float]":
    return float(gate["x"]), float(gate["y"])


def _gate_to(gates, system: str):
    """The gate in ``gates`` whose target is ``system`` (names compared stripped)."""
    want = str(system).strip()
    for g in gates or ():
        if str(g.get("target_system", "")).strip() == want:
            return g
    return None


def _gate_core(gate: dict, core_min: float) -> float:
    """Client.py ``_ap_flythrough_core_radius``: the gate's own ``radius`` (40, the renderer's default, when
    it has none), at least ``core_min``."""
    try:
        radius = float(gate.get("radius", 40.0))
    except (TypeError, ValueError):
        return 40.0
    return max(core_min, radius) if math.isfinite(radius) else 40.0


def build_legs(current_exit: "tuple[float, float]", route: Sequence[str], current_system: str,
               visited: dict, *, current_core: float = 40.0, core_min: float = 25.0) -> "list[Leg]":
    """The route as legs. ``route`` is the client's ``_autopilot_route`` ([next system, ...,
    destination]); the ship is in ``current_system`` heading for ``current_exit``. Later legs use the
    explored systems' stored gates: the ship appears at the gate that leads back to the system it came
    from, and leaves by the gate that leads to the next one. Geometry that is missing ends the list
    early, which means the estimate cannot be made (it needs the last gate)."""
    route = [str(r).strip() for r in route]
    if not route:
        return []
    n = len(route)
    legs = [Leg(None, (float(current_exit[0]), float(current_exit[1])), n == 1, float(current_core))]
    previous = str(current_system).strip()
    for k in range(1, n):
        system = route[k - 1]
        info = visited.get(system)
        if info is None:
            lower = {str(name).strip().lower(): name for name in visited}
            info = visited.get(lower.get(system.lower(), ""))
        gates = (info or {}).get("warp_gates") or []
        entry_gate = _gate_to(gates, previous)
        exit_gate = _gate_to(gates, route[k])
        if entry_gate is None or exit_gate is None:
            break
        try:
            legs.append(Leg(_gate_xy(entry_gate), _gate_xy(exit_gate), k == n - 1, _gate_core(exit_gate, core_min)))
        except (KeyError, TypeError, ValueError):
            break
        previous = system
    return legs

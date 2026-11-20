"""autopilot_route_sim.py - fly the rest of a solar route on the game's own flight plan (pure, no pygame).

Since v0.5.82 the autopilot flies on ``flight_plan`` (the dev's time-optimal steering, shared with the trade
bots). This module is the simulation behind the banner's ETA (autopilot_eta.py): from the ship's real state
it flies each remaining leg through ``flight_plan.steer`` with the same rules ``SolarSystemWindow._ap_fly``
and the autopilot block in Client.py apply, so the estimate follows whatever the steering does:

  * a mid-route gate is a REACH leg into the gate's core circle. The real autopilot warps as soon as the
    ship's step crosses that circle, or, when the course misses it, at its closest approach (inside the
    450 activation circle and already moving away from the centre);
  * the last gate is a STOP leg: the ship halts inside half the activation circle, planned 120 from the
    gate's centre, and then warps;
  * every leg starts with the maneuver ``flight_plan.plan`` picks from the state the ship is in then
    (the live leg keeps the maneuver its pilot already chose);
  * a warp costs ``planner.WARP_TRANSITION_S`` and the ship appears at the paired gate keeping its velocity
    and nose (a warp arrival keeps the velocity; only the position changes).

Units are the game's: px, px/s, px/s^2, degrees. Nothing here touches the window, the disk or the network.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Callable, Optional, Sequence

import flight_plan
import autopilot_planner as planner
from autopilot_route import WARP_ACTIVATION_RADIUS

DT = 1.0 / 30.0                    # simulation step (30 Hz vs 60/120 Hz moved the old estimate 0.2% on average)
MAX_LEG_S = 1500.0                 # a simulated leg that has not ended by now has failed: no estimate
FINAL_RADIUS = WARP_ACTIVATION_RADIUS * 0.5     # Client.py: the last gate is stopped inside half the circle ...
FINAL_AIM = 120.0                               # ... planned this far from the gate's centre
PAUSE_EVERY = 96                   # steps between ``pause`` calls inside a leg (about a frame's worth of work)

State = "tuple[float, float, float, float, float]"      # x, y, vx, vy, nose angle


def _segment_hits(ax, ay, bx, by, cx, cy, r) -> bool:
    return flight_plan._segment_hits(ax, ay, bx, by, cx, cy, r)


def fly_leg(ship: "flight_plan.Ship", state, leg: "flight_plan.Leg", pilot: "flight_plan.Pilot",
            pause: "Optional[Callable[[], None]]" = None, max_s: "Optional[float]" = None, dt: float = DT):
    """Fly one leg with ``pilot``. Returns ``(seconds, end_state)``, or ``None`` when it never ended.

    REACH legs end where the real autopilot warps (see the module docstring); STOP legs end when the
    steering says it is done."""
    x, y, vx, vy, angle = state
    max_s = MAX_LEG_S if max_s is None else max_s
    reach = leg.mode == flight_plan.REACH
    if reach and _warp_due(x, y, vx, vy, leg, None):
        return 0.0, (x, y, vx, vy, angle)
    t = 0.0
    steps = 0
    while t < max_s:
        cmd = flight_plan.steer(x, y, vx, vy, angle, ship, leg, pilot, dt)
        if cmd.done:
            return t, (x, y, vx, vy, cmd.angle)
        angle = cmd.angle
        vx, vy = flight_plan.advance_velocity(vx, vy, angle, ship, cmd.thrust, cmd.brake, dt)
        px, py = x, y
        x += vx * dt
        y += vy * dt
        t += dt
        if reach and _warp_due(x, y, vx, vy, leg, (px, py)):
            return t, (x, y, vx, vy, angle)
        steps += 1
        if pause is not None and steps % PAUSE_EVERY == 0:
            pause()
    return None


def _warp_due(x, y, vx, vy, leg: "flight_plan.Leg", previous) -> bool:
    """Client.py ``_ap_flythrough_warp_due``: the ship's step crossed the gate's core, or it is inside the
    activation circle and already moving away from the centre. (For a step shorter than the activation circle
    the second rule already holds on the far side of any crossing, so the segment test below never decides
    alone; it is kept because that is how the game checks the core.)"""
    if previous is not None and _segment_hits(previous[0], previous[1], x, y, leg.x, leg.y, leg.radius):
        return True
    if math.hypot(x - leg.x, y - leg.y) <= leg.radius:
        return True
    if math.hypot(x - leg.x, y - leg.y) > WARP_ACTIVATION_RADIUS:
        return False
    return (x - leg.x) * vx + (y - leg.y) * vy > 0.0


def leg_for(route_leg: "planner.Leg") -> "flight_plan.Leg":
    """The flight_plan leg the autopilot flies for a route leg."""
    gx, gy = route_leg.exit
    if route_leg.final:
        return flight_plan.Leg(gx, gy, flight_plan.STOP, radius=FINAL_RADIUS, aim=FINAL_AIM)
    return flight_plan.Leg(gx, gy, flight_plan.REACH, radius=float(route_leg.core))


def estimate_route_time(legs: "Sequence[planner.Leg]", state, stats: "planner.ShipStats", *,
                        pilot: "Optional[flight_plan.Pilot]" = None,
                        pause: "Optional[Callable[[], None]]" = None,
                        warp_s: float = planner.WARP_TRANSITION_S) -> "Optional[float]":
    """Predicted seconds from ``state`` (the ship now, on legs[0]) until the route is finished, or None when
    ``legs`` does not reach the end of the route (a later system's gates are unknown) or a leg cannot be flown.

    ``pilot`` is the live leg's pilot (its maneuver and whether it is still fixing the course); a copy is
    used, so the caller's pilot is never moved. Later legs pick their maneuver the way the autopilot does,
    when they start. Each gate adds ``warp_s``."""
    if not legs or not legs[-1].final:
        return None
    ship = flight_plan.Ship(stats.accel, stats.turn_rate, stats.max_speed)
    state = tuple(float(v) for v in state)
    total = 0.0
    for index, route_leg in enumerate(legs):
        leg = leg_for(route_leg)
        if index == 0 and pilot is not None:
            leg_pilot = replace(pilot)
        else:
            chosen = flight_plan.plan(state[0], state[1], state[2], state[3], state[4], ship, leg)
            leg_pilot = flight_plan.Pilot(chosen.maneuver)
        flown = fly_leg(ship, state, leg, leg_pilot, pause)
        if flown is None:
            return None
        seconds, state = flown
        total += seconds + warp_s
        if route_leg.final:
            return total
        following = legs[index + 1]
        if following.entry is None:
            return None
        state = (following.entry[0], following.entry[1], state[2], state[3], state[4])
        if pause is not None:
            pause()
    return None

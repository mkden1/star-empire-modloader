"""target_info.py - the planet orbit figures the target panel shows (period of a lap, how far through it a body is).

The game's own target_display.py holds the target panel's text rules; these are ours: ``planet_orbit_period_seconds``
(a full lap from the server's angular speed), ``format_orbit_period`` (``1h 03m`` / ``42m 10s``) and
``planet_orbit_progress_pct`` (0-100 through the current lap), with ``planet_orbit_figures`` and ``gate_sun_figures``
that work out what the target panel's planet and gate branches show. Client.py imports them (one patched import) and the
panel calls them (a small patch there).
"""

from __future__ import annotations

import math


def planet_orbit_period_seconds(orbit_speed: float) -> float | None:
    """Return the time for one full lap, or ``None`` for a stationary body.

    ``orbit_speed`` is the server's angular rate in radians/second (see
    ``Client._refresh_orbit_angles``), so a full orbit is ``2*pi / speed``.
    """
    speed = abs(float(orbit_speed))
    return (2.0 * math.pi) / speed if speed > 0.0 else None


def format_orbit_period(seconds: float | None) -> str:
    """Render an orbit period as a compact ``1h 03m`` / ``42m 10s`` string."""
    if seconds is None:
        return "Stationary"
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def planet_orbit_progress_pct(orbit_angle_rad: float) -> float:
    """Return how far through the current lap a body is, as 0-100.

    ``orbit_angle_rad`` is expected to already be the body's live angle
    (``Client._orbit_angles``), which the client keeps wrapped to
    ``[0, 2*pi)`` — this wraps defensively too so a raw/unwrapped angle
    still produces a sane percentage.
    """
    return (float(orbit_angle_rad) % (2.0 * math.pi)) / (2.0 * math.pi) * 100.0


def planet_orbit_figures(planet: dict, orbit_angles: dict):
    """What the planet branch of the target panel shows about a body's orbit, as
    ``(period seconds, live angle, percent through the lap, orbit radius, bearing in degrees)``.

    ``orbit_radius`` is the distance from whatever the body orbits -- the sun for a planet, the parent planet for a moon.
    The bearing is in the SAME convention as the gate panel's Sun Dist / Bearing line (atan2/cos/sin), so a gate's fixed
    (dist, bearing) and a planet's (orbit_radius, bearing, orbit_speed) drop straight into a law-of-cosines projection
    without unit conversion."""
    period = planet_orbit_period_seconds(planet.get("orbit_speed", 0.0))
    angle = orbit_angles.get(planet.get("id"), 0.0)
    pct = planet_orbit_progress_pct(angle)
    radius = float(planet.get("orbit_radius", 0.0))
    bearing = math.degrees(angle) % 360.0
    return period, angle, pct, radius, bearing


def gate_sun_figures(layout, gate_x: float, gate_y: float):
    """A gate's distance from the sun and its bearing from it in degrees, as ``(distance, bearing)``.

    The same angle convention as planet orbits (atan2/cos/sin), so this composes directly with a planet's orbit_radius
    + live phase (see the planet Orbit line) to work out gate <-> planet distance at any future time, accounting for the
    planet's motion around the sun. ``layout`` is the solar system's layout dict (None while unknown)."""
    sun = layout.get("sun", {}) if layout else {}
    sun_x = float(sun.get("x", 0.0))
    sun_y = float(sun.get("y", 0.0))
    dist = math.hypot(gate_x - sun_x, gate_y - sun_y)
    bearing = math.degrees(math.atan2(gate_y - sun_y, gate_x - sun_x)) % 360.0
    return dist, bearing

"""autopilot_eta.py - an estimated time remaining on the route autopilot's banner.

The banner reads "Autopiloting to Gamma, 2 jumps left". With this module it reads "...  ·  ETA 1:42".

The estimate is a simulation, not a guess from distance and speed: from the ship's real position,
velocity and nose it flies the rest of the route on the game's own flight plan (``autopilot_route_sim``:
the dev's ``flight_plan.steer``, the same call ``SolarSystemWindow._ap_fly`` makes every frame),
including the swing after each warp, the final stop inside the last gate, and about a quarter of a second
per warp (the median client-observed SOLAR_WARP -> layout time over 561 warps was 0.20 s). The live leg
is flown with the maneuver its pilot has already chosen.

How it runs:

  * ``install`` wraps ``SolarSystemWindow._ap_fly`` from outside; after each autopilot frame the wrapper
    calls ``ensure``, which starts a small worker thread, "ap-eta", on the first frame of each leg,
  * the worker re-estimates from the live state every couple of seconds and publishes a deadline,
  * the banner counts down to that deadline between estimates, so it ticks smoothly and is
    corrected, not jumped, when a new estimate arrives; the deadline carries across warps so the
    text does not flicker while the next leg's first estimate is on its way,
  * the banner text comes from a wrapper around the module-level ``_format_autopilot_status`` in
    render_mixin, installed from outside, so the vanilla draw code is untouched.

Nothing here sends anything to the server or touches the disk, and it never changes what the autopilot does
(the wrapper returns the original answer untouched). It shows an estimate only for solar-system routes whose
later systems are all explored (their gates are what the estimate needs); otherwise the banner is
unchanged. When a route ends at its last gate the log records how long it took against the first estimate
(``autopilot_eta:`` lines), which is how to see how good the estimate is. Any error turns the estimate off
for the rest of the session.

History: until v0.5.82 this flew the old ``_ap_warp_approach_controls`` / ``_ap_final_warp_controls`` steering
through autopilot_planner; the dev replaced that with flight_plan.py and the estimate was ported to it.
"""

from __future__ import annotations

import functools
import inspect
import logging
import math
import threading
import time

import autopilot_planner as planner
import autopilot_route_sim as route_sim

logger = logging.getLogger(__name__)

REFRESH_S = 2.0                    # re-estimate this often
POLL_S = 0.25                      # how often the worker checks the route is still going
STALE_S = 15.0                     # an estimate nobody has refreshed for this long is hidden
MAX_WORKER_S = 1800.0
_YIELD_S = 0.002

_failed = False
_latest_lock = threading.Lock()
_latest: "tuple[str | None, float, float]" = (None, 0.0, 0.0)    # destination, deadline, set_at


def reset_for_tests() -> None:
    global _failed, _latest
    _failed = False
    _latest = (None, 0.0, 0.0)


def disable_for_tests() -> None:
    """Make ``ensure`` do nothing (tests of other things that fly a fake window)."""
    global _failed
    _failed = True


def has_failed() -> bool:
    return _failed


def format_eta(seconds: float) -> str:
    """1:42, 0:07, 1:02:03 (rounded up to a whole second)."""
    total = max(0, int(math.ceil(seconds)))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _publish(destination: str, seconds: float) -> None:
    global _latest
    now = time.monotonic()
    with _latest_lock:
        _latest = (destination, now + seconds, now)


def eta_text(remaining_route, now: "float | None" = None) -> str:
    """The countdown for the route the banner is showing, or "" when there is no usable estimate."""
    route = list(remaining_route or ())
    if not route:
        return ""
    now = time.monotonic() if now is None else now
    with _latest_lock:
        destination, deadline, set_at = _latest
    if destination is None or str(route[-1]).strip() != destination or now - set_at > STALE_S:
        return ""
    return format_eta(max(0.0, deadline - now))


# -- what the window is doing -----------------------------------------------------------------------

def route_context(win):
    """(key, system, route, target) for the solar route leg the ship is flying, or None when it is
    not on one (no route, a dungeon, or a window without the route state). ``key`` changes with every
    new leg and every new route."""
    route = getattr(win, "_autopilot_route", None)
    target = getattr(win, "_autopilot_target", None)
    lock = getattr(win, "_lock", None)
    if (not isinstance(route, list) or not route or target is None or lock is None
            or getattr(win, "_autopilot_route_dungeon_gate_id", None) is not None):
        return None
    with lock:
        layout = getattr(win, "_layout", None)
    if not layout or layout.get("is_dungeon") or layout.get("dungeon_floor"):
        return None
    system = str(layout.get("system_name", "")).strip()
    key = (system, round(float(target[0])), round(float(target[1])), tuple(route))
    return key, system, list(route), target


def ship_stats(win, accel: float, max_spd: float) -> "planner.ShipStats":
    limiter = max(0.0, min(100.0, float(win._turning_limiter))) / 100.0
    return planner.ShipStats(accel=float(accel), turn_rate=float(win._TURN_RATE) * limiter,
                             max_speed=float(max_spd))


class EtaState:
    def __init__(self, key, legs, destination, stats, trip):
        self.key = key
        self.legs = legs
        self.destination = destination
        self.stats = stats
        self.trip = trip
        self.stop = threading.Event()
        self.thread: "threading.Thread | None" = None


def _pause() -> None:
    time.sleep(_YIELD_S)


def _live_pilot(win):
    """The pilot ``_ap_fly`` keeps for the leg the ship is on (flight_plan.Pilot), or None."""
    pilot = getattr(win, "_ap_pilot", None)
    return pilot if hasattr(pilot, "maneuver") and hasattr(pilot, "redirecting") else None


def _worker(state: EtaState, win) -> None:
    global _failed
    began = time.monotonic()
    trip = state.trip
    try:
        while (not state.stop.is_set() and time.monotonic() - began < MAX_WORKER_S
               and getattr(win, "_autopilot", False)):
            here = (win._local_x, win._local_y, win._local_vx, win._local_vy, win._local_angle)
            total = route_sim.estimate_route_time(state.legs, here, state.stats, pilot=_live_pilot(win),
                                                  pause=_pause)
            if state.stop.is_set():
                break
            if total is not None:
                _publish(state.destination, total)
                if trip.get("first") is None:
                    trip["first"] = total
                    logger.info("autopilot_eta: route to %s, %d jump%s: estimated %.0fs",
                                state.destination, trip["jumps"], "" if trip["jumps"] == 1 else "s", total)
            waited = 0.0
            while (waited < REFRESH_S and not state.stop.is_set()
                   and getattr(win, "_autopilot", False)):
                if state.stop.wait(POLL_S):
                    break
                waited += POLL_S
        # The last gate warped (autopilot dropped itself, nobody cancelled it): the trip is over.
        if (state.legs and state.legs[0].final and not state.stop.is_set()
                and not getattr(win, "_autopilot", True) and trip.get("first") is not None):
            logger.info("autopilot_eta: arrived at %s after %.0fs (first estimate %.0fs, %d jump%s)",
                        state.destination, time.monotonic() - trip["started"], trip["first"],
                        trip["jumps"], "" if trip["jumps"] == 1 else "s")
    except Exception:
        _failed = True
        logger.exception("autopilot_eta: estimate failed; the ETA is off until the client restarts")


def ensure(win, accel: float, max_spd: float) -> None:
    """Make sure an estimate is being kept for the leg the ship is on. Cheap enough for every frame."""
    global _failed
    if _failed:
        return
    try:
        _ensure(win, accel, max_spd)
    except Exception:
        _failed = True
        logger.exception("autopilot_eta: could not start an estimate; the ETA is off until the client restarts")


def _core_radius(win) -> float:
    """The core circle the current gate is flown through (Client.py ``_ap_flythrough_core_radius``)."""
    method = getattr(win, "_ap_flythrough_core_radius", None)
    try:
        return float(method()) if callable(method) else 40.0
    except Exception:
        return 40.0


def _ensure(win, accel, max_spd) -> None:
    ctx = route_context(win)
    state = getattr(win, "_ap_eta_state", None)
    if ctx is None:
        if state is not None:
            state.stop.set()
            win._ap_eta_state = None
        return
    key, system, route, target = ctx
    if state is not None and state.key == key:
        return
    if state is not None:
        state.stop.set()
    visited = getattr(win, "_visited_systems", None)
    if not isinstance(visited, dict):
        return
    destination = str(route[-1]).strip()
    trip = getattr(win, "_ap_eta_trip", None)
    if trip is None or trip["destination"] != destination or len(route) > trip["last_len"]:
        trip = {"destination": destination, "started": time.monotonic(), "first": None,
                "jumps": len(route), "last_len": len(route)}
        win._ap_eta_trip = trip
    trip["last_len"] = len(route)
    legs = planner.build_legs(target, route, system, visited, current_core=_core_radius(win),
                              core_min=float(getattr(win, "_AP_GATE_CORE_MIN", 25.0)))
    state = EtaState(key, legs, destination, ship_stats(win, accel, max_spd), trip)
    win._ap_eta_state = state
    if len(legs) < len(route) or not legs[-1].final:
        return                                  # a later system's gates are unknown: no estimate
    state.thread = threading.Thread(target=_worker, args=(state, win), name="ap-eta", daemon=True)
    state.thread.start()


# -- the hook -----------------------------------------------------------------------------------------

def install(window_cls) -> bool:
    """Wrap ``window_cls._ap_fly`` (the autopilot's one steering call, called every frame it flies a leg) so
    each call also keeps the estimate going. The wrapper returns the original answer untouched and never
    raises. Returns False, and logs a warning, when the game has no such method or it no longer takes the
    ship's acceleration and top speed (this runs while Client.py is imported: it must not raise). Safe to
    call twice."""
    original = window_cls.__dict__.get("_ap_fly") or getattr(window_cls, "_ap_fly", None)
    if original is None:
        logger.warning("autopilot_eta: the game has no _ap_fly; the ETA is off")
        return False
    if getattr(original, "_ap_eta_wrapped", False):
        return False
    try:
        names = list(inspect.signature(original).parameters)
        accel_at, speed_at = names.index("accel"), names.index("max_spd")
    except (TypeError, ValueError):
        logger.warning("autopilot_eta: _ap_fly no longer takes accel and max_spd; the ETA is off")
        return False

    @functools.wraps(original)
    def fly(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        try:
            accel = kwargs["accel"] if "accel" in kwargs else args[accel_at - 1]
            max_spd = kwargs["max_spd"] if "max_spd" in kwargs else args[speed_at - 1]
        except (IndexError, KeyError):
            return result
        ensure(self, accel, max_spd)
        return result

    fly._ap_eta_wrapped = True
    window_cls._ap_fly = fly
    return True


# -- the banner ---------------------------------------------------------------------------------------

def install_banner(render_module) -> bool:
    """Append the estimate to the autopilot banner text. ``render_module`` is render_mixin; its
    module-level ``_format_autopilot_status`` is what the HUD calls. Safe to call twice."""
    original = render_module._format_autopilot_status
    if getattr(original, "_ap_eta_wrapped", False):
        return False

    @functools.wraps(original)
    def with_eta(remaining_route, route_destination="", target_name=""):
        text = original(remaining_route, route_destination, target_name)
        try:
            eta = eta_text(remaining_route)
        except Exception:
            eta = ""
        return f"{text}  ·  ETA {eta}" if eta else text

    with_eta._ap_eta_wrapped = True
    render_module._format_autopilot_status = with_eta
    return True

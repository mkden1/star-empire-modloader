"""planet_scan_capture.py - record each planet / moon scan the server answers, and keep the galaxy map's scan indexes live.

``capture_planet_scan`` runs just BEFORE the game's own ``_on_planet_scan_result`` (modloader hooks it onto the method by
name, from a one-line call at the end of Client.py), so the scan is on its way to scan_data/planet_scans.csv before the
game starts writing its chat lines, and stays recorded whatever the chat code does afterwards.

It repeats the guards the game's handler starts with (a payload that parses, a window to show it in, a scan that
succeeded) and then does two things with the payload:

* ``record_scan`` upserts the row in scan_data/planet_scans.csv, with the body's orbit as the solar window knows it;
* the three in-memory indexes the galaxy map's SCANNED readouts and system info popup read (``_scanned_by_system``,
  ``_scanned_moons_by_system``, ``_scanned_resources_by_system``, seeded from the same file by
  galaxy_map_scans.init_state) are updated, so the map never re-reads the file per frame.
"""

import json

import modloader
from scan_logger import record_scan

# protocol.py's own JSON decoder: install() copies it in from that module
_BORROWED = ("_loads",)
_loads = None


def capture_planet_scan(self, payload) -> None:
    """The scan reply the server just sent (``payload``, before the game's handler shows it in chat)."""
    try:
        data = _loads(payload)
    except (json.JSONDecodeError, KeyError, ValueError):
        return                                  # the game's handler logs a payload it cannot read
    window = self._solar_window
    if window is None or not data.get("ok"):
        return                                  # a failed scan has nothing to record
    layout = getattr(window, "_layout", None) or {}
    bodies = layout.get("planets", [])
    body = next((p for p in bodies if p.get("name") == data.get("planet_name")), None)
    parent_name = None
    if body and body.get("is_moon"):
        parent = next((p for p in bodies if p.get("id") == body.get("parent_planet_id")), None)
        parent_name = (parent or {}).get("name")
    system = getattr(window, "_last_solar_system_name", "") or "Unknown"
    record_scan(
        system, data,
        orbit_radius=(body or {}).get("orbit_radius"),
        orbit_speed=(body or {}).get("orbit_speed"),
        orbit_angle_seed=(body or {}).get("orbit_angle"),
        parent_planet=parent_name)
    res = data.get("resources", {}) or {}
    ext = data.get("extractors", {}) or {}

    if body and body.get("is_moon"):
        moons = getattr(window, "_scanned_moons_by_system", None)
        if moons is not None:
            moons.setdefault(system, set()).add(data.get("planet_name", ""))
    else:
        planets = getattr(window, "_scanned_by_system", None)
        if planets is not None:
            planets.setdefault(system, {})[data.get("planet_name", "")] = data.get("planet_type", "")
    # Resource extractor-slot counts, per scanned body (planet or moon alike) -- kept per-body rather than folded into
    # a running system total so a rescan (record_scan above upserts the CSV row in place) replaces this body's
    # contribution instead of double-counting it. The galaxy map's system-info popup sums across bodies itself; see
    # load_scanned_resources_by_system's own docstring for why the shape matches _scanned_by_system's.
    resources = getattr(window, "_scanned_resources_by_system", None)
    if resources is not None:
        resources.setdefault(system, {})[data.get("planet_name", "")] = {
            key: ext.get(key, 0) for key in res if res.get(key, 0)}


def install(target) -> "list[str]":
    """Hook the game's ``_on_planet_scan_result`` (``ProtocolMixin``)."""
    return modloader.install_methods(target, None, globals(), _BORROWED,
                                     before={"_on_planet_scan_result": capture_planet_scan})


# what test_mods_apply checks after every game update: (game module, game class, game method, the function that runs
# around it)
HOOKS = (("protocol", "ProtocolMixin", "_on_planet_scan_result", "capture_planet_scan"),)

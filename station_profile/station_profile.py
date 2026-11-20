"""station_profile.py - what a player station looked like the last time you were docked at it.

One small JSON file per station in ``scan_data/station_profiles/<station id>.json``, rewritten whenever you leave
a station (and, while you are docked, about every PROFILE_EVERY_S when its cargo changes, so a client closed while
docked still leaves something recent). It is the input of the STATIONS tab of the scan archive (station_overview.py).

Everything comes from what the client already holds for the docked station:

  * the dock payload (``_station_overlay["data"]``): name, owner, permissions, MOTD, switches, construction queue,
    trade-bay listing size, whatever other plain fields the server sent;
  * PS_STATION_CARGO state (``_ps_*``): resource stock, caps and reserves, credits, workers, hull use, and the
    equipped modules with their cycle stats (so production per day can be worked out later);
  * the Trade Manager's list for this station (requested once at dock, owner only: the same PS_TRADE_MANAGER_REQUEST
    the Trade Manager sends), ``None`` when it never arrived.

Unlike colony_scan.jsonl's ``station_setup`` row (rewritten only when modules or caps change, so its stock figures
can be months behind) a profile is a complete snapshot taken at the end of the visit, with ``seen_at``.

Writes go through scan_writer (a background thread); the capture on the game's thread is a handful of dict reads.
Switch: capture_settings "colony_scan" (Data Capture page: "Colony & station state"). ``install(SolarSystemWindow)`` wraps
``show_station_overlay`` (asks for the trade list), ``hide_station_overlay`` (the undock snapshot) and
``set_ps_station_cargo`` (the debounced one). Errors switch the capture off for the session.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time

import capture_settings
import scan_paths
from scan_writer import submit
from scan_data import (  # core: the read side of the scan files, shared with the scan archive and the galaxy map
    compact_cargo,
    load_profiles,
    PROFILE_DIR_NAME as DIR_NAME,
)  # noqa: F401
from station_bodies import body_of, same as _same   # core: shared with station_auto_name

logger = logging.getLogger(__name__)

PROFILE_VERSION = 1
PROFILE_EVERY_S = 60.0
KEY = "colony_scan"      # the Data Capture page's "Colony & station state" switch covers these files too

_failed = False
_last_cargo_write: dict = {}

# Dock-payload fields that are named in the profile itself (everything else plain goes under "overlay").
_NAMED = {"attached_station_id", "station_id", "station_name", "owner_name", "is_owner", "management_access",
          "public_dock", "dock_min_rank", "management_min_rank", "motd", "planet_id", "has_colony",
          "construction_projects", "blueprints", "ps_trade_inventory", "trade_inventory", "training_inventory",
          "jump_gate_key"}
_FLAGS = ("trade_enabled", "construction_enabled", "hangar_enabled", "fuel_access", "sell_to_colony")
_PROJECT_NAME_KEYS = ("display_name", "name", "blueprint_name", "item_name", "item_type")
_PROJECT_NUMBER_KEYS = ("quantity", "manhours_total", "manhours_done", "manhours_left", "eta_seconds",
                        "workers_assigned")


def _switch_off(where: str) -> None:
    global _failed
    if not _failed:
        logger.exception("station_profile: %s failed; station profiles are switched off for this session", where)
    _failed = True


def enabled() -> bool:
    return not _failed and capture_settings.enabled(KEY)


def reset_for_tests() -> None:
    global _failed
    _failed = False
    _last_cargo_write.clear()


def policies(data) -> dict:
    """The docking and management policy the permission dialogs show: public (docking only), a coalition rank
    key, or owner."""
    data = data or {}
    docking = "public" if data.get("public_dock") else (str(data.get("dock_min_rank") or "").strip().lower() or "owner")
    management = str(data.get("management_min_rank") or "").strip().lower() or "owner"
    return {"docking": docking, "management": management}


def _plain(value) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _project(row) -> dict:
    out = {}
    for key in _PROJECT_NAME_KEYS:
        if row.get(key):
            out["name"] = str(row[key])
            break
    for key in ("build_state", "waiting_for"):
        if row.get(key) is not None and _plain(row[key]):
            out[key] = row[key]
    for key in _PROJECT_NUMBER_KEYS:
        if isinstance(row.get(key), (int, float)):
            out[key] = row[key]
    return out


def build_profile(win, event: str) -> "dict | None":
    """The docked player station as a profile dict, or None when you are not at one of your own managed stations."""
    with win._lock:
        overlay = getattr(win, "_station_overlay", None)
        if not overlay or overlay.get("kind") != "player_station":
            return None
        data = dict(overlay.get("data") or {})
        cargo_rows = list(getattr(win, "_ps_station_cargo", None) or ())
        caps = dict(getattr(win, "_ps_resource_caps", None) or {})
        reserves = dict(getattr(win, "_ps_resource_reserves", None) or {})
        credits = getattr(win, "_ps_station_credits", None)
        workers_used = getattr(win, "_ps_workers_used", None)
        hull = (getattr(win, "_ps_hull_used", None), getattr(win, "_ps_hull_space", None))
        manager = dict(getattr(win, "_ps_trade_mgr", None) or {})
        layout = getattr(win, "_layout", None) or {}
        username = getattr(win, "_username", "")
        my_stations = list(getattr(win, "_my_stations_data", None) or ())
    station_id = data.get("attached_station_id", data.get("station_id"))
    if station_id is None or data.get("jump_gate_key"):
        return None
    contents = compact_cargo(cargo_rows)
    body = body_of(data, layout, my_stations, station_id)
    trades = None
    if _same(manager.get("station_id"), station_id) and isinstance(manager.get("config"), dict):
        trades = [{"category": k[0], "item_type": k[1], "buy_price": int(v.get("buy_price", 0)),
                   "sell_price": int(v.get("sell_price", 0)), "min_qty": int(v.get("min_qty", 0)),
                   "max_qty": int(v.get("max_qty", 0))} for k, v in manager["config"].items()]
        trades.sort(key=lambda r: (str(r["category"]), str(r["item_type"])))
    listing = data.get("ps_trade_inventory", data.get("trade_inventory"))
    projects = data.get("construction_projects")
    owner = data.get("owner_name", "")
    return {
        "version": PROFILE_VERSION, "station_id": str(station_id), "seen_at": time.time(), "event": event,
        "taken_by": username, "system_name": layout.get("system_name", ""),
        "station_name": data.get("station_name", ""), "owner_name": owner,
        "is_owner": bool(data.get("is_owner", owner == username)),
        "management_access": bool(data.get("management_access", False)),
        **policies(data),
        "motd": str(data.get("motd") or "")[:300],
        "planet_id": data.get("planet_id"), "has_colony": bool(data.get("has_colony", False)),
        "body_name": body["name"], "body_kind": body["kind"], "body_parent": body["parent"],
        "flags": {k: bool(data[k]) for k in _FLAGS if k in data},
        "overlay": {k: v for k, v in data.items() if k not in _NAMED and k not in _FLAGS and _plain(v)
                    and not (isinstance(v, str) and len(v) > 200)},
        "resources": contents["resources"], "modules": contents["modules"],
        "resource_caps": caps, "resource_reserves": reserves,
        "credits": credits, "workers_used": workers_used,
        "hull_used": hull[0], "hull_space": hull[1],
        "trade_entries": trades,
        "trade_listed": len(listing) if isinstance(listing, list) else None,
        "construction": ({"projects": [_project(r) for r in projects if isinstance(r, dict)],
                          "blueprints": len(data.get("blueprints") or [])}
                         if isinstance(projects, list) else None),
    }


def _file_name(station_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(station_id)) + ".json"


def _write(path: str, profile: dict) -> None:
    scan_paths.ensure_dir(path)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(profile, handle, separators=(",", ":"))
    os.replace(temp, path)


def record(profile: dict, out_dir: "str | None" = None) -> None:
    """Hand one profile to the writer thread (explicit ``out_dir``: tests, always recorded)."""
    directory = out_dir or scan_paths.path(DIR_NAME)
    submit(_write, os.path.join(directory, _file_name(profile["station_id"])), profile)


def capture(win, event: str, out_dir: "str | None" = None) -> bool:
    """Snapshot the docked station now. True if a profile was handed to the writer."""
    if out_dir is None and not enabled():
        return False
    profile = build_profile(win, event)
    if profile is None:
        return False
    if event == "cargo" and not (profile["resources"] or profile["modules"]):
        return False                     # the dock's own cargo push has not landed yet: nothing worth keeping
    record(profile, out_dir)
    _last_cargo_write[profile["station_id"]] = time.monotonic()
    return True


def on_cargo(win, now: "float | None" = None) -> bool:
    """A cargo push while docked: snapshot, at most once per PROFILE_EVERY_S per station."""
    with win._lock:
        overlay = getattr(win, "_station_overlay", None)
        data = (overlay or {}).get("data") or {}
        station_id = data.get("attached_station_id", data.get("station_id")) if overlay else None
    if station_id is None:
        return False
    now = time.monotonic() if now is None else now
    if now - _last_cargo_write.get(str(station_id), -1e9) < PROFILE_EVERY_S:
        return False
    return capture(win, "cargo")


def install(window_cls) -> bool:
    """Wrap ``show_station_overlay``, ``hide_station_overlay`` and ``set_ps_station_cargo``. Idempotent."""
    if window_cls.__dict__.get("_station_profile_installed"):
        return False
    missing = [n for n in ("show_station_overlay", "hide_station_overlay", "set_ps_station_cargo",
                           "request_ps_trade_manager") if not hasattr(window_cls, n)]
    if missing:
        logger.warning("station_profile: not installed, the window has no %s", ", ".join(missing))
        return False
    show, hide, cargo = window_cls.show_station_overlay, window_cls.hide_station_overlay, window_cls.set_ps_station_cargo

    def show_station_overlay(self, data, kind):
        result = show(self, data, kind)
        if enabled() and kind == "player_station" and isinstance(data, dict) and data.get("is_owner") \
                and data.get("management_access") and not data.get("jump_gate_key"):
            try:
                self.request_ps_trade_manager()     # the owner's trade list, for the profile taken when you leave
            except Exception:
                _switch_off("trade list request")
        return result

    def hide_station_overlay(self):
        if enabled():
            try:
                capture(self, "undock")
            except Exception:
                _switch_off("capture(undock)")
        return hide(self)

    def set_ps_station_cargo(self, data):
        result = cargo(self, data)
        if enabled():
            try:
                on_cargo(self)
            except Exception:
                _switch_off("on_cargo()")
        return result

    for name, wrapper, original in (("show_station_overlay", show_station_overlay, show),
                                    ("hide_station_overlay", hide_station_overlay, hide),
                                    ("set_ps_station_cargo", set_ps_station_cargo, cargo)):
        wrapper.__wrapped__ = original
        wrapper.__doc__ = original.__doc__
        setattr(window_cls, name, wrapper)
    window_cls._station_profile_installed = True
    return True

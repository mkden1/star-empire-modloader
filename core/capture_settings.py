"""capture_settings.py — which of this client's data-capture loggers are switched on.

The mod client records a lot to disk as you play: trade-bot states and leg times, combat and
gear numbers, station logs, planet scans, station ids, shuttle routes, trade-bot slots, and the
colony / station-setup / shuttle-network state behind the Bot Activity dashboard and the last-docked
station snapshots in ``station_profiles/`` (one switch, "Colony & station state", covers both) (all under the
shared ``scan_data/``), plus the freeze watchdog's reports in the client log. Several clients
run at once and every one records the same things into the same files, on a drive the freeze
watchdog caught stalling for seconds at a time. This module is the one place that says what each
client records, so a logger can be switched off without editing code -- from the in-game
"Data Capture" page (capture_settings_panel.py) or by editing ``capture_settings.json``.

Every logger asks ``enabled("<key>")`` before it records anything. That is a dict lookup: the
file is read once, at import (client start-up), never on the game's threads.

Everything defaults to ON, so nothing changes until a switch is flipped. ``capture_all`` is a
master switch over the data loggers; ``freeze_watchdog`` and ``click_diag`` are separate (they write to the
client log, not scan_data, and are applied live), and so is ``sound_prefetch`` (it only READS the weapon
sound files, once, to warm the file cache; turning it on starts that read, turning it off does not undo it).

Only the real files are gated: a logger called with an explicit ``out_path`` (the tests) always
records, so a developer's own settings can never make a test pass or fail.

Settings are per client (``capture_settings.json`` sits in the client's folder, git-ignored), so
one client can record bot states while the others do not.
"""

from __future__ import annotations

import json
import logging
import os

import modloader

from scan_writer import submit

logger = logging.getLogger(__name__)

MASTER = "capture_all"

# (key, label, what it writes, longer note shown on the page or None)
DATA_ENTRIES = (
    ("bot_fleet", "Trade bot states", "bot_fleet_scan.jsonl", None),
    ("bot_legs", "Bot leg times", "bot_leg_times.jsonl", None),
    ("combat", "Combat & gear capture", "combat_scan.jsonl", None),
    ("station_logs", "Station logs", "station_logs.csv", None),
    ("planet_scans", "Planet scans*", "planet_scans.csv",
     "* also what the galaxy map remembers\n  as scanned after a restart"),
    ("station_ids", "Station IDs", "station_ids.csv", None),
    ("shuttle_routes", "Shuttle routes", "shuttle_routes.csv", None),
    ("trade_bot_slots", "Trade bot slots", "trade_bot_slots.csv", None),
    ("colony_scan", "Colony & station state", "colony_scan.jsonl", None),
)
WATCHDOG = "freeze_watchdog"
CLICK_DIAG = "click_diag"          # one client-log line per left click (click_diag.py); like the watchdog, not scan_data
SOUND_PREFETCH = "sound_prefetch"  # read the weapon sounds ahead (sound_prefetch.py): reads, never writes, scan_data
DATA_KEYS = tuple(k for k, _, _, _ in DATA_ENTRIES)
ALL_KEYS = (MASTER,) + DATA_KEYS + (WATCHDOG, CLICK_DIAG, SOUND_PREFETCH)

_DEFAULTS = {k: True for k in ALL_KEYS}


def _default_path() -> str:
    client_root = str(modloader.REPO_DIR)
    return os.path.join(client_root, "capture_settings.json")


_path: str = _default_path()
_state: "dict[str, bool]" = dict(_DEFAULTS)


def enabled(key: str) -> bool:
    """Is ``key`` switched on? For a data logger this also needs the master switch on."""
    state = _state
    if key in DATA_KEYS:
        return state.get(MASTER, True) and state.get(key, True)
    return state.get(key, True)


def snapshot() -> "dict[str, bool]":
    """A copy of every switch as stored (the master is not folded into the others)."""
    return dict(_state)


def set_enabled(key: str, value: bool) -> None:
    """Flip one switch now and save the file in the background (never blocks the caller)."""
    global _state
    if key not in ALL_KEYS:
        raise KeyError(key)
    value = bool(value)
    if _state.get(key, True) == value:
        return
    _state = {**_state, key: value}
    submit(_save, _path, dict(_state))
    if key == WATCHDOG:
        _apply_watchdog(value)
    elif key == SOUND_PREFETCH and value:
        _apply_sound_prefetch()


def toggle(key: str) -> bool:
    """Flip ``key`` and return its new stored value."""
    set_enabled(key, not _state.get(key, True))
    return _state.get(key, True)


def _apply_watchdog(on: bool) -> None:
    try:
        import freeze_watchdog
        if on:
            freeze_watchdog.start()
        else:
            freeze_watchdog.stop()
    except Exception:
        logger.exception("capture_settings: could not apply the freeze_watchdog switch")


def _apply_sound_prefetch() -> None:
    """Switched ON in the page: start the read-ahead now (it only needs the folder the audio set-up
    reported; before that there is nothing to do and the hook starts it)."""
    try:
        import sound_prefetch
        sound_prefetch.start()
    except Exception:
        logger.exception("capture_settings: could not apply the sound_prefetch switch")


def _save(path: str, state: "dict[str, bool]") -> None:
    """Writer-thread side of set_enabled: temp file + atomic replace, so a reader never sees a
    half-written file."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        logger.exception("capture_settings: could not save %s", path)


def load(path: "str | None" = None) -> "dict[str, bool]":
    """(Re)read the settings file. A missing, unreadable or malformed file means "all defaults"
    (everything on); keys it does not know are ignored. Reads the disk: call at start-up only."""
    global _path, _state
    if path is not None:
        _path = path
    state = dict(_DEFAULTS)
    try:
        with open(_path, encoding="utf-8") as handle:
            stored = json.load(handle)
        if isinstance(stored, dict):
            for key in ALL_KEYS:
                if isinstance(stored.get(key), bool):
                    state[key] = stored[key]
        else:
            logger.warning("capture_settings: %s is not a JSON object; using defaults", _path)
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        logger.warning("capture_settings: could not read %s; using defaults", _path)
    _state = state
    return dict(state)


def configure_for_tests(path: "str | None") -> None:
    """Point at another settings file (None restores the real one) and reload it."""
    global _path
    _path = _default_path() if path is None else path
    load()


load()   # once, at start-up: the file is tiny and nothing is running yet

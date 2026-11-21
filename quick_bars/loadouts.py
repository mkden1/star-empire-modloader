"""loadouts.py - a LOADOUT is a named setup of the quick action bars, to switch between (one per class, one per job).

Pure Python: no pygame, no game imports.

A loadout holds

  * ``main``  - what is in the slots of the game's own quick action bar, every tab (the list the game itself keeps in options.txt);
  * ``bars``  - the extra bars (bars.py): each with its name, its key set, its place, whether it is shown, and what is in its slots;
  * ``keys``  - the keys changed for those bars' slots (a bar's slot keys are part of the bar, so they travel with it).

It does NOT hold the game's hotkeys (dock, map ...) or the keys of the game's own bar: those are how you like to play, not what a class
needs, and stay as they are. Loading a loadout REPLACES the game's bar contents and the extra bars (with an UNDO that comes back after a
restart), so a setup you have not saved is worth saving first; the window shows which loadout is what you have right now.

Loadouts are shared by every account run from the folder (a loadout named after a class suits whichever character plays it) and live in
``quick_bars.json`` under ``loadouts``, one entry each, so two clients cannot wipe each other's. The undo of the last load, per account,
is under ``previous``.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import threading
import time

import bars as bars_model
import keybinds as kb

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MAX_NAME_LEN = 32
MAX_MAIN = 60                       # the game's bar has 20 slots today; a longer list is cut here
_BAR_KEY = re.compile(r"^slot\.([A-Za-z0-9_-]{1,16})\.(\d{1,2})$")


def clean_name(name) -> str:
    return " ".join(str(name or "").split())[:MAX_NAME_LEN]


def make_id(name, when) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", clean_name(name).lower()).strip("-")[:24] or "loadout"
    return f"{int(when * 1000)}-{slug}"


# ── a setup ──────────────────────────────────────────────────────────────────

def clean_setup(data) -> "dict | None":
    """A setup (``main``, ``bars``, ``keys``) the way it is kept: bindings as the game keeps them, the bars as bars.py loads them, the
    keys only for slots of those bars. Unusable parts are dropped; None when *data* is not a dict at all."""
    if not isinstance(data, dict):
        return None
    main = data.get("main")
    main = [bars_model.clean_binding(b) for b in main[:MAX_MAIN]] if isinstance(main, list) else []
    barset = bars_model.BarSet.from_json({"bars": data.get("bars")})
    ids = {b.id for b in barset}
    keys: dict = {}
    raw = data.get("keys")
    if isinstance(raw, dict):
        for action_id, entry in raw.items():
            match = _BAR_KEY.match(action_id) if isinstance(action_id, str) else None
            if match is None or match.group(1) not in ids or not 1 <= int(match.group(2)) <= bars_model.SLOTS:
                continue
            if entry is None or (isinstance(entry, dict) and isinstance(entry.get("key"), str)):
                keys[action_id] = copy.deepcopy(entry)
    return {"main": main, "bars": [b.to_json() for b in barset], "keys": keys}


def setup_key(setup: dict) -> str:
    """Two setups are the same exactly when their keys are: it is how the window tells which loadout is what you have now. The game
    bar's empty slots at the end do not count (a loadout saved when the bar had fewer slots is still the same once loaded)."""
    cleaned = clean_setup(setup)
    if cleaned is not None:
        main = list(cleaned["main"])
        while main and main[-1] is None:
            main.pop()
        cleaned = {**cleaned, "main": main}
    return json.dumps(cleaned, sort_keys=True)


def setup_of(loadout: dict) -> dict:
    return {"main": loadout["main"], "bars": loadout["bars"], "keys": loadout["keys"]}


def new_loadout(name, setup: dict, when: "float | None" = None) -> dict:
    when = time.time() if when is None else float(when)
    name = clean_name(name)
    return {"version": SCHEMA_VERSION, "id": make_id(name, when), "name": name, "saved_at": when, **clean_setup(setup)}


def normalize(data) -> "dict | None":
    """A saved loadout as the program uses it, or None when it is not one (no id, no name, a newer version than this knows)."""
    if not isinstance(data, dict) or not isinstance(data.get("id"), str) or not data["id"]:
        return None
    if not isinstance(data.get("name"), str) or not clean_name(data["name"]):
        return None
    saved_at = data.get("saved_at")
    if isinstance(saved_at, bool) or not isinstance(saved_at, (int, float)):
        return None
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version > SCHEMA_VERSION:
        return None
    setup = clean_setup(data)
    return {"version": SCHEMA_VERSION, "id": data["id"], "name": clean_name(data["name"]), "saved_at": float(saved_at), **setup}


def summary(loadout: dict) -> str:
    """The second line of a list row: ``main 6  -  2 bars, 14 slots``."""
    main = sum(1 for b in loadout["main"] if b is not None)
    bars = loadout["bars"]
    filled = sum(1 for bar in bars for s in bar["slots"] if s is not None)
    return f"main {main}  -  {len(bars)} bar{'' if len(bars) == 1 else 's'}, {filled} slot{'' if filled == 1 else 's'}"


def format_age(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    days = int(seconds // 86400)
    return f"{days} day{'s' if days != 1 else ''} ago"


# ── the file ─────────────────────────────────────────────────────────────────

def read_all(path: str) -> "list[dict]":
    section = kb.read_file(path).get("loadouts")
    if not isinstance(section, dict):
        return []
    return [item for item in (normalize(v) for v in section.values()) if item is not None]


def by_name(items) -> "list[dict]":
    return sorted(items, key=lambda d: (d["name"].lower(), d["saved_at"]))


class LoadoutStore:
    """The saved loadouts, read and written off the render thread.

    ``items`` is a tuple (by name) that is replaced whole; reading it never blocks. ``refresh``, ``save`` and ``delete`` queue their
    disk work on the shared writer thread, so they run in order. A write changes only its own entry of the file (a read-modify-write),
    so two clients cannot wipe each other's loadouts, and ``refresh`` picks up what the other saved. A save or delete is visible at
    once and stays visible: a scan queued before it reads the file without the change, so every scan puts the changes that are not on
    disk yet back on top."""

    def __init__(self, path_fn=None, submit_fn=None):
        self._path_fn = path_fn or kb.default_path
        self._submit_fn = submit_fn
        self.items: tuple = ()
        self.loaded = False
        self.error: "str | None" = None
        self._lock = threading.Lock()
        self._unsaved: dict = {}
        self._deleted: set = set()

    @property
    def path(self) -> str:
        return self._path_fn()

    def _run(self, fn, *args) -> None:
        submit = self._submit_fn() if self._submit_fn is not None else None
        if submit is None:
            from scan_writer import submit
        submit(fn, *args)

    def _publish(self, on_disk) -> None:
        with self._lock:
            by_id = {d["id"]: d for d in on_disk}
            by_id.update(self._unsaved)
            for item_id in self._deleted:
                by_id.pop(item_id, None)
        self.items = tuple(by_name(by_id.values()))

    def refresh(self) -> None:
        self._run(self._scan)

    def _scan(self, keep_error: bool = False) -> None:
        try:
            self._publish(read_all(self.path))
            if not keep_error:
                self.error = None
        except Exception as exc:                 # a scan must never take the writer thread's loop down
            logger.exception("loadouts: could not scan %s", self.path)
            self.error = str(exc)
        self.loaded = True

    def save(self, item: dict) -> None:
        with self._lock:
            self._unsaved[item["id"]] = item
            self._deleted.discard(item["id"])
        self._publish(self.items)
        self._run(self._write, item)

    def _write(self, item: dict) -> None:
        failed = False
        try:
            kb.write_entry(self.path, "loadouts", item["id"], item)
            with self._lock:
                if self._unsaved.get(item["id"]) is item:        # a newer save of it stays pending
                    del self._unsaved[item["id"]]
        except Exception as exc:
            logger.exception("loadouts: could not save %s", item.get("id"))
            self.error = f"could not save: {exc}"
            failed = True
        self._scan(keep_error=failed)

    def delete(self, item_id: str) -> None:
        with self._lock:
            self._deleted.add(item_id)
            self._unsaved.pop(item_id, None)
        self._publish(self.items)
        self._run(self._delete, item_id)

    def _delete(self, item_id: str) -> None:
        failed = False
        try:
            kb.delete_entry(self.path, "loadouts", item_id)
            with self._lock:
                self._deleted.discard(item_id)
        except Exception as exc:
            logger.exception("loadouts: could not delete %s", item_id)
            self.error = f"could not delete: {exc}"
            failed = True
        self._scan(keep_error=failed)

    def get(self, item_id) -> "dict | None":
        return next((d for d in self.items if d["id"] == item_id), None)

    def find(self, text: str) -> "dict | None":
        """A loadout by name: the exact name (any case), else the one name that starts with *text*."""
        wanted = clean_name(text).lower()
        if not wanted:
            return None
        for item in self.items:
            if item["name"].lower() == wanted:
                return item
        starts = [d for d in self.items if d["name"].lower().startswith(wanted)]
        return starts[0] if len(starts) == 1 else None

    def unique_name(self, name: str) -> str:
        """*name*, or "name (2)", "name (3)" ... when a loadout already has it (any case)."""
        base = clean_name(name) or "Loadout"
        taken = {d["name"].lower() for d in self.items}
        if base.lower() not in taken:
            return base
        number = 2
        while True:
            suffix = f" ({number})"
            candidate = base[:MAX_NAME_LEN - len(suffix)] + suffix
            if candidate.lower() not in taken:
                return candidate
            number += 1

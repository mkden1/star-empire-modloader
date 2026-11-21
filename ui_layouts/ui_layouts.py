"""ui_layouts.py - the model behind the UI LAYOUTS window: what a saved layout holds, how it is fitted to another window
size, how it is put back, and the file it lives in.

A LAYOUT is where the HUD's windows sit, and nothing else: the screen position of every panel the game lays out
(Ship Inventory, Turret Control, Chat, the Quick Actions bar, the minimap, Stats, Squad, Coalition, Friends ...), the sizes the
player gave the ones that can be resized, and the window size it was made for. The game already writes all of that into
``options.txt`` through ``_panel_layout_dict`` and reads it back through ``apply_panel_layout`` (a mod's window, like the Combat Log,
adds its own entry to the same dict), so this module does not know the panels one by one: it takes the game's own dict and keeps the
parts that are PLACES.

  * kept: the leaves named x, y, w, h, width, height, max_h, max_w and resize (a null position is kept too: it means "the
    default place", which the game works out from the window size each frame, so a layout saved that way follows the resolution);
  * left out: the Quick Actions bindings, the hangar sort order, the Galaxy Map favourites, the chat tabs and colours,
    whether a panel is open or collapsed, the minimap's zoom. A layout moves windows; it never changes what is in them.

Putting a layout back builds ONE dict, the game's current one with the saved places laid over it, and hands it to the game's own
``apply_panel_layout``: nothing that is not a place can change, and a window the game learns about in a later version comes along.

Pure Python: no pygame, no game imports. The window is ui_layouts_window.py.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
import time

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MAX_NAME_LEN = 32

# What a layout does not hold: sections of the game's layout dict that are settings, not places.
NOT_PLACES = ("hotbar", "hangar_order", "galaxy_favorites")
# Sections that are a map of id -> entry (every other section is one entry).
NESTED = ("panels",)
# The leaves of an entry that are places.
PLACE_KEYS = ("x", "y", "w", "h", "width", "height", "max_h", "max_w", "resize")


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# ── what a layout holds ──────────────────────────────────────────────────────

def _leaves(entry: dict) -> dict:
    return {key: entry[key] for key in PLACE_KEYS if key in entry and (entry[key] is None or _number(entry[key]))}


def places(layout_dict: dict) -> dict:
    """The places in the game's layout dict (``_panel_layout_dict()``): ``{section: {leaf: value}}``, and for the panels
    ``{"panels": {panel id: {leaf: value}}}``. Anything that is not a place is left out."""
    out: dict = {}
    for section, value in layout_dict.items():
        if section in NOT_PLACES or not isinstance(value, dict):
            continue
        if section in NESTED:
            inner = {pid: kept for pid, entry in value.items() if isinstance(entry, dict) and (kept := _leaves(entry))}
            if inner:
                out[section] = inner
        elif (kept := _leaves(value)):
            out[section] = kept
    return out


def _entries(geometry: dict):
    """Every entry of *geometry* that can hold a position, as dicts (the panels' entries and the other sections)."""
    for section, value in geometry.items():
        if section in NESTED:
            yield from (entry for entry in value.values() if isinstance(entry, dict))
        elif isinstance(value, dict):
            yield value


def count(geometry: dict, windows: "dict | None" = None) -> int:
    """How many windows the layout places (an entry with a position, plus the mods' windows)."""
    placed = sum(1 for entry in _entries(geometry) if _number(entry.get("x")) and _number(entry.get("y")))
    return placed + len(windows or {})


def _overlay(entry: dict, saved: dict) -> None:
    """Lay the saved places over one entry. A null x or y is not laid over: the game's loader ignores it in most sections
    and would choke on it in Chat's; the sections saved at their default place are reset by the window (``default_sections``)."""
    for key, value in saved.items():
        if key in PLACE_KEYS and not (key in ("x", "y") and value is None):
            entry[key] = value


def merge(current: dict, geometry: dict) -> dict:
    """A copy of the game's current layout dict with the saved places laid over it. Only sections the game has now are
    touched (a section of a mod that is not installed has nobody to read it), but every saved panel is added, because the
    game creates some panels late (Turret Control exists once a ship has turrets)."""
    merged = copy.deepcopy(current)
    for section, values in geometry.items():
        target = merged.get(section)
        if section in NESTED:
            if not isinstance(target, dict) or not isinstance(values, dict):
                continue
            for pid, saved in values.items():
                entry = target.setdefault(pid, {})
                if isinstance(entry, dict) and isinstance(saved, dict):
                    _overlay(entry, saved)
        elif isinstance(target, dict) and isinstance(values, dict):
            _overlay(target, values)
    return merged


def default_sections(geometry: dict) -> "list[str]":
    """The sections saved at their default place (x and y both null): the game's loader ignores a null position, so the
    window resets those itself."""
    return [section for section, value in geometry.items()
            if section not in NESTED and isinstance(value, dict)
            and "x" in value and "y" in value and value["x"] is None and value["y"] is None]


def null_sizes(geometry: dict) -> "list[tuple[str, str]]":
    """``(section, leaf)`` of every size a layout leaves at the window's default size (null), outside the panels: the game's loader
    ignores a null size as it ignores a null position (a panel's max_h / max_w are different: it does read a null there), so the
    window resets those itself."""
    return [(section, key) for section, value in geometry.items()
            if section not in NESTED and isinstance(value, dict)
            for key in ("w", "h", "width", "height", "resize") if key in value and value[key] is None]


# ── fitting a layout to another window size ──────────────────────────────────

def fit_point(x, y, old: tuple, new: tuple) -> tuple:
    """Where a window at (x, y) goes when the screen changes from *old* to *new* size: it keeps its distance from the screen
    corner it was nearest to. This is the game's own rule for a resize (the corner-anchored repositioning in its frame loop)."""
    old_w, old_h = old
    new_w, new_h = new
    new_x = new_w - (old_w - x) if x > old_w / 2 else x
    new_y = new_h - (old_h - y) if y > old_h / 2 else y
    return int(new_x), int(new_y)


def fit_geometry(geometry: dict, old: tuple, new: tuple) -> dict:
    """A copy of *geometry* with every position moved from the *old* window size to the *new* one. Sizes stay as they are."""
    fitted = copy.deepcopy(geometry)
    for entry in _entries(fitted):
        if _number(entry.get("x")) and _number(entry.get("y")):
            entry["x"], entry["y"] = fit_point(entry["x"], entry["y"], old, new)
    return fitted


def fit_windows(windows: dict, old: tuple, new: tuple) -> dict:
    """The same for the mods' windows (``{key: [x, y, w, h]}``): the top left corner moves, the size stays."""
    return {key: [*fit_point(rect[0], rect[1], old, new), rect[2], rect[3]] for key, rect in windows.items()}


# ── one layout ───────────────────────────────────────────────────────────────

def clean_name(name) -> str:
    return " ".join(str(name or "").split())[:MAX_NAME_LEN]


def make_id(name, when) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", clean_name(name).lower()).strip("-")[:24] or "layout"
    return f"{int(when * 1000)}-{slug}"


def new_layout(name, size, geometry: dict, windows: "dict | None" = None, when: "float | None" = None,
               extras: "dict | None" = None) -> dict:
    """``extras`` is what other mods keep the places of their windows in (``{mod key: data}``): it is stored as it is given and
    handed back to that mod on a load, so this module never needs to know what is in it."""
    when = time.time() if when is None else float(when)
    name = clean_name(name)
    return {"version": SCHEMA_VERSION, "id": make_id(name, when), "name": name, "saved_at": when,
            "size": [int(size[0]), int(size[1])], "geometry": copy.deepcopy(geometry),
            "windows": {key: [int(v) for v in rect] for key, rect in (windows or {}).items()},
            "extras": copy.deepcopy(extras or {})}


def valid_layout(data) -> bool:
    if not isinstance(data, dict) or not isinstance(data.get("id"), str) or not data["id"]:
        return False
    if not isinstance(data.get("name"), str) or not clean_name(data["name"]):
        return False
    size = data.get("size")
    if not (isinstance(size, list) and len(size) == 2 and all(isinstance(v, int) and not isinstance(v, bool) and v > 0
                                                              for v in size)):
        return False
    if not _number(data.get("saved_at")) or not isinstance(data.get("geometry"), dict):
        return False
    windows = data.get("windows")
    if not isinstance(windows, dict) or not all(
            isinstance(rect, list) and len(rect) == 4 and all(isinstance(v, int) and not isinstance(v, bool) for v in rect)
            for rect in windows.values()):
        return False
    extras = data.get("extras", {})                  # (a layout saved before extras existed has none)
    if not isinstance(extras, dict) or not all(isinstance(k, str) and isinstance(v, dict) for k, v in extras.items()):
        return False
    return isinstance(data.get("version"), int) and data["version"] <= SCHEMA_VERSION


def summary(layout: dict) -> str:
    """The second line of a list row: ``2560 x 1440  -  14 windows``."""
    width, height = layout["size"]
    placed = count(layout["geometry"], layout["windows"])
    return f"{width} x {height}  -  {placed} window{'s' if placed != 1 else ''}"


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

def default_path() -> str:
    import modloader
    return os.path.join(str(modloader.REPO_DIR), "ui_layouts.json")


def read_file(path: str) -> "list[dict]":
    """The valid layouts in the file; a missing or unreadable file is an empty list (and an unreadable one is logged)."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        logger.warning("ui_layouts: could not read %s", path)
        return []
    items = data.get("layouts") if isinstance(data, dict) else None
    return [item for item in items if valid_layout(item)] if isinstance(items, list) else []


def write_file(path: str, layouts) -> None:
    """Temp file and atomic replace, so another client reading the file never sees half of it."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({"version": SCHEMA_VERSION, "layouts": list(layouts)}, handle, indent=1, sort_keys=True)
    os.replace(tmp, path)


def _by_name(layouts) -> "list[dict]":
    return sorted(layouts, key=lambda d: (d["name"].lower(), d["saved_at"]))


class LayoutStore:
    """The saved layouts, read and written off the render thread.

    ``layouts`` is a tuple (by name) that is replaced whole; reading it never blocks. ``refresh``, ``save`` and ``delete`` queue
    their disk work on the shared writer thread, so they run in order. Every write READS THE FILE FIRST and changes only the
    layout it was asked about, so two clients running from one folder (two accounts) cannot wipe each other's layouts, and
    ``refresh`` picks up what the other one saved. A save or delete is visible at once and stays visible: a scan that was
    queued before it reads the file without the change, so every scan puts the changes not on disk yet back on top."""

    def __init__(self, path=None, submit=None):
        self._path = path
        self._submit = submit
        self.layouts: tuple = ()
        self.loaded = False
        self.error: "str | None" = None
        self._lock = threading.Lock()
        self._unsaved: dict = {}          # id -> layout accepted by save() but not on disk yet
        self._deleted: set = set()        # ids accepted by delete() but still on disk

    @property
    def path(self) -> str:
        return self._path or default_path()

    def _run(self, fn, *args) -> None:
        submit = self._submit
        if submit is None:
            from scan_writer import submit
        submit(fn, *args)

    def _publish(self, on_disk) -> None:
        with self._lock:
            by_id = {d["id"]: d for d in on_disk}
            by_id.update(self._unsaved)
            for layout_id in self._deleted:
                by_id.pop(layout_id, None)
        self.layouts = tuple(_by_name(by_id.values()))

    def refresh(self) -> None:
        self._run(self._scan)

    def _scan(self, keep_error: bool = False) -> None:
        try:
            self._publish(read_file(self.path))
            if not keep_error:
                self.error = None
        except Exception as exc:                 # a scan must never take the writer thread's loop down
            logger.exception("ui_layouts: could not scan %s", self.path)
            self.error = str(exc)
        self.loaded = True

    def save(self, layout: dict) -> None:
        """Show *layout* at once and queue the write."""
        with self._lock:
            self._unsaved[layout["id"]] = layout
            self._deleted.discard(layout["id"])
        self._publish(self.layouts)
        self._run(self._write, layout)

    def _write(self, layout: dict) -> None:
        failed = False
        try:
            current = {d["id"]: d for d in read_file(self.path)}
            current[layout["id"]] = layout
            write_file(self.path, _by_name(current.values()))
            with self._lock:
                if self._unsaved.get(layout["id"]) is layout:      # a newer save of it stays pending
                    del self._unsaved[layout["id"]]
        except Exception as exc:
            logger.exception("ui_layouts: could not save %s", layout.get("id"))
            self.error = f"could not save: {exc}"
            failed = True
        self._scan(keep_error=failed)

    def delete(self, layout_id: str) -> None:
        with self._lock:
            self._deleted.add(layout_id)
            self._unsaved.pop(layout_id, None)
        self._publish(self.layouts)
        self._run(self._delete, layout_id)

    def _delete(self, layout_id: str) -> None:
        failed = False
        try:
            current = {d["id"]: d for d in read_file(self.path)}
            if current.pop(layout_id, None) is not None:
                write_file(self.path, _by_name(current.values()))
            with self._lock:
                self._deleted.discard(layout_id)
        except Exception as exc:
            logger.exception("ui_layouts: could not delete %s", layout_id)
            self.error = f"could not delete: {exc}"
            failed = True
        self._scan(keep_error=failed)

    def get(self, layout_id) -> "dict | None":
        return next((d for d in self.layouts if d["id"] == layout_id), None)

    def find(self, text: str) -> "dict | None":
        """A layout by name: the exact name (any case), else the one name that starts with *text*."""
        wanted = clean_name(text).lower()
        if not wanted:
            return None
        for layout in self.layouts:
            if layout["name"].lower() == wanted:
                return layout
        starts = [d for d in self.layouts if d["name"].lower().startswith(wanted)]
        return starts[0] if len(starts) == 1 else None

    def unique_name(self, name: str) -> str:
        """*name*, or "name (2)", "name (3)", ... when a layout already has it (any case)."""
        base = clean_name(name)
        taken = {d["name"].lower() for d in self.layouts}
        if base.lower() not in taken:
            return base
        number = 2
        while True:
            suffix = f" ({number})"
            candidate = base[:MAX_NAME_LEN - len(suffix)] + suffix
            if candidate.lower() not in taken:
                return candidate
            number += 1

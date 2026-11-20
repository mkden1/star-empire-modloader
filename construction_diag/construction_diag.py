"""construction_diag.py - log what the server really tells the client about station construction.

The aim is an estimated time to complete for every project in a station's Construction Bay. How a
project's time comes about (man-hours against the workers available, per-tick resource costs taken in
priority order, resources that may not be on hand) is the server's business; what the CLIENT can see of
it is not known from the code: it reads only project_id / name / qty / paused / progress from each queued
project, the blueprint's ``manhours`` and ``resource_costs`` for a tooltip, and the station's cargo.
This module records the actual messages so that question can be answered from a real session:

  * PS_CONSTRUCTION_BAY (``_on_ps_construction_bay``): the field names of the message, of a queue entry
    and of a blueprint; the FULL text of each project the first time it appears and of one blueprint that
    has man-hours and resource costs; and on every message one compact line - seconds since the previous
    one, each project's progress and every OTHER field of it that changed - so the cadence of updates and
    which numbers move are both visible;
  * PS_STATION_CARGO (``_on_ps_station_cargo``): the station's workers (a workers row's ``cargo_size`` is the
    head count; ``workers_used`` how many are working) and its stock of every resource (also a row's
    ``cargo_size``), with how much each changed since the previous message and over how many seconds -
    the arrival rate of the resources a project needs.

Log lines start ``construction_diag:`` and go to the client log (``grep construction_diag
custom_build/star_empire_client.log``). The message thread only puts the raw text on a queue; parsing and
formatting happen on a worker thread. Both handlers are hooked from OUTSIDE protocol.py (``install`` is called
once from Client.py), so vanilla's source and tests stay as the developer wrote them. A hard cap on the
number of lines keeps a long session from filling the log. ``STAR_EMPIRE_NO_CONSTRUCTION_DIAG=1`` turns it
off. Nothing here changes what the game does: the original handler always runs, unchanged and first-class.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time

logger = logging.getLogger(__name__)

ENV_OFF = "STAR_EMPIRE_NO_CONSTRUCTION_DIAG"
MAX_LINES = 2000             # per client session; one notice, then silence
CARGO_MIN_GAP_S = 5.0        # the station pushes its cargo about once a second; log it at most this often
MAX_FULL_CHARS = 3500        # a full dump of one project / blueprint is cut here
MAX_FULL_DUMPS = 40          # projects dumped in full per session
QUEUE_SIZE = 100
_PER_PROJECT_FIELDS = 6      # at most this many changed fields are listed per project per message


def _short(value, limit=48) -> str:
    text = json.dumps(value, default=str, sort_keys=True) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[:limit - 1] + "..."


def _full(obj) -> str:
    text = json.dumps(obj, default=str, sort_keys=True)
    if len(text) <= MAX_FULL_CHARS:
        return text
    return f"{text[:MAX_FULL_CHARS]}... (+{len(text) - MAX_FULL_CHARS} chars)"


def _type_of(value) -> str:
    if isinstance(value, (list, tuple, dict)):
        return f"{type(value).__name__}[{len(value)}]"
    return type(value).__name__


def _types(mapping) -> str:
    return ", ".join(f"{key}:{_type_of(value)}" for key, value in sorted(mapping.items()))


def _union_types(entries) -> str:
    seen: "dict[str, str]" = {}
    for entry in entries:
        if isinstance(entry, dict):
            for key, value in entry.items():
                seen.setdefault(key, type(value).__name__)
    return ", ".join(f"{key}:{kind}" for key, kind in sorted(seen.items()))


def entries_with(entries, keys) -> list:
    """The given entries cut down to ``keys`` (for listing the types of newly seen fields)."""
    return [{k: e[k] for k in keys if k in e} for e in entries if isinstance(e, dict)]


def _since(seconds) -> str:
    return "first" if seconds is None else f"+{seconds:.1f}s"


class Tracker:
    """What has been seen so far, and the log lines each new message earns. Pure: no I/O."""

    def __init__(self):
        self.bay_n = 0
        self.cargo_n = 0
        self.last_bay_t: "float | None" = None
        self.last_cargo_t: "dict[object, float]" = {}      # when each station's cargo was last LOGGED
        self.projects: "dict[object, dict]" = {}      # project_id -> last entry
        self.seen_projects: set = set()
        self.dumps_left = MAX_FULL_DUMPS
        self.blueprint_dumped = False
        self.bay_keys: "dict[str, set]" = {"top": set(), "queue": set(), "blueprint": set()}
        self.bay_announced = False
        self.cargo_keys: set = set()
        self.cargo_announced = False
        self.stock: "dict[object, dict]" = {}         # station_id -> {resource: quantity}
        self.max_lines = MAX_LINES
        self.lines_left = MAX_LINES
        self.announced_limit = False

    # -- the Construction Bay ---------------------------------------------------------------------

    def bay_lines(self, data, now: float) -> "list[str]":
        self.bay_n += 1
        since = None if self.last_bay_t is None else now - self.last_bay_t
        self.last_bay_t = now
        if not isinstance(data, dict):
            return [f"bay #{self.bay_n}: payload is a {type(data).__name__}, not an object"]
        queue_rows = data.get("queue")
        queue_rows = [r for r in queue_rows if isinstance(r, dict)] if isinstance(queue_rows, list) else []
        blueprints = data.get("blueprints")
        blueprints = [b for b in blueprints if isinstance(b, dict)] if isinstance(blueprints, list) else []
        lines: "list[str]" = []

        if not self.bay_announced:
            self.bay_announced = True
            lines.append(f"PS_CONSTRUCTION_BAY fields: {{{_types(data)}}}")
            lines.append(f"  queue entry fields: {_union_types(queue_rows) or '(queue empty so far)'}")
            lines.append(f"  blueprint fields: {_union_types(blueprints) or '(none)'}")
            self.bay_keys["top"] = set(data)
            self.bay_keys["queue"] = {k for r in queue_rows for k in r}
            self.bay_keys["blueprint"] = {k for b in blueprints for k in b}
        else:
            added = []
            for label, found, entries in (("top-level", set(data), [data]),
                                          ("queue entry", {k for r in queue_rows for k in r}, queue_rows),
                                          ("blueprint", {k for b in blueprints for k in b}, blueprints)):
                key = {"top-level": "top", "queue entry": "queue", "blueprint": "blueprint"}[label]
                new = found - self.bay_keys[key]
                if new:
                    self.bay_keys[key] |= new
                    added.append(f"{label} {{{_union_types(entries_with(entries, new))}}}")
            if added:
                lines.append("PS_CONSTRUCTION_BAY fields ADDED: " + "; ".join(added))

        for row in queue_rows:
            pid = row.get("project_id")
            if pid not in self.seen_projects and self.dumps_left > 0:
                self.dumps_left -= 1
                self.seen_projects.add(pid)
                lines.append(f"  project {pid} in full: {_full(row)}")
        if blueprints and not self.blueprint_dumped:
            self.blueprint_dumped = True
            with_hours = sum(1 for b in blueprints if b.get("manhours"))
            with_costs = sum(1 for b in blueprints if b.get("resource_costs"))
            example = next((b for b in blueprints if b.get("manhours") and b.get("resource_costs")), blueprints[0])
            lines.append(f"  {len(blueprints)} blueprint(s), {with_hours} with manhours, {with_costs} with "
                         f"resource_costs; one in full: {_full(example)}")

        parts = []
        current = {}
        for row in queue_rows:
            pid = row.get("project_id")
            current[pid] = row
            label = f"{pid} {_short(row.get('name', '?'), 28)} x{row.get('qty', 1)}"
            if row.get("paused"):
                label += " [PAUSED]"
            before = self.projects.get(pid)
            progress = row.get("progress")
            if before is None:
                parts.append(f"{label} progress={_short(progress)} (new)")
                continue
            moved = []
            for key in sorted(set(before) | set(row)):
                if key == "progress" or before.get(key) == row.get(key):
                    continue
                moved.append(f"{key}: {_short(before.get(key), 20)}->{_short(row.get(key), 20)}")
            more = len(moved) - _PER_PROJECT_FIELDS
            moved = moved[:_PER_PROJECT_FIELDS] + ([f"+{more} more"] if more > 0 else [])
            old_progress = before.get("progress")
            if old_progress != progress:
                text = f"progress {_short(old_progress)}->{_short(progress)}"
            else:
                text = f"progress {_short(progress)} (no change)"
            parts.append(f"{label} {text}" + (f"; changed: {', '.join(moved)}" if moved else ""))
        for pid, row in self.projects.items():
            if pid not in current:
                parts.append(f"{pid} {_short(row.get('name', '?'), 28)} LEFT the queue at progress "
                             f"{_short(row.get('progress'))}")
        self.projects = current
        station = f" station={data['station_id']}" if "station_id" in data else ""
        lines.append(f"bay #{self.bay_n} {_since(since)}{station}: "
                     + (" | ".join(parts) if parts else "queue empty"))
        return lines

    # -- the station's workers and stock ----------------------------------------------------------

    def cargo_lines(self, data, now: float) -> "list[str]":
        self.cargo_n += 1
        if not isinstance(data, dict):
            return [f"cargo #{self.cargo_n}: payload is a {type(data).__name__}, not an object"]
        sid = data.get("station_id")
        rows = data.get("cargo")
        rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        lines: "list[str]" = []
        if not self.cargo_announced:
            self.cargo_announced = True
            self.cargo_keys = set(data)
            lines.append(f"PS_STATION_CARGO fields: {{{_types(data)}}}")
        elif set(data) - self.cargo_keys:
            new = set(data) - self.cargo_keys
            self.cargo_keys |= new
            lines.append(f"PS_STATION_CARGO fields ADDED: {{{_types({k: data[k] for k in new})}}}")
        logged_at = self.last_cargo_t.get(sid)
        if logged_at is not None and now - logged_at < CARGO_MIN_GAP_S:
            return lines                      # too soon after the last cargo line; changes add up for the next
        since = None if logged_at is None else now - logged_at
        self.last_cargo_t[sid] = now

        workers = [r for r in rows if r.get("item_key") == "workers"]
        resources = {}
        for row in rows:
            if row.get("item_category") == "resource" and row.get("item_key") != "workers":
                # real messages: the stock of a resource is the row's cargo_size (it has no quantity)
                resources[row.get("item_key")] = row.get("cargo_size", row.get("quantity", 0))
        used = data.get("workers_used")
        worker_text = "no workers row"
        if workers:
            quantity = sum(_number(r.get("quantity")) for r in workers)
            cargo_size = sum(_number(r.get("cargo_size")) for r in workers)
            worker_text = f"workers qty={quantity:g} cargo_size={cargo_size:g}"
        worker_text += f" used={used}"

        credits = data.get("station_credits")
        if credits is not None:
            resources["credits"] = credits        # tracked like a resource: is it taken as the work goes?
        before = self.stock.get(sid)
        moved = ""
        if before is not None and since:
            changes = [f"{key} {_signed(_number(value) - _number(before.get(key, 0)))}"
                       for key, value in sorted(resources.items())
                       if _number(value) != _number(before.get(key, 0))]
            moved = (f" | changed over {since:.1f}s: " + ", ".join(changes)) if changes else \
                f" | no stock change over {since:.1f}s"
        self.stock[sid] = dict(resources)
        stock = ", ".join(f"{key}={_number(value):.0f}" if key == "credits" else f"{key}={_number(value):g}"
                          for key, value in sorted(resources.items())) or "none"
        reserves = data.get("resource_reserves")
        reserve_text = f" | reserves {_short(reserves, 120)}" if reserves else ""
        lines.append(f"cargo #{self.cargo_n} {_since(since)} station={sid}: {worker_text} | stock {stock}"
                     f"{moved}{reserve_text}")
        return lines

    # -- the cap on how much is written -----------------------------------------------------------

    def limited(self, lines: "list[str]") -> "list[str]":
        out = []
        for line in lines:
            if self.lines_left <= 0:
                if not self.announced_limit:
                    self.announced_limit = True
                    out.append(f"line limit ({self.max_lines}) reached for this session; no more construction "
                               "messages will be logged")
                break
            self.lines_left -= 1
            out.append(line)
        return out


def _number(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _signed(value: float) -> str:
    return f"{value:+g}"


# -- the thread that does the work ------------------------------------------------------------------

_lock = threading.Lock()
_queue: "queue.Queue" = queue.Queue(maxsize=QUEUE_SIZE)
_thread: "threading.Thread | None" = None
_tracker = Tracker()
_dropped = 0
_failed = False


def _enabled() -> bool:
    return not _failed and not os.environ.get(ENV_OFF)


def _enqueue(kind: str, payload) -> None:
    global _thread, _dropped
    if not _enabled():
        return
    try:
        _queue.put_nowait((kind, time.monotonic(), payload))
    except queue.Full:
        _dropped += 1
        return
    if _thread is None or not _thread.is_alive():
        with _lock:
            if _thread is None or not _thread.is_alive():
                _thread = threading.Thread(target=_worker, name="construction-diag", daemon=True)
                _thread.start()


def _worker() -> None:
    global _failed, _dropped
    while True:
        item = _queue.get()
        try:
            if item is None:
                return
            kind, arrived, payload = item
            try:
                data = json.loads(payload) if isinstance(payload, (str, bytes, bytearray)) else payload
            except ValueError:
                continue                  # not JSON: the handler itself ignores it too
            try:
                lines = (_tracker.bay_lines if kind == "bay" else _tracker.cargo_lines)(data, arrived)
                if _dropped:
                    lines.insert(0, f"{_dropped} message(s) were dropped because the queue was full")
                    _dropped = 0
                for line in _tracker.limited(lines):
                    logger.info("construction_diag: %s", line)
            except Exception:
                _failed = True
                logger.exception("construction_diag: switched off for this session after an error")
                return
        finally:
            _queue.task_done()


def _hook(original, kind: str):
    def hooked(self, payload, *args, **kwargs):
        try:
            _enqueue(kind, payload)
        except Exception:
            pass
        return original(self, payload, *args, **kwargs)
    hooked.__name__ = original.__name__
    hooked.__doc__ = original.__doc__
    hooked.__wrapped__ = original
    hooked._construction_diag = True
    return hooked


def install(protocol_cls) -> bool:
    """Hook the two handlers on ``protocol_cls`` (protocol.ProtocolMixin). Idempotent; True if it
    hooked something this call."""
    hooked = False
    for name, kind in (("_on_ps_construction_bay", "bay"), ("_on_ps_station_cargo", "cargo")):
        original = protocol_cls.__dict__.get(name)
        if original is None or getattr(original, "_construction_diag", False):
            continue
        setattr(protocol_cls, name, _hook(original, kind))
        hooked = True
    return hooked


def reset_for_tests() -> None:
    """Stop the worker, empty the queue and forget everything seen."""
    global _thread, _tracker, _dropped, _failed
    with _lock:
        thread = _thread
        _thread = None
    if thread is not None and thread.is_alive():
        try:
            _queue.put(None, timeout=1)
        except queue.Full:
            pass
        thread.join(2)
    while True:
        try:
            _queue.get_nowait()
        except queue.Empty:
            break
    _tracker = Tracker()
    _dropped = 0
    _failed = False

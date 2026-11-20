"""gc_watch.py — say so in the log when Python's garbage collector pauses the whole client.

Each client holds 1.5-2.5 GB of Python objects. A garbage collection runs with the GIL held, so
every thread stops until it finishes; one that has to walk a heap that size can take a good
fraction of a second, and it lands on whatever the game happens to be doing - which is how a
frame-time profile shows a one-off 0.2-0.8 s hitch inside a section that normally takes 1 ms
(draw_ships, attendance_gui, events...), paired with the message thread stalling for exactly as
long. Nothing so far showed whether the collector is behind any of that. This module times every
collection through ``gc.callbacks`` and logs the slow ones with the generation, how much it freed
and how many objects the process holds, so the next freeze can be matched to a collection - or
ruled out.

Cost: two clock reads per collection (a few per second at most).

It also freezes the long-lived heap once, 90 s after the game window starts (see
``schedule_freeze``): on this interpreter that is what takes the multi-hundred-millisecond
collections away.
"""

from __future__ import annotations

import atexit
import gc
import logging
import os
import sys
import threading
import time

logger = logging.getLogger(__name__)

SLOW_S = 0.05                 # collections at least this long are logged
_started_at = 0.0
_count = 0
_total_s = 0.0
_slow_count = 0
_longest_s = 0.0
_installed = False


def _callback(phase: str, info: dict) -> None:
    global _started_at, _count, _total_s, _slow_count, _longest_s
    if phase == "start":
        _started_at = time.perf_counter()
        return
    if not _started_at:
        return
    elapsed = time.perf_counter() - _started_at
    _started_at = 0.0
    _count += 1
    _total_s += elapsed
    if elapsed > _longest_s:
        _longest_s = elapsed
    if elapsed >= SLOW_S:
        _slow_count += 1
        try:
            logger.warning(
                "gc_watch: a generation-%s garbage collection took %.2fs (freed %s, could not "
                "free %s; ~%.1f million objects in memory; %d such pause(s) this session, "
                "%.1fs of %d collections in all) - every thread waits for it",
                info.get("generation"), elapsed, info.get("collected"),
                info.get("uncollectable"), sys.getallocatedblocks() / 1e6,
                _slow_count, _total_s, _count)
        except Exception:
            pass


def install() -> None:
    """Start timing collections (Client.py calls this once at import). Idempotent."""
    global _installed
    if _installed:
        return
    _installed = True
    gc.callbacks.append(_callback)
    atexit.register(_uninstall)


def _uninstall() -> None:
    """Stop timing collections. Runs at exit, before the interpreter empties its modules: a big
    heap still gets a collection while that happens, by which time this module's globals
    (``time`` among them) are None and the callback died with "'NoneType' object has no attribute
    'perf_counter'" (seen at the end of the 2026-11-10 update's test run)."""
    global _installed
    _installed = False
    try:
        gc.callbacks.remove(_callback)
    except ValueError:
        pass


# ── Taking the long-lived heap out of the collector's reach ───────────────────────────────────
# Measured on this interpreter (Python 3.14.2, incremental collector) with a synthetic 14-million
# -block heap and game-like churn (gc_experiment in the 2026-10-10 session): the default collector
# spent 3.6 s of 25 s collecting, with single pauses up to 1.17 s; after ``gc.collect()`` +
# ``gc.freeze()`` the same churn never paused longer than 1.4 ms. ``freeze`` moves every object
# that exists at that moment into a permanent generation the collector no longer scans. What it
# costs: a reference CYCLE among frozen objects that later becomes garbage is never freed (objects
# not in cycles are freed by reference counting as always). So it is done once, after a full
# collection, when the game window has been up long enough for the long-lived data (galaxy map,
# caches, UI state) to exist - not repeatedly.

FREEZE_AFTER_S = 90.0
_freeze_timer = None
_frozen = False


def schedule_freeze(delay_s: "float | None" = None) -> None:
    """Freeze the heap ``delay_s`` seconds from now, once per process (a no-op after the first
    call). STAR_EMPIRE_NO_GC_FREEZE=1 turns it off."""
    global _freeze_timer
    if os.environ.get("STAR_EMPIRE_NO_GC_FREEZE") or _frozen or _freeze_timer is not None:
        return
    _freeze_timer = threading.Timer(FREEZE_AFTER_S if delay_s is None else delay_s, _freeze_now)
    _freeze_timer.daemon = True
    _freeze_timer.name = "gc-freeze"
    _freeze_timer.start()


def _freeze_now() -> None:
    global _frozen
    try:
        blocks_before = sys.getallocatedblocks()
        started = time.perf_counter()
        gc.collect()
        took = time.perf_counter() - started
        gc.freeze()
        _frozen = True
        logger.info(
            "gc_watch: froze %.2f million objects out of the garbage collector's reach after a "
            "%.2fs full collection (%.1f million memory blocks in use); later collections only "
            "scan what is created from now on", gc.get_freeze_count() / 1e6, took,
            blocks_before / 1e6)
    except Exception:
        logger.exception("gc_watch: could not freeze the heap")


def install_window(window_cls) -> None:
    """Start the freeze countdown when the game window starts running (Client.py calls this once,
    after the class; ``run`` is wrapped, the vanilla source is untouched). Idempotent."""
    original = window_cls.run
    if getattr(original, "__wrapped__", None) is not None:
        return

    def run(self, *args, **kwargs):
        schedule_freeze()
        return original(self, *args, **kwargs)

    run.__wrapped__ = original
    run.__doc__ = original.__doc__
    window_cls.run = run


def stats() -> "dict[str, float]":
    return {"collections": _count, "total_s": _total_s, "slow": _slow_count,
            "longest_s": _longest_s}


def _reset() -> None:
    global _started_at, _count, _total_s, _slow_count, _longest_s, _freeze_timer, _frozen
    _started_at, _count, _total_s, _slow_count, _longest_s = 0.0, 0, 0.0, 0, 0.0
    if _freeze_timer is not None:
        _freeze_timer.cancel()
    _freeze_timer, _frozen = None, False

"""asset_watch.py — say so in the log when loading a game image blocks the thread that asked.

``SolarSystemWindow._load_raw_image`` reads a ship / station / planet / drone picture from disk
and decodes it the first time anything needs it, on whichever thread asked - for a ship that is
the render thread, in the middle of ``draw_ships``. The pictures are big (the 127 ship PNGs add
up to 346 MB, about 2.7 MB each), so the first sighting of each ship type in a session is a
disk read plus a decode on the render thread, holding the GIL; a frame profile showed a one-off
317 ms call inside ``draw_ships``. This wraps the loader (``install`` is called once after the
class, so the vanilla source is untouched) and logs every load that took 30 ms or more with the
file, its size and the thread, so that hitch can be confirmed - and the files that cause it
named - before anything is changed.
"""

from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

SLOW_S = 0.03


def _timed(original):
    def _load_raw_image(self, folder, filename, cache, cache_key=None, warn_missing=False):
        key = cache_key if cache_key is not None else filename
        if not filename or key in cache:                 # the common case: a cache hit, no clock
            return original(self, folder, filename, cache, cache_key, warn_missing)
        started = time.perf_counter()
        try:
            return original(self, folder, filename, cache, cache_key, warn_missing)
        finally:
            elapsed = time.perf_counter() - started
            if elapsed >= SLOW_S:
                _report(self, folder, filename, elapsed)
    _load_raw_image.__wrapped__ = original
    _load_raw_image.__doc__ = original.__doc__
    return _load_raw_image


def _report(window, folder, filename, elapsed: float) -> None:
    try:
        try:
            path = os.path.join(window._script_dir(), folder, filename)
            size_mb = os.path.getsize(path) / (1 << 20)
        except Exception:
            size_mb = -1.0
        logger.warning(
            "asset_watch: loading %s/%s took %.2fs (%.1f MB) on thread %s - the whole process "
            "waits on it", folder, filename, elapsed, size_mb, threading.current_thread().name)
    except Exception:
        pass


def install(window_cls) -> None:
    """Time ``window_cls._load_raw_image`` (Client.py calls this once, after the class)."""
    original = window_cls._load_raw_image
    if getattr(original, "__wrapped__", None) is not None:
        return
    window_cls._load_raw_image = _timed(original)

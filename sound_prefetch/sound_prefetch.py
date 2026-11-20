"""sound_prefetch.py — keep the weapon sound files' first read off the message thread.

Weapon fire sounds are loaded the first time they are heard (``AudioManager.play_weapon_sound``
-> ``_Sound(path)`` -> ``soundfile.read``). The projectile handler (``SolarSystemWindow.set_projectiles``)
plays them while holding ``self._lock``, on the message thread, and the render thread needs that same
lock every frame. So one cold read of a 0.5 MB .wav from the project drive froze the whole game for
5.4 s (freeze watchdog, 2026-10-22: the message thread inside ``soundfile._open``, the render thread
parked on ``with self._lock``). The drive is shared by several clients and stalls for seconds at times.

``install(Audio)`` (called once from Client.py, after the imports, like the other hooks) does two
things without touching the vanilla source:

  * when an ``AudioManager`` is created it starts one background thread that reads every ``*.wav``
    in ``Sounds/Weapons`` through (``start``), so each is already in Windows' file cache when the
    game first needs it - about 1.8 MB, held by the OS rather than the game, and gone again if
    the OS needs the memory;
  * it wraps ``_Sound._load`` so a sound that took 30 ms or more to load is logged with its size,
    the thread, and whether the read-ahead had reached it - a stall that is not the drive then shows
    up as such.

The switch is "Sound prefetch" on the Data Capture page (capture_settings.SOUND_PREFETCH, on by
default). Turning it on in the page starts the read at once; turning it off only stops a read that has
not begun (a file cache cannot be taken back).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

import capture_settings

logger = logging.getLogger(__name__)

SLOW_LOAD_S = 0.03          # a sound load slower than this is logged (asset_watch uses the same bar)
SLOW_READ_S = 1.0           # a whole read-ahead slower than this says the drive itself was slow
_CHUNK = 1 << 20

_lock = threading.Lock()
_directory: "Path | None" = None                  # the weapons folder, as last seen by the hook
_started: "set[str]" = set()                      # folders whose read-ahead has begun this session
_warmed_at: "dict[str, float]" = {}               # file -> monotonic time its read-ahead finished


def start(directory=None) -> bool:
    """Begin reading every .wav in ``directory`` on a background thread. True if a read began.

    ``directory`` is remembered, so a later ``start()`` (the page's switch being turned on) reads the
    same folder. No-op when the switch is off, when the folder was already read this session, or when
    it does not exist. Never blocks, never raises."""
    global _directory
    try:
        if directory is not None:
            _directory = Path(directory)
        folder = _directory
        if folder is None or not capture_settings.enabled(capture_settings.SOUND_PREFETCH):
            return False
        key = str(folder)
        with _lock:
            if key in _started:
                return False
            _started.add(key)
        threading.Thread(target=_read_folder, args=(key,), name="sound-prefetch", daemon=True).start()
        return True
    except Exception:
        logger.exception("sound_prefetch: could not start the read-ahead")
        return False


def _read_folder(key: str) -> None:
    began = time.perf_counter()
    slowest = ("", 0.0)
    count = total = 0
    try:
        names = sorted(n for n in os.listdir(key) if n.lower().endswith(".wav"))
    except OSError:
        return                       # no weapons folder: the game is silent there, nothing to warm
    for name in names:
        path = os.path.join(key, name)
        t0 = time.perf_counter()
        try:
            size = _read_through(path)
        except OSError:
            continue                 # an unreadable file is the sound loader's problem, not ours
        took = time.perf_counter() - t0
        count += 1
        total += size
        if took > slowest[1]:
            slowest = (name, took)
        with _lock:
            _warmed_at[path] = time.monotonic()
    elapsed = time.perf_counter() - began
    message = ("sound_prefetch: read %d weapon sound(s) (%.1f MB) in %.2fs, slowest %s %.2fs"
               % (count, total / (1 << 20), elapsed, slowest[0] or "-", slowest[1]))
    if elapsed >= SLOW_READ_S:
        logger.warning("%s - the drive was slow; a first-time load on the message thread would have "
                       "waited that long", message)
    else:
        logger.info(message)


def _read_through(path: str) -> int:
    size = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                return size
            size += len(chunk)


def timed_load(original):
    """``original`` (_Sound._load) with a warning logged when it took SLOW_LOAD_S or more."""
    def _load(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return original(self, *args, **kwargs)
        finally:
            elapsed = time.perf_counter() - started
            if elapsed >= SLOW_LOAD_S:
                _log_slow_load(getattr(self, "_path", None), elapsed)
    _load.__wrapped__ = original
    _load.__doc__ = original.__doc__
    return _load


def _log_slow_load(path, elapsed: float) -> None:
    try:
        key = str(path)
        with _lock:
            warmed = _warmed_at.get(key)
        try:
            size_kb = os.path.getsize(key) / 1024
        except OSError:
            size_kb = -1.0
        logger.warning(
            "sound_prefetch: loading %s took %.2fs (%.0f KB) on thread %s - the whole process waits on "
            "it; %s", os.path.basename(key), elapsed, size_kb, threading.current_thread().name,
            f"it had been read ahead {time.monotonic() - warmed:.0f}s earlier" if warmed is not None
            else "it had not been read ahead")
    except Exception:
        logger.exception("sound_prefetch: could not describe a slow sound load")


def _hook_init(original):
    def __init__(self, sounds_dir, *args, **kwargs):
        # first, so the read-ahead overlaps the manager's own preload instead of following it
        start(Path(sounds_dir) / "Weapons")
        original(self, sounds_dir, *args, **kwargs)
    __init__.__wrapped__ = original
    __init__.__doc__ = original.__doc__
    return __init__


def install(audio_module) -> bool:
    """Hook ``audio_module.AudioManager.__init__`` and ``audio_module._Sound._load``. Idempotent;
    True if it hooked something this call."""
    hooked = False
    manager = audio_module.AudioManager
    if getattr(manager.__init__, "__wrapped__", None) is None:
        manager.__init__ = _hook_init(manager.__init__)
        hooked = True
    sound = audio_module._Sound
    if getattr(sound._load, "__wrapped__", None) is None:
        sound._load = timed_load(sound._load)
        hooked = True
    return hooked


def reset_for_tests() -> None:
    global _directory
    with _lock:
        _started.clear()
        _warmed_at.clear()
    _directory = None

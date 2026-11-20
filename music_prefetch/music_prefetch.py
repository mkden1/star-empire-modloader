"""music_prefetch.py — keep the music player's file reads off the message thread.

The player starts each track with ``music_output.load(path)`` on the message thread, and the
tracks live on the project drive. The freeze watchdog caught that call 5 times in a day (when it
was still ``pygame.mixer.music.load``) with the message thread (and, through the GIL, the render
thread with it) stuck for 1.2-2.5 s -- the longest stalls of the lot -- whenever the first read of
a 3-4 MB mp3 landed on a busy drive. Since v0.5.63 the dev streams music from his own
``Audio.MusicOutput`` (the service thread decodes; ``load`` only opens the file with
``soundfile.SoundFile``), so the open is much lighter, but it is still a file read on the message
thread and the service thread's first reads still hit the drive.

``install(MusicPlayer)`` (called once from Client.py, after the class) does two things without
touching the vanilla class's source:

  * after every ``set_gameplay`` / ``_start_next_track`` it reads the track that will play NEXT on
    a background thread (``warm``), so it is already in Windows' file cache when the player loads
    it a moment later;
  * it wraps ``Audio.music_output.load`` so a slow load is logged, with the file size and whether
    a read-ahead had run -- a stall that is not the drive then shows up as such.

Vanilla's own tests load ``MusicPlayer`` straight out of Client.py's source, so keeping the hooks
outside the class also keeps those tests exactly as the developer wrote them.
"""

from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

SLOW_LOAD_S = 0.25
_CHUNK = 1 << 20

_lock = threading.Lock()
_in_flight: "set[str]" = set()
_warmed_at: "dict[str, float]" = {}


def warm(path) -> None:
    """Read ``path`` in the background (never blocks, never raises)."""
    try:
        key = str(path)
        with _lock:
            if key in _in_flight:
                return
            _in_flight.add(key)
        threading.Thread(target=_read_through, args=(key,), name="music-prefetch",
                         daemon=True).start()
    except Exception:
        logger.exception("music_prefetch: could not start a read-ahead for %s", path)


def _read_through(key: str) -> None:
    try:
        with open(key, "rb") as handle:
            while handle.read(_CHUNK):
                pass
        with _lock:
            _warmed_at[key] = time.monotonic()
    except OSError:
        pass                              # a missing track is the player's problem, not ours
    finally:
        with _lock:
            _in_flight.discard(key)


def timed_load(original):
    """``original`` (Audio.music_output.load) with a warning logged when it took SLOW_LOAD_S+."""
    def load(*args, **kwargs):
        started = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            elapsed = time.perf_counter() - started
            if elapsed >= SLOW_LOAD_S:
                _log_slow_load(args[0] if args else kwargs.get("path"), elapsed)
    load.__wrapped__ = original
    return load


def _log_slow_load(path, elapsed: float) -> None:
    try:
        key = str(path)
        with _lock:
            warmed = key in _warmed_at
            age = time.monotonic() - _warmed_at[key] if warmed else None
        try:
            size_mb = os.path.getsize(key) / (1 << 20)
        except OSError:
            size_mb = -1.0
        logger.warning(
            "music_prefetch: loading %s took %.2fs (%.1f MB, the whole process waits on it); %s",
            os.path.basename(key), elapsed, size_mb,
            f"it had been read {age:.0f}s earlier" if warmed else "it had not been read ahead")
    except Exception:
        logger.exception("music_prefetch: could not describe a slow music load")


def _warm_upcoming(player) -> None:
    try:
        playlist = player._playlist
        if playlist:
            warm(playlist[player._track_index % len(playlist)])
    except Exception:
        logger.exception("music_prefetch: could not pick the next track")


def install(player, music=None) -> None:
    """Hook ``player`` (Client.py's MusicPlayer class) and the music output's loader (``music``,
    default ``Audio.music_output`` -- the object the player calls). Idempotent."""
    if getattr(player, "_music_prefetch_installed", False):
        return
    player._music_prefetch_installed = True
    try:
        if music is None:
            from Audio import music_output as music
        if not hasattr(music.load, "__wrapped__"):
            music.load = timed_load(music.load)
    except Exception:
        logger.exception("music_prefetch: could not time Audio.music_output.load")

    for name in ("set_gameplay", "_start_next_track"):
        original = player.__dict__[name].__func__

        def make(original):
            def hooked(cls, *args, **kwargs):
                result = original(cls, *args, **kwargs)
                _warm_upcoming(cls)
                return result
            hooked.__name__ = original.__name__
            hooked.__doc__ = original.__doc__
            return hooked

        setattr(player, name, classmethod(make(original)))


def _reset() -> None:
    with _lock:
        _in_flight.clear()
        _warmed_at.clear()

"""Freeze watchdog: make the next freeze explain itself.

The /profiler only covers the minute or ten you happen to be recording, and a freeze that
lasts a second or two in a whole evening is rarely inside it. This runs permanently and costs
next to nothing: the App thread (network / message loop) and the render thread each call
``tick()`` once per frame. If either one goes quiet for ``STALL_S`` seconds, Python's
``faulthandler`` -- a C-level timer that needs neither thread nor the GIL -- writes every
thread's current stack to the log, so the dump shows the exact line a stuck thread was on (a
disk read, a lock wait, a message handler). When the thread ticks again a WARNING gives the
time and how long it was stuck. Normal frames (about 17 ms) never get near either threshold.

The stack dump has no timestamp of its own; the WARNING that follows it does.
"""
import atexit
import faulthandler
import logging
import sys
import time

logger = logging.getLogger(__name__)

STALL_S = 0.5        # dump every thread's stack once a thread has been silent this long
REPORT_S = 0.2       # warn (with the stall length) when a tick arrives this much later
REARM_EVERY_S = 0.1  # re-arming starts a timer thread; do it at most this often
GONE_S = 30.0        # a thread silent this long has exited (e.g. the window closed): ignore it

_enabled = False
_file = None
_armed_at = float("-inf")  # so the very first tick always arms the timer
_last = {"app": None, "render": None}


def start(file=None) -> bool:
    """Turn the watchdog on. ``file`` must be a real file object (faulthandler writes to its
    descriptor); by default the crash log's own handle, so dumps land beside the log lines."""
    global _enabled, _file
    if _enabled:
        return True
    if file is None:
        try:
            import crash_log
            file = getattr(crash_log, "_fault_fp", None)
        except Exception:
            file = None
    if file is None:
        file = sys.stderr
    if file is None:  # windowed build with no log file: nowhere to write
        return False
    # A watchdog switched off and on again (the Data Capture page) must not read the gap while
    # it was off as one enormous stall.
    _last.update(app=None, render=None)
    _file, _enabled = file, True
    atexit.register(stop)
    return True


def stop() -> None:
    global _enabled
    _enabled = False
    try:
        faulthandler.cancel_dump_traceback_later()
    except Exception:
        pass


def tick(name: str, now: "float | None" = None) -> None:
    """Called once per frame by the ``"app"`` and ``"render"`` threads."""
    global _armed_at
    if not _enabled:
        return
    now = time.monotonic() if now is None else now
    prev = _last.get(name)
    _last[name] = now
    if prev is not None and now - prev >= REPORT_S:
        logger.warning(
            "freeze_watchdog: the %s thread was stalled for %.2fs "
            "(the thread stacks from the stall are in the traceback dump just above)",
            name, now - prev)
    other = _last["render" if name == "app" else "app"]
    if other is not None and STALL_S <= now - other < GONE_S:
        return  # the other thread is stuck: leave the timer alone so it fires
    if now - _armed_at >= REARM_EVERY_S:
        _armed_at = now
        try:
            faulthandler.dump_traceback_later(STALL_S, repeat=False, file=_file)
        except Exception:
            pass


def _reset() -> None:
    """Forget all state (tests)."""
    global _enabled, _file, _armed_at
    stop()
    _file, _armed_at = None, float("-inf")
    _last.update(app=None, render=None)

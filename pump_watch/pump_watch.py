"""pump_watch.py — say so in the log when pygame.event.get() blocks the whole client.

``pygame.event.get()`` pumps SDL's event queue, and pygame does that WITHOUT releasing Python's
GIL. If the pump blocks inside Windows or SDL for half a second, the render thread is stuck in
it -- and so is every other Python thread, because none of them can run without the GIL: the
message thread, which drains the network, stalls for exactly as long. The freeze watchdog's
dumps showed exactly this pattern (render thread parked in ``pygame.event.get()``, message
thread waiting at its frame limiter, both stalled for the same 0.3-3.5 s) in over half of the
stalls left once the logger stalls were fixed -- but a Python stack dump cannot say WHY a C call
is slow. This wrapper times the call and, when it was slow, logs what the pump returned and
what was happening around it, so the next stalls explain themselves:

  * the event types in the batch (a burst of device / audio / window events is a trigger),
  * whether our window was the active one and which program was in the foreground,
  * how many such stalls have happened so far this session.

``get_events()`` is a drop-in for ``pygame.event.get()`` and costs two clock reads per call.

``disable_joysticks()`` shuts SDL's joystick subsystem down (``install_init_hook()`` makes every
``pygame.init()`` call it). The game has no gamepad support, but ``pygame.init()`` starts it, and
SDL runs joystick / HID device detection from inside this same event pump. Evidence, 2026-10-10:
two bare pygame windows pumped events side by side for 30 minutes, one with the joystick
subsystem running and one without. At 16:56:41 the one WITH it blocked in ``event.get()`` for
0.88 s, 0.50 s and 0.39 s; the one without never took more than 5 ms. The real client, which
has the subsystem running, froze at the very same seconds with its render thread parked in
``pygame.event.get()`` (0.61, 0.68, 0.78, 0.51, 0.28 s).
"""

from __future__ import annotations

import collections
import logging
import os
import sys
import time

import pygame

logger = logging.getLogger(__name__)

_click_diag = None       # click_diag's module once looked up; False when that mod is not installed

SLOW_S = 0.25            # an event.get() slower than this is logged
_slow_count = 0
_slow_total_s = 0.0


def get_events():
    """``pygame.event.get()``, logging the call if it blocked for SLOW_S or longer."""
    t0 = time.perf_counter()
    events = pygame.event.get()
    elapsed = time.perf_counter() - t0
    if elapsed >= SLOW_S:
        _report(elapsed, events)
    _note_events(events)
    return events


def _note_events(events) -> None:
    """Hand the batch to click_diag, which remembers left presses so a swallowed click can be reported. click_diag is an
    optional mod: it is looked up once, on first use, and without it this does nothing."""
    global _click_diag
    if _click_diag is None:
        try:
            import click_diag
            _click_diag = click_diag
        except ImportError:
            _click_diag = False
    if _click_diag:
        _click_diag.note_events(events)


def _report(elapsed: float, events) -> None:
    global _slow_count, _slow_total_s
    _slow_count += 1
    _slow_total_s += elapsed
    try:
        kinds = collections.Counter(pygame.event.event_name(e.type) for e in events)
        batch = ", ".join(f"{name} x{n}" if n > 1 else name
                          for name, n in kinds.most_common(8)) or "none"
        try:
            active = pygame.display.get_active()
        except pygame.error:
            active = "?"
        logger.warning(
            "pump_watch: pygame.event.get() blocked for %.2fs (the GIL is held, so every thread "
            "stalled); batch: %s; window active=%s; foreground program=%s; this session: %d "
            "such stall(s), %.1fs",
            elapsed, batch, active, _foreground_program(), _slow_count, _slow_total_s)
    except Exception:           # a diagnostic must never take the render loop down
        logger.exception("pump_watch: could not describe a slow event pump")


def _foreground_program() -> str:
    """The .exe name (never the window title) of the program in the foreground right now."""
    if not sys.platform.startswith("win"):
        return "?"
    try:
        import ctypes
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        user32.GetForegroundWindow.restype = wintypes.HWND
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return "none"
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, pid.value)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return f"pid {pid.value}"
        try:
            size = wintypes.DWORD(520)
            buf = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value)
            return f"pid {pid.value}"
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return "?"


def disable_joysticks() -> bool:
    """Shut down SDL's joystick subsystem (call after ``pygame.init()``). Returns True if it was
    running. Set STAR_EMPIRE_KEEP_JOYSTICK=1 to leave it alone."""
    if os.environ.get("STAR_EMPIRE_KEEP_JOYSTICK"):
        return False
    try:
        was_running = pygame.joystick.get_init()
        pygame.joystick.quit()
        if was_running:
            logger.info("pump_watch: SDL joystick subsystem shut down "
                        "(set STAR_EMPIRE_KEEP_JOYSTICK=1 to keep it)")
        return bool(was_running)
    except Exception:
        return False


def install_init_hook() -> None:
    """Make ``pygame.init()`` shut the joystick subsystem down right after it has started it
    (Client.py calls this once at import). Idempotent."""
    original = pygame.init
    if getattr(original, "_pump_watch_hook", False):
        return

    def init(*args, **kwargs):
        result = original(*args, **kwargs)
        disable_joysticks()
        return result

    init._pump_watch_hook = True
    init.__doc__ = original.__doc__
    init.__wrapped__ = original
    pygame.init = init


def stats() -> "tuple[int, float]":
    """(slow pumps so far, their total seconds) -- for tests and the log."""
    return _slow_count, _slow_total_s


def _reset() -> None:
    global _slow_count, _slow_total_s
    _slow_count, _slow_total_s = 0, 0.0

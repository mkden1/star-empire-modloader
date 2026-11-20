"""options_saver.py — save options.txt from the game window without stopping the game.

Every time the player closes a window, drops a dragged panel or flips a toggle, the render
thread calls ``LoginScreen.save_options``, which READS options.txt (to merge in the hotbars other
running clients saved) and then REWRITES it, on the render thread. The freeze watchdog caught
that read taking 3.12 s on 2026-10-10 (closing the Stats window; the file is 6 KB) and found it
in 7 of ~240 stall dumps over two days: the shared project drive hiccups for seconds now and then
and the render thread - and, through the GIL, every thread - waits for it.

``install(LoginScreen)`` replaces ``save_options`` so that:

  * called from the game window's thread: the arguments are queued and a background thread makes
    the real call. Every argument means "set this, None = leave it", so queued calls are merged
    (the latest value of each field wins) and a burst of saves becomes one read + one write. The
    file ends up exactly as it would have after the calls ran one by one.
  * called from any other thread (the login screen saving after authentication, before the game
    window exists and reads the layout back): unchanged and synchronous, after anything still
    queued has been written so the older save can never land on top of the newer one.

Nothing else reads the values the vanilla function caches on the login screen while the game
window is running (the window keeps its own copies and only ever passes them in), so deferring
the call is invisible to the game. Queued saves are written at exit (``flush`` is registered with
atexit), and at most a few seconds of layout changes could be lost to a hard kill.
"""

from __future__ import annotations

import atexit
import inspect
import logging
import threading
import time

logger = logging.getLogger(__name__)

WINDOW_THREAD_NAME = "SolarSystemWindow"

_state_lock = threading.Lock()
_call_lock = threading.RLock()          # one real save_options at a time, from any thread
# id(login) -> (login, the real save_options, merged keyword arguments)
_pending: "dict[int, tuple[object, object, dict]]" = {}
_wake = threading.Event()
_busy = False
_thread: "threading.Thread | None" = None


def _is_window_thread() -> bool:
    return threading.current_thread().name == WINDOW_THREAD_NAME


def _merge(login, original, values: dict) -> None:
    with _state_lock:
        entry = _pending.get(id(login))
        if entry is None:
            _pending[id(login)] = (login, original, dict(values))
        else:
            entry[2].update(values)
    _wake.set()
    _ensure_started()


def _ensure_started() -> None:
    global _thread
    with _state_lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(target=_worker, name="options-saver", daemon=True)
        _thread.start()


def _worker() -> None:
    global _busy
    while True:
        _wake.wait()
        with _state_lock:
            _wake.clear()
            batch = list(_pending.values())
            _pending.clear()
            _busy = bool(batch)
        for login, original, values in batch:
            try:
                with _call_lock:
                    original(login, **values)
            except Exception:
                logger.exception("options_saver: save_options failed")
        with _state_lock:
            _busy = False


def flush(timeout: "float | None" = 10.0) -> bool:
    """Wait until every queued save has been written. True if the queue drained in time."""
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        with _state_lock:
            idle = not _pending and not _busy
        if idle:
            return True
        if deadline is not None and time.monotonic() >= deadline:
            return False
        time.sleep(0.005)


def install(login_cls) -> None:
    """Hook ``login_cls.save_options`` (Client.py calls this once at import). Idempotent."""
    if getattr(login_cls.save_options, "_options_saver", False):
        return
    original = login_cls.save_options
    signature = inspect.signature(original)

    def save_options(self, *args, **kwargs):
        if not _is_window_thread():
            flush()                                   # older queued saves land first
            with _call_lock:
                return original(self, *args, **kwargs)
        bound = signature.bind(self, *args, **kwargs)
        values = {name: value for name, value in list(bound.arguments.items())[1:]
                  if value is not None}
        _merge(self, original, values)

    save_options._options_saver = True
    save_options.__wrapped__ = original
    save_options.__doc__ = original.__doc__
    login_cls.save_options = save_options
    atexit.register(flush)


def _reset() -> None:
    """Forget queued work (tests)."""
    global _busy
    with _state_lock:
        _pending.clear()
        _busy = False

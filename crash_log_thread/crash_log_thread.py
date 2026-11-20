"""crash_log_thread.py - the client's log file is written by a background thread.

A ``FileHandler`` flushes the file inside every ``logger.x()`` call, on the calling thread. When the drive stalls
(the freeze watchdog caught the E: drive holding file calls for 1-3 s) that call stalls too -- and the message
thread logs. Records now go to a queue and this module's own thread waits on the disk: the thread that logs only
formats the message. The crash hooks wait for the queue to drain (``drain``) before they return, so a crash is never
reported as "logged" while its line is still queued.

The one trade-off is that a hard process kill (a native crash, ``os._exit``) can lose the last few queued lines,
which would have been flushed immediately before.

The dev's crash_log.py (patched, see crash_log.py.patch) hands its handlers to ``add_handlers`` instead of adding
them to the root logger itself, and its flush asks ``drain`` and ``real_handlers``. Pure standard library, like
crash_log.py: it is imported before anything else of the client is.
"""

import atexit
import logging
import logging.handlers
import queue
import time

_log_queue = None     # records waiting for the logging thread (see route_through_queue)
_log_listener = None  # the background thread that actually writes them
_real_handlers = []   # the file / console handlers that listener drives


def route_through_queue(root, handlers):
    """Make ``root`` hand every record to a queue, with a background thread driving ``handlers``.

    Returns the listener, or None if there was nothing to drive."""
    global _log_queue, _log_listener, _real_handlers
    if not handlers:
        return None
    _real_handlers = list(handlers)
    _log_queue = queue.Queue()      # unbounded: a stalled disk must never make logging raise
    root.addHandler(logging.handlers.QueueHandler(_log_queue))
    _log_listener = logging.handlers.QueueListener(
        _log_queue, *handlers, respect_handler_level=True)
    _log_listener.start()
    atexit.register(stop_listener)
    return _log_listener


def add_handlers(root, handlers) -> None:
    """What crash_log.install() does with the handlers it built: the background thread drives them. If that cannot
    be set up, fall back to direct handlers on ``root`` (the log is written, on the calling thread, as before)."""
    try:
        routed = route_through_queue(root, handlers)
    except Exception:
        routed = None
    if routed is None:
        for handler in handlers:
            root.addHandler(handler)


def real_handlers():
    """The handlers the background thread drives (they are not on the root logger: it holds only the queue)."""
    return list(_real_handlers)


def drain(timeout=1.5):
    """Wait (up to ``timeout`` s) until the logging thread has written everything queued so far,
    so a crash hook never reports "logged" while the line is still in the queue."""
    q = _log_queue
    if q is None:
        return
    deadline = time.monotonic() + timeout
    while q.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.005)


def stop_listener():
    """At exit: write what is queued, then stop the logging thread."""
    global _log_listener
    listener, _log_listener = _log_listener, None
    if listener is not None:
        try:
            listener.stop()
        except Exception:
            pass

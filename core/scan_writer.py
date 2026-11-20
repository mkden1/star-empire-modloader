"""scan_writer.py — offloads scan-logger file writes onto a single background thread, so the
network message-handling thread that receives PS_STATION_CARGO / RC_BOT_STATE / etc. never blocks
on file I/O or cross-process lock contention.

Why this exists
----------------
Even after excluding noisy/high-frequency fields from each logger's own "skip if unchanged" check
(colony_scan_logger.py, bot_fleet_scan_logger.py), a REAL write still means: acquire a cross-
process lock (scan_file_lock — up to 4 separate client processes, one per logged-in character,
share these files), read the whole file, reparse it, and rewrite it — all synchronously, on the
thread that just received a server message. A player reported real, felt lag specifically when
docking (PS_STATION_CARGO fires on every dock) — however fast one write normally is on its own,
contention with the other characters' own concurrent writes to the SAME shared file can make any
one of them wait, and that wait was landing directly on the game's own responsiveness.

This module moves the actual file I/O off that thread entirely. A ``record_*`` function still
does its cheap, in-memory dedup check synchronously (near-instant, no I/O) — only the real write
(lock acquisition included) is handed off to one dedicated background thread via a queue, so it
happens whenever it happens, never blocking whatever the game was about to do next.

``submit`` never raises and never blocks the caller beyond a cheap ``queue.put`` — a failure
enqueuing or executing a write is logged and swallowed, same best-effort contract as every
record_* function that calls it. ``flush`` is test-only in practice: real callers never need to
wait for their own write to land, but a test asserting on file content needs the write to have
actually happened first, and there's no signal for that otherwise.
"""

from __future__ import annotations

import logging
import queue
import threading

logger = logging.getLogger(__name__)

_queue: "queue.Queue" = queue.Queue()
_thread: "threading.Thread | None" = None
_start_lock = threading.Lock()


def _worker() -> None:
    while True:
        fn, args, kwargs = _queue.get()
        try:
            fn(*args, **kwargs)
        except Exception:
            logger.exception("scan_writer: background write failed")
        finally:
            _queue.task_done()


def _ensure_started() -> None:
    global _thread
    if _thread is not None:
        return
    with _start_lock:
        if _thread is None:
            t = threading.Thread(target=_worker, name="scan-writer", daemon=True)
            t.start()
            _thread = t


def submit(fn, *args, **kwargs) -> None:
    """Enqueue ``fn(*args, **kwargs)`` to run on the background writer thread. Starts the thread
    lazily on first use, so importing this module (or calling a record_* function that never
    actually writes, e.g. a deduped repeat) costs nothing."""
    try:
        _ensure_started()
        _queue.put_nowait((fn, args, kwargs))
    except Exception:
        logger.exception("scan_writer: failed to enqueue write")


def dispatch(out_path, fn, *args, **kwargs) -> None:
    """Production (``out_path`` is None, i.e. the real scan_data file): queue the write for the
    background thread. A test passing its own ``out_path``: run it right here, so "the file is
    written when record_*() returns" keeps holding for the existing tests."""
    if out_path is None:
        submit(fn, *args, **kwargs)
    else:
        fn(*args, **kwargs)


def flush() -> None:
    """Blocks until every write enqueued so far has been processed. Test-only in practice --
    lets a test assert on file content deterministically without racing the background thread.
    A no-op (returns immediately) if nothing has ever been submitted, since the worker thread
    itself is only started lazily by submit()."""
    if _thread is not None:
        _queue.join()

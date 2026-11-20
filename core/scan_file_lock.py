"""scan_file_lock.py — a small cross-process advisory lock for the shared scan_data/*.jsonl
files.

Why this exists
----------------
Multiple client processes (one per logged-in character) can each independently read-modify-write
the SAME shared scan file (e.g. colony_scan.jsonl, bot_fleet_scan.jsonl) whenever their own bot or
station pushes an update. Without serializing that read-modify-write across processes, two writes
close together race: process A reads the file (sees everything so far), process B reads it too
before A's write lands, then A writes its update, then B writes ITS update -- built from a read
that never saw A's write -- silently discarding it. This is not theoretical: it's what actually
happened to colony_scan.jsonl this session (went from ~70 rows to 1).

Windows-only (msvcrt), matching this project's only real target platform. Best-effort: if the
lock can't be acquired within a bounded wait, the caller proceeds anyway rather than hanging the
game's network thread indefinitely -- a slim residual race is a much smaller problem than a
client-visible stall.
"""

from __future__ import annotations

import contextlib
import os
import time

try:
    import msvcrt
except ImportError:  # pragma: no cover - Windows only
    msvcrt = None


@contextlib.contextmanager
def locked(path: str, timeout: float = 2.0, poll_interval: float = 0.02):
    """Holds an exclusive lock on ``<path>.lock`` for the duration of the with-block, so
    concurrent read-modify-write cycles on ``path`` from other processes serialize instead of
    racing. Best-effort: proceeds WITHOUT the lock if msvcrt is unavailable, the lock file can't
    be opened, or the lock couldn't be acquired within ``timeout`` seconds -- never raises, never
    blocks indefinitely."""
    if msvcrt is None:
        yield
        return
    lock_path = path + ".lock"
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    except OSError:
        yield
        return
    acquired = False
    try:
        # LK_NBLCK needs at least one real byte to lock -- a freshly created lock file is empty.
        try:
            if os.fstat(fd).st_size < 1:
                os.write(fd, b"\0")
        except OSError:
            pass
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                acquired = True
                break
            except OSError:
                time.sleep(poll_interval)
        yield
    finally:
        if acquired:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        try:
            os.close(fd)
        except OSError:
            pass

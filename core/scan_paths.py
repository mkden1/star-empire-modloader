"""scan_paths.py — where the scan loggers keep their files, worked out without touching the disk.

Every scan logger used to build its default file path with ``os.makedirs(scan_data,
exist_ok=True)`` and did so on the network/message thread, once per server push that changed
anything (a trade bot's RC_BOT_STATE alone is several a second). ``makedirs`` on a folder that
already exists is still a filesystem call (a failed mkdir plus a stat), and the project lives on a
hard drive that several clients share. The freeze watchdog (freeze_watchdog.py) caught the message
thread parked inside exactly that call, for 1-3 seconds at a time, in roughly two thirds of all
the stalls it recorded in a session -- while the same call measured at 0.08 ms when the drive was
quiet. The drive is slow only occasionally; the client must simply never wait on it.

So: the path is a pure string computation, done once per process and remembered. The directory is
created by whichever background writer thread is about to write (``ensure_dir``), where waiting
on the disk costs the game nothing.
"""

from __future__ import annotations

import os

import modloader

# _internal/ lives at <project>/<client>/_internal/ -- scan_data/ is shared by every client and by
# colony_tool/ (a sibling repo) and lives one level further up, at the true project root.
_root: "str | None" = None
_paths: "dict[str, str]" = {}


def root() -> str:
    """The shared scan_data directory. No filesystem access."""
    global _root
    if _root is None:
        # scan_data/ is shared with colony_tool/ (a sibling repo) and lives one level above
        # the client's folder, at the true project root.
        project_root = str(modloader.PROJECT_DIR)
        _root = os.path.join(project_root, "scan_data")
    return _root


def path(filename: str) -> str:
    """``scan_data/<filename>``, computed once. No filesystem access, safe on any thread."""
    cached = _paths.get(filename)
    if cached is None:
        cached = _paths[filename] = os.path.join(root(), filename)
    return cached


def ensure_dir(file_path: str) -> None:
    """Create the folder that will hold ``file_path``. Touches the disk: call it only from a
    background writer (or a test), never from the message or render thread."""
    os.makedirs(os.path.dirname(file_path), exist_ok=True)


def set_root_for_tests(new_root: "str | None") -> None:
    """Point every default path at ``new_root``. None goes back to the default: the real scan_data folder, or, in a
    test run (modloader.setup_for_tests redirects the whole run to a throwaway folder), that throwaway folder -- a test
    that cleans up after itself must never open the real archive to the tests that run after it."""
    global _root
    scratch = getattr(modloader, "_scratch_scan_data", None)
    _root = os.path.join(scratch, "scan_data") if new_root is None and scratch else new_root
    _paths.clear()

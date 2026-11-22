"""mod_digest.py - a hash of what a mod folder holds that the game runs, and the comparison of two sets of mods.

Used by ``change_mods`` (what changed since the last start) and ``mod_updates`` (which mods a newer version on the net changes). Two
folders, or a folder and the files of a downloaded zip, have the same digest exactly when the files that matter are the same:
the code and data of a mod, not its README, screenshots, tests or caches (the same things the package's ``modctl`` does not copy
into ``mods/``), and not file times or the line endings of text files (Windows and Unix ones hash alike), so a mod that was only copied again, or
checked out on Windows, is not "updated".

Standard library only.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
from pathlib import Path

# What is not part of a mod as the game runs it: these patterns are the package's modctl.NOT_COPIED.
_NOT_RUN = ("__pycache__", "*.pyc", "*.pyo", "test_*", "conftest.py", "expected_failures.txt", "README.md", "screenshots")
# What a catalogue (mods_available/) does not need either: the tests and caches (it keeps README.md and screenshots: the picker shows them).
_NOT_KEPT = ("__pycache__", "*.pyc", "*.pyo", "test_*", "conftest.py", "expected_failures.txt")


def _matches(name: str, patterns) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def counts(name: str) -> bool:
    """Does a file or folder with this name matter to the game?"""
    return not _matches(name, _NOT_RUN)


def worth_keeping(name: str) -> bool:
    """Does a catalogue keep a file or folder with this name?"""
    return not _matches(name, _NOT_KEPT)


def _kept(relative_path: str, keep) -> bool:
    return all(keep(part) for part in relative_path.split("/"))


def _order(relative_path: str) -> tuple:
    """The order ``os.walk`` (top-down, names sorted) visits files in: a folder's own files first, then its sub-folders."""
    *folders, name = relative_path.split("/")
    return (*(("1", folder) for folder in folders), ("0", name))


def _same_text(data: bytes) -> bytes:
    """A text file with Windows line endings is the same file as with Unix ones (a git clone on Windows has the one, a zip from
    GitHub the other); a binary file (it has a NUL byte) is left exactly as it is."""
    return data if b"\0" in data else data.replace(b"\r\n", b"\n")


def digest_files(files: "dict[str, bytes]") -> str:
    """The digest of a mod's files given as ``{relative path with /: bytes}`` (a zip's contents). Files that do not matter are ignored,
    and so are the line endings of text files."""
    hasher = hashlib.sha1()
    for path in sorted((p for p in files if _kept(p, counts)), key=_order):
        hasher.update(path.encode("utf-8", "replace"))
        hasher.update(b"\0")
        hasher.update(_same_text(files[path]))
        hasher.update(b"\0")
    return hasher.hexdigest()


def read_folder(folder: Path, keep=counts) -> "dict[str, bytes]":
    """The files of a mod folder that pass *keep* (default: those that matter to the game), by relative path with ``/``."""
    found: "dict[str, bytes]" = {}
    for base, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if keep(d)]
        for name in names:
            if not keep(name):
                continue
            path = Path(base) / name
            try:
                found[path.relative_to(folder).as_posix()] = path.read_bytes()
            except OSError:
                continue
    return found


def digest(folder: Path) -> str:
    """The digest of a mod folder on disk (equal to ``digest_files`` of the same files)."""
    return digest_files(read_folder(Path(folder)))


def digests(mods_dir: Path, require_mod_json: bool = False) -> "dict[str, str]":
    """Every mod folder of *mods_dir* with its digest. Dot- and underscore-folders and loose files are not mods; with
    *require_mod_json* a folder without a ``mod.json`` is not one either."""
    try:
        folders = sorted(p for p in Path(mods_dir).iterdir()
                         if p.is_dir() and not p.name.startswith((".", "_")) and (not require_mod_json or (p / "mod.json").is_file()))
    except OSError:
        return {}
    return {folder.name: digest(folder) for folder in folders}


def diff(old: "dict[str, str]", new: "dict[str, str]") -> "tuple[list[str], list[str], list[str]]":
    """``(updated, added, removed)`` mod names between two sets of digests, each sorted."""
    updated = sorted(name for name in new if name in old and new[name] != old[name])
    added = sorted(name for name in new if name not in old)
    removed = sorted(name for name in old if name not in new)
    return updated, added, removed

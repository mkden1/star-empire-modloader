"""Tests for mod_digest.py: what counts as part of a mod, that a folder and a zip's files hash alike, and the comparison."""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
import unittest
from pathlib import Path

import mod_digest as md


def write(folder: Path, files: "dict[str, bytes | str]") -> None:
    for relative, content in files.items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)


NESTED = {"mod.json": "{}", "thing.py": "x = 1\n", "Client.py.patch": "@@ a @@\n", "sub/inner.py": "y = 2\n", "sub/deep/more.json": "[1]",
          "zeta/last.py": "z = 3\n", "a_first.txt": "data"}


def old_change_mods_digest(folder: Path) -> str:
    """What change_mods computed before it shared this module (walk order, sorted names), so snapshots already on disk stay valid."""
    hasher = hashlib.sha1()
    for base, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in {"__pycache__", "screenshots"})
        for name in sorted(names):
            if name in {"README.md"} or name.endswith((".pyc", ".pyo")):
                continue
            path = Path(base) / name
            hasher.update(path.relative_to(folder).as_posix().encode("utf-8", "replace"))
            hasher.update(b"\0")
            hasher.update(path.read_bytes())
            hasher.update(b"\0")
    return hasher.hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.mod = self.root / "alpha"
        write(self.mod, NESTED)


class RuleTests(unittest.TestCase):
    def test_what_matters_to_the_game(self):
        for name in ("thing.py", "mod.json", "Client.py.patch", "data.json", "sub", "web", "style.css", "testing.py", "my_test_helper.py"):
            self.assertTrue(md.counts(name), name)
        for name in ("README.md", "screenshots", "__pycache__", "x.pyc", "x.pyo", "test_x.py", "test_data", "conftest.py", "expected_failures.txt"):
            self.assertFalse(md.counts(name), name)

    def test_what_a_catalogue_keeps(self):
        for name in ("README.md", "screenshots", "thing.py", "mod.json"):
            self.assertTrue(md.worth_keeping(name), name)
        for name in ("__pycache__", "x.pyc", "test_x.py", "conftest.py", "expected_failures.txt"):
            self.assertFalse(md.worth_keeping(name), name)


class DigestTests(Base):
    def test_the_same_files_have_the_same_digest(self):
        self.assertEqual(md.digest(self.mod), md.digest(self.mod))
        write(self.root / "beta", NESTED)
        self.assertEqual(md.digest(self.mod), md.digest(self.root / "beta"))               # (the folder's name is not part of it)

    def test_a_folder_and_the_files_of_a_zip_hash_alike(self):
        files = {path: (self.mod / path).read_bytes() for path in NESTED}
        self.assertEqual(md.digest(self.mod), md.digest_files(files))
        self.assertEqual(md.digest_files(md.read_folder(self.mod)), md.digest(self.mod))

    def test_the_order_files_come_in_does_not_matter_for_a_zip(self):
        files = {path: (self.mod / path).read_bytes() for path in NESTED}
        shuffled = dict(reversed(list(files.items())))
        self.assertEqual(md.digest_files(files), md.digest_files(shuffled))

    def test_it_is_the_digest_change_mods_always_had_for_a_folder_of_runtime_files(self):
        self.assertEqual(md.digest(self.mod), old_change_mods_digest(self.mod))

    def test_a_changed_added_removed_or_renamed_file_changes_it(self):
        before = md.digest(self.mod)
        (self.mod / "thing.py").write_text("x = 2\n", encoding="utf-8")
        changed = md.digest(self.mod)
        self.assertNotEqual(before, changed)
        write(self.mod, {"new.patch": "@@ b @@\n"})
        added = md.digest(self.mod)
        self.assertNotEqual(changed, added)
        (self.mod / "new.patch").unlink()
        self.assertEqual(md.digest(self.mod), changed)
        (self.mod / "thing.py").rename(self.mod / "other.py")
        self.assertNotEqual(md.digest(self.mod), changed)

    def test_a_file_that_moves_into_a_folder_changes_it(self):
        before = md.digest(self.mod)
        (self.mod / "thing.py").rename(self.mod / "sub" / "thing.py")
        self.assertNotEqual(md.digest(self.mod), before)

    def test_what_does_not_matter_does_not_count(self):
        before = md.digest(self.mod)
        write(self.mod, {"README.md": "# a", "screenshots/a.png": b"x", "__pycache__/t.cpython-314.pyc": b"x", "thing.pyc": b"x",
                         "test_thing.py": "assert False", "conftest.py": "x", "expected_failures.txt": "x", "sub/test_inner.py": "x",
                         "sub/__pycache__/i.pyc": b"x"})
        self.assertEqual(md.digest(self.mod), before)
        files = md.read_folder(self.mod, keep=lambda name: True)
        self.assertIn("test_thing.py", files)
        self.assertEqual(md.digest_files(files), before)                                   # (a zip that carries them hashes the same)

    def test_windows_and_unix_line_endings_hash_alike(self):
        unix = self.root / "unix"
        windows = self.root / "windows"
        write(unix, {"mod.json": "{}\n", "a.py": "x = 1\ny = 2\n", "sub/b.patch": "@@ a @@\n c\n"})
        write(windows, {"mod.json": "{}\r\n", "a.py": "x = 1\r\ny = 2\r\n", "sub/b.patch": "@@ a @@\r\n c\r\n"})
        self.assertEqual(md.digest(unix), md.digest(windows))
        self.assertEqual(md.digest_files({"a.py": b"x = 1\r\n"}), md.digest_files({"a.py": b"x = 1\n"}))

    def test_a_real_difference_in_a_text_file_still_counts_whatever_its_line_endings(self):
        self.assertNotEqual(md.digest_files({"a.py": b"x = 1\r\n"}), md.digest_files({"a.py": b"x = 2\n"}))
        self.assertNotEqual(md.digest_files({"a.py": b"x = 1\n\n"}), md.digest_files({"a.py": b"x = 1\n"}))        # (a blank line is one)

    def test_a_binary_file_is_taken_exactly_as_it_is(self):
        self.assertNotEqual(md.digest_files({"data.bin": b"\x00\r\n"}), md.digest_files({"data.bin": b"\x00\n"}))

    def test_file_times_do_not_count(self):
        before = md.digest(self.mod)
        later = time.time() + 1000
        for path in self.mod.rglob("*"):
            os.utime(path, (later, later))
        self.assertEqual(md.digest(self.mod), before)

    def test_read_folder_can_keep_what_a_catalogue_keeps(self):
        write(self.mod, {"README.md": "# a", "screenshots/a.png": b"x", "test_thing.py": "x", "__pycache__/t.pyc": b"x"})
        files = md.read_folder(self.mod, keep=md.worth_keeping)
        self.assertIn("README.md", files)
        self.assertIn("screenshots/a.png", files)
        self.assertNotIn("test_thing.py", files)
        self.assertFalse(any("__pycache__" in path for path in files))

    def test_an_empty_folder_has_a_digest(self):
        (self.root / "empty").mkdir()
        self.assertEqual(md.digest(self.root / "empty"), hashlib.sha1().hexdigest())


class DigestsTests(Base):
    def test_every_mod_folder_is_listed_and_nothing_else(self):
        write(self.root / "beta", {"mod.json": "{}"})
        write(self.root / ".replaced", {"old.py": "x"})
        write(self.root / "_private", {"x.py": "x"})
        (self.root / "stray.txt").write_text("not a mod", encoding="utf-8")
        got = md.digests(self.root)
        self.assertEqual(sorted(got), ["alpha", "beta"])
        self.assertNotEqual(got["alpha"], got["beta"])

    def test_a_folder_without_mod_json_can_be_left_out(self):
        write(self.root / "notes", {"readme.txt": "just notes"})
        self.assertEqual(sorted(md.digests(self.root)), ["alpha", "notes"])
        self.assertEqual(sorted(md.digests(self.root, require_mod_json=True)), ["alpha"])

    def test_a_missing_folder_has_no_mods(self):
        self.assertEqual(md.digests(self.root / "nowhere"), {})


class DiffTests(unittest.TestCase):
    def test_updated_new_and_removed_are_told_apart_and_sorted(self):
        old = {"a": "1", "b": "1", "c": "1", "d": "1"}
        new = {"a": "1", "b": "2", "d": "9", "e": "1", "f": "1"}
        self.assertEqual(md.diff(old, new), (["b", "d"], ["e", "f"], ["c"]))

    def test_nothing_changed_is_nothing(self):
        self.assertEqual(md.diff({"a": "1"}, {"a": "1"}), ([], [], []))
        self.assertEqual(md.diff({}, {}), ([], [], []))


if __name__ == "__main__":
    unittest.main()

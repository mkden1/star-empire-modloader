"""Tests for the launcher half of mod_updates.py (the package's modloader/ folder): the listing and downloads (faked GitHub, including
hostile answers), comparing with what is installed, swapping the files in (with rollback), the combined check and apply, the bar,
and a core mod that is older than this one."""

from __future__ import annotations
import modloader

import dataclasses
import importlib.abc
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

import mod_digest
import mod_updates as mu
import test_mod_updates as t

replace = dataclasses.replace

SHA1, SHA2, SHA3, SHA4 = t.SHA1, t.SHA2, "c" * 40, "d" * 40
NOW = 2_000_000.0

LOADER_V1 = {
    "modloader.py": "VERSION = 1\n",
    "start_modded_client.py": "print('launch')\n",
    "modctl.py": "def update(names, available_dir, installed_dir):\n    return [], [], []\n",
    "modpicker.py": "PICKER = 1\n",
    "modctl.bat": "@echo off\n",
}
LOADER_V2 = {
    "modloader.py": "VERSION = 2\n",
    "start_modded_client.py": "import modctl\nmodctl.refresh_at_launch('.')\n",
    "modctl.py": "def update(names, available_dir, installed_dir):\n    return [], [], []\n\n\ndef refresh_at_launch(root):\n    return ''\n",
    "modpicker.py": "PICKER = 1\n",
    "modctl.bat": "@echo off\n",
    "MOD_AUTHORS_README.md": "# for mod authors\n",
}
CHANGED_V2 = ["modctl.py", "modloader.py", "start_modded_client.py"]


def raw(files):
    return {name: (text.encode("utf-8") if isinstance(text, str) else text) for name, text in files.items()}


class FakeGitHub:
    """What ``mod_updates.http_get`` is for both branches: the branch list (``github.com/<repo>.git/info/refs``), the REST API's contents
    listing of modloader/ (the only REST call there is), the raw files, and the mods zip. The REST API's commits endpoint does not
    exist here: nothing may ask for it. Hostile answers are set per test."""

    def __init__(self, mods_sha=SHA1, loader_sha=SHA3, zip_bytes=b"", loader_files=None, branch=mu.DEFAULT_LOADER_BRANCH,
                 mods_branch=mu.DEFAULT_BRANCH):
        self.mods_sha, self.loader_sha, self.branch, self.mods_branch = mods_sha, loader_sha, branch, mods_branch
        self.zip_bytes = zip_bytes
        self.files = raw(loader_files or {})
        self.listing = None                    # a (status, bytes) that replaces the contents answer
        self.served: "dict[str, bytes]" = {}   # name -> what is served for that raw file instead of the real one
        self.fail: "dict[str, object]" = {}    # url fragment -> the UpdateError text (or the exception) raised for it
        self.calls: list = []

    def urls(self, fragment):
        return [url for url, _headers in self.calls if fragment in url]

    def __call__(self, url, headers=None, timeout=12.0, max_bytes=0):
        headers = dict(headers or {})
        self.calls.append((url, headers))
        for fragment, why in self.fail.items():
            if fragment in url:
                raise why if isinstance(why, Exception) else mu.UpdateError(why)
        if "/commits/" in url:
            raise AssertionError("the REST API's commits endpoint must not be used: " + url)
        if "/info/refs" in url:
            return 200, {}, t.refs_body({self.mods_branch: self.mods_sha, self.branch: self.loader_sha})
        if "/contents/" in url:
            if self.listing is not None:
                return self.listing
            body = [{"name": name, "type": "file", "size": len(data), "sha": mu.blob_sha(data)} for name, data in sorted(self.files.items())]
            return 200, {}, json.dumps(body).encode()
        if "raw.githubusercontent.com" in url:
            name = url.rsplit("/", 1)[1]
            if name in self.served:
                return 200, {}, self.served[name]
            if name not in self.files:
                raise mu.UpdateError("the server answered 404")
            return 200, {}, self.files[name]
        return 200, {}, self.zip_bytes


def write_loader(root: Path, files, newline: str = "\n") -> None:
    folder = Path(root) / "modloader"
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        data = text.encode("utf-8") if isinstance(text, str) else text
        (folder / name).write_bytes(data.replace(b"\n", newline.encode()) if newline != "\n" else data)


def read_loader(root: Path) -> "dict[str, bytes]":
    folder = Path(root) / "modloader"
    return {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}


class Package(t.Base):
    """A stand-alone package: modloader/ with the launcher's files, mods_available/ with a catalogue, an empty mods/."""

    def setUp(self):
        super().setUp()
        write_loader(self.root, LOADER_V1)
        self.catalogue_has(t.MODS_V1)

    def github(self, **kwargs):
        kwargs.setdefault("loader_files", LOADER_V2)
        kwargs.setdefault("zip_bytes", t.make_zip(t.MODS_V1))
        return FakeGitHub(**kwargs)


# ── the launcher's folder ────────────────────────────────────────────────────

class ConfigTests(unittest.TestCase):
    def test_the_loader_branch_is_modloader_unless_named_otherwise(self):
        with mock.patch.dict(os.environ, clear=False):
            os.environ.pop(mu.ENV_LOADER_BRANCH, None)
            self.assertEqual(mu.loader_branch(), "modloader")
        with mock.patch.dict(os.environ, {mu.ENV_LOADER_BRANCH: " loader-test "}):
            self.assertEqual(mu.loader_branch(), "loader-test")

    def test_a_malformed_branch_is_ignored(self):
        for bad in ("a b", "x;y", "..?", "a\nb"):
            with mock.patch.dict(os.environ, {mu.ENV_LOADER_BRANCH: bad}):
                self.assertEqual(mu.loader_branch(), "modloader", bad)

    def test_the_blob_hash_is_what_git_gives_a_file(self):
        self.assertEqual(mu.blob_sha(b"hello\n"), "ce013625030ba8dba906f756967f9e9ca394464a")
        self.assertEqual(mu.blob_sha(b""), "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")


class LoaderAvailableTests(Package):
    def test_the_stand_alone_package_has_a_launcher_to_keep_up_to_date(self):
        self.assertTrue(mu.loader_available(self.root))

    def test_the_developer_folder_has_none(self):
        self.assertFalse(mu.loader_available(self.root.parent / "dev"))

    def test_a_folder_without_the_loader_or_the_launch_file_is_not_the_package(self):
        for name in mu.LOADER_MUST_HAVE:
            (self.root / "modloader" / name).rename(self.root / "modloader" / (name + ".x"))
            self.assertFalse(mu.loader_available(self.root), name)
            (self.root / "modloader" / (name + ".x")).rename(self.root / "modloader" / name)
        self.assertTrue(mu.loader_available(self.root))

    def test_a_package_that_is_a_git_clone_is_left_to_git(self):
        (self.root / ".git").mkdir()
        self.assertFalse(mu.loader_available(self.root))

    def test_it_can_be_switched_off_on_its_own(self):
        with mock.patch.dict(os.environ, {mu.ENV_NO_LOADER: "1"}):
            self.assertFalse(mu.loader_available(self.root))
        self.assertTrue(mu.available(self.root))                         # (the mods are not affected)


# ── the listing and the downloads ────────────────────────────────────────────

def listing_http(entries, status=200):
    body = entries if isinstance(entries, bytes) else json.dumps(entries).encode()
    calls = []

    def http(url, headers=None, timeout=12.0, max_bytes=0):
        calls.append((url, dict(headers or {}), max_bytes))
        return status, {}, body
    http.calls = calls
    return http


def entry(name="modloader.py", data=b"x = 1\n", **kw):
    return {"name": name, "type": "file", "size": len(data), "sha": mu.blob_sha(data), **kw}


MUST = [entry("modloader.py"), entry("start_modded_client.py")]


class ListLoaderFilesTests(unittest.TestCase):
    def test_it_lists_the_launcher_files_of_exactly_that_commit(self):
        http = listing_http([*MUST, entry("modctl.py", b"abc"), entry("modctl.bat", b"@echo off\n"), entry("MOD_AUTHORS_README.md", b"#")])
        files = mu.list_loader_files("o/r", SHA3, http)
        self.assertEqual(sorted(files), ["MOD_AUTHORS_README.md", "modctl.bat", "modctl.py", "modloader.py", "start_modded_client.py"])
        self.assertEqual(files["modctl.py"], (3, mu.blob_sha(b"abc")))
        (url, headers, _), = http.calls
        self.assertEqual(url, f"https://api.github.com/repos/o/r/contents/modloader?ref={SHA3}")
        self.assertEqual(headers["Accept"], "application/vnd.github+json")

    def test_only_plain_files_with_a_safe_name_and_a_known_extension_are_taken(self):
        http = listing_http([*MUST, {"name": "__pycache__", "type": "dir", "size": 0, "sha": "e" * 40},
                             entry("../evil.py"), entry("sub/x.py"), entry(".hidden.py"), entry("tool.exe"), entry("tool.dll"),
                             entry("sym.py", type="symlink"), entry("with space.py"), entry("x" * 80 + ".py"), entry("ok_2.PY")])
        self.assertEqual(sorted(mu.list_loader_files("o/r", SHA3, http)), ["modloader.py", "ok_2.PY", "start_modded_client.py"])

    def test_a_listing_without_the_loader_or_the_launch_file_is_refused(self):
        for lacking in ("modloader.py", "start_modded_client.py"):
            http = listing_http([e for e in MUST if e["name"] != lacking])
            with self.assertRaises(mu.UpdateError, msg=lacking):
                mu.list_loader_files("o/r", SHA3, http)

    def test_answers_that_are_not_a_file_list_are_refused(self):
        for body in (b"not json", b"\xff\xfe", b'{"message": "Not Found"}', b"null", b'"text"', b"[]"):
            with self.assertRaises(mu.UpdateError, msg=body):
                mu.list_loader_files("o/r", SHA3, listing_http(body))

    def test_a_status_that_is_not_200_is_refused(self):
        for status in (304, 404, 500):
            with self.assertRaises(mu.UpdateError):
                mu.list_loader_files("o/r", SHA3, listing_http(MUST, status=status))

    def test_too_many_files_are_refused(self):
        many = [*MUST, *[entry(f"f{i}.py") for i in range(mu.MAX_LOADER_FILES)]]
        with self.assertRaises(mu.UpdateError):
            mu.list_loader_files("o/r", SHA3, listing_http(many))

    def test_a_file_that_is_too_big_is_refused_and_so_is_a_launcher_that_is_too_big(self):
        big = [*MUST, {**entry("huge.py"), "size": mu.MAX_LOADER_FILE_BYTES + 1}]
        with self.assertRaises(mu.UpdateError):
            mu.list_loader_files("o/r", SHA3, listing_http(big))
        each = mu.MAX_LOADER_FILE_BYTES
        heavy = [{**entry(f"h{i}.py"), "size": each} for i in range(4)]
        with self.assertRaises(mu.UpdateError):
            mu.list_loader_files("o/r", SHA3, listing_http([*MUST, *heavy]))

    def test_a_file_exactly_at_the_size_limit_is_fine(self):
        files = mu.list_loader_files("o/r", SHA3, listing_http([*MUST, {**entry("edge.py"), "size": mu.MAX_LOADER_FILE_BYTES}]))
        self.assertEqual(files["edge.py"][0], mu.MAX_LOADER_FILE_BYTES)

    def test_malformed_entries_are_refused(self):
        for bad in ({**entry("a.py"), "size": "12"}, {**entry("a.py"), "size": -1}, {**entry("a.py"), "size": True},
                    {**entry("a.py"), "size": 1.5}, {**entry("a.py"), "sha": "xyz"}, {**entry("a.py"), "sha": 7},
                    {**entry("a.py"), "sha": "A" * 40}):
            with self.assertRaises(mu.UpdateError, msg=bad):
                mu.list_loader_files("o/r", SHA3, listing_http([*MUST, bad]))

    def test_entries_that_are_not_objects_or_not_files_are_skipped(self):
        files = mu.list_loader_files("o/r", SHA3, listing_http([*MUST, "text", 5, None, {"type": "file"}, {"name": 4, "type": "file"}]))
        self.assertEqual(sorted(files), ["modloader.py", "start_modded_client.py"])

    def test_the_commit_must_be_a_commit(self):
        for bad in ("main", "", "../x", "A" * 40, "a" * 39):
            with self.assertRaises(mu.UpdateError):
                mu.list_loader_files("o/r", bad, listing_http(MUST))


class DownloadLoaderFileTests(unittest.TestCase):
    DATA = b"VERSION = 2\n"

    def get(self, body=None, status=200, name="modloader.py", size=None, blob=None, sha=SHA3):
        calls = []

        def http(url, headers=None, timeout=12.0, max_bytes=0):
            calls.append((url, max_bytes))
            return status, {}, self.DATA if body is None else body
        result = mu.download_loader_file("o/r", sha, name, len(self.DATA) if size is None else size,
                                         mu.blob_sha(self.DATA) if blob is None else blob, http)
        return result, calls

    def test_the_file_of_exactly_that_commit_is_downloaded_and_checked(self):
        data, calls = self.get()
        self.assertEqual(data, self.DATA)
        (url, limit), = calls
        self.assertEqual(url, f"https://raw.githubusercontent.com/o/r/{SHA3}/modloader/modloader.py")
        self.assertEqual(limit, mu.MAX_LOADER_FILE_BYTES)

    def test_a_file_that_is_not_what_the_listing_said_is_refused(self):
        for kwargs in ({"body": b"VERSION = 3\n"}, {"body": self.DATA + b"#"}, {"size": 3}, {"blob": "0" * 40}, {"body": b""}):
            with self.assertRaises(mu.UpdateError, msg=kwargs):
                self.get(**kwargs)

    def test_a_status_that_is_not_200_is_refused(self):
        with self.assertRaises(mu.UpdateError):
            self.get(status=404)

    def test_names_and_commits_that_could_leave_the_folder_are_refused(self):
        for kwargs in ({"name": "../x.py"}, {"name": "a/b.py"}, {"name": ".x.py"}, {"name": ""}, {"sha": "main"}, {"sha": "../" + "a" * 37}):
            with self.assertRaises(mu.UpdateError, msg=kwargs):
                self.get(**kwargs)


# ── comparing with what is installed ─────────────────────────────────────────

class CheckLoaderTests(Package):
    def check(self, gh, state=None, now=NOW):
        state = {} if state is None else state
        return mu.check_loader(self.root, state, mu.Tips(mu.config()[0], gh), gh, lambda: now), state

    def test_launcher_files_that_are_the_same_have_nothing_to_offer_and_the_version_is_remembered(self):
        gh = self.github(loader_files=LOADER_V1)
        update, state = self.check(gh)
        self.assertIsNone(update)
        self.assertEqual(state, {"loader_sha": SHA3})
        self.assertEqual(gh.urls("raw.githubusercontent.com"), [])                   # (nothing was downloaded: the hashes matched)

    def test_files_that_differ_are_offered_and_only_they_are_downloaded(self):
        gh = self.github()
        update, state = self.check(gh)
        self.assertEqual((update.loader_updated, update.loader_added), (CHANGED_V2, ["MOD_AUTHORS_README.md"]))
        self.assertEqual(sorted(update.loader_files), sorted([*CHANGED_V2, "MOD_AUTHORS_README.md"]))
        self.assertEqual(update.loader_files["modloader.py"], b"VERSION = 2\n")
        self.assertEqual((update.loader_sha, update.repo, update.backend), (SHA3, "mkden1/star-empire-modloader", ""))
        self.assertEqual(len(gh.urls("raw.githubusercontent.com")), 4)               # (modpicker.py and modctl.bat are the same: not fetched)
        self.assertTrue(update.has_loader)
        self.assertFalse(update.has_mods)

    def test_an_offer_records_nothing(self):
        _update, state = self.check(self.github())
        self.assertEqual(state, {})

    def test_a_launcher_checked_out_with_windows_line_endings_is_not_out_of_date_for_that(self):
        shutil.rmtree(self.root / "modloader")
        write_loader(self.root, LOADER_V1, newline="\r\n")
        self.assertIn(b"\r\n", (self.root / "modloader" / "modloader.py").read_bytes())
        gh = self.github(loader_files=LOADER_V1)
        update, state = self.check(gh)
        self.assertIsNone(update)
        self.assertEqual(gh.urls("raw.githubusercontent.com"), [])
        self.assertEqual(state["loader_sha"], SHA3)

    def test_files_stored_with_windows_line_endings_upstream_are_not_an_update_either(self):
        gh = self.github(loader_files={name: text.replace("\n", "\r\n") for name, text in LOADER_V1.items()})
        update, state = self.check(gh)
        self.assertIsNone(update)                                                     # (downloaded, then compared ignoring line endings)
        self.assertEqual(len(gh.urls("raw.githubusercontent.com")), len(LOADER_V1))
        self.assertEqual(state["loader_sha"], SHA3)

    def test_binary_looking_content_is_compared_exactly(self):
        write_loader(self.root, {"modpicker.py": b"A\r\n\0B"})
        gh = self.github(loader_files={**LOADER_V1, "modpicker.py": b"A\n\0B"})
        update, _ = self.check(gh)
        self.assertEqual(update.loader_updated, ["modpicker.py"])

    def test_a_file_that_is_missing_here_is_added_and_one_that_only_exists_here_is_left_alone(self):
        (self.root / "modloader" / "modpicker.py").unlink()
        write_loader(self.root, {"my_notes.py": "mine = 1\n"})
        update, _ = self.check(self.github(loader_files=LOADER_V1))
        self.assertEqual((update.loader_updated, update.loader_added), ([], ["modpicker.py"]))

    def test_a_file_that_is_there_but_cannot_be_read_is_not_known_to_be_the_same(self):
        real = Path.read_bytes

        def read_bytes(path):
            if path.name == "modpicker.py":
                raise PermissionError("locked")
            return real(path)

        gh = self.github(loader_files=LOADER_V1)
        with mock.patch.object(Path, "read_bytes", read_bytes):
            update, _ = self.check(gh)
        self.assertEqual((update.loader_updated, update.loader_added), (["modpicker.py"], []))

    def test_a_launcher_branch_that_has_not_moved_is_not_listed_or_downloaded(self):
        gh = self.github()
        update, state = self.check(gh, {"loader_sha": SHA3})
        self.assertIsNone(update)
        self.assertEqual(len(gh.calls), 1)                                              # (just the branch list: no REST call at all)
        self.assertIn("/info/refs", gh.calls[0][0])
        self.assertEqual(gh.urls("api.github.com"), [])

    def test_a_branch_that_has_moved_is_listed_with_the_one_rest_call(self):
        gh = self.github()
        self.check(gh, {"loader_sha": SHA4})
        self.assertEqual([url.split("?")[0].rsplit("/", 1)[-1] for url in gh.urls("api.github.com")], ["modloader"])
        self.assertTrue(gh.urls("api.github.com")[0].endswith(f"/contents/modloader?ref={SHA3}"))

    def test_a_repository_without_the_launcher_branch_is_an_error(self):
        gh = self.github(branch="other-branch")
        with self.assertRaises(mu.UpdateError) as caught:
            self.check(gh)
        self.assertIn(mu.DEFAULT_LOADER_BRANCH, str(caught.exception))

    # GitHub's request limit (the contents listing is the one REST call): the launcher is left alone until it is lifted

    def limited(self, reset):
        gh = self.github()
        gh.fail["/contents/"] = mu.RateLimited("used up", reset)
        return gh

    def test_a_used_up_limit_is_remembered_until_it_is_lifted_and_the_error_still_travels(self):
        gh = self.limited(NOW + 900)
        state = {}
        with self.assertRaises(mu.RateLimited):
            self.check(gh, state)
        self.assertEqual(state, {"loader_retry_after": NOW + 900})

    def test_the_pause_is_at_least_a_minute_and_at_most_two_hours(self):
        for reset, wanted in ((NOW - 500, NOW + mu.MIN_BACKOFF_S), (NOW + 5, NOW + mu.MIN_BACKOFF_S),
                              (NOW + 10 * 3600, NOW + mu.MAX_BACKOFF_S), (NOW + 3 * 3600, NOW + mu.MAX_BACKOFF_S)):
            state = {}
            with self.assertRaises(mu.RateLimited):
                self.check(self.limited(reset), state)
            self.assertEqual(state["loader_retry_after"], wanted, reset - NOW)

    def test_a_limit_hit_while_downloading_a_file_is_remembered_too(self):
        gh = self.github()
        gh.fail["raw.githubusercontent.com"] = mu.RateLimited("used up", NOW + 300)
        state = {}
        with self.assertRaises(mu.RateLimited):
            self.check(gh, state)
        self.assertEqual(state, {"loader_retry_after": NOW + 300})

    def test_while_paused_nothing_is_asked_at_all(self):
        gh = self.github()
        update, state = self.check(gh, {"loader_retry_after": NOW + 600})
        self.assertIsNone(update)
        self.assertEqual(gh.calls, [])
        self.assertEqual(state, {"loader_retry_after": NOW + 600})

    def test_the_pause_ends_at_the_time_it_names(self):
        gh = self.github()
        update, state = self.check(gh, {"loader_retry_after": NOW + 600}, now=NOW + 600)
        self.assertIsNotNone(update)
        self.assertNotIn("loader_retry_after", state)                                    # (a successful listing clears it)

    def test_an_up_to_date_launcher_clears_a_pause_that_has_ended(self):
        update, state = self.check(self.github(), {"loader_retry_after": NOW - 1, "loader_sha": SHA3})
        self.assertIsNone(update)
        self.assertEqual(state, {"loader_sha": SHA3})

    def test_a_launcher_that_is_the_same_after_a_pause_is_recorded_and_clears_it(self):
        update, state = self.check(self.github(loader_files=LOADER_V1), {"loader_retry_after": NOW - 1})
        self.assertIsNone(update)
        self.assertEqual(state, {"loader_sha": SHA3})

    def test_other_failures_do_not_start_a_pause(self):
        gh = self.github()
        gh.fail["/contents/"] = "boom"
        state = {}
        with self.assertRaises(mu.UpdateError):
            self.check(gh, state)
        self.assertEqual(state, {})

    def test_a_file_that_does_not_match_its_listing_is_an_error_and_records_nothing(self):
        gh = self.github()
        gh.served["modloader.py"] = b"VERSION = 666\n"
        state = {}
        with self.assertRaises(mu.UpdateError):
            self.check(gh, state)
        self.assertEqual(state, {})

    def test_a_download_that_fails_is_an_error(self):
        gh = self.github()
        gh.fail["raw.githubusercontent.com"] = "no connection"
        with self.assertRaises(mu.UpdateError):
            self.check(gh)

    def test_another_branch_and_another_repository_are_followed_when_named(self):
        gh = self.github(branch="loader-test", mods_branch="mods-test")
        with mock.patch.dict(os.environ, {mu.ENV_LOADER_BRANCH: "loader-test", mu.ENV_REPO: "someone/fork@mods-test"}):
            update, _ = self.check(gh)
        self.assertIsNotNone(update)
        self.assertEqual(gh.calls[0][0], "https://github.com/someone/fork.git/info/refs?service=git-upload-pack")
        self.assertEqual(update.loader_sha, SHA3)                                        # (the loader branch's commit, not the mods branch's)
        self.assertTrue(all("someone/fork" in url for url, _ in gh.calls))
        self.assertEqual(update.repo, "someone/fork")


# ── swapping the files in ────────────────────────────────────────────────────

class ApplyLoaderTests(Package):
    def files(self, **extra):
        return {**raw({n: LOADER_V2[n] for n in CHANGED_V2}), **extra}

    def test_the_files_are_swapped_in_and_the_old_ones_kept(self):
        mu.apply_loader(self.root / "modloader", self.files(**{"MOD_AUTHORS_README.md": b"# new\n"}))
        now = read_loader(self.root)
        self.assertEqual(now["modloader.py"], b"VERSION = 2\n")
        self.assertEqual(now["MOD_AUTHORS_README.md"], b"# new\n")
        self.assertEqual(now["modpicker.py"], b"PICKER = 1\n")                          # (untouched)
        old = {p.name: p.read_bytes() for p in (self.root / "modloader" / ".update_old").iterdir()}
        self.assertEqual(old, {n: LOADER_V1[n].encode() for n in CHANGED_V2})              # (only what existed is kept; the new file had no old copy)
        self.assertFalse((self.root / "modloader" / ".update_new").exists())

    def test_only_one_generation_of_the_old_files_is_kept(self):
        mu.apply_loader(self.root / "modloader", self.files())
        mu.apply_loader(self.root / "modloader", {"modloader.py": b"VERSION = 3\n"})
        old = {p.name: p.read_bytes() for p in (self.root / "modloader" / ".update_old").iterdir()}
        self.assertEqual(old, {"modloader.py": b"VERSION = 2\n"})

    def test_a_python_file_that_does_not_compile_stops_everything_before_anything_is_touched(self):
        before = read_loader(self.root)
        for bad in (b"def broken(:\n", b"x = (\n", b"\xff\xfe\x00bad"):
            with self.assertRaises(mu.UpdateError, msg=bad):
                mu.apply_loader(self.root / "modloader", self.files(**{"modpicker.py": bad}))
            self.assertEqual(read_loader(self.root), before)
        self.assertFalse((self.root / "modloader" / ".update_new").exists())
        self.assertFalse((self.root / "modloader" / ".update_old").exists())

    def test_a_python_file_with_an_upper_case_extension_is_checked_too(self):
        with self.assertRaises(mu.UpdateError):
            mu.apply_loader(self.root / "modloader", {"SHOUT.PY": b"def broken(:\n"})

    def test_files_that_are_not_python_are_not_compiled(self):
        mu.apply_loader(self.root / "modloader", {"modctl.bat": b"@echo off\r\n:: ( not python\r\n", "NOTES.md": b"def broken(:\n"})
        self.assertEqual(read_loader(self.root)["NOTES.md"], b"def broken(:\n")

    def test_names_that_could_leave_the_folder_are_refused_before_anything_is_written(self):
        before = read_loader(self.root)
        for name in ("../escape.py", "sub/x.py", "..", ".hidden.py", "x.exe", "", "a\\b.py"):
            with self.assertRaises(mu.UpdateError, msg=name):
                mu.apply_loader(self.root / "modloader", self.files(**{name: b"x = 1\n"}))
        self.assertEqual(read_loader(self.root), before)
        self.assertFalse((self.root.parent / "escape.py").exists())

    def test_a_swap_that_fails_part_way_puts_every_file_back_and_removes_the_new_ones(self):
        before = read_loader(self.root)
        real_replace = os.replace
        calls = {"n": 0}

        def replace_file(src, dst):
            if Path(dst).parent.name == "modloader":
                calls["n"] += 1
                if calls["n"] == 3:
                    raise PermissionError("in use")
            return real_replace(src, dst)

        files = self.files(**{"MOD_AUTHORS_README.md": b"# new\n", "zz_new.py": b"z = 1\n"})
        with mock.patch.object(mu.os, "replace", replace_file), mock.patch.object(mu, "logger"):
            with self.assertRaises(PermissionError):
                mu.apply_loader(self.root / "modloader", files)
        self.assertEqual({k: v for k, v in read_loader(self.root).items()}, before)
        self.assertFalse((self.root / "modloader" / ".update_new").exists())

    def test_a_new_file_that_was_swapped_before_a_failure_is_removed_again(self):
        before = read_loader(self.root)
        real_replace = os.replace
        files = {"aa_new.py": b"a = 1\n", "modloader.py": b"VERSION = 2\n"}

        def replace_file(src, dst):
            if Path(dst).name == "modloader.py":
                raise PermissionError("in use")
            return real_replace(src, dst)

        with mock.patch.object(mu.os, "replace", replace_file), mock.patch.object(mu, "logger"):
            with self.assertRaises(PermissionError):
                mu.apply_loader(self.root / "modloader", files)
        self.assertEqual(read_loader(self.root), before)

    def test_a_file_that_cannot_be_put_back_does_not_hide_the_first_error_or_stop_the_others(self):
        real_replace = os.replace
        calls = {"n": 0}

        def replace_file(src, dst):
            if Path(dst).parent.name == "modloader":
                calls["n"] += 1
                if calls["n"] == 3:
                    raise PermissionError("in use")
                if Path(dst).name == "modloader.py" and Path(src).parent.name == ".update_old":
                    raise PermissionError("locked")
            return real_replace(src, dst)

        logger = mock.MagicMock()
        with mock.patch.object(mu.os, "replace", replace_file), mock.patch.object(mu, "logger", logger):
            with self.assertRaisesRegex(PermissionError, "in use"):
                mu.apply_loader(self.root / "modloader", self.files())
        logger.exception.assert_called_once()
        now = read_loader(self.root)
        self.assertEqual(now["modctl.py"], LOADER_V1["modctl.py"].encode())                # (put back although modloader.py could not be)

    def test_it_is_not_confused_by_files_it_left_from_a_failed_earlier_try(self):
        (self.root / "modloader" / ".update_new").mkdir()
        (self.root / "modloader" / ".update_new" / "junk.py").write_bytes(b"junk")
        (self.root / "modloader" / ".update_old").mkdir()
        (self.root / "modloader" / ".update_old" / "junk.py").write_bytes(b"junk")
        mu.apply_loader(self.root / "modloader", self.files())
        self.assertEqual(sorted(p.name for p in (self.root / "modloader" / ".update_old").iterdir()), sorted(CHANGED_V2))
        self.assertFalse((self.root / "modloader" / ".update_new").exists())


# ── will the new versions reach mods/? ───────────────────────────────────────

class RefreshTests(Package):
    def set_launch_file(self, text):
        (self.root / "modloader" / "start_modded_client.py").write_text(text, encoding="utf-8")

    def test_a_launcher_that_refreshes_at_launch_does_not_need_help(self):
        self.set_launch_file("import modctl\nmodctl.refresh_at_launch(root)\n")
        self.assertTrue(mu.launcher_refreshes(self.root))

    def test_an_older_launcher_does(self):
        self.assertFalse(mu.launcher_refreshes(self.root))

    def test_only_the_launch_file_decides(self):
        self.set_launch_file("print('no refresh here')")
        (self.root / "modloader" / "modctl.py").write_text("def refresh_at_launch(root): pass\n", encoding="utf-8")      # (defined, but never called)
        self.assertFalse(mu.launcher_refreshes(self.root))

    def test_without_a_launch_file_the_answer_is_yes_so_nothing_is_second_guessed(self):
        (self.root / "modloader" / "start_modded_client.py").unlink()
        self.assertTrue(mu.launcher_refreshes(self.root))


class SyncInstalledTests(Package):
    def test_it_asks_the_launchers_modctl_to_update_exactly_those_mods(self):
        calls = []
        fake = SimpleNamespace(update=lambda **kw: calls.append(kw) or (["alpha"], [], []))
        self.assertEqual(mu.sync_installed(self.root, ["alpha", "beta"], modctl=fake), ["alpha"])
        self.assertEqual(calls, [{"names": ("alpha", "beta"), "available_dir": str(self.root / "mods_available"),
                                  "installed_dir": str(self.root / "mods")}])

    def test_the_launchers_modctl_is_loaded_from_its_file_when_not_imported(self):
        (self.root / "modloader" / "modctl.py").write_text("def update(names, available_dir, installed_dir):\n    return ['x'], [], []\n", encoding="utf-8")
        with mock.patch.dict(sys.modules):
            sys.modules.pop("modctl", None)
            self.assertEqual(mu.sync_installed(self.root, ["x"]), ["x"])

    def test_an_already_imported_modctl_is_used(self):
        fake = SimpleNamespace(update=lambda **kw: (["done"], [], []))
        with mock.patch.dict(sys.modules, {"modctl": fake}):
            self.assertEqual(mu.sync_installed(self.root, ["done"]), ["done"])

    def test_a_modctl_that_cannot_update_is_not_used(self):
        (self.root / "modloader" / "modctl.py").write_text("def update(names, available_dir, installed_dir):\n    return ['file'], [], []\n", encoding="utf-8")
        with mock.patch.dict(sys.modules, {"modctl": SimpleNamespace()}):
            self.assertEqual(mu.sync_installed(self.root, ["file"]), ["file"])

    def test_a_modctl_that_cannot_be_loaded_is_an_error(self):
        with mock.patch.dict(sys.modules), mock.patch.object(mu.importlib.util, "spec_from_file_location", return_value=None):
            sys.modules.pop("modctl", None)
            with self.assertRaises(mu.UpdateError):
                mu.sync_installed(self.root, ["x"])

    def test_a_missing_modctl_is_an_error_not_a_crash(self):
        (self.root / "modloader" / "modctl.py").unlink()
        with mock.patch.dict(sys.modules):
            sys.modules.pop("modctl", None)
            with self.assertRaises(Exception):
                mu.sync_installed(self.root, ["x"])

    REAL = Path(__file__).resolve().parents[2] / "custom_build" / "package" / "modctl.py"

    @unittest.skipUnless(REAL.is_file(), "the package's modctl.py is not beside the mods")
    def test_with_the_real_modctl_the_installed_copy_really_changes(self):
        shutil.copy(self.REAL, self.root / "modloader" / "modctl.py")
        self.catalogue_has({"alpha": {"mod.json": "{}", "alpha.py": "x = 2\n"}})
        t.write_mods(self.root / "mods", {"alpha": {"mod.json": "{}", "alpha.py": "x = 1\n"}})
        with mock.patch.dict(sys.modules), mock.patch.object(sys, "path", list(sys.path)):       # (modctl.py puts its own folder first on sys.path)
            sys.modules.pop("modctl", None)
            self.assertEqual(mu.sync_installed(self.root, ["alpha"]), ["alpha"])
        self.assertEqual((self.root / "mods" / "alpha" / "alpha.py").read_text(encoding="utf-8"), "x = 2\n")


# ── the check, both halves together ──────────────────────────────────────────

class CombinedCheckTests(Package):
    def check(self, gh, **kwargs):
        return mu.check(self.root, now=lambda: NOW, http=gh, **kwargs)

    def test_a_package_that_is_up_to_date_has_nothing_to_offer_and_everything_is_remembered(self):
        gh = self.github(loader_files=LOADER_V1)
        self.assertIsNone(self.check(gh))
        self.assertEqual(mu.read_state(self.root), {"sha": SHA1, "loader_sha": SHA3, "checked_at": NOW})

    def test_an_up_to_date_package_costs_one_small_request_and_no_rest_call(self):
        mu.write_state(self.root, {"sha": SHA1, "loader_sha": SHA3, "checked_at": 0})
        gh = self.github()
        self.assertIsNone(self.check(gh))
        self.assertEqual([url for url, _ in gh.calls], ["https://github.com/mkden1/star-empire-modloader.git/info/refs?service=git-upload-pack"])

    def test_both_halves_share_one_branch_list(self):
        gh = self.github(mods_sha=SHA2, zip_bytes=t.make_zip(t.MODS_V2, SHA2))
        self.check(gh)
        self.assertEqual(len(gh.urls("/info/refs")), 1)

    def test_a_new_launcher_alone_is_offered_as_one_update(self):
        gh = self.github()
        update = self.check(gh)
        self.assertEqual((update.backend, update.updated, update.added, update.blocked), ("", [], [], None))
        self.assertEqual(update.loader_updated, CHANGED_V2)
        self.assertTrue(update.has_loader and not update.has_mods)
        state = mu.read_state(self.root)
        self.assertEqual(state["sha"], SHA1)                                           # (what concluded is kept)
        self.assertNotIn("checked_at", state)                                          # (an offer is made again at the next start)
        self.assertNotIn("loader_sha", state)

    def test_new_mods_alone_are_offered_as_before(self):
        gh = self.github(mods_sha=SHA2, loader_files=LOADER_V1, zip_bytes=t.make_zip(t.MODS_V2, SHA2))
        update = self.check(gh)
        self.assertEqual((update.backend, update.updated, update.added), ("zip", ["alpha"], ["gamma"]))
        self.assertFalse(update.has_loader)
        self.assertEqual(mu.read_state(self.root)["loader_sha"], SHA3)

    def test_both_halves_are_offered_together(self):
        gh = self.github(mods_sha=SHA2, zip_bytes=t.make_zip(t.MODS_V2, SHA2))
        update = self.check(gh)
        self.assertEqual((update.updated, update.added, update.loader_updated), (["alpha"], ["gamma"], CHANGED_V2))
        self.assertEqual((update.sha, update.loader_sha), (SHA2, SHA3))
        self.assertEqual(update.repo, "mkden1/star-empire-modloader")

    def test_a_launcher_that_cannot_be_checked_does_not_hide_the_mods(self):
        gh = self.github(mods_sha=SHA2, zip_bytes=t.make_zip(t.MODS_V2, SHA2))
        gh.fail["/contents/"] = "boom"
        with self.assertLogs("mod_updates", level="INFO"):
            update = self.check(gh)
        self.assertEqual(update.updated, ["alpha"])
        self.assertFalse(update.has_loader)

    def test_a_launcher_that_cannot_be_checked_is_tried_again_at_the_next_start(self):
        gh = self.github(loader_files=LOADER_V1)
        gh.fail["/contents/"] = "boom"
        gh.loader_sha = SHA4                                                            # (a new commit: it has to be listed)
        with self.assertLogs("mod_updates", level="INFO"):
            self.assertIsNone(self.check(gh))
        state = mu.read_state(self.root)
        self.assertNotIn("checked_at", state)                                           # (so the 20 minutes do not apply)
        self.assertEqual(state["sha"], SHA1)

    def test_mods_that_cannot_be_checked_do_not_hide_the_launcher(self):
        gh = self.github()
        gh.fail["codeload.github.com"] = "boom"
        gh.mods_sha = SHA2
        update = self.check(gh)
        self.assertEqual(update.loader_updated, CHANGED_V2)
        self.assertFalse(update.has_mods)

    def test_when_nothing_can_be_checked_it_is_an_error_and_nothing_is_recorded(self):
        gh = self.github()
        gh.fail["/info/refs"] = "no connection"
        with self.assertRaises(mu.UpdateError):
            self.check(gh)
        self.assertEqual(mu.read_state(self.root), {})

    # GitHub's request limit: only the file listing of the launcher uses the REST API, so only the launcher is paused

    def limited(self, **kwargs):
        gh = self.github(**kwargs)
        gh.fail["/contents/"] = mu.RateLimited("used up", NOW + 900)
        return gh

    def test_a_used_up_limit_pauses_the_launcher_and_still_offers_the_mods(self):
        gh = self.limited(mods_sha=SHA2, zip_bytes=t.make_zip(t.MODS_V2, SHA2))
        update = self.check(gh)
        self.assertEqual(update.updated, ["alpha"])
        self.assertFalse(update.has_loader)
        self.assertEqual(mu.read_state(self.root)["loader_retry_after"], NOW + 900)            # (saved although an offer records nothing else)

    def test_a_pause_with_nothing_else_to_say_is_an_error_that_still_saves_the_pause(self):
        shutil.rmtree(self.catalogue)
        with self.assertRaises(mu.RateLimited):
            self.check(self.limited())
        self.assertEqual(mu.read_state(self.root), {"loader_retry_after": NOW + 900})

    def test_the_next_start_during_the_pause_asks_only_the_branch_list_and_is_then_done_for_a_while(self):
        self.check(self.limited(loader_files=LOADER_V2))
        gh = self.github()
        self.assertIsNone(self.check(gh))
        self.assertEqual(len(gh.calls), 1)                                                      # (the branch list; no listing, no downloads)
        state = mu.read_state(self.root)
        self.assertEqual(state["checked_at"], NOW)                                              # (nothing is left to retry: the wait applies)
        self.assertEqual(state["loader_retry_after"], NOW + 900)

    def test_after_the_pause_the_launcher_is_checked_again_and_the_pause_forgotten(self):
        self.check(self.limited(loader_files=LOADER_V2))
        update = mu.check(self.root, now=lambda: NOW + 1000, http=self.github(), force=True)
        self.assertEqual(update.loader_updated, CHANGED_V2)
        self.assertNotIn("loader_retry_after", mu.read_state(self.root))

    def test_the_wait_applies_to_both_halves(self):
        mu.write_state(self.root, {"checked_at": NOW - 60})
        gh = self.github()
        self.assertIsNone(self.check(gh))
        self.assertEqual(gh.calls, [])
        self.assertIsNotNone(self.check(gh, force=True))

    def test_without_a_launcher_folder_only_the_mods_are_asked_about(self):
        shutil.rmtree(self.root / "modloader")
        gh = self.github(loader_files=LOADER_V2)
        self.check(gh)
        self.assertEqual(gh.urls("/contents/"), [])
        self.assertNotIn("loader_sha", mu.read_state(self.root))

    def test_without_a_catalogue_only_the_launcher_is_asked_about(self):
        shutil.rmtree(self.catalogue)
        gh = self.github()
        update = self.check(gh)
        self.assertEqual(update.loader_updated, CHANGED_V2)
        self.assertEqual(gh.urls("codeload"), [])
        self.assertNotIn("sha", mu.read_state(self.root))

    def test_with_the_launcher_switched_off_only_the_mods_are_asked_about(self):
        gh = self.github(loader_files=LOADER_V2)
        with mock.patch.dict(os.environ, {mu.ENV_NO_LOADER: "1"}):
            self.check(gh)
        self.assertEqual(gh.urls("/contents/"), [])
        self.assertNotIn("loader_sha", mu.read_state(self.root))

    def test_a_catalogue_that_is_a_git_clone_is_checked_with_git_and_the_launcher_with_https(self):
        (self.catalogue / ".git").mkdir()
        blocked = mu.Update(backend="git", sha=SHA2, updated=["alpha"], blocked="mods_available has local changes")
        gh = self.github()
        with mock.patch.object(mu, "git_check", return_value=blocked) as git_check:
            update = self.check(gh, git="git")
        git_check.assert_called_once()
        self.assertEqual(gh.urls("codeload"), [])                                        # (git did the mods)
        self.assertEqual((update.backend, update.blocked, update.updated, update.loader_updated), ("git", "mods_available has local changes",
                                                                                                    ["alpha"], CHANGED_V2))

    def test_a_clone_with_nothing_new_and_a_launcher_that_is_current_has_nothing_to_offer(self):
        (self.catalogue / ".git").mkdir()
        gh = self.github(loader_files=LOADER_V1)
        with mock.patch.object(mu, "git_check", return_value=None):
            self.assertIsNone(self.check(gh, git="git"))
        self.assertEqual(mu.read_state(self.root)["checked_at"], NOW)


# ── applying, both halves together ───────────────────────────────────────────

class CombinedApplyTests(Package):
    def setUp(self):
        super().setUp()
        self.synced: list = []
        self.sync = lambda root, names: self.synced.append(list(names)) or list(names)
        gh = self.github(mods_sha=SHA2, zip_bytes=t.make_zip(t.MODS_V2, SHA2))
        self.both = mu.check(self.root, now=lambda: NOW, http=gh)
        self.launcher_only = replace(self.both, backend="", sha="", updated=[], added=[], mods={})
        self.mods_only = replace(self.both, loader_files={}, loader_updated=[], loader_added=[], loader_sha="")

    def apply(self, update, **kwargs):
        return mu.apply(self.root, update, now=lambda: 5.0, sync=self.sync, **kwargs)

    def test_a_launcher_update_swaps_the_files_records_the_version_and_restarts(self):
        ok, message, restart = self.apply(self.launcher_only)
        self.assertEqual((ok, restart), (True, True))
        self.assertIn("Restarting", message)
        self.assertEqual(read_loader(self.root)["modloader.py"], b"VERSION = 2\n")
        state = mu.read_state(self.root)
        self.assertEqual((state["loader_sha"], state["checked_at"]), (SHA3, 5.0))
        self.assertNotIn("sha", state)                                                   # (the mods were not part of it)
        self.assertEqual(self.synced, [])

    def test_both_halves_are_applied_the_launcher_first(self):
        order = []
        real_loader, real_zip = mu.apply_loader, mu.apply_zip
        with mock.patch.object(mu, "apply_loader", lambda *a, **k: order.append("launcher") or real_loader(*a, **k)), \
                mock.patch.object(mu, "apply_zip", lambda *a, **k: order.append("mods") or real_zip(*a, **k)):
            ok, _message, restart = self.apply(self.both)
        self.assertEqual((ok, restart, order), (True, True, ["launcher", "mods"]))
        self.assertEqual((self.catalogue / "alpha" / "alpha.py").read_text(encoding="utf-8"), "x = 2\n")
        self.assertEqual(read_loader(self.root)["modloader.py"], b"VERSION = 2\n")
        state = mu.read_state(self.root)
        self.assertEqual((state["sha"], state["loader_sha"], state["checked_at"]), (SHA2, SHA3, 5.0))

    def test_a_launcher_that_cannot_be_updated_changes_nothing_and_the_mods_wait(self):
        before = read_loader(self.root)
        files = {**self.both.loader_files, "modctl.py": b"def broken(:\n"}
        update = replace(self.both, loader_files=files)
        with self.assertLogs("mod_updates", level="WARNING"):
            ok, message, restart = self.apply(update)
        self.assertEqual((ok, restart), (False, False))
        self.assertIn("Could not update the launcher", message)
        self.assertIn("modctl.py does not compile", message)
        self.assertIn("Nothing was changed", message)
        self.assertEqual(read_loader(self.root), before)
        self.assertEqual((self.catalogue / "alpha" / "alpha.py").read_text(encoding="utf-8"), "x = 1\n")
        self.assertEqual(mu.read_state(self.root), {})

    def test_mods_that_fail_after_the_launcher_was_updated_say_so_and_keep_the_launcher_version(self):
        self.install("alpha")
        files = {**self.both.loader_files, "start_modded_client.py": b"print('no refresh')\n"}
        with mock.patch.object(mu, "apply_zip", side_effect=OSError("disk full")), self.assertLogs("mod_updates", level="WARNING"):
            ok, message, restart = self.apply(replace(self.both, loader_files=files))
        self.assertEqual((ok, restart, self.synced), (False, False, []))                # (nothing is copied into mods/ for mods that were not updated)
        self.assertTrue(message.startswith("Updated the launcher, but could not update the mods: disk full"), message)
        self.assertIn("Start the game again", message)
        state = mu.read_state(self.root)
        self.assertEqual(state["loader_sha"], SHA3)
        self.assertNotIn("sha", state)                                                   # (the mods are offered again)
        self.assertNotIn("checked_at", state)

    def test_mods_that_cannot_be_applied_here_do_not_stop_the_launcher(self):
        self.install("alpha")
        files = {**self.both.loader_files, "start_modded_client.py": b"print('no refresh')\n"}              # (so copying into mods/ would be the fallback)
        update = replace(self.both, backend="git", blocked="mods_available has local changes", loader_files=files)
        ok, message, restart = self.apply(update)
        self.assertEqual((ok, restart, self.synced), (True, True, []))                  # (nothing is copied: the mods were not updated)
        self.assertEqual(read_loader(self.root)["modloader.py"], b"VERSION = 2\n")
        self.assertEqual((self.catalogue / "alpha" / "alpha.py").read_text(encoding="utf-8"), "x = 1\n")      # (the mods were not touched)
        self.assertNotIn("sha", mu.read_state(self.root))

    def test_a_blocked_update_with_nothing_for_the_launcher_says_why_and_changes_nothing(self):
        blocked = mu.Update(backend="git", sha=SHA2, updated=["alpha"], blocked="mods_available has local changes")
        self.assertEqual(self.apply(blocked), (False, "mods_available has local changes", False))

    def test_an_update_with_nothing_in_it_is_not_pretended(self):
        self.assertEqual(self.apply(mu.Update(backend="", sha="")), (False, "Nothing to update.", False))

    # the older launcher that never copies the catalogue's new versions into mods/

    def test_with_an_older_launcher_the_updated_mods_that_are_used_are_copied_into_mods_here(self):
        self.install("alpha", "beta")
        ok, message, restart = self.apply(self.mods_only)
        self.assertEqual((ok, restart), (True, True))
        self.assertEqual(self.synced, [["alpha"]])                                       # (alpha was updated and is used; beta was not updated)
        self.assertIn("Restarting", message)

    def test_a_launcher_that_refreshes_by_itself_is_left_to_do_so(self):
        (self.root / "modloader" / "start_modded_client.py").write_text("modctl.refresh_at_launch(root)", encoding="utf-8")
        self.install("alpha")
        ok, _message, restart = self.apply(self.mods_only)
        self.assertEqual((ok, restart, self.synced), (True, True, []))

    def test_a_launcher_that_was_just_updated_is_judged_by_its_new_launch_file(self):
        self.install("alpha")
        ok, _message, restart = self.apply(self.both)                                    # (LOADER_V2's launch file calls refresh_at_launch)
        self.assertEqual((ok, restart, self.synced), (True, True, []))

    def test_a_new_launcher_that_does_not_refresh_either_leaves_the_copying_to_us(self):
        self.install("alpha")
        files = {**self.both.loader_files, "start_modded_client.py": b"print('still no refresh')\n"}
        ok, _message, restart = self.apply(replace(self.both, loader_files=files))
        self.assertEqual((ok, restart, self.synced), (True, True, [["alpha"]]))

    def test_mods_that_are_not_used_need_no_copying_and_no_restart(self):
        self.install("beta")
        ok, message, restart = self.apply(self.mods_only)
        self.assertEqual((ok, restart, self.synced), (True, False, []))
        self.assertIn("not ones you use", message)

    def test_a_copy_into_mods_that_fails_is_told_and_does_not_restart_into_old_code(self):
        self.install("alpha")

        def sync(root, names):
            raise OSError("locked")
        with self.assertLogs("mod_updates", level="WARNING"):
            ok, message, restart = mu.apply(self.root, self.mods_only, now=lambda: 5.0, sync=sync)
        self.assertEqual((ok, restart), (False, False))
        self.assertTrue(message.startswith("Updated the mods, but could not copy the new mods into mods/: locked"), message)
        self.assertIn("modctl.bat update", message)                                      # (an older launcher will not do it at the next start)
        self.assertNotIn("Start the game again", message)
        self.assertEqual(mu.read_state(self.root)["sha"], SHA2)                          # (the catalogue IS updated)
        self.assertEqual((self.catalogue / "alpha" / "alpha.py").read_text(encoding="utf-8"), "x = 2\n")

    def test_the_default_copy_is_the_launchers_own_modctl(self):
        self.install("alpha")
        with mock.patch.object(mu, "sync_installed", return_value=["alpha"]) as sync:
            ok, _message, restart = mu.apply(self.root, self.mods_only, now=lambda: 5.0)
        self.assertEqual((ok, restart), (True, True))
        sync.assert_called_once_with(self.root, ["alpha"])


# ── the bar ──────────────────────────────────────────────────────────────────

class LauncherBarTests(t.BarBase):
    def setUp(self):
        super().setUp()
        write_loader(self.root, LOADER_V1)
        gh = FakeGitHub(mods_sha=SHA2, loader_files=LOADER_V2, zip_bytes=t.make_zip(t.MODS_V2, SHA2))
        self.both = mu.check(self.root, now=lambda: NOW, http=gh)
        self.launcher_only = replace(self.both, backend="", sha="", updated=[], added=[], mods={})

    def test_with_a_new_launcher_the_bar_says_so_and_the_button_changes_its_name(self):
        self.offered(self.launcher_only)
        self.assertIn("Updates available:  launcher: modctl.py, modloader.py, start_modded_client.py, MOD_AUTHORS_README.md", self.text)
        self.assertIn("UPDATE", self.text)
        self.assertNotIn("UPDATE MODS", self.text)
        self.assertTrue(any("nothing changes until you press UPDATE" in line and "UPDATE MODS" not in line for line in self.text), self.text)

    def test_mods_and_launcher_are_listed_together(self):
        self.offered(self.both)
        self.assertTrue(any(line.startswith("Updates available:  updated: alpha   new: gamma   launcher: ") for line in self.text), self.text)

    def test_mods_alone_keep_their_wording(self):
        self.offered(replace(self.both, loader_files={}, loader_updated=[], loader_added=[]))
        self.assertIn("Mod updates available:  updated: alpha   new: gamma", self.text)
        self.assertIn("UPDATE MODS", self.text)

    def test_the_whole_caption_fits_at_the_normal_window_size(self):
        self.offered(self.both)
        caption = next(line for line in self.text if "nothing changes" in line)
        self.assertFalse(caption.endswith("..."))

    def test_the_launcher_part_is_not_the_one_that_gives_way_when_there_is_not_room(self):
        self.offered(replace(self.both, updated=[f"{'x' * 40}{i}" for i in range(5)], added=[f"{'y' * 40}{i}" for i in range(4)]))
        (line,) = [line for line in self.text if line.startswith("Updates available")]
        self.assertTrue(line.endswith("launcher: 4 files"), line)
        self.assertNotIn("...", line)

    def test_one_launcher_file_is_a_file_not_files(self):
        self.assertEqual(mu.summary_counts(mu.Update("", "", loader_updated=["a.py"])), "launcher: 1 file")
        self.assertEqual(mu.summary_counts(mu.Update("", "", loader_added=["a.py", "b.py"])), "launcher: 2 files")
        self.assertEqual(mu.summary_counts(mu.Update("zip", SHA1, updated=["a", "b"], added=["c"])), "updated: 2   new: 1")

    def test_mods_that_cannot_be_applied_here_still_leave_the_launcher_to_update(self):
        state = self.offered(replace(self.both, backend="git", blocked="mods_available has local changes"))
        self.assertEqual(state["phase"], "available")
        self.assertIn("update", state["rects"])
        caption = next(line for line in self.text if "local changes" in line)
        self.assertIn("UPDATE does the launcher only", caption)

    def test_a_blocked_update_with_nothing_for_the_launcher_is_still_just_a_notice(self):
        state = self.offered(mu.Update(backend="git", sha=SHA2, updated=["alpha"], blocked="mods_available has local changes"))
        self.assertEqual(state["phase"], "blocked")
        self.assertEqual(list(state["rects"]), ["later"])

    def test_pressing_update_swaps_the_launcher_then_restarts(self):
        state = self.offered(self.launcher_only)
        button = state["rects"]["update"]
        self.assertTrue(mu.handle_click(self.win, t.click(button.center)))
        self.assertEqual(self.draw()["phase"], "applying")
        self.assertIn("Updating...", self.text)
        self.wait("apply")
        state = self.draw()
        self.assertEqual(state["phase"], "restarting")
        self.assertIn("Restarting with the new versions...", self.text)
        self.assertEqual(read_loader(self.root)["modloader.py"], b"VERSION = 2\n")
        self.assertEqual(len(self.restarts), 1)
        self.assertEqual([e.type for e in self.quits], [pygame.QUIT])

    def test_pressing_update_for_mods_alone_still_says_updating_the_mods(self):
        state = self.offered(replace(self.both, loader_files={}, loader_updated=[], loader_added=[]))
        mu.handle_click(self.win, t.click(state["rects"]["update"].center))
        self.draw()
        self.assertIn("Updating the mods...", self.text)
        self.wait("apply")

    def test_later_leaves_every_file_alone(self):
        before = read_loader(self.root)
        state = self.offered(self.launcher_only)
        mu.handle_click(self.win, t.click(state["rects"]["later"].center))
        self.assertEqual(self.draw()["phase"], "idle")
        self.assertEqual(read_loader(self.root), before)
        self.assertEqual(self.restarts, [])

    def test_a_failed_launcher_update_is_shown_in_red_and_does_not_restart(self):
        files = {**self.launcher_only.loader_files, "modloader.py": b"def broken(:\n"}
        state = self.offered(replace(self.launcher_only, loader_files=files))
        mu.handle_click(self.win, t.click(state["rects"]["update"].center))
        with self.assertLogs("mod_updates", level="WARNING"):
            self.draw()
            self.wait("apply")
        state = self.draw()
        self.assertEqual(state["phase"], "failed")
        self.assertTrue(any("does not compile" in line for line in self.text), self.text)
        self.assertEqual(self.restarts, [])


class PhaseTests(t.BarBase):
    def phase_after_first_frame(self):
        with mock.patch.object(mu, "check", return_value=None):
            self.draw()
            self.wait("check") if mu._state(self.win)["check"] else None
        return mu._state(self.win)["phase"]

    def test_a_package_with_only_a_launcher_is_still_checked(self):
        shutil.rmtree(self.catalogue)
        write_loader(self.root, LOADER_V1)
        self.assertEqual(self.phase_after_first_frame(), "checking")

    def test_a_folder_with_neither_is_left_alone(self):
        shutil.rmtree(self.catalogue)
        self.assertEqual(self.phase_after_first_frame(), "off")


# ── a core mod that is older than this one ───────────────────────────────────

class BlockImports(importlib.abc.MetaPathFinder):
    """Makes ``import <name>`` fail, as it does for a core folder that lacks the file."""

    def __init__(self, *names):
        self.names = set(names)

    def find_spec(self, fullname, path=None, target=None):
        if fullname in self.names:
            raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
        return None


def load_without(path: Path, name: str, *blocked: str):
    """Import the module at *path* afresh, with the *blocked* modules (and any copy already imported) not importable."""
    finder = BlockImports(*blocked)
    saved = {n: sys.modules.pop(n) for n in blocked if n in sys.modules}
    sys.meta_path.insert(0, finder)
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module                  # (the dataclass decorator looks its module up by name while the class is built)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.meta_path.remove(finder)
        sys.modules.pop(name, None)
        sys.modules.update(saved)


class FakeLoginClass:
    pass


class OlderCoreTests(unittest.TestCase):
    PATH = Path(mu.__file__)

    def setUp(self):
        patcher = mock.patch.dict(modloader.ATTACH_FAILURES)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_with_the_helpers_present_there_is_no_problem(self):
        self.assertIsNone(mu.core_problem())

    def test_the_module_still_imports_without_the_core_files_and_says_what_is_missing(self):
        module = load_without(self.PATH, "mod_updates_nocore", "mod_digest", "relaunch")
        self.assertIsNone(module.mod_digest)
        self.assertIsNone(module.relaunch)
        problem = module.core_problem()
        self.assertIn("mod_digest.py", problem)
        self.assertIn("relaunch.py", problem)
        self.assertIn("older", problem)

    def test_each_file_is_checked_on_its_own(self):
        only_digest = load_without(self.PATH, "mod_updates_nodigest", "mod_digest")
        self.assertIn("mod_digest.py", only_digest.core_problem())
        self.assertNotIn("relaunch", only_digest.core_problem())
        only_relaunch = load_without(self.PATH, "mod_updates_norelaunch", "relaunch")
        self.assertIn("relaunch.py", only_relaunch.core_problem())
        self.assertNotIn("mod_digest", only_relaunch.core_problem())

    def test_a_helper_that_lacks_a_function_this_mod_calls_is_a_problem_too(self):
        stale = SimpleNamespace(digests=mod_digest.digests)                              # (an older mod_digest: no diff, counts ...)
        with mock.patch.object(mu, "mod_digest", stale):
            problem = mu.core_problem()
        self.assertIn("mod_digest.diff", problem)
        self.assertIn("mod_digest.counts", problem)
        self.assertNotIn("mod_digest.digests", problem)
        with mock.patch.object(mu, "relaunch", SimpleNamespace()):
            self.assertIn("relaunch.restart_after_exit", mu.core_problem())

    def test_installing_with_an_older_core_hooks_nothing_and_reports_on_the_mods_page(self):
        module = load_without(self.PATH, "mod_updates_nocore2", "mod_digest", "relaunch")
        with mock.patch.object(modloader, "install_methods") as install_methods:
            problems = module.install(FakeLoginClass)
        install_methods.assert_not_called()
        self.assertEqual(len(problems), 1)
        self.assertEqual(modloader.ATTACH_FAILURES["mod_updates"], problems[0])
        self.assertFalse(FakeLoginClass.__dict__.get("_mod_updates_installed"))

    def test_installing_with_a_current_core_still_hooks_the_login_screen(self):
        with mock.patch.object(modloader, "install_methods", return_value=[]) as install_methods:
            cls = type("Fresh", (), {})
            self.assertEqual(mu.install(cls), [])
        install_methods.assert_called_once()
        self.assertNotIn("mod_updates", modloader.ATTACH_FAILURES)


if __name__ == "__main__":
    unittest.main()

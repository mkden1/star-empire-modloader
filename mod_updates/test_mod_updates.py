"""Tests for mod_updates.py: the HTTPS calls (faked), the zip (including hostile ones), swapping mods into the catalogue (with failures),
the git path (real temporary repositories), the check and what it records, applying, and the bar on the login screen."""

from __future__ import annotations
import modloader

import io
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

import mod_digest
import mod_updates as mu

pygame.init()
pygame.font.init()
WINDOW = (1200, 720)
SHA1, SHA2 = "a" * 40, "b" * 40


# ── helpers ──────────────────────────────────────────────────────────────────

def write_mods(folder: Path, mods: "dict[str, dict[str, str | bytes]]") -> None:
    for name, files in mods.items():
        for relative, content in files.items():
            path = folder / name / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)


def make_zip(mods: "dict[str, dict[str, str | bytes]]", sha: str = SHA1, extra: "dict[str, bytes] | None" = None, top=None) -> bytes:
    """A GitHub-style zip of a branch: ``<repo>-<sha>/`` holding the mod folders and a few loose files."""
    top = top or f"star-empire-modloader-{sha}"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        def put(path: str, content: bytes, mode: int = 0o100644):
            info = zipfile.ZipInfo(path)
            info.external_attr = mode << 16
            archive.writestr(info, content)

        archive.writestr(zipfile.ZipInfo(top + "/"), b"")
        put(f"{top}/README.md", b"# the mods")
        put(f"{top}/LICENSE", b"MIT")
        for name, files in mods.items():
            archive.writestr(zipfile.ZipInfo(f"{top}/{name}/"), b"")
            for relative, content in files.items():
                put(f"{top}/{name}/{relative}", content.encode("utf-8") if isinstance(content, str) else content)
        for path, content in (extra or {}).items():
            put(path, content)
    return buffer.getvalue()


MODS_V1 = {"alpha": {"mod.json": "{}", "alpha.py": "x = 1\n", "README.md": "# alpha"}, "beta": {"mod.json": "{}", "beta.py": "y = 1\n"}}
MODS_V2 = {"alpha": {"mod.json": "{}", "alpha.py": "x = 2\n", "README.md": "# alpha"}, "beta": {"mod.json": "{}", "beta.py": "y = 1\n"},
           "gamma": {"mod.json": "{}", "gamma.py": "z = 1\n"}}


def pkt(text: bytes) -> bytes:
    """One line of a git ref advertisement: four hex digits (the length, themselves included) and the text."""
    return b"%04x" % (len(text) + 4) + text


def refs_body(refs: "dict[str, str]") -> bytes:
    """What ``github.com/<repo>.git/info/refs?service=git-upload-pack`` answers: the service line, a separator, HEAD with the server's
    capabilities after a NUL, every branch, a tag with its peeled entry, a separator."""
    first = next(iter(refs.values()), SHA1)
    lines = [pkt(b"# service=git-upload-pack\n"), b"0000", pkt(f"{first} HEAD".encode() + b"\0multi_ack thin-pack agent=git/test\n")]
    lines += [pkt(f"{sha} refs/heads/{name}\n".encode()) for name, sha in refs.items()]
    lines += [pkt(f"{first} refs/tags/v1\n".encode()), pkt(f"{first} refs/tags/v1^{{}}\n".encode()), b"0000"]
    return b"".join(lines)


# a real answer for this repository (captured 2026-11-22), so the parser is tested on what GitHub really sends
REAL_REFS = (b"001e# service=git-upload-pack\n0000015eb5e0f3da410199539f5dd3c35b7741884f2e723c HEAD\x00multi_ack thin-pack side-band "
             b"side-band-64k ofs-delta shallow deepen-since deepen-not deepen-relative no-progress include-tag multi_ack_detailed "
             b"allow-tip-sha1-in-want allow-reachable-sha1-in-want no-done symref=HEAD:refs/heads/modloader filter object-format=sha1 "
             b"agent=git/github-9922ec520325-Linux\n003caf6bb919e74a6e2015a7d135bca0e7dd2f46bf74 refs/heads/dev\n"
             b"0042b5e0f3da410199539f5dd3c35b7741884f2e723c refs/heads/modloader\n"
             b"00479163e6395f3e4c12ae79087cec3cac5bf5c3f480 refs/heads/mods_available\n0000")


class FakeHttp:
    """What ``mod_updates.http_get`` is, answering from a table: the branch list (``refs``, default just the mods branch at *sha*)
    and the zip. It has no answer for the REST API at all: the mods part must never ask for one."""

    def __init__(self, sha=SHA1, zip_bytes=b"", refs_status=200, fail=None, refs=None):
        self.sha, self.zip_bytes, self.refs_status, self.fail = sha, zip_bytes, refs_status, fail
        self.refs = refs if refs is not None else {mu.DEFAULT_BRANCH: sha}
        self.calls: list = []

    def __call__(self, url, headers=None, timeout=12.0, max_bytes=0):
        self.calls.append((url, dict(headers or {})))
        if self.fail:
            raise mu.UpdateError(self.fail)
        if "api.github.com" in url:
            raise AssertionError("the REST API must not be used here: " + url)
        if "/info/refs" in url:
            return self.refs_status, {}, refs_body(self.refs)
        return 200, {}, self.zip_bytes


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "pkg"
        (self.root / "mods_available").mkdir(parents=True)
        (self.root / "mods").mkdir()
        self.catalogue = self.root / "mods_available"
        mu.reset_for_tests()
        self.addCleanup(mu.reset_for_tests)
        self.addCleanup(finish_threads)

    def catalogue_has(self, mods):
        write_mods(self.catalogue, mods)

    def install(self, *names):
        for name in names:
            (self.root / "mods" / name).mkdir(parents=True, exist_ok=True)
            (self.root / "mods" / name / "mod.json").write_text("{}", encoding="utf-8")


def finish_threads():
    for thread in threading.enumerate():
        if thread.name.startswith("mod_updates-"):
            thread.join(10)


# ── configuration and the network ────────────────────────────────────────────

class ConfigTests(unittest.TestCase):
    def test_the_default_is_the_projects_mods_branch(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(mu.ENV_REPO, None)
            self.assertEqual(mu.config(), ("mkden1/star-empire-modloader", "mods_available"))

    def test_a_fork_or_branch_can_be_named(self):
        with mock.patch.dict(os.environ, {mu.ENV_REPO: "someone/their-fork"}):
            self.assertEqual(mu.config(), ("someone/their-fork", "mods_available"))
        with mock.patch.dict(os.environ, {mu.ENV_REPO: "someone/their-fork@testing/x"}):
            self.assertEqual(mu.config(), ("someone/their-fork", "testing/x"))

    def test_a_malformed_value_is_ignored(self):
        for value in ("nonsense", "a/b/c", "a b/c", "../x/y", "a/b@bad branch", "a/b@", "https://evil.example/x"):
            with mock.patch.dict(os.environ, {mu.ENV_REPO: value}):
                repo, branch = mu.config()
                self.assertIn(repo, ("mkden1/star-empire-modloader", "a/b"), value)
                self.assertEqual(branch, "mods_available", value)


class FakeResponse:
    def __init__(self, chunks, status=200, headers=None):
        self.chunks, self.status, self.headers = list(chunks), status, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size=-1):
        return self.chunks.pop(0) if self.chunks else b""


class HttpGetTests(unittest.TestCase):
    def test_a_get_returns_status_headers_and_body(self):
        response = FakeResponse([b"ab", b"cd"], headers={"ETag": '"x"'})
        with mock.patch.object(urllib.request, "urlopen", return_value=response) as opened:
            status, headers, body = mu.http_get("https://example.test/a", {"Accept": "x"}, 5.0, 100)
        self.assertEqual((status, headers["ETag"], body), (200, '"x"', b"abcd"))
        request = opened.call_args.args[0]
        self.assertEqual(request.get_header("User-agent"), mu.USER_AGENT)
        self.assertEqual(request.get_header("Accept"), "x")
        self.assertEqual(opened.call_args.kwargs["timeout"], 5.0)

    def test_the_request_names_a_generic_client_and_nothing_personal(self):
        with mock.patch.object(urllib.request, "urlopen", return_value=FakeResponse([b""])) as opened:
            mu.http_get("https://example.test/a")
        self.assertEqual(sorted(k.lower() for k in opened.call_args.args[0].headers), ["user-agent"])

    def test_only_https_is_used(self):
        with mock.patch.object(urllib.request, "urlopen", return_value=FakeResponse([b"x"])) as opened, self.assertRaises(mu.UpdateError):
            mu.http_get("http://example.test/a")
        opened.assert_not_called()

    def test_a_body_larger_than_allowed_is_refused_as_declared_or_as_it_streams(self):
        declared = FakeResponse([b"x"], headers={"Content-Length": "5000"})
        with mock.patch.object(urllib.request, "urlopen", return_value=declared), self.assertRaises(mu.UpdateError):
            mu.http_get("https://example.test/a", max_bytes=100)
        streaming = FakeResponse([b"x" * 60, b"x" * 60])
        with mock.patch.object(urllib.request, "urlopen", return_value=streaming), self.assertRaises(mu.UpdateError):
            mu.http_get("https://example.test/a", max_bytes=100)

    def test_other_errors_and_no_connection_are_update_errors(self):
        for failure in (urllib.error.HTTPError("https://x", 404, "no", {}, None), urllib.error.HTTPError("https://x", 304, "same", {}, None),
                        urllib.error.URLError("offline"), OSError("reset"), TimeoutError("slow")):
            with mock.patch.object(urllib.request, "urlopen", side_effect=failure), self.assertRaises(mu.UpdateError) as caught:
                mu.http_get("https://example.test/a")
            self.assertNotIsInstance(caught.exception, mu.RateLimited)

    def refused(self, code, headers):
        error = urllib.error.HTTPError("https://x", code, "refused", headers, None)
        with mock.patch.object(urllib.request, "urlopen", side_effect=error):
            return mu.http_get("https://example.test/a")

    def test_a_refusal_because_the_request_limit_is_used_up_says_when_it_is_lifted(self):
        for code in (403, 429):
            with self.assertRaises(mu.RateLimited, msg=code) as caught:
                self.refused(code, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1795360515"})
            self.assertEqual(caught.exception.reset, 1795360515.0)
            self.assertIn("request limit", str(caught.exception))
            self.assertIsInstance(caught.exception, mu.UpdateError)                       # (everything that catches UpdateError still does)

    def test_the_limit_headers_are_found_in_any_case_and_in_a_real_header_object(self):
        import email.message
        message = email.message.Message()
        message["x-ratelimit-remaining"] = "0"
        message["X-RATELIMIT-RESET"] = "1795360515"
        with self.assertRaises(mu.RateLimited):
            self.refused(403, message)

    def test_a_refusal_that_is_not_about_the_limit_is_an_ordinary_one(self):
        for code, headers in ((403, {}), (403, {"X-RateLimit-Remaining": "7", "X-RateLimit-Reset": "1795360515"}),
                              (403, {"X-RateLimit-Remaining": "0"}), (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "soon"}),
                              (404, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1795360515"}), (500, {"Retry-After": "30"})):
            with self.assertRaises(mu.UpdateError, msg=(code, headers)) as caught:
                self.refused(code, headers)
            self.assertNotIsInstance(caught.exception, mu.RateLimited)
            self.assertIn(str(code), str(caught.exception))

    def test_a_retry_after_means_that_long_from_now(self):
        self.assertEqual(mu._rate_limit_reset({"Retry-After": " 120 "}, now=lambda: 1000.0), 1120.0)
        self.assertEqual(mu._rate_limit_reset({"retry-after": "5", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "9999"}, now=lambda: 1000.0), 1005.0)
        self.assertIsNone(mu._rate_limit_reset({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, now=lambda: 1000.0))
        self.assertIsNone(mu._rate_limit_reset({}, now=lambda: 1000.0))
        self.assertIsNone(mu._rate_limit_reset(None, now=lambda: 1000.0))

    def test_a_429_with_a_retry_after_is_rate_limited(self):
        with self.assertRaises(mu.RateLimited) as caught:
            self.refused(429, {"Retry-After": "90"})
        self.assertGreater(caught.exception.reset, time.time() + 60)


class ParseRefsTests(unittest.TestCase):
    def test_what_github_really_sends_gives_the_three_branches(self):
        self.assertEqual(mu.parse_refs(REAL_REFS), {"dev": "af6bb919e74a6e2015a7d135bca0e7dd2f46bf74",
                                                    "modloader": "b5e0f3da410199539f5dd3c35b7741884f2e723c",
                                                    "mods_available": "9163e6395f3e4c12ae79087cec3cac5bf5c3f480"})

    def test_only_branches_count_not_head_tags_or_peeled_tags(self):
        refs = mu.parse_refs(refs_body({"main": SHA1, "feature/x-1.2": SHA2}))
        self.assertEqual(refs, {"main": SHA1, "feature/x-1.2": SHA2})

    def test_the_first_line_carries_capabilities_after_a_nul_which_are_not_part_of_the_name(self):
        body = pkt(b"# service=git-upload-pack\n") + b"0000" + pkt(f"{SHA1} refs/heads/main".encode() + b"\0multi_ack agent=x\n") + b"0000"
        self.assertEqual(mu.parse_refs(body), {"main": SHA1})

    def test_a_ref_with_an_odd_name_or_hash_is_skipped_not_trusted(self):
        body = (pkt(f"{SHA1} refs/heads/good\n".encode()) + pkt(f"{'A' * 40} refs/heads/upper\n".encode()) +
                pkt(f"{'a' * 39} refs/heads/short\n".encode()) + pkt(f"{SHA2} refs/heads/with space\n".encode()) +
                pkt(f"{SHA2} refs/heads/bad;name\n".encode()) + pkt(f"{SHA2} refs/heads/\n".encode()) + pkt(f"{SHA2}  refs/heads/x\n".encode()))
        self.assertEqual(mu.parse_refs(body), {"good": SHA1})

    def test_a_branch_listed_twice_keeps_the_last(self):
        self.assertEqual(mu.parse_refs(pkt(f"{SHA1} refs/heads/m\n".encode()) + pkt(f"{SHA2} refs/heads/m\n".encode())), {"m": SHA2})

    def test_a_line_without_a_trailing_newline_is_fine(self):
        self.assertEqual(mu.parse_refs(pkt(f"{SHA1} refs/heads/m".encode())), {"m": SHA1})

    def test_an_answer_that_is_not_a_ref_list_is_refused(self):
        for body in (b"<!DOCTYPE html><html>", b"", b"zzzzrest", b"001e# service", b"0001x", b"0003", b"00" + b"1e" + b"short", b"001E" + b"x" * 26,
                     pkt(f"{SHA1} refs/heads/m\n".encode()) + b"ffff"):
            with self.assertRaises(mu.UpdateError, msg=body):
                mu.parse_refs(body)

    def test_an_answer_with_no_branch_in_it_is_refused(self):
        for body in (b"0000", pkt(b"# service=git-upload-pack\n") + b"0000", refs_body({})):
            with self.assertRaises(mu.UpdateError, msg=body):
                mu.parse_refs(body)

    def test_a_length_that_stops_short_or_runs_over_is_refused(self):
        line = pkt(f"{SHA1} refs/heads/mmmmmmmm\n".encode())
        with self.assertRaises(mu.UpdateError):
            mu.parse_refs(line[:-2])                                                  # (still reads as a branch if the cut is ignored)
        with self.assertRaises(mu.UpdateError):
            mu.parse_refs(line + b"0002")

    def test_a_length_written_in_capitals_is_not_what_git_sends_and_is_refused(self):
        line = pkt(f"{SHA1} refs/heads/m\n".encode())
        self.assertNotEqual(line[:4], line[:4].upper())                                  # (the length has a hex letter in it)
        with self.assertRaises(mu.UpdateError):
            mu.parse_refs(line[:4].upper() + line[4:])


class LatestShasTests(unittest.TestCase):
    def test_one_request_for_the_git_address_gives_every_branch(self):
        http = FakeHttp(refs={"mods_available": SHA1, "modloader": SHA2})
        self.assertEqual(mu.latest_shas("o/r", http), {"mods_available": SHA1, "modloader": SHA2})
        (url, headers), = http.calls
        self.assertEqual(url, "https://github.com/o/r.git/info/refs?service=git-upload-pack")
        self.assertEqual(headers, {})

    def test_the_rest_api_is_not_involved(self):
        http = FakeHttp()                                                         # (it raises if the REST API is asked)
        mu.latest_shas("o/r", http)

    def test_the_answer_is_size_capped(self):
        seen = []

        def http(url, headers=None, timeout=0, max_bytes=0):
            seen.append(max_bytes)
            return 200, {}, refs_body({"m": SHA1})
        mu.latest_shas("o/r", http)
        self.assertEqual(seen, [mu.REFS_BYTES])

    def test_a_status_other_than_ok_is_refused(self):
        for status in (202, 204, 301):
            with self.assertRaises(mu.UpdateError, msg=status):
                mu.latest_shas("o/r", FakeHttp(refs_status=status))

    def test_a_page_that_is_not_a_ref_list_is_refused(self):
        def http(url, headers=None, timeout=0, max_bytes=0):
            return 200, {}, b"<html>Sign in to GitHub</html>"
        with self.assertRaises(mu.UpdateError):
            mu.latest_shas("o/r", http)


class TipsTests(unittest.TestCase):
    def test_nothing_is_asked_until_a_branch_is_wanted(self):
        http = FakeHttp()
        mu.Tips("o/r", http)
        self.assertEqual(http.calls, [])

    def test_all_the_branches_come_from_one_request(self):
        http = FakeHttp(refs={"mods_available": SHA1, "modloader": SHA2})
        tips = mu.Tips("o/r", http)
        self.assertEqual((tips("mods_available"), tips("modloader"), tips("mods_available")), (SHA1, SHA2, SHA1))
        self.assertEqual(len(http.calls), 1)

    def test_a_branch_that_is_not_there_is_an_error_that_names_it(self):
        tips = mu.Tips("o/r", FakeHttp(refs={"main": SHA1}))
        with self.assertRaises(mu.UpdateError) as caught:
            tips("modloader")
        self.assertIn("modloader", str(caught.exception))
        self.assertEqual(tips("main"), SHA1)                                        # (the other branches still work)

    def test_a_failed_request_is_remembered_not_repeated(self):
        http = FakeHttp(fail="no connection")
        tips = mu.Tips("o/r", http)
        for _ in range(3):
            with self.assertRaises(mu.UpdateError):
                tips("mods_available")
        self.assertEqual(len(http.calls), 1)

    def test_the_kind_of_failure_is_kept(self):
        def http(url, headers=None, timeout=0, max_bytes=0):
            raise mu.RateLimited("used up", 5.0)
        tips = mu.Tips("o/r", http)
        with self.assertRaises(mu.RateLimited):
            tips("x")
        with self.assertRaises(mu.RateLimited):
            tips("x")


class DownloadTests(unittest.TestCase):
    def test_it_downloads_exactly_that_commit(self):
        http = FakeHttp(zip_bytes=b"zipdata")
        self.assertEqual(mu.download_zip("o/r", SHA2, http), b"zipdata")
        self.assertEqual(http.calls[0][0], f"https://codeload.github.com/o/r/zip/{SHA2}")

    def test_only_a_commit_sha_is_downloaded(self):
        for sha in ("main", "../../x", "", "a" * 39):
            http = FakeHttp()
            with self.assertRaises(mu.UpdateError, msg=sha):
                mu.download_zip("o/r", sha, http)
            self.assertEqual(http.calls, [])

    def test_a_failed_download_is_an_error(self):
        def http(url, headers=None, timeout=0, max_bytes=0):
            return 500, {}, b""

        with self.assertRaises(mu.UpdateError):
            mu.download_zip("o/r", SHA1, http)


# ── the zip ──────────────────────────────────────────────────────────────────

class ReadModsTests(unittest.TestCase):
    def test_the_mods_of_a_branch_zip_come_out_by_name_with_their_files(self):
        mods = mu.read_mods(make_zip(MODS_V2))
        self.assertEqual(sorted(mods), ["alpha", "beta", "gamma"])
        self.assertEqual(mods["alpha"]["alpha.py"], b"x = 2\n")
        self.assertEqual(sorted(mods["beta"]), ["beta.py", "mod.json"])

    def test_nested_files_keep_their_paths(self):
        mods = mu.read_mods(make_zip({"alpha": {"mod.json": "{}", "web/js/app.js": "1", "web/index.html": "2"}}))
        self.assertEqual(sorted(mods["alpha"]), ["mod.json", "web/index.html", "web/js/app.js"])

    def test_only_folders_with_a_mod_json_are_mods(self):
        mods = mu.read_mods(make_zip({"alpha": {"mod.json": "{}"}, "notes": {"readme.txt": "x"}, ".github": {"mod.json": "{}"},
                                      "_private": {"mod.json": "{}"}}))
        self.assertEqual(sorted(mods), ["alpha"])

    def test_loose_files_beside_the_mods_are_ignored(self):
        mods = mu.read_mods(make_zip(MODS_V1, extra={"star-empire-modloader-" + SHA1 + "/conftest.py": b"x"}))
        self.assertEqual(sorted(mods), ["alpha", "beta"])

    def test_a_path_that_could_leave_the_folder_is_refused(self):
        top = "star-empire-modloader-" + SHA1
        for bad in (f"{top}/alpha/../../evil.py", f"{top}/alpha/./x.py", "/abs/evil.py", f"{top}/alpha/..\\..\\evil.py", "C:/evil.py"):
            data = make_zip(MODS_V1, extra={bad: b"boom"})
            with self.assertRaises(mu.UpdateError, msg=bad):
                mu.read_mods(data)

    def test_a_zip_whose_every_path_is_absolute_or_has_a_drive_is_refused(self):
        for top in ("/abs", "C:", "D:/x"):
            with self.assertRaises(mu.UpdateError, msg=top):
                mu.read_mods(make_zip(MODS_V1, top=top))

    def test_a_zip_with_more_than_one_top_folder_is_refused(self):
        with self.assertRaises(mu.UpdateError):
            mu.read_mods(make_zip(MODS_V1, extra={"other-folder/alpha/x.py": b"x"}))

    def test_a_symlink_is_not_taken(self):
        top = "star-empire-modloader-" + SHA1
        data = make_zip(MODS_V1, extra={f"{top}/alpha/link.py": b"/etc/passwd"})
        buffer = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(buffer, "w") as target:
            for info in source.infolist():
                if info.filename.endswith("alpha/link.py"):
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                target.writestr(info, source.read(info))
        mods = mu.read_mods(buffer.getvalue())
        self.assertNotIn("link.py", mods["alpha"])

    def test_too_many_files_a_zip_that_is_too_big_unpacked_or_a_huge_file_is_refused(self):
        data = make_zip(MODS_V1)
        with mock.patch.object(mu, "MAX_FILES", 3), self.assertRaises(mu.UpdateError):
            mu.read_mods(data)
        with mock.patch.object(mu, "MAX_UNZIPPED_BYTES", 10), self.assertRaises(mu.UpdateError):
            mu.read_mods(data)
        with mock.patch.object(mu, "MAX_FILE_BYTES", 3), self.assertRaises(mu.UpdateError):
            mu.read_mods(data)

    def test_something_that_is_not_a_zip_is_refused(self):
        with self.assertRaises(mu.UpdateError):
            mu.read_mods(b"<html>not a zip</html>")


class ApplyZipTests(Base):
    def setUp(self):
        super().setUp()
        self.mods = mu.read_mods(make_zip({**MODS_V2, "alpha": {**MODS_V2["alpha"], "test_alpha.py": "assert 1", "__pycache__/x.pyc": b"x",
                                                                 "screenshots/a.png": b"png"}}))

    def test_an_updated_and_a_new_mod_are_put_in_and_the_untouched_one_is_left_alone(self):
        self.catalogue_has(MODS_V1)
        before = (self.catalogue / "beta" / "beta.py").stat().st_mtime_ns
        mu.apply_zip(self.catalogue, self.mods, ["alpha", "gamma"])
        self.assertEqual((self.catalogue / "alpha" / "alpha.py").read_text(encoding="utf-8"), "x = 2\n")
        self.assertTrue((self.catalogue / "gamma" / "mod.json").is_file())
        self.assertEqual((self.catalogue / "beta" / "beta.py").stat().st_mtime_ns, before)

    def test_the_catalogue_keeps_readmes_and_screenshots_but_not_tests_or_caches(self):
        mu.apply_zip(self.catalogue, self.mods, ["alpha"])
        kept = sorted(p.relative_to(self.catalogue / "alpha").as_posix() for p in (self.catalogue / "alpha").rglob("*") if p.is_file())
        self.assertEqual(kept, ["README.md", "alpha.py", "mod.json", "screenshots/a.png"])

    def test_the_old_copy_is_kept_one_generation_back_and_nothing_else_is_left_over(self):
        self.catalogue_has(MODS_V1)
        mu.apply_zip(self.catalogue, self.mods, ["alpha"])
        self.assertEqual((self.catalogue / ".update_old" / "alpha" / "alpha.py").read_text(encoding="utf-8"), "x = 1\n")
        self.assertFalse((self.catalogue / ".update_new").exists())
        mu.apply_zip(self.catalogue, self.mods, ["alpha"])                              # a second time replaces the backup
        self.assertEqual((self.catalogue / ".update_old" / "alpha" / "alpha.py").read_text(encoding="utf-8"), "x = 2\n")

    def test_a_failure_part_way_puts_every_mod_back_as_it_was(self):
        self.catalogue_has(MODS_V1)
        real_rename = os.rename
        calls = []

        def failing(src, dst, *args, **kwargs):
            calls.append((str(src), str(dst)))
            if str(src).endswith(os.path.join(".update_new", "beta")):
                raise OSError("locked")
            return real_rename(src, dst, *args, **kwargs)

        with mock.patch.object(os, "rename", failing), self.assertRaises(OSError):
            mu.apply_zip(self.catalogue, self.mods, ["alpha", "beta", "gamma"])
        self.assertEqual((self.catalogue / "alpha" / "alpha.py").read_text(encoding="utf-8"), "x = 1\n")
        self.assertEqual((self.catalogue / "beta" / "beta.py").read_text(encoding="utf-8"), "y = 1\n")
        self.assertFalse((self.catalogue / "gamma").exists())
        self.assertFalse((self.catalogue / ".update_new").exists())

    def test_a_path_that_would_land_outside_is_refused_and_nothing_changes(self):
        self.catalogue_has(MODS_V1)
        evil = {"alpha": {"mod.json": b"{}", "../../escape.py": b"boom"}}
        with self.assertRaises(mu.UpdateError):
            mu.apply_zip(self.catalogue, evil, ["alpha"])
        self.assertFalse((self.catalogue.parent / "escape.py").exists())
        self.assertEqual((self.catalogue / "alpha" / "alpha.py").read_text(encoding="utf-8"), "x = 1\n")

    def test_the_picker_and_the_loader_do_not_see_the_working_folders(self):
        mu.apply_zip(self.catalogue, self.mods, ["alpha"])
        listed = [p.name for p in self.catalogue.iterdir() if not p.name.startswith((".", "_"))]
        self.assertEqual(listed, ["alpha"])


# ── git (real repositories) ──────────────────────────────────────────────────

GIT = shutil.which("git")


def run_git(cwd, *args):
    done = subprocess.run([GIT, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", "-C", str(cwd), *args],
                          capture_output=True, text=True, check=True)
    return done.stdout.strip()


@unittest.skipUnless(GIT, "git is not installed")
class GitTests(Base):
    def setUp(self):
        super().setUp()
        self.origin = Path(self._tmp.name) / "origin"
        self.origin.mkdir()
        run_git(self.origin, "init", "-q", "-b", "mods_available")
        write_mods(self.origin, MODS_V1)
        run_git(self.origin, "add", "-A")
        run_git(self.origin, "commit", "-q", "-m", "one")
        shutil.rmtree(self.catalogue)
        subprocess.run([GIT, "clone", "-q", "--branch", "mods_available", "--single-branch", str(self.origin), str(self.catalogue)],
                       capture_output=True, check=True)

    def push(self, message, changes=None, remove=()):
        write_mods(self.origin, changes or {})
        for path in remove:
            target = self.origin / path
            shutil.rmtree(target) if target.is_dir() else target.unlink()
        run_git(self.origin, "add", "-A")
        run_git(self.origin, "commit", "-q", "-m", message)

    def check(self):
        return mu.git_check(self.catalogue, GIT)

    def test_nothing_new_is_none(self):
        self.assertIsNone(self.check())

    def test_an_updated_a_new_and_a_removed_mod_are_found(self):
        self.push("two", {"alpha": {"alpha.py": "x = 2\n"}, "gamma": {"mod.json": "{}", "gamma.py": "z"}}, remove=["beta"])
        update = self.check()
        self.assertEqual((update.updated, update.added, update.removed), (["alpha"], ["gamma"], ["beta"]))
        self.assertEqual(update.backend, "git")
        self.assertIsNone(update.blocked)
        self.assertEqual(update.sha, run_git(self.origin, "rev-parse", "HEAD"))

    def test_applying_fast_forwards_the_clone(self):
        self.push("two", {"alpha": {"alpha.py": "x = 2\n"}})
        update = self.check()
        ok, message, restart = mu.apply(self.root, update)
        self.assertTrue(ok, message)
        self.assertEqual((self.catalogue / "alpha" / "alpha.py").read_text(encoding="utf-8"), "x = 2\n")
        self.assertEqual(run_git(self.catalogue, "rev-parse", "HEAD"), update.sha)
        self.assertIsNone(self.check())

    def test_a_change_to_a_readme_or_a_test_is_not_worth_asking_about(self):
        self.push("docs", {"alpha": {"README.md": "# changed", "test_alpha.py": "x"}, "beta": {"screenshots/a.png": b"png"}})
        update = self.check()
        self.assertTrue(update.quiet)
        self.assertEqual((update.updated, update.added), ([], []))

    def test_a_clone_with_local_changes_is_reported_and_not_touched(self):
        self.push("two", {"alpha": {"alpha.py": "x = 2\n"}})
        (self.catalogue / "beta" / "beta.py").write_text("y = hand edit\n", encoding="utf-8")
        update = self.check()
        self.assertIn("local changes", update.blocked)
        ok, message, restart = mu.apply(self.root, update)
        self.assertFalse(ok)
        self.assertEqual((self.catalogue / "beta" / "beta.py").read_text(encoding="utf-8"), "y = hand edit\n")
        self.assertEqual((self.catalogue / "alpha" / "alpha.py").read_text(encoding="utf-8"), "x = 1\n")

    def test_a_clone_that_has_diverged_is_reported_and_not_touched(self):
        self.push("two", {"alpha": {"alpha.py": "x = 2\n"}})
        write_mods(self.catalogue, {"beta": {"beta.py": "y = local commit\n"}})
        run_git(self.catalogue, "add", "-A")
        run_git(self.catalogue, "commit", "-q", "-m", "local")
        update = self.check()
        self.assertIn("fast-forward", update.blocked)
        self.assertFalse(mu.apply(self.root, update)[0])

    def test_an_unreachable_remote_is_an_error_not_a_hang(self):
        run_git(self.catalogue, "remote", "set-url", "origin", str(Path(self._tmp.name) / "nowhere"))
        with self.assertRaises(mu.UpdateError):
            self.check()

    def test_git_runs_with_every_prompt_off_and_no_credential_helper(self):
        seen = []

        def runner(cmd, **kwargs):
            seen.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch.dict(os.environ, {"GIT_TERMINAL_PROMPT": "1", "GCM_INTERACTIVE": "always"}), self.assertRaises(mu.UpdateError):
            mu.git_check(self.catalogue, "git", runner)                       # (whatever the machine says, the prompts are switched off)
        cmd, kwargs = seen[0]
        self.assertIn("credential.helper=", cmd)
        self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(kwargs["env"]["GCM_INTERACTIVE"], "never")
        self.assertIn("timeout", kwargs)

    def test_the_merge_is_a_fast_forward_or_nothing(self):
        self.push("two", {"alpha": {"alpha.py": "x = 2\n"}})
        write_mods(self.catalogue, {"beta": {"beta.py": "y = local commit\n"}})
        run_git(self.catalogue, "add", "-A")
        run_git(self.catalogue, "commit", "-q", "-m", "local")
        run_git(self.catalogue, "fetch", "-q", "origin")
        head = run_git(self.catalogue, "rev-parse", "HEAD")
        with self.assertRaises(mu.UpdateError):
            mu.git_apply(self.catalogue, GIT)
        self.assertEqual(run_git(self.catalogue, "rev-parse", "HEAD"), head)             # (no merge commit was made)

    def test_a_git_that_hangs_is_an_error(self):
        def runner(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 1)

        with self.assertRaises(mu.UpdateError):
            mu.git_check(self.catalogue, "git", runner)

    def test_the_check_chooses_git_for_a_clone_and_records_the_time_when_up_to_date(self):
        self.assertIsNone(mu.check(self.root, git=GIT))
        self.assertGreater(mu.read_state(self.root)["checked_at"], 0)

    def test_the_check_chooses_git_and_returns_the_update(self):
        self.push("two", {"alpha": {"alpha.py": "x = 2\n"}})
        update = mu.check(self.root, git=GIT)
        self.assertEqual(update.updated, ["alpha"])
        self.assertNotIn("checked_at", mu.read_state(self.root))                     # (asked again next start until applied)

    def test_a_clone_on_a_machine_without_git_is_left_alone(self):
        ran = []
        with mock.patch.object(mu, "git_program", return_value=None):
            self.assertIsNone(mu.check(self.root, runner=lambda *a, **k: ran.append(a)))
        self.assertEqual(ran, [])                                                   # (no git command, and no download either)


# ── the state file ───────────────────────────────────────────────────────────

class StateTests(Base):
    def test_there_is_none_to_begin_with(self):
        self.assertEqual(mu.read_state(self.root), {})

    def test_a_state_is_read_back_and_leaves_nothing_else_behind(self):
        self.assertTrue(mu.write_state(self.root, {"sha": SHA1, "checked_at": 5.0}))
        self.assertEqual(mu.read_state(self.root), {"sha": SHA1, "checked_at": 5.0})
        self.assertEqual([p.name for p in self.root.iterdir() if "mod_updates" in p.name], [mu.STATE_FILE])

    def test_a_damaged_state_is_none(self):
        for text in ("junk", "[]", "5", ""):
            (self.root / mu.STATE_FILE).write_text(text, encoding="utf-8")
            self.assertEqual(mu.read_state(self.root), {}, text)

    def test_a_state_that_cannot_be_written_says_so(self):
        self.assertFalse(mu.write_state(self.root / "no" / "folder", {"a": 1}))


# ── the check (zip backend) ──────────────────────────────────────────────────

class CheckTests(Base):
    def setUp(self):
        super().setUp()
        self.catalogue_has(MODS_V1)
        self.now = 1_000_000.0

    def check(self, http, **kwargs):
        return mu.check(self.root, now=lambda: self.now, http=http, **kwargs)

    def test_a_catalogue_that_matches_the_zip_has_nothing_to_offer_and_the_version_is_remembered(self):
        http = FakeHttp(zip_bytes=make_zip(MODS_V1))
        self.assertIsNone(self.check(http))
        self.assertEqual(mu.read_state(self.root), {"sha": SHA1, "checked_at": self.now})

    def test_a_newer_version_is_offered_with_what_it_changes(self):
        http = FakeHttp(sha=SHA2, zip_bytes=make_zip(MODS_V2, SHA2))
        update = self.check(http)
        self.assertEqual((update.updated, update.added, update.removed), (["alpha"], ["gamma"], []))
        self.assertEqual((update.backend, update.sha, update.repo), ("zip", SHA2, "mkden1/star-empire-modloader"))
        self.assertEqual(sorted(update.mods), ["alpha", "beta", "gamma"])

    def test_a_check_is_two_requests_the_branch_list_and_the_zip_and_never_the_rest_api(self):
        http = FakeHttp(sha=SHA2, zip_bytes=make_zip(MODS_V2, SHA2))
        self.check(http)
        self.assertEqual([url for url, _ in http.calls], ["https://github.com/mkden1/star-empire-modloader.git/info/refs?service=git-upload-pack",
                                                          f"https://codeload.github.com/mkden1/star-empire-modloader/zip/{SHA2}"])

    def test_nothing_is_recorded_for_an_offer_so_it_is_made_again_at_the_next_start(self):
        self.check(FakeHttp(sha=SHA2, zip_bytes=make_zip(MODS_V2, SHA2)))
        self.assertEqual(mu.read_state(self.root), {})
        later = self.check(FakeHttp(sha=SHA2, zip_bytes=make_zip(MODS_V2, SHA2)))
        self.assertIsNotNone(later)

    def test_a_check_just_made_is_not_made_again_for_a_while(self):
        mu.write_state(self.root, {"sha": SHA1, "checked_at": self.now - 60})
        http = FakeHttp(sha=SHA2, zip_bytes=make_zip(MODS_V2, SHA2))
        self.assertIsNone(self.check(http))
        self.assertEqual(http.calls, [])
        self.now += mu.CHECK_EVERY_S
        self.assertIsNotNone(self.check(http))

    def test_a_forced_check_ignores_the_wait(self):
        mu.write_state(self.root, {"sha": SHA1, "checked_at": self.now - 1})
        http = FakeHttp(sha=SHA2, zip_bytes=make_zip(MODS_V2, SHA2))
        self.assertIsNotNone(self.check(http, force=True))

    def test_a_branch_that_has_not_moved_costs_one_small_request_and_no_download(self):
        mu.write_state(self.root, {"sha": SHA1, "checked_at": 0})
        http = FakeHttp(sha=SHA1)
        self.assertIsNone(self.check(http))
        self.assertEqual(len(http.calls), 1)
        self.assertIn("/info/refs", http.calls[0][0])
        self.assertEqual(mu.read_state(self.root), {"sha": SHA1, "checked_at": self.now})

    def test_a_branch_that_has_moved_is_downloaded_even_when_another_branch_moves_too(self):
        mu.write_state(self.root, {"sha": SHA1, "checked_at": 0})
        http = FakeHttp(refs={mu.DEFAULT_BRANCH: SHA2, "dev": SHA1}, zip_bytes=make_zip(MODS_V2, SHA2))
        self.assertEqual(self.check(http).sha, SHA2)

    def test_the_branch_list_of_a_repository_without_the_mods_branch_is_an_error(self):
        with self.assertRaises(mu.UpdateError):
            self.check(FakeHttp(refs={"main": SHA1}))
        self.assertEqual(mu.read_state(self.root), {})

    def test_what_an_earlier_version_kept_about_etags_is_dropped(self):
        mu.write_state(self.root, {"sha": SHA1, "etag": '"old"', "loader_etag": '"older"', "checked_at": 0})
        self.check(FakeHttp(sha=SHA1))
        self.assertEqual(mu.read_state(self.root), {"sha": SHA1, "checked_at": self.now})

    def test_no_network_is_an_error_and_records_nothing(self):
        with self.assertRaises(mu.UpdateError):
            self.check(FakeHttp(fail="no connection"))
        self.assertEqual(mu.read_state(self.root), {})
        self.assertFalse((self.root / mu.STATE_FILE).exists())                           # (not even an empty file)

    def test_a_bad_zip_is_an_error(self):
        with self.assertRaises(mu.UpdateError):
            self.check(FakeHttp(zip_bytes=b"not a zip"))

    def test_a_mod_that_is_only_gone_from_the_zip_is_not_an_update(self):
        self.catalogue_has({"old_mod": {"mod.json": "{}", "x.py": "1"}})
        self.assertIsNone(self.check(FakeHttp(zip_bytes=make_zip(MODS_V1))))

    def test_readmes_tests_and_screenshots_do_not_make_an_update(self):
        mods = {name: dict(files) for name, files in MODS_V1.items()}
        mods["alpha"]["README.md"] = "# a different readme"
        mods["alpha"]["test_alpha.py"] = "assert True"
        mods["beta"]["screenshots/new.png"] = b"png"
        self.assertIsNone(self.check(FakeHttp(zip_bytes=make_zip(mods))))

    def test_a_catalogue_checked_out_on_windows_is_not_out_of_date_just_for_its_line_endings(self):
        crlf = {name: {path: content.replace("\n", "\r\n") if isinstance(content, str) else content for path, content in files.items()}
                for name, files in MODS_V1.items()}
        shutil.rmtree(self.catalogue)
        self.catalogue.mkdir()
        write_mods(self.catalogue, crlf)
        self.assertEqual((self.catalogue / "alpha" / "alpha.py").read_bytes(), b"x = 1\r\n")
        self.assertIsNone(self.check(FakeHttp(zip_bytes=make_zip(MODS_V1))))

    def test_a_mod_in_the_catalogue_that_the_zip_has_changed_is_updated_even_if_only_one_file_differs(self):
        mods = {name: dict(files) for name, files in MODS_V1.items()}
        mods["beta"]["extra.json"] = "[1]"
        update = self.check(FakeHttp(sha=SHA2, zip_bytes=make_zip(mods, SHA2)))
        self.assertEqual(update.updated, ["beta"])

    def test_a_fork_is_followed_when_named(self):
        with mock.patch.dict(os.environ, {mu.ENV_REPO: "someone/fork@testing"}):
            http = FakeHttp(refs={"testing": SHA2, mu.DEFAULT_BRANCH: SHA1}, zip_bytes=make_zip(MODS_V2, SHA2))
            update = self.check(http)
        self.assertEqual(http.calls[0][0], "https://github.com/someone/fork.git/info/refs?service=git-upload-pack")
        self.assertIn("someone/fork/zip/" + SHA2, http.calls[1][0])                    # (the named branch's commit, not the default branch's)
        self.assertEqual((update.sha, update.repo), (SHA2, "someone/fork"))


class AvailableTests(Base):
    def test_a_catalogue_with_mods_is_a_place_to_update(self):
        self.assertFalse(mu.available(self.root))
        self.catalogue_has(MODS_V1)
        self.assertTrue(mu.available(self.root))

    def test_no_catalogue_means_no_update_check_as_in_the_developer_folder(self):
        self.assertFalse(mu.available(self.root.parent / "dev"))


# ── applying ─────────────────────────────────────────────────────────────────

class ApplyTests(Base):
    def setUp(self):
        super().setUp()
        self.catalogue_has(MODS_V1)
        self.update = mu.Update(backend="zip", sha=SHA2, updated=["alpha"], added=["gamma"], repo="o/r",
                                mods=mu.read_mods(make_zip(MODS_V2, SHA2)))

    def test_a_used_mod_among_the_updated_needs_a_restart(self):
        self.install("alpha")
        ok, message, restart = mu.apply(self.root, self.update, now=lambda: 5.0)
        self.assertTrue(ok)
        self.assertTrue(restart)
        self.assertIn("Restarting", message)
        self.assertEqual((self.catalogue / "alpha" / "alpha.py").read_text(encoding="utf-8"), "x = 2\n")
        self.assertTrue((self.catalogue / "gamma").is_dir())
        self.assertEqual(mu.read_state(self.root), {"sha": SHA2, "checked_at": 5.0})

    def test_mods_that_are_not_used_need_no_restart(self):
        self.install("beta")
        ok, message, restart = mu.apply(self.root, self.update)
        self.assertTrue(ok)
        self.assertFalse(restart)
        self.assertIn("not ones you use", message)

    def test_a_new_mod_alone_needs_no_restart(self):
        self.install("alpha")
        update = mu.Update(backend="zip", sha=SHA2, added=["gamma"], mods=self.update.mods)
        self.assertEqual(mu.apply(self.root, update)[2], False)

    def test_a_failure_is_told_changes_nothing_and_records_nothing(self):
        with mock.patch.object(mu, "apply_zip", side_effect=OSError("disk full")), self.assertLogs("mod_updates", level="WARNING"):
            ok, message, restart = mu.apply(self.root, self.update)
        self.assertFalse(ok)
        self.assertIn("disk full", message)
        self.assertFalse(restart)
        self.assertEqual(mu.read_state(self.root), {})

    def test_an_update_that_cannot_be_applied_here_says_why_and_changes_nothing(self):
        blocked = mu.Update(backend="git", sha=SHA2, updated=["alpha"], blocked="mods_available has local changes")
        self.assertEqual(mu.apply(self.root, blocked), (False, "mods_available has local changes", False))

    def test_the_mods_folder_is_never_touched(self):
        self.install("alpha")
        before = mod_digest.digests(self.root / "mods")
        mu.apply(self.root, self.update)
        self.assertEqual(mod_digest.digests(self.root / "mods"), before)


class SummaryTests(unittest.TestCase):
    def test_it_names_the_updated_and_the_new_mods(self):
        self.assertEqual(mu.summary(mu.Update("zip", SHA1, updated=["a", "b"], added=["c"])), "updated: a, b   new: c")
        self.assertEqual(mu.summary(mu.Update("zip", SHA1, added=["c"])), "new: c")

    def test_a_long_list_is_cut_with_a_count(self):
        update = mu.Update("zip", SHA1, updated=[f"m{i}" for i in range(7)])
        self.assertEqual(mu.summary(update), "updated: m0, m1, m2, m3 +3 more")


# ── the bar on the login screen ──────────────────────────────────────────────

class FakeFont:
    """Measures like the game's own small font (consolas 14), so what fits in a test fits on the screen."""

    def __init__(self, log):
        self.log = log
        self.real = pygame.font.SysFont("consolas", 14)

    def render(self, text, antialias, colour):
        self.log.append(text)
        return pygame.Surface((max(1, self.real.size(text)[0]), self.real.get_height()), pygame.SRCALPHA)

    def size(self, text):
        return self.real.size(text)

    def get_height(self):
        return self.real.get_height()


def click(pos, button=1, kind=pygame.MOUSEBUTTONDOWN):
    return pygame.event.Event(kind, pos=pos, button=button)


class BarBase(Base):
    def setUp(self):
        super().setUp()
        self.catalogue_has(MODS_V1)
        patches = [mock.patch.object(mu, "_root", lambda: self.root)]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.win = SimpleNamespace()
        self.text: list = []
        self.fonts = (FakeFont(self.text), FakeFont(self.text), FakeFont(self.text))
        self.screen = pygame.Surface(WINDOW)
        self.restarts: list = []
        self.quits: list = []
        self.restart_ok = True
        for patcher in (mock.patch.object(mu.relaunch, "restart_after_exit", lambda **kw: self.restarts.append(kw) or self.restart_ok),
                        mock.patch.object(mu.pygame.event, "post", lambda e: self.quits.append(e))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def update(self, **kwargs):
        defaults = dict(backend="zip", sha=SHA2, updated=["alpha"], added=["gamma"], repo="o/r", mods=self.zip_mods())
        defaults.update(kwargs)
        return mu.Update(**defaults)

    def zip_mods(self):
        return mu.read_mods(make_zip(MODS_V2, SHA2))

    def draw(self, mouse=(0, 0)):
        self.text.clear()
        with mock.patch("pygame.mouse.get_pos", return_value=mouse):
            mu.draw_bar(self.win, self.screen, self.fonts)
        return mu._state(self.win)

    def wait(self, key):
        holder = mu._state(self.win)[key]
        deadline = time.monotonic() + 10
        while not holder["done"] and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(holder["done"])

    def offered(self, update=None):
        """Draw until the check (stubbed to find *update*) has finished; the bar is up."""
        with mock.patch.object(mu, "check", return_value=update or self.update()):
            self.draw()
            self.wait("check")
        return self.draw()


class BarTests(BarBase):
    def test_with_a_newer_version_the_bar_names_what_changes_and_where_it_comes_from(self):
        self.offered()
        for wanted in ("Mod updates available:  updated: alpha   new: gamma", "UPDATE MODS", "LATER"):
            self.assertIn(wanted, self.text)
        self.assertTrue(any("github.com/o/r" in line and "nothing changes until you press UPDATE MODS" in line for line in self.text), self.text)

    def test_the_whole_caption_fits_at_the_normal_window_size(self):
        self.offered(self.update(updated=["quick_bars", "ui_layouts"], added=[]))
        caption = next(line for line in self.text if "nothing changes" in line)
        self.assertFalse(caption.endswith("..."))                                   # (the sentence that matters is never cut off)

    def test_nothing_is_changed_by_showing_it(self):
        before = mod_digest.digests(self.catalogue)
        self.offered()
        self.assertEqual(mod_digest.digests(self.catalogue), before)
        self.assertEqual(self.restarts, [])
        self.assertEqual(mu.read_state(self.root), {})

    def test_it_takes_the_top_of_the_window_and_tells_other_mods_how_much(self):
        state = self.offered()
        self.assertEqual(self.win._top_banner_h, mu.bar_rect(*WINDOW).bottom + 4)
        self.assertEqual(sorted(state["rects"]), ["later", "update"])
        for rect in state["rects"].values():
            self.assertTrue(mu.bar_rect(*WINDOW).contains(rect))
        self.assertFalse(state["rects"]["update"].colliderect(state["rects"]["later"]))

    def test_nothing_is_shown_when_there_is_nothing_new(self):
        with mock.patch.object(mu, "check", return_value=None):
            self.draw()
            self.wait("check")
            state = self.draw()
        self.assertEqual(state["phase"], "idle")
        self.assertEqual(self.text, [])
        self.assertEqual(self.win._top_banner_h, 0)

    def test_nothing_is_shown_when_there_is_no_network_or_the_check_fails(self):
        for failure in (mu.UpdateError("no connection"), RuntimeError("boom")):
            self.win = SimpleNamespace()
            with mock.patch.object(mu, "check", side_effect=failure):
                self.draw()
                self.wait("check")
                state = self.draw()
            self.assertEqual(state["phase"], "idle", failure)
            self.assertEqual(self.text, [])

    def test_there_is_no_check_without_a_catalogue(self):
        shutil.rmtree(self.catalogue)
        with mock.patch.object(mu, "check") as check:
            state = self.draw()
            self.draw()
        check.assert_not_called()
        self.assertEqual(state["phase"], "off")
        self.assertEqual(self.text, [])

    def test_a_slow_check_never_holds_up_the_drawing(self):
        gate = threading.Event()

        def slow(root, **kwargs):
            gate.wait(10)
            return None

        with mock.patch.object(mu, "check", slow):
            started = time.monotonic()
            state = self.draw()
            self.assertLess(time.monotonic() - started, 3.0)
            self.assertEqual(state["phase"], "checking")
            self.assertEqual(self.text, [])
            gate.set()
            self.wait("check")
        self.assertEqual(self.draw()["phase"], "idle")

    def test_the_check_is_made_once_and_off_the_drawing_thread(self):
        with mock.patch.object(mu, "check", return_value=None) as check:
            self.draw()
            self.draw()
            self.draw()
            self.wait("check")
        self.assertEqual(check.call_count, 1)

    def test_the_bar_is_narrower_in_a_narrow_window_and_stays_inside_it(self):
        for width in (1200, 800, 400, 230):
            rect = mu.bar_rect(width, 720)
            self.assertTrue(pygame.Rect(0, 0, width, 720).contains(rect) or width < 224, width)
            self.assertGreaterEqual(rect.w, 200)

    def test_a_long_list_shows_fewer_names_with_a_count_rather_than_being_cut_off(self):
        self.offered(self.update(updated=[f"a_rather_long_mod_name_{i}" for i in range(4)]))
        (line,) = [line for line in self.text if line.startswith("Mod updates available")]
        self.assertLessEqual(self.fonts[2].size(line)[0], mu.bar_rect(*WINDOW).w)
        self.assertFalse(line.endswith("..."), line)
        self.assertRegex(line, r"updated: a_rather_long_mod_name_0.* \+\d more")                # (the counted rest, not a cut name)

    def test_names_that_cannot_fit_even_one_at_a_time_give_way_to_counts(self):
        self.offered(self.update(updated=[f"{'x' * 60}{i}" for i in range(5)], added=[f"{'y' * 60}{i}" for i in range(4)]))
        self.assertIn("Mod updates available:  updated: 5   new: 4", self.text)

    def test_text_that_cannot_fit_even_as_counts_is_cut_to_fit(self):
        self.screen = pygame.Surface((300, 400))                                       # (a very narrow window)
        self.offered(self.update(updated=["x" * 400]))
        (line,) = [line for line in self.text if line.startswith("Mod")]
        self.assertLessEqual(self.fonts[2].size(line)[0], mu.bar_rect(300, 400).w)
        self.assertTrue(line.endswith("..."), line)                                    # (the bar's last resort: cut, with dots)


class ClickTests(BarBase):
    def setUp(self):
        super().setUp()
        self.state = self.offered()

    def press(self, key, **kwargs):
        return mu.handle_click(self.win, click(self.state["rects"][key].center, **kwargs))

    def test_later_hides_the_bar_until_the_next_start(self):
        self.assertTrue(self.press("later"))
        state = self.draw()
        self.assertEqual(state["phase"], "idle")
        self.assertEqual(self.text, [])
        self.assertEqual(self.win._top_banner_h, 0)
        self.assertEqual(mu.read_state(self.root), {})
        self.assertEqual(self.restarts, [])

    def test_update_applies_on_a_thread_and_the_bar_says_so(self):
        gate = threading.Event()

        def slow_apply(root, update, **kwargs):
            gate.wait(10)
            return True, "Updated.", False

        with mock.patch.object(mu, "apply", slow_apply):
            self.assertTrue(self.press("update"))
            state = self.draw()
            self.assertEqual(state["phase"], "applying")
            self.assertIn("Updating the mods...", self.text)
            self.assertEqual(state["rects"], {})                                    # (no buttons while it works)
            gate.set()
            self.wait("apply")
            self.draw()

    def test_a_click_that_is_not_on_a_button_or_not_the_left_button_does_nothing(self):
        for event in (click((5, 5)), click(self.state["rects"]["update"].center, button=3),
                      click(self.state["rects"]["update"].center, kind=pygame.MOUSEBUTTONUP),
                      pygame.event.Event(pygame.MOUSEMOTION, pos=self.state["rects"]["update"].center, rel=(0, 0), buttons=(0, 0, 0))):
            self.assertFalse(mu.handle_click(self.win, event))
        self.assertEqual(mu._state(self.win)["phase"], "available")

    def test_a_second_click_while_it_updates_does_not_start_another_update(self):
        button = self.state["rects"]["update"].center
        with mock.patch.object(mu, "apply", return_value=(True, "Updated.", False)) as apply:
            self.press("update")
            self.wait("apply")
            self.assertFalse(mu.handle_click(self.win, click(button)))                  # (the buttons are gone as soon as one is pressed)
        self.assertEqual(apply.call_count, 1)

    def finish(self, result):
        with mock.patch.object(mu, "apply", return_value=result):
            self.press("update")
            self.wait("apply")
            return self.draw()

    def test_an_update_that_needs_a_restart_restarts_the_client(self):
        state = self.finish((True, "Updated. Restarting to use the new versions...", True))
        self.assertEqual(state["phase"], "restarting")
        self.assertEqual(self.restarts, [{"root": self.root}])
        self.assertEqual([e.type for e in self.quits], [pygame.QUIT])
        self.assertIn("Restarting with the new mods...", self.text)

    def test_an_update_that_cannot_restart_says_so_and_keeps_the_game_open(self):
        self.restart_ok = False
        state = self.finish((True, "Updated.", True))
        self.assertEqual(state["phase"], "done")
        self.assertEqual(self.quits, [])
        self.assertIn("Updated. Close the game and start it again to use the new versions.", self.text)

    def test_an_update_that_needs_no_restart_says_so_for_a_while_and_then_goes(self):
        state = self.finish((True, "Updated. The changed mods are not ones you use: CHANGE MODS lists them.", False))
        self.assertEqual(state["phase"], "done")
        self.assertEqual(self.quits, [])
        self.assertTrue(any("not ones you use" in line for line in self.text))
        with mock.patch.object(mu.time, "monotonic", return_value=time.monotonic() + mu.SHOW_DONE_S + 1):
            state = self.draw()
        self.assertEqual(state["phase"], "idle")
        self.assertEqual(self.text, [])

    def test_a_failed_update_says_why_and_leaves_the_game_alone(self):
        state = self.finish((False, "Could not update the mods: disk full", False))
        self.assertEqual(state["phase"], "failed")
        self.assertIn("Could not update the mods: disk full", self.text)
        self.assertEqual(self.quits, [])

    def test_an_update_that_blows_up_is_a_failure_not_a_crash(self):
        with mock.patch.object(mu, "apply", side_effect=RuntimeError("boom")), self.assertLogs("mod_updates", level="ERROR"):
            self.press("update")
            self.wait("apply")
            state = self.draw()
        self.assertEqual(state["phase"], "failed")


class BlockedBarTests(BarBase):
    def test_an_update_that_cannot_be_applied_is_told_with_an_ok_and_no_update_button(self):
        state = self.offered(self.update(backend="git", blocked="mods_available has local changes: update it by hand with git pull"))
        self.assertEqual(sorted(state["rects"]), ["later"])
        self.assertIn("OK", self.text)
        self.assertNotIn("UPDATE MODS", self.text)
        self.assertTrue(any("local changes" in line for line in self.text))
        self.assertTrue(mu.handle_click(self.win, click(state["rects"]["later"].center)))
        self.assertEqual(self.draw()["phase"], "idle")


class SwitchTests(BarBase):
    def test_the_environment_switch_turns_the_whole_thing_off(self):
        with mock.patch.dict(os.environ, {mu.ENV_OFF: "1"}), mock.patch.object(mu, "check") as check:
            mu.after_draw(self.win, self.screen, self.fonts)
            mu.before_event(self.win, click((5, 5)))
        check.assert_not_called()
        self.assertIsNone(self.win.__dict__.get("_mu_state"))

    def test_an_error_switches_it_off_for_the_session(self):
        with mock.patch.object(mu, "draw_bar", side_effect=RuntimeError("boom")), self.assertLogs("mod_updates", level="ERROR"):
            mu.after_draw(self.win, self.screen, self.fonts)
        self.assertFalse(mu.enabled())
        with mock.patch.object(mu, "handle_click", side_effect=RuntimeError("again")):
            mu.before_event(self.win, click((5, 5)))                                # (switched off: not even called)


class HookTests(unittest.TestCase):
    """On the game's real login screen."""

    @classmethod
    def setUpClass(cls):
        import login

        class Hooked(login.LoginScreen):
            pass

        cls.Hooked = Hooked
        cls.problems = mu.install(Hooked)

    def setUp(self):
        mu.reset_for_tests()
        self.addCleanup(mu.reset_for_tests)
        self.addCleanup(finish_threads)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "pkg"
        (self.root / "mods_available" / "alpha").mkdir(parents=True)
        (self.root / "mods_available" / "alpha" / "mod.json").write_text("{}", encoding="utf-8")
        (self.root / "mods").mkdir()
        patcher = mock.patch.object(mu, "_root", lambda: self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.screen_obj = self.Hooked(lambda _line: None, lambda _line: None)
        self.screen_obj._screen = "login"
        self.surface = pygame.Surface(WINDOW)
        self.fonts = (pygame.font.Font(None, 28), pygame.font.Font(None, 18), pygame.font.Font(None, 14))

    def draw(self):
        with mock.patch.object(self.Hooked, "_draw_background", lambda self_, screen: None):
            self.screen_obj.draw(self.surface, self.fonts)

    def wait(self):
        holder = self.screen_obj._mu_state["check"]
        deadline = time.monotonic() + 10
        while not holder["done"] and time.monotonic() < deadline:
            time.sleep(0.01)

    def test_installing_reports_no_problem_and_is_idempotent(self):
        self.assertEqual(self.problems, [])
        self.assertEqual(mu.install(self.Hooked), [])

    def test_the_real_login_screen_shows_the_bar_and_hands_it_the_click(self):
        update = mu.Update(backend="zip", sha=SHA2, updated=["alpha"], repo="o/r")
        with mock.patch.object(mu, "check", return_value=update):
            self.draw()
            self.wait()
            self.draw()
        rect = self.screen_obj._mu_state["rects"]["later"]
        self.assertTrue(pygame.Rect(0, 0, *WINDOW).contains(rect))
        self.screen_obj.handle_event(click(rect.center), *WINDOW)
        self.assertEqual(self.screen_obj._mu_state["phase"], "idle")

    def test_the_card_still_works_as_before(self):
        self.draw()
        self.wait()
        self.draw()
        field = next(iter(self.screen_obj._field_rects.items()))
        self.screen_obj._field_focus = None
        self.screen_obj.handle_event(click(field[1].center), *WINDOW)
        self.assertEqual(self.screen_obj._field_focus, field[0])

    def test_a_login_screen_with_no_catalogue_is_left_alone(self):
        shutil.rmtree(self.root / "mods_available")
        self.draw()
        self.assertEqual(self.screen_obj._mu_state["phase"], "off")


class GamePatchTests(unittest.TestCase):
    def test_the_patched_client_installs_the_hooks_after_the_login_import(self):
        source = modloader.game_source("Client.py")
        self.assertIn("mod_updates.install(LoginScreen)", source)
        self.assertLess(source.index("from login import LoginScreen"), source.index("mod_updates.install(LoginScreen)"))

    def test_the_login_methods_the_hooks_go_on_take_what_the_hooks_expect(self):
        import inspect
        import login
        self.assertEqual(list(inspect.signature(login.LoginScreen.draw).parameters), ["self", "screen", "fonts"])
        self.assertEqual(list(inspect.signature(login.LoginScreen.handle_event).parameters), ["self", "ev", "ww", "wh"])


if __name__ == "__main__":
    unittest.main()

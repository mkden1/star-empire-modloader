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


class FakeHttp:
    """What ``mod_updates.http_get`` is, answering from a table."""

    def __init__(self, sha=SHA1, etag='"e1"', zip_bytes=b"", sha_status=200, fail=None):
        self.sha, self.etag, self.zip_bytes, self.sha_status, self.fail = sha, etag, zip_bytes, sha_status, fail
        self.calls: list = []

    def __call__(self, url, headers=None, timeout=12.0, max_bytes=0):
        self.calls.append((url, dict(headers or {})))
        if self.fail:
            raise mu.UpdateError(self.fail)
        if "api.github.com" in url:
            if self.sha_status == 304:
                return 304, {}, b""
            return self.sha_status, {"ETag": self.etag}, self.sha.encode()
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

    def test_not_modified_is_an_answer(self):
        error = urllib.error.HTTPError("https://x", 304, "Not Modified", {"ETag": '"x"'}, None)
        with mock.patch.object(urllib.request, "urlopen", side_effect=error):
            self.assertEqual(mu.http_get("https://example.test/a")[0], 304)

    def test_other_errors_and_no_connection_are_update_errors(self):
        for failure in (urllib.error.HTTPError("https://x", 404, "no", {}, None), urllib.error.URLError("offline"), OSError("reset"),
                        TimeoutError("slow")):
            with mock.patch.object(urllib.request, "urlopen", side_effect=failure), self.assertRaises(mu.UpdateError):
                mu.http_get("https://example.test/a")


class LatestShaTests(unittest.TestCase):
    def test_it_asks_for_the_branchs_sha_only(self):
        http = FakeHttp(sha=SHA2, etag='"tag"')
        self.assertEqual(mu.latest_sha("o/r", "mods_available", None, http), (SHA2, '"tag"'))
        url, headers = http.calls[0]
        self.assertEqual(url, "https://api.github.com/repos/o/r/commits/mods_available")
        self.assertEqual(headers, {"Accept": "application/vnd.github.sha"})

    def test_it_sends_the_etag_it_has_and_a_304_means_nothing_changed(self):
        http = FakeHttp(sha_status=304)
        self.assertEqual(mu.latest_sha("o/r", "b", '"old"', http), (None, '"old"'))
        self.assertEqual(http.calls[0][1]["If-None-Match"], '"old"')

    def test_an_answer_that_is_not_a_sha_is_refused(self):
        for body in ("", "<html>rate limited</html>", "abc", "Z" * 40, "a" * 41):
            http = FakeHttp(sha=body)
            with self.assertRaises(mu.UpdateError, msg=body):
                mu.latest_sha("o/r", "b", None, http)

    def test_a_status_other_than_ok_is_refused(self):
        with self.assertRaises(mu.UpdateError):
            mu.latest_sha("o/r", "b", None, FakeHttp(sha_status=202))

    def test_the_etag_header_is_found_in_any_case(self):
        def http(url, headers=None, timeout=0, max_bytes=0):
            return 200, {"etag": '"low"'}, SHA1.encode()

        self.assertEqual(mu.latest_sha("o/r", "b", None, http), (SHA1, '"low"'))


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
        self.assertTrue(mu.write_state(self.root, {"sha": SHA1, "etag": '"e"'}))
        self.assertEqual(mu.read_state(self.root), {"sha": SHA1, "etag": '"e"'})
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
        state = mu.read_state(self.root)
        self.assertEqual((state["sha"], state["etag"], state["checked_at"]), (SHA1, '"e1"', self.now))

    def test_a_newer_version_is_offered_with_what_it_changes(self):
        http = FakeHttp(sha=SHA2, zip_bytes=make_zip(MODS_V2, SHA2))
        update = self.check(http)
        self.assertEqual((update.updated, update.added, update.removed), (["alpha"], ["gamma"], []))
        self.assertEqual((update.backend, update.sha, update.etag, update.repo), ("zip", SHA2, '"e1"', "mkden1/star-empire-modloader"))
        self.assertEqual(sorted(update.mods), ["alpha", "beta", "gamma"])

    def test_nothing_is_recorded_for_an_offer_so_it_is_made_again_at_the_next_start(self):
        self.check(FakeHttp(sha=SHA2, zip_bytes=make_zip(MODS_V2, SHA2)))
        self.assertEqual(mu.read_state(self.root), {})
        later = self.check(FakeHttp(sha=SHA2, zip_bytes=make_zip(MODS_V2, SHA2)))
        self.assertIsNotNone(later)

    def test_a_check_just_made_is_not_made_again_for_a_while(self):
        mu.write_state(self.root, {"sha": SHA1, "etag": '"e1"', "checked_at": self.now - 60})
        http = FakeHttp(sha=SHA2, zip_bytes=make_zip(MODS_V2, SHA2))
        self.assertIsNone(self.check(http))
        self.assertEqual(http.calls, [])
        self.now += mu.CHECK_EVERY_S
        self.assertIsNotNone(self.check(http))

    def test_a_forced_check_ignores_the_wait(self):
        mu.write_state(self.root, {"sha": SHA1, "etag": '"e1"', "checked_at": self.now - 1})
        http = FakeHttp(sha=SHA2, zip_bytes=make_zip(MODS_V2, SHA2))
        self.assertIsNotNone(self.check(http, force=True))

    def test_the_known_version_is_asked_about_with_its_etag_and_not_downloaded_again(self):
        mu.write_state(self.root, {"sha": SHA1, "etag": '"e1"', "checked_at": 0})
        http = FakeHttp(sha_status=304)
        self.assertIsNone(self.check(http))
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(http.calls[0][1]["If-None-Match"], '"e1"')
        self.assertEqual(mu.read_state(self.root)["checked_at"], self.now)

    def test_the_same_sha_with_a_new_etag_is_not_downloaded(self):
        mu.write_state(self.root, {"sha": SHA1, "etag": '"old"', "checked_at": 0})
        http = FakeHttp(sha=SHA1, etag='"new"')
        self.assertIsNone(self.check(http))
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(mu.read_state(self.root)["etag"], '"new"')

    def test_an_etag_is_only_sent_with_a_version_to_go_with_it(self):
        mu.write_state(self.root, {"etag": '"orphan"', "checked_at": 0})
        http = FakeHttp(zip_bytes=make_zip(MODS_V1))
        self.check(http)
        self.assertNotIn("If-None-Match", http.calls[0][1])

    def test_no_network_is_an_error_and_records_nothing(self):
        with self.assertRaises(mu.UpdateError):
            self.check(FakeHttp(fail="no connection"))
        self.assertEqual(mu.read_state(self.root), {})

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
            http = FakeHttp(sha=SHA2, zip_bytes=make_zip(MODS_V2, SHA2))
            self.check(http)
        self.assertIn("someone/fork/commits/testing", http.calls[0][0])
        self.assertIn("someone/fork/zip/", http.calls[1][0])


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
        self.update = mu.Update(backend="zip", sha=SHA2, updated=["alpha"], added=["gamma"], etag='"e2"', repo="o/r",
                                mods=mu.read_mods(make_zip(MODS_V2, SHA2)))

    def test_a_used_mod_among_the_updated_needs_a_restart(self):
        self.install("alpha")
        ok, message, restart = mu.apply(self.root, self.update, now=lambda: 5.0)
        self.assertTrue(ok)
        self.assertTrue(restart)
        self.assertIn("Restarting", message)
        self.assertEqual((self.catalogue / "alpha" / "alpha.py").read_text(encoding="utf-8"), "x = 2\n")
        self.assertTrue((self.catalogue / "gamma").is_dir())
        self.assertEqual(mu.read_state(self.root), {"sha": SHA2, "etag": '"e2"', "checked_at": 5.0})

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
        defaults = dict(backend="zip", sha=SHA2, updated=["alpha"], added=["gamma"], etag='"e"', repo="o/r", mods=self.zip_mods())
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

    def test_long_text_is_cut_to_fit(self):
        self.offered(self.update(updated=[f"a_rather_long_mod_name_{i}" for i in range(4)]))
        widest = max(self.fonts[2].size(line)[0] for line in self.text if line.startswith("Mod updates available"))
        self.assertLessEqual(widest, mu.bar_rect(*WINDOW).w)
        self.assertTrue(any(line.endswith("...") for line in self.text))


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

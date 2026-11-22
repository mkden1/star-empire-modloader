"""mod_updates.py - asks GitHub whether the mods (and the launcher) have newer versions, and updates them when the player says yes.

At the first frame of the login screen a background thread asks whether the repository's ``mods_available`` branch has moved. If it
has, the mods that would change are worked out and the login screen shows a bar:

    Mod updates available:  updated: quick_bars, ui_layouts   new: foo          [UPDATE MODS] [LATER]

NOTHING is changed until UPDATE MODS is pressed; LATER hides the bar until the next start. Updating refreshes the catalogue
(``mods_available/``); the launcher's own start-up refresh then copies what changed into ``mods/``, so when a mod the player
uses is among them the client restarts (relaunch.py) to pick it up. An older launcher has no such refresh: then the new versions
are copied into ``mods/`` here (modctl.update), so the restart really does use them.

The launcher itself (the files in the package's ``modloader/`` folder: the loader, the picker, modctl, the launch file) is checked
the same way against the repository's ``modloader`` branch, which also holds the bundled Python runtime (tens of MB), so there
is no zip: the folder's file list comes from the contents API for that exact commit (the only use of the REST API, and only when
the branch has moved), only files whose content differs are
downloaded (``raw.githubusercontent.com``, each checked against the hash the API listed), every ``.py`` must compile before
anything is touched, and the files are swapped in together (the old ones kept in ``modloader/.update_old/``, all put back if one
swap fails). The bar then reads ``launcher: modctl.py ...`` and the client always restarts. A package that is a git clone is left
to git.

Two ways to get the files, chosen by what ``mods_available/`` is:

  * a plain folder (the stand-alone package): over HTTPS, no git and no account needed (the repository is public): the latest
    commit of each branch from ``github.com/<repo>.git/info/refs`` (what ``git clone`` starts with: a few hundred bytes, ONE request
    for every branch, and no request budget: the REST API allows an address only 60 anonymous requests an hour, which other tools and
    everyone behind the same router also use up), and only when it is new the zip of exactly that commit from ``codeload.github.com``. What the zip holds is compared with the catalogue folder by folder (mod_digest.py), so the bar
    names the mods that really differ. Paths in the zip are checked (no ``..``, no absolute paths), its size is capped, and only
    folders with a ``mod.json`` are taken; every mod is swapped in beside the old one first and put back if anything fails.
  * a git clone (the developer, or whoever cloned it): ``git fetch`` and a fast-forward, with prompts switched off. A clone with
    local changes, or one that cannot fast-forward, is only reported, never touched.

Why ask first: a mod is Python that runs inside the game, next to the saved logins. Nothing here runs or installs code by itself,
the bar says where it comes from, and a start with no network, or a check that fails, shows nothing at all. A check is made at
most every 20 minutes (a start that finds everything up to date), and ``STAR_EMPIRE_NO_MOD_UPDATES=1`` switches the whole thing off
(``STAR_EMPIRE_NO_LOADER_UPDATES=1`` only the launcher part).

Every function the game calls here is total: an error switches the bar off for the session. A ``core`` mod that is older than this
one (no ``mod_digest.py`` / ``relaunch.py``: the folder was copied without it) switches the mod off at install time, with the
reason on the Mods page, instead of failing the game's start-up.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path

import pygame

try:                                 # (the core mod may be older than this one: it was copied without core's newer files; install() then does nothing)
    import mod_digest
except ImportError:
    mod_digest = None
try:
    import relaunch
except ImportError:
    relaunch = None

# what this mod calls in core's helpers, by module: an older core that lacks one of them is reported, it never crashes the game's start-up
CORE_NEEDS = {"mod_digest": ("counts", "diff", "digest_files", "digests", "worth_keeping"), "relaunch": ("restart_after_exit",)}

logger = logging.getLogger(__name__)

ENV_OFF = "STAR_EMPIRE_NO_MOD_UPDATES"
ENV_REPO = "STAR_EMPIRE_MOD_UPDATES_REPO"            # "owner/repo" or "owner/repo@branch": a fork, or a test
ENV_NO_LOADER = "STAR_EMPIRE_NO_LOADER_UPDATES"      # the launcher (modloader/) is left alone; the mods still update
ENV_LOADER_BRANCH = "STAR_EMPIRE_LOADER_BRANCH"      # the branch that holds the launcher's files (default: modloader)
DEFAULT_REPO = "mkden1/star-empire-modloader"
DEFAULT_BRANCH = "mods_available"
DEFAULT_LOADER_BRANCH = "modloader"
LOADER_DIR = "modloader"                              # the launcher's folder: in the package, and in the branch
LOADER_EXTENSIONS = (".py", ".bat", ".md")            # what is taken from it (not the bundled runtime, the licences, the .bat files at the root)
LOADER_MUST_HAVE = ("modloader.py", "start_modded_client.py")
MAX_LOADER_FILES = 40
MAX_LOADER_FILE_BYTES = 2 * 1024 * 1024
MAX_LOADER_BYTES = 6 * 1024 * 1024
LIST_BYTES = 512 * 1024
REFS_BYTES = 256 * 1024                               # the branch list of the repository (a few hundred bytes today)
MIN_BACKOFF_S = 60.0                                  # when GitHub's request limit is used up, the launcher is not asked about again for ...
MAX_BACKOFF_S = 2 * 3600.0                            # ... until it says it is lifted: at least this, at most that
STATE_FILE = "mod_updates.json"                        # next to the client: the last version seen, its ETag, when it was last checked
CHECK_EVERY_S = 20 * 60
TIMEOUT_S = 12.0
ZIP_TIMEOUT_S = 40.0
MAX_ZIP_BYTES = 40 * 1024 * 1024
MAX_UNZIPPED_BYTES = 120 * 1024 * 1024
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_FILES = 6000
USER_AGENT = "star-empire-mod-updater"
GIT_TIMEOUT_S = 40.0

BAR_H = 56
CARD_MAX_H = 510
SHOW_DONE_S = 10.0
_NO_WINDOW = 0x08000000

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_LOADER_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")      # a file name directly in modloader/: no slash, no leading dot
_PKT_LEN_RE = re.compile(rb"^[0-9a-f]{4}$")                              # the length prefix of a line of a git ref advertisement
_REF_LINE_RE = re.compile(rb"^([0-9a-f]{40}) refs/heads/([A-Za-z0-9_./-]+)$")

_failed = False


def core_problem() -> "str | None":
    """Why this mod cannot work with the core mod that is installed (None when it can): the core folder is older than this mod."""
    lacking: "list[str]" = []
    for name, module in (("mod_digest", mod_digest), ("relaunch", relaunch)):
        if module is None:
            lacking.append(f"{name}.py")
        else:
            lacking.extend(f"{name}.{need}" for need in CORE_NEEDS[name] if not hasattr(module, need))
    if not lacking:
        return None
    return "core is older than this mod (it lacks " + ", ".join(lacking) + "): update the core folder"


class UpdateError(Exception):
    """Something the check or the update could not do (network, a bad zip, a git refusal); the text is for the log and the bar."""


class RateLimited(UpdateError):
    """GitHub refused because the request limit of this address is used up; ``reset`` is when it is lifted (epoch seconds)."""

    def __init__(self, message: str, reset: float):
        super().__init__(message)
        self.reset = reset


def _switch_off(where: str) -> None:
    global _failed
    if not _failed:
        _failed = True
        logger.exception("mod_updates: switched off for this session (%s)", where)


def enabled() -> bool:
    return not _failed and not os.environ.get(ENV_OFF)


def reset_for_tests() -> None:
    global _failed
    _failed = False


def config() -> "tuple[str, str]":
    """The repository and branch to follow (``STAR_EMPIRE_MOD_UPDATES_REPO`` overrides; a malformed value is ignored)."""
    text = os.environ.get(ENV_REPO, "").strip()
    repo, _, branch = text.partition("@")
    if _REPO_RE.match(repo) and (not branch or _BRANCH_RE.match(branch)):
        return repo, branch or DEFAULT_BRANCH
    return DEFAULT_REPO, DEFAULT_BRANCH


def loader_branch() -> str:
    """The branch that holds the launcher's files (``STAR_EMPIRE_LOADER_BRANCH`` overrides; a malformed value is ignored)."""
    text = os.environ.get(ENV_LOADER_BRANCH, "").strip()
    return text if text and _BRANCH_RE.match(text) else DEFAULT_LOADER_BRANCH


def _root() -> Path:
    import modloader
    return Path(modloader.REPO_DIR)


# ── the network ──────────────────────────────────────────────────────────────

def _header(headers, name: str):
    for key, value in (headers or {}).items():
        if key.lower() == name.lower():
            return value
    return None


def _rate_limit_reset(headers, now=time.time) -> "float | None":
    """When GitHub says the request limit is used up: the time (epoch seconds) it is lifted, else None (an ordinary refusal)."""
    wait = _header(headers, "Retry-After")
    if isinstance(wait, str) and wait.strip().isdigit():
        return now() + int(wait.strip())
    reset = _header(headers, "X-RateLimit-Reset")
    if str(_header(headers, "X-RateLimit-Remaining")).strip() == "0" and isinstance(reset, str) and reset.strip().isdigit():
        return float(reset.strip())
    return None


def http_get(url: str, headers=None, timeout: float = TIMEOUT_S, max_bytes: int = MAX_ZIP_BYTES) -> "tuple[int, dict, bytes]":
    """One HTTPS GET: ``(status, headers, body)``; the body is refused beyond *max_bytes*. A refusal because GitHub's request limit
    is used up raises RateLimited (with the time it is lifted), any other one UpdateError."""
    if not url.startswith("https://"):
        raise UpdateError("only https is used")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise UpdateError("the download is larger than allowed")
            body = bytearray()
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                body += chunk
                if len(body) > max_bytes:
                    raise UpdateError("the download is larger than allowed")
            return response.status, dict(response.headers), bytes(body)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            reset = _rate_limit_reset(exc.headers)
            if reset is not None:
                raise RateLimited(f"GitHub's request limit is used up until {time.strftime('%H:%M', time.localtime(reset))}", reset) from None
        raise UpdateError(f"the server answered {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise UpdateError(f"no connection ({exc})") from None


def parse_refs(body: bytes) -> "dict[str, str]":
    """The branches in a git ref advertisement (what ``git clone`` is first told): ``{branch: commit SHA}``. The answer is a list of
    length-prefixed lines (four hex digits, the length including themselves; ``0000`` is a separator), each ``<sha> <ref name>``, the
    first with a ``\\0`` and the server's capabilities after the name. Anything that is not that is refused."""
    refs: "dict[str, str]" = {}
    position = 0
    while position < len(body):
        prefix = body[position:position + 4]
        if not _PKT_LEN_RE.match(prefix):
            raise UpdateError("the answer is not a list of branches")
        length = int(prefix, 16)
        if length == 0:                                     # (a separator)
            position += 4
            continue
        if length < 4 or position + length > len(body):
            raise UpdateError("the answer is not a list of branches")
        line = body[position + 4:position + length].split(b"\0", 1)[0].rstrip(b"\n")
        position += length
        found = _REF_LINE_RE.match(line)
        if found:
            refs[found.group(2).decode("ascii")] = found.group(1).decode("ascii")
    if not refs:
        raise UpdateError("the answer holds no branches")
    return refs


def latest_shas(repo: str, http=http_get) -> "dict[str, str]":
    """The newest commit of every branch of *repo*, in ONE request to ``github.com/<repo>.git/info/refs``: the same address
    ``git clone`` starts with. Unlike the REST API (60 anonymous requests an hour per address, shared by everyone behind it and
    by every other tool on the machine) it has no such budget, and it is only a few hundred bytes."""
    status, _answer, body = http(f"https://github.com/{repo}.git/info/refs?service=git-upload-pack", {}, TIMEOUT_S, REFS_BYTES)
    if status != 200:
        raise UpdateError("unexpected answer from GitHub")
    return parse_refs(body)


class Tips:
    """``tips(branch)``: that branch's newest commit. The refs are asked for once (a failure too is remembered), on first use, so a
    check that needs none (a git clone and no launcher) asks for none."""

    def __init__(self, repo: str, http=http_get):
        self.repo, self.http = repo, http
        self._refs: "dict[str, str] | None" = None
        self._error: "UpdateError | None" = None

    def __call__(self, branch: str) -> str:
        if self._refs is None and self._error is None:
            try:
                self._refs = latest_shas(self.repo, self.http)
            except UpdateError as exc:
                self._error = exc
        if self._error is not None:
            raise self._error
        sha = (self._refs or {}).get(branch)
        if sha is None:
            raise UpdateError(f"there is no branch {branch} in {self.repo}")
        return sha


def download_zip(repo: str, sha: str, http=http_get) -> bytes:
    """The zip of exactly commit *sha* (not 'the latest': nothing can change between the check and the download)."""
    if not _SHA_RE.match(sha):
        raise UpdateError("not a commit")
    status, _answer, body = http(f"https://codeload.github.com/{repo}/zip/{sha}", {}, ZIP_TIMEOUT_S, MAX_ZIP_BYTES)
    if status != 200:
        raise UpdateError("the download failed")
    return body


# ── the zip ──────────────────────────────────────────────────────────────────

def read_mods(data: bytes) -> "dict[str, dict[str, bytes]]":
    """The mods in a downloaded branch zip: ``{mod name: {path inside the mod with /: bytes}}``. Only folders that hold a
    ``mod.json`` (and whose name is not private); files and folders beside them are ignored. Raises UpdateError for a zip that
    is too big, has too many files, or has a path that could land outside the folder it is unpacked into."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UpdateError("the download is not a zip") from None
    infos = archive.infolist()
    if len(infos) > MAX_FILES:
        raise UpdateError("the zip holds too many files")
    if sum(info.file_size for info in infos) > MAX_UNZIPPED_BYTES:
        raise UpdateError("the zip is larger than allowed once unpacked")
    top = None
    mods: "dict[str, dict[str, bytes]]" = {}
    for info in infos:
        name = info.filename
        if name.startswith("/") or "\\" in name or re.match(r"^[A-Za-z]:", name):
            raise UpdateError("the zip has an unsafe path")
        parts = name.split("/")
        if any(part in (".", "..") for part in parts):
            raise UpdateError("the zip has an unsafe path")
        if top is None:
            top = parts[0]
        if parts[0] != top:
            raise UpdateError("the zip has more than one top folder")
        if info.is_dir() or len(parts) < 3 or not parts[-1]:                # (a file inside <top>/<mod>/...)
            continue
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode) or (mode and not stat.S_ISREG(mode)):
            continue
        mod = parts[1]
        if mod.startswith((".", "_")):
            continue
        if info.file_size > MAX_FILE_BYTES:
            raise UpdateError("a file in the zip is larger than allowed")
        mods.setdefault(mod, {})["/".join(parts[2:])] = archive.read(info)
    return {name: files for name, files in sorted(mods.items()) if "mod.json" in files}


def apply_zip(catalogue: Path, mods: "dict[str, dict[str, bytes]]", names) -> None:
    """Put these mods of the zip into the catalogue folder: each new copy is built beside the old one first, the old one moves to
    ``.update_old/`` (one generation is kept), and if anything fails what was already swapped is put back."""
    catalogue = Path(catalogue)
    staging = catalogue / ".update_new"
    backup_root = catalogue / ".update_old"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        for name in names:
            folder = staging / name
            for path, content in mods[name].items():
                if not all(mod_digest.worth_keeping(part) for part in path.split("/")):
                    continue                                       # (the tests and caches stay out of the catalogue)
                target = (folder / path).resolve()
                if folder.resolve() not in target.parents:
                    raise UpdateError("the zip has an unsafe path")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        shutil.rmtree(backup_root, ignore_errors=True)
        backup_root.mkdir()
        done: "list[tuple[str, bool]]" = []
        try:
            for name in names:
                target = catalogue / name
                had_old = target.exists()
                if had_old:
                    os.rename(target, backup_root / name)
                try:
                    os.rename(staging / name, target)
                except Exception:
                    if had_old:
                        os.rename(backup_root / name, target)
                    raise
                done.append((name, had_old))
        except Exception:
            for name, had_old in reversed(done):
                shutil.rmtree(catalogue / name, ignore_errors=True)
                if had_old:
                    os.rename(backup_root / name, catalogue / name)
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ── git (a catalogue that is a clone) ────────────────────────────────────────

def git_program() -> "str | None":
    return shutil.which("git")


def _git(git: str, catalogue: Path, args, runner=subprocess.run, timeout: float = GIT_TIMEOUT_S) -> "tuple[int, str]":
    """Run git in the clone with every prompt switched off (a credential window must never open from the login screen)."""
    environment = dict(os.environ)
    environment.update({"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never", "GIT_OPTIONAL_LOCKS": "0"})
    try:
        done = runner([git, "-c", "credential.helper=", "-C", str(catalogue), *args], capture_output=True, encoding="utf-8", errors="replace",
                      timeout=timeout, env=environment, creationflags=_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(f"git did not answer ({exc})") from None
    return done.returncode, (done.stdout or "").strip()


def _top_level(names) -> "set[str]":
    return {n for n in names if n and not n.startswith((".", "_"))}


def git_check(catalogue: Path, git: str, runner=subprocess.run) -> "Update | None":
    """Fetch, and say what a fast-forward would change. None when up to date; an Update with ``blocked`` set when it cannot be applied."""
    code, _ = _git(git, catalogue, ["fetch", "--quiet", "origin"], runner)
    if code != 0:
        raise UpdateError("git could not fetch")
    code, head = _git(git, catalogue, ["rev-parse", "HEAD"], runner)
    code2, upstream = _git(git, catalogue, ["rev-parse", "@{upstream}"], runner)
    if code != 0 or code2 != 0 or not _SHA_RE.match(head) or not _SHA_RE.match(upstream):
        raise UpdateError("the clone has no upstream")
    if head == upstream:
        return None
    code, _ = _git(git, catalogue, ["merge-base", "--is-ancestor", "HEAD", "@{upstream}"], runner)
    old_top = _top_level(_git(git, catalogue, ["ls-tree", "--name-only", "HEAD"], runner)[1].splitlines())
    new_top = _top_level(_git(git, catalogue, ["ls-tree", "--name-only", "@{upstream}"], runner)[1].splitlines())
    changed: "set[str]" = set()
    for line in _git(git, catalogue, ["diff", "--name-only", "HEAD", "@{upstream}"], runner)[1].splitlines():
        parts = line.strip().split("/")
        if len(parts) >= 2 and _top_level(parts[:1]) and all(mod_digest.counts(part) for part in parts[1:]):
            changed.add(parts[0])
    updated = sorted(name for name in changed if name in old_top and name in new_top)
    added = sorted(name for name in changed if name not in old_top)
    removed = sorted(old_top - new_top)
    update = Update(backend="git", sha=upstream, updated=updated, added=added, removed=removed)
    if not (updated or added):
        update.quiet = True                                  # (README, screenshots or tests moved: nothing the player needs to be asked)
    if code != 0:
        update.blocked = "mods_available cannot be fast-forwarded: update it by hand with git pull"
    elif _git(git, catalogue, ["status", "--porcelain", "--untracked-files=no"], runner)[1]:
        update.blocked = "mods_available has local changes: update it by hand with git pull"
    return update


def git_apply(catalogue: Path, git: str, runner=subprocess.run) -> None:
    code, _ = _git(git, catalogue, ["merge", "--ff-only", "@{upstream}"], runner)
    if code != 0:
        raise UpdateError("git could not fast-forward")


# ── the launcher (the package's modloader/ folder) ───────────────────────────

def loader_dir(root: Path) -> Path:
    return Path(root) / LOADER_DIR


def loader_available(root: Path) -> bool:
    """Is there a launcher to keep up to date? Only in the stand-alone package (``modloader/`` holds the loader and the launch file),
    not in a package that is a git clone (git updates that), not in the developer folder, and not when switched off."""
    root = Path(root)
    try:
        return (not os.environ.get(ENV_NO_LOADER) and not (root / ".git").exists()
                and all((loader_dir(root) / name).is_file() for name in LOADER_MUST_HAVE))
    except OSError:
        return False


def blob_sha(data: bytes) -> str:
    """The hash git gives a file's content (what the contents API lists), so a download can be checked against the listing."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _lf(data: bytes) -> bytes:
    """A text file with Windows line endings read as with Unix ones (a git checkout vs. what GitHub serves); binary is exact."""
    return data if b"\0" in data else data.replace(b"\r\n", b"\n")


def list_loader_files(repo: str, sha: str, http=http_get) -> "dict[str, tuple[int, str]]":
    """The launcher's files at exactly commit *sha*: ``{name: (size, blob sha)}``, from GitHub's contents API. Only plain files with a
    safe name and one of ``LOADER_EXTENSIONS``; refuses a listing that is too big or that lacks the loader or the launch file."""
    if not _SHA_RE.match(sha):
        raise UpdateError("not a commit")
    status, _answer, body = http(f"https://api.github.com/repos/{repo}/contents/{LOADER_DIR}?ref={sha}",
                                 {"Accept": "application/vnd.github+json"}, TIMEOUT_S, LIST_BYTES)
    if status != 200:
        raise UpdateError("unexpected answer from GitHub")
    try:
        entries = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise UpdateError("the launcher's file list is not readable") from None
    if not isinstance(entries, list) or len(entries) > MAX_LOADER_FILES:
        raise UpdateError("the launcher's file list is not what was expected")
    files: "dict[str, tuple[int, str]]" = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("type") != "file":
            continue
        name, size, blob = entry.get("name"), entry.get("size"), entry.get("sha")
        if not (isinstance(name, str) and _LOADER_NAME_RE.match(name) and name.lower().endswith(LOADER_EXTENSIONS)):
            continue
        if not (isinstance(size, int) and not isinstance(size, bool) and 0 <= size <= MAX_LOADER_FILE_BYTES):
            raise UpdateError("a launcher file is larger than allowed")
        if not (isinstance(blob, str) and _SHA_RE.match(blob)):
            raise UpdateError("the launcher's file list is not what was expected")
        files[name] = (size, blob)
    if sum(size for size, _ in files.values()) > MAX_LOADER_BYTES:
        raise UpdateError("the launcher is larger than allowed")
    if not all(name in files for name in LOADER_MUST_HAVE):
        raise UpdateError("the launcher's file list lacks " + " / ".join(LOADER_MUST_HAVE))
    return files


def download_loader_file(repo: str, sha: str, name: str, size: int, blob: str, http=http_get) -> bytes:
    """One launcher file of exactly commit *sha*, checked against the size and hash the listing gave."""
    if not (_SHA_RE.match(sha) and _LOADER_NAME_RE.match(name)):
        raise UpdateError("not a launcher file")
    status, _answer, body = http(f"https://raw.githubusercontent.com/{repo}/{sha}/{LOADER_DIR}/{name}", {}, TIMEOUT_S, MAX_LOADER_FILE_BYTES)
    if status != 200:
        raise UpdateError(f"the download of {name} failed")
    if len(body) != size or blob_sha(body) != blob:
        raise UpdateError(f"{name} is not what the listing says")
    return body


def check_loader(root: Path, state: dict, tip, http=http_get, now=time.time) -> "Update | None":
    """Do the launcher's files on GitHub differ from the ones in ``modloader/``? An Update that holds only the launcher part, or None.
    *tip* is ``Tips(repo)``: ``tip(branch)`` is the newest commit of a branch. A conclusion (the branch has not moved, or nothing
    really differs) is noted in *state* (the caller writes it). The file list is the one thing that has to come from GitHub's REST
    API, whose anonymous budget is small: when it says that is used up, the launcher is left alone until it is lifted."""
    if now() < float(state.get("loader_retry_after") or 0):
        return None
    repo = config()[0]
    sha = tip(loader_branch())
    if sha == state.get("loader_sha"):
        state.pop("loader_retry_after", None)
        return None
    try:
        listing = list_loader_files(repo, sha, http)
        folder = loader_dir(root)
        changed: "list[str]" = []
        added: "list[str]" = []
        files: "dict[str, bytes]" = {}
        for name, (size, blob) in sorted(listing.items()):
            exists = (folder / name).is_file()
            try:
                local = (folder / name).read_bytes() if exists else None
            except OSError:                                         # (there, but cannot be read: not known to be the same)
                local = None
            if local is not None and blob in (blob_sha(local), blob_sha(_lf(local))):
                continue                                            # (the same file, with or without Windows line endings)
            data = download_loader_file(repo, sha, name, size, blob, http)
            if local is not None and _lf(local) == _lf(data):
                continue
            files[name] = data
            (changed if exists else added).append(name)
    except RateLimited as exc:
        state["loader_retry_after"] = min(max(exc.reset, now() + MIN_BACKOFF_S), now() + MAX_BACKOFF_S)
        raise
    state.pop("loader_retry_after", None)
    if not files:
        state["loader_sha"] = sha
        return None
    return Update(backend="", sha="", repo=repo, loader_sha=sha, loader_updated=changed, loader_added=added, loader_files=files)


def apply_loader(folder: Path, files: "dict[str, bytes]") -> None:
    """Put these files into the launcher's folder. Every ``.py`` must compile first (a broken launcher could not start the game, and
    nothing in the game could repair it); the new copies are built beside the old ones, the old ones go to ``.update_old/`` (one
    generation is kept), and if a swap fails the ones already swapped are put back."""
    folder = Path(folder)
    for name, data in files.items():
        if not _LOADER_NAME_RE.match(name) or not name.lower().endswith(LOADER_EXTENSIONS):
            raise UpdateError(f"{name} is not a launcher file")
        if name.lower().endswith(".py"):
            try:
                compile(data, name, "exec", dont_inherit=True)
            except (SyntaxError, ValueError):
                raise UpdateError(f"the new {name} does not compile") from None
    staging = folder / ".update_new"
    backup = folder / ".update_old"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        for name, data in files.items():
            (staging / name).write_bytes(data)
        shutil.rmtree(backup, ignore_errors=True)
        backup.mkdir()
        done: "list[tuple[str, bool]]" = []
        try:
            for name in files:
                target = folder / name
                had_old = target.is_file()
                if had_old:
                    shutil.copy2(target, backup / name)
                os.replace(staging / name, target)
                done.append((name, had_old))
        except Exception:
            for name, had_old in reversed(done):
                try:
                    if had_old:
                        os.replace(backup / name, folder / name)
                    else:
                        (folder / name).unlink()
                except OSError:
                    logger.exception("mod_updates: could not put %s back", name)
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def launcher_refreshes(root: Path) -> bool:
    """Will the launcher copy updated mods from ``mods_available/`` into ``mods/`` at its next start (``modctl.refresh_at_launch``)?
    An older launcher does not, so the new versions would never reach ``mods/``. Asked after a launcher update has been swapped in,
    it is about the new launch file. When the launch file cannot be read the answer is yes (not the package layout: nothing to correct)."""
    try:
        data = (loader_dir(root) / "start_modded_client.py").read_bytes()
    except OSError:
        return True
    return b"refresh_at_launch" in data


def _modctl(root: Path):
    """The launcher's ``modctl`` module (already imported by the launcher in most layouts; otherwise loaded from its file)."""
    module = sys.modules.get("modctl")
    if module is not None and hasattr(module, "update"):
        return module
    path = loader_dir(root) / "modctl.py"
    spec = importlib.util.spec_from_file_location("modctl", path)
    if spec is None or spec.loader is None:
        raise UpdateError("modctl.py cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sync_installed(root: Path, names, modctl=None) -> "list[str]":
    """Copy the new versions of the installed mods *names* from ``mods_available/`` into ``mods/`` with the launcher's own
    ``modctl.update`` (what an up to date launcher does at every start). Returns the mods that were replaced."""
    root = Path(root)
    modctl = modctl or _modctl(root)
    updated, _added, _unknown = modctl.update(names=tuple(names), available_dir=str(root / "mods_available"),
                                              installed_dir=str(root / "mods"))
    return list(updated)


# ── the state file ───────────────────────────────────────────────────────────

def read_state(root: Path) -> dict:
    try:
        data = json.loads((Path(root) / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(root: Path, state: dict) -> bool:
    path = Path(root) / STATE_FILE
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
        return True
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass
        return False


# ── checking and applying ────────────────────────────────────────────────────

@dataclass
class Update:
    """Newer versions of the mods and/or the launcher: what they would change, and what is needed to apply them."""
    backend: str                                       # "zip" or "git" (the mods part); "" when only the launcher has an update
    sha: str
    updated: list = field(default_factory=list)
    added: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    repo: str = ""
    mods: dict = field(default_factory=dict)           # the zip's mods (zip backend)
    blocked: "str | None" = None                       # why the MODS part cannot be applied here (git backend)
    quiet: bool = False                                # nothing the player is asked about
    loader_sha: str = ""                               # the launcher part: the commit of the modloader branch ...
    loader_updated: list = field(default_factory=list)  # ... the files that differ ...
    loader_added: list = field(default_factory=list)    # ... the files that are new ...
    loader_files: dict = field(default_factory=dict)    # ... and their new content (name -> bytes: a few hundred KB at most)

    @property
    def has_mods(self) -> bool:
        return bool(self.updated or self.added)

    @property
    def has_loader(self) -> bool:
        return bool(self.loader_files)


def installed_mods(root: Path) -> "set[str]":
    try:
        return {p.name for p in (Path(root) / "mods").iterdir() if p.is_dir() and not p.name.startswith((".", "_"))}
    except OSError:
        return set()


def available(root: "Path | None" = None) -> bool:
    """Is there a catalogue (``mods_available/`` with mods in it) to keep up to date? Not in the developer folder."""
    root = Path(root) if root is not None else _root()
    catalogue = root / "mods_available"
    try:
        return catalogue.is_dir() and any(p.is_dir() and (p / "mod.json").is_file() for p in catalogue.iterdir())
    except OSError:
        return False


def _check_mods(root: Path, state: dict, now, http, runner, git, tip) -> "Update | None":
    """The mods part of a check: an Update, or None (up to date, or nothing to ask about). A conclusion is noted in *state*.
    *tip* is ``Tips(repo)`` (``tip(branch)`` is a branch's newest commit); a git clone does not need it."""
    catalogue = root / "mods_available"
    if (catalogue / ".git").exists():
        git = git or git_program()
        if git is None:
            return None
        update = git_check(catalogue, git, runner)
        return None if update is None or update.quiet else update
    repo, branch = config()
    sha = tip(branch)
    if sha == state.get("sha"):
        return None
    mods = read_mods(download_zip(repo, sha, http))
    updated, added, removed = mod_digest.diff(mod_digest.digests(catalogue, require_mod_json=True),
                                              {name: mod_digest.digest_files(files) for name, files in mods.items()})
    if not (updated or added):
        state["sha"] = sha
        return None
    return Update(backend="zip", sha=sha, updated=updated, added=added, removed=removed, repo=repo, mods=mods)


def check(root: Path, now=time.time, http=http_get, runner=subprocess.run, git=None, force: bool = False) -> "Update | None":
    """Is there a newer version of the mods or the launcher that the player has not been shown? Raises UpdateError when it cannot
    tell at all (no network...). The two parts are independent: when one cannot be checked the other still is, and a check that
    could not finish is made again at the next start instead of waiting 20 minutes."""
    root = Path(root)
    state = read_state(root)
    if not force and now() - float(state.get("checked_at") or 0) < CHECK_EVERY_S:
        return None
    for old_key in ("etag", "loader_etag"):                      # (what an earlier version of this mod kept; nothing uses it any more)
        state.pop(old_key, None)
    before = dict(state)
    tip = Tips(config()[0], http)
    mods_update = loader_update = None
    errors: "list[UpdateError]" = []
    concluded = False
    if available(root):
        try:
            mods_update = _check_mods(root, state, now, http, runner, git, tip)
            concluded = True
        except UpdateError as exc:
            errors.append(exc)
    if loader_available(root):
        try:
            loader_update = check_loader(root, state, tip, http, now)
            concluded = True
        except UpdateError as exc:
            logger.info("mod_updates: the launcher could not be checked: %s", exc)
            errors.append(exc)
    if errors and not concluded:
        if state != before:                                      # (e.g. the pause after GitHub's request limit was used up)
            write_state(root, state)
        raise errors[0]
    if mods_update is None and loader_update is None:
        if not errors:
            state["checked_at"] = now()
        write_state(root, state)
        return None
    update = mods_update if mods_update is not None else Update(backend="", sha="", repo=config()[0])
    if loader_update is not None:
        update = replace(update, loader_sha=loader_update.loader_sha,
                         loader_updated=loader_update.loader_updated, loader_added=loader_update.loader_added,
                         loader_files=loader_update.loader_files, repo=update.repo or loader_update.repo)
    write_state(root, state)                                     # (what concluded; an offered update records nothing: it is offered again)
    return update


def apply(root: Path, update: Update, now=time.time, runner=subprocess.run, git=None, sync=None) -> "tuple[bool, str, bool]":
    """Update the launcher and/or the catalogue. Returns ``(ok, what to tell the player, restart needed)``. A restart is needed when
    the launcher was updated, or a mod that is in ``mods/`` is among the updated ones: the launcher's refresh copies it in at the next
    start, and an older launcher that has no such refresh is helped here (the new versions are copied into ``mods/`` now)."""
    root = Path(root)
    catalogue = root / "mods_available"
    mods_part = update.has_mods and not update.blocked
    if not update.has_loader and (update.blocked or not mods_part):
        return False, update.blocked or "Nothing to update.", False
    state = read_state(root)
    done: "list[str]" = []
    failed: "list[tuple[str, Exception]]" = []
    copy_failed = False
    if update.has_loader:                          # first: it is small, it is checked before anything is swapped, and the new mods may need it
        try:
            apply_loader(loader_dir(root), update.loader_files)
        except Exception as exc:                   # (nothing was changed: and the mods are not put on top of a launcher that stayed old)
            logger.warning("mod_updates: could not update the launcher: %s", exc)
            return False, f"Could not update the launcher: {exc}. Nothing was changed.", False
        state["loader_sha"] = update.loader_sha
        done.append("the launcher")
    if mods_part:
        try:
            if update.backend == "git":
                git_apply(catalogue, git or git_program() or "git", runner)
            else:
                apply_zip(catalogue, update.mods, [*update.updated, *update.added])
            state["sha"] = update.sha
            done.append("the mods")
        except Exception as exc:
            logger.warning("mod_updates: could not update the mods: %s", exc)
            failed.append(("update the mods", exc))
    if not failed:
        state["checked_at"] = now()
    write_state(root, state)
    launcher_done = "the launcher" in done
    used = [name for name in update.updated if name in installed_mods(root)] if "the mods" in done else []
    if used and not launcher_refreshes(root):
        try:                                       # an older launcher never copies the catalogue's new versions into mods/: do it now
            (sync or sync_installed)(root, used)
        except Exception as exc:
            logger.warning("mod_updates: could not copy the new mods into mods/: %s", exc)
            failed.append(("copy the new mods into mods/", exc))
            copy_failed = True
    if failed:
        message = "could not " + " and ".join(what for what, _ in failed) + ": " + "; ".join(str(exc) for _, exc in failed)
        if done:
            then = "Run modctl.bat update (in the modloader folder) to finish." if copy_failed else "Start the game again to use what was updated."
            return False, f"Updated {' and '.join(done)}, but {message}. {then}", False
        return False, message[0].upper() + message[1:], False
    if launcher_done or used:
        return True, "Updated. Restarting to use the new versions...", True
    return True, "Updated. The changed mods are not ones you use: CHANGE MODS lists them.", False


# ── the bar ──────────────────────────────────────────────────────────────────

def summary(update: Update, limit: int = 4) -> str:
    """What the bar says the update changes, e.g. ``updated: a, b +2 more   new: c   launcher: modctl.py``."""
    parts = []
    for label, names in (("updated", update.updated), ("new", update.added), ("launcher", [*update.loader_updated, *update.loader_added])):
        if names:
            parts.append(f"{label}: " + ", ".join(names[:limit]) + (f" +{len(names) - limit} more" if len(names) > limit else ""))
    return "   ".join(parts)


def bar_title(update: Update) -> str:
    return "Updates available" if update.has_loader else "Mod updates available"


def button_label(update: Update) -> str:
    return "UPDATE" if update.has_loader else "UPDATE MODS"


def summary_counts(update: Update) -> str:
    """The shortest form of the summary: how many, not which (``updated: 5   new: 4   launcher: 4 files``)."""
    parts = []
    if update.updated:
        parts.append(f"updated: {len(update.updated)}")
    if update.added:
        parts.append(f"new: {len(update.added)}")
    files = len(update.loader_updated) + len(update.loader_added)
    if files:
        parts.append(f"launcher: {files} file" + ("" if files == 1 else "s"))
    return "   ".join(parts)


def fitted_headline(font, head: str, update: Update, room: int) -> str:
    """``Title:  updated: ...   new: ...   launcher: ...``: the lists are cut to as few names as it takes to fit in *room*, then to
    counts (so the launcher part is never the one that disappears); the caller cuts text that still does not fit."""
    for text in [f"{head}:  {summary(update, limit)}" for limit in (4, 3, 2, 1)] + [f"{head}:  {summary_counts(update)}"]:
        if font.size(text)[0] <= room:
            break
    return text


def _fit(font, text: str, width: int) -> str:
    if font.size(text)[0] <= width:
        return text
    while text and font.size(text + "...")[0] > width:
        text = text[:-1]
    return text.rstrip() + "..."


def bar_rect(win_w: int, win_h: int) -> "pygame.Rect":
    width = max(200, min(win_w - 24, 1000))
    return pygame.Rect((win_w - width) // 2, 8, width, BAR_H)


def _state(win) -> dict:
    state = win.__dict__.get("_mu_state")
    if state is None:
        state = win._mu_state = {"phase": "new", "check": None, "apply": None, "update": None, "message": None, "until": 0.0,
                                 "rects": {}, "restart": False}
    return state


def _start_check(win, root: Path) -> None:
    state = _state(win)
    holder = state["check"] = {"done": False, "update": None, "error": None}

    def work():
        try:
            holder["update"] = check(root)
        except UpdateError as exc:
            logger.info("mod_updates: no check this time: %s", exc)
            holder["error"] = str(exc)
        except Exception:
            logger.exception("mod_updates: the check failed")
            holder["error"] = "failed"
        holder["done"] = True

    threading.Thread(target=work, name="mod_updates-check", daemon=True).start()


def _start_apply(win, root: Path, update: Update) -> None:
    state = _state(win)
    holder = state["apply"] = {"done": False, "result": None}

    def work():
        try:
            holder["result"] = apply(root, update)
        except Exception as exc:
            logger.exception("mod_updates: the update failed")
            holder["result"] = (False, f"Could not update the mods: {exc}", False)
        holder["done"] = True

    threading.Thread(target=work, name="mod_updates-apply", daemon=True).start()


def restart(win) -> bool:
    """Close this client the way Exit Game does and start a new one when it is gone."""
    if relaunch.restart_after_exit(root=_root()):
        pygame.event.post(pygame.event.Event(pygame.QUIT))
        return True
    return False


def poll(win) -> None:
    """Every frame: move the bar on when the check or the update has finished."""
    state = _state(win)
    now = time.monotonic()
    if state["phase"] == "new":
        root = _root()
        state["phase"] = "checking" if available(root) or loader_available(root) else "off"
        if state["phase"] == "checking":
            _start_check(win, root)
    elif state["phase"] == "checking" and state["check"]["done"]:
        update = state["check"]["update"]
        if update is None:
            state["phase"] = "idle"
        else:
            state["update"] = update
            state["phase"] = "blocked" if update.blocked and not update.has_loader else "available"
    elif state["phase"] == "applying" and state["apply"]["done"]:
        ok, message, needs_restart = state["apply"]["result"]
        state["message"], state["until"] = (message, not ok), now + SHOW_DONE_S
        if ok and needs_restart:
            if restart(win):
                state["phase"] = "restarting"
                state["until"] = now + 120.0
            else:
                state["message"] = ("Updated. Close the game and start it again to use the new versions.", False)
                state["phase"] = "done"
        else:
            state["phase"] = "done" if ok else "failed"
    elif state["phase"] in ("done", "failed") and now > state["until"]:
        state["phase"] = "idle"


def visible(state: dict) -> bool:
    return state["phase"] in ("available", "blocked", "applying", "restarting", "done", "failed")


def draw_bar(win, screen, fonts) -> None:
    state = _state(win)
    poll(win)
    if not visible(state):
        win._top_banner_h = 0
        state["rects"] = {}
        return
    width, height = screen.get_size()
    rect = bar_rect(width, height)
    win._top_banner_h = rect.bottom + 4
    pygame.draw.rect(screen, (8, 12, 30), rect, border_radius=3)
    border = (210, 160, 50) if state["phase"] in ("available", "blocked") else ((220, 60, 60) if state["phase"] == "failed" else (0, 80, 200))
    pygame.draw.rect(screen, border, rect, 1, border_radius=3)
    font = fonts[2]
    mouse = pygame.mouse.get_pos()
    phase = state["phase"]
    buttons: "list[tuple[str, str]]" = []
    update = state["update"]
    shown = None                                                                  # (the update whose summary follows the title)
    if phase == "available":
        head, shown = bar_title(update), update
        caption = f"github.com/{update.repo or config()[0]} - nothing changes until you press {button_label(update)}"
        if update.blocked:                           # (the launcher can be updated here; the mods cannot)
            caption = f"{update.blocked} - {button_label(update)} does the launcher only"
        buttons = [("update", button_label(update)), ("later", "LATER")]
    elif phase == "blocked":
        head, shown = bar_title(update), update
        caption = update.blocked
        buttons = [("later", "OK")]
    elif phase == "applying":
        head, caption = ("Updating..." if update.has_loader else "Updating the mods..."), "Please wait."
    elif phase == "restarting":
        head, caption = "Restarting with the new versions..." if update.has_loader else "Restarting with the new mods...", ""
    else:
        head, caption = state["message"][0], ""
    rects: "dict[str, pygame.Rect]" = {}
    x = rect.right - 8
    for key, label in reversed(buttons):
        label_w = font.size(label)[0] + 22
        button = pygame.Rect(x - label_w, rect.y + (rect.h - 28) // 2, label_w, 28)
        hover = button.collidepoint(mouse)
        pygame.draw.rect(screen, (20, 40, 90) if hover else (14, 22, 52), button, border_radius=3)
        pygame.draw.rect(screen, (0, 180, 255) if hover else (0, 90, 200), button, 1, border_radius=3)
        text = font.render(label, True, (0, 180, 255))
        screen.blit(text, text.get_rect(center=button.center))
        rects[key] = button
        x = button.x - 6
    state["rects"] = rects
    room = x - rect.x - 24
    headline = fitted_headline(font, head, shown, room) if shown is not None else head
    screen.blit(font.render(_fit(font, headline, room), True, (255, 210, 120) if phase in ("available", "blocked") else
                            ((220, 60, 60) if phase == "failed" else (150, 190, 240))), (rect.x + 12, rect.y + 9))
    if caption:
        screen.blit(font.render(_fit(font, caption, room), True, (110, 130, 170)), (rect.x + 12, rect.y + 9 + font.get_height() + 4))


def handle_click(win, event) -> bool:
    """True when the click was on one of the bar's buttons (and was acted on)."""
    state = _state(win)
    if event.type != pygame.MOUSEBUTTONDOWN or getattr(event, "button", 0) != 1:
        return False
    for key, rect in state["rects"].items():
        if rect.collidepoint(event.pos):
            if key == "later":
                state["phase"] = "idle"
            elif key == "update" and state["phase"] == "available":
                state["phase"] = "applying"
                _start_apply(win, _root(), state["update"])
            state["rects"] = {}                                   # (the buttons are gone: a second click cannot press them again)
            return True
    return False


# ── the game's hooks (LoginScreen.draw, LoginScreen.handle_event) ────────────

def after_draw(win, screen, fonts, *args, **kwargs) -> None:
    if not enabled():
        return
    try:
        draw_bar(win, screen, fonts)
    except Exception:
        _switch_off("after_draw()")


def before_event(win, event, *args, **kwargs) -> None:
    if not enabled():
        return
    try:
        handle_click(win, event)
    except Exception:
        _switch_off("before_event()")


def install(login_cls) -> "list[str]":
    """Hook the login screen. Idempotent; never raises (see ``modloader.install_methods``)."""
    if login_cls.__dict__.get("_mod_updates_installed"):
        return []
    import modloader
    problem = core_problem()
    if problem:                                      # (the Mods page shows it in red; the game carries on without this mod's feature)
        logger.warning("mod_updates: switched off: %s", problem)
        getattr(modloader, "ATTACH_FAILURES", {})["mod_updates"] = problem
        return [problem]
    problems = modloader.install_methods(login_cls, None, None, (), before={"handle_event": before_event},
                                         after={"draw": after_draw})
    login_cls._mod_updates_installed = True
    return problems


# what test_mods_apply checks after every game update: (game module, game class, game method, the function that runs around it)
HOOKS = (("login", "LoginScreen", "draw", "after_draw"), ("login", "LoginScreen", "handle_event", "before_event"))

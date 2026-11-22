"""mod_updates.py - asks GitHub whether the mods have newer versions, and updates them when the player says yes.

At the first frame of the login screen a background thread asks whether the repository's ``mods_available`` branch has moved. If it
has, the mods that would change are worked out and the login screen shows a bar:

    Mod updates available:  updated: quick_bars, ui_layouts   new: foo          [UPDATE MODS] [LATER]

NOTHING is changed until UPDATE MODS is pressed; LATER hides the bar until the next start. Updating refreshes the catalogue
(``mods_available/``); the launcher's own start-up refresh then copies what changed into ``mods/``, so when a mod the player
uses is among them the client restarts (relaunch.py) to pick it up.

Two ways to get the files, chosen by what ``mods_available/`` is:

  * a plain folder (the stand-alone package): over HTTPS, no git and no account needed (the repository is public): the latest
    commit's SHA from ``api.github.com`` (cheap, conditional on an ETag), and only when it is new the zip of exactly that commit
    from ``codeload.github.com``. What the zip holds is compared with the catalogue folder by folder (mod_digest.py), so the bar
    names the mods that really differ. Paths in the zip are checked (no ``..``, no absolute paths), its size is capped, and only
    folders with a ``mod.json`` are taken; every mod is swapped in beside the old one first and put back if anything fails.
  * a git clone (the developer, or whoever cloned it): ``git fetch`` and a fast-forward, with prompts switched off. A clone with
    local changes, or one that cannot fast-forward, is only reported, never touched.

Why ask first: a mod is Python that runs inside the game, next to the saved logins. Nothing here runs or installs code by itself,
the bar says where it comes from, and a start with no network, or a check that fails, shows nothing at all. A check is made at
most every 20 minutes (a start that finds the mods up to date), and ``STAR_EMPIRE_NO_MOD_UPDATES=1`` switches the whole thing off.

Every function the game calls here is total: an error switches the bar off for the session.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pygame

import mod_digest
import relaunch

logger = logging.getLogger(__name__)

ENV_OFF = "STAR_EMPIRE_NO_MOD_UPDATES"
ENV_REPO = "STAR_EMPIRE_MOD_UPDATES_REPO"            # "owner/repo" or "owner/repo@branch": a fork, or a test
DEFAULT_REPO = "mkden1/star-empire-modloader"
DEFAULT_BRANCH = "mods_available"
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

_failed = False


class UpdateError(Exception):
    """Something the check or the update could not do (network, a bad zip, a git refusal); the text is for the log and the bar."""


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


def _root() -> Path:
    import modloader
    return Path(modloader.REPO_DIR)


# ── the network ──────────────────────────────────────────────────────────────

def _header(headers, name: str):
    for key, value in (headers or {}).items():
        if key.lower() == name.lower():
            return value
    return None


def http_get(url: str, headers=None, timeout: float = TIMEOUT_S, max_bytes: int = MAX_ZIP_BYTES) -> "tuple[int, dict, bytes]":
    """One HTTPS GET: ``(status, headers, body)``. 304 is an answer, not an error; the body is refused beyond *max_bytes*."""
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
        if exc.code == 304:
            return 304, dict(exc.headers), b""
        raise UpdateError(f"the server answered {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise UpdateError(f"no connection ({exc})") from None


def latest_sha(repo: str, branch: str, etag=None, http=http_get) -> "tuple[str | None, str | None]":
    """The SHA of the branch's newest commit, as ``(sha, etag)``; ``(None, etag)`` when the ETag says nothing changed."""
    headers = {"Accept": "application/vnd.github.sha"}
    if etag:
        headers["If-None-Match"] = etag
    status, answer, body = http(f"https://api.github.com/repos/{repo}/commits/{branch}", headers, TIMEOUT_S, 4096)
    if status == 304:
        return None, etag
    sha = body.decode("ascii", "replace").strip()
    if status != 200 or not _SHA_RE.match(sha):
        raise UpdateError("unexpected answer from GitHub")
    return sha, _header(answer, "ETag")


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
    """A newer version of the mods: what it would change, and what is needed to apply it."""
    backend: str                                       # "zip" or "git"
    sha: str
    updated: list = field(default_factory=list)
    added: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    etag: "str | None" = None
    repo: str = ""
    mods: dict = field(default_factory=dict)           # the zip's mods (zip backend)
    blocked: "str | None" = None                       # why it cannot be applied here (git backend)
    quiet: bool = False                                # nothing the player is asked about


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


def check(root: Path, now=time.time, http=http_get, runner=subprocess.run, git=None, force: bool = False) -> "Update | None":
    """Is there a newer version of the mods the player has not been shown? Raises UpdateError when it cannot tell (no network...)."""
    root = Path(root)
    catalogue = root / "mods_available"
    state = read_state(root)
    if not force and now() - float(state.get("checked_at") or 0) < CHECK_EVERY_S:
        return None
    if (catalogue / ".git").exists():
        git = git or git_program()
        if git is None:
            return None
        update = git_check(catalogue, git, runner)
        if update is None or update.quiet:
            state["checked_at"] = now()
            write_state(root, state)
            return None
        return update
    repo, branch = config()
    sha, etag = latest_sha(repo, branch, state.get("etag") if state.get("sha") else None, http)
    if sha is None or sha == state.get("sha"):
        state.update(checked_at=now(), etag=etag or state.get("etag"))
        write_state(root, state)
        return None
    mods = read_mods(download_zip(repo, sha, http))
    updated, added, removed = mod_digest.diff(mod_digest.digests(catalogue, require_mod_json=True),
                                              {name: mod_digest.digest_files(files) for name, files in mods.items()})
    if not (updated or added):
        state.update(sha=sha, etag=etag, checked_at=now())
        write_state(root, state)
        return None
    return Update(backend="zip", sha=sha, updated=updated, added=added, removed=removed, etag=etag, repo=repo, mods=mods)


def apply(root: Path, update: Update, now=time.time, runner=subprocess.run, git=None) -> "tuple[bool, str, bool]":
    """Update the catalogue. Returns ``(ok, what to tell the player, restart needed)``: a restart is needed when a mod that is
    in ``mods/`` is among the updated ones (the launcher's refresh then copies it in at the next start)."""
    root = Path(root)
    catalogue = root / "mods_available"
    if update.blocked:
        return False, update.blocked, False
    try:
        if update.backend == "git":
            git_apply(catalogue, git or git_program() or "git", runner)
        else:
            apply_zip(catalogue, update.mods, [*update.updated, *update.added])
    except Exception as exc:
        logger.warning("mod_updates: could not update: %s", exc)
        return False, f"Could not update the mods: {exc}", False
    state = read_state(root)
    state.update(sha=update.sha, etag=update.etag, checked_at=now())
    write_state(root, state)
    used = installed_mods(root)
    restart = any(name in used for name in update.updated)
    if restart:
        return True, "Updated. Restarting to use the new versions...", True
    return True, "Updated. The changed mods are not ones you use: CHANGE MODS lists them.", False


# ── the bar ──────────────────────────────────────────────────────────────────

def summary(update: Update, limit: int = 4) -> str:
    """What the bar says the update changes, e.g. ``updated: a, b +2 more   new: c``."""
    parts = []
    for label, names in (("updated", update.updated), ("new", update.added)):
        if names:
            parts.append(f"{label}: " + ", ".join(names[:limit]) + (f" +{len(names) - limit} more" if len(names) > limit else ""))
    return "   ".join(parts)


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
        state["phase"] = "checking" if available(root) else "off"
        if state["phase"] == "checking":
            _start_check(win, root)
    elif state["phase"] == "checking" and state["check"]["done"]:
        update = state["check"]["update"]
        if update is None:
            state["phase"] = "idle"
        else:
            state["update"] = update
            state["phase"] = "blocked" if update.blocked else "available"
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
    if phase == "available":
        head, detail = "Mod updates available", summary(state["update"])
        repo = state["update"].repo or config()[0]
        caption = f"github.com/{repo} - nothing changes until you press UPDATE MODS"
        buttons = [("update", "UPDATE MODS"), ("later", "LATER")]
    elif phase == "blocked":
        head, detail = "Mod updates available", summary(state["update"])
        caption = state["update"].blocked
        buttons = [("later", "OK")]
    elif phase == "applying":
        head, detail, caption = "Updating the mods...", "", "Please wait."
    elif phase == "restarting":
        head, detail, caption = "Restarting with the new mods...", "", ""
    else:
        head, detail, caption = state["message"][0], "", ""
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
    headline = head + (f":  {detail}" if detail else "")
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
    problems = modloader.install_methods(login_cls, None, None, (), before={"handle_event": before_event},
                                         after={"draw": after_draw})
    login_cls._mod_updates_installed = True
    return problems


# what test_mods_apply checks after every game update: (game module, game class, game method, the function that runs around it)
HOOKS = (("login", "LoginScreen", "draw", "after_draw"), ("login", "LoginScreen", "handle_event", "before_event"))

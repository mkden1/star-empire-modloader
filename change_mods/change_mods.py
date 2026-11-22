"""change_mods.py - a CHANGE MODS button on the login screen, and a note of the mods that changed since you last started.

The mods are applied while the game starts, so changing them means starting the game again. The button opens the mod picker
window (the same one ``Choose Mods.bat`` opens: ``modloader/modpicker.py`` of the stand-alone package) and waits for it WITHOUT
freezing the login window. When the picker closes:

  * Apply (exit code 0) with a different set of mods in ``mods/``: this client closes the normal way and a new one starts once it
    is gone (relaunch.py), so the new mods are in use at once;
  * Apply with nothing changed: a short note, the login screen stays;
  * closed without applying (exit code 2): nothing happens;
  * anything else (the picker crashed): a note pointing at ``picker_error.txt``.

The button is drawn under the login card by a hook after ``LoginScreen.draw`` and its click is taken by a hook before
``LoginScreen.handle_event`` (the card ignores clicks outside its own controls, so the game's handler never sees it as anything).
It only appears where there is a picker and mods to pick from: not in the dev folder, not in a package older than the picker.

The NOTE: the first time the login screen is drawn, a background thread hashes what is in ``mods/`` and compares it with the
snapshot it wrote at the previous start (``mods_seen.json`` next to the client: mod name -> hash of its files). Mods that differ, are
new or are gone are listed at the top of the login screen for a while ("Mods changed since your last start: updated: quick_bars").
That is how an update the launch-time refresh brought in (or a mod copied in by hand) becomes visible. The first start, and any start
with nothing changed, show nothing. When the picker changes the mods this client restarts, and the snapshot is brought up to date
first, so the player's own choice is never reported as a change.

Every function the game calls here is total: an error switches the button off for the session.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pygame

import relaunch

logger = logging.getLogger(__name__)

ENV_OFF = "STAR_EMPIRE_NO_CHANGE_MODS"
BUTTON_W, BUTTON_H = 230, 30
CARD_MAX_H = 510                 # the login card's height on the LOGIN tab (the REGISTER tab's is 20 less), centred in the window
GAP = 14
STATUS_S = 6.0                   # how long a note stays under the button
SEEN_FILE = "mods_seen.json"     # next to the client: what mods/ held at the previous start
NOTE_S = 25.0                    # how long the list of changed mods stays up
NOTE_NAMES = 6                   # names per line before "+N more"

_NO_WINDOW = 0x08000000          # CREATE_NO_WINDOW
_SKIP_DIRS = {"__pycache__", "screenshots"}
_SKIP_FILES = {"README.md"}

_failed = False


def _switch_off(where: str) -> None:
    global _failed
    if not _failed:
        _failed = True
        logger.exception("change_mods: switched off for this session (%s)", where)


def enabled() -> bool:
    return not _failed and not os.environ.get(ENV_OFF)


def reset_for_tests() -> None:
    global _failed
    _failed = False


# ── what there is to open ────────────────────────────────────────────────────

def _root() -> Path:
    import modloader
    return Path(modloader.REPO_DIR)


def picker_command(root: "Path | None" = None) -> "list[str] | None":
    """The command that opens the mod picker, or None when this folder has no picker or no mods to pick from (the dev folder keeps
    every mod in ``mods/``; a package older than the picker has no ``modpicker.py``)."""
    root = Path(root) if root is not None else _root()
    script = root / "modloader" / "modpicker.py"
    offered = root / "mods_available"
    if not sys.executable or not script.is_file() or not offered.is_dir():
        return None
    if not any(p.is_dir() and not p.name.startswith(".") for p in offered.iterdir()):
        return None
    return [sys.executable, str(script), "--root", str(root)]


def fingerprint(mods_dir: "Path | None" = None) -> tuple:
    """What is in ``mods/``: every mod folder with the size and time of each of its files (README, screenshots, tests' caches and
    dot-folders such as ``.replaced`` do not count). Two fingerprints differ exactly when the set of mods or a mod's files changed."""
    mods_dir = Path(mods_dir) if mods_dir is not None else _root() / "mods"
    out = []
    try:
        folders = sorted(p for p in mods_dir.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return ()
    for folder in folders:
        files = []
        for base, dirs, names in os.walk(folder):
            dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
            for name in sorted(names):
                if name in _SKIP_FILES or name.endswith((".pyc", ".pyo")):
                    continue
                path = Path(base) / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                files.append((str(path.relative_to(folder)), stat.st_size, stat.st_mtime_ns))
        out.append((folder.name, tuple(files)))
    return tuple(out)


# ── what changed since the last start ────────────────────────────────────────

def digest(folder: Path) -> str:
    """A hash of everything a mod folder holds that matters (its code and data: not the README, screenshots or caches), so a mod
    that was updated differs and a mod that was only copied again does not."""
    hasher = hashlib.sha1()
    for base, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        for name in sorted(names):
            if name in _SKIP_FILES or name.endswith((".pyc", ".pyo")):
                continue
            path = Path(base) / name
            try:
                data = path.read_bytes()
            except OSError:
                continue
            hasher.update(path.relative_to(folder).as_posix().encode("utf-8", "replace"))
            hasher.update(b"\0")
            hasher.update(data)
            hasher.update(b"\0")
    return hasher.hexdigest()


def digests(mods_dir: "Path | None" = None) -> "dict[str, str]":
    """Every mod in ``mods/`` with its hash (dot-folders and loose files are not mods)."""
    mods_dir = Path(mods_dir) if mods_dir is not None else _root() / "mods"
    try:
        folders = sorted(p for p in mods_dir.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return {}
    return {folder.name: digest(folder) for folder in folders}


def read_seen(root: "Path | None" = None) -> "dict[str, str] | None":
    """The snapshot of the previous start, or None when there is none (the first start) or it cannot be read."""
    path = (Path(root) if root is not None else _root()) / SEEN_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    mods = data.get("mods") if isinstance(data, dict) else None
    if not isinstance(mods, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mods.items()):
        return None
    return dict(mods)


def write_seen(snapshot: "dict[str, str]", root: "Path | None" = None) -> bool:
    """Keep *snapshot* as what the next start compares with (written aside and moved into place). False when it could not be."""
    path = (Path(root) if root is not None else _root()) / SEEN_FILE
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps({"version": 1, "mods": snapshot}, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
        return True
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass
        return False


def diff(old: "dict[str, str]", new: "dict[str, str]") -> "tuple[list[str], list[str], list[str]]":
    """``(updated, added, removed)`` mod names, each sorted."""
    updated = sorted(name for name in new if name in old and new[name] != old[name])
    added = sorted(name for name in new if name not in old)
    removed = sorted(name for name in old if name not in new)
    return updated, added, removed


def note_lines(updated, added, removed) -> "list[str]":
    """The text of the note: a header and a line per kind of change; nothing when nothing changed."""
    lines = []
    for label, names in (("updated", updated), ("new", added), ("removed", removed)):
        if names:
            shown = ", ".join(names[:NOTE_NAMES]) + (f" +{len(names) - NOTE_NAMES} more" if len(names) > NOTE_NAMES else "")
            lines.append(f"  {label}: {shown}")
    return ["Mods changed since your last start:", *lines] if lines else []


def scan_changes(root: "Path | None" = None) -> "tuple[list[str], list[str], list[str]]":
    """Compare ``mods/`` with the previous snapshot, keep the new one, and say what changed (nothing on the first start)."""
    root = Path(root) if root is not None else _root()
    current = digests(root / "mods")
    seen = read_seen(root)
    write_seen(current, root)
    return diff(seen, current) if seen is not None else ([], [], [])


def _begin_scan(win, root: Path) -> None:
    state = _state(win)
    holder = state["scan"] = {"done": False, "lines": []}

    def work():
        try:
            holder["lines"] = note_lines(*scan_changes(root))
        except Exception:
            logger.exception("change_mods: could not compare the mods with the last start")
        holder["done"] = True

    threading.Thread(target=work, name="change_mods-scan", daemon=True).start()


def mods_in_effect() -> "int | None":
    try:
        import modloader
        info = modloader.installed()
        return None if info is None else len(info.result.applied)
    except Exception:
        return None


# ── the button ───────────────────────────────────────────────────────────────

def button_rect(win_w: int, win_h: int) -> "pygame.Rect | None":
    """Under the login card, centred; None when the window is too small to leave room below the card."""
    width = min(BUTTON_W, win_w - 20)
    if width < 60:
        return None
    card_bottom = (win_h + CARD_MAX_H) // 2
    rect = pygame.Rect((win_w - width) // 2, card_bottom + GAP, width, BUTTON_H)
    rect.y = min(rect.y, win_h - BUTTON_H - 8)
    return rect if rect.top >= card_bottom + 4 else None


def _state(win) -> dict:
    state = win.__dict__.get("_cm_state")
    if state is None:
        state = win._cm_state = {"proc": None, "before": (), "status": None, "rect": None}
    return state


def _say(win, text: str, error: bool = False, seconds: float = STATUS_S) -> None:
    _state(win)["status"] = (text, error, time.monotonic() + seconds)


def running(win) -> bool:
    return _state(win)["proc"] is not None


def start_picker(win) -> bool:
    """Open the picker (a window of its own) and keep the login screen alive while it is open. False when nothing was started."""
    state = _state(win)
    if state["proc"] is not None:
        return False
    command = picker_command()
    if command is None:
        _say(win, "The mod picker is not in this folder.", True)
        return False
    state["before"] = fingerprint()
    try:
        state["proc"] = subprocess.Popen(command, cwd=command[-1], creationflags=_NO_WINDOW if os.name == "nt" else 0)
    except OSError:
        logger.exception("change_mods: could not open the mod picker")
        _say(win, "The mod picker could not be opened.", True)
        return False
    return True


def poll(win) -> None:
    """Called every frame: has the picker closed, and what did the player do in it?"""
    state = _state(win)
    proc = state["proc"]
    if proc is None:
        return
    code = proc.poll()
    if code is None:
        return
    state["proc"] = None
    if code == 2:                                           # closed without applying: nothing to say
        return
    if code != 0:
        _say(win, "The mod picker could not open (picker_error.txt has the reason).", True, 10.0)
        return
    if fingerprint() == state["before"]:
        _say(win, "No changes.")
        return
    restart(win)


def restart(win) -> None:
    """The mods changed: close this client the way Exit Game does and start a new one when it is gone."""
    try:
        write_seen(digests(), _root())                      # the player's own choice is not "a change since your last start"
    except Exception:
        logger.exception("change_mods: could not update the snapshot of the mods")
    if relaunch.restart_after_exit(root=_root()):
        _say(win, "Restarting with your mods...", False, 60.0)
        pygame.event.post(pygame.event.Event(pygame.QUIT))
    else:
        _say(win, "Saved. Close the game and start it again to use the new mods.", False, 15.0)


def draw_button(win, screen, fonts) -> None:
    state = _state(win)
    poll(win)
    width, height = screen.get_size()
    if "available" not in state:                            # the folder does not change while the login screen is up: look once
        state["available"] = picker_command() is not None
    if state["available"] and "scan" not in state:
        _begin_scan(win, _root())
    _draw_note(win, screen, fonts, state)
    rect = state["rect"] = button_rect(width, height) if state["available"] else None
    if rect is None:
        return
    busy = running(win)
    hover = not busy and rect.collidepoint(pygame.mouse.get_pos())
    font_med, font_sm = fonts[1], fonts[2]
    pygame.draw.rect(screen, (20, 40, 90) if hover else (8, 12, 30), rect, border_radius=3)
    pygame.draw.rect(screen, (0, 180, 255) if hover else (0, 80, 200), rect, 1, border_radius=3)
    count = mods_in_effect()
    label = "CHOOSING MODS..." if busy else (f"CHANGE MODS ({count} on)" if count else "CHANGE MODS")
    text = font_sm.render(label, True, (90, 120, 160) if busy else (0, 180, 255))
    screen.blit(text, text.get_rect(center=rect.center))
    status = state["status"]
    if status is not None and time.monotonic() > status[2]:
        status = state["status"] = None
    if status is not None:
        note = font_sm.render(status[0], True, (220, 60, 60) if status[1] else (150, 190, 240))
        screen.blit(note, note.get_rect(midtop=(width // 2, rect.bottom + 8)))


def _draw_note(win, screen, fonts, state: dict) -> None:
    """The list of mods that changed, at the top of the window while there is room above the card (and for NOTE_S seconds)."""
    holder = state.get("scan")
    if holder is None or not holder["done"] or not holder["lines"]:
        return
    now = time.monotonic()
    state.setdefault("note_until", now + NOTE_S)
    if now > state["note_until"]:
        return
    width, height = screen.get_size()
    font = fonts[2]
    line_h = font.render("Ag", True, (0, 0, 0)).get_height() + 3
    if (height - CARD_MAX_H) // 2 < 12 + line_h * len(holder["lines"]):
        return                                              # the card is in the way
    for index, line in enumerate(holder["lines"]):
        text = font.render(line, True, (0, 180, 255) if index == 0 else (150, 190, 240))
        screen.blit(text, text.get_rect(midtop=(width // 2, 10 + index * line_h)))


def handle_click(win, event) -> bool:
    """True when the click was on the button (and the picker was asked for)."""
    rect = _state(win)["rect"]
    if (rect is None or event.type != pygame.MOUSEBUTTONDOWN or getattr(event, "button", 0) != 1
            or not rect.collidepoint(event.pos)):
        return False
    if not running(win):
        start_picker(win)
    return True


# ── the game's hooks (LoginScreen.draw, LoginScreen.handle_event) ────────────

def after_draw(win, screen, fonts, *args, **kwargs) -> None:
    if not enabled():
        return
    try:
        draw_button(win, screen, fonts)
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
    if login_cls.__dict__.get("_change_mods_installed"):
        return []
    import modloader
    problems = modloader.install_methods(login_cls, None, None, (), before={"handle_event": before_event},
                                         after={"draw": after_draw})
    login_cls._change_mods_installed = True
    return problems


# what test_mods_apply checks after every game update: (game module, game class, game method, the function that runs around it)
HOOKS = (("login", "LoginScreen", "draw", "after_draw"), ("login", "LoginScreen", "handle_event", "before_event"))

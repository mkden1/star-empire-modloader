"""change_mods.py - a CHANGE MODS button on the login screen.

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
Every function the game calls here is total: an error switches the button off for the session.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
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

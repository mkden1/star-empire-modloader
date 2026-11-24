"""modguard.py - keep the game runnable when a mod misbehaves (the mod loader's safety net).

The mod loader (modloader.py) already switches off a mod whose PATCH no longer fits the game, at start. That cannot see a mod
whose hooks still apply but no longer fit the game's functions (a wrapper that takes three arguments where the game now passes
four: every call raises before the wrapper's first line runs, and the client dies right after login), or a mod that fails
later. This file adds three layers, and every one is total: nothing in here may stop the game from starting.

1. ``check_wrappers``: once the game's code and the mods' hooks are loaded and before the window opens, every function a mod has
   wrapped (it carries ``__wrapped__``, which is how a wrapper says what it wraps) is compared with the function it wraps. A
   wrapper that cannot take every call the game's function takes is a crash waiting for its first call: its mod is switched off
   and the game restarts without it (or, if it cannot restart, the game's own function is put back).
2. The crash catcher: an uncaught exception that ends the game (the world window's thread, or the main thread) is attributed to
   a mod when it can be, and that mod is switched off from the next start on. It is the mod when the frame that raised is the
   mod's code or a line a mod's patch added to a game file; when a call failed to bind its arguments and names a mod's function;
   or when the mod's code called the function that raised. Nothing is guessed beyond that.
3. Safe mode: two crashes in a row that cannot be pinned on a mod start the game with every mod off, once, and say so.

A mod switched off this way is written to ``mods_auto_off.json`` with fingerprints of the game and of the mod. It comes back by
itself when either changes (the game was updated, or the mod was: it may be fixed); delete the file to bring every one back at
once. ``STAR_EMPIRE_NO_MODGUARD=1`` turns this whole file off.

Standard library only.
"""

from __future__ import annotations

import atexit
import hashlib
import inspect
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional, Sequence

LOG = logging.getLogger("modguard")

ENV_OFF = "STAR_EMPIRE_NO_MODGUARD"
ENV_RESTARTS = "STAR_EMPIRE_MODGUARD_RESTARTS"
ENV_NO_UPDATE = "STAR_EMPIRE_NO_UPDATE"       # the restarted client skips the official launcher's update check

AUTO_OFF_FILE = "mods_auto_off.json"
CRASH_FILE = "mods_crash.json"

MAX_RESTARTS = 3                # restarts one launch may chain (each can switch one more mod off) before the game just runs
SAFE_MODE_AFTER = 2             # crashes in a row that no mod could be blamed for
FATAL_THREADS = ("SolarSystemWindow", "MainThread")      # an uncaught exception in one of these ends the game
CRASH_LOGGER = "crash_log"      # the game's crash_log.py logs every uncaught exception as CRITICAL on this logger
HISTORY = 10

_DETACHED = 0x00000008          # DETACHED_PROCESS
_NEW_GROUP = 0x00000200         # CREATE_NEW_PROCESS_GROUP
_NO_WINDOW = 0x08000000         # CREATE_NO_WINDOW

# The command that started this process, taken at import (the launch scripts change the working directory later).
_LAUNCH: "Optional[list[str]]" = ([sys.executable, os.path.abspath(sys.argv[0]), *sys.argv[1:]]
                                  if sys.executable and sys.argv and sys.argv[0] else None)
_LAUNCH_CWD = os.getcwd()


def enabled(env=None) -> bool:
    return (os.environ if env is None else env).get(ENV_OFF, "").strip() in ("", "0")


# -- small files ------------------------------------------------------------------------------------------

def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict) -> None:
    try:
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass                                                    # a read-only folder: the guard just forgets


def game_fingerprint(internal: Path) -> str:
    """The game's version and the size of Client.py: what changes when the game is updated (not its time stamps, which the
    launcher may touch)."""
    internal = Path(internal)
    try:
        version = (internal.parent / "version.txt").read_text(encoding="utf-8-sig").strip()
    except OSError:
        version = ""
    try:
        size = str((internal / "Client.py").stat().st_size)
    except OSError:
        size = ""
    return f"{version}|{size}"


_FINGERPRINT_SKIP = ("conftest.py", "expected_failures.txt")


def mod_fingerprint(folder: Path) -> str:
    """What the mod IS: its code, its patches and its mod.json (not its tests, and not the data files it writes in its folder)."""
    h = hashlib.sha1()
    try:
        for f in sorted(Path(folder).iterdir(), key=lambda p: p.name):
            if (not f.is_file() or f.name.startswith("test_") or f.name in _FINGERPRINT_SKIP
                    or not (f.suffix in (".py", ".patch") or f.name == "mod.json")):
                continue
            h.update(f.name.encode("utf-8"))
            h.update(f.read_bytes())
    except OSError:
        pass
    return h.hexdigest()[:16]


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# -- which mod does this code belong to ----------------------------------------------------------------------

def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


class Attributor:
    """Maps a file (and a line) to the mod it belongs to: a file in a mod's folder, or a line that a mod's patch added to a game file."""

    def __init__(self, mods: Sequence, internal: Path, spans: "Optional[dict]" = None):
        self.roots = [(_norm(str(m.path)) + os.sep, m.name) for m in mods]
        self.internal = _norm(str(internal))
        self.spans = {os.path.normcase(name): list(items) for name, items in (spans or {}).items()}
        self.namespaces: "list[dict]" = []          # extra dicts to look in (the game's __main__ namespace is not in sys.modules)

    def owner_of_file(self, filename: str, lineno: "Optional[int]" = None) -> "Optional[str]":
        if not filename or filename.startswith("<"):
            return None
        f = _norm(filename)
        for root, name in self.roots:
            if f.startswith(root):
                return name
        if lineno is not None and os.path.dirname(f) == self.internal:
            for first, last, mod in self.spans.get(os.path.basename(f), ()):
                if first <= lineno <= last:
                    return mod
        return None

    def owner_of_function(self, qualname: str) -> "Optional[str]":
        """The mod of the one loaded function called ``qualname`` whose code is a mod's (None when there are none or several)."""
        owners = set()
        for _container, _where, _name, _raw, fn in iter_functions(self.namespaces):
            if fn.__qualname__ == qualname:
                owner = self.owner_of_file(fn.__code__.co_filename, getattr(fn.__code__, "co_firstlineno", None))
                if owner:
                    owners.add(owner)
        return next(iter(owners)) if len(owners) == 1 else None

    def blame(self, exc: BaseException, tb=None) -> "Optional[Blame]":
        """The mod an uncaught exception is the fault of, or None when it cannot be told."""
        tb = tb if tb is not None else exc.__traceback__
        frames = [(f.f_code.co_filename, line, f.f_code.co_name) for f, line in traceback.walk_tb(tb)]
        if frames:
            filename, line, func = frames[-1]
            owner = self.owner_of_file(filename, line)
            if owner:
                return Blame(owner, f"the error was raised in {func}() at {os.path.basename(filename)}:{line}, code of this mod")
        if isinstance(exc, TypeError):
            bound = _BINDING.match(str(exc))
            if bound:
                owner = self.owner_of_function(bound.group("q"))
                if owner:
                    return Blame(owner, f"{bound.group('q')}() is this mod's function and the game called it with arguments it does not take")
        if len(frames) >= 2:
            filename, line, func = frames[-2]
            owner = self.owner_of_file(filename, line)
            if owner:
                return Blame(owner, f"{func}() of this mod called the function that raised")
        return None


# A call whose arguments do not fit raises in the CALLER (no frame of the callee exists), and the message names the callee.
_BINDING = re.compile(r"^(?P<q>[A-Za-z_][\w.<>]*)\(\) (?:takes|got|missing)\b")


@dataclass
class Blame:
    mod: str
    how: str


# -- layer 1: do the mods' wrappers fit the game's functions ------------------------------------------------

def _function_of(obj) -> "Optional[types.FunctionType]":
    if isinstance(obj, types.FunctionType):
        return obj
    if isinstance(obj, (staticmethod, classmethod)) and isinstance(obj.__func__, types.FunctionType):
        return obj.__func__
    return None


def iter_functions(namespaces: Sequence[dict] = ()) -> "Iterator[tuple]":
    """Every function defined in a loaded module, in a class of one, or in one of ``namespaces`` (a dict: the game's Client.py
    runs as ``__main__`` in a namespace of its own that sys.modules does not hold). Yields ``(container, where, name, raw, function)``
    where ``container`` is a module, a class or a dict, ``raw`` is what it holds (a staticmethod stays one)."""
    seen_classes: "set[int]" = set()

    def from_dict(container, where: str, table: dict):
        for name, obj in list(table.items()):
            fn = _function_of(obj)
            if fn is not None:
                yield container, where, name, obj, fn
            elif isinstance(obj, type) and id(obj) not in seen_classes and obj.__module__ in (table.get("__name__"), where):
                seen_classes.add(id(obj))
                for member, value in list(vars(obj).items()):
                    inner = _function_of(value)
                    if inner is not None:
                        yield obj, f"{where}.{obj.__name__}", member, value, inner

    for modname, module in list(sys.modules.items()):
        table = getattr(module, "__dict__", None)
        if isinstance(table, dict):
            yield from from_dict(module, modname, table)
    for table in namespaces:
        yield from from_dict(table, str(table.get("__name__", "<namespace>")), table)


def signature_problem(wrapper: Callable, original: Callable) -> str:
    """Empty when ``wrapper`` can take every call ``original`` can; else why not. ``inspect.signature`` follows ``__wrapped__``
    by default and would report the ORIGINAL's signature for the wrapper, hiding exactly the mismatch looked for."""
    try:
        w = inspect.signature(wrapper, follow_wrapped=False)
        o = inspect.signature(original, follow_wrapped=False)
    except (TypeError, ValueError):
        return ""
    wp, op = list(w.parameters.values()), list(o.parameters.values())
    has_var_pos = any(p.kind is p.VAR_POSITIONAL for p in wp)
    has_var_kw = any(p.kind is p.VAR_KEYWORD for p in wp)
    w_pos = [p for p in wp if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    w_names = {p.name for p in wp if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
    problems = []
    o_pos = [p for p in op if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    if len(o_pos) > len(w_pos) and not has_var_pos:
        problems.append(f"the game's function takes {len(o_pos)} positional arguments, the wrapper only {len(w_pos)}")
    for p in op:
        if p.kind is p.KEYWORD_ONLY and p.name not in w_names and not has_var_kw:
            problems.append(f"the wrapper does not take the keyword '{p.name}'")
        elif p.kind is p.VAR_POSITIONAL and not has_var_pos:
            problems.append("the game's function takes *args, the wrapper does not")
        elif p.kind is p.VAR_KEYWORD and not has_var_kw:
            problems.append("the game's function takes **kwargs, the wrapper does not")
    original_names = {p.name for p in op}
    original_has_var_kw = any(p.kind is p.VAR_KEYWORD for p in op)
    for p in wp:
        if (p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY) and p.default is p.empty
                and p.name not in original_names and not original_has_var_kw):
            problems.append(f"the wrapper requires '{p.name}', which the game's function does not take")
    return "; ".join(problems)


@dataclass
class Problem:
    mod: str
    where: str                      # "hangar_inventory.ship_inventory_view"
    qualname: str
    why: str
    container: object = None
    name: str = ""
    raw: object = None
    original: object = None

    def reason(self) -> str:
        return f"its wrapper {self.qualname} (installed as {self.where}) cannot take every call the game makes: {self.why}"


def check_wrappers(attributor: Attributor, namespaces: Sequence[dict] = ()) -> "list[Problem]":
    """The mods' wrappers (a function carrying ``__wrapped__`` whose code is a mod's) that cannot take every call the function
    they wrap takes."""
    problems: "list[Problem]" = []
    for container, where, name, raw, fn in iter_functions(list(namespaces)):
        inner = getattr(fn, "__wrapped__", None)
        if inner is None or not callable(inner):
            continue
        if os.path.basename(fn.__code__.co_filename).startswith("test_"):
            continue                                            # a wrapper a test of the mod made: not what the client runs
        owner = attributor.owner_of_file(fn.__code__.co_filename, fn.__code__.co_firstlineno)
        if owner is None:
            continue
        why = signature_problem(fn, inner)
        if why:
            problems.append(Problem(owner, f"{where}.{name}", fn.__qualname__, why, container, name, raw, inner))
    return problems


def restore(problem: Problem) -> bool:
    """Put the game's own function back where a mod's wrapper replaced it (the last resort when the game cannot restart)."""
    try:
        original = problem.original
        if isinstance(problem.raw, staticmethod):
            original = staticmethod(original)
        elif isinstance(problem.raw, classmethod):
            original = classmethod(original)
        if isinstance(problem.container, dict):
            problem.container[problem.name] = original
        else:
            setattr(problem.container, problem.name, original)
        return True
    except Exception:
        return False


# -- the session: what the last runs left behind ---------------------------------------------------------------

@dataclass
class Session:
    root: Path
    internal: Path
    game: str
    auto_off: "dict[str, str]" = field(default_factory=dict)      # mod -> why, in effect for this start
    safe_mode: bool = False
    notes: "list[str]" = field(default_factory=list)              # news for the player: what was switched off since last time
    crashed: bool = False                                         # a fatal crash was recorded in this run


def start_session(mods: Sequence, root: Path, internal: Path) -> Session:
    """Read what earlier runs left: the mods that are switched off (records whose game or mod has changed since are dropped:
    they get another try) and the crash count (``SAFE_MODE_AFTER`` unexplained crashes in a row = safe mode, once)."""
    root, internal = Path(root), Path(internal)
    session = Session(root, internal, game_fingerprint(internal))
    path = root / AUTO_OFF_FILE
    stored = _read_json(path)
    records = stored.get("mods") if isinstance(stored.get("mods"), dict) else {}
    by_name = {m.name: m for m in mods}
    kept: dict = {}
    for name, record in records.items():
        mod = by_name.get(name)
        if mod is None or not isinstance(record, dict):
            continue                                            # the mod is gone: nothing to remember
        if record.get("game") != session.game or record.get("mod") != mod_fingerprint(mod.path):
            LOG.info("mod %s gets another try: the game or the mod changed since it was switched off", name)
            continue
        kept[name] = record
        reason = str(record.get("reason", "it caused a problem"))
        session.auto_off[name] = reason
        if not record.get("announced"):
            session.notes.append(f"{name}: {reason}")
            record["announced"] = True
    if kept != records or session.notes:
        _write_json(path, {"version": 1, "mods": kept})

    crash_path = root / CRASH_FILE
    state = _read_json(crash_path)
    if int(state.get("streak", 0) or 0) >= SAFE_MODE_AFTER:
        session.safe_mode = True
        state["streak"] = 0
        _write_json(crash_path, state)
        session.notes.insert(0, f"the game crashed {SAFE_MODE_AFTER} times in a row and no mod could be blamed, so every mod is off for "
                                f"this start; start the game again to use them (if it runs well now, one of them is the cause: "
                                f"switch them off one at a time in the mod picker)")
    return session


def switch_off(session: Session, mod, reason: str, source: str) -> None:
    """Remember that ``mod`` (a loader Mod) is off until the game or the mod changes."""
    path = session.root / AUTO_OFF_FILE
    stored = _read_json(path)
    records = stored.get("mods") if isinstance(stored.get("mods"), dict) else {}
    records[mod.name] = {"reason": reason, "source": source, "when": _now(), "game": session.game,
                         "mod": mod_fingerprint(mod.path), "announced": False}
    _write_json(path, {"version": 1, "mods": records})


# -- restarting ---------------------------------------------------------------------------------------------

def restarts_so_far(env=None) -> int:
    try:
        return int((os.environ if env is None else env).get(ENV_RESTARTS, "0") or 0)
    except ValueError:
        return 0


def restart(launch: "Optional[Sequence[str]]" = None, cwd: "Optional[str]" = None, env=None, popen=None,
            exit_process: "Optional[Callable[[int], None]]" = None) -> bool:
    """Start this client again and end this process, which has opened nothing yet. False (and this process carries on) when
    ``MAX_RESTARTS`` were used, the launch command is not known or points at nothing, it cannot be started, or the new process
    is gone again within a moment: this process only ends when the new one is really running."""
    environment = dict(os.environ if env is None else env)
    count = restarts_so_far(environment)
    command = list(launch) if launch else _LAUNCH
    if count >= MAX_RESTARTS or not command:
        return False
    if len(command) > 1 and not os.path.isfile(command[1]):
        return False                                            # a relative script path the launch scripts' chdir has made wrong
    environment[ENV_RESTARTS] = str(count + 1)
    environment[ENV_NO_UPDATE] = "1"
    try:
        flags = (_DETACHED | _NEW_GROUP | _NO_WINDOW) if os.name == "nt" else 0
        child = (popen or subprocess.Popen)(command, cwd=cwd or _LAUNCH_CWD, env=environment, creationflags=flags, close_fds=True,
                                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if child is not None and hasattr(child, "wait"):
            try:
                child.wait(timeout=0.5)
                return False                                    # it ended at once: it cannot start the game, so this one must
            except subprocess.TimeoutExpired:
                pass
    except Exception:
        return False
    (exit_process or os._exit)(0)
    return True


def message_box(text: str, title: str = "Star Empire mods") -> None:
    try:                                                        # pythonw has no console: say it where it cannot be missed
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x30)
    except Exception:
        pass


# -- layers 2 and 3: the guard that runs with the game ----------------------------------------------------------

class _CrashCatcher(logging.Handler):
    """Listens on the game's ``crash_log`` logger (the game replaces the ROOT logger's handlers when it installs its crash
    logging, a handler on its own logger survives that) for the CRITICAL record of an uncaught exception that ends the game."""

    def __init__(self, guard: "Guard"):
        super().__init__(logging.CRITICAL)
        self.guard = guard

    def emit(self, record: logging.LogRecord) -> None:
        try:
            info = record.exc_info
            if not info or info[1] is None or record.threadName not in FATAL_THREADS:
                return
            self.guard.record_crash(info[1], info[2], record.threadName)
        except Exception:
            pass


class Guard:
    def __init__(self, session: Session, mods: Sequence, internal: Path, spans: "Optional[dict]" = None):
        self.session = session
        self.mods = {m.name: m for m in mods}
        self.attributor = Attributor(mods, internal, spans)
        self.started = time.time()
        self._lock = threading.Lock()
        self._catcher: "Optional[_CrashCatcher]" = None
        self.switched_off: "Optional[str]" = None               # the mod the recorded crash switched off, if any
        self.launch: "Optional[list[str]]" = None               # tests
        self.popen = None
        self.exit_process: "Optional[Callable[[int], None]]" = None

    # -- before the window opens
    def after_import(self, namespaces: Sequence[dict] = ()) -> None:
        """The game's code and the mods' hooks are loaded and nothing is open: check the wrappers, then listen for crashes."""
        self.attributor.namespaces = list(namespaces)
        try:
            problems = check_wrappers(self.attributor, namespaces)
            if problems:
                self._handle_problems(problems)
        except Exception:
            LOG.exception("modguard: the wrapper check failed (ignored)")
        self.arm()

    def _handle_problems(self, problems: "list[Problem]") -> None:
        by_mod: "dict[str, list[Problem]]" = {}
        for p in problems:
            by_mod.setdefault(p.mod, []).append(p)
        names = []
        for name, items in by_mod.items():
            mod = self.mods.get(name)
            if mod is None:
                continue
            switch_off(self.session, mod, items[0].reason(), "start-up check")
            names.append(name)
            LOG.warning("mod %s switched off: %s", name, items[0].reason())
        if not names:
            return
        if restart(self.launch, None, None, self.popen, self.exit_process):
            return                                              # (a test's exit_process returns; the real one does not)
        # no restart possible: put the game's own functions back so this run still works
        undone = [p for p in problems if p.mod in names and restore(p)]
        message_box("These mods do not fit this version of the game and were switched off for this run:\n\n" + ", ".join(names)
                    + (f"\n\nThe game's own functions were put back ({len(undone)})." if undone else "")
                    + "\n\nThey come back when the game or the mod changes.")

    def arm(self) -> None:
        """Start listening for the crash that would end the game; a clean end resets the crash count."""
        os.environ.pop(ENV_RESTARTS, None)                      # this run started; a later relaunch counts from zero
        if self._catcher is None:
            self._catcher = _CrashCatcher(self)
            logging.getLogger(CRASH_LOGGER).addHandler(self._catcher)
            atexit.register(self._clean_end)

    def disarm(self) -> None:
        if self._catcher is not None:
            logging.getLogger(CRASH_LOGGER).removeHandler(self._catcher)
            self._catcher = None

    # -- when it ends
    def record_crash(self, exc: BaseException, tb=None, thread: str = "") -> "Optional[Blame]":
        """The game is ending on ``exc``: write down who is to blame, once per run."""
        with self._lock:
            if self.session.crashed:
                return None
            self.session.crashed = True
        blame = None
        try:
            blame = self.attributor.blame(exc, tb)
        except Exception:
            LOG.exception("modguard: attribution failed")
        frames = list(traceback.walk_tb(tb if tb is not None else exc.__traceback__))
        where = ""
        if frames:
            frame, line = frames[-1]
            where = f"{os.path.basename(frame.f_code.co_filename)}:{line} in {frame.f_code.co_name}"
        what = f"{type(exc).__name__}: {str(exc)[:300]}"
        path = self.session.root / CRASH_FILE
        state = _read_json(path)
        mod = self.mods.get(blame.mod) if blame else None
        if mod is not None and blame.mod not in self.session.auto_off:
            switch_off(self.session, mod, f"it crashed the game ({what}; {blame.how})", "crash")
            self.switched_off = blame.mod
            state["streak"] = 0
        else:
            state["streak"] = int(state.get("streak", 0) or 0) + 1
        entry = {"when": _now(), "thread": thread or threading.current_thread().name, "exception": what, "where": where,
                 "culprit": blame.mod if blame else None, "how": blame.how if blame else None,
                 "seconds_into_run": round(time.time() - self.started)}
        history = [e for e in state.get("history", []) if isinstance(e, dict)][-(HISTORY - 1):] + [entry]
        state.update({"version": 1, "last": entry, "history": history})
        _write_json(path, state)
        return blame

    def on_failure(self, exc: BaseException, starting: bool) -> None:
        """An exception left the game's own code (``starting``: while its modules were still being loaded, before the main
        block). One that a mod caused is written down and, while starting, the game restarts without that mod."""
        try:
            self.record_crash(exc, exc.__traceback__, threading.current_thread().name)
            if starting and self.switched_off:
                restart(self.launch, None, None, self.popen, self.exit_process)
        except Exception:
            LOG.exception("modguard: could not record the failure")

    def _clean_end(self) -> None:
        if self.session.crashed:
            return
        path = self.session.root / CRASH_FILE
        state = _read_json(path)
        if int(state.get("streak", 0) or 0):
            state["streak"] = 0
            _write_json(path, state)


# -- running the game's own file in two parts ------------------------------------------------------------------

_ENTRY = re.compile(rb'^if __name__ == (["\'])__main__\1:[ \t]*(#.*)?\r?$', re.M)
_FUTURE_ANNOTATIONS = re.compile(rb"^from __future__ import [^\n]*\bannotations\b", re.M)


def split_entry(data: bytes) -> "Optional[tuple[bytes, bytes, int]]":
    """Cut a game file at its last top-level ``if __name__ == "__main__":`` into ``(head, tail, compiler flags)``: the head
    defines everything (and the mods' hooks run), the tail starts the game. The tail is padded with empty lines so its line numbers
    are the file's own. None when the file has no such block (it is then run in one piece, unchecked)."""
    found = list(_ENTRY.finditer(data))
    if not found:
        return None
    cut = found[-1].start()
    head, tail = data[:cut], data[cut:]
    flags = 0
    if _FUTURE_ANNOTATIONS.search(head):
        import __future__
        flags = __future__.annotations.compiler_flag
    return head, b"\n" * head.count(b"\n") + tail, flags

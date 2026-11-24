"""modloader.py - apply the mods in ``mods/`` to the game's own files, in memory, as the client starts.

The game has no mod API, so a mod that has to change the dev's code ships a *patch* instead of an edit
inside the dev's file. ``mods/<name>/<File>.py.patch`` says "after this line of the game's code, add these
lines" or "replace exactly these lines with these"; this module finds each place by the text around it,
builds the patched source in memory and hands it to Python in place of the file on disk, which stays the
dev's, untouched. An update that changes the code next to a hook then costs one anchor to re-point, not a
merge, and an update that moves a hook's anchor switches that ONE mod off with a clear message while the
game starts without it.

Patch file format (a unified diff without line numbers)::

    anything before the first header is a free-text note for humans
    @@ label shown in messages @@
     a context line (one leading space; a blank context line is a single space)
    -a line that is replaced or deleted
    +a line that is added
     a context line
    @@ another hunk @@ order=20

``order=N`` (optional, after the closing ``@@``) sets where this hunk's lines land among other hunks, of any
mod, that insert at the very same spot: lower numbers first, then mod order, then position in the file.
Hunks without it come after those with one, so a new hook at a busy spot just goes last.
``eof`` (also after the closing ``@@``) says the hunk's old lines must be the very end of the file: use it for a
hook that closes a class by adding a line at column 0 after the last method; if the dev ever adds code below
those lines, the hook would land in the middle of the class, so the mod is switched off instead.

* The hunk's old side (context + ``-`` lines, in order) must match the game's file exactly ONCE, comparing
  lines with trailing whitespace ignored. No match = "anchor not found"; more than one = "ambiguous".
  Line numbers are not used, so lines added elsewhere in the file do not matter.
* A hunk with no ``-`` lines is an insertion between its leading and trailing context.
* Insertions at the same spot keep (order=, mod order, position in the patch file) order.
* A blank context line is a single space; an empty line (an editor stripped the space) between two lines of a
  hunk counts as one too. Empty lines before the next header or the end of the file are just separators.``\\r`` is ignored too (the files are read as text).

``mods/<name>/mod.json`` (optional)::

    {"description": "...", "order": 0, "requires": ["other_mod"], "enabled": true}

A mod folder also holds the mod's own code: its modules (``chat_suggest.py``) and their tests
(``test_chat_suggest.py``). Every mod folder with ``*.py`` files is put on ``sys.path``, so the game's patched
code just says ``import chat_suggest``. A folder with no patch at all is a library for other mods (a mod is
"switched off" only when its patches cannot be applied; its modules stay importable either way).

Every hunk is resolved against the PRISTINE game file first. A mod with any hunk that fails (in any file), or
whose edits overlap an earlier mod's, is switched off as a whole; a mod that ``requires`` a switched-off mod
is switched off too; the rest are applied together. Mods apply in (order, name) order, which decides who wins
when two mods want the same lines.

The patched source is compiled with the game file's own name (``__file__`` and asset paths keep working),
registered with ``linecache`` so tracebacks show the lines that really ran, and cached as marshalled code
under ``custom_build/mod_cache`` keyed by the patched text, so a normal start does not recompile.

Switches: ``STAR_EMPIRE_NO_MODS=1`` runs the game as the dev shipped it; ``STAR_EMPIRE_DISABLE_MODS=a,b`` turns
named mods off (is this bug mine or the dev's?).

Beyond patches (modguard.py, on for a real client start, ``STAR_EMPIRE_NO_MODGUARD=1`` turns it off): Client.py runs in two
pieces so the mods' wrappers can be checked against the game's functions before the window opens (a wrapper that cannot take
what the game passes switches its mod off and restarts the game), a crash that ends the game is pinned on its mod when it can
be and that mod stays off until the game or the mod changes (``mods_auto_off.json``), and two unexplained crashes in a row
start the game once with every mod off.

``python modloader.py`` prints the report and exits 1 when a mod is switched off (use it after an update).
"""

from __future__ import annotations

import functools
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
import linecache
import logging
import marshal
import os
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping, Optional, Sequence

LOG = logging.getLogger("modloader")

try:                                    # modguard.py reads the command that started this process when it is imported: do it before the launch scripts change directory
    import modguard                     # noqa: F401
except Exception:                       # a package without it, or one that cannot load: the game starts without the safety net
    pass

ENV_OFF = "STAR_EMPIRE_NO_MODS"
ENV_DISABLE = "STAR_EMPIRE_DISABLE_MODS"
ENV_CACHE = "STAR_EMPIRE_MOD_CACHE"

ENV_GAME = "STAR_EMPIRE_GAME_DIR"
GAME_DIR_FILE = "game_dir.txt"


def find_layout(here: "Optional[Path]" = None, env: "Optional[Mapping[str, str]]" = None) -> "tuple[Path, Path, Path]":
    """Where things are: ``(the game's _internal folder, the mods' root, the folder scan_data/ lives in)``.

    Two layouts work. The loader can sit INSIDE the game's own ``_internal/`` (next to ``Client.py``; ``mods/`` is then
    next to ``_internal/``, and ``scan_data/`` is shared one level above, with sibling tools). Or it is the stand-alone
    package: ``<root>/modloader/modloader.py`` with ``mods/`` beside it in ``<root>``, and the game found by, in this
    order, the ``STAR_EMPIRE_GAME_DIR`` environment variable, the first line of ``<root>/game_dir.txt``, or ``<root>``
    itself (the package was dropped into the game's folder, next to ``Client.exe``)."""
    here = Path(here) if here is not None else Path(__file__).resolve().parent
    env = os.environ if env is None else env
    if (here / "Client.py").is_file() and not env.get(ENV_GAME):
        return here, here.parent, here.parent.parent
    root = here.parent
    game = (env.get(ENV_GAME) or "").strip()
    if not game:
        pointer = root / GAME_DIR_FILE
        try:
            lines = pointer.read_text(encoding="utf-8-sig").splitlines() if pointer.is_file() else []
        except OSError:
            lines = []
        game = next((l.strip().strip('"') for l in lines if l.strip() and not l.lstrip().startswith("#")), "")
    game_dir = Path(game) if game else root
    return game_dir / "_internal", root, root


INTERNAL_DIR, REPO_DIR, PROJECT_DIR = find_layout()
# INTERNAL_DIR: the game's files (Client.py ...), which stay as the dev shipped them. REPO_DIR: where mods/ lives and the
# *.json settings and mods_report.txt are written. PROJECT_DIR: where scan_data/ lives.
MODS_DIR = REPO_DIR / "mods"
REPORT_FILE = REPO_DIR / "mods_report.txt"

PATCH_SUFFIX = ".py.patch"
DEFAULT_PRIORITY = 1_000_000            # a hunk without order=N goes after every hunk that has one
_BOM = b"\xef\xbb\xbf"


class PatchError(Exception):
    """A patch file is malformed, or an anchor does not match the game's file."""


# -- patch files ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Hunk:
    mod: str
    file: str                       # the game file it patches, e.g. "Client.py"
    label: str
    before: "tuple[str, ...]"       # context lines before the change
    removed: "tuple[str, ...]"      # lines replaced or deleted
    added: "tuple[str, ...]"
    after: "tuple[str, ...]"        # context lines after the change
    origin: str                     # "mods/foo/Client.py.patch:123" for messages
    seq: int = 0                    # position within its mod (the order insertions at one spot keep)
    priority: float = DEFAULT_PRIORITY  # order=N: sequence among insertions at the same spot (3.5 fits between 3 and 4)
    at_end: bool = False                # eof: the old side must be the end of the file (a hook that closes a class)

    @property
    def old(self) -> "tuple[str, ...]":
        return self.before + self.removed + self.after

    @property
    def where(self) -> str:
        return f"{self.origin} [{self.label}]" if self.label else self.origin


def parse_patch(text: str, mod: str, file: str, origin: str = "") -> "list[Hunk]":
    """The hunks of one patch file. Raises PatchError on a line that is not part of the format."""
    hunks: "list[Hunk]" = []
    cur: "Optional[dict]" = None

    def finish() -> None:
        nonlocal cur
        if cur is None:
            return
        lines = cur["lines"]
        first = next((i for i, (k, _t) in enumerate(lines) if k != " "), None)
        last = next((i for i in range(len(lines) - 1, -1, -1) if lines[i][0] != " "), None)
        if first is None:                       # context only: nothing to do
            raise PatchError(f"{cur['origin']}: the hunk changes nothing")
        before = tuple(t for _k, t in lines[:first])
        after = tuple(t for _k, t in lines[last + 1:])
        body = lines[first:last + 1]
        removed = tuple(t for k, t in body if k == "-")
        added = tuple(t for k, t in body if k == "+")
        # context in the middle of a changed region would make "replace" ambiguous: the format is
        # before / (- and + lines) / after, so a ' ' line between two changes is a hunk of its own.
        if any(k == " " for k, _t in body):
            raise PatchError(f"{cur['origin']}: a context line sits between two changes; "
                             f"split the hunk or turn that line into '-' and '+'")
        if not (before or removed or after):
            raise PatchError(f"{cur['origin']}: an insertion needs at least one context line")
        hunks.append(Hunk(mod, file, cur["label"], before, removed, added, after, cur["origin"], len(hunks),
                          cur["priority"], cur["at_end"]))
        cur = None

    for number, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        here = f"{origin}:{number}" if origin else f"line {number}"
        if raw.startswith("@@"):
            finish()
            body = raw[2:]
            close = body.rfind("@@")
            label, tail = (body[:close], body[close + 2:]) if close >= 0 else (body, "")
            priority, at_end = DEFAULT_PRIORITY, False
            for token in tail.split():
                m = re.fullmatch(r"order=(-?\d+(?:\.\d+)?)", token)
                if m is not None:
                    priority = float(m.group(1)) if "." in m.group(1) else int(m.group(1))
                elif token == "eof":
                    at_end = True
                else:
                    raise PatchError(f"{here}: after the closing @@ only 'order=N' and 'eof' are allowed "
                                     f"(got {tail.strip()!r})")
            cur = {"label": label.strip(), "origin": here, "lines": [], "priority": priority, "at_end": at_end}
            continue
        if cur is None:
            continue                            # a free-text note before the first header
        if raw == "":
            cur["pending"] = cur.get("pending", 0) + 1     # a separator, or a blank context line an editor stripped
            continue
        kind, body = raw[0], raw[1:]
        if kind not in " +-":
            raise PatchError(f"{here}: a hunk line must start with ' ', '+' or '-' (got {raw[:20]!r})")
        # empty lines that are followed by more of the hunk were blank context lines (editors strip the one
        # space); empty lines before the next header or the end of the file are only separators
        cur["lines"].extend([(" ", "")] * cur.pop("pending", 0))
        cur["lines"].append((kind, body))
    finish()
    return hunks


# -- mods ----------------------------------------------------------------------------------------------

@dataclass
class Mod:
    name: str
    path: Path
    description: str = ""
    order: int = 0
    requires: "tuple[str, ...]" = ()
    enabled: bool = True
    hunks: "list[Hunk]" = field(default_factory=list)
    error: str = ""                 # the mod folder itself could not be read

    @property
    def files(self) -> "list[str]":
        return sorted({h.file for h in self.hunks})


def load_mods(mods_dir: "Optional[Path]" = None) -> "list[Mod]":
    """Every mod folder under ``mods_dir`` in (order, name) order. A broken folder becomes a Mod with
    ``error`` set (it will be reported and switched off), never an exception."""
    mods_dir = Path(mods_dir) if mods_dir is not None else MODS_DIR
    mods: "list[Mod]" = []
    if not mods_dir.is_dir():
        return mods
    for folder in sorted(p for p in mods_dir.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
        mod = Mod(folder.name, folder)
        try:
            meta_file = folder / "mod.json"
            if meta_file.is_file():
                meta = json.loads(meta_file.read_text(encoding="utf-8-sig"))
                if not isinstance(meta, dict):
                    raise ValueError("mod.json must hold an object")
                mod.description = str(meta.get("description", ""))
                mod.order = int(meta.get("order", 0))
                mod.requires = tuple(str(r) for r in meta.get("requires", ()))
                mod.enabled = bool(meta.get("enabled", True))
            for patch in sorted(folder.glob("*" + PATCH_SUFFIX)):
                target = patch.name[:-len(".patch")]
                origin = f"mods/{folder.name}/{patch.name}"
                base = len(mod.hunks)
                mod.hunks.extend(
                    replace(h, seq=base + i)
                    for i, h in enumerate(parse_patch(patch.read_text(encoding="utf-8-sig"),
                                                      folder.name, target, origin)))
        except (OSError, ValueError, PatchError) as exc:
            mod.error = f"{type(exc).__name__}: {exc}"
        mods.append(mod)
    mods.sort(key=lambda m: (m.order, m.name))
    return mods


# -- game files ----------------------------------------------------------------------------------------

@dataclass
class SourceText:
    """A game file as a list of lines plus what is needed to write it back byte for byte."""
    lines: "list[str]"              # without terminators; the last item is "" when the file ends with one
    eol: str
    bom: bool

    @classmethod
    def from_bytes(cls, data: bytes) -> "SourceText":
        bom = data.startswith(_BOM)
        text = data[len(_BOM):].decode("utf-8") if bom else data.decode("utf-8")
        crlf, lf = text.count("\r\n"), text.count("\n")
        if crlf and crlf != lf:
            raise PatchError("the file mixes CRLF and LF line endings")
        eol = "\r\n" if crlf else "\n"
        if eol == "\r\n":
            text = text.replace("\r\n", "\n")
        return cls(text.split("\n"), eol, bom)

    def to_bytes(self, lines: "Optional[Sequence[str]]" = None) -> bytes:
        text = "\n".join(self.lines if lines is None else lines)
        if self.eol != "\n":
            text = text.replace("\n", self.eol)
        data = text.encode("utf-8")
        return _BOM + data if self.bom else data


@dataclass(frozen=True)
class Edit:
    mod: str
    file: str
    rank: int
    seq: int
    priority: float
    start: int                      # replace lines[start:end] ...
    end: int
    new: "tuple[str, ...]"          # ... with these
    where: str


class _Index:
    """Finds a run of lines in a game file by its first line (trailing whitespace ignored)."""

    def __init__(self, lines: "Sequence[str]"):
        self.norm = [l.rstrip() for l in lines]
        self.by_first: "dict[str, list[int]]" = {}
        for i, l in enumerate(self.norm):
            self.by_first.setdefault(l, []).append(i)

    def find(self, old: "Sequence[str]") -> "list[int]":
        want = [l.rstrip() for l in old]
        n = len(want)
        hits = []
        for i in self.by_first.get(want[0], ()):
            if self.norm[i:i + n] == want:
                hits.append(i)
        return hits


def resolve_hunk(index: _Index, hunk: Hunk, rank: int) -> Edit:
    """The edit a hunk makes to the pristine file. Raises PatchError when its anchor is not exactly one place."""
    old = hunk.old
    hits = index.find(old)
    if hits and hunk.at_end:
        # the match must end at the end of the file (index.norm ends with "" when the file ends with a newline)
        at_end = [h for h in hits if h + len(old) >= len(index.norm) - 1]
        if not at_end:
            raise PatchError(f"{hunk.file}: {hunk.where} is meant for the very end of the file, but the game's code now "
                             f"continues after those {len(old)} line(s): the dev added something below them")
        hits = at_end
    if not hits:
        first = hunk.before[0] if hunk.before else (hunk.removed[0] if hunk.removed else hunk.after[0])
        raise PatchError(f"{hunk.file}: anchor not found for {hunk.where}: the game's code no longer has "
                         f"{len(old)} line(s) starting {first.strip()[:70]!r}")
    if len(hits) > 1:
        at = ", ".join(str(h + 1) for h in hits[:6])
        raise PatchError(f"{hunk.file}: anchor is ambiguous for {hunk.where}: it matches {len(hits)} places "
                         f"(lines {at}); give the hunk more context")
    start = hits[0] + len(hunk.before)
    return Edit(hunk.mod, hunk.file, rank, hunk.seq, hunk.priority, start, start + len(hunk.removed),
                hunk.added, hunk.where)


def _conflicts(a: Edit, b: Edit) -> bool:
    """Two edits to one file cannot both be applied: their replaced lines overlap, or one inserts inside the
    lines the other replaces. Insertions at the same spot are fine (they keep mod order)."""
    if a.start < a.end and b.start < b.end:
        return a.start < b.end and b.start < a.end
    for ins, rep in ((a, b), (b, a)):
        if ins.start == ins.end and rep.start < rep.end and rep.start < ins.start < rep.end:
            return True
    return False


# -- building ------------------------------------------------------------------------------------------

@dataclass
class BuildResult:
    applied: "list[str]" = field(default_factory=list)              # mods in effect, in order
    failed: "dict[str, list[str]]" = field(default_factory=dict)    # mod -> why it was switched off
    skipped: "list[str]" = field(default_factory=list)              # switched off on purpose
    sources: "dict[str, bytes]" = field(default_factory=dict)       # game file -> patched bytes
    vanilla: "dict[str, bytes]" = field(default_factory=dict)       # game file -> the dev's bytes
    spans: "dict[str, list[tuple[int, int, str]]]" = field(default_factory=dict)   # game file -> (first line, last line, mod) of every line a patch added, numbered as in the PATCHED file
    auto_off: "dict[str, str]" = field(default_factory=dict)        # mod -> why it was switched off after a problem (modguard.py); also in ``failed``

    @property
    def ok(self) -> bool:
        return not self.failed

    def report(self) -> str:
        lines = [f"mods in effect ({len(self.applied)}): " + (", ".join(self.applied) or "none")]
        if self.skipped:
            lines.append("switched off on purpose: " + ", ".join(self.skipped))
        for name, reasons in self.failed.items():
            lines.append(f"SWITCHED OFF  {name}"
                         + (" (automatically; it comes back when the game or the mod changes)" if name in self.auto_off else "") + ":")
            lines.extend(f"    {r}" for r in reasons)
        return "\n".join(lines)


def build(mods: "Sequence[Mod]", read_game_file, disabled: "Sequence[str]" = ()) -> BuildResult:
    """Resolve every mod against the pristine game files and apply the ones that hold together.

    ``read_game_file(name) -> bytes`` returns a game file (raise OSError when it is missing).
    """
    result = BuildResult()
    off = set(disabled)
    texts: "dict[str, Optional[SourceText]]" = {}
    indexes: "dict[str, _Index]" = {}
    problems: "dict[str, list[str]]" = {}
    edits: "dict[str, list[Edit]]" = {}          # per mod, every edit (all files)

    def source(name: str) -> "SourceText":
        if name not in texts:
            try:
                data = read_game_file(name)
                result.vanilla[name] = data
                texts[name] = SourceText.from_bytes(data)
            except (OSError, PatchError, UnicodeDecodeError) as exc:
                texts[name] = None
                raise PatchError(f"{name}: cannot read the game file ({exc})") from exc
        if texts[name] is None:
            raise PatchError(f"{name}: cannot read the game file")
        return texts[name]                      # type: ignore[return-value]

    ranks = {m.name: i for i, m in enumerate(mods)}
    for mod in mods:
        if not mod.enabled or mod.name in off:
            result.skipped.append(mod.name)
            continue
        if mod.error:
            problems[mod.name] = [mod.error]
            continue
        mine: "list[Edit]" = []
        why: "list[str]" = []
        for hunk in mod.hunks:
            try:
                src = source(hunk.file)
                if hunk.file not in indexes:
                    indexes[hunk.file] = _Index(src.lines)
                edit = resolve_hunk(indexes[hunk.file], hunk, ranks[mod.name])
                mine.append(edit)
            except PatchError as exc:
                why.append(str(exc))
        if why:
            problems[mod.name] = why
        else:
            edits[mod.name] = mine

    # a mod that requires a mod that is off, missing or broken is off too
    changed = True
    while changed:
        changed = False
        for mod in mods:
            if mod.name in edits and mod.name not in problems:
                for need in mod.requires:
                    if need not in edits or need in problems:
                        edits.pop(mod.name, None)
                        problems[mod.name] = [f"needs mod {need!r}, which is not in effect"]
                        changed = True
                        break

    # accept mods in order; one whose edits collide with an earlier mod's is switched off
    accepted: "dict[str, list[Edit]]" = {}
    for mod in mods:
        mine = edits.get(mod.name)
        if mine is None:
            continue
        clash = None
        for e in mine:
            for other in accepted.get(e.file, ()):
                if _conflicts(e, other):
                    clash = f"{e.where} overlaps {other.where} (mod {other.mod!r})"
                    break
            if clash:
                break
        if clash:
            problems[mod.name] = [clash]
            continue
        for e in mine:
            accepted.setdefault(e.file, []).append(e)
        result.applied.append(mod.name)

    for mod in mods:
        if mod.name in problems:
            result.failed[mod.name] = problems[mod.name]

    for name, file_edits in accepted.items():
        src = source(name)
        file_edits.sort(key=lambda e: (e.start, e.end, e.priority, e.rank, e.seq))
        out: "list[str]" = []
        pos = 0
        for e in file_edits:
            out.extend(src.lines[pos:e.start])
            if e.new:
                result.spans.setdefault(name, []).append((len(out) + 1, len(out) + len(e.new), e.mod))
            out.extend(e.new)
            pos = e.end
        out.extend(src.lines[pos:])
        result.sources[name] = src.to_bytes(out)
    return result


# -- feeding the patched source to Python -------------------------------------------------------------

def _cache_dir() -> Path:
    override = os.environ.get(ENV_CACHE)
    if override:
        return Path(override)
    build_dir = REPO_DIR / "custom_build"
    return build_dir / "mod_cache" if build_dir.is_dir() else REPO_DIR / ".mod_cache"


def _cache_key(data: bytes, path: str = "") -> str:
    """The patched text, the interpreter and the FILE NAME the code is compiled as: a code object carries its file name
    (tracebacks, ``inspect.getsource`` and the linecache entry all go by it), so code compiled for one folder must not be
    reused from another, which is what a copied or moved install would otherwise do with the cache folder it brings along."""
    h = hashlib.sha256()
    h.update(sys.version.encode("utf-8"))
    h.update(importlib.util.MAGIC_NUMBER)
    h.update(str(path).encode("utf-8", "replace"))
    h.update(b"\0")
    h.update(data)
    return h.hexdigest()[:24]


def compile_cached(name: str, path: str, data: bytes, cache_dir: "Optional[Path]" = None, flags: int = 0):
    """``compile`` the patched source (as file ``path``), reusing marshalled code from an earlier run. ``flags`` are compiler
    flags (a ``from __future__`` feature) for a piece of a file that does not carry its own import of it."""
    cache_dir = Path(cache_dir) if cache_dir is not None else _cache_dir()
    key = _cache_key(data, path + (f"\0{flags}" if flags else ""))
    entry = cache_dir / f"{Path(name).stem}-{key}.bin"
    try:
        return marshal.loads(entry.read_bytes())
    except (OSError, ValueError, EOFError, TypeError):
        pass
    code = compile(data[len(_BOM):] if data.startswith(_BOM) else data, path, "exec", flags=flags, dont_inherit=True)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = entry.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_bytes(marshal.dumps(code))
        os.replace(tmp, entry)
        for old in cache_dir.glob(f"{Path(name).stem}-*.bin"):
            if old != entry:
                try:
                    old.unlink()
                except OSError:
                    pass
    except OSError:
        pass                                    # a read-only folder only costs the next start a compile
    return code


def _register_lines(path: str, data: bytes) -> None:
    """Make tracebacks and inspect show the patched text, not the file on disk."""
    text = (data[len(_BOM):] if data.startswith(_BOM) else data).decode("utf-8")
    lines = text.replace("\r\n", "\n").splitlines(True)
    linecache.cache[path] = (len(text), None, lines, path)


class _PatchedLoader(importlib.abc.InspectLoader):
    def __init__(self, fullname: str, path: str, data: bytes, cache_dir: "Optional[Path]"):
        self.name, self.path, self._data, self._cache_dir = fullname, path, data, cache_dir

    def get_filename(self, fullname: str) -> str:
        return self.path

    def get_source(self, fullname: str) -> str:
        data = self._data[len(_BOM):] if self._data.startswith(_BOM) else self._data
        return data.decode("utf-8")

    def get_code(self, fullname: str):
        return compile_cached(Path(self.path).name, self.path, self._data, self._cache_dir)

    def is_package(self, fullname: str) -> bool:
        return False

    def create_module(self, spec):
        return None

    def exec_module(self, module) -> None:
        _register_lines(self.path, self._data)
        exec(self.get_code(module.__name__), module.__dict__)


class _Finder(importlib.abc.MetaPathFinder):
    def __init__(self, sources: "Mapping[str, bytes]", internal: Path, cache_dir: "Optional[Path]"):
        self.sources = {Path(n).stem: d for n, d in sources.items()}
        self.internal, self.cache_dir = internal, cache_dir

    def find_spec(self, fullname, path=None, target=None):
        data = self.sources.get(fullname)
        if data is None or path is not None:
            return None
        file = str(self.internal / f"{fullname}.py")
        return importlib.util.spec_from_file_location(
            fullname, file, loader=_PatchedLoader(fullname, file, data, self.cache_dir))


@dataclass
class Installed:
    result: BuildResult
    internal: Path
    cache_dir: "Optional[Path]"
    finder: "Optional[_Finder]" = None
    mods: "list[Mod]" = field(default_factory=list)       # every mod folder read, in order (the ESC menu's Mods page)
    switched_off_by_env: bool = False                      # STAR_EMPIRE_NO_MODS: the game runs as shipped
    safe_mode: bool = False                                # modguard: every mod left off for this start after repeated crashes
    guard: "Optional[object]" = None                       # modguard.Guard of a real client start (None in the tests)

    def run_main(self, name: str, namespace: dict) -> None:
        """Run a game file as ``__main__`` (how run_client.py starts Client.py), patched when it has mods.

        With the safety net on (a real client start, see modguard.py) the file runs in two pieces: everything above its
        ``if __name__ == "__main__":`` block (the game's definitions and the mods' hooks), then, once the mods' wrappers
        have been checked against the game's functions, the block that starts the game."""
        path = str(self.internal / name)
        data = self.result.sources.get(name)
        if data is None:
            data = (self.internal / name).read_bytes()
        namespace.setdefault("__file__", path)
        _register_lines(path, data)
        parts = None
        if self.guard is not None:
            try:
                import modguard
                parts = modguard.split_entry(data)
            except Exception:
                LOG.exception("modguard: %s cannot be run in two pieces (it runs in one, unchecked)", name)
        if parts is None:
            exec(compile_cached(name, path, data, self.cache_dir), namespace)
            return
        head, tail, flags = parts
        guard, stem = self.guard, Path(name).stem
        try:
            exec(compile_cached(f"{stem}_head.py", path, head, self.cache_dir, flags), namespace)
        except (SystemExit, KeyboardInterrupt, GeneratorExit):
            raise
        except BaseException as exc:
            guard.on_failure(exc, starting=True)               # a mod's hook that fails here: restart without that mod
            raise
        guard.after_import([namespace])                        # never raises
        try:
            exec(compile_cached(f"{stem}_main.py", path, tail, self.cache_dir, flags), namespace)
        except (SystemExit, KeyboardInterrupt, GeneratorExit):
            raise
        except BaseException as exc:
            guard.on_failure(exc, starting=False)
            raise


_installed: "Optional[Installed]" = None


def installed() -> "Optional[Installed]":
    return _installed


def attach_methods(target: type, source: type) -> "list[str]":
    """Give the game's class ``target`` the methods and class attributes written on ``source``, a plain class in a
    mod's module. This is how a mod adds a big block of methods to the dev's class without a patch carrying the
    code: the module holds the code (as ordinary, importable, testable Python), the patch carries one call.

    A name the target class already defines itself is NOT overwritten (the dev may have added a method of the
    same name): it is logged, and ``test_mods_apply`` fails on it, which is how an update that collides with a mod
    shows up. Returns the names that were attached."""
    attached = []
    for name, value in vars(source).items():
        if name.startswith("__") and name.endswith("__"):
            continue
        if name in vars(target):
            LOG.warning("%s already has %r: the mod's version from %s is not attached",
                        target.__name__, name, source.__module__)
            continue
        if callable(value) and hasattr(value, "__qualname__"):
            value.__qualname__ = f"{target.__name__}.{name}"
        setattr(target, name, value)
        attached.append(name)
    return attached


HOOKS_ATTR = "__mod_hooks__"


def _plain_method(target: type, name: str):
    """The game's method ``target.name`` as a plain function (a mixin's counts), or an error saying why it is not
    one: a hook can only be put on a method that is still there under that name."""
    current = getattr(target, name, None)
    if current is None:
        raise AttributeError(f"{target.__name__} no longer has a method {name}")
    if not callable(current) or isinstance(vars(target).get(name), (staticmethod, classmethod)):
        raise TypeError(f"{target.__name__}.{name} is not a plain method")
    return current


def wrap_method(target: type, name: str, *, before=None, after=None, result=None) -> None:
    """Make the game's method ``target.name`` also run a mod's function, with no patch and no anchor in the game's
    text: the one thing it relies on is that the method still exists under that name.

    ``before(self, *args, **kwargs)`` runs before the game's method (capture what it is about to be handed: the
    game's own code then runs whatever the hook did). ``after(self, *args, **kwargs)`` runs once the game's method
    has returned (set up a mod's state after the game's own ``_init_...``; read a saved layout after
    ``apply_panel_layout``). ``result(self, value, *args, **kwargs)`` gets what the game's method returned and may
    change it in place or return a replacement (None keeps it: add the mod's entry to the dict
    ``_panel_layout_dict`` builds). The game's own exceptions are its own (``after`` and ``result`` hooks do not run
    when it raised); a hook that raises is logged, recorded in ``ATTACH_FAILURES`` (the Mods page shows it) and
    skipped, so a bug in a mod never stops the game's method from running or returning. Several mods can hook the
    same method; they run in the order they were installed, and hooking again with the same function does nothing."""
    given = [(k, v) for k, v in (("before", before), ("after", after), ("result", result)) if v is not None]
    if len(given) != 1:
        raise ValueError("give wrap_method exactly one of before=, after= and result=")
    kind, hook = given[0]
    current = _plain_method(target, name)
    hooks = getattr(vars(target).get(name), HOOKS_ATTR, None)
    if hooks is None:                       # the game's own method (or a mixin's): put the wrapper on this class
        hooks = []
        original = current

        def run(hook_kind, hook_fn, self, value, args, kwargs):
            try:
                if hook_kind == "result":
                    replaced = hook_fn(self, value, *args, **kwargs)
                    return value if replaced is None else replaced
                hook_fn(self, *args, **kwargs)
            except Exception as exc:
                LOG.exception("%s.%s: the hook %s raised; skipped", target.__name__, name, hook_fn.__name__)
                ATTACH_FAILURES.setdefault(
                    _mod_of(hook_fn), f"{hook_fn.__name__} (run around {target.__name__}.{name}) raised: {exc!r}")
            return value

        @functools.wraps(original)
        def wrapper(self, *args, **kwargs):
            for hook_kind, hook_fn in tuple(hooks):
                if hook_kind == "before":
                    run(hook_kind, hook_fn, self, None, args, kwargs)
            value = original(self, *args, **kwargs)
            for hook_kind, hook_fn in tuple(hooks):
                if hook_kind != "before":
                    value = run(hook_kind, hook_fn, self, value, args, kwargs)
            return value

        setattr(wrapper, HOOKS_ATTR, hooks)
        setattr(target, name, wrapper)
    if (kind, hook) not in hooks:
        hooks.append((kind, hook))


def hooks_on(target: type, name: str) -> "list[tuple[str, object]]":
    """The ``(kind, function)`` hooks mods have put on ``target.name`` with ``wrap_method``, in run order."""
    return list(getattr(vars(target).get(name), HOOKS_ATTR, ()))


def install_methods(target: type, source: "Optional[type]" = None, namespace: "Optional[dict]" = None,
                    borrow: "Sequence[str]" = (), before: "Optional[Mapping[str, object]]" = None,
                    after: "Optional[Mapping[str, object]]" = None,
                    result: "Optional[Mapping[str, object]]" = None) -> "list[str]":
    """What a mod's ``install(target)`` calls: fill ``namespace`` (the mod module's ``globals()``) with the ``borrow``
    names that live in the module the game's class ``target`` is defined in (private constants and helpers that
    code moved out of that file still uses), then ``attach_methods`` the methods written on ``source``, then put the
    mod's ``before={"game_method": function}`` / ``after={...}`` / ``result={...}`` hooks on the game's methods
    (``wrap_method``).

    It never raises, and it is all or nothing: it checks everything first (a borrowed name, every method a hook is
    to go on) and changes the game's class only when all of it holds. A game update that renames something the mod
    uses (or any other surprise) must not stop the client from starting: the problem is logged, the mod's methods
    are simply not attached (the mod's feature is missing, the game runs) and the Mods page shows the reason.
    ``test_mods_apply`` checks every mod module attaches cleanly, so an update that breaks one is reported there."""
    hooks = [(kind, name, fn) for kind, table in (("before", before), ("after", after), ("result", result))
             for name, fn in (table or {}).items()]
    owner = source if source is not None else (hooks[0][2] if hooks else target)
    try:
        if borrow:
            module = sys.modules[target.__module__]
            lacking = [n for n in borrow if not hasattr(module, n)]
            if lacking:
                raise AttributeError(f"{target.__module__} no longer defines {', '.join(lacking)}")
        for _kind, name, _fn in hooks:
            _plain_method(target, name)
        for name in borrow:
            namespace[name] = getattr(sys.modules[target.__module__], name)
        attached = attach_methods(target, source) if source is not None else []
        for kind, name, fn in hooks:
            wrap_method(target, name, **{kind: fn})
        return attached
    except Exception as exc:
        LOG.exception("could not attach %s to %s: the mod's feature is missing", owner.__name__, target.__name__)
        ATTACH_FAILURES[_mod_of(owner)] = f"{owner.__name__} could not be attached to {target.__name__}: {exc}"
        return []


# mod name -> why its methods could not be attached to the game's classes (the ESC menu's Mods page shows it in red)
ATTACH_FAILURES: "dict[str, str]" = {}


def _mod_of(source) -> str:
    """The mod a module belongs to: the name of its folder under mods/ (the module's own name when it is not in one).
    ``source`` is a class or function of that module."""
    module = sys.modules.get(source.__module__)
    folder = Path(getattr(module, "__file__", "") or "").resolve().parent
    return folder.name if folder.parent == MODS_DIR.resolve() else source.__module__


def attachment_problems(module) -> "list[str]":
    """Check a mod module that attaches methods to the game's classes (it lists them in ``ATTACHES``, a tuple of
    ``(game module, game class, the mod's source class)``) or hooks the game's methods (``HOOKS``, a tuple of
    ``(game module, game class, game method, the mod's function)``): every method really is on the game class (the
    game's own class did not already have one of that name), every hooked method still exists and runs the mod's
    function, and the module resolves every global name its code uses. Empty when all is well; ``test_mods_apply``
    runs it for every such module after a game update."""
    import importlib
    problems = []
    for game_module, class_name, source_name in getattr(module, "ATTACHES", ()):
        target = getattr(importlib.import_module(game_module), class_name)
        source = getattr(module, source_name)
        for name, value in vars(source).items():
            if name.startswith("__") and name.endswith("__"):
                continue
            if vars(target).get(name) is not value:
                problems.append(f"{class_name}.{name} is not the mod's: the game's class already has one, or the install "
                                f"hook in {game_module} did not run ({module.__name__}.{source_name})")
    for game_module, class_name, method, hook_name in getattr(module, "HOOKS", ()):
        target = getattr(importlib.import_module(game_module), class_name)
        if not any(fn is getattr(module, hook_name) for _kind, fn in hooks_on(target, method)):
            problems.append(f"{class_name}.{method} does not run {module.__name__}.{hook_name}: the game no longer has "
                            f"that method, or the install hook in {game_module} did not run")
    lacking = missing_globals(module)
    if lacking:
        problems.append(f"{module.__name__} uses names it does not define or import: {', '.join(sorted(lacking))}")
    return problems


def missing_globals(*objects) -> "set[str]":
    """Global names that the code of the given modules / classes / functions loads but that are not defined in
    the module the code lives in (nor builtins). A method moved out of the game's file into a mod's module keeps
    looking its names up in ITS module now: this lists the ones that module forgot to import or define."""
    import builtins
    import dis
    import types

    def functions(obj):
        if isinstance(obj, types.FunctionType):
            yield obj
        elif isinstance(obj, (staticmethod, classmethod)):
            yield from functions(obj.__func__)
        elif isinstance(obj, property):
            for part in (obj.fget, obj.fset, obj.fdel):
                if part is not None:
                    yield from functions(part)
        elif isinstance(obj, type):
            for value in vars(obj).values():
                yield from functions(value)
        elif isinstance(obj, types.ModuleType):
            for value in vars(obj).values():
                if getattr(value, "__module__", None) == obj.__name__ or isinstance(value, (staticmethod, classmethod)):
                    yield from functions(value)

    def codes(code):
        yield code
        for const in code.co_consts:
            if isinstance(const, types.CodeType):
                yield from codes(const)

    missing = set()
    for obj in objects:
        for function in functions(obj):
            namespace = function.__globals__
            for code in codes(function.__code__):
                for ins in dis.get_instructions(code):
                    if ins.opname == "LOAD_GLOBAL":
                        name = ins.argval
                        if name not in namespace and not hasattr(builtins, name):
                            missing.add(name)
    return missing


# -- checks that mods can be picked one by one ----------------------------------------------------------

def module_owners(mods: "Sequence[Mod]") -> "dict[str, list[str]]":
    """module name -> the mods whose folder holds a module of that name (tests and conftest.py are not modules)."""
    owners: "dict[str, list[str]]" = {}
    for mod in mods:
        for path in sorted(mod.path.glob("*.py")):
            if not path.name.startswith("test_") and path.name != "conftest.py":
                owners.setdefault(path.stem, []).append(mod.name)
    return owners


def _imported_modules(text: str) -> "set[str]":
    """Top-level module names imported at MODULE level of ``text`` (also inside module-level if / try): an import inside
    a function runs later, when its failure can be guarded, so it is not a hard dependency."""
    import ast
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    found: "set[str]" = set()

    def visit(node) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            if isinstance(child, ast.Import):
                found.update(a.name.split(".")[0] for a in child.names)
            elif isinstance(child, ast.ImportFrom) and child.level == 0 and child.module:
                found.add(child.module.split(".")[0])
            visit(child)
    visit(tree)
    return found


_IMPORT_LINE = re.compile(r"^\s*(?:from\s+(\w+)(?:\.\w+)*\s+import|import\s+(\w+))")


def hard_dependencies(mod: Mod, mods: "Sequence[Mod]") -> "dict[str, list[str]]":
    """The other mods ``mod`` cannot work without, found from what it imports: ``{other mod: [what imports it]}``.

    Counted: an import at module level in one of the mod's modules, and any import line a patch adds to the game's
    file (which runs when the game's module is imported). Not counted: an import inside a function (the mod can guard
    it), and a module that the game itself has (a mod's module of the same name as a game file is not ours to need)."""
    owners = module_owners(mods)
    found: "dict[str, list[str]]" = {}

    def note(name: str, where: str) -> None:
        for other in owners.get(name, ()):
            if other != mod.name:
                found.setdefault(other, []).append(f"{name} ({where})")

    for path in sorted(mod.path.glob("*.py")):
        if path.name.startswith("test_") or path.name == "conftest.py":
            continue
        for name in sorted(_imported_modules(path.read_text(encoding="utf-8-sig"))):
            note(name, path.name)
    for hunk in mod.hunks:
        for line in hunk.added:
            match = _IMPORT_LINE.match(line)
            if match:
                note(match.group(1) or match.group(2), f"{hunk.file} patch")
    return found


def requirement_closure(mods: "Sequence[Mod]", names: "Sequence[str]") -> "set[str]":
    """``names`` and everything they ``requires``, transitively (names that are not mods are kept as they are)."""
    by_name = {m.name: m for m in mods}
    seen: "set[str]" = set()
    todo = list(names)
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        if name in by_name:
            todo.extend(by_name[name].requires)
    return seen


def undefined_globals(source: str, filename: str = "<game>") -> "set[tuple[str, str]]":
    """``(scope, name)`` for every global name that code in ``source`` loads but the file never defines (assigned,
    imported, declared ``global`` and assigned, or a builtin). A mod whose patch uses a name that only ANOTHER mod's
    patch provides shows up here when it is built alone."""
    import builtins
    import symtable
    top = symtable.symtable(source, filename, "exec")
    defined = {s.get_name() for s in top.get_symbols() if s.is_assigned() or s.is_imported() or s.is_namespace()}
    scopes: "list[tuple[str, object]]" = []

    def walk(table, path: "list[str]") -> None:
        for child in table.get_children():
            where = path + [child.get_name()]
            scopes.append((".".join(where), child))
            for sym in child.get_symbols():
                if sym.is_declared_global() and sym.is_assigned():
                    defined.add(sym.get_name())
            walk(child, where)
    walk(top, [])
    missing = set()
    for where, table in scopes:
        for sym in table.get_symbols():
            name = sym.get_name()
            if (sym.is_global() and sym.is_referenced() and name not in defined and not hasattr(builtins, name)
                    and not (name.startswith("__") and name.endswith("__"))):
                missing.add((where, name))
    return missing


def standalone_problems(mod: Mod, mods: "Sequence[Mod]", read_game_file) -> "list[str]":
    """Why ``mod`` would not work if it were the only one picked, with the mods it ``requires`` (empty when it would):
    its patches must apply, the patched files must compile, and they must not use a global name that no patch of the
    picked mods provides. Needs the game's files (``read_game_file(name) -> bytes``)."""
    wanted = requirement_closure(mods, [mod.name])
    missing = sorted(wanted - {m.name for m in mods})
    if missing:
        return [f"requires {', '.join(missing)}, which is not there"]
    subset = [m for m in mods if m.name in wanted]
    result = build(subset, read_game_file)
    problems = [f"{name}: {reason}" for name, reasons in result.failed.items() for reason in reasons]
    if problems:
        return problems
    for name, data in result.sources.items():
        try:                                    # parsing it (symtable) also reports a file that does not compile
            added = undefined_globals(data.decode("utf-8-sig"), name) - _vanilla_undefined(name, result.vanilla[name])
        except (SyntaxError, ValueError) as exc:
            problems.append(f"{name} does not compile: {exc}")
            continue
        problems.extend(f"{name}: {where} uses {used}, which nothing defines" for where, used in sorted(added))
    return problems


_VANILLA_UNDEFINED: "dict[tuple[str, int], set]" = {}


def _vanilla_undefined(name: str, data: bytes) -> "set[tuple[str, str]]":
    """``undefined_globals`` of an unpatched game file, remembered (it is the same for every mod checked)."""
    key = (name, hash(data))
    if key not in _VANILLA_UNDEFINED:
        _VANILLA_UNDEFINED[key] = undefined_globals(data.decode("utf-8-sig"), name)
    return _VANILLA_UNDEFINED[key]


def game_source(name: str) -> str:
    """The text of a game file as the client runs it: patched when mods apply to it, as shipped otherwise
    (LF line endings, no BOM). For tests that pin what the code says."""
    data = _installed.result.sources.get(name) if _installed is not None else None
    if data is None:
        base = _installed.internal if _installed is not None else INTERNAL_DIR
        data = (base / name).read_bytes()
    if data.startswith(_BOM):
        data = data[len(_BOM):]
    return data.decode("utf-8").replace("\r\n", "\n")


def _add_mod_paths(mods: "Sequence[Mod]") -> None:
    """Put every mod folder that holds modules of its own (``*.py``) on ``sys.path``, so ``import chat_suggest``
    finds ``mods/chat_suggest/chat_suggest.py``. A mod that was switched off is on the path too: its modules are
    inert until something imports them, and another mod's module may import one of them."""
    for mod in mods:
        if any(mod.path.glob("*.py")) and str(mod.path) not in sys.path:
            sys.path.append(str(mod.path))


def setup_for_tests() -> "Installed":
    """What ``_internal/conftest.py`` and ``mods/conftest.py`` run: the loader (so the game modules the tests
    import are the patched ones the client runs, and the mods' own modules are importable), the runtime shim
    the launchers install before the game starts (moderngl_compat, next to this file), and a throwaway
    ``scan_data/`` for the whole test run: the dev's tests exercise code that the scan mods hook into, and no
    test may write the real archive."""
    info = _installed if _installed is not None else install()
    import moderngl_compat
    moderngl_compat.install()
    _redirect_scan_data()
    return info


_scratch_scan_data: "Optional[str]" = None


def _redirect_scan_data() -> None:
    global _scratch_scan_data
    try:
        import scan_paths
    except ImportError:                      # the scan mod is not there: nothing can write it
        return
    if _scratch_scan_data is None:
        import atexit
        import shutil
        import tempfile
        _scratch_scan_data = tempfile.mkdtemp(prefix="star_empire_test_scan_")
        atexit.register(shutil.rmtree, _scratch_scan_data, True)
    scan_paths._root = os.path.join(_scratch_scan_data, "scan_data")
    scan_paths._paths.clear()


# -- dev tests that a mod makes fail on purpose ------------------------------------------------------------

EXPECTED_FAILURES_FILE = "expected_failures.txt"


def expected_failures(mods_dir: "Optional[Path]" = None) -> "list[tuple[str, str, str]]":
    """``(test id, mod, reason)`` from every ``mods/<name>/expected_failures.txt``.

    A mod that changes the game on purpose (a different wire format, say) makes a dev test that asserts the old
    behaviour fail. That is not a bug in either: the mod lists the test here, one per line, as
    ``test_file.py::Class::test_name  # why`` (the id is matched against the end of pytest's node id), and the
    tests' conftest files mark it as an expected failure. It is *strict*: when the dev's test starts passing
    (the dev changed it, or the mod went), the run says so, and the line has to go."""
    mods_dir = Path(mods_dir) if mods_dir is not None else MODS_DIR
    found = []
    if not mods_dir.is_dir():
        return found
    for folder in sorted(p for p in mods_dir.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
        path = folder / EXPECTED_FAILURES_FILE
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line, _hash, reason = raw.partition("#")
            if line.strip():
                found.append((line.strip(), folder.name, reason.strip() or "changed on purpose by the mod"))
    return found


def mark_expected_failures(items) -> int:
    """pytest's ``pytest_collection_modifyitems``: mark the listed dev tests as strict expected failures. Returns how
    many items were marked."""
    import pytest
    entries = expected_failures()
    if not entries:
        return 0
    marked = 0
    for item in items:
        node = item.nodeid.replace("\\", "/")
        if any(m.name == "xfail" and str(m.kwargs.get("reason", "")).startswith("changed on purpose by the ")
               for m in item.iter_markers()):
            continue                         # both conftest files call this: once is enough
        for test_id, mod, reason in entries:
            if node == test_id or node.endswith("/" + test_id) or node.startswith(test_id + "[") \
                    or ("/" + test_id + "[") in node:
                item.add_marker(pytest.mark.xfail(reason=f"changed on purpose by the {mod} mod: {reason}", strict=True))
                marked += 1
                break
    return marked


def install(mods_dir: "Optional[Path]" = None, internal_dir: "Optional[Path]" = None,
            cache_dir: "Optional[Path]" = None, *, interactive: bool = False) -> Installed:
    """Build the mods and make the patched files what ``import`` (and ``Installed.run_main``) gives.

    Call it once, first thing, before anything imports the game's modules. ``interactive`` (the real client,
    not the tests) also writes ``mods_report.txt`` and shows a message box when a mod had to be switched off.
    """
    global _installed
    internal = Path(internal_dir) if internal_dir is not None else INTERNAL_DIR
    cache = Path(cache_dir) if cache_dir is not None else None
    mods = load_mods(mods_dir)
    _add_mod_paths(mods)
    if os.environ.get(ENV_OFF, "").strip() not in ("", "0"):
        _installed = Installed(BuildResult(), internal, cache, mods=mods, switched_off_by_env=True)
        return _installed
    session = _start_session(mods, internal) if interactive else None
    if session is not None and session.safe_mode:
        _installed = Installed(BuildResult(), internal, cache, mods=mods, switched_off_by_env=True, safe_mode=True)
        _announce(_installed.result, session.notes, safe_mode=True)
        return _installed
    disabled = [n.strip() for n in os.environ.get(ENV_DISABLE, "").split(",") if n.strip()]
    if session is not None:
        disabled += [n for n in session.auto_off if n not in disabled]
    result = build(mods, lambda name: (internal / name).read_bytes(), disabled)
    if session is not None:                    # switched off after a problem: shown (Mods page, report) as switched off WITH its reason
        for name, reason in session.auto_off.items():
            if name in result.skipped:
                result.skipped.remove(name)
                result.auto_off[name] = reason
                result.failed[name] = [f"switched off automatically: {reason}"]
    for name, reasons in result.failed.items():
        for reason in reasons:
            LOG.warning("mod %s switched off: %s", name, reason)
    finder = _Finder(result.sources, internal, cache)
    if result.sources:
        sys.meta_path.insert(0, finder)
    guard = None
    if session is not None:
        try:
            import modguard
            guard = modguard.Guard(session, mods, internal, result.spans)
        except Exception:
            LOG.exception("modguard could not start (the game runs without its safety net)")
    _installed = Installed(result, internal, cache, finder, mods, guard=guard)
    if interactive:
        _announce(result, session.notes if session is not None else ())
    return _installed


def _start_session(mods: "Sequence[Mod]", internal: Path):
    """What the earlier runs left (modguard.py): the mods switched off after a problem, safe mode. None when the safety net is
    off (``STAR_EMPIRE_NO_MODGUARD=1``) or cannot start: the game then starts as it always did."""
    try:
        import modguard
        if not modguard.enabled():
            return None
        return modguard.start_session(mods, REPO_DIR, internal)
    except Exception:
        LOG.exception("modguard could not start (the game runs without its safety net)")
        return None


def uninstall() -> None:
    """Take the import hook out again (tests)."""
    global _installed
    if _installed is not None and _installed.finder is not None:
        try:
            sys.meta_path.remove(_installed.finder)
        except ValueError:
            pass
    _installed = None


def _announce(result: BuildResult, notes: "Sequence[str]" = (), safe_mode: bool = False) -> None:
    """Write mods_report.txt and, when something was switched off, say so in a message box. ``notes`` are what modguard
    switched off since the last start (and why); ``safe_mode`` means every mod is off for this start."""
    report = result.report()
    if safe_mode:
        report = "SAFE MODE: every mod is off for this start\n" + "\n".join(f"    {n}" for n in notes)
    elif notes:
        report += "\nswitched off since the last start, to keep the game running:\n" + "\n".join(f"    {n}" for n in notes)
    try:
        REPORT_FILE.write_text(report + "\n", encoding="utf-8")
    except OSError:
        pass
    parts = []
    patch_failures = [n for n in result.failed if n not in result.auto_off]
    if patch_failures:
        parts.append("These mods were switched off because they no longer fit the game's code, or a mod they need is off:\n\n"
                     + ", ".join(patch_failures))
    if notes:
        parts.append(("Safe mode:\n\n" if safe_mode else "Switched off to keep the game running (they come back when the game or the mod changes):\n\n")
                     + "\n\n".join(notes if safe_mode else [f"- {n}" for n in notes]))
    if not parts:                               # nothing failed, or only mods the player already knows are off
        return
    text = "\n\n".join(parts) + (f"\n\nThe game starts without them. Details: {REPORT_FILE}" if not safe_mode
                                  else f"\n\nDetails: {REPORT_FILE}")
    _message_box(text)


def _message_box(text: str) -> None:
    """pythonw has no console, so say it where it cannot be missed (the tests replace this)."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, "Star Empire mods", 0x30)
    except Exception:                           # not Windows, or no desktop: the report file has it
        pass


def game_problem(internal: "Optional[Path]" = None) -> str:
    """Why the game cannot be found (empty when ``internal`` holds Client.py): the text a launcher shows."""
    internal = Path(internal) if internal is not None else INTERNAL_DIR
    if (internal / "Client.py").is_file():
        return ""
    return (f"The Star Empire game was not found: there is no Client.py in {internal}.\n\n"
            f"If the game is not installed yet, put the official launcher (StarEmpireLauncher.exe) in {REPO_DIR} and start "
            f"this again: it downloads the game for you. Otherwise put this folder inside your Star Empire folder (the one "
            f"with Client.exe in it), or write the path of that folder in the file {REPO_DIR / GAME_DIR_FILE}, or set "
            f"{ENV_GAME}.")


def main(argv: "Sequence[str]" = ()) -> int:
    problem = game_problem()
    if problem:
        print(problem)
        return 2
    mods = load_mods()
    result = build(mods, lambda name: (INTERNAL_DIR / name).read_bytes(),
                   [n.strip() for n in os.environ.get(ENV_DISABLE, "").split(",") if n.strip()])
    print(result.report())
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

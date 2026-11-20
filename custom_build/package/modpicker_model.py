"""modpicker_model.py - what the mod picker window knows and does, with no drawing in it (modpicker.py is the window).

* ``load_catalog`` reads the mods in ``mods_available/``: for each one its README.md (title, one-sentence summary, the
  sections), its mod.json (what it requires, its category, whether it is recommended) and its screenshots/ folder.
* ``Selection`` is what the player has ticked. A mod needs other mods: ticking it brings them along (shown as "added
  automatically"), and the libraries nobody should have to know about (category "library") are only ever added that way.
* ``apply_selection`` makes ``mods/`` match: copies what is missing, removes what was unticked, refreshes changed copies. A
  mod in ``mods/`` that is not in ``mods_available/`` is the player's own and is left alone, and so is what it requires.
* ``choose_on_first_run`` is what the launch file calls: the first time (nothing chosen yet) it opens the window.

Standard library only (plus modloader.py and modctl.py beside it).
"""
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import modctl  # noqa: E402
import modloader  # noqa: E402

CATEGORIES = (("features", "Features"), ("speed", "Speed fixes"), ("recorders", "Data recorders"),
              ("diagnostics", "Diagnostics"), ("advanced", "Advanced"))
CATEGORY_HELP = {
    "features": "Things you see and use in the game.",
    "speed": "Invisible fixes for freezes and slow saves.",
    "recorders": "Save game data to files on your computer.",
    "diagnostics": "Write troubleshooting information to the log.",
    "advanced": "For people who know what they are doing.",
}
LIBRARY = "library"                  # code other mods use: never listed, only added when a mod needs it
EXIT_CONTINUE, EXIT_CANCELLED = 0, 2
MARKER = ".chosen"                   # in mods/: the choice has been made once, do not open the window on every start
SCREENSHOT_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".bmp")


@dataclass
class Entry:
    name: str
    title: str = ""
    summary: str = ""
    category: str = "features"
    recommended: bool = False
    requires: tuple = ()
    sections: list = field(default_factory=list)          # [(heading, [lines])] of the README after the summary
    screenshots: list = field(default_factory=list)       # [Path], in name order
    captions: dict = field(default_factory=dict)          # {file name: caption} from the README's picture links

    @property
    def visible(self) -> bool:
        return self.category != LIBRARY


def parse_readme(text: str):
    """``(title, summary, sections)`` of a mod's README.md: the ``# `` title, the first paragraph after it, and every
    ``## `` section as ``(heading, [lines])``."""
    lines = text.replace("\r\n", "\n").split("\n")
    title, i = "", 0
    if lines and lines[0].startswith("# "):
        title, i = lines[0][2:].strip(), 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    summary = []
    while i < len(lines) and lines[i].strip() and not lines[i].startswith("#"):
        summary.append(lines[i].strip())
        i += 1
    sections, current = [], None
    for line in lines[i:]:
        if line.startswith("## "):
            current = (line[3:].strip(), [])
            sections.append(current)
        elif current is not None:
            current[1].append(line.rstrip())
    for _heading, body in sections:                        # no blank lines at the ends of a section
        while body and not body[0].strip():
            body.pop(0)
        while body and not body[-1].strip():
            body.pop()
    return title, " ".join(summary), sections


_MODCTL_NOTE = re.compile(r"\.\s*`?modctl add`?\s+copies what a mod needs for you.*$")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")


def for_players(sections):
    """The README sections as the window shows them. The README is written for the repository: where it says how
    ``modctl`` copies what a mod needs the window says that ticking the mod does it, and the picture links are left out
    (the window shows the pictures of the ``screenshots/`` folder itself, so a **Screenshots** section is left out too)."""
    out = []
    for heading, body in sections:
        body = [line for line in body if not _IMAGE.fullmatch(line.strip())]
        if heading.lower() == "needs":
            body = [_MODCTL_NOTE.sub(" (added for you when you tick this mod).", line) for line in body]
        if heading.lower() == "screenshots" and not any(line.strip() for line in body):
            continue
        out.append((heading, body))
    return out


def image_captions(text: str) -> "dict[str, str]":
    """``{file name: caption}`` from the picture links of a README (``![caption](screenshots/name.png)``)."""
    return {os.path.basename(path.split("#")[0]): alt.strip() for alt, path in _IMAGE.findall(text)}


class Catalog:
    def __init__(self, entries, mods):
        self.entries = list(entries)
        self.by_name = {e.name: e for e in self.entries}
        self._mods = list(mods)
        self.visible = [e for e in self.entries if e.visible]

    def closure(self, names) -> "set[str]":
        """The names plus everything they need, all the way down (only mods that are in the catalog)."""
        names = [n for n in names if n in self.by_name]
        return set(modloader.requirement_closure(self._mods, names)) & set(self.by_name) if names else set()

    def grouped(self):
        """``[(category key, label, [entries])]`` in display order, empty categories left out."""
        out = []
        for key, label in CATEGORIES:
            members = sorted((e for e in self.visible if e.category == key), key=lambda e: e.title.lower())
            if members:
                out.append((key, label, members))
        other = sorted((e for e in self.visible if e.category not in dict(CATEGORIES)), key=lambda e: e.title.lower())
        if other:
            out.append(("other", "Other", other))
        return out


def load_catalog(available_dir) -> Catalog:
    """The mods on offer in ``available_dir``: folders with a mod.json that the loader can read (a folder of notes, or a
    mod whose files are broken, cannot be used and is not offered)."""
    mods = [m for m in modloader.load_mods(Path(available_dir)) if not m.error and (m.path / "mod.json").is_file()]
    entries = []
    for mod in mods:
        meta = {}
        try:
            meta = json.loads((mod.path / "mod.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            pass
        entry = Entry(mod.name, title=mod.name, summary=mod.description, requires=tuple(mod.requires),
                      category=str(meta.get("category", "features")), recommended=bool(meta.get("recommended", False)))
        readme = mod.path / "README.md"
        if readme.is_file():
            title, summary, sections = parse_readme(readme.read_text(encoding="utf-8-sig", errors="replace"))
            entry.title = title or mod.name
            entry.summary = summary or mod.description
            entry.sections = for_players(sections)
            entry.captions = image_captions(readme.read_text(encoding="utf-8-sig", errors="replace"))
        shots = mod.path / "screenshots"
        if shots.is_dir():
            entry.screenshots = sorted(p for p in shots.iterdir() if p.suffix.lower() in SCREENSHOT_SUFFIXES)
        entries.append(entry)
    return Catalog(entries, mods)


class Selection:
    """What is ticked. ``chosen`` holds the mods the player ticked; ``state`` says "on" (ticked), "auto" (not ticked, but
    another ticked mod needs it) or "off"."""

    def __init__(self, catalog: Catalog, chosen=()):
        self.catalog = catalog
        self.chosen = {n for n in chosen if n in catalog.by_name and catalog.by_name[n].visible}

    def closure(self) -> "set[str]":
        return self.catalog.closure(self.chosen)

    def state(self, name: str) -> str:
        if name in self.chosen:
            return "on"
        return "auto" if name in self.closure() else "off"

    def toggle(self, name: str) -> None:
        """Tick or untick a listed mod. Ticking one that is only there because another needs it makes it a real choice."""
        entry = self.catalog.by_name.get(name)
        if entry is None or not entry.visible:
            return
        self.chosen.symmetric_difference_update({name})

    def needed_by(self, name: str) -> "list[str]":
        """The ticked mods (other than ``name``) that need it."""
        return sorted(n for n in self.chosen if n != name and name in self.catalog.closure([n]))

    def adds(self, name: str) -> "list[str]":
        """What ticking ``name`` would add beyond itself: the mods it needs that are not already in."""
        return sorted(self.catalog.closure([name]) - {name} - self.closure())

    def recommended(self) -> None:
        self.chosen = {e.name for e in self.catalog.visible if e.recommended}

    def none(self) -> None:
        self.chosen = set()


def installed_names(installed_dir) -> "set[str]":
    return modctl.installed_names(str(installed_dir))


def initial_selection(catalog: Catalog, installed_dir) -> Selection:
    """What to show ticked when the window opens: what is in mods/ now; on the very first run (nothing there and nothing
    ever chosen) the recommended mods."""
    installed = {n for n in installed_names(installed_dir) if n in catalog.by_name and catalog.by_name[n].visible}
    selection = Selection(catalog, installed)
    if not installed_names(installed_dir) and not (Path(installed_dir) / MARKER).exists():
        selection.recommended()
    return selection


def plan(selection: Selection, installed_dir) -> dict:
    """What applying would do: ``{"add": [...], "remove": [...], "keep_own": [...]}`` (names)."""
    catalog = selection.catalog
    installed = installed_names(installed_dir)
    own = sorted(installed - set(catalog.by_name))                 # the player's own mods: not ours to touch
    wanted = set(selection.closure())
    own_needs = set()
    for mod in modloader.load_mods(Path(installed_dir)):
        if mod.name in own:
            own_needs |= {r for r in mod.requires if r in catalog.by_name}
    wanted |= catalog.closure(own_needs)                           # what their own mods need stays too
    return {"add": sorted(wanted - installed), "remove": sorted((installed & set(catalog.by_name)) - wanted),
            "keep_own": own, "wanted": sorted(wanted)}


def apply_selection(selection: Selection, available_dir, installed_dir) -> dict:
    """Make mods/ match the selection. Returns ``{"added", "removed", "updated", "kept_own"}``."""
    available_dir, installed_dir = str(available_dir), str(installed_dir)
    todo = plan(selection, installed_dir)
    os.makedirs(installed_dir, exist_ok=True)
    removed, _refused = modctl.remove(todo["remove"], installed_dir, force=True)
    added, _unknown = modctl.add(todo["add"], available_dir, installed_dir)
    updated, _added2, _unknown2 = modctl.update(sorted(set(todo["wanted"]) & installed_names(installed_dir)), available_dir,
                                                installed_dir)
    mark_chosen(installed_dir)
    return {"added": sorted(added), "removed": sorted(removed), "updated": sorted(set(updated) - set(added)),
            "kept_own": todo["keep_own"]}


def mark_chosen(installed_dir) -> None:
    """Remember that the choice has been made, so the window does not open on every start."""
    try:
        os.makedirs(str(installed_dir), exist_ok=True)
        (Path(installed_dir) / MARKER).write_text("The mods were chosen with the mod picker; delete this file to be asked "
                                                  "again on the next start.\n", encoding="utf-8")
    except OSError:
        pass


def first_run_needed(root) -> bool:
    """The window opens on its own only when mods are on offer, none is in use, and the player has never chosen."""
    root = Path(root)
    available, installed = root / "mods_available", root / "mods"
    offered = available.is_dir() and any(p.is_dir() and not p.name.startswith((".", "_")) and (p / "mod.json").is_file()
                                         for p in available.iterdir())
    return bool(offered and not installed_names(installed) and not (installed / MARKER).exists())


def choose_on_first_run(root, run=subprocess.run, python=None) -> bool:
    """For the launch file: the first time, show the window and wait for it. Returns False when the player closed it to
    cancel (then the game should not start); True otherwise, also when the window could not be opened at all."""
    if not first_run_needed(root):
        return True
    python = python or sys.executable
    try:
        done = run([python, os.path.join(_HERE, "modpicker.py"), "--play", "--root", str(root)],
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return True
    return done.returncode != EXIT_CANCELLED

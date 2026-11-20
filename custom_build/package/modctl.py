"""modctl - pick mods from mods_available/ into mods/, with the mods they need.

    modctl.bat list                 every mod: what it does, what it needs, whether it is in mods/
    modctl.bat add NAME [NAME ...]  copy mods (and the mods they require) into mods/
    modctl.bat add all              copy every mod
    modctl.bat update [NAME ...]    replace the copies in mods/ that differ from mods_available/ (after fetching newer
                                    mods); without names, every mod in mods/
    modctl.bat remove NAME [...]    take mods out of mods/ (refuses while another mod in mods/ needs one)
    modctl.bat check                list the mods that no longer fit the game (the launch file does this on every start)

mods_available/ is the mods branch of the repository (or a folder of mods you were given). Copying a mod folder from it
into mods/ by hand works too; this only saves looking up what each one needs, and leaves out the mods' test files.
Standard library only.
"""
import os
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import modloader  # noqa: E402

ROOT = os.path.dirname(_HERE)
AVAILABLE = os.path.join(ROOT, "mods_available")
INSTALLED = os.path.join(ROOT, "mods")


def available(available_dir: str = AVAILABLE):
    return modloader.load_mods(available_dir)


def installed_names(installed_dir: str = INSTALLED) -> "set[str]":
    if not os.path.isdir(installed_dir):
        return set()
    return {n for n in os.listdir(installed_dir)
            if os.path.isdir(os.path.join(installed_dir, n)) and not n.startswith((".", "_"))}


def plan_add(names, mods, installed: "set[str]") -> "tuple[list[str], list[str]]":
    """``(mods to copy, unknown names)``: what ``names`` need on top of what is already installed (``all`` = every mod)."""
    known = {m.name for m in mods}
    wanted = sorted(known) if list(names) == ["all"] else list(names)
    unknown = [n for n in wanted if n not in known]
    closure = modloader.requirement_closure(mods, [n for n in wanted if n in known])
    return sorted(n for n in closure if n in known and n not in installed), unknown


def add(names, available_dir: str = AVAILABLE, installed_dir: str = INSTALLED) -> "tuple[list[str], list[str]]":
    mods = available(available_dir)
    copy, unknown = plan_add(names, mods, installed_names(installed_dir))
    os.makedirs(installed_dir, exist_ok=True)
    for name in copy:
        shutil.copytree(os.path.join(available_dir, name), os.path.join(installed_dir, name), ignore=NOT_COPIED)
    return copy, unknown


# What is not copied out of a mod's folder: caches and the mod's own tests (the repository's mods branch keeps them
# beside the code; the game does not need them).
NOT_COPIED = shutil.ignore_patterns("__pycache__", "*.pyc", "test_*", "conftest.py", "expected_failures.txt")


def _files(folder: str) -> "dict[str, bytes]":
    """The files of a mod folder that are copied (by NOT_COPIED's rules), by relative path."""
    found = {}
    for here, dirs, names in os.walk(folder):
        skipped = set(NOT_COPIED(here, dirs + names))
        dirs[:] = [d for d in dirs if d not in skipped]
        for name in names:
            if name not in skipped:
                path = os.path.join(here, name)
                with open(path, "rb") as handle:
                    found[os.path.relpath(path, folder).replace(os.sep, "/")] = handle.read()
    return found


def update(names=("all",), available_dir: str = AVAILABLE, installed_dir: str = INSTALLED) -> "tuple[list[str], list[str], list[str]]":
    """``(updated, added, unknown)``: replace the copy of each installed mod that differs from the one in mods_available/
    (a new package version, or a fixed mod) and add what the new versions need. A mod only in mods/ is left alone."""
    mods = available(available_dir)
    known = {m.name for m in mods}
    present = installed_names(installed_dir)
    wanted = sorted(present & known) if list(names) == ["all"] else list(names)
    unknown = [n for n in wanted if n not in known]
    updated = []
    for name in wanted:
        if name not in known or name not in present:
            continue
        source, target = os.path.join(available_dir, name), os.path.join(installed_dir, name)
        if _files(source) != _files(target):
            shutil.rmtree(target)
            shutil.copytree(source, target, ignore=NOT_COPIED)
            updated.append(name)
    added, _ = add([n for n in wanted if n in known and n in present], available_dir, installed_dir)
    return updated, added, unknown


def dependants(name: str, installed_dir: str = INSTALLED) -> "list[str]":
    """The mods in mods/ that require ``name``."""
    mods = modloader.load_mods(installed_dir)
    return sorted(m.name for m in mods if name in m.requires and m.name != name)


def remove(names, installed_dir: str = INSTALLED, force: bool = False) -> "tuple[list[str], dict[str, list[str]]]":
    """``(removed, refused)``: a mod that another installed mod (not also being removed) needs is refused."""
    present = installed_names(installed_dir)
    removed, refused = [], {}
    for name in names:
        if name not in present:
            continue
        needed_by = [d for d in dependants(name, installed_dir) if d not in names]
        if needed_by and not force:
            refused[name] = needed_by
            continue
        shutil.rmtree(os.path.join(installed_dir, name))
        removed.append(name)
    return removed, refused


def listing(available_dir: str = AVAILABLE, installed_dir: str = INSTALLED) -> str:
    mods = available(available_dir)
    here = installed_names(installed_dir)
    lines = []
    for mod in mods:
        mark = "[x]" if mod.name in here else "[ ]"
        need = f"  (needs: {', '.join(mod.requires)})" if mod.requires else ""
        lines.append(f"{mark} {mod.name}{need}")
        if mod.description:
            lines.append(f"      {mod.description}")
    extra = sorted(here - {m.name for m in mods})
    if extra:
        lines.append("")
        lines.append("In mods/ but not in mods_available/: " + ", ".join(extra))
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd, args = (argv[0].lower(), argv[1:]) if argv else ("list", [])
    if cmd == "list":
        print(listing())
        print("\n[x] = in mods/ and used by the game.   modctl.bat add NAME   adds one with what it needs.")
        return 0
    if cmd == "add":
        if not args:
            print("Say which mod: modctl.bat add NAME   (modctl.bat list shows the names)")
            return 2
        copied, unknown = add(args)
        for name in copied:
            print("added", name)
        if not copied and not unknown:
            print("Nothing to add: already in mods/.")
        for name in unknown:
            print(f"no mod called {name!r} in mods_available/")
        return 2 if unknown else 0
    if cmd == "update":
        updated, added, unknown = update(args or ["all"])
        for name in updated:
            print("updated", name)
        for name in added:
            print("added", name, "(needed by an updated mod)")
        if not updated and not added and not unknown:
            print("Everything in mods/ is already the version in mods_available/.")
        for name in unknown:
            print(f"no mod called {name!r} in mods_available/")
        return 2 if unknown else 0
    if cmd == "remove":
        names = [a for a in args if a != "--force"]
        removed, refused = remove(names, force="--force" in args)
        for name in removed:
            print("removed", name)
        for name, by in refused.items():
            print(f"kept {name}: needed by {', '.join(by)} (remove those first, or add --force)")
        return 1 if refused else 0
    if cmd == "check":
        return modloader.main([])
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

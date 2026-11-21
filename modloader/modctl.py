"""modctl - pick mods from mods_available/ into mods/, with the mods they need.

    modctl.bat list                 every mod: what it does, what it needs, whether it is in mods/
    modctl.bat add NAME [NAME ...]  copy mods (and the mods they require) into mods/
    modctl.bat add all              copy every mod
    modctl.bat update [NAME ...]    replace the copies in mods/ that differ from mods_available/ (after fetching newer
                                    mods); without names, every mod in mods/
    modctl.bat remove NAME [...]    take mods out of mods/ (refuses while another mod in mods/ needs one)
    modctl.bat check                list the mods that no longer fit the game (the launch file does this on every start)

mods_available/ is the mods_available branch of the repository (or a folder of mods you were given). Copying a mod folder from it
into mods/ by hand works too; this only saves looking up what each one needs, and leaves out the mods' test files.
Standard library only.
"""
import os
import shutil
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import modloader  # noqa: E402

ROOT = os.path.dirname(_HERE)
AVAILABLE = os.path.join(ROOT, "mods_available")
INSTALLED = os.path.join(ROOT, "mods")

BACKUP_DIR = ".replaced"        # in mods/: the copy an update replaced, one generation per mod (a way back)
LOCK_NAME = ".refresh.lock"     # in mods/: held while a launch refreshes the mods (several clients may start at once)
LOCK_STALE_S = 120              # a lock older than this was left by a launch that died
ENV_NO_REFRESH = "STAR_EMPIRE_NO_MOD_REFRESH"


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


# What is not copied out of a mod's folder: caches, the mod's own tests, and its README and screenshots (the repository's
# mods_available branch keeps them beside the code; they are for choosing a mod, the game does not need them).
NOT_COPIED = shutil.ignore_patterns("__pycache__", "*.pyc", "test_*", "conftest.py", "expected_failures.txt",
                                    "README.md", "screenshots")


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


def _swap_in(source: str, installed_dir: str, name: str) -> None:
    """Put the mod folder ``source`` where mods/<name> is. The new copy is made beside it first, so a failure leaves the old
    copy exactly as it was; the old copy then moves to mods/.replaced/<name> (replacing the one an earlier update left)."""
    target = os.path.join(installed_dir, name)
    staging = os.path.join(installed_dir, f".{name}.new")
    backup_root = os.path.join(installed_dir, BACKUP_DIR)
    backup = os.path.join(backup_root, name)
    shutil.rmtree(staging, ignore_errors=True)
    try:
        shutil.copytree(source, staging, ignore=NOT_COPIED)
        os.makedirs(backup_root, exist_ok=True)
        shutil.rmtree(backup, ignore_errors=True)
        os.rename(target, backup)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    try:
        os.rename(staging, target)
    except Exception:
        os.rename(backup, target)                       # put the old copy back
        shutil.rmtree(staging, ignore_errors=True)
        raise


def update(names=("all",), available_dir: str = AVAILABLE, installed_dir: str = INSTALLED,
           errors: "dict | None" = None) -> "tuple[list[str], list[str], list[str]]":
    """``(updated, added, unknown)``: replace the copy of each installed mod that differs from the one in mods_available/
    (a new package version, or a fixed mod) and add what the new versions need. A mod only in mods/ is left alone, and the
    copy that was replaced is kept in mods/.replaced/<name>. With ``errors`` (a dict) a mod that cannot be replaced is
    recorded there as ``{name: why}`` and the others carry on; without it the first failure raises."""
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
        try:
            if _files(source) != _files(target):
                _swap_in(source, installed_dir, name)
                updated.append(name)
        except Exception as exc:
            if errors is None:
                raise
            errors[name] = f"{type(exc).__name__}: {exc}"[:160]
    added, _ = add([n for n in wanted if n in known and n in present], available_dir, installed_dir)
    return updated, added, unknown


def _take_lock(path: str) -> bool:
    """True if this process now holds the refresh lock. A lock older than LOCK_STALE_S is a dead launch's and is taken over."""
    for _attempt in (1, 2):
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return True
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(path) < LOCK_STALE_S:
                    return False
                os.remove(path)
            except OSError:
                return False
        except OSError:
            return False
    return False


def refresh_at_launch(root: str) -> str:
    """What the launch file does on every start: every mod in mods/ that is also in mods_available/ and differs (it was
    updated, e.g. by a ``git pull`` there) is replaced by the mods_available version, and the mods a new version needs are
    added. Mods the player put in mods/ themselves (not in mods_available/) are never touched and nothing is ever removed.
    Returns one line for launch_log.txt, "" when nothing changed. Never raises. ``STAR_EMPIRE_NO_MOD_REFRESH=1`` skips it."""
    try:
        if os.environ.get(ENV_NO_REFRESH):
            return ""
        available_dir, installed_dir = os.path.join(root, "mods_available"), os.path.join(root, "mods")
        if not (os.path.isdir(available_dir) and os.path.isdir(installed_dir)):
            return ""
        lock = os.path.join(installed_dir, LOCK_NAME)
        if not _take_lock(lock):
            return "mods not refreshed: another launch is doing it"
        try:
            errors: dict = {}
            updated, added, _unknown = update(["all"], available_dir, installed_dir, errors)
        finally:
            try:
                os.remove(lock)
            except OSError:
                pass
        parts = []
        if updated:
            parts.append("updated " + ", ".join(updated))
        if added:
            parts.append("added " + ", ".join(added) + " (needed by an updated mod)")
        if errors:
            parts.append("could NOT update " + "; ".join(f"{n} ({why})" for n, why in sorted(errors.items())))
        if not parts:
            return ""
        kept = f" (the old copies are in mods\\{BACKUP_DIR})" if updated else ""
        return "mods refreshed from mods_available: " + "; ".join(parts) + kept
    except Exception as exc:
        return f"mods not refreshed: {exc}"


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

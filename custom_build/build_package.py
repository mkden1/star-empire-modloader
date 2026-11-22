"""Build the stand-alone mod loader package: a folder to give to someone else.

    custom_build/python.exe custom_build/build_package.py [OUTPUT_FOLDER] [--no-verify] [--without-mods]

The package holds only what the loader needs: modloader.py, the one launch file's script (which also runs the official
launcher to update the game), the mod manager, the mods (without their tests; not with --without-mods, which is what the
repository's ``modloader`` branch is made from, see publish_branch.py), a copy of the Python runtime this client runs on
and of the third-party libraries the game imports (pygame, numpy, moderngl ...). It does NOT hold the game: whoever uses
it installs the game with the official launcher into this folder. Nothing personal is copied: the folder is built from
lists of what goes in, and a saved login (options.txt), logs, scan_data and the settings files the mods write are
refused wherever they would be copied from.

The package is rebuilt from this repository every time, so this repository stays the one place the code lives.

Default output: ``<project>/star_empire_modloader`` next to this client's folder. An existing output folder is only
touched if this script made it, and then only the files and folders it makes itself are replaced (see OWNED): a game
installed into the folder with the official launcher, the mods chosen in mods/, game_dir.txt and every settings file stay.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INTERNAL = REPO_ROOT / "_internal"
CUSTOM_BUILD = REPO_ROOT / "custom_build"
PACKAGE_SRC = CUSTOM_BUILD / "package"
MODS_SRC = REPO_ROOT / "mods"
DEFAULT_OUT = REPO_ROOT.parent / "star_empire_modloader"
MARKER = ".built_by_build_package"

# -- what goes in -------------------------------------------------------------------------------------------

# the Python interpreter and its extension modules, from custom_build/ (the stdlib is python314.zip)
PYTHON_FILES = ("python.exe", "pythonw.exe", "python314.dll", "python3.dll", "python.cat", "vcruntime140.dll",
                "vcruntime140_1.dll", "libcrypto-3.dll", "libssl-3.dll", "libffi-8.dll", "sqlite3.dll", "LICENSE.txt",
                "python314.zip")
PYTHON_GLOBS = ("*.pyd",)
PTH = "python314.zip\n.\n..\\lib\nimport site\n"

# the third-party libraries the game imports (found with a scan of its imports), from the game's _internal/ where
# the client's runtime keeps them working; the package keeps its own copy so a launcher update cannot undo them
LIB_DIRS = ("pygame", "numpy", "numpy.libs", "glcontext", "moderngl", "cffi", "pycparser", "_soundfile_data",
            "_sounddevice_data")
LIB_FILES = ("sounddevice.py", "_sounddevice.py", "soundfile.py", "_soundfile.py", "typing_extensions.py")
LIB_GLOBS = ("_cffi_backend*.pyd", "orjson*.pyd")
LIB_DLLS = ("SDL2.dll", "SDL2_image.dll", "SDL2_mixer.dll", "SDL2_ttf.dll", "freetype.dll", "libjpeg-62.dll",
            "libogg-0.dll", "libopus-0.dll", "libopusfile-0.dll", "libpng16-16.dll", "libtiff-5.dll", "libwavpack-1.dll",
            "libwebp-7.dll", "libwebpdemux-2.dll", "libxmp.dll", "portmidi.dll", "soft_oal.dll", "zlib1.dll",
            "MSVCR100.dll")
LICENSE_GLOBS = ("OpenAL-Soft-*.txt",)
# never copied out of a library: its caches and its own test suites (numpy ships 232 files of them), which nothing needs
LIB_SKIP = {"__pycache__", "tests"}

# what is never copied out of a mod folder: the tests' helpers, and anything a game run leaves behind - the saved login
# (options.txt), per-machine settings, logs, scan data - so none of it can ride along by accident
MOD_SKIP_NAMES = {"conftest.py", "expected_failures.txt", "__pycache__",
                  "options.txt", "capture_settings.json", "galaxy_map_window.json", "ship_layouts_window.json",
                  "range_rings.json", "ui_layouts.json", "quick_bars.json", "mods_seen.json", "mods_report.txt", "version.txt", "scan_data"}
MOD_SKIP_SUFFIXES = (".pyc", ".log", ".old")

# Files from _internal/ that belong to the runtime, not to a mod: they go into modloader/ next to the loader and the
# launcher applies them itself, always. moderngl_compat: the runtime's moderngl is stricter than the one the game is
# written for, and without it the client closes on its first frame in the world.
RUNTIME_FILES = ("moderngl_compat.py",)

# Everything the build writes into the output folder at its top level. A rebuild replaces exactly these and leaves the
# rest of the folder alone: the game itself (Client.exe, _internal ... put there by the official launcher), the mods the
# user chose in mods/, game_dir.txt and the settings and logs the game writes.
OWNED = ("modloader", "mods_available", "runtime", "licenses", "README.txt", "THIRD_PARTY_NOTICES.txt", "LICENSE",
         "Launch Modded Client.bat", "Choose Mods.bat")
# made by earlier builds and no longer: the one launch file checks the mods itself, the mod picker window replaced the
# console mod manager's place at the top (modctl.bat lives in modloader/ now); removed on a rebuild
STALE = ("Check Mods.bat", "Open Mod Manager.bat", "modctl.bat")
MODS_README = ("The game uses the mod folders in here. Pick them with Choose Mods.bat (the window), or copy a folder\n"
               "from mods_available by hand. Empty = the plain game.\n")
GAME_DIR_TEXT = ("# Only needed when this folder is NOT inside your Star Empire folder.\n"
                 "# Write the full path of the Star Empire folder (the one with Client.exe) on the next line, e.g.\n"
                 "# C:\\Games\\Star Empire\n")


def keep_mod_file(name: str) -> bool:
    return not (name in MOD_SKIP_NAMES or name.startswith("test_") or name.endswith(MOD_SKIP_SUFFIXES))


def copy_tree(src: Path, dst: Path, keep=lambda name: True) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for entry in sorted(src.iterdir()):
        if not keep(entry.name):
            continue
        if entry.is_dir():
            copy_tree(entry, dst / entry.name, keep)
        else:
            shutil.copy2(entry, dst / entry.name)


def log(text: str) -> None:
    print(f"[package] {text}", flush=True)


# -- the package ------------------------------------------------------------------------------------------------

def clear_owned(out: Path) -> None:
    """Remove what an earlier build made (OWNED), ALL OR NOTHING. Every path is first renamed aside; Windows refuses to
    rename a folder that holds a file in use (the log of a game running from this folder, a loaded DLL), and then
    everything already moved is put back and the build stops, so a running game never loses its files half-way (a plain
    delete used to remove the loader and stop at the open log). Only when every path moved are the old ones deleted.
    The logs the game wrote inside them are kept, in ``kept_logs/``."""
    aside = []
    try:
        for name in OWNED + STALE:
            path, target = out / name, out / (".old_" + name)
            if target.is_dir():                                  # left behind by an earlier build that stopped
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            if path.exists():
                path.rename(target)
                aside.append((path, target))
    except OSError as exc:
        for done, old in reversed(aside):
            old.rename(done)
        raise SystemExit(f"nothing was changed: {path.name} could not be replaced ({exc}). Is the game running from "
                         f"{out}? Close it, then build again.")
    for _path, target in aside:
        if target.is_dir():
            for log in target.rglob("*.log"):
                (out / "kept_logs").mkdir(exist_ok=True)
                shutil.move(str(log), str(out / "kept_logs" / f"{target.name[5:]}_{log.name}"))
            shutil.rmtree(target)
        else:
            target.unlink()


def prepare_output(out: Path) -> None:
    if out.exists():
        if not (out / MARKER).is_file():
            if any(out.iterdir()):
                raise SystemExit(f"{out} exists and was not made by this script; choose another folder or remove it")
        else:
            clear_owned(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / MARKER).write_text("made by custom_build/build_package.py; what it makes is replaced on each build, the rest "
                              "of the folder (the game, mods/, settings) is left alone\n", encoding="utf-8")


def copy_runtime(out: Path) -> None:
    py = out / "runtime" / "python"
    py.mkdir(parents=True)
    for name in PYTHON_FILES:
        shutil.copy2(CUSTOM_BUILD / name, py / name)
    for pattern in PYTHON_GLOBS:
        for path in sorted(CUSTOM_BUILD.glob(pattern)):
            shutil.copy2(path, py / path.name)
    (py / "python314._pth").write_text(PTH, encoding="ascii", newline="\n")

    lib = out / "runtime" / "lib"
    lib.mkdir(parents=True)
    for name in LIB_DIRS:
        copy_tree(INTERNAL / name, lib / name, lambda n: n not in LIB_SKIP)
    for name in LIB_FILES:
        if (INTERNAL / name).is_file():
            shutil.copy2(INTERNAL / name, lib / name)
    for pattern in LIB_GLOBS:
        for path in sorted(INTERNAL.glob(pattern)):
            shutil.copy2(path, lib / path.name)
    for name in LIB_DLLS:
        shutil.copy2(INTERNAL / name, lib / name)


def copy_licenses(out: Path) -> None:
    licenses = out / "licenses"
    licenses.mkdir()
    for pattern in LICENSE_GLOBS:
        for path in sorted(INTERNAL.glob(pattern)):
            shutil.copy2(path, licenses / path.name)
    for dist in sorted(INTERNAL.glob("*.dist-info")):
        for path in dist.rglob("*"):
            if path.is_file() and re.match(r"(?i)(licen[sc]e|copying|notice|authors)", path.name):
                target = licenses / dist.name.split("-")[0] / path.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
    shutil.copy2(PACKAGE_SRC / "THIRD_PARTY_NOTICES.txt", out / "THIRD_PARTY_NOTICES.txt")
    shutil.copy2(REPO_ROOT / "LICENSE", out / "LICENSE")             # the loader's and the mods' own license


def mod_list_text(mods) -> str:
    lines = []
    for mod in mods:
        lines.append(f"  {mod.name}")
        if mod.description:
            lines.append(f"      {mod.description}")
        if mod.requires:
            lines.append(f"      needs: {', '.join(mod.requires)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def copy_loader(out: Path) -> None:
    """The ``modloader/`` folder: the loader, the launcher, the mod manager, the runtime fix and the authors' notes."""
    (out / "modloader").mkdir(exist_ok=True)
    shutil.copy2(INTERNAL / "modloader.py", out / "modloader" / "modloader.py")
    for name in ("start_modded_client.py", "game_updater.py", "modctl.py", "modpicker.py", "modpicker_model.py"):
        shutil.copy2(PACKAGE_SRC / name, out / "modloader" / name)
    for name in RUNTIME_FILES:
        shutil.copy2(INTERNAL / name, out / "modloader" / name)
    authors = (MODS_SRC / "README.md").read_text(encoding="utf-8")
    (out / "modloader" / "MOD_AUTHORS_README.md").write_text(
        "> Notes for writing mods. This is the development README of the mod loader's repository: the tests and the\n"
        "> `custom_build/` scripts it mentions are not part of this package.\n\n"
        + authors, encoding="utf-8", newline="\n")


def readme_text(template: str, mods, with_mods: bool) -> str:
    """The package's README.txt from the template: the parts marked {{BUNDLED}}..{{/BUNDLED}} stay only when the mods
    are in the package, those marked {{SEPARATE}}..{{/SEPARATE}} only when they are not (they come from their own
    branch of the repository)."""
    keep, drop = ("BUNDLED", "SEPARATE") if with_mods else ("SEPARATE", "BUNDLED")
    text = re.sub(r"\{\{%s\}\}.*?\{\{/%s\}\}\n?" % (drop, drop), "", template, flags=re.S)
    text = text.replace("{{%s}}\n" % keep, "").replace("{{/%s}}\n" % keep, "")
    return text.replace("{{COUNT}}", str(len(mods))).replace("{{MODLIST}}", mod_list_text(mods))


def build_package(out: Path, with_mods: bool = True) -> "list[str]":
    """Make the package; returns the names of the mods in it. Without ``with_mods`` mods_available/ is left out (the
    mods are then picked from the repository's mods_available branch)."""
    sys.path.insert(0, str(INTERNAL))
    import modloader
    mods = modloader.load_mods(MODS_SRC)
    prepare_output(out)

    copy_loader(out)

    if with_mods:
        for mod in mods:
            copy_tree(mod.path, out / "mods_available" / mod.name, keep_mod_file)
    (out / "mods").mkdir(exist_ok=True)                              # the user's choices in it are kept
    (out / "mods" / "README.txt").write_text(MODS_README, encoding="utf-8")

    copy_runtime(out)
    copy_licenses(out)

    batch = {
        "Launch Modded Client.bat": '@echo off\nstart "" "%~dp0runtime\\python\\pythonw.exe" "%~dp0modloader\\start_modded_client.py"\nexit\n',
        "Choose Mods.bat": '@echo off\nstart "" "%~dp0runtime\\python\\pythonw.exe" "%~dp0modloader\\modpicker.py"\nexit\n',
        # for people who like a console: lives in modloader/ because the picker window does the same job
        "modloader/modctl.bat": '@echo off\n"%~dp0..\\runtime\\python\\python.exe" "%~dp0modctl.py" %*\n',
    }
    for name, text in batch.items():
        (out / name).write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    if not (out / "game_dir.txt").exists():                          # may hold the user's own path
        (out / "game_dir.txt").write_text(GAME_DIR_TEXT, encoding="utf-8")
    readme = readme_text((PACKAGE_SRC / "README.txt.tpl").read_text(encoding="utf-8"), mods, with_mods)
    (out / "README.txt").write_bytes(readme.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
    return [m.name for m in mods]


# -- what the build made -----------------------------------------------------------------------------------------

def owned_paths(out: Path) -> "list[Path]":
    """Every file and folder the build made in ``out`` (see OWNED), without the game or anything the user added."""
    paths = []
    for name in OWNED:
        path = out / name
        if path.exists():
            paths.append(path)
            if path.is_dir():
                paths.extend(path.rglob("*"))
    return paths


# -- verification ------------------------------------------------------------------------------------------------

LIB_SMOKE = r"""
import os, sys, json
root = sys.argv[1]
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
lib = os.path.join(root, "runtime", "lib")
os.add_dll_directory(lib)
sys.path.append(os.path.join(root, "_internal"))   # the decoys must lose to runtime/lib (python314._pth)
out = {}
import pygame; pygame.init()
import numpy, orjson, sounddevice, soundfile, glcontext, moderngl, cffi
for mod in (pygame, numpy, orjson, sounddevice, soundfile, glcontext, moderngl, cffi):
    out[mod.__name__] = os.path.dirname(os.path.abspath(mod.__file__)).startswith(os.path.abspath(lib))
print("RESULT " + json.dumps(out))
"""

MOD_SMOKE = r"""
import importlib, json, os, sys
root = sys.argv[1]
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.join(root, "modloader"))
import modloader
lib = os.path.join(root, "runtime", "lib")
os.add_dll_directory(lib)
if lib not in sys.path:
    sys.path.append(lib)
sys.path.append(str(modloader.INTERNAL_DIR))
info = modloader.install(interactive=False)
for name in ("Client", "protocol", "render_mixin", "crash_log"):
    importlib.import_module(name)
print("RESULT " + json.dumps({"applied": sorted(info.result.applied), "failed": info.result.failed,
                              "attach_failures": dict(modloader.ATTACH_FAILURES)}))
"""


GL_SMOKE = r"""
# What the game does on its first frame in the world, on a real OpenGL context: the sun upload of hull_batch (the call that
# raised "the value must be a list not tuple" and closed the client right after login) with the launcher's own shim.
import json, os, sys
from types import SimpleNamespace
root = sys.argv[1]
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
lib = os.path.join(root, "runtime", "lib")
os.add_dll_directory(lib)
sys.path.insert(0, os.path.join(root, "modloader"))
sys.path.append(lib)
sys.path.append(os.path.join(root, "_internal"))
import moderngl
try:
    ctx = moderngl.create_standalone_context(require=330)
except Exception as exc:
    print("RESULT " + json.dumps({"context": False, "why": str(exc)}))
    raise SystemExit(0)
prog = ctx.program(vertex_shader='''
#version 330
in vec2 pos;
uniform int u_sun_count;
uniform vec3 u_suns[4];
uniform vec3 u_sun_cols[4];
uniform float u_reach;
uniform float u_fade;
uniform float u_floor0;
out vec3 v;
void main() {
    v = u_suns[0] + u_suns[1] + u_suns[2] + u_suns[3] + u_sun_cols[0] + u_sun_cols[1] + u_sun_cols[2] + u_sun_cols[3]
        + vec3(float(u_sun_count) + u_reach + u_fade + u_floor0);
    gl_Position = vec4(pos, 0.0, 1.0);
}''', fragment_shader='''
#version 330
in vec3 v; out vec4 c;
void main() { c = vec4(v, 1.0); }''')
suns = ((1.0, 2.0, 3.0), (4.0, 5.0, 6.0), (0.0, 0.0, 0.0), (0.0, 0.0, 9.0))
try:
    prog["u_suns"] = suns
    strict = False
except moderngl.Error:
    strict = True                       # this runtime's moderngl is as strict as it was: the shim is what lets it through
import moderngl_compat                  # as start_modded_client.py does
moderngl_compat.install()
import hull_light
from hull_batch import HullSpriteBatch
sun_a = hull_light.light(-500, 120, (255, 255, 255))
sun_b = hull_light.light(300, -40, (255, 200, 140))
batch = SimpleNamespace(prog=prog, _sent={}, _sun=hull_light.as_lights(hull_light.lights([sun_a, sun_b], 5000.0)))
HullSpriteBatch._send_sun(batch)
print("RESULT " + json.dumps({"context": True, "strict_without_the_shim": strict,
                              "sun_count": prog["u_sun_count"].value, "first_sun": list(prog["u_suns"].value[0][:2])}))
"""


PICKER_SMOKE = r"""
# The mod picker with the package's own Python and libraries: draw the window on a dummy display, tick the recommended mods,
# press Apply and see what landed in mods/ (the same calls the real window makes).
import json, os, sys
root = sys.argv[1]
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.join(root, "modloader"))
import modpicker                       # adds runtime/lib itself
import pygame
pygame.init()
screen = pygame.display.set_mode((1120, 720))
picker = modpicker.Picker(root=root, play=True)
first = sorted(picker.selection.chosen)            # a first run opens with the recommended mods ticked
picker.draw(screen)
picker.result = "apply"
code = picker.finish()
mods = os.path.join(root, "mods")
print("RESULT " + json.dumps({"code": code, "listed": len(picker.catalog.visible), "ticked": first,
                              "applied": sorted(n for n in os.listdir(mods) if os.path.isdir(os.path.join(mods, n))),
                              "marker": os.path.isfile(os.path.join(mods, ".chosen"))}))
"""


REFRESH_SMOKE = r"""
# What the launch file does on every start (modctl.refresh_at_launch), with the real mods: right after every mod was copied
# there must be NO difference to report; then one file is added to a mod in mods_available/ and exactly that mod must be
# replaced (its old copy kept in mods/.replaced).
import json, os, sys
root = sys.argv[1]
sys.path.insert(0, os.path.join(root, "modloader"))
import modctl
in_sync = modctl.refresh_at_launch(root)
probe = os.path.join(root, "mods_available", "core", "zz_refresh_probe.txt")
with open(probe, "w", encoding="utf-8") as handle:
    handle.write("a newer version of core")
changed = modctl.refresh_at_launch(root)
then_in_sync = modctl.refresh_at_launch(root)
print("RESULT " + json.dumps({
    "in_sync": in_sync, "changed": changed, "then_in_sync": then_in_sync,
    "probe_copied": os.path.isfile(os.path.join(root, "mods", "core", "zz_refresh_probe.txt")),
    "old_kept": os.path.isdir(os.path.join(root, "mods", ".replaced", "core"))}))
"""


DECOY = 'raise ImportError("the game folder has its own copy of %s and it was used instead of the package\'s")\n'


def run_python(root: Path, args, env_extra=None, timeout=300):
    env = dict(os.environ)
    env.pop("STAR_EMPIRE_NO_MODS", None)
    env.pop("STAR_EMPIRE_DISABLE_MODS", None)
    env.pop("STAR_EMPIRE_GAME_DIR", None)
    env.update(env_extra or {})
    return subprocess.run([str(root / "runtime" / "python" / "python.exe"), *map(str, args)], cwd=root, env=env,
                          capture_output=True, text=True, timeout=timeout)


def result_of(proc):
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT "):])
    return None


def verify(out: Path, names: "list[str]") -> "list[str]":
    """Put the package in a stripped copy of the game (only its .py files, no libraries, no DLLs, no assets) and run it
    with the package's own Python: every library must come from the package, the loader must find the game, every mod
    must apply, and every mod picked ALONE (with what it needs) must import the patched game. Returns the problems."""
    problems = []
    with tempfile.TemporaryDirectory(prefix="se_package_verify_") as tmp:
        game = Path(tmp) / "game"
        (game / "_internal").mkdir(parents=True)
        for path in INTERNAL.glob("*.py"):
            if (not path.name.startswith("test_") and path.name not in ("modloader.py", "conftest.py")
                    and path.name not in LIB_FILES):
                shutil.copy2(path, game / "_internal" / path.name)
        # decoys: if the game's own copy of a library were ever picked over the package's, this is what would load
        for name in ("pygame", "numpy", "moderngl", "glcontext", "cffi", "pycparser"):
            (game / "_internal" / name).mkdir()
            (game / "_internal" / name / "__init__.py").write_text(DECOY % name, encoding="utf-8")
        for name in LIB_FILES:
            (game / "_internal" / name).write_text(DECOY % name, encoding="utf-8")
        for name in OWNED:                       # the package as built: not the game the output folder may also hold
            if (out / name).is_dir():
                copy_tree(out / name, game / name)
            elif (out / name).is_file():
                shutil.copy2(out / name, game / name)
        (game / "mods").mkdir()
        (game / "mods" / "README.txt").write_text(MODS_README, encoding="utf-8")
        (game / "game_dir.txt").write_text(GAME_DIR_TEXT, encoding="utf-8")

        proc = run_python(game, ["-I", "-c", LIB_SMOKE, game])
        smoke = result_of(proc)
        if smoke is None:
            problems.append("the runtime libraries do not import:\n" + (proc.stderr or proc.stdout)[-1500:])
        else:
            problems += [f"{lib} was loaded from outside the package's runtime/lib" for lib, ok in smoke.items() if not ok]

        proc = run_python(game, ["-I", "-c", GL_SMOKE, game])
        gl = result_of(proc)
        if gl is None:
            problems.append("the game's first-frame sun upload failed on a real OpenGL context:\n"
                            + (proc.stderr or proc.stdout)[-1500:])
        elif not gl["context"]:
            log(f"note: no OpenGL 3.3 context here ({gl['why']}), so the sun upload was not tried")
        elif gl["sun_count"] != 2 or gl["first_sun"] != [-500.0, 120.0]:
            problems.append(f"the sun upload went through but read back wrong: {gl}")
        elif not gl["strict_without_the_shim"]:
            log("note: this runtime's moderngl no longer needs moderngl_compat (it took a tuple for an array uniform)")

        proc = run_python(game, ["-I", "modloader/modloader.py"])
        if proc.returncode != 0 or "mods in effect (0)" not in proc.stdout:
            problems.append(f"Check Mods with an empty mods/ folder: exit {proc.returncode}\n" + proc.stdout[-800:])

        add = run_python(game, ["-I", "modloader/modctl.py", "add", "all"])
        if add.returncode != 0:
            problems.append("modctl add all failed:\n" + (add.stdout + add.stderr)[-800:])
        proc = run_python(game, ["-I", "modloader/modloader.py"])
        if proc.returncode != 0 or f"mods in effect ({len(names)})" not in proc.stdout:
            problems.append(f"with every mod added the check says:\n" + proc.stdout[-1200:])
        proc = run_python(game, ["-I", "-c", REFRESH_SMOKE, game])
        refreshed = result_of(proc)
        if refreshed is None:
            problems.append("the launch-time mod refresh does not run with the package's Python:\n" + (proc.stderr or proc.stdout)[-1500:])
        elif refreshed["in_sync"] != "":
            problems.append(f"right after modctl add all the launch-time refresh found a difference: {refreshed['in_sync']!r}")
        elif not (refreshed["changed"].startswith("mods refreshed from mods_available: updated core")
                  and refreshed["probe_copied"] and refreshed["old_kept"] and refreshed["then_in_sync"] == ""):
            problems.append(f"the launch-time refresh did not update exactly the changed mod: {refreshed}")
        shutil.rmtree(game / "mods")
        (game / "mods").mkdir()

        sys.path.insert(0, str(INTERNAL))
        import modloader
        mods = modloader.load_mods(out / "mods_available")

        recommended = [m.name for m in mods if json.loads((m.path / "mod.json").read_text(encoding="utf-8-sig"))
                       .get("recommended")]
        proc = run_python(game, ["-I", "-c", PICKER_SMOKE, game])
        picked = result_of(proc)
        if picked is None:
            problems.append("the mod picker does not run with the package's Python:\n" + (proc.stderr or proc.stdout)[-1500:])
        elif (picked["code"], picked["marker"]) != (0, True) or picked["ticked"] != sorted(recommended):
            problems.append(f"the mod picker's first run is wrong: {picked}")
        elif picked["applied"] != sorted(modloader.requirement_closure(mods, recommended)):
            problems.append(f"the mod picker applied {picked['applied']} for the recommended mods {sorted(recommended)}")
        shutil.rmtree(game / "mods")
        (game / "mods").mkdir()

        def alone(name):
            keep = modloader.requirement_closure(mods, [name])
            others = ",".join(n for n in names if n not in keep)
            # each mod in its own copy of mods/, so the check is the real "only these folders are there"
            work = Path(tmp) / f"alone_{name}"
            copy_tree(game, work, lambda n: n not in ("_internal",))
            shutil.rmtree(work / "mods")
            for n in sorted(keep):
                copy_tree(out / "mods_available" / n, work / "mods" / n)
            (work / "game_dir.txt").write_text(str(game), encoding="utf-8")
            proc = run_python(work, ["-I", "-c", MOD_SMOKE, work], timeout=600)
            data = result_of(proc)
            if data is None:
                return [f"{name} alone: the patched game does not import:\n" + (proc.stderr or proc.stdout)[-1500:]]
            bad = [f"{name} alone: {n} was switched off: {why}" for n, why in data["failed"].items()]
            bad += [f"{name} alone: {n} could not attach: {why}" for n, why in data["attach_failures"].items()]
            if sorted(data["applied"]) != sorted(keep):
                bad.append(f"{name} alone: applied {data['applied']} instead of {sorted(keep)}")
            return bad

        with ThreadPoolExecutor(max_workers=4) as pool:
            for result in pool.map(alone, names):
                problems += result
    return problems


def main(argv) -> int:
    args = [a for a in argv if not a.startswith("--")]
    out = Path(args[0]).resolve() if args else DEFAULT_OUT
    if out == REPO_ROOT or REPO_ROOT in out.parents:
        raise SystemExit("the output folder must be outside this repository")
    with_mods = "--without-mods" not in argv
    names = build_package(out, with_mods)
    log(f"built {out}: {len(names)} mods" + ("" if with_mods else " (not copied: --without-mods)"))
    problems = []
    if not with_mods:
        log("not verified: every mod is built alone from mods_available/, which this build left out")
    elif "--no-verify" not in argv:
        log("verifying in a stripped copy of the game (every mod picked alone) ...")
        found = verify(out, names)
        for problem in found:
            log("VERIFY: " + problem)
        problems += found
    size = sum(p.stat().st_size for p in owned_paths(out) if p.is_file()) / 1e6
    log(f"{'FAILED' if problems else 'ok'}: {size:.0f} MB, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

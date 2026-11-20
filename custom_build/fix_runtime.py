"""Reapply the manual fixes needed for our from-source Python 3.14 runtime to
actually run the game's vendored native libraries.

Why this exists
----------------
_internal/pygame, numpy, moderngl, glcontext, orjson, sounddevice, soundfile
etc. are deliberately gitignored -- they're vendored runtime libraries, not
our mod code. The dev's official installer/launcher only ships their
COMPILED extension modules (.pyd files) for these; the pure-Python glue that
normally ships alongside them is frozen into the dev's own exe and never
shipped as loose files. We layer real pure-Python source over the vendored
binaries to make them importable from our own separate interpreter.

Every time the launcher resyncs (i.e. on any game update), it overwrites
those specific vendored files with the dev's originals again -- silently
undoing these fixes, since none of it is tracked by git. Run this script
after every launcher update, before testing or launching the modded client.

Safe to run repeatedly: every fix checks whether it's already applied before
doing anything, and prints what it did (or skipped).

Requires: custom_build/python.exe, custom_build/include/, and
custom_build/libs/python314.lib to already exist (the one-time embeddable-
Python + headers setup). This script does not redo that part -- see the
project notes for the full from-scratch runtime rebuild if those are gone.
"""
import json
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INTERNAL = REPO_ROOT / "_internal"
CUSTOM_BUILD = REPO_ROOT / "custom_build"
PYTHON_EXE = CUSTOM_BUILD / "python.exe"
SCRATCH = CUSTOM_BUILD / "_fix_runtime_scratch"

# Versions confirmed (this session) to work against this game's specific
# vendored cp314-win_amd64 binaries. Don't casually bump these -- newer
# releases can drift out of sync with the compiled extension's ABI (this is
# exactly how the numpy 2.5.2 mismatch happened; 2.4.0 is the one verified
# to actually match the vendored _multiarray_umath.pyd).
PYGAME_VERSION = "2.6.1"
NUMPY_VERSION = "2.4.0"
GLCONTEXT_VERSION = "3.0.0"
MODERNGL_VERSION = "5.6.4"

FORCE_MODERNGL = "--force" in sys.argv


def log(msg):
    print(f"[fix_runtime] {msg}", flush=True)


def pypi_json(package, version):
    url = f"https://pypi.org/pypi/{package}/{version}/json"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def pypi_url(package, version, *, packagetype=None, contains=None):
    data = pypi_json(package, version)
    for u in data["urls"]:
        if packagetype and u["packagetype"] != packagetype:
            continue
        if contains and contains not in u["url"]:
            continue
        return u["url"]
    raise RuntimeError(f"No matching distribution for {package}=={version} "
                        f"(packagetype={packagetype!r}, contains={contains!r})")


def download(url, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)
    return dest


def fresh_scratch() -> Path:
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True)
    return SCRATCH


# ---------------------------------------------------------------------------
# pygame: layer the pure-Python package source over the vendored .pyd files.
# ---------------------------------------------------------------------------
def fix_pygame():
    target = INTERNAL / "pygame"
    if (target / "__init__.py").exists():
        log("pygame: already has pure-Python glue, skipping")
        return
    log(f"pygame: missing glue, fetching {PYGAME_VERSION} sdist")
    work = fresh_scratch()
    url = pypi_url("pygame", PYGAME_VERSION, packagetype="sdist")
    tgz = download(url, work / "pygame.tar.gz")
    with tarfile.open(tgz) as tf:
        tf.extractall(work)
    src = work / f"pygame-{PYGAME_VERSION}" / "src_py"
    for f in src.glob("*.py"):
        shutil.copy2(f, target / f.name)
    (target / "_sdl2").mkdir(exist_ok=True)
    shutil.copy2(src / "_sdl2" / "__init__.py", target / "_sdl2" / "__init__.py")
    (target / "threads").mkdir(exist_ok=True)
    for f in (src / "threads").glob("*.py"):
        shutil.copy2(f, target / "threads" / f.name)
    log("pygame: glue installed")


# ---------------------------------------------------------------------------
# numpy: layer the matching pure-Python package source over the vendored
# compiled _core. Version-pinned -- see NUMPY_VERSION comment above.
# ---------------------------------------------------------------------------
def fix_numpy():
    target = INTERNAL / "numpy"
    if (target / "__init__.py").exists():
        log("numpy: already has pure-Python glue, skipping")
        return
    log(f"numpy: missing glue, fetching {NUMPY_VERSION} wheel")
    work = fresh_scratch()
    url = pypi_url("numpy", NUMPY_VERSION, contains="cp314-cp314-win_amd64")
    whl = download(url, work / "numpy.whl")
    extracted = work / "extracted"
    with zipfile.ZipFile(whl) as zf:
        zf.extractall(extracted)
    src = extracted / "numpy"
    for f in src.rglob("*.py"):
        rel = f.relative_to(src)
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dest)
    log("numpy: glue installed")


# ---------------------------------------------------------------------------
# glcontext: same pattern, tiny package (__init__.py + empty.py).
# ---------------------------------------------------------------------------
def fix_glcontext():
    target = INTERNAL / "glcontext"
    if (target / "__init__.py").exists():
        log("glcontext: already has pure-Python glue, skipping")
        return
    log(f"glcontext: missing glue, fetching {GLCONTEXT_VERSION} wheel")
    work = fresh_scratch()
    url = pypi_url("glcontext", GLCONTEXT_VERSION, contains="cp313-cp313-win_amd64")
    whl = download(url, work / "glcontext.whl")
    extracted = work / "extracted"
    with zipfile.ZipFile(whl) as zf:
        zf.extractall(extracted)
    src = extracted / "glcontext"
    for name in ("__init__.py", "empty.py"):
        shutil.copy2(src / name, target / name)
    log("glcontext: glue installed")


# ---------------------------------------------------------------------------
# moderngl: the vendored mgl.pyd is broken (built expecting a top-level
# `_moderngl` module that doesn't exist). We rebuild moderngl.mgl from
# source ourselves against our python314.lib, patching the one thing that
# doesn't compile under Python 3.14 headers: the old Py_TYPE(x) = y lvalue
# idiom, which CPython now only allows as Py_SET_TYPE(x, y).
# ---------------------------------------------------------------------------
def moderngl_is_broken():
    result = subprocess.run(
        [str(PYTHON_EXE), "-c",
         "import os; os.add_dll_directory(r'%s'); import moderngl.mgl" % INTERNAL],
        capture_output=True, text=True)
    return result.returncode != 0


def fix_moderngl():
    target = INTERNAL / "moderngl"
    needs_glue = not (target / "__init__.py").exists()
    if not needs_glue and not FORCE_MODERNGL and not moderngl_is_broken():
        log("moderngl: already working, skipping (pass --force to rebuild anyway)")
        return

    include_dir = CUSTOM_BUILD / "include"
    libs_dir = CUSTOM_BUILD / "libs"
    if not (libs_dir / "python314.lib").exists() or not (include_dir / "Python.h").exists():
        log("moderngl: custom_build/include or custom_build/libs/python314.lib "
            "is missing -- the one-time embeddable-Python headers/import-lib "
            "setup hasn't been done in this custom_build/. Skipping rebuild; "
            "see the project notes for the full runtime rebuild.")
        return

    vswhere = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")
    if not vswhere.exists():
        log("moderngl: vswhere.exe not found -- MSVC Build Tools don't appear "
            "to be installed. Skipping rebuild.")
        return
    vs_path = subprocess.run(
        [str(vswhere), "-latest", "-products", "*", "-property", "installationPath"],
        capture_output=True, text=True).stdout.strip()
    vcvars = Path(vs_path) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    if not vcvars.exists():
        log(f"moderngl: vcvars64.bat not found under {vs_path!r}. Skipping rebuild.")
        return

    log(f"moderngl: (re)building {MODERNGL_VERSION} from source, patched for "
        f"Python 3.14 headers")
    work = fresh_scratch()
    url = pypi_url("moderngl", MODERNGL_VERSION, packagetype="sdist")
    tgz = download(url, work / "moderngl.tar.gz")
    with tarfile.open(tgz) as tf:
        tf.extractall(work)
    src_dir = work / f"moderngl-{MODERNGL_VERSION}" / "moderngl" / "src"
    pattern = re.compile(
        r"Py_TYPE\(([a-zA-Z_][a-zA-Z0-9_]*)\) = (&[A-Za-z_]+);")
    patched = 0
    for cpp in src_dir.glob("*.cpp"):
        text = cpp.read_text()
        new_text, n = pattern.subn(r"Py_SET_TYPE(\1, \2);", text)
        if n:
            cpp.write_text(new_text)
            patched += n
    log(f"moderngl: patched {patched} Py_TYPE(x) = y lvalue assignment(s)")

    build_out = fresh_scratch_subdir("moderngl_build_out")
    src_pkg = work / f"moderngl-{MODERNGL_VERSION}"
    cmd = (
        f'call "{vcvars}" && '
        f'"{PYTHON_EXE}" -m pip install --no-binary=:all: '
        f'--target "{build_out}" "{src_pkg}" --no-deps -q'
    )
    # shell=True (not ["cmd", "/c", cmd]) -- passing this as a list makes
    # Python's list2cmdline() re-escape the already-quoted vcvars64.bat path,
    # which cmd.exe's own /c quote-stripping then mishandles.
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        log("moderngl: build FAILED")
        print(result.stdout[-4000:])
        print(result.stderr[-4000:])
        raise SystemExit(1)

    built_pkg = build_out / "moderngl"
    for f in built_pkg.rglob("*"):
        if f.is_dir() or f.name == "__pycache__":
            continue
        if "__pycache__" in f.parts:
            continue
        rel = f.relative_to(built_pkg)
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        copy_with_retry(f, dest)
    log("moderngl: rebuilt and installed")


def copy_with_retry(src: Path, dest: Path, attempts=6, delay=0.5):
    """shutil.copy2, retrying on WinError 32/5 (file in use).

    Two known causes: a transient Windows Defender real-time-scan lock on
    the freshly-built .pyd right after link.exe writes it (clears within a
    couple seconds); or the official Client.exe having _internal/moderngl's
    old .pyd loaded (it shares this same _internal/ folder) -- that one
    doesn't clear on its own, hence the actionable error below.
    """
    import time
    for i in range(attempts):
        try:
            shutil.copy2(src, dest)
            return
        except PermissionError:
            if i == attempts - 1:
                log(f"moderngl: still can't overwrite {dest} after "
                    f"{attempts} tries -- if the official (unmodified) "
                    f"Client.exe is running, close it and retry; it shares "
                    f"this _internal/ folder and can hold the old .pyd "
                    f"loaded.")
                raise
            time.sleep(delay)


def fresh_scratch_subdir(name) -> Path:
    d = SCRATCH / name
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# orjson: vendored one level too deep (_internal/orjson/orjson.*.pyd) --
# Python sees the empty `orjson` package dir before the real extension.
# Needs to sit directly at _internal/orjson.*.pyd.
# ---------------------------------------------------------------------------
def fix_orjson():
    nested_dir = INTERNAL / "orjson"
    top_level = list(INTERNAL.glob("orjson.cp*.pyd"))
    if top_level and not nested_dir.exists():
        log("orjson: already correctly placed, skipping")
        return
    if nested_dir.is_dir():
        nested_pyd = list(nested_dir.glob("orjson.cp*.pyd"))
        if nested_pyd and not top_level:
            shutil.move(str(nested_pyd[0]), str(INTERNAL / nested_pyd[0].name))
            log("orjson: moved .pyd up to _internal/ top level")
        shutil.rmtree(nested_dir)
        log("orjson: removed stray nested orjson/ directory")
    if not list(INTERNAL.glob("orjson.cp*.pyd")):
        log("orjson: WARNING -- no orjson .pyd found anywhere, can't fix placement")


# ---------------------------------------------------------------------------
# sounddevice / soundfile: entirely absent from the vendor install (only the
# native _sounddevice_data/_soundfile_data folders ship loose). Pure-Python
# + cffi, so a plain pip install (with cp314 wheels) is enough.
# ---------------------------------------------------------------------------
def fix_sounddevice_soundfile():
    missing = [name for name in ("sounddevice.py", "soundfile.py")
               if not (INTERNAL / name).exists()]
    if not missing:
        log("sounddevice/soundfile: already installed, skipping")
    else:
        log(f"sounddevice/soundfile: installing (missing: {missing})")
        result = subprocess.run(
            [str(PYTHON_EXE), "-m", "pip", "install", "--target", str(INTERNAL),
             "sounddevice", "soundfile", "cffi", "-q"],
            capture_output=True, text=True)
        if result.returncode != 0:
            log("sounddevice/soundfile: install FAILED")
            print(result.stdout[-3000:])
            print(result.stderr[-3000:])
            raise SystemExit(1)
        log("sounddevice/soundfile: installed")

    # Both data folders need __init__.py -- _soundfile_data.__file__ must be
    # non-None (soundfile.py does os.path.dirname(_soundfile_data.__file__)),
    # and it's harmless/cheap to add the same to _sounddevice_data.
    for name in ("_soundfile_data", "_sounddevice_data"):
        d = INTERNAL / name
        init = d / "__init__.py"
        if d.is_dir() and not init.exists():
            init.write_text("")
            log(f"{name}: added missing __init__.py")


# ---------------------------------------------------------------------------
# libpng16-16.dll: SDL2_image can only find its codec DLLs via same-
# directory search reliably; the vendor ships libpng16-16.dll at
# _internal/ top level but SDL2_image.dll lives in _internal/pygame/.
# ---------------------------------------------------------------------------
def fix_libpng():
    dest = INTERNAL / "pygame" / "libpng16-16.dll"
    if dest.exists():
        log("libpng16-16.dll: already copied into pygame/, skipping")
        return
    src = INTERNAL / "libpng16-16.dll"
    if not src.exists():
        log("libpng16-16.dll: WARNING -- not found anywhere in _internal/, can't fix")
        return
    shutil.copy2(src, dest)
    log("libpng16-16.dll: copied into pygame/")


def main():
    if not PYTHON_EXE.exists():
        log(f"ERROR: {PYTHON_EXE} not found -- custom_build/'s Python runtime "
            f"hasn't been set up at all yet. This script only reapplies fixes "
            f"to an existing runtime.")
        raise SystemExit(1)

    fix_pygame()
    fix_numpy()
    fix_glcontext()
    fix_moderngl()
    fix_orjson()
    fix_sounddevice_soundfile()
    fix_libpng()

    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)

    log("Verifying with a real smoke test...")
    smoke = (
        "import os; os.add_dll_directory(r'%s')\n"
        "import pygame; pygame.init()\n"
        "import numpy, glcontext, orjson, sounddevice, soundfile\n"
        "pygame.display.set_mode((64,64), pygame.OPENGL | pygame.DOUBLEBUF)\n"
        "import moderngl; ctx = moderngl.create_context()\n"
        "print('OK:', pygame.version.ver, numpy.__version__, ctx.info.get('GL_VERSION'))\n"
    ) % INTERNAL
    result = subprocess.run([str(PYTHON_EXE), "-c", smoke],
                             capture_output=True, text=True)
    if result.returncode != 0:
        log("SMOKE TEST FAILED:")
        print(result.stdout)
        print(result.stderr)
        raise SystemExit(1)
    log("Smoke test passed: " + result.stdout.strip().splitlines()[-1])


if __name__ == "__main__":
    main()

"""Start the Star Empire client with the mods in mods/ applied.

Run by ``Launch Modded Client.bat`` with the package's own Python (runtime/python): the only file anyone has to run.
It runs the official launcher if there is one (game_updater.py) so the game is installed and up to date, finds the game
(see modloader.find_layout), puts the package's own copies of the game's third-party libraries (pygame, numpy, moderngl
...) ahead of the game's folder, lets the mod loader patch the game's files in memory (a mod that no longer fits the game
is switched off and named in a message), and runs the game's Client.py.

The game's files are never changed. If the mod loader itself fails the game starts without any mods and the reason is
written to mods_report.txt. What the update step did is in launch_log.txt.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))        # <package>/modloader
_ROOT = os.path.dirname(_HERE)                             # <package>
_LIB = os.path.join(_ROOT, "runtime", "lib")               # pygame, numpy, moderngl ... and their DLLs


def _say(text: str) -> None:
    """pythonw has no console: show a message box where it cannot be missed."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, "Star Empire mods", 0x10)
    except Exception:
        pass


sys.path.insert(0, _HERE)
try:
    import modloader
except Exception:
    import traceback
    _say("The mod loader could not be loaded:\n\n" + traceback.format_exc())
    raise SystemExit(1)

# The first time (mods are on offer, none is in use, nothing was ever chosen) the mod picker window opens and the start waits
# for it: tick the mods, press "Apply and play". Closing the window cancels the start. Later starts never open it (Choose
# Mods.bat does, to change the mods). If the window itself cannot open, the game starts with the mods there are.
try:
    import modpicker_model
    if not modpicker_model.choose_on_first_run(_ROOT):
        raise SystemExit(0)
except Exception:
    pass

# The one launch file does everything: if the official launcher is in the game's folder (or this one) it is run first, so
# the game is installed or brought up to date, and the plain client it opens is closed again (game_updater.py). Then the
# mods are checked as the game starts below: a mod that no longer fits the game's code is switched off and named in a
# message, and the rest keep working. Nothing in this step may stop the modded client from starting.
try:
    import game_updater
    _note = game_updater.update_game(os.path.dirname(str(modloader.INTERNAL_DIR)), _ROOT)
except Exception as _exc:
    _note = f"the update step could not run: {_exc}"
if _note:
    try:
        import time
        with open(os.path.join(_ROOT, "launch_log.txt"), "w", encoding="utf-8") as _log:
            _log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {_note}\n")
    except OSError:
        pass

_problem = modloader.game_problem()
if _problem:
    _say(_problem)
    raise SystemExit(1)

_INTERNAL = str(modloader.INTERNAL_DIR)
_GAME = os.path.dirname(_INTERNAL)

# The package's Python and its stdlib come first (python314._pth), then the package's own copies of the game's libraries,
# then the game's folder: the game's own copies of pygame, numpy ... (which the launcher may replace at any update, and
# which are not made for this Python) are never the ones used, and the game's modules are found last, as before.
if os.path.isdir(_LIB):
    os.add_dll_directory(_LIB)
    if _LIB not in sys.path:
        sys.path.append(_LIB)
sys.path.append(_INTERNAL)
os.chdir(_GAME)

# The moderngl in runtime/lib is stricter than the one the game is written for: it wants a LIST for an array uniform, the
# game sends a tuple, and the first frame in the world then raises "the value must be a list not tuple" and the client
# closes right after login. This shim (modloader/moderngl_compat.py) puts such values in the shape it wants. It belongs
# to the runtime, not to a mod, so it is on even with an empty mods/ folder. STAR_EMPIRE_NO_MODERNGL_COMPAT=1 turns it off.
try:
    import moderngl_compat
    moderngl_compat.install()
except Exception:
    import traceback
    _say("The OpenGL compatibility fix could not be installed, so the game is likely to close right after login:\n\n"
         + traceback.format_exc())

_mods = None
try:
    _mods = modloader.install(interactive=True)
except Exception:
    import traceback
    try:
        with open(os.path.join(str(modloader.REPO_DIR), "mods_report.txt"), "w", encoding="utf-8") as _report:
            _report.write("The mod loader failed, so the client started WITHOUT any mods:\n\n" + traceback.format_exc())
    except OSError:
        pass

if _mods is not None:
    _mods.run_main("Client.py", {"__name__": "__main__"})
else:
    _client_path = os.path.join(_INTERNAL, "Client.py")
    with open(_client_path, "rb") as _f:
        _code = compile(_f.read(), _client_path, "exec")
    exec(_code, {"__name__": "__main__", "__file__": _client_path})

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_INTERNAL = os.path.join(_ROOT, "_internal")

os.add_dll_directory(_INTERNAL)
sys.path.insert(0, _INTERNAL)
os.chdir(_ROOT)

# The moderngl this runtime is built with is stricter than the one the game is written for (it wants a list for an array
# uniform): without this shim the first frame in the world raises "the value must be a list not tuple". It is part of
# the runtime, so it is on with or without mods. STAR_EMPIRE_NO_MODERNGL_COMPAT=1 turns it off.
try:
    import moderngl_compat
    moderngl_compat.install()
except Exception:
    pass                                                    # the game then reports the real problem

# The mods live in mods/ as patches to the dev's files, which stay as shipped (see _internal/modloader.py):
# the loader builds the patched source in memory and runs it. A bug in the loader itself must not keep the
# game from starting, so then the client runs exactly as the dev shipped it and the reason is written down.
_mods = None
try:
    import modloader
    _mods = modloader.install(interactive=True)
except Exception:
    import traceback
    try:
        with open(os.path.join(_ROOT, "mods_report.txt"), "w", encoding="utf-8") as _report:
            _report.write("The mod loader failed, so the client started WITHOUT any mods:\n\n"
                          + traceback.format_exc())
    except OSError:
        pass

if _mods is not None:
    _mods.run_main("Client.py", {"__name__": "__main__"})
else:
    _client_path = os.path.join(_INTERNAL, "Client.py")
    with open(_client_path, "rb") as f:
        _code = compile(f.read(), _client_path, "exec")
    exec(_code, {"__name__": "__main__", "__file__": _client_path})

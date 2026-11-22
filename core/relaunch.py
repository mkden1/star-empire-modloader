"""relaunch.py - start a new client once this one has closed (the Log Out button, the login screen's Change Mods button).

The game cannot go back to its login screen without ending the process: after login the world window owns the display and the
process ends when it closes. So "log out" and "apply other mods" are the same two steps: close the game the normal way (the
caller posts ``pygame.QUIT``, as the ESC menu's Exit Game does) and start a fresh client that waits for this one to be gone.

``restart_after_exit()`` starts a small detached helper process (this very file, run as a script) that waits for this process's
PID to end and then starts the launch command again. The new client skips the official launcher's update check
(``STAR_EMPIRE_NO_UPDATE=1``: the game was just updated and the player is waiting). Nothing here may stop the game: every
function is total and says False / None when it cannot do its job.

Standard library only, so the helper starts under any Python the game runs on.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ENV_NO_UPDATE = "STAR_EMPIRE_NO_UPDATE"
WAIT_SECONDS = 45.0             # the old client has this long to finish closing; then the new one starts anyway

_DETACHED = 0x00000008          # DETACHED_PROCESS
_NEW_GROUP = 0x00000200         # CREATE_NEW_PROCESS_GROUP
_NO_WINDOW = 0x08000000         # CREATE_NO_WINDOW


# ── what to start ────────────────────────────────────────────────────────────

def _default_root() -> "Path | None":
    try:
        import modloader
        return Path(modloader.REPO_DIR)
    except Exception:
        return None


def launch_command(argv=None, executable=None, root=None) -> "list[str] | None":
    """The command that starts this client again: the interpreter and the script it was started with (and that script's own
    arguments). The script comes from ``sys.argv[0]``; a relative one is looked for under *root* (the game changes the working
    directory when it starts), and failing that the package's or the dev folder's own launch script is used. None when none is
    found."""
    argv = list(sys.argv if argv is None else argv)
    exe = executable if executable is not None else sys.executable
    root = Path(root) if root is not None else _default_root()
    if not exe:
        return None
    if argv and argv[0]:
        script = Path(argv[0])
        for candidate in ([script] if script.is_absolute() else ([root / script] if root is not None else [])):
            if candidate.is_file():
                return [str(exe), str(candidate), *argv[1:]]
    if root is not None:
        for relative in (("modloader", "start_modded_client.py"), ("custom_build", "run_client.py")):
            candidate = root.joinpath(*relative)
            if candidate.is_file():
                return [str(exe), str(candidate)]
    return None


def restart_after_exit(pid: "int | None" = None, command=None, root=None, timeout: float = WAIT_SECONDS, env=None,
                       popen=None) -> bool:
    """Start the helper that launches the client again once process *pid* (this one) has ended. True when it was started; False
    when there is nothing to start or the helper could not be (the caller then must not close the game)."""
    try:
        command = list(command) if command else launch_command(root=root)
        if not command or not sys.executable:
            return False
        cwd = str(root) if root is not None else (str(_default_root() or os.getcwd()))
        helper = [sys.executable, str(Path(__file__).resolve()), "--wait-pid", str(int(pid if pid is not None else os.getpid())),
                  "--timeout", str(float(timeout)), "--cwd", cwd, "--", *command]
        environment = dict(os.environ)
        environment[ENV_NO_UPDATE] = "1"
        environment.update(env or {})
        flags = (_DETACHED | _NEW_GROUP | _NO_WINDOW) if os.name == "nt" else 0
        (popen or subprocess.Popen)(helper, cwd=cwd, env=environment, creationflags=flags, close_fds=True,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


# ── the helper (this file run as a script) ───────────────────────────────────

def wait_for_exit(pid: int, timeout: float) -> bool:
    """Block until process *pid* has ended (True) or *timeout* seconds passed (False). A process that is already gone (or cannot be
    opened) counts as ended."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x00100000, False, int(pid))          # SYNCHRONIZE
        if not handle:
            return True
        try:
            return kernel.WaitForSingleObject(handle, int(max(0.0, timeout) * 1000)) == 0
        finally:
            kernel.CloseHandle(handle)
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(int(pid), 0)
        except OSError:
            return True
        time.sleep(0.1)
    return False


def main(argv) -> int:
    args = list(argv)
    pid, timeout, cwd = 0, WAIT_SECONDS, None
    while args:
        arg = args.pop(0)
        if arg == "--":
            break
        if arg == "--wait-pid" and args:
            pid = int(args.pop(0))
        elif arg == "--timeout" and args:
            timeout = float(args.pop(0))
        elif arg == "--cwd" and args:
            cwd = args.pop(0)
    if not args:
        return 2
    if pid:
        wait_for_exit(pid, timeout)
    flags = (_DETACHED | _NEW_GROUP | _NO_WINDOW) if os.name == "nt" else 0
    try:
        subprocess.Popen(args, cwd=cwd if cwd and os.path.isdir(cwd) else None, creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

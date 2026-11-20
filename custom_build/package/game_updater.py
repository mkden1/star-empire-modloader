"""game_updater.py - make sure the game is installed and up to date before the modded client starts.

The official launcher (StarEmpireLauncher.exe) downloads the game, and every update of it, into the folder it sits in,
and when it is done it opens the plain, unmodded client. ``update_game`` runs it, waits for it, and closes THAT plain
client again, so one launch file does everything: install or update the game, then start the modded client.

Only a Client.exe that was not running before the launcher started, and whose file is in the launcher's own folder, is
ever closed: a game you already have open (or any other program's Client.exe) is left alone.

Nothing here may keep the modded client from starting: ``update_game`` never raises, and without a launcher in the
folder it does nothing. ``STAR_EMPIRE_NO_UPDATE=1`` skips the whole step (offline, or to start at once). Standard
library only; every outside action (starting the launcher, listing and closing processes, the clock) is a parameter so
the logic can be tested without any of them.
"""
import os
import subprocess
import time
from pathlib import Path

LAUNCHER_GLOB = "StarEmpireLauncher*.exe"
ENV_OFF = "STAR_EMPIRE_NO_UPDATE"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)       # pythonw has no console: do not flash one for each helper

POLL_S = 1.0             # how often the processes are looked at while the launcher runs
GRACE_S = 10.0           # after the launcher has exited, how long to wait for the plain client it opens late
SETTLE_S = 10.0          # after the plain client was closed, how long the launcher may take to exit before we go on


def find_launcher(*folders) -> "Path | None":
    """The official launcher in the first of ``folders`` that has one (the newest, if there are several copies)."""
    for folder in folders:
        if not folder:
            continue
        found = [p for p in Path(folder).glob(LAUNCHER_GLOB) if p.is_file()]
        if found:
            return max(found, key=lambda p: p.stat().st_mtime)
    return None


def running_clients() -> "dict[int, str]":
    """``{process id: path of the file}`` of every running Client.exe (a program called Client, whoever's it is)."""
    command = "Get-Process -Name Client -ErrorAction SilentlyContinue | ForEach-Object { '{0}|{1}' -f $_.Id, $_.Path }"
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                             capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    found = {}
    for line in out.splitlines():
        pid, _, path = line.partition("|")
        if pid.strip().isdigit():
            found[int(pid)] = path.strip()
    return found


def close_process(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, creationflags=NO_WINDOW)


def _inside(path: str, folder) -> bool:
    """Whether the file at ``path`` is directly in ``folder`` (case-blind, as on Windows)."""
    if not path:
        return False
    return os.path.normcase(os.path.dirname(os.path.abspath(path))) == os.path.normcase(os.path.abspath(str(folder)))


def run_launcher(launcher: Path, before: "dict[int, str]", *, popen=subprocess.Popen, clients=running_clients,
                 close=close_process, sleep=time.sleep, clock=time.monotonic) -> "list[int]":
    """Start the launcher and wait for it, closing the plain client it opens. Returns the ids of the clients it closed.

    Both ways the launcher can behave are covered: it exits and leaves the client running, or it stays until the client
    is closed (then closing the client is what lets it finish). With no client opened at all it simply waits for the
    launcher, however long the download takes."""
    folder = launcher.parent
    process = popen([str(launcher)], cwd=str(folder))
    closed: "list[int]" = []
    exited_at = closed_at = None
    while True:
        for pid, path in clients().items():
            if pid not in before and pid not in closed and _inside(path, folder):
                close(pid)
                closed.append(pid)
                closed_at = clock()
        if process.poll() is not None:
            exited_at = clock() if exited_at is None else exited_at
            if closed or clock() - exited_at >= GRACE_S:
                return closed
        elif closed and closed_at is not None and clock() - closed_at >= SETTLE_S:
            return closed                       # it closed the client but keeps running: do not wait for it for ever
        sleep(POLL_S)


def update_game(game_dir, root, *, env=os.environ, **actions) -> str:
    """Run the official launcher if there is one (in ``game_dir``, else in ``root``). Returns a short note of what was
    done ("" when there was nothing to do). Never raises."""
    try:
        if env.get(ENV_OFF):
            return f"update step skipped ({ENV_OFF} is set)"
        launcher = find_launcher(game_dir, root)
        if launcher is None:
            return ""
        clients = actions.get("clients", running_clients)
        closed = run_launcher(launcher, clients(), **actions)
        return f"ran {launcher.name}" + (f"; closed the plain client it opened (process {', '.join(map(str, closed))})" if closed
                                          else "; it opened no client")
    except Exception as exc:                                 # the modded client must still start
        return f"the update step failed: {type(exc).__name__}: {exc}"

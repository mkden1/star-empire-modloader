"""Tests for relaunch.py: the command that starts the client again, and the helper process that starts it once the old client is gone
(run for real, against a real short-lived process)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import relaunch


def python() -> str:
    return sys.executable


class LaunchCommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "modloader").mkdir()
        (self.root / "custom_build").mkdir()
        self.package_script = self.root / "modloader" / "start_modded_client.py"
        self.dev_script = self.root / "custom_build" / "run_client.py"

    def touch(self, path: Path) -> Path:
        path.write_text("", encoding="utf-8")
        return path

    def test_it_is_the_interpreter_and_the_script_the_client_was_started_with(self):
        script = self.touch(self.package_script)
        self.assertEqual(relaunch.launch_command([str(script)], "pythonw.exe", self.root), ["pythonw.exe", str(script)])

    def test_the_scripts_own_arguments_come_along(self):
        script = self.touch(self.package_script)
        self.assertEqual(relaunch.launch_command([str(script), "--fast", "x"], "pythonw.exe", self.root),
                         ["pythonw.exe", str(script), "--fast", "x"])

    def test_a_relative_script_is_looked_for_under_the_root(self):
        script = self.touch(self.package_script)
        got = relaunch.launch_command([os.path.join("modloader", "start_modded_client.py")], "pythonw.exe", self.root)
        self.assertEqual(got, ["pythonw.exe", str(script)])

    def test_a_relative_script_is_the_one_asked_for_not_the_fallback(self):
        self.touch(self.package_script)
        dev = self.touch(self.dev_script)
        got = relaunch.launch_command([os.path.join("custom_build", "run_client.py"), "--x"], "pythonw.exe", self.root)
        self.assertEqual(got, ["pythonw.exe", str(dev), "--x"])

    def test_a_script_that_is_not_there_falls_back_to_the_packages_own(self):
        script = self.touch(self.package_script)
        got = relaunch.launch_command([r"C:\gone\start.py", "--fast"], "pythonw.exe", self.root)
        self.assertEqual(got, ["pythonw.exe", str(script)])                    # and not the other script's arguments

    def test_the_dev_folders_launch_script_is_the_last_resort(self):
        script = self.touch(self.dev_script)
        self.assertEqual(relaunch.launch_command(["-c"], "pythonw.exe", self.root), ["pythonw.exe", str(script)])

    def test_the_package_script_wins_over_the_dev_script(self):
        package = self.touch(self.package_script)
        self.touch(self.dev_script)
        self.assertEqual(relaunch.launch_command([], "pythonw.exe", self.root), ["pythonw.exe", str(package)])

    def test_nothing_to_start_is_none(self):
        self.assertIsNone(relaunch.launch_command([r"C:\gone\start.py"], "pythonw.exe", self.root))
        self.assertIsNone(relaunch.launch_command([], "pythonw.exe", self.root))
        script = self.touch(self.package_script)
        self.assertIsNone(relaunch.launch_command([str(script)], "", self.root))


class RestartTests(unittest.TestCase):
    """The helper process, run for real."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.marker = self.dir / "started.txt"
        self.children: list = []
        self.addCleanup(self._kill_children)

    def _kill_children(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait()

    def old_client(self, seconds: float):
        child = subprocess.Popen([python(), "-c", f"import time; time.sleep({seconds})"])
        self.children.append(child)
        return child

    def new_client_command(self):
        """A 'client' that writes where it was started and what the update switch said."""
        code = ("import os, sys; open(sys.argv[1], 'w').write(os.getcwd() + '|' + os.environ.get('STAR_EMPIRE_NO_UPDATE', 'unset'))")
        return [python(), "-c", code, str(self.marker)]

    def wait_for_marker(self, seconds=15.0) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.marker.exists() and self.marker.read_text(encoding="utf-8"):
                return True
            time.sleep(0.05)
        return False

    def test_the_new_client_starts_after_the_old_one_is_gone_and_not_before(self):
        old = self.old_client(2.0)
        self.assertTrue(relaunch.restart_after_exit(pid=old.pid, command=self.new_client_command(), root=self.dir))
        time.sleep(0.8)
        self.assertIsNone(old.poll())                                         # the old client is still closing
        self.assertFalse(self.marker.exists())
        old.wait()
        self.assertTrue(self.wait_for_marker())

    def test_the_new_client_starts_in_the_given_folder_and_skips_the_update_step(self):
        old = self.old_client(0.2)
        self.assertTrue(relaunch.restart_after_exit(pid=old.pid, command=self.new_client_command(), root=self.dir))
        self.assertTrue(self.wait_for_marker())
        folder, update = self.marker.read_text(encoding="utf-8").split("|")
        self.assertEqual(os.path.normcase(os.path.realpath(folder)), os.path.normcase(os.path.realpath(str(self.dir))))
        self.assertEqual(update, "1")

    def test_an_old_client_that_never_ends_does_not_stop_the_new_one_for_ever(self):
        old = self.old_client(60.0)
        self.assertTrue(relaunch.restart_after_exit(pid=old.pid, command=self.new_client_command(), root=self.dir, timeout=1.0))
        started = time.monotonic()
        self.assertTrue(self.wait_for_marker(20.0))
        self.assertGreaterEqual(time.monotonic() - started, 0.5)             # it did wait, then gave up waiting
        self.assertIsNone(old.poll())

    def test_a_client_that_is_already_gone_is_replaced_at_once(self):
        old = self.old_client(0.0)
        old.wait()
        self.assertTrue(relaunch.restart_after_exit(pid=old.pid, command=self.new_client_command(), root=self.dir))
        self.assertTrue(self.wait_for_marker())

    def test_extra_environment_reaches_the_new_client(self):
        code = "import os, sys; open(sys.argv[1], 'w').write(os.environ.get('MY_SWITCH', 'unset'))"
        old = self.old_client(0.1)
        self.assertTrue(relaunch.restart_after_exit(pid=old.pid, command=[python(), "-c", code, str(self.marker)], root=self.dir,
                                                    env={"MY_SWITCH": "on"}))
        self.assertTrue(self.wait_for_marker())
        self.assertEqual(self.marker.read_text(encoding="utf-8"), "on")

    def test_with_nothing_to_start_it_says_no_and_starts_nothing(self):
        started: list = []
        with mock.patch.object(relaunch, "launch_command", return_value=None):
            self.assertFalse(relaunch.restart_after_exit(root=self.dir, popen=lambda *a, **k: started.append(a)))
        self.assertEqual(started, [])

    def test_a_helper_that_cannot_be_started_is_a_no_not_an_error(self):
        def refuse(*args, **kwargs):
            raise OSError("no")

        self.assertFalse(relaunch.restart_after_exit(command=[python(), "-c", "pass"], root=self.dir, popen=refuse))

    def test_the_helper_is_this_file_run_with_the_wait_and_the_command(self):
        seen: dict = {}

        def fake(cmd, **kwargs):
            seen["cmd"], seen["kwargs"] = cmd, kwargs

        self.assertTrue(relaunch.restart_after_exit(pid=1234, command=["pythonw.exe", "go.py", "--a"], root=self.dir, timeout=9.0,
                                                    popen=fake))
        cmd = seen["cmd"]
        self.assertEqual(Path(cmd[1]).name, "relaunch.py")
        self.assertEqual(cmd[2:cmd.index("--")], ["--wait-pid", "1234", "--timeout", "9.0", "--cwd", str(self.dir)])
        self.assertEqual(cmd[cmd.index("--") + 1:], ["pythonw.exe", "go.py", "--a"])
        self.assertEqual(seen["kwargs"]["env"]["STAR_EMPIRE_NO_UPDATE"], "1")
        if os.name == "nt":                                                   # it outlives this process and shows no window
            flags = seen["kwargs"]["creationflags"]
            self.assertTrue(flags & relaunch._DETACHED and flags & relaunch._NO_WINDOW)

    def test_the_helper_starts_the_command_in_the_folder_it_is_given(self):
        elsewhere = self.dir / "elsewhere"
        elsewhere.mkdir()
        code = "import os, sys; open(sys.argv[1], 'w').write(os.getcwd())"
        here = os.getcwd()
        os.chdir(elsewhere)
        try:
            self.assertEqual(relaunch.main(["--cwd", str(self.dir), "--", python(), "-c", code, str(self.marker)]), 0)
        finally:
            os.chdir(here)
        self.assertTrue(self.wait_for_marker())
        self.assertEqual(os.path.normcase(os.path.realpath(self.marker.read_text(encoding="utf-8"))),
                         os.path.normcase(os.path.realpath(str(self.dir))))

    def test_the_helper_with_no_command_does_nothing(self):
        self.assertEqual(relaunch.main(["--wait-pid", "1", "--timeout", "0.1"]), 2)

    def test_the_helper_that_cannot_start_the_command_says_so_by_its_exit_code(self):
        self.assertEqual(relaunch.main(["--", str(self.dir / "no_such_program.exe")]), 1)


class WaitForExitTests(unittest.TestCase):
    def test_a_process_that_ends_is_waited_for(self):
        child = subprocess.Popen([python(), "-c", "import time; time.sleep(0.6)"])
        try:
            started = time.monotonic()
            self.assertTrue(relaunch.wait_for_exit(child.pid, 20.0))
            self.assertGreaterEqual(time.monotonic() - started, 0.3)
        finally:
            child.wait()

    def test_a_process_that_does_not_end_times_out(self):
        child = subprocess.Popen([python(), "-c", "import time; time.sleep(60)"])
        try:
            self.assertFalse(relaunch.wait_for_exit(child.pid, 0.5))
        finally:
            child.kill()
            child.wait()

    def test_a_process_that_does_not_exist_counts_as_ended_at_once(self):
        started = time.monotonic()
        self.assertTrue(relaunch.wait_for_exit(2 ** 31 - 8, 20.0))
        self.assertLess(time.monotonic() - started, 5.0)

    def test_a_process_that_is_gone_counts_as_ended(self):
        child = subprocess.Popen([python(), "-c", "pass"])
        child.wait()
        self.assertTrue(relaunch.wait_for_exit(child.pid, 5.0))


if __name__ == "__main__":
    unittest.main()

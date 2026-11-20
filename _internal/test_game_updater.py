"""game_updater.py: the update step of the one launch file. It runs the official launcher and closes the plain client the
launcher opens. Everything that touches the machine (starting the launcher, listing and closing processes, the clock) is
a parameter, so none of this starts a real program."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

import modloader

SOURCE = Path(modloader.REPO_DIR) / "custom_build" / "package" / "game_updater.py"
spec = importlib.util.spec_from_file_location("game_updater_under_test", SOURCE)
game_updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(game_updater)


class Folders(unittest.TestCase):
    def folder(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)


class FindLauncherTests(Folders):
    def test_the_first_folder_that_has_one_wins_and_the_newest_copy_is_taken(self):
        game, root = self.folder(), self.folder()
        old, new = game / "StarEmpireLauncher.exe", game / "StarEmpireLauncher(2).exe"
        old.write_text("x"), new.write_text("x")
        os.utime(old, (1, 1))
        (root / "StarEmpireLauncher.exe").write_text("x")
        self.assertEqual(game_updater.find_launcher(game, root), new)
        self.assertEqual(game_updater.find_launcher(self.folder(), root), root / "StarEmpireLauncher.exe")

    def test_no_launcher_anywhere_is_none_and_an_empty_folder_name_is_ignored(self):
        self.assertIsNone(game_updater.find_launcher(self.folder(), "", None, self.folder()))


class RunLauncherTests(Folders):
    """A fake machine: a clock the sleeps move, processes that appear at set times, and a launcher that exits at one."""

    def setUp(self):
        self.folder_path = self.folder()
        self.launcher = self.folder_path / "StarEmpireLauncher.exe"
        self.ours = str(self.folder_path / "Client.exe")
        self.elsewhere = str(self.folder() / "Client.exe")

    def run_it(self, schedule, before=None, exits_at=None, exits_when_closed=False):
        now = [0.0]
        closed = set()

        def clock():
            return now[0]

        def sleep(seconds):
            now[0] += seconds

        def clients():
            return {pid: path for pid, path, at in schedule if now[0] >= at and pid not in closed}

        class Process:
            def poll(self):
                if exits_at is not None and now[0] >= exits_at:
                    return 0
                if exits_when_closed and closed:
                    return 0
                return None

        result = game_updater.run_launcher(self.launcher, before or {}, popen=lambda *a, **k: Process(), clients=clients,
                                           close=closed.add, sleep=sleep, clock=clock)
        return result, closed, now[0]

    def test_the_plain_client_the_launcher_opens_is_closed_when_the_launcher_exits_and_leaves_it_running(self):
        result, closed, _ = self.run_it([(100, self.ours, 5.0)], exits_at=6.0)
        self.assertEqual((result, closed), ([100], {100}))

    def test_a_launcher_that_stays_until_the_client_is_closed_is_let_finish(self):
        result, closed, now = self.run_it([(100, self.ours, 5.0)], exits_when_closed=True)
        self.assertEqual(result, [100])
        self.assertLess(now, 20)

    def test_a_client_that_opens_a_little_after_the_launcher_exited_is_still_closed(self):
        result, _, _ = self.run_it([(100, self.ours, 8.0)], exits_at=3.0)
        self.assertEqual(result, [100])

    def test_a_launcher_that_opens_no_client_is_just_waited_for_and_nothing_is_closed(self):
        result, closed, now = self.run_it([], exits_at=3.0)
        self.assertEqual((result, closed), ([], set()))
        self.assertGreaterEqual(now, 3.0 + game_updater.GRACE_S)

    def test_a_launcher_that_never_exits_is_not_waited_for_for_ever_once_its_client_is_closed(self):
        result, _, now = self.run_it([(100, self.ours, 5.0)])
        self.assertEqual(result, [100])
        self.assertLess(now, 5.0 + game_updater.SETTLE_S + 3)

    def test_only_a_client_the_launcher_started_in_its_own_folder_is_ever_closed(self):
        schedule = [(50, self.ours, 0.0),            # already open before: the player's own game
                    (60, self.elsewhere, 5.0),       # another program's Client.exe, or a game in another folder
                    (70, self.ours, 5.0)]            # the one the launcher opened
        result, closed, _ = self.run_it(schedule, before={50: self.ours}, exits_at=6.0)
        self.assertEqual((result, closed), ([70], {70}))

    def test_a_client_without_a_known_path_is_left_alone(self):
        result, closed, _ = self.run_it([(80, "", 5.0)], exits_at=6.0)
        self.assertEqual((result, closed), ([], set()))


class UpdateGameTests(Folders):
    def fakes(self, calls):
        class Process:
            def poll(self):
                return 0
        return dict(popen=lambda args, **kw: calls.append(args) or Process(), clients=lambda: {}, close=lambda pid: None,
                    sleep=lambda s: None, clock=iter(range(0, 10_000, 5)).__next__)

    def test_without_a_launcher_nothing_runs_and_the_note_is_empty(self):
        calls = []
        self.assertEqual(game_updater.update_game(self.folder(), self.folder(), env={}, **self.fakes(calls)), "")
        self.assertEqual(calls, [])

    def test_the_launcher_beside_the_game_is_run_in_preference_to_one_in_the_package_folder(self):
        game, root = self.folder(), self.folder()
        (game / "StarEmpireLauncher.exe").write_text("x")
        (root / "StarEmpireLauncher.exe").write_text("x")
        calls = []
        note = game_updater.update_game(game, root, env={}, **self.fakes(calls))
        self.assertEqual(calls, [[str(game / "StarEmpireLauncher.exe")]])
        self.assertTrue(note.startswith("ran StarEmpireLauncher.exe"), note)

    def test_the_environment_switch_skips_the_whole_step(self):
        root = self.folder()
        (root / "StarEmpireLauncher.exe").write_text("x")
        calls = []
        note = game_updater.update_game(self.folder(), root, env={game_updater.ENV_OFF: "1"}, **self.fakes(calls))
        self.assertEqual(calls, [])
        self.assertIn("skipped", note)

    def test_a_failure_in_the_step_is_reported_and_never_raised(self):
        root = self.folder()
        (root / "StarEmpireLauncher.exe").write_text("x")

        def broken(*args, **kwargs):
            raise OSError("the launcher cannot be started")

        note = game_updater.update_game(self.folder(), root, env={}, popen=broken, clients=lambda: {})
        self.assertIn("update step failed", note)
        self.assertIn("cannot be started", note)


if __name__ == "__main__":
    unittest.main()

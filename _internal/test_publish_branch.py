"""publish_branch.py: makes the repository's ``modloader`` branch (the ready-to-run folder) from a built package. The branch
must hold the loader, the launch file and the runtime, and none of the game, the mods or what a player's machine writes;
the repository's own checkout must not be touched by it."""
import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import modloader

SOURCE = Path(modloader.REPO_DIR) / "custom_build" / "publish_branch.py"
spec = importlib.util.spec_from_file_location("publish_branch_under_test", SOURCE)
publish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publish)

HAVE_GIT = shutil.which("git") is not None


def make_package(root: Path) -> Path:
    """A built package as the build leaves it: what goes on the branch and what does not."""
    files = {"Launch Modded Client.bat": "@echo off\r\n", "modctl.bat": "@echo off\r\n", "README.txt": "read me",
             "THIRD_PARTY_NOTICES.txt": "notices", "modloader/modloader.py": "# loader", "modloader/game_updater.py": "#",
             "runtime/python/python.exe": "exe", "runtime/lib/pygame/__init__.py": "#", "runtime/lib/SDL2.dll": "dll",
             "licenses/a.txt": "x", "mods/README.txt": "mods go here",
             # not on the branch:
             "mods_available/core/mod.json": "{}", "mods/combat_log/mod.json": "{}", ".built_by_build_package": "marker",
             "game_dir.txt": "# template", "runtime/lib/pygame/__pycache__/x.pyc": "pyc"}
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


class BranchFilesTests(unittest.TestCase):
    def test_the_loader_the_launch_file_and_the_runtime_go_on_the_branch_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as tmp:
            chosen = set(publish.branch_files(make_package(Path(tmp))))
        self.assertEqual(chosen, {"Launch Modded Client.bat", "modctl.bat", "README.txt", "THIRD_PARTY_NOTICES.txt",
                                  "modloader/modloader.py", "modloader/game_updater.py", "runtime/python/python.exe",
                                  "runtime/lib/pygame/__init__.py", "runtime/lib/SDL2.dll", "licenses/a.txt",
                                  "mods/README.txt"})


@unittest.skipUnless(HAVE_GIT, "needs git")
class CommitFolderTests(unittest.TestCase):
    def repo(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Path(tmp.name)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        return repo

    def tree(self, repo, branch="modloader"):
        out = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "--name-only", branch], capture_output=True, text=True)
        return set(out.stdout.split())

    def test_the_first_publish_is_a_root_commit_and_the_second_one_builds_on_it(self):
        repo = self.repo()
        with tempfile.TemporaryDirectory() as tmp:
            built = make_package(Path(tmp))
            first = publish.commit_folder(repo, built, "modloader", "first")
            self.assertIsNotNone(first)
            self.assertIn(".gitignore", self.tree(repo))
            self.assertIn(".gitattributes", self.tree(repo))
            self.assertIn("runtime/lib/SDL2.dll", self.tree(repo))               # a dll is NOT ignored on this branch
            self.assertIsNone(publish.commit_folder(repo, built, "modloader", "again"))   # nothing changed: no commit
            (built / "README.txt").write_text("a changed readme", encoding="utf-8")
            second = publish.commit_folder(repo, built, "modloader", "second")
        parents = subprocess.run(["git", "-C", str(repo), "rev-list", "--parents", "-n", "1", second],
                                 capture_output=True, text=True).stdout.split()
        self.assertEqual(parents[1:], [first])

    def test_publishing_leaves_the_repositorys_own_checkout_alone(self):
        repo = self.repo()
        (repo / "mine.txt").write_text("uncommitted work", encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            publish.commit_folder(repo, make_package(Path(tmp)), "modloader", "publish")
        status = subprocess.run(["git", "-C", str(repo), "status", "--short"], capture_output=True, text=True).stdout
        self.assertEqual(status.split(), ["??", "mine.txt"])                      # still just an untracked file
        self.assertEqual((repo / "mine.txt").read_text(encoding="utf-8"), "uncommitted work")
        self.assertFalse((repo / "runtime").exists())                             # nothing was checked out into it

    def test_the_branchs_gitignore_ignores_the_game_and_the_mods_but_not_the_runtime(self):
        repo = self.repo()
        (repo / ".gitignore").write_text(publish.GITIGNORE, encoding="utf-8")

        def ignored(path):
            return subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", path]).returncode == 0

        for path in ("Client.exe", "version.txt", "prune_list.txt", "StarEmpireLauncher(2).exe", "_internal/Client.py",
                     "mods_available/core/mod.json", "mods/combat_log/mod.json", "game_dir.txt", "star_empire_client.log",
                     "mods_report.txt", "launch_log.txt", "capture_settings.json", "scan_data/x.csv", ".mod_cache/x.bin"):
            self.assertTrue(ignored(path), path)
        for path in ("mods/README.txt", "runtime/python/python.exe", "runtime/lib/SDL2.dll", "runtime/lib/numpy/x.pyd",
                     "modloader/modloader.py", "Launch Modded Client.bat", "README.txt", "runtime/lib/pygame/data.json"):
            self.assertFalse(ignored(path), path)


if __name__ == "__main__":
    unittest.main()

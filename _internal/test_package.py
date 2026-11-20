"""The stand-alone package (custom_build/build_package.py, custom_build/package/): the mod manager, what is copied and what
never is, and how a rebuild treats the folder it builds into. The full build-and-verify run is
``custom_build\\python.exe custom_build\\build_package.py``; this keeps its pieces honest in every test run."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

import modloader

REPO = Path(modloader.REPO_DIR)
CUSTOM_BUILD = REPO / "custom_build"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


modctl = load("package_modctl", CUSTOM_BUILD / "package" / "modctl.py")
build_package = load("package_build", CUSTOM_BUILD / "build_package.py")


class Folders(unittest.TestCase):
    def make(self, **mods):
        """A temp ``mods_available`` with the given mods ({name: {file: text}}) and an empty ``mods``."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, files in mods.items():
            (root / "mods_available" / name).mkdir(parents=True)
            for fname, text in files.items():
                (root / "mods_available" / name / fname).write_text(text, encoding="utf-8")
        (root / "mods").mkdir()
        return str(root / "mods_available"), str(root / "mods")


class ModctlTests(Folders):
    def catalogue(self):
        return self.make(core={"mod.json": '{"description": "shared"}', "core.py": "X = 1\n"},
                         a={"mod.json": '{"requires": ["core"], "description": "a mod"}'},
                         b={"mod.json": '{"requires": ["a"]}'},
                         solo={})

    def test_adding_a_mod_brings_everything_it_requires_all_the_way_down(self):
        available, installed = self.catalogue()
        copied, unknown = modctl.add(["b"], available, installed)
        self.assertEqual((sorted(copied), unknown), (["a", "b", "core"], []))
        self.assertEqual(modctl.installed_names(installed), {"a", "b", "core"})
        self.assertTrue((Path(installed) / "core" / "core.py").is_file())

    def test_what_is_already_there_is_not_copied_again(self):
        available, installed = self.catalogue()
        modctl.add(["a"], available, installed)
        (Path(installed) / "core" / "core.py").write_text("X = 99\n", encoding="utf-8")     # the user's own edit
        copied, _ = modctl.add(["b"], available, installed)
        self.assertEqual(copied, ["b"])
        self.assertEqual((Path(installed) / "core" / "core.py").read_text(encoding="utf-8"), "X = 99\n")

    def test_all_adds_every_mod_and_an_unknown_name_is_reported(self):
        available, installed = self.catalogue()
        copied, unknown = modctl.add(["all"], available, installed)
        self.assertEqual(sorted(copied), ["a", "b", "core", "solo"])
        copied, unknown = modctl.add(["nope", "solo"], available, installed)
        self.assertEqual((copied, unknown), ([], ["nope"]))

    def test_removing_a_mod_another_installed_mod_needs_is_refused_unless_forced(self):
        available, installed = self.catalogue()
        modctl.add(["b"], available, installed)
        removed, refused = modctl.remove(["core"], installed)
        self.assertEqual((removed, refused), ([], {"core": ["a"]}))
        removed, refused = modctl.remove(["b", "a", "core"], installed)         # all together is fine
        self.assertEqual((sorted(removed), refused), (["a", "b", "core"], {}))
        modctl.add(["b"], available, installed)
        removed, _ = modctl.remove(["core"], installed, force=True)
        self.assertEqual(removed, ["core"])

    def test_update_replaces_only_the_copies_that_differ_and_leaves_the_users_own_mods(self):
        available, installed = self.catalogue()
        modctl.add(["b"], available, installed)
        (Path(installed) / "mine").mkdir()                                       # not in mods_available/: not ours to touch
        (Path(installed) / "mine" / "mod.json").write_text("{}", encoding="utf-8")
        self.assertEqual(modctl.update(["all"], available, installed), ([], [], []))        # nothing differs yet
        (Path(available) / "core" / "core.py").write_text("X = 2\n", encoding="utf-8")      # the fixed mod
        (Path(installed) / "a" / "stale.pyc").write_bytes(b"x")                              # caches never count
        updated, added, unknown = modctl.update(["all"], available, installed)
        self.assertEqual((updated, added, unknown), (["core"], [], []))
        self.assertEqual((Path(installed) / "core" / "core.py").read_text(encoding="utf-8"), "X = 2\n")
        self.assertTrue((Path(installed) / "mine" / "mod.json").is_file())

    def test_update_by_name_and_it_adds_what_the_new_version_now_needs(self):
        available, installed = self.catalogue()
        modctl.add(["solo"], available, installed)
        (Path(available) / "solo" / "mod.json").write_text('{"requires": ["core"]}', encoding="utf-8")
        updated, added, unknown = modctl.update(["solo", "nope"], available, installed)
        self.assertEqual((updated, added, unknown), (["solo"], ["core"], ["nope"]))
        self.assertTrue((Path(installed) / "core" / "core.py").is_file())

    def test_a_mods_tests_are_not_copied_and_do_not_count_as_a_difference(self):
        """The mods branch keeps each mod's tests beside its code; a player's copy does not need them."""
        available, installed = self.make(core={"mod.json": "{}", "core.py": "X = 1\n", "test_core.py": "tests",
                                               "conftest.py": "x", "expected_failures.txt": "x", "README.md": "# Core\n"})
        (Path(available) / "core" / "screenshots").mkdir()
        (Path(available) / "core" / "screenshots" / "a.png").write_bytes(b"png")
        modctl.add(["core"], available, installed)
        self.assertEqual(sorted(p.name for p in (Path(installed) / "core").iterdir()), ["core.py", "mod.json"])
        (Path(available) / "core" / "test_core.py").write_text("more tests", encoding="utf-8")
        (Path(available) / "core" / "README.md").write_text("# Core, reworded\n", encoding="utf-8")
        (Path(available) / "core" / "screenshots" / "b.png").write_bytes(b"png2")
        self.assertEqual(modctl.update(["all"], available, installed), ([], [], []))        # none of that is a change
        (Path(available) / "core" / "core.py").write_text("X = 2\n", encoding="utf-8")
        self.assertEqual(modctl.update(["all"], available, installed), (["core"], [], []))     # a real change still counts

    def test_the_listing_shows_what_is_in_use_what_each_needs_and_strangers(self):
        available, installed = self.catalogue()
        modctl.add(["a"], available, installed)
        (Path(installed) / "mine").mkdir()
        text = modctl.listing(available, installed)
        self.assertIn("[x] a  (needs: core)", text)
        self.assertIn("[ ] b  (needs: a)", text)
        self.assertIn("a mod", text)
        self.assertIn("In mods/ but not in mods_available/: mine", text)

class BuildPackageTests(unittest.TestCase):
    def test_mod_folders_are_copied_without_their_tests_and_bytecode(self):
        keep = build_package.keep_mod_file
        for name in ("combat_log.py", "Client.py.patch", "mod.json", "scan_stations.js", "dashboard.html"):
            self.assertTrue(keep(name), name)
        for name in ("test_combat_log.py", "test_scan_stations.js", "conftest.py", "expected_failures.txt", "__pycache__",
                     "combat_log.cpython-314.pyc"):
            self.assertFalse(keep(name), name)

    def test_what_a_game_run_leaves_behind_is_never_copied_out_of_a_mod_folder(self):
        """A saved login, per-machine settings, logs and scan data cannot ride along into a package, wherever they are."""
        keep = build_package.keep_mod_file
        for name in ("options.txt", "capture_settings.json", "galaxy_map_window.json", "ship_layouts_window.json",
                     "range_rings.json", "mods_report.txt", "star_empire_client.log", "crash.log", "settings.json.old",
                     "scan_data"):
            self.assertFalse(keep(name), name)
        self.assertTrue(keep("mod.json"))                      # a mod's own json is shipped

    def test_the_launcher_and_the_loader_agree_on_the_package_layout(self):
        text = (CUSTOM_BUILD / "package" / "start_modded_client.py").read_text(encoding="utf-8")
        self.assertIn('os.path.join(_ROOT, "runtime", "lib")', text)
        self.assertIn("modloader.game_problem()", text)
        self.assertIn("sys.path.append(_INTERNAL)", text)          # the game's folder LAST: its own libraries lose
        self.assertNotIn("sys.path.insert(0, _INTERNAL)", text)
        # the update step runs first: it may be what installs the game that the check below looks for
        self.assertLess(text.index("game_updater.update_game("), text.index("modloader.game_problem()"))

    def test_the_launcher_fixes_moderngl_itself_so_even_the_plain_game_runs(self):
        """2026-11-20: the package's first real launch (mods/ empty) closed right after login with "the value must be a
        list not tuple", because that fix was an optional mod. It is the runtime's, applied before the game starts."""
        text = (CUSTOM_BUILD / "package" / "start_modded_client.py").read_text(encoding="utf-8")
        self.assertIn("moderngl_compat.install()", text)
        self.assertLess(text.index("moderngl_compat.install()"), text.index("run_main("))
        self.assertLess(text.index("sys.path.append(_LIB)"), text.index("moderngl_compat.install()"))
        self.assertIn("moderngl_compat.py", build_package.RUNTIME_FILES)
        for name in build_package.RUNTIME_FILES:                    # the runtime's own files are in _internal/
            self.assertTrue((Path(modloader.INTERNAL_DIR) / name).is_file(), name)

    def test_the_readme_keeps_the_part_that_fits_how_the_mods_come(self):
        template = "A\n{{BUNDLED}}\nin the package: {{COUNT}}\n{{/BUNDLED}}\n{{SEPARATE}}\nfrom the mods branch\n{{/SEPARATE}}\nZ\n"
        mods = [type("M", (), {"name": "a", "description": "does a", "requires": ()})()]
        bundled = build_package.readme_text(template, mods, with_mods=True)
        separate = build_package.readme_text(template, mods, with_mods=False)
        self.assertEqual(bundled, "A\nin the package: 1\nZ\n")
        self.assertEqual(separate, "A\nfrom the mods branch\nZ\n")
        for text in (bundled, separate):
            self.assertNotIn("{{", text)

    def test_the_real_readme_template_reads_cleanly_both_ways(self):
        template = (CUSTOM_BUILD / "package" / "README.txt.tpl").read_text(encoding="utf-8")
        for with_mods in (True, False):
            text = build_package.readme_text(template, [], with_mods)
            self.assertNotIn("{{", text)
            self.assertIn("Launch Modded Client.bat", text)
        self.assertIn("git clone --branch mods", build_package.readme_text(template, [], False))
        self.assertNotIn("git clone", build_package.readme_text(template, [], True))

class RebuildKeepsTheGameTests(unittest.TestCase):
    """The output folder doubles as an install: the game is put into it with the official launcher, and mods are chosen
    in it. A rebuild must replace only what the build makes."""

    USERS_OWN_EDIT = "whatever the user wrote in game_dir.txt"       # a rebuild must leave a file like this alone

    def lay_out(self, root: Path) -> None:
        for rel, text in {"modloader/old.py": "x", "mods_available/old/mod.json": "{}", "runtime/python/python.exe": "x",
                          "licenses/a.txt": "x", "README.txt": "old", "Launch Modded Client.bat": "old",
                          "Check Mods.bat": "from an earlier build",        # no longer made: removed on a rebuild
                          # not the build's:
                          "Client.exe": "exe", "_internal/Client.py": "game", "version.txt": "0.5.86",
                          "mods/combat_log/mod.json": "{}", "game_dir.txt": self.USERS_OWN_EDIT, "options.txt": "secret",
                          "star_empire_client.log": "log", ".mod_cache/x": "cache"}.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
        (root / build_package.MARKER).write_text("made by the script", encoding="utf-8")

    def test_a_rebuild_replaces_what_it_made_and_keeps_the_game_the_mods_and_the_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self.lay_out(out)
            build_package.prepare_output(out)
            for gone in ("modloader", "mods_available", "runtime", "licenses", "README.txt", "Launch Modded Client.bat",
                         "Check Mods.bat"):
                self.assertFalse((out / gone).exists(), gone)
            for kept in ("Client.exe", "_internal/Client.py", "version.txt", "mods/combat_log/mod.json", "game_dir.txt",
                         "options.txt", "star_empire_client.log", ".mod_cache/x", build_package.MARKER):
                self.assertTrue((out / kept).exists(), kept)
            self.assertEqual((out / "game_dir.txt").read_text(encoding="utf-8"), self.USERS_OWN_EDIT)

    @unittest.skipUnless(os.name == "nt", "only Windows refuses to rename a folder that holds an open file")
    def test_a_game_running_from_the_folder_stops_the_build_with_nothing_lost(self):
        """2026-11-20: a rebuild while the game ran from the folder deleted the loader and then stopped at the open log,
        leaving a package that could not start. Now every owned path is moved aside first and put back on a failure."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self.lay_out(out)
            with open(out / "runtime" / "python" / "python.exe", "a"):        # in use: its folder cannot be renamed
                with self.assertRaises(SystemExit) as stop:
                    build_package.prepare_output(out)
            self.assertIn("nothing was changed", str(stop.exception))
            for kept in ("modloader/old.py", "mods_available/old/mod.json", "runtime/python/python.exe", "licenses/a.txt",
                         "README.txt", "Launch Modded Client.bat", "Client.exe", "_internal/Client.py"):
                self.assertTrue((out / kept).exists(), kept)
            self.assertEqual([p.name for p in out.iterdir() if p.name.startswith(".old_")], [])      # all put back

    def test_the_logs_the_game_wrote_inside_the_replaced_folders_are_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self.lay_out(out)
            (out / "modloader" / "star_empire_client.log").write_text("the crash", encoding="utf-8")
            build_package.prepare_output(out)
            self.assertEqual((out / "kept_logs" / "modloader_star_empire_client.log").read_text(encoding="utf-8"), "the crash")
            self.assertFalse((out / "modloader").exists())

    def test_a_folder_this_script_did_not_make_is_never_touched(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "somebody's game").mkdir()
            with self.assertRaises(SystemExit):
                build_package.prepare_output(out)
            self.assertTrue((out / "somebody's game").is_dir())

if __name__ == "__main__":
    unittest.main()

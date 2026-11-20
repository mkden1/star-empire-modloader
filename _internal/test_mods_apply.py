"""The mods in mods/ against the game's files in _internal/ -- the check to run after every vanilla update.

The dev's files are never edited by us: every change to them is a patch in mods/<name>/ (see modloader.py and
mods/README.md). When an update changes the code a patch is anchored on, the first test here names the mod and
the hunk to re-point; that one mod stays switched off in the client until it is fixed.
"""
import os
import unittest
from pathlib import Path

import modloader

HERE = Path(__file__).parent
MODS = modloader.load_mods()
TARGETS = sorted({h.file for m in MODS for h in m.hunks})


def read(name: str) -> bytes:
    return (HERE / name).read_bytes()


class ModsApplyTests(unittest.TestCase):
    def test_every_mod_applies_to_the_games_files(self):
        result = modloader.build(MODS, read)
        self.assertEqual(result.failed, {}, "\n" + result.report())
        self.assertEqual(sorted(result.applied), sorted(m.name for m in MODS if m.enabled))

    def test_every_patched_file_still_compiles(self):
        result = modloader.build(MODS, read)
        self.assertTrue(result.sources)
        for name, data in result.sources.items():
            with self.subTest(file=name):
                compile(data.decode("utf-8-sig"), name, "exec")

    def test_the_game_files_are_the_devs_own(self):
        """A '# MOD' marker in a file a mod patches means someone edited the dev's file instead of writing a patch
        (the next update would then fight the edit)."""
        for name in TARGETS:
            with self.subTest(file=name):
                marked = [n for n, line in enumerate(read(name).decode("utf-8-sig").splitlines(), 1)
                          if "# MOD" in line]
                self.assertEqual(marked, [], f"{name} has '# MOD' lines: put them in a mod's patch instead")

    def test_every_mod_has_a_description_and_only_requires_mods_that_exist(self):
        names = {m.name for m in MODS}
        for m in MODS:
            with self.subTest(mod=m.name):
                self.assertFalse(m.error, m.error)
                self.assertTrue(m.description.strip(), "mod.json needs a description")
                self.assertTrue(m.hunks or any(m.path.glob("*.py")),
                                "a mod folder with neither a patch nor a module of its own does nothing")
                self.assertLessEqual(set(m.requires), names, f"{m.name} requires a mod that is not there")
                self.assertNotIn(m.name, m.requires)

    def test_every_patch_targets_a_game_file_that_exists(self):
        for name in TARGETS:
            with self.subTest(file=name):
                self.assertTrue((HERE / name).is_file(), f"mods patch {name}, which is not in _internal/")

    @unittest.skipIf(os.environ.get(modloader.ENV_OFF) or os.environ.get(modloader.ENV_DISABLE),
                     "mods are switched off in this environment")
    def test_the_client_runs_with_every_mod_applied(self):
        """conftest.py installs the loader: the modules the tests import are the patched ones."""
        info = modloader.installed()
        self.assertIsNotNone(info, "conftest.py must call modloader.install()")
        self.assertEqual(info.result.failed, {}, info.result.report())
        self.assertEqual(sorted(info.result.applied), sorted(m.name for m in MODS if m.enabled))
        import render_mixin
        self.assertIn("# MOD", modloader.game_source("render_mixin.py"))
        self.assertEqual(Path(render_mixin.__file__), HERE / "render_mixin.py")

    def test_one_changed_line_switches_off_only_the_mod_anchored_on_it(self):
        """Failure isolation on the real mods: change one line of the dev's code that only one mod's hunks use as
        an anchor (a mod nobody requires), and exactly that mod goes; every other mod still applies."""
        by_name = {m.name: m for m in MODS}
        required = {r for m in MODS for r in m.requires}
        victim = line = None
        for m in MODS:
            if m.name in required:
                continue
            elsewhere = {l.rstrip() for o in MODS if o is not m for h in o.hunks for l in h.old}
            own = [(h.file, l.rstrip()) for h in m.hunks for l in h.old if len(l.strip()) > 20 and h.file == "Client.py"
                   and l.rstrip() not in elsewhere]
            if own:
                victim, line = m, own[0][1]
                break
        self.assertIsNotNone(victim, "no mod has an anchor line of its own in Client.py")
        text = read("Client.py").decode("utf-8")
        changed = text.replace(line, line + "_RENAMED_BY_THE_DEV")
        self.assertNotEqual(text, changed)
        result = modloader.build(MODS, lambda n: changed.encode("utf-8") if n == "Client.py" else read(n))
        self.assertEqual(list(result.failed), [victim.name], result.report())
        self.assertIn("anchor not found", result.failed[victim.name][0])
        self.assertEqual(set(result.applied), set(by_name) - {victim.name})

    def test_a_mod_that_is_switched_off_takes_the_mods_that_need_it_along(self):
        by_name = {m.name: m for m in MODS}
        needed = by_name["render_alias"]
        line = needed.hunks[0].old[0].rstrip()
        text = read("Client.py").decode("utf-8")
        changed = text.replace(line, line + "_RENAMED_BY_THE_DEV")
        result = modloader.build(MODS, lambda n: changed.encode("utf-8") if n == "Client.py" else read(n))
        dependents = {m.name for m in MODS if "render_alias" in m.requires}
        self.assertTrue(dependents)
        self.assertTrue({"render_alias"} | dependents <= set(result.failed), result.report())

    def test_every_mod_module_that_attaches_methods_to_the_game_did_so_cleanly(self):
        """Mods put their big blocks of methods in modules (``ATTACHES = ((game module, class, source class),)``) and
        a one-line hook in the game's file attaches them; ``HOOKS = ((game module, class, method, function),)`` lists
        the functions they run after the game's own methods. An update that gives the game's class a method of the
        same name, renames a private name the module borrows or a method it hooks, or moves the install line, is
        reported here."""
        import importlib
        found = 0
        for mod in MODS:
            for path in sorted(mod.path.glob("*.py")):
                text = path.read_text(encoding="utf-8-sig")
                if path.name.startswith("test_") or not ("ATTACHES" in text or "HOOKS" in text):
                    continue
                found += 1
                with self.subTest(module=path.stem):
                    self.assertEqual(modloader.attachment_problems(importlib.import_module(path.stem)), [])
        self.assertGreater(found, 0)

    def test_every_expected_failure_names_a_dev_test_that_exists_and_has_a_reason(self):
        """expected_failures.txt lists dev tests a mod makes fail on purpose. A line that names a test that is gone
        (the dev renamed or deleted it) is dead weight and must be removed; one without a reason is a mystery."""
        import ast
        entries = modloader.expected_failures()
        self.assertTrue(entries, "wire_zlib3 lists the zlib2 handshake test")
        for test_id, mod, reason in entries:
            with self.subTest(test=test_id):
                self.assertNotEqual(reason, "changed on purpose by the mod", f"{mod}: say why in a # comment")
                parts = test_id.split("::")
                self.assertEqual(len(parts), 3, "file::Class::test_name")
                file, cls, name = parts
                path = next((p for p in [HERE / file, *modloader.MODS_DIR.glob(f"*/{file}")] if p.is_file()), None)
                self.assertIsNotNone(path, f"{file} is not in _internal/ or any mod folder")
                tree = ast.parse(path.read_text(encoding="utf-8-sig"))
                klass = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls), None)
                self.assertIsNotNone(klass, f"{file} has no class {cls}")
                self.assertTrue(any(isinstance(n, ast.FunctionDef) and n.name == name for n in klass.body),
                                f"{cls} has no test {name}")

    def test_no_two_mods_have_a_module_of_the_same_name(self):
        """Every mod folder is on sys.path: two folders holding the same module name would shadow one another."""
        clashes = {name: owners for name, owners in modloader.module_owners(MODS).items() if len(owners) > 1}
        self.assertEqual(clashes, {})

    def test_every_mod_lists_the_mods_whose_modules_it_cannot_work_without(self):
        """Mods are picked one by one: a mod that imports (at module level, or in a line its patch adds) a module that
        lives in another mod's folder must say so in mod.json's ``requires``, or the game would fail to start when that
        other mod is not in the folder. (An import inside a function is the mod's own to guard.)"""
        for mod in MODS:
            with self.subTest(mod=mod.name):
                needed = modloader.hard_dependencies(mod, MODS)
                covered = modloader.requirement_closure(MODS, [mod.name])
                undeclared = {other: why for other, why in needed.items() if other not in covered}
                self.assertEqual(undeclared, {}, f"add {sorted(undeclared)} to requires in mods/{mod.name}/mod.json")

    def test_every_mod_works_when_it_is_picked_alone_with_what_it_requires(self):
        """The core promise of the mods folder: pick any mod, drop it in with its requirements, and it applies to the
        game's files, the patched files compile, and no patch uses a name that only another mod's patch provides."""
        for mod in MODS:
            with self.subTest(mod=mod.name):
                self.assertEqual(modloader.standalone_problems(mod, MODS, read), [])

    def test_mods_order_is_stable_and_names_are_plain(self):
        self.assertEqual([m.name for m in MODS], [m.name for m in sorted(MODS, key=lambda m: (m.order, m.name))])
        for m in MODS:
            self.assertRegex(m.name, r"^[a-z][a-z0-9_]*$")


if __name__ == "__main__":
    unittest.main()

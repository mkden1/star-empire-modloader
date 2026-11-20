"""modloader.py: patch files, anchoring, failure isolation, conflicts, and feeding the patched source to Python."""
import builtins
import linecache
import os
import sys
import tempfile
import textwrap
import traceback
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import modloader
from modloader import Mod, PatchError, build, load_mods, parse_patch

GAME = textwrap.dedent("""\
    import os

    ZOOM = 0.10
    NAMES = ["a", "b"]


    def alpha():
        return 1


    def beta():
        return 2


    def gamma():
        return 3
    """)


def patch(text: str, mod: str = "m", file: str = "game.py") -> "list":
    return parse_patch(textwrap.dedent(text), mod, file, f"mods/{mod}/{file}.patch")


def mod_of(name: str, text: str, *, file: str = "game.py", **meta) -> Mod:
    m = Mod(name, Path(name), **meta)
    m.hunks = parse_patch(textwrap.dedent(text), name, file, f"mods/{name}/{file}.patch")
    return m


def run_build(mods, game: "dict[str, str]" = None, **kw):
    files = {"game.py": GAME} if game is None else game

    def read(name):
        if name not in files:
            raise FileNotFoundError(name)
        return files[name].encode("utf-8")
    return build(mods, read, **kw)


class ParseTests(unittest.TestCase):
    def test_a_hunk_has_context_changes_and_a_label(self):
        (h,) = parse_patch("note before the first header is ignored\n"
                           "@@ the zoom floor @@\n"
                           " import os\n"
                           " \n"                       # a blank context line is one space
                           "-ZOOM = 0.10\n"
                           "+ZOOM = 0.05\n"
                           "+# lower\n"
                           ' NAMES = ["a", "b"]\n', "m", "game.py", "mods/m/game.py.patch")
        self.assertEqual(h.label, "the zoom floor")
        self.assertEqual(h.before, ("import os", ""))
        self.assertEqual(h.removed, ("ZOOM = 0.10",))
        self.assertEqual(h.added, ("ZOOM = 0.05", "# lower"))
        self.assertEqual(h.after, ('NAMES = ["a", "b"]',))
        self.assertEqual(h.old, ("import os", "", "ZOOM = 0.10", 'NAMES = ["a", "b"]'))
        self.assertEqual(h.origin, "mods/m/game.py.patch:2")

    def test_an_insertion_has_no_removed_lines(self):
        (h,) = patch("""\
            @@ @@
             import os
            +import mine
            """)
        self.assertEqual((h.before, h.removed, h.added, h.after), (("import os",), (), ("import mine",), ()))

    def test_empty_lines_are_separators_and_a_blank_context_line_is_one_space(self):
        a, b = patch("""\
            @@ one @@
             import os
            +import x

            @@ two @@
             NAMES = ["a", "b"]
            +NAMES2 = []
            """)
        self.assertEqual((a.before, b.before), (("import os",), ('NAMES = ["a", "b"]',)))
        self.assertEqual([h.seq for h in (a, b)], [0, 1])

    def test_an_empty_line_inside_a_hunk_is_a_blank_context_line_but_one_before_a_header_is_a_separator(self):
        (a, b) = parse_patch("@@ one @@\n def f():\n\n\n+x = 1\n\n@@ two @@\n y = 2\n+z = 3\n\n", "m", "game.py", "p")
        self.assertEqual(a.before, ("def f():", "", ""))            # the editor stripped the spaces of two blank lines
        self.assertEqual((a.added, a.after), (("x = 1",), ()))      # the one before the next header is a separator
        self.assertEqual((b.before, b.after), (("y = 2",), ()))     # nor does a trailing one at the end count

    def test_bad_lines_and_empty_hunks_are_refused(self):
        for text in ("@@ @@\n garbage? no\nx = 1\n",           # a line with no prefix
                     "@@ @@\n import os\n",                    # changes nothing
                     "@@ @@\n+import mine\n",                  # an insertion with nothing to find it by
                     "@@ @@\n import os\n-a\n b\n+c\n d\n"):   # context between two changes
            with self.subTest(text=text), self.assertRaises(PatchError):
                parse_patch(text, "m", "game.py", "p")

    def test_crlf_patch_files_parse_like_lf_ones(self):
        (h,) = parse_patch("@@ x @@\r\n import os\r\n+import y\r\n", "m", "game.py", "p")
        self.assertEqual(h.added, ("import y",))


class BuildTests(unittest.TestCase):
    def applied(self, *mods, game=None, **kw):
        result = run_build(list(mods), game, **kw)
        return result, result.sources.get("game.py", b"").decode("utf-8")

    def test_an_insertion_goes_after_its_anchor(self):
        result, out = self.applied(mod_of("m", """\
            @@ @@
             import os
            +import mine
            """))
        self.assertTrue(result.ok)
        self.assertEqual(result.applied, ["m"])
        self.assertTrue(out.startswith("import os\nimport mine\n\nZOOM = 0.10\n"))

    def test_a_replacement_and_a_deletion(self):
        _result, out = self.applied(mod_of("m", """\
            @@ @@
            -ZOOM = 0.10
            +ZOOM = 0.05
            @@ @@
             def beta():
            -    return 2

            """))
        self.assertIn("ZOOM = 0.05\n", out)
        self.assertNotIn("return 2", out)
        self.assertIn("def beta():\n\n", out)

    def test_anchors_are_text_not_line_numbers(self):
        padded = "# one\n# two\n# three\n" + GAME
        _result, out = self.applied(mod_of("m", "@@ @@\n-ZOOM = 0.10\n+ZOOM = 0.05\n"), game={"game.py": padded})
        self.assertIn("ZOOM = 0.05", out)
        self.assertTrue(out.startswith("# one\n"))

    def test_trailing_whitespace_in_the_game_file_is_ignored_when_matching(self):
        game = GAME.replace("ZOOM = 0.10", "ZOOM = 0.10   ")
        _result, out = self.applied(mod_of("m", "@@ @@\n-ZOOM = 0.10\n+ZOOM = 0.05\n"), game={"game.py": game})
        self.assertIn("ZOOM = 0.05", out)

    def test_crlf_bom_and_a_missing_final_newline_come_back_byte_for_byte(self):
        for label, raw in (("crlf", GAME.replace("\n", "\r\n").encode()),
                           ("bom", b"\xef\xbb\xbf" + GAME.encode()),
                           ("no final newline", GAME.rstrip("\n").encode()),
                           ("crlf+bom", b"\xef\xbb\xbf" + GAME.replace("\n", "\r\n").encode())):
            with self.subTest(label):
                m = mod_of("m", "@@ @@\n-ZOOM = 0.10\n+ZOOM = 0.05\n+# note\n")
                result = build([m], lambda name: raw)
                out = result.sources["game.py"]
                self.assertEqual(out, raw.replace(b"ZOOM = 0.10", b"ZOOM = 0.05" + (b"\r\n" if b"\r\n" in raw else b"\n") + b"# note"))

    def test_an_unchanged_file_is_not_in_sources(self):
        result = run_build([mod_of("m", "@@ @@\n-ZOOM = 0.10\n+ZOOM = 0.05\n", file="game.py")],
                           {"game.py": GAME, "other.py": "x = 1\n"})
        self.assertEqual(list(result.sources), ["game.py"])

    def test_a_missing_anchor_switches_that_mod_off_and_only_that_mod(self):
        good = mod_of("good", "@@ @@\n-ZOOM = 0.10\n+ZOOM = 0.05\n")
        bad = mod_of("bad", "@@ lost @@\n-ZOOM = 0.20\n+ZOOM = 0.30\n")
        result, out = self.applied(bad, good)
        self.assertEqual(result.applied, ["good"])
        self.assertEqual(list(result.failed), ["bad"])
        self.assertIn("anchor not found", result.failed["bad"][0])
        self.assertIn("lost", result.failed["bad"][0])
        self.assertIn("ZOOM = 0.05", out)
        self.assertIn("SWITCHED OFF  bad", result.report())

    def test_an_ambiguous_anchor_is_refused_with_the_line_numbers(self):
        game = GAME + "\n\ndef alpha2():\n    return 1\n"
        result, _out = self.applied(mod_of("m", "@@ @@\n-    return 1\n+    return 10\n"), game={"game.py": game})
        self.assertEqual(result.applied, [])
        self.assertIn("ambiguous", result.failed["m"][0])
        self.assertIn("matches 2 places", result.failed["m"][0])

    def test_one_bad_hunk_in_any_file_switches_the_whole_mod_off(self):
        m = Mod("m", Path("m"))
        m.hunks = (parse_patch("@@ @@\n-ZOOM = 0.10\n+ZOOM = 0.05\n", "m", "game.py", "p1")
                   + parse_patch("@@ @@\n-nothing like this\n+x\n", "m", "other.py", "p2"))
        result = run_build([m], {"game.py": GAME, "other.py": "x = 1\n"})
        self.assertEqual(result.sources, {})
        self.assertIn("other.py", result.failed["m"][0])

    def test_a_missing_game_file_switches_the_mod_off(self):
        result = run_build([mod_of("m", "@@ @@\n import os\n+import y\n", file="gone.py")])
        self.assertIn("cannot read the game file", result.failed["m"][0])

    def test_insertions_at_one_spot_keep_mod_order(self):
        a = mod_of("a", "@@ @@\n import os\n+import a\n")
        b = mod_of("b", "@@ @@\n import os\n+import b\n")
        c = mod_of("c", "@@ @@\n import os\n+import c\n", order=-1)
        result, out = self.applied(a, b, c)
        self.assertTrue(out.startswith("import os\nimport a\nimport b\nimport c\n"))     # the caller's order
        ordered = sorted([a, b, c], key=lambda m: (m.order, m.name))
        _r, out2 = self.applied(*ordered)
        self.assertTrue(out2.startswith("import os\nimport c\nimport a\nimport b\n"))

    def test_order_numbers_interleave_insertions_of_different_mods_at_one_spot(self):
        a = mod_of("a", "@@ one @@ order=20\n import os\n+import a1\n@@ two @@ order=40\n import os\n+import a2\n")
        b = mod_of("b", "@@ one @@ order=30\n import os\n+import b1\n")
        c = mod_of("c", "@@ new hook @@\n import os\n+import c1\n")          # no number: last
        _result, out = self.applied(c, a, b)
        self.assertTrue(out.startswith("import os\nimport a1\nimport b1\nimport a2\nimport c1\n"), out[:80])

    def test_order_must_be_a_number_and_nothing_else_may_follow_the_header(self):
        for header in ("@@ x @@ order=abc", "@@ x @@ whatever", "@@ x @@ order=1 extra"):
            with self.subTest(header=header), self.assertRaises(PatchError):
                parse_patch(header + "\n import os\n+import y\n", "m", "game.py", "p")
        (h,) = parse_patch("@@ x @@ order=-3\n import os\n+import y\n", "m", "game.py", "p")
        self.assertEqual(h.priority, -3)
        (h,) = parse_patch("@@ x @@ order=3.5\n import os\n+import y\n", "m", "game.py", "p")      # between 3 and 4
        self.assertEqual(h.priority, 3.5)
        (h,) = parse_patch("@@ no closing\n import os\n+import y\n", "m", "game.py", "p")
        self.assertEqual((h.label, h.priority), ("no closing", modloader.DEFAULT_PRIORITY))

    def test_an_eof_hunk_only_matches_at_the_very_end_of_the_file(self):
        hook = mod_of("hook", "@@ closes the class @@ eof\n     return 3\n+install(Thing)\n")
        game = "class Thing:\n    def f(self):\n        return 3\n"
        ok = run_build([hook], {"game.py": game.replace("        return 3", "    return 3")})
        self.assertEqual(ok.sources["game.py"].decode(), game.replace("        return 3", "    return 3") + "install(Thing)\n")
        # the dev adds a method below: the hook would now land in the middle of the class, so the mod goes off
        grown = game.replace("        return 3", "    return 3") + "    def g(self):\n        return 4\n"
        bad = run_build([hook], {"game.py": grown})
        self.assertEqual(bad.sources, {})
        self.assertIn("very end of the file", bad.failed["hook"][0])
        # without eof the same hook would have been applied (and broken the class): that is what eof is for
        loose = mod_of("loose", "@@ x @@\n     return 3\n+install(Thing)\n")
        self.assertIn("loose", run_build([loose], {"game.py": grown}).applied)

    def test_eof_is_a_header_flag_next_to_order(self):
        (h,) = parse_patch("@@ x @@ eof order=3\n import os\n+import y\n", "m", "game.py", "p")
        self.assertEqual((h.at_end, h.priority), (True, 3))
        (h,) = parse_patch("@@ x @@\n import os\n+import y\n", "m", "game.py", "p")
        self.assertFalse(h.at_end)

    def test_load_mods_sorts_by_order_then_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, order in (("zeta", 0), ("alpha", 0), ("first", -5)):
                folder = Path(tmp, name)
                folder.mkdir()
                (folder / "mod.json").write_text(f'{{"order": {order}}}', encoding="utf-8")
            self.assertEqual([m.name for m in load_mods(Path(tmp))], ["first", "alpha", "zeta"])

    def test_two_mods_replacing_the_same_line_cannot_both_apply(self):
        a = mod_of("a", "@@ @@\n-ZOOM = 0.10\n+ZOOM = 0.05\n")
        b = mod_of("b", "@@ @@\n-ZOOM = 0.10\n+ZOOM = 0.07\n")
        result, out = self.applied(a, b)
        self.assertEqual(result.applied, ["a"])
        self.assertIn("overlaps", result.failed["b"][0])
        self.assertIn("ZOOM = 0.05", out)

    def test_an_insertion_inside_replaced_lines_conflicts_but_one_at_the_edge_does_not(self):
        replace = mod_of("a", "@@ @@\n-    return 1\n-\n-\n+    return 100\n+\n+\n")
        inside = mod_of("b", "@@ @@\n-    return 1\n+    return 100\n")            # same lines: conflict
        before = mod_of("c", "@@ @@\n def alpha():\n+    # first\n")                # at the start edge
        after = mod_of("d", "@@ @@\n def beta():\n+    # second\n")                 # next to the end edge
        result, out = self.applied(replace, inside, before, after)
        self.assertEqual(result.applied, ["a", "c", "d"])
        self.assertIn("b", result.failed)
        self.assertIn("def alpha():\n    # first\n    return 100\n", out)

    def test_a_mod_that_needs_a_switched_off_mod_is_switched_off_too(self):
        base = mod_of("base", "@@ @@\n-nothing like this\n+x\n")
        user = mod_of("user", "@@ @@\n-ZOOM = 0.10\n+ZOOM = 0.05\n", requires=("base",))
        stray = mod_of("stray", "@@ @@\n-NAMES = [\"a\", \"b\"]\n+NAMES = []\n", requires=("nobody",))
        result, _out = self.applied(base, user, stray)
        self.assertEqual(result.applied, [])
        self.assertIn("'base'", result.failed["user"][0])
        self.assertIn("'nobody'", result.failed["stray"][0])

    def test_mods_can_be_turned_off_in_the_file_or_by_name(self):
        a = mod_of("a", "@@ @@\n-ZOOM = 0.10\n+ZOOM = 0.05\n", enabled=False)
        b = mod_of("b", "@@ @@\n-NAMES = [\"a\", \"b\"]\n+NAMES = []\n")
        c = mod_of("c", "@@ @@\n import os\n+import c\n")
        result, _out = self.applied(a, b, c, disabled=["c"])
        self.assertEqual(result.applied, ["b"])
        self.assertEqual(result.skipped, ["a", "c"])
        self.assertTrue(result.ok)

    def test_the_game_files_are_never_changed_by_building(self):
        raw = GAME.encode()
        result = build([mod_of("m", "@@ @@\n-ZOOM = 0.10\n+ZOOM = 0.05\n")], lambda name: raw)
        self.assertEqual(result.vanilla["game.py"], raw)
        self.assertNotEqual(result.sources["game.py"], raw)


class LoadModsTests(unittest.TestCase):
    def test_a_mod_folder_is_read_with_its_metadata_and_patches(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp, "chat")
            folder.mkdir()
            (folder / "mod.json").write_text('{"description": "d", "order": 3, "requires": ["x"]}', encoding="utf-8")
            (folder / "game.py.patch").write_text("@@ one @@\n import os\n+import chat\n", encoding="utf-8")
            (folder / "other.py.patch").write_text("@@ two @@\n x = 1\n+y = 2\n", encoding="utf-8")
            (folder / "notes.txt").write_text("ignored", encoding="utf-8")
            (Path(tmp, ".hidden")).mkdir()
            (Path(tmp, "_template")).mkdir()
            (mod,) = load_mods(Path(tmp))
        self.assertEqual((mod.name, mod.description, mod.order, mod.requires), ("chat", "d", 3, ("x",)))
        self.assertEqual(mod.files, ["game.py", "other.py"])
        self.assertEqual([h.seq for h in mod.hunks], [0, 1])
        self.assertEqual(mod.hunks[0].origin, "mods/chat/game.py.patch:1")

    def test_a_broken_mod_folder_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, files in (("badjson", {"mod.json": "{nope"}),
                                ("badpatch", {"game.py.patch": "@@ @@\nwhat\n"}),
                                ("badutf8", {"game.py.patch": b"@@ @@\n \xff\n+x\n"})):
                folder = Path(tmp, name)
                folder.mkdir()
                for fname, content in files.items():
                    (folder / fname).write_bytes(content if isinstance(content, bytes) else content.encode())
            mods = load_mods(Path(tmp))
            self.assertEqual({m.name for m in mods}, {"badjson", "badpatch", "badutf8"})
            self.assertTrue(all(m.error for m in mods), [m.error for m in mods])
            result = build(mods, lambda name: GAME.encode())
        self.assertEqual(result.applied, [])
        self.assertEqual(set(result.failed), {"badjson", "badpatch", "badutf8"})

    def test_a_missing_mods_folder_means_no_mods(self):
        self.assertEqual(load_mods(Path("/no/such/folder")), [])


class InstallTests(unittest.TestCase):
    """The import hook, run_main, the code cache, the switches. A throwaway game folder and mods folder."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.internal, self.mods, self.cache = root / "_internal", root / "mods", root / "cache"
        self.internal.mkdir()
        (self.mods / "tweak").mkdir(parents=True)
        (self.internal / "fakegame.py").write_text(textwrap.dedent("""\
            import os
            HERE = os.path.dirname(os.path.abspath(__file__))

            def answer():
                return 1

            def boom():
                raise RuntimeError("x")
            """), encoding="utf-8")
        (self.mods / "tweak" / "fakegame.py.patch").write_text(textwrap.dedent("""\
            @@ answer @@
             def answer():
            -    return 1
            +    # patched by the tweak mod
            +    return 42
            """), encoding="utf-8")
        self.saved_path = list(sys.path)
        self.saved_meta = list(sys.meta_path)
        self.saved_installed = modloader.installed()          # conftest installed the real mods for the whole run
        self.addCleanup(self._restore)
        for key in ("fakegame",):
            sys.modules.pop(key, None)
        os.environ.pop(modloader.ENV_OFF, None)
        os.environ.pop(modloader.ENV_DISABLE, None)

    def _restore(self):
        modloader.uninstall()
        modloader._installed = self.saved_installed
        sys.path[:] = self.saved_path
        sys.meta_path[:] = self.saved_meta
        sys.modules.pop("fakegame", None)
        os.environ.pop(modloader.ENV_OFF, None)
        os.environ.pop(modloader.ENV_DISABLE, None)

    def install(self, **kw):
        return modloader.install(self.mods, self.internal, self.cache, **kw)

    def test_importing_a_patched_module_gives_the_patched_code_with_the_files_own_name(self):
        info = self.install()
        self.assertEqual(info.result.applied, ["tweak"])
        import fakegame
        self.assertEqual(fakegame.answer(), 42)
        self.assertEqual(Path(fakegame.__file__), self.internal / "fakegame.py")
        self.assertEqual(Path(fakegame.HERE), self.internal)             # asset paths keep working
        self.assertEqual((self.internal / "fakegame.py").read_text(encoding="utf-8").count("return 1"), 1)

    def test_tracebacks_and_getsource_show_the_lines_that_really_ran(self):
        self.install()
        import fakegame
        text = (self.internal / "fakegame.py").read_text(encoding="utf-8")
        shown = linecache.getlines(fakegame.__file__)
        self.assertNotEqual(shown, text.splitlines(True))                # the patched text, not the disk's
        self.assertIn("    # patched by the tweak mod\n", shown)
        try:
            fakegame.boom()
        except RuntimeError:
            frames = traceback.extract_tb(sys.exc_info()[2])
        self.assertEqual(frames[-1].line, 'raise RuntimeError("x")')

    def test_a_module_no_mod_touches_imports_normally(self):
        (self.internal / "plain.py").write_text("VALUE = 7\n", encoding="utf-8")
        self.addCleanup(sys.modules.pop, "plain", None)
        sys.path.insert(0, str(self.internal))
        self.install()
        import plain
        self.assertEqual(plain.VALUE, 7)

    def test_compiled_code_is_cached_by_the_patched_text(self):
        self.install()
        import fakegame                                                  # noqa: F401 (compiles and stores)
        entries = list(self.cache.glob("fakegame-*.bin"))
        self.assertEqual(len(entries), 1)
        data = self.internal / "fakegame.py"
        patched = modloader.installed().result.sources["fakegame.py"]
        with mock.patch.object(builtins, "compile", side_effect=AssertionError("recompiled")):
            code = modloader.compile_cached("fakegame.py", str(data), patched, self.cache)
        self.assertEqual(code.co_filename, str(data))
        changed = patched.replace(b"return 42", b"return 43")
        modloader.compile_cached("fakegame.py", str(data), changed, self.cache)
        entries = list(self.cache.glob("fakegame-*.bin"))
        self.assertEqual(len(entries), 1)                                # the stale entry was dropped

    def test_code_compiled_for_one_folder_is_not_reused_from_another(self):
        """A copied install brings its cache folder along: the same patched text under a new path must be compiled again, so
        the code carries the new file name (tracebacks, inspect.getsource and the linecache entry all go by it)."""
        text = b"def answer():\n    return 42\n"
        old_name, new_name = "old_install/_internal/fakegame.py", "new_install/_internal/fakegame.py"    # only names: no file
        first = modloader.compile_cached("fakegame.py", old_name, text, self.cache)
        second = modloader.compile_cached("fakegame.py", new_name, text, self.cache)
        self.assertEqual(first.co_filename, old_name)
        self.assertEqual(second.co_filename, new_name)
        self.assertEqual(len(list(self.cache.glob("fakegame-*.bin"))), 1)        # one entry per name: the old one is dropped
        third = modloader.compile_cached("fakegame.py", new_name, text, self.cache)
        self.assertEqual(third.co_filename, new_name)                            # and the new one is then reused

    def test_a_cache_that_cannot_be_written_only_costs_a_compile(self):
        blocker = Path(self._tmp.name, "blocked")
        blocker.write_text("a file where the folder should be", encoding="utf-8")
        code = modloader.compile_cached("fakegame.py", "x.py", b"A = 1\n", blocker / "cache")
        namespace = {}
        exec(code, namespace)
        self.assertEqual(namespace["A"], 1)

    def test_a_corrupt_cache_entry_is_ignored(self):
        self.install()
        import fakegame                                                  # noqa: F401
        (entry,) = self.cache.glob("fakegame-*.bin")
        entry.write_bytes(b"garbage")
        sys.modules.pop("fakegame")
        import fakegame as again
        self.assertEqual(again.answer(), 42)

    def test_a_mod_that_no_longer_fits_is_switched_off_and_the_game_runs_as_shipped(self):
        (self.internal / "fakegame.py").write_text("def answer():\n    return 2\n", encoding="utf-8")
        with self.assertLogs("modloader", level="WARNING") as logs:
            info = self.install()
        self.assertEqual(list(info.result.failed), ["tweak"])
        self.assertIn("tweak", logs.output[0])
        sys.path.insert(0, str(self.internal))
        import fakegame
        self.assertEqual(fakegame.answer(), 2)

    def test_run_main_runs_a_game_file_as_main_with_the_patch(self):
        (self.internal / "entry.py").write_text(textwrap.dedent("""\
            def answer():
                return 1
            RESULT = (__name__, answer(), __file__)
            """), encoding="utf-8")
        (self.mods / "tweak" / "entry.py.patch").write_text(
            "@@ @@\n def answer():\n-    return 1\n+    return 5\n", encoding="utf-8")
        info = self.install()
        namespace = {"__name__": "__main__"}
        info.run_main("entry.py", namespace)
        self.assertEqual(namespace["RESULT"], ("__main__", 5, str(self.internal / "entry.py")))
        plain = {"__name__": "__main__"}
        (self.internal / "solo.py").write_text("X = __name__\n", encoding="utf-8")
        info.run_main("solo.py", plain)
        self.assertEqual(plain["X"], "__main__")

    def test_the_off_switch_runs_the_game_as_shipped(self):
        os.environ[modloader.ENV_OFF] = "1"
        info = self.install()
        self.assertEqual(info.result.applied, [])
        sys.path.insert(0, str(self.internal))
        import fakegame
        self.assertEqual(fakegame.answer(), 1)

    def test_a_mod_can_be_turned_off_by_name(self):
        os.environ[modloader.ENV_DISABLE] = "other, tweak"
        info = self.install()
        self.assertEqual(info.result.skipped, ["tweak"])
        sys.path.insert(0, str(self.internal))
        import fakegame
        self.assertEqual(fakegame.answer(), 1)

    def test_a_mods_own_modules_become_importable(self):
        (self.mods / "tweak" / "tweaklib.py").write_text("NAME = 'tweaklib'\n", encoding="utf-8")
        self.addCleanup(sys.modules.pop, "tweaklib", None)
        self.install()
        import tweaklib
        self.assertEqual(tweaklib.NAME, "tweaklib")

    def test_a_switched_off_mods_modules_stay_importable_for_the_mods_that_use_them(self):
        (self.mods / "tweak" / "tweaklib.py").write_text("NAME = 'tweaklib'\n", encoding="utf-8")
        (self.internal / "fakegame.py").write_text("def answer():\n    return 2\n", encoding="utf-8")
        info = self.install()
        self.assertEqual(list(info.result.failed), ["tweak"])
        self.assertIn(str(self.mods / "tweak"), sys.path)

    def test_a_library_mod_with_no_patches_is_applied_and_its_modules_are_importable(self):
        (self.mods / "lib").mkdir()
        (self.mods / "lib" / "mod.json").write_text('{"description": "shared code"}', encoding="utf-8")
        (self.mods / "lib" / "libmod.py").write_text("VALUE = 5\n", encoding="utf-8")
        self.addCleanup(sys.modules.pop, "libmod", None)
        info = self.install()
        self.assertIn("lib", info.result.applied)
        import libmod
        self.assertEqual(libmod.VALUE, 5)

    def test_the_off_switch_still_puts_the_mod_modules_on_the_path(self):
        os.environ[modloader.ENV_OFF] = "1"
        (self.mods / "tweak" / "tweaklib.py").write_text("NAME = 'tweaklib'\n", encoding="utf-8")
        self.install()
        self.assertIn(str(self.mods / "tweak"), sys.path)

    def test_project_dir_is_the_folder_above_the_client(self):
        self.assertEqual(modloader.PROJECT_DIR, modloader.REPO_DIR.parent)
        self.assertEqual(modloader.REPO_DIR, modloader.INTERNAL_DIR.parent)

    def test_game_source_is_the_patched_text_when_a_mod_applies_and_the_files_otherwise(self):
        info = self.install()
        self.assertIn("return 42", modloader.game_source("fakegame.py"))
        self.assertNotIn("\r", modloader.game_source("fakegame.py"))
        (self.internal / "plain.py").write_bytes(b"\xef\xbb\xbfX = 1\r\n")
        self.assertEqual(modloader.game_source("plain.py"), "X = 1\n")
        modloader.uninstall()
        self.assertIsNone(modloader.installed())
        self.assertIn("def game_source", modloader.game_source("modloader.py"))   # not installed: the real _internal
        self.assertIsNotNone(info)

    def test_uninstall_takes_the_import_hook_out(self):
        def finders():
            return [f for f in sys.meta_path if isinstance(f, modloader._Finder)]
        before = finders()                      # conftest.py installed the real mods' hook for the whole run
        info = self.install()
        self.assertEqual(finders(), [info.finder] + before)                # the newest hook goes first
        modloader.uninstall()
        self.assertEqual(finders(), before)
        self.assertIsNone(modloader.installed())


class ExpectedFailuresTests(unittest.TestCase):
    def folder(self, **files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for mod, text in files.items():
            (Path(tmp.name) / mod).mkdir()
            (Path(tmp.name) / mod / "expected_failures.txt").write_text(text, encoding="utf-8")
        return Path(tmp.name)

    def test_the_file_lists_tests_with_a_reason_and_skips_comments_and_blank_lines(self):
        root = self.folder(wire="# dev tests this mod breaks on purpose\n\n"
                                "test_arena.py::ConnectionTests::test_caps  # zlib3, not zlib2\n"
                                "test_other.py::T::test_x\n", other="test_q.py::Q::test_q # why\n")
        self.assertEqual(modloader.expected_failures(root), [
            ("test_q.py::Q::test_q", "other", "why"),
            ("test_arena.py::ConnectionTests::test_caps", "wire", "zlib3, not zlib2"),
            ("test_other.py::T::test_x", "wire", "changed on purpose by the mod")])
        self.assertEqual(modloader.expected_failures(Path("/no/such/folder")), [])

    def fake_item(self, nodeid):
        markers = []
        return SimpleNamespace(nodeid=nodeid, markers=markers, iter_markers=lambda: iter(markers),
                               add_marker=markers.append)

    def test_the_listed_tests_are_marked_as_strict_expected_failures_whatever_the_folder_in_the_id(self):
        root = self.folder(wire="test_arena.py::ConnectionTests::test_caps  # zlib3\n")
        items = [self.fake_item("_internal/test_arena.py::ConnectionTests::test_caps"),
                 self.fake_item("mods/wire/test_arena.py::ConnectionTests::test_caps"),
                 self.fake_item("_internal/test_arena.py::ConnectionTests::test_caps_more"),
                 self.fake_item("_internal/test_arena.py::ConnectionTests::test_other")]
        with mock.patch.object(modloader, "MODS_DIR", root):
            self.assertEqual(modloader.mark_expected_failures(items), 2)
            self.assertEqual(modloader.mark_expected_failures(items), 0)         # both conftest files call it
        marked = [i for i in items if i.markers]
        self.assertEqual([i.nodeid for i in marked], [items[0].nodeid, items[1].nodeid])
        mark = marked[0].markers[0]
        self.assertEqual(mark.name, "xfail")
        self.assertTrue(mark.kwargs["strict"])
        self.assertEqual(mark.kwargs["reason"], "changed on purpose by the wire mod: zlib3")

    def test_a_parametrised_test_is_matched_too(self):
        root = self.folder(m="test_a.py::T::test_p\n")
        item = self.fake_item("_internal/test_a.py::T::test_p[case-1]")
        with mock.patch.object(modloader, "MODS_DIR", root):
            self.assertEqual(modloader.mark_expected_failures([item]), 1)

    def test_the_tests_run_with_a_throwaway_scan_data_folder(self):
        import scan_paths
        root = Path(scan_paths.root())
        self.assertNotEqual(root, modloader.PROJECT_DIR / "scan_data")
        self.assertIn("star_empire_test_scan_", str(root))
        self.assertTrue(str(scan_paths.path("anything.csv")).startswith(str(root)))


class LayoutTests(unittest.TestCase):
    """modloader.find_layout: the loader inside the game's own _internal/, or the stand-alone package."""

    def folder(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def test_inside_the_games_internal_folder_mods_sit_beside_it_and_scan_data_one_level_up(self):
        root = self.folder()
        internal = root / "client" / "_internal"
        internal.mkdir(parents=True)
        (internal / "Client.py").write_text("")
        self.assertEqual(modloader.find_layout(internal, {}), (internal, root / "client", root))

    def test_the_package_dropped_into_the_game_folder_finds_the_game_beside_it(self):
        game = self.folder()
        (game / "modloader").mkdir()
        self.assertEqual(modloader.find_layout(game / "modloader", {}), (game / "_internal", game, game))

    def test_a_pointer_file_names_the_game_folder_when_the_package_lives_elsewhere(self):
        root = self.folder()
        (root / "modloader").mkdir()
        game = self.folder() / "Star Empire"             # a path with a space; it need not exist, the loader only builds paths
        (root / "game_dir.txt").write_text(f'# where the game is\n\n  "{game}"  \n', encoding="utf-8")
        internal, mods_root, project = modloader.find_layout(root / "modloader", {})
        self.assertEqual((internal, mods_root, project), (game / "_internal", root, root))

    def test_the_environment_variable_beats_the_pointer_file_and_even_a_loader_inside_internal(self):
        root = self.folder()
        (root / "modloader").mkdir()
        from_pointer, from_env = self.folder() / "FromPointer", self.folder() / "FromEnv"
        (root / "game_dir.txt").write_text(str(from_pointer), encoding="utf-8")
        env = {modloader.ENV_GAME: str(from_env)}
        self.assertEqual(modloader.find_layout(root / "modloader", env)[0], from_env / "_internal")
        inside = root / "_internal"
        inside.mkdir()
        (inside / "Client.py").write_text("")
        self.assertEqual(modloader.find_layout(inside, env)[0], from_env / "_internal")

    def test_a_missing_game_is_explained_with_the_ways_to_fix_it(self):
        empty = self.folder()
        problem = modloader.game_problem(empty / "_internal")
        self.assertIn("no Client.py", problem)
        self.assertIn("game_dir.txt", problem)
        self.assertIn(modloader.ENV_GAME, problem)
        self.assertIn("official launcher", problem)                     # the way for someone who has no game yet
        (empty / "_internal").mkdir()
        (empty / "_internal" / "Client.py").write_text("")
        self.assertEqual(modloader.game_problem(empty / "_internal"), "")


class AttachMethodsTests(unittest.TestCase):
    def test_methods_and_class_attributes_are_attached_to_the_game_class(self):
        class Game:
            def own(self):
                return "dev"

        class Mod:
            LIMIT = 5

            def extra(self):
                return self.own() + "+mod"

            @staticmethod
            def helper(x):
                return x * 2

            @property
            def size(self):
                return self.LIMIT

        names = modloader.attach_methods(Game, Mod)
        self.assertEqual(sorted(names), ["LIMIT", "extra", "helper", "size"])
        game = Game()
        self.assertEqual((game.extra(), game.helper(3), game.size), ("dev+mod", 6, 5))
        self.assertEqual(Game.extra.__qualname__, "Game.extra")

    def test_a_name_the_game_class_already_has_is_kept_and_logged(self):
        class Game:
            def clash(self):
                return "dev"

        class Mod:
            def clash(self):
                return "mod"

        with self.assertLogs("modloader", level="WARNING") as logs:
            self.assertEqual(modloader.attach_methods(Game, Mod), [])
        self.assertIn("'clash'", logs.output[0])
        self.assertEqual(Game().clash(), "dev")

    def test_a_name_defined_only_on_a_base_class_is_overridden_like_a_method_in_the_class_body(self):
        class Base:
            def shared(self):
                return "base"

        class Game(Base):
            pass

        class Mod:
            def shared(self):
                return "mod"

        modloader.attach_methods(Game, Mod)
        self.assertEqual(Game().shared(), "mod")

    def test_dunder_names_are_not_copied(self):
        class Game:
            pass

        class Mod:
            """doc"""
            def method(self):
                pass

        modloader.attach_methods(Game, Mod)
        self.assertIsNone(Game.__doc__)


class InstallMethodsTests(unittest.TestCase):
    def test_borrowed_names_are_filled_in_from_the_game_module_before_attaching(self):
        import types
        game_module = types.ModuleType("fake_game_mod")
        game_module.SECRET = 42
        sys.modules["fake_game_mod"] = game_module
        self.addCleanup(sys.modules.pop, "fake_game_mod", None)
        Game = type("Game", (), {"__module__": "fake_game_mod"})

        class Mod:
            def f(self):
                return SECRET            # noqa: F821  (borrowed: lives in this test module's globals)

        namespace = globals()
        self.addCleanup(namespace.pop, "SECRET", None)
        names = modloader.install_methods(Game, Mod, namespace, ("SECRET",))
        self.assertEqual(names, ["f"])
        self.assertEqual(Game().f(), 42)

    def test_a_renamed_game_name_is_logged_and_nothing_is_attached_instead_of_raising(self):
        import types
        game_module = types.ModuleType("fake_game_mod2")
        sys.modules["fake_game_mod2"] = game_module
        self.addCleanup(sys.modules.pop, "fake_game_mod2", None)
        Game = type("Game", (), {"__module__": "fake_game_mod2"})

        class Mod:
            def f(self):
                return 1

        self.addCleanup(modloader.ATTACH_FAILURES.clear)
        with self.assertLogs("modloader", level="ERROR") as logs:
            self.assertEqual(modloader.install_methods(Game, Mod, {}, ("GONE",)), [])
        self.assertIn("no longer defines GONE", logs.output[0])
        self.assertFalse(hasattr(Game, "f"))
        # remembered for the Mods page, under the mod's name (a module outside mods/ is filed under its own name)
        (reason,) = modloader.ATTACH_FAILURES.values()
        self.assertIn("no longer defines GONE", reason)
        self.assertIn("Mod could not be attached to Game", reason)


class WrapMethodTests(unittest.TestCase):
    """modloader.wrap_method / install_methods(after=, result=): a mod's function runs after a named game method,
    with no patch and no anchor in the game's text."""

    def setUp(self):
        self.addCleanup(modloader.ATTACH_FAILURES.clear)
        calls = self.calls = []

        class Game:
            def build(self, a, b=2):
                calls.append(("build", a, b))
                return {"a": a}

            def init(self):
                calls.append("init")

        self.Game = Game

    def test_after_runs_once_the_games_method_has_returned_and_gets_its_arguments(self):
        calls = self.calls
        modloader.wrap_method(self.Game, "build", after=lambda self, a, b=2: calls.append(("hook", a, b)))
        self.assertEqual(self.Game().build(7, b=9), {"a": 7})              # the game's own result is untouched
        self.assertEqual(calls, [("build", 7, 9), ("hook", 7, 9)])

    def test_result_hook_may_change_the_value_in_place_or_replace_it(self):
        modloader.wrap_method(self.Game, "build", result=lambda self, value, a, b=2: value.update(extra=b))
        self.assertEqual(self.Game().build(1), {"a": 1, "extra": 2})        # returned None: the dict is kept
        modloader.wrap_method(self.Game, "build", result=lambda self, value, a, b=2: ["replaced", a])
        self.assertEqual(self.Game().build(5), ["replaced", 5])

    def test_before_runs_ahead_of_the_games_method_with_its_arguments_and_the_game_still_runs_after_it(self):
        calls = self.calls
        modloader.wrap_method(self.Game, "build", before=lambda self, a, b=2: calls.append(("before", a, b)))
        modloader.wrap_method(self.Game, "build", after=lambda self, a, b=2: calls.append("after"))
        self.assertEqual(self.Game().build(4, b=6), {"a": 4})
        self.assertEqual(calls, [("before", 4, 6), ("build", 4, 6), "after"])

    def test_before_has_run_even_when_the_game_then_raises_and_a_failing_before_does_not_stop_the_game(self):
        calls = self.calls

        class Boom:
            def go(self):
                calls.append("go")
                raise KeyError("games own")

        modloader.wrap_method(Boom, "go", before=lambda self: calls.append("captured"))
        with self.assertRaises(KeyError):
            Boom().go()
        self.assertEqual(calls, ["captured", "go"])

        def bad(self, a, b=2):
            raise RuntimeError("boom")

        modloader.wrap_method(self.Game, "build", before=bad)
        with self.assertLogs("modloader", level="ERROR"):
            self.assertEqual(self.Game().build(1), {"a": 1})
        self.assertEqual(calls[-1], ("build", 1, 2))

    def test_the_wrapper_looks_like_the_games_method(self):
        original = self.Game.build
        modloader.wrap_method(self.Game, "build", after=lambda self, a, b=2: None)
        self.assertEqual(self.Game.build.__name__, "build")
        self.assertIs(self.Game.build.__wrapped__, original)

    def test_several_mods_hook_the_same_method_in_install_order_and_hooking_twice_does_nothing(self):
        calls = self.calls
        first = lambda self: calls.append("first")
        second = lambda self: calls.append("second")
        for hook in (first, second, first):
            modloader.wrap_method(self.Game, "init", after=hook)
        self.Game().init()
        self.assertEqual(calls, ["init", "first", "second"])
        self.assertEqual([kind for kind, _fn in modloader.hooks_on(self.Game, "init")], ["after", "after"])

    def test_a_method_the_game_inherits_is_wrapped_on_the_subclass_only(self):
        class Child(self.Game):
            pass

        calls = self.calls
        modloader.wrap_method(Child, "init", after=lambda self: calls.append("hooked"))
        self.assertIn("init", vars(Child))
        self.assertEqual(modloader.hooks_on(self.Game, "init"), [])
        self.Game().init()
        self.assertEqual(calls, ["init"])
        Child().init()
        self.assertEqual(calls, ["init", "init", "hooked"])

    def test_a_hook_that_raises_does_not_stop_the_game_or_the_hooks_after_it(self):
        calls = self.calls

        def bad(self, a, b=2):
            raise RuntimeError("boom")

        modloader.wrap_method(self.Game, "build", after=bad)
        modloader.wrap_method(self.Game, "build", after=lambda self, a, b=2: calls.append("later hook"))
        with self.assertLogs("modloader", level="ERROR") as logs:
            self.assertEqual(self.Game().build(3), {"a": 3})
        self.assertIn("bad raised", logs.output[0])
        self.assertEqual(calls[-1], "later hook")
        (reason,) = modloader.ATTACH_FAILURES.values()                       # shown on the Mods page
        self.assertIn("bad", reason)
        self.assertIn("Game.build", reason)

    def test_a_game_exception_is_the_games_own(self):
        class Boom:
            def go(self):
                raise KeyError("games own")

        hook = mock.Mock()
        modloader.wrap_method(Boom, "go", after=hook)
        with self.assertRaises(KeyError):
            Boom().go()
        hook.assert_not_called()

    def test_a_method_that_is_gone_or_not_a_plain_method_is_refused(self):
        class Odd:
            @staticmethod
            def s():
                pass

            value = 3

        with self.assertRaisesRegex(AttributeError, "no longer has a method missing"):
            modloader.wrap_method(Odd, "missing", after=lambda self: None)
        for name in ("s", "value"):
            with self.assertRaisesRegex(TypeError, "not a plain method"):
                modloader.wrap_method(Odd, name, after=lambda self: None)
        with self.assertRaises(ValueError):
            modloader.wrap_method(self.Game, "init")
        with self.assertRaises(ValueError):
            modloader.wrap_method(self.Game, "init", after=lambda self: None, result=lambda self, v: None)
        with self.assertRaises(ValueError):
            modloader.wrap_method(self.Game, "init", before=lambda self: None, after=lambda self: None)

    def test_install_methods_attaches_and_hooks_together(self):
        class Mod:
            def extra(self):
                return "extra"

        def setup(self):
            self.state = "ready"

        self.assertEqual(modloader.install_methods(self.Game, Mod, {}, after={"init": setup}), ["extra"])
        game = self.Game()
        game.init()
        self.assertEqual((game.extra(), game.state), ("extra", "ready"))

    def test_install_methods_with_only_hooks_needs_no_source_class(self):
        def setup(self):
            self.state = "ready"

        self.assertEqual(modloader.install_methods(self.Game, after={"init": setup}), [])
        game = self.Game()
        game.init()
        self.assertEqual(game.state, "ready")

    def test_a_hook_on_a_method_the_game_dropped_leaves_the_game_untouched_and_is_reported(self):
        class Mod:
            def extra(self):
                return 1

        def setup(self):
            pass

        before = dict(vars(self.Game))
        with self.assertLogs("modloader", level="ERROR") as logs:
            self.assertEqual(modloader.install_methods(self.Game, Mod, {}, after={"init": setup, "renamed": setup}), [])
        self.assertIn("no longer has a method renamed", logs.output[0])
        self.assertEqual(vars(self.Game).keys(), before.keys())              # nothing attached, nothing wrapped
        self.assertIs(vars(self.Game)["init"], before["init"])
        (reason,) = modloader.ATTACH_FAILURES.values()
        self.assertIn("no longer has a method renamed", reason)

    def test_attachment_problems_checks_that_each_declared_hook_is_in_place(self):
        import types
        game_module = types.ModuleType("fake_game_hooks")
        game_module.Game = self.Game
        self.Game.__module__ = "fake_game_hooks"
        sys.modules["fake_game_hooks"] = game_module
        self.addCleanup(sys.modules.pop, "fake_game_hooks", None)
        mod = types.ModuleType("fake_hook_mod")
        exec("def setup(self):\n    self.state = 1\n"
             "HOOKS = ((\"fake_game_hooks\", \"Game\", \"init\", \"setup\"),)\n", mod.__dict__)
        problems = modloader.attachment_problems(mod)
        self.assertEqual(len(problems), 1)
        self.assertIn("Game.init does not run fake_hook_mod.setup", problems[0])
        modloader.wrap_method(self.Game, "init", after=mod.setup)
        self.assertEqual(modloader.attachment_problems(mod), [])
        mod.HOOKS = (("fake_game_hooks", "Game", "gone", "setup"),)
        self.assertEqual(len(modloader.attachment_problems(mod)), 1)


class StandaloneTests(unittest.TestCase):
    """modloader.standalone_problems / hard_dependencies: can a mod be picked on its own?"""

    GAME = {"Game.py": b"import os\n\n\ndef run():\n    one = 1\n    return one\n"}

    def mods(self, **folders):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, files in folders.items():
            (root / name).mkdir()
            for fname, text in files.items():
                (root / name / fname).write_text(text, encoding="utf-8")
        return load_mods(root)

    def problems(self, mods, name):
        mod = next(m for m in mods if m.name == name)
        return modloader.standalone_problems(mod, mods, lambda f: self.GAME[f])

    def test_a_mod_with_a_clean_patch_works_alone(self):
        mods = self.mods(a={"Game.py.patch": "@@ a @@\n     one = 1\n+    two = 2\n"})
        self.assertEqual(self.problems(mods, "a"), [])

    def test_a_patch_that_uses_a_name_only_another_mods_patch_provides_is_caught(self):
        provider = {"Game.py.patch": "@@ p @@\n import os\n+SHARED = 5\n"}
        user = {"Game.py.patch": "@@ u @@\n     one = 1\n+    two = SHARED\n"}
        mods = self.mods(provider=provider, user=user)
        (problem,) = self.problems(mods, "user")
        self.assertIn("run uses SHARED", problem)
        mods = self.mods(provider=provider, user=dict(user, **{"mod.json": '{"requires": ["provider"]}'}))
        self.assertEqual(self.problems(mods, "user"), [], "declared, so the provider comes along")

    def test_a_missing_required_mod_and_a_stale_anchor_are_reported(self):
        mods = self.mods(a={"mod.json": '{"requires": ["gone"]}'},
                         b={"Game.py.patch": "@@ b @@\n     nothing here\n+    x = 1\n"})
        self.assertEqual(self.problems(mods, "a"), ["requires gone, which is not there"])
        (problem,) = self.problems(mods, "b")
        self.assertIn("anchor not found", problem)

    def test_a_patch_that_breaks_the_files_syntax_is_caught(self):
        mods = self.mods(a={"Game.py.patch": "@@ a @@\n     one = 1\n+    two = (\n"})
        (problem,) = self.problems(mods, "a")
        self.assertIn("does not compile", problem)

    def test_hard_dependencies_are_module_level_imports_and_imports_a_patch_adds_not_lazy_ones(self):
        mods = self.mods(
            lib={"shared.py": "VALUE = 1\n", "other.py": "OTHER = 1\n"},
            hard={"hard.py": "import shared\n", "Game.py.patch": "@@ h @@\n import os\n+from other import OTHER\n"},
            lazy={"lazy.py": "def f():\n    import shared\n    return shared.VALUE\n"},
            own={"own.py": "import own_helper\n", "own_helper.py": "X = 1\n"})
        by_name = {m.name: m for m in mods}
        self.assertEqual(sorted(modloader.hard_dependencies(by_name["hard"], mods)), ["lib"])
        self.assertEqual(modloader.hard_dependencies(by_name["lazy"], mods), {})
        self.assertEqual(modloader.hard_dependencies(by_name["own"], mods), {})

    def test_requirement_closure_follows_requires_all_the_way(self):
        mods = self.mods(a={"mod.json": '{"requires": ["b"]}'}, b={"mod.json": '{"requires": ["c"]}'}, c={})
        self.assertEqual(modloader.requirement_closure(mods, ["a"]), {"a", "b", "c"})

    def test_module_owners_lists_every_module_and_who_has_it(self):
        mods = self.mods(a={"x.py": "", "test_x.py": ""}, b={"x.py": ""})
        self.assertEqual(modloader.module_owners(mods), {"x": ["a", "b"]})


class MissingGlobalsTests(unittest.TestCase):
    def test_a_module_that_forgot_an_import_is_caught(self):
        import types
        module = types.ModuleType("fake_mod")
        exec("import math\n\ndef good(x):\n    return math.sqrt(x) + len([x])\n\n"
             "def bad(x):\n    return math.sqrt(x) + undefined_thing\n\n"
             "class K:\n    def m(self):\n        return also_missing + math.pi\n\n"
             "def outer():\n    def inner():\n        return nested_missing\n    return inner\n", module.__dict__)
        self.assertEqual(modloader.missing_globals(module), {"undefined_thing", "also_missing", "nested_missing"})
        self.assertEqual(modloader.missing_globals(module.good), set())
        self.assertEqual(modloader.missing_globals(module.K), {"also_missing"})

    def test_names_of_other_modules_are_not_counted_for_the_module_under_test(self):
        import types
        module = types.ModuleType("fake_mod2")
        exec("from json import dumps\n\ndef f():\n    return dumps({})\n", module.__dict__)
        self.assertEqual(modloader.missing_globals(module), set())


class CommandLineTests(unittest.TestCase):
    def test_main_reports_and_exits_nonzero_only_when_a_mod_had_to_go(self):
        with mock.patch.object(modloader, "load_mods", return_value=[]), \
                mock.patch("builtins.print") as printed:
            self.assertEqual(modloader.main(), 0)
        self.assertIn("mods in effect (0)", printed.call_args[0][0])
        bad = mod_of("bad", "@@ @@\n-no such line\n+x\n", file="Client.py")
        with mock.patch.object(modloader, "load_mods", return_value=[bad]), \
                mock.patch("builtins.print"):
            self.assertEqual(modloader.main(), 1)


if __name__ == "__main__":
    unittest.main()

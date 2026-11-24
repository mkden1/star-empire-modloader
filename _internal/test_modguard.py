"""Tests of modguard.py, the mod loader's safety net: the wrapper check, the crash catcher, safe mode, the two-part run of the
game's file, and what the loader does with all of it."""
import importlib.util
import logging
import os
import sys
import tempfile
import textwrap
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

import modguard
import modloader


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8", newline="\n")
    return path


class Workspace(unittest.TestCase):
    """A scratch game folder (``game/_internal/Client.py``) and mods folder, and cleanup of what a test puts in sys.modules."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.mods_dir = self.root / "mods"
        self.mods_dir.mkdir()
        self.internal = self.root / "game" / "_internal"
        self.internal.mkdir(parents=True)
        write(self.internal / "Client.py", "x = 1\n")
        write(self.internal.parent / "version.txt", '"0.5.1"')
        self._modules = []
        self.addCleanup(self._drop_modules)
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(modguard.ENV_RESTARTS, None)

    def _drop_modules(self):
        for name in self._modules:
            sys.modules.pop(name, None)

    def mod(self, name: str, files: "dict[str, str]"):
        for file, text in files.items():
            write(self.mods_dir / name / file, text)

    def mods(self):
        return modloader.load_mods(self.mods_dir)

    def load_from_file(self, name: str, path: Path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        self._modules.append(name)
        spec.loader.exec_module(module)
        return module

    def fake_module(self, name: str, **attrs):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        sys.modules[name] = module
        self._modules.append(name)
        return module


GAME_VIEW = '''
def view(rows, query, collapsed, open_groups=frozenset()):
    return ("game", rows, query, collapsed, open_groups)
'''

THREE_ARGUMENT_WRAPPER = '''
def wrap(original):
    def view(rows, query, collapsed):
        return original(rows, query, collapsed)
    view.__wrapped__ = original
    return view
'''


class SignatureProblemTests(unittest.TestCase):
    def test_a_wrapper_with_fewer_positional_arguments_than_the_game_function_is_a_problem(self):
        def original(rows, query, collapsed, open_groups=frozenset()):
            pass

        def wrapper(rows, query, collapsed):
            pass

        self.assertIn("4 positional", modguard.signature_problem(wrapper, original))

    def test_the_blind_spot_of_inspect_signature_is_not_a_blind_spot_here(self):
        """A wrapper that sets __wrapped__ (as every wrapper of ours does) reports the ORIGINAL's signature through inspect.signature."""
        import inspect

        def original(a, b, c=None):
            pass

        def wrapper(a, b):
            pass
        wrapper.__wrapped__ = original
        self.assertEqual(str(inspect.signature(wrapper)), "(a, b, c=None)")        # the trap
        self.assertTrue(modguard.signature_problem(wrapper, original))

    def test_passing_everything_on_is_fine(self):
        def original(a, b, c=None, *, d=1, **rest):
            pass

        def forwards(*args, **kwargs):
            pass

        def names_the_known_part(a, b, *extra, **named):
            pass

        self.assertEqual(modguard.signature_problem(forwards, original), "")
        self.assertEqual(modguard.signature_problem(names_the_known_part, original), "")

    def test_a_keyword_only_argument_the_wrapper_does_not_take(self):
        def original(self, a, *, fade=None):
            pass

        def wrapper(self, a):
            pass

        self.assertIn("'fade'", modguard.signature_problem(wrapper, original))

        def takes_it(self, a, *, fade=None):
            pass

        self.assertEqual(modguard.signature_problem(takes_it, original), "")

    def test_an_extra_required_argument_is_a_problem_an_optional_one_is_not(self):
        def original(a):
            pass

        def needs_more(a, b):
            pass

        def optional(a, b=None):
            pass

        self.assertIn("'b'", modguard.signature_problem(needs_more, original))
        self.assertEqual(modguard.signature_problem(optional, original), "")


class FingerprintTests(Workspace):
    def test_the_mod_fingerprint_follows_its_code_not_its_tests_or_data(self):
        self.mod("m", {"m.py": "A = 1\n", "mod.json": "{}", "test_m.py": "T = 1\n", "settings.json": "{}"})
        folder = self.mods_dir / "m"
        before = modguard.mod_fingerprint(folder)
        write(folder / "test_m.py", "T = 2\n")
        write(folder / "settings.json", '{"saved": true}')
        (folder / "__pycache__").mkdir()
        write(folder / "__pycache__" / "x.py", "junk = 1\n")
        self.assertEqual(modguard.mod_fingerprint(folder), before)
        write(folder / "m.py", "A = 2\n")
        self.assertNotEqual(modguard.mod_fingerprint(folder), before)

    def test_the_game_fingerprint_is_its_version_and_the_size_of_client_py(self):
        before = modguard.game_fingerprint(self.internal)
        self.assertEqual(before, '"0.5.1"|6')                                   # version.txt holds the quotes, as the game's does
        os.utime(self.internal / "Client.py", (1, 1))                           # a time stamp the launcher touched
        self.assertEqual(modguard.game_fingerprint(self.internal), before)
        write(self.internal.parent / "version.txt", '"0.5.2"')
        self.assertNotEqual(modguard.game_fingerprint(self.internal), before)


class SessionTests(Workspace):
    def switch_off(self, name="m", reason="it broke"):
        session = modguard.start_session(self.mods(), self.root, self.internal)
        modguard.switch_off(session, next(m for m in self.mods() if m.name == name), reason, "test")

    def test_a_switched_off_mod_stays_off_and_is_announced_once(self):
        self.mod("m", {"m.py": "A = 1\n"})
        self.switch_off()
        first = modguard.start_session(self.mods(), self.root, self.internal)
        self.assertEqual(first.auto_off, {"m": "it broke"})
        self.assertEqual(first.notes, ["m: it broke"])
        second = modguard.start_session(self.mods(), self.root, self.internal)
        self.assertEqual(second.auto_off, {"m": "it broke"})
        self.assertEqual(second.notes, [])

    def test_the_mod_gets_another_try_when_it_changes(self):
        self.mod("m", {"m.py": "A = 1\n"})
        self.switch_off()
        write(self.mods_dir / "m" / "m.py", "A = 2\n")
        self.assertEqual(modguard.start_session(self.mods(), self.root, self.internal).auto_off, {})

    def test_the_mod_gets_another_try_when_the_game_changes(self):
        self.mod("m", {"m.py": "A = 1\n"})
        self.switch_off()
        write(self.internal.parent / "version.txt", '"0.5.2"')
        self.assertEqual(modguard.start_session(self.mods(), self.root, self.internal).auto_off, {})

    def test_a_mod_that_is_gone_is_forgotten(self):
        self.mod("m", {"m.py": "A = 1\n"})
        self.mod("n", {"n.py": "B = 1\n"})
        self.switch_off("m")
        import shutil
        shutil.rmtree(self.mods_dir / "m")
        self.assertEqual(modguard.start_session(self.mods(), self.root, self.internal).auto_off, {})
        self.assertEqual(modguard._read_json(self.root / modguard.AUTO_OFF_FILE)["mods"], {})

    def test_deleting_the_file_brings_every_mod_back(self):
        self.mod("m", {"m.py": "A = 1\n"})
        self.switch_off()
        (self.root / modguard.AUTO_OFF_FILE).unlink()
        self.assertEqual(modguard.start_session(self.mods(), self.root, self.internal).auto_off, {})

    def test_a_damaged_file_is_ignored(self):
        self.mod("m", {"m.py": "A = 1\n"})
        (self.root / modguard.AUTO_OFF_FILE).write_text("{not json", encoding="utf-8")
        (self.root / modguard.CRASH_FILE).write_text("[1, 2]", encoding="utf-8")
        session = modguard.start_session(self.mods(), self.root, self.internal)
        self.assertEqual((session.auto_off, session.safe_mode), ({}, False))

    def test_two_unexplained_crashes_in_a_row_mean_safe_mode_once(self):
        self.mod("m", {"m.py": "A = 1\n"})
        modguard._write_json(self.root / modguard.CRASH_FILE, {"streak": 1})
        self.assertFalse(modguard.start_session(self.mods(), self.root, self.internal).safe_mode)
        modguard._write_json(self.root / modguard.CRASH_FILE, {"streak": 2})
        safe = modguard.start_session(self.mods(), self.root, self.internal)
        self.assertTrue(safe.safe_mode)
        self.assertIn("every mod is off", safe.notes[0])
        self.assertFalse(modguard.start_session(self.mods(), self.root, self.internal).safe_mode)


class AttributionTests(Workspace):
    def attributor(self, spans=None):
        return modguard.Attributor(self.mods(), self.internal, spans)

    def raised(self, fn, *args):
        try:
            fn(*args)
        except BaseException as exc:                                           # noqa: BLE001
            return exc
        self.fail("nothing was raised")

    def test_an_error_raised_in_a_mods_code_is_the_mods(self):
        self.mod("m", {"m.py": "def go():\n    raise KeyError('x')\n"})
        module = self.load_from_file("mg_m", self.mods_dir / "m" / "m.py")
        blame = self.attributor().blame(self.raised(module.go))
        self.assertEqual(blame.mod, "m")
        self.assertIn("go()", blame.how)

    def test_a_line_a_patch_added_to_a_game_file_is_the_mods(self):
        self.mod("m", {"m.py": "A = 1\n"})
        game_file = self.internal / "Client.py"
        code = compile("\n\n\ndef hook():\n    raise ValueError('patched in')\n\ndef game():\n    raise ValueError('dev')\n",
                       str(game_file), "exec")
        scope = {}
        exec(code, scope)
        attributor = self.attributor({"Client.py": [(4, 5, "m")]})
        self.assertEqual(attributor.blame(self.raised(scope["hook"])).mod, "m")
        self.assertIsNone(attributor.blame(self.raised(scope["game"])))

    def test_an_error_in_the_games_own_code_is_nobodys(self):
        self.mod("m", {"m.py": "A = 1\n"})
        self.assertIsNone(self.attributor().blame(self.raised(lambda: 1 / 0)))

    def test_a_mod_that_called_the_function_that_raised_is_blamed_too(self):
        self.mod("m", {"m.py": "def call(fn):\n    return fn()\n"})
        module = self.load_from_file("mg_m2", self.mods_dir / "m" / "m.py")
        blame = self.attributor().blame(self.raised(module.call, lambda: 1 / 0))
        self.assertEqual(blame.mod, "m")
        self.assertIn("called the function that raised", blame.how)

    def test_a_mods_wrapper_that_cannot_take_the_call_is_found_by_the_name_in_the_message(self):
        """The game's call fails in the GAME's frame (no frame of the wrapper exists): only the message names it."""
        self.mod("m", {"m.py": THREE_ARGUMENT_WRAPPER})
        wrapper_module = self.load_from_file("mg_wrap", self.mods_dir / "m" / "m.py")
        wrapped = wrapper_module.wrap(lambda *a: None)
        self.fake_module("mg_game_installs_it", view=wrapped)

        def game_calls_it():
            return wrapped([], "", set(), frozenset())

        exc = self.raised(game_calls_it)
        self.assertIsInstance(exc, TypeError)
        blame = self.attributor().blame(exc)
        self.assertEqual(blame.mod, "m")
        self.assertIn("arguments it does not take", blame.how)

    def test_a_binding_error_that_names_a_game_function_is_nobodys(self):
        self.mod("m", {"m.py": "A = 1\n"})

        def games_own(a):
            pass
        self.fake_module("mg_dev_module", games_own=games_own)
        self.assertIsNone(self.attributor().blame(self.raised(games_own, 1, 2)))


class CheckWrappersTests(Workspace):
    def setUp(self):
        super().setUp()
        self.mod("m", {"m.py": THREE_ARGUMENT_WRAPPER + "\ndef fine(original):\n    def view(*args, **kwargs):\n        return original(*args, **kwargs)\n    view.__wrapped__ = original\n    return view\n"})
        self.module = self.load_from_file("mg_wrappers", self.mods_dir / "m" / "m.py")
        ns = {}
        exec(GAME_VIEW, ns)
        self.original = ns["view"]

    def check(self, namespaces=()):
        return modguard.check_wrappers(modguard.Attributor(self.mods(), self.internal), namespaces)

    def test_the_wrapper_that_cannot_take_what_the_game_passes_is_found(self):
        game = self.fake_module("mg_dev_view", view=self.module.wrap(self.original))
        problems = self.check()
        self.assertEqual([(p.mod, p.where) for p in problems], [("m", "mg_dev_view.view")])
        self.assertIn("4 positional", problems[0].why)
        self.assertIn("view", problems[0].reason())
        self.assertTrue(modguard.restore(problems[0]))
        self.assertIs(game.view, self.original)

    def test_a_wrapper_that_passes_everything_on_is_fine(self):
        self.fake_module("mg_dev_view_ok", view=self.module.fine(self.original))
        self.assertEqual(self.check(), [])

    def test_a_wrapper_in_a_namespace_that_is_not_in_sys_modules_is_found_and_restored(self):
        namespace = {"__name__": "__main__", "view": self.module.wrap(self.original)}
        problems = self.check([namespace])
        self.assertEqual([p.where for p in problems], ["__main__.view"])
        modguard.restore(problems[0])
        self.assertIs(namespace["view"], self.original)

    def test_a_method_wrapped_on_a_class_is_found_and_a_static_method_is_put_back_as_one(self):
        class Window:
            @staticmethod
            def draw(a, b, c, fade=None):
                pass

            def method(self, a, b, *, fade=None):
                pass

        original_draw, original_method = Window.__dict__["draw"].__func__, Window.__dict__["method"]
        Window.method = self.module.wrap(original_method)
        Window.draw = staticmethod(self.module.wrap(original_draw))
        Window.__module__ = "mg_dev_class"
        self.fake_module("mg_dev_class", Window=Window)
        problems = {p.name: p for p in self.check()}
        self.assertEqual(sorted(problems), ["draw", "method"])
        for p in problems.values():
            modguard.restore(p)
        self.assertIsInstance(Window.__dict__["draw"], staticmethod)
        self.assertIs(Window.__dict__["draw"].__func__, original_draw)
        self.assertIs(Window.__dict__["method"], original_method)
        self.assertEqual(self.check(), [])

    def test_a_wrapper_that_is_not_a_mods_is_left_alone(self):
        def other(a):
            pass

        def not_ours(a, b):
            pass
        not_ours.__wrapped__ = other                                           # code in THIS file, not in a mod folder
        self.fake_module("mg_not_a_mod", f=not_ours)
        self.assertEqual(self.check(), [])


class GuardTests(Workspace):
    def setUp(self):
        super().setUp()
        self.mod("m", {"m.py": THREE_ARGUMENT_WRAPPER + "\ndef boom():\n    raise RuntimeError('mod bug')\n"})
        self.module = self.load_from_file("mg_guard_mod", self.mods_dir / "m" / "m.py")
        ns = {}
        exec(GAME_VIEW, ns)
        self.original = ns["view"]
        self.launched, self.exited = [], []
        self.boxes = []
        for patcher in (mock.patch.object(modguard, "message_box", self.boxes.append),
                        mock.patch.object(modguard.atexit, "register")):            # the guards are throw-away: no exit hooks left behind
            patcher.start()
            self.addCleanup(patcher.stop)

    def guard(self, spans=None):
        session = modguard.start_session(self.mods(), self.root, self.internal)
        guard = modguard.Guard(session, self.mods(), self.internal, spans)
        guard.launch = ["python", str(write(self.root / "start.py", "# the launch script\n"))]
        guard.popen = lambda cmd, **kw: self.launched.append((cmd, kw))
        guard.exit_process = self.exited.append
        self.addCleanup(guard.disarm)
        return guard

    def test_a_wrapper_that_does_not_fit_switches_its_mod_off_and_restarts_the_game(self):
        self.fake_module("mg_dev_g", view=self.module.wrap(self.original))
        guard = self.guard()
        guard.after_import()
        self.assertEqual(len(self.launched), 1)
        command, kwargs = self.launched[0]
        self.assertEqual(command, ["python", str(self.root / "start.py")])
        self.assertEqual(kwargs["env"][modguard.ENV_NO_UPDATE], "1")
        self.assertEqual(kwargs["env"][modguard.ENV_RESTARTS], "1")
        self.assertEqual(self.exited, [0])
        later = modguard.start_session(self.mods(), self.root, self.internal)
        self.assertIn("m", later.auto_off)
        self.assertIn("cannot take every call", later.auto_off["m"])

    def test_when_the_restarts_are_used_up_the_games_own_function_is_put_back(self):
        game = self.fake_module("mg_dev_g2", view=self.module.wrap(self.original))
        os.environ[modguard.ENV_RESTARTS] = str(modguard.MAX_RESTARTS)
        self.guard().after_import()
        self.assertEqual((self.launched, self.exited), ([], []))
        self.assertIs(game.view, self.original)
        self.assertIn("m", self.boxes[0])

    def test_a_game_that_cannot_restart_still_gets_its_functions_back(self):
        game = self.fake_module("mg_dev_g3", view=self.module.wrap(self.original))
        guard = self.guard()
        guard.popen = mock.Mock(side_effect=OSError("no"))
        guard.after_import()
        self.assertEqual(self.exited, [])
        self.assertIs(game.view, self.original)

    def test_a_clean_game_is_left_alone(self):
        self.fake_module("mg_dev_g4", view=self.original)
        guard = self.guard()
        guard.after_import()
        self.assertEqual((self.launched, self.exited, self.boxes), ([], [], []))

    def test_arming_forgets_the_restart_count_so_a_later_relaunch_counts_from_zero(self):
        os.environ[modguard.ENV_RESTARTS] = "2"
        self.guard().after_import()
        self.assertNotIn(modguard.ENV_RESTARTS, os.environ)

    def crash_in_thread(self, exc_source, thread_name="SolarSystemWindow"):
        """What the game's crash_log does when a thread dies: one CRITICAL record with the exception, on its logger."""
        def run():
            try:
                exc_source()
            except BaseException:                                              # noqa: BLE001
                logging.getLogger(modguard.CRASH_LOGGER).critical("Uncaught exception in thread %r", thread_name,
                                                                  exc_info=sys.exc_info())
        thread = threading.Thread(target=run, name=thread_name)
        thread.start()
        thread.join()

    def test_a_crash_in_a_mods_code_switches_that_mod_off_for_the_next_start(self):
        guard = self.guard()
        guard.after_import()
        self.crash_in_thread(self.module.boom)
        later = modguard.start_session(self.mods(), self.root, self.internal)
        self.assertIn("RuntimeError: mod bug", later.auto_off["m"])
        state = modguard._read_json(self.root / modguard.CRASH_FILE)
        self.assertEqual(state["last"]["culprit"], "m")
        self.assertEqual(state["streak"], 0)

    def test_a_crash_is_recorded_once_per_run(self):
        guard = self.guard()
        guard.after_import()
        self.crash_in_thread(self.module.boom)
        self.crash_in_thread(lambda: 1 / 0, "MainThread")
        self.assertEqual(len(modguard._read_json(self.root / modguard.CRASH_FILE)["history"]), 1)

    def test_a_worker_thread_that_dies_is_not_the_end_of_the_game(self):
        guard = self.guard()
        guard.after_import()
        self.crash_in_thread(self.module.boom, "scan-writer")
        self.assertFalse((self.root / modguard.CRASH_FILE).exists())
        self.assertEqual(modguard.start_session(self.mods(), self.root, self.internal).auto_off, {})

    def test_the_game_crash_loggers_own_record_is_what_the_catcher_reads(self):
        """The game's real crash_log.py, when it can be imported: its thread hook logs on the logger and with the exc_info this relies on."""
        try:
            import crash_log
        except ImportError:
            self.skipTest("the game's crash_log.py is not in this folder")
        self.assertEqual(crash_log.__name__, modguard.CRASH_LOGGER)
        source = Path(crash_log.__file__).read_text(encoding="utf-8")
        self.assertIn('log.critical("Uncaught exception in thread %r"', source)
        self.assertIn("exc_info=(args.exc_type, args.exc_value", source)

    def test_two_crashes_nobody_can_be_blamed_for_lead_to_safe_mode(self):
        for _ in range(2):
            guard = self.guard()
            guard.after_import()
            self.crash_in_thread(lambda: 1 / 0)
            guard.disarm()
        self.assertEqual(modguard._read_json(self.root / modguard.CRASH_FILE)["streak"], 2)
        self.assertTrue(modguard.start_session(self.mods(), self.root, self.internal).safe_mode)

    def test_a_clean_end_forgets_the_crashes_in_a_row(self):
        guard = self.guard()
        guard.after_import()
        self.crash_in_thread(lambda: 1 / 0)
        self.assertEqual(modguard._read_json(self.root / modguard.CRASH_FILE)["streak"], 1)
        later = self.guard()
        later._clean_end()
        self.assertEqual(modguard._read_json(self.root / modguard.CRASH_FILE)["streak"], 0)

    def test_a_failure_while_the_game_is_loading_restarts_without_the_mod_that_caused_it(self):
        guard = self.guard()
        try:
            self.module.boom()
        except RuntimeError as exc:
            guard.on_failure(exc, starting=True)
        self.assertEqual(self.exited, [0])
        self.assertIn("m", modguard.start_session(self.mods(), self.root, self.internal).auto_off)

    def test_a_failure_nobody_can_be_blamed_for_does_not_restart(self):
        guard = self.guard()
        try:
            1 / 0
        except ZeroDivisionError as exc:
            guard.on_failure(exc, starting=True)
        self.assertEqual((self.launched, self.exited), ([], []))


class RestartTests(Workspace):
    def setUp(self):
        super().setUp()
        self.script = str(write(self.root / "go.py", "# the launch script\n"))

    def test_it_starts_the_same_command_with_the_update_check_skipped_and_ends_this_process(self):
        calls, exited = [], []
        env = {"A": "1"}
        self.assertTrue(modguard.restart(["py", self.script], "C:/here", env, lambda cmd, **kw: calls.append((cmd, kw)), exited.append))
        command, kwargs = calls[0]
        self.assertEqual(command, ["py", self.script])
        self.assertEqual((kwargs["cwd"], kwargs["env"]["A"], kwargs["env"][modguard.ENV_NO_UPDATE], kwargs["env"][modguard.ENV_RESTARTS]),
                         ("C:/here", "1", "1", "1"))
        self.assertEqual(exited, [0])

    def test_it_gives_up_after_the_limit_or_without_a_command(self):
        calls = []
        env = {modguard.ENV_RESTARTS: str(modguard.MAX_RESTARTS)}
        self.assertFalse(modguard.restart(["py", self.script], None, env, lambda *a, **k: calls.append(a), lambda code: None))
        with mock.patch.object(modguard, "_LAUNCH", None):
            self.assertFalse(modguard.restart(None, None, {}, lambda *a, **k: calls.append(a), lambda code: None))
        self.assertEqual(calls, [])

    def test_it_does_not_end_this_process_for_a_script_that_is_not_there(self):
        """The launch scripts change the working directory: a relative path taken at the wrong moment would point at nothing."""
        calls, exited = [], []
        self.assertFalse(modguard.restart(["py", "no/such/start.py"], None, {}, lambda *a, **k: calls.append(a), exited.append))
        self.assertEqual((calls, exited), ([], []))

    def test_it_does_not_end_this_process_when_the_new_one_dies_at_once(self):
        import subprocess
        exited = []
        dies = mock.Mock()
        dies.wait.return_value = 1                                              # returned within the wait: it has ended
        self.assertFalse(modguard.restart(["py", self.script], None, {}, lambda *a, **k: dies, exited.append))
        self.assertEqual(exited, [])
        lives = mock.Mock()
        lives.wait.side_effect = subprocess.TimeoutExpired("py", 0.5)           # still running after the wait
        self.assertTrue(modguard.restart(["py", self.script], None, {}, lambda *a, **k: lives, exited.append))
        self.assertEqual(exited, [0])


class SplitEntryTests(unittest.TestCase):
    SOURCE = b'from __future__ import annotations\nimport os\n\n\ndef f():\n    return 1\n\n\nif __name__ == "__main__":\n    MAIN.append(f())\n'

    def test_it_cuts_at_the_main_block_and_keeps_the_line_numbers(self):
        head, tail, flags = modguard.split_entry(self.SOURCE)
        self.assertTrue(head.endswith(b"    return 1\n\n\n"))
        self.assertEqual(tail.count(b"\n"), self.SOURCE.count(b"\n"))
        self.assertEqual(tail.split(b"\n")[8], b'if __name__ == "__main__":')       # the line it has in the whole file
        self.assertNotEqual(flags, 0)                                           # from __future__ import annotations carries over

    def test_the_last_block_is_the_one_that_counts(self):
        data = b'if __name__ == "__main__":\n    A = 1\nx = 2\nif __name__ == "__main__":\n    B = 1\n'
        head, tail, _ = modguard.split_entry(data)
        self.assertIn(b"x = 2", head)
        self.assertIn(b"B = 1", tail)

    def test_a_file_without_a_main_block_is_not_split(self):
        self.assertIsNone(modguard.split_entry(b"x = 1\n"))
        self.assertIsNone(modguard.split_entry(b'    if __name__ == "__main__":\n        pass\n'))      # indented: not top level

    def test_crlf_files_split_too(self):
        head, tail, _ = modguard.split_entry(b'x = 1\r\nif __name__ == "__main__":\r\n    y = 2\r\n')
        self.assertEqual(head, b"x = 1\r\n")


class RunMainTests(Workspace):
    """``Installed.run_main`` with a guard: head, then the check, then the block that starts the game."""

    GAME = '''
        ORDER = []
        ORDER.append("head")


        def start():
            raise RuntimeError("in the game's main block")


        if __name__ == "__main__":
            ORDER.append("main")
            %s
    '''

    def installed(self, game_source: str, guard):
        write(self.internal / "Client.py", game_source)
        result = modloader.BuildResult()
        return modloader.Installed(result, self.internal, self.root / "cache", mods=[], guard=guard)

    def guard_double(self):
        calls = []
        guard = mock.Mock()
        guard.after_import.side_effect = lambda namespaces: calls.append(("check", namespaces[0]["ORDER"][:]))
        guard.on_failure.side_effect = lambda exc, starting: calls.append(("failure", starting, type(exc).__name__))
        return guard, calls

    def test_the_check_runs_between_the_games_definitions_and_the_block_that_starts_it(self):
        guard, calls = self.guard_double()
        scope = {"__name__": "__main__"}
        self.installed(self.GAME % "pass", guard).run_main("Client.py", scope)
        self.assertEqual(scope["ORDER"], ["head", "main"])
        self.assertEqual(calls, [("check", ["head"])])                          # when it ran, only the head had

    def test_without_a_guard_the_file_runs_in_one_piece(self):
        scope = {"__name__": "__main__"}
        self.installed(self.GAME % "pass", None).run_main("Client.py", scope)
        self.assertEqual(scope["ORDER"], ["head", "main"])

    def test_a_failure_in_the_main_block_is_reported_and_keeps_the_games_own_line_numbers(self):
        guard, calls = self.guard_double()
        scope = {"__name__": "__main__"}
        source = self.GAME % "start()"
        try:                                           # not assertRaises: it throws the traceback away
            self.installed(source, guard).run_main("Client.py", scope)
        except RuntimeError as exc:
            error = exc
        else:
            self.fail("the game's error was swallowed")
        self.assertEqual(calls[-1], ("failure", False, "RuntimeError"))
        lines = textwrap.dedent(source).lstrip("\n").split("\n")
        frames = []
        tb = error.__traceback__
        while tb is not None:
            frames.append((os.path.basename(tb.tb_frame.f_code.co_filename), tb.tb_lineno))
            tb = tb.tb_next
        game_frames = [line for name, line in frames if name == "Client.py"]                    # the block, then start()
        self.assertEqual(len(game_frames), 2)
        self.assertIn("start()", lines[game_frames[0] - 1])                                       # the call is in the main block
        self.assertIn("RuntimeError(\"in the game's main block\")", lines[game_frames[1] - 1])    # start() raises on ITS own line

    def test_a_failure_while_loading_is_reported_as_starting(self):
        guard, calls = self.guard_double()
        with self.assertRaises(ZeroDivisionError):
            self.installed("1 / 0\n\nif __name__ == \"__main__\":\n    pass\n", guard).run_main("Client.py", {"__name__": "__main__"})
        self.assertEqual(calls, [("failure", True, "ZeroDivisionError")])

    def test_leaving_through_sys_exit_is_not_a_failure(self):
        guard, calls = self.guard_double()
        with self.assertRaises(SystemExit):
            self.installed(self.GAME % "raise SystemExit(0)", guard).run_main("Client.py", {"__name__": "__main__"})
        self.assertNotIn("failure", [c[0] for c in calls])

    def test_the_two_pieces_are_cached_and_run_the_same_the_second_time(self):
        guard, _calls = self.guard_double()
        for _ in range(2):
            scope = {"__name__": "__main__"}
            self.installed(self.GAME % "pass", guard).run_main("Client.py", scope)
            self.assertEqual(scope["ORDER"], ["head", "main"])
        names = sorted(p.name.split("-")[0] for p in (self.root / "cache").glob("*.bin"))
        self.assertEqual(names, ["Client_head", "Client_main"])


class InstallTests(Workspace):
    """``modloader.install(interactive=True)`` with the guard: what an earlier run left decides what is switched off."""

    def setUp(self):
        super().setUp()
        self.mod("good", {"mod.json": "{}", "Client.py.patch": "@@ one @@\n x = 1\n+y = 2\n"})
        self.mod("bad", {"mod.json": "{}", "Client.py.patch": "@@ two @@\n x = 1\n+z = 3\n", "bad.py": "A = 1\n"})
        self.boxes = []
        for patcher in (mock.patch.object(modloader, "REPO_DIR", self.root), mock.patch.object(modloader, "REPORT_FILE", self.root / "mods_report.txt"),
                        mock.patch.object(modloader, "_message_box", self.boxes.append),
                        mock.patch.object(modloader, "_installed", None), mock.patch.object(sys, "meta_path", list(sys.meta_path)),
                        mock.patch.object(sys, "path", list(sys.path))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def install(self, **kw):
        return modloader.install(self.mods_dir, self.internal, self.root / "cache", interactive=True, **kw)

    def test_a_mod_switched_off_by_an_earlier_run_is_off_with_its_reason(self):
        session = modguard.start_session(self.mods(), self.root, self.internal)
        modguard.switch_off(session, next(m for m in self.mods() if m.name == "bad"), "it crashed the game (ValueError: x)", "crash")
        info = self.install()
        self.assertEqual(info.result.applied, ["good"])
        self.assertEqual(info.result.auto_off, {"bad": "it crashed the game (ValueError: x)"})
        self.assertIn("it crashed the game", info.result.failed["bad"][0])
        self.assertNotIn("bad", info.result.skipped)
        self.assertIsNotNone(info.guard)
        report = (self.root / "mods_report.txt").read_text(encoding="utf-8")
        self.assertIn("SWITCHED OFF  bad (automatically", report)
        self.assertEqual(len(self.boxes), 1)
        self.assertIn("bad: it crashed the game", self.boxes[0])
        self.assertNotIn("code changed under them", self.boxes[0])                # that wording is for patches that stopped fitting

    def test_the_player_is_told_once(self):
        session = modguard.start_session(self.mods(), self.root, self.internal)
        modguard.switch_off(session, next(m for m in self.mods() if m.name == "bad"), "why", "crash")
        self.install()
        modloader.uninstall()
        self.install()
        self.assertEqual(len(self.boxes), 1)

    def test_safe_mode_starts_the_game_with_every_mod_off(self):
        modguard._write_json(self.root / modguard.CRASH_FILE, {"streak": modguard.SAFE_MODE_AFTER})
        info = self.install()
        self.assertTrue(info.safe_mode)
        self.assertEqual(info.result.applied, [])
        self.assertIsNone(info.guard)
        self.assertIn("SAFE MODE", (self.root / "mods_report.txt").read_text(encoding="utf-8"))
        self.assertEqual(len(self.boxes), 1)
        self.assertFalse(modloader.install(self.mods_dir, self.internal, self.root / "cache", interactive=True).safe_mode)

    def test_a_clean_install_has_a_guard_and_no_message(self):
        info = self.install()
        self.assertEqual(sorted(info.result.applied), ["bad", "good"])
        self.assertIsNotNone(info.guard)
        self.assertEqual(self.boxes, [])

    def test_the_guard_can_be_turned_off(self):
        os.environ[modguard.ENV_OFF] = "1"
        self.assertIsNone(self.install().guard)

    def test_the_tests_never_get_a_guard(self):
        info = modloader.install(self.mods_dir, self.internal, self.root / "cache")
        self.assertIsNone(info.guard)

    def test_the_lines_a_patch_adds_are_recorded_for_the_attribution(self):
        info = self.install()
        spans = info.result.spans["Client.py"]
        self.assertEqual(sorted(mod for _first, _last, mod in spans), ["bad", "good"])
        patched = info.result.sources["Client.py"].decode("utf-8").split("\n")
        for first, last, mod in spans:
            added = patched[first - 1:last]
            self.assertEqual(added, ["y = 2"] if mod == "good" else ["z = 3"])


class TheRealGameTests(unittest.TestCase):
    def test_no_mod_wrapper_fails_to_take_a_call_the_games_function_takes(self):
        """The check the client runs at start, run on every update by the suite: a mod's wrapper of a game function must take every
        argument that function takes. (v0.5.89.1 added one to ship_inventory_view; the mod that named three crashed the client on login.)"""
        info = modloader.installed()
        if info is None or info.switched_off_by_env:
            self.skipTest("the mods are not in effect in this run")
        try:
            import Client                                                       # noqa: F401  (every mod's install() hook runs)
        except Exception as exc:                                                # noqa: BLE001
            self.skipTest(f"the game's Client cannot be imported here: {exc}")
        attributor = modguard.Attributor(info.mods, info.internal, info.result.spans)
        problems = modguard.check_wrappers(attributor)
        self.assertEqual([f"{p.mod}: {p.reason()}" for p in problems], [])


if __name__ == "__main__":
    unittest.main()

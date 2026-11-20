import modloader
import inspect
import threading
import time
import unittest
from pathlib import Path

import options_saver


class FakeLogin:
    """The shape of login.LoginScreen.save_options: every argument is 'set it, None = leave'."""

    def __init__(self, delay=0.0, fail_once=False):
        self.calls = []                  # (thread name, kwargs)
        self.delay = delay
        self.fail_once = fail_once

    def save_options(self, volume=None, fullscreen=None, panels=None, vsync=None) -> str:
        """doc of the real one"""
        time.sleep(self.delay)
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("disk full")
        self.calls.append((threading.current_thread().name,
                           dict(volume=volume, fullscreen=fullscreen, panels=panels, vsync=vsync)))
        return "saved"


def make_class():
    cls = type("Login", (FakeLogin,), {})
    cls.save_options = FakeLogin.save_options
    options_saver.install(cls)
    return cls


def from_the_window_thread(fn, *args, **kwargs):
    box = {}

    def run():
        t0 = time.perf_counter()
        box["result"] = fn(*args, **kwargs)
        box["elapsed"] = time.perf_counter() - t0

    thread = threading.Thread(target=run, name=options_saver.WINDOW_THREAD_NAME)
    thread.start()
    thread.join(5)
    return box


class OptionsSaverTests(unittest.TestCase):
    def setUp(self):
        options_saver._reset()
        self.addCleanup(options_saver._reset)
        self.addCleanup(options_saver.flush)

    def test_a_save_from_the_game_window_returns_at_once_and_is_written_by_another_thread(self):
        login = make_class()(delay=0.3)
        box = from_the_window_thread(login.save_options, 0.5, panels={"a": 1})
        self.assertLess(box["elapsed"], 0.15)            # the 0.3 s save did not hold the caller
        self.assertIsNone(box["result"])
        self.assertTrue(options_saver.flush(5))
        self.assertEqual(len(login.calls), 1)
        thread_name, kwargs = login.calls[0]
        self.assertEqual(thread_name, "options-saver")
        self.assertEqual(kwargs, dict(volume=0.5, fullscreen=None, panels={"a": 1}, vsync=None))

    def test_a_burst_of_saves_becomes_one_real_save_with_the_latest_of_every_field(self):
        login = make_class()(delay=0.2)

        def burst():
            login.save_options(0.2, True)                          # positional, as Client.py does
            login.save_options(panels={"v": 1})
            login.save_options(panels={"v": 2}, fullscreen=False)  # False is a value, not "unset"
            login.save_options(volume=0.0)                         # so is 0.0
            login.save_options(vsync=None)                         # None: leave as is

        from_the_window_thread(burst)
        self.assertTrue(options_saver.flush(5))
        merged = {}
        for _name, kwargs in login.calls:
            merged.update({k: v for k, v in kwargs.items() if v is not None})
        self.assertEqual(merged, dict(volume=0.0, fullscreen=False, panels={"v": 2}))
        self.assertLessEqual(len(login.calls), 2)        # the first may have started before the rest

    def test_a_save_from_any_other_thread_is_synchronous_and_returns_the_real_result(self):
        login = make_class()()
        self.assertEqual(login.save_options(0.7), "saved")
        self.assertEqual(login.calls, [(threading.current_thread().name,
                                        dict(volume=0.7, fullscreen=None, panels=None, vsync=None))])

    def test_a_synchronous_save_waits_for_queued_ones_so_it_lands_last(self):
        login = make_class()(delay=0.15)
        from_the_window_thread(login.save_options, 0.1)           # queued, takes 0.15 s
        login.save_options(0.9)                                   # must not overtake it
        self.assertEqual([k["volume"] for _n, k in login.calls], [0.1, 0.9])

    def test_a_failing_save_is_logged_and_does_not_stop_later_ones(self):
        login = make_class()(fail_once=True)
        with self.assertLogs(options_saver.logger, "ERROR"):
            from_the_window_thread(login.save_options, 0.1)
            self.assertTrue(options_saver.flush(5))
        from_the_window_thread(login.save_options, 0.2)
        self.assertTrue(options_saver.flush(5))
        self.assertEqual([k["volume"] for _n, k in login.calls], [0.2])

    def test_flush_gives_up_after_its_timeout_if_the_disk_never_answers(self):
        release = threading.Event()

        class Stuck(FakeLogin):
            def save_options(self, volume=None, fullscreen=None, panels=None, vsync=None):
                release.wait(5)

        options_saver.install(Stuck)
        stuck = Stuck()
        from_the_window_thread(stuck.save_options, 0.3)
        started = time.perf_counter()
        self.assertFalse(options_saver.flush(0.1))
        self.assertLess(time.perf_counter() - started, 1.0)
        release.set()
        self.assertTrue(options_saver.flush(5))

    def test_install_is_idempotent_and_keeps_the_original_reachable(self):
        cls = make_class()
        wrapped = cls.save_options
        options_saver.install(cls)
        self.assertIs(cls.save_options, wrapped)
        self.assertEqual(wrapped.__doc__, "doc of the real one")
        self.assertTrue(callable(wrapped.__wrapped__))


class RealLoginScreenTests(unittest.TestCase):
    def test_every_argument_of_the_real_save_options_binds_through_the_wrapper(self):
        import login
        real = getattr(login.LoginScreen.save_options, "__wrapped__", login.LoginScreen.save_options)
        signature = inspect.signature(real)
        names = list(signature.parameters)[1:]
        self.assertIn("panels", names)
        values = {name: None for name in names}
        values["panels"], values["volume"], values["fullscreen"] = {"x": 1}, 0.0, False
        signature.bind(object(), **values)                # the wrapper binds with this same signature

    def test_client_installs_it_at_import(self):
        source = modloader.game_source("Client.py")
        self.assertIn("options_saver.install(LoginScreen)", source)


if __name__ == "__main__":
    unittest.main()

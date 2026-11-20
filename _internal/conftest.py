"""pytest setup for the client's tests: the mod loader, so the game modules the tests import are the patched ones the
client runs (the files on disk are the dev's, untouched) and the mods' own modules are importable, the runtime
compatibility shim the launchers install before the game starts (moderngl_compat), so the game's own GPU tests run against the same
moderngl behaviour as the game does, and a throwaway scan_data/ so no test writes the real archive. See modloader.py and
mods/. The tests of the mods live in mods/<name>/ and share this setup through mods/conftest.py.

A mod can make one of the dev's own tests fail on purpose (it changes the behaviour the test asserts). It lists that test
in its ``expected_failures.txt`` and the hook below marks it as a strict expected failure (modloader.expected_failures)."""
import modloader

modloader.setup_for_tests()


def pytest_collection_modifyitems(config, items):
    modloader.mark_expected_failures(items)

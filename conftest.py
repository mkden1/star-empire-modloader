"""pytest setup for the tests that live in the mod folders (mods/<name>/test_*.py): the same as _internal/conftest.py
-- the mod loader, the moderngl shim, a throwaway scan_data/ and the mods' expected failures -- with the game's own folder
on the path so the tests can import the game."""
import sys
from pathlib import Path

_INTERNAL = Path(__file__).resolve().parent.parent / "_internal"
if str(_INTERNAL) not in sys.path:
    sys.path.insert(0, str(_INTERNAL))

import modloader

modloader.setup_for_tests()


def pytest_collection_modifyitems(config, items):
    modloader.mark_expected_failures(items)

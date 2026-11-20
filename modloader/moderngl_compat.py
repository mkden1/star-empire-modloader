"""moderngl_compat.py - let this runtime's moderngl take the uniform values the game's newer moderngl takes.

The game is written against a recent moderngl, which accepts any sequence for a uniform. The moderngl this runtime
builds from source for Python 3.14 (custom_build/fix_runtime.py, moderngl 5.6.4) is stricter: its compiled setter wants
a LIST for an array uniform and a TUPLE for a vector. v0.5.69's multi-sun hull lighting uploads ``u_suns`` and
``u_sun_cols`` (``uniform vec3 x[4]``) as tuples of tuples, so the first frame that drew a hull raised
``moderngl.error.Error: the value must be a list not tuple`` and the client closed right after login.

``install()`` wraps the setter of ``moderngl.program_members.uniform.Uniform.value`` so that, before the compiled setter
sees it, a value is put in the shape it insists on:

  * an array uniform (``array_length`` above 1) gets a list, and for an array of vectors or matrices each element a tuple;
  * a vector or matrix that is not an array gets a tuple when it was given a list.

Anything else (scalars, numpy arrays, bytes, values already in the right shape) goes through untouched, so no call that
worked before changes, and a wrong value still raises moderngl's own error. A moderngl that no longer has this property
(a newer build) needs nothing and is left alone.

It is part of the runtime, not a mod: the launchers (start_modded_client.py, run_client.py) install it before the game
starts, whatever mods are in use, and ``modloader.setup_for_tests()`` installs it for the test run, so the game's own GPU
tests see the same moderngl behaviour. Kill switch: STAR_EMPIRE_NO_MODERNGL_COMPAT=1.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return os.environ.get("STAR_EMPIRE_NO_MODERNGL_COMPAT") != "1"


def conform(value, array_length, dimension):
    """``value`` in the shape the strict compiled setter takes for a uniform of this length and dimension."""
    if array_length > 1:
        if isinstance(value, (tuple, list)):
            if dimension > 1:
                return [tuple(item) if isinstance(item, list) else item for item in value]
            return list(value) if isinstance(value, tuple) else value
    elif dimension > 1 and isinstance(value, list):
        return tuple(value)
    return value


def install(uniform_class=None) -> bool:
    """Wrap the ``value`` setter of moderngl's ``Uniform``. Idempotent; True if hooked. ``uniform_class`` is for tests."""
    if uniform_class is None:
        try:
            from moderngl.program_members.uniform import Uniform as uniform_class
        except Exception:
            logger.warning("moderngl_compat: not installed, moderngl cannot be imported", exc_info=True)
            return False
    prop = uniform_class.__dict__.get("value")
    if not isinstance(prop, property) or prop.fset is None:
        logger.info("moderngl_compat: not installed, this moderngl's Uniform has no value setter to wrap")
        return False
    original = prop.fset
    if getattr(original, "_moderngl_compat", False):
        return False

    def value(self, new_value):
        if enabled():
            try:
                new_value = conform(new_value, self.array_length, self.dimension)
            except Exception:
                pass                                 # never worse than without the shim: moderngl judges the value
        original(self, new_value)

    value.__doc__ = original.__doc__
    value.__wrapped__ = original
    value._moderngl_compat = True
    uniform_class.value = property(prop.fget, value, prop.fdel, prop.__doc__)
    return True

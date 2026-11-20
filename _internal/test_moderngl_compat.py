"""moderngl_compat: this runtime's moderngl (5.6.4) takes a list for an array uniform and a tuple for a vector; the game's
newer moderngl takes either. The shim puts a value in the shape the compiled setter wants, so v0.5.69's multi-sun hull
lighting (which sends u_suns as a tuple of tuples) no longer closes the client on its first frame in the world."""
import modloader
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import moderngl_compat as compat

try:
    import moderngl
except Exception:                                    # a runtime without moderngl: the GPU tests skip
    moderngl = None


def stub_class(array_length=1, dimension=1):
    """A stand-in for moderngl's Uniform: a ``value`` property whose setter records what it was given."""
    class Uniform:
        def __init__(self):
            self.array_length, self.dimension, self.given = array_length, dimension, []

        @property
        def value(self):
            """the original doc"""
            return self.given[-1] if self.given else None

        @value.setter
        def value(self, new):
            self.given.append(new)
    return Uniform


class ConformTests(unittest.TestCase):
    def test_an_array_of_vectors_becomes_a_list_of_tuples(self):
        self.assertEqual(compat.conform(((1.0, 2.0, 3.0), (4.0, 5.0, 6.0)), 2, 3), [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)])
        self.assertIsInstance(compat.conform(((1.0, 2.0, 3.0),) * 4, 4, 3), list)

    def test_the_vectors_inside_an_array_are_tuples_whatever_they_came_as(self):
        out = compat.conform([[1.0, 2.0, 3.0], (4.0, 5.0, 6.0)], 2, 3)
        self.assertEqual(out, [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)])
        self.assertTrue(all(isinstance(v, tuple) for v in out))
        out = compat.conform(([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]), 2, 3)
        self.assertTrue(all(isinstance(v, tuple) for v in out))

    def test_an_array_of_scalars_becomes_a_list(self):
        self.assertEqual(compat.conform((1.0, 2.0, 3.0), 3, 1), [1.0, 2.0, 3.0])
        self.assertEqual(compat.conform([1, 2, 3], 3, 1), [1, 2, 3])

    def test_a_vector_or_matrix_that_is_not_an_array_becomes_a_tuple(self):
        self.assertEqual(compat.conform([1.0, 2.0, 3.0], 1, 3), (1.0, 2.0, 3.0))
        self.assertIsInstance(compat.conform([0.0] * 9, 1, 9), tuple)

    def test_values_already_in_shape_are_the_same_object(self):
        vec, arr = (1.0, 2.0, 3.0), [(1.0, 2.0, 3.0)] * 4
        self.assertIs(compat.conform(vec, 1, 3), vec)
        self.assertIs(compat.conform([1.0, 2.0], 2, 1).__class__, list)
        self.assertEqual(compat.conform(arr, 4, 3), arr)

    def test_scalars_and_other_types_pass_through_untouched(self):
        for value in (1, 2.5, True, b"\x00\x01", "x", None):
            self.assertIs(compat.conform(value, 1, 1), value)
        for array_like in (b"\x00" * 12, bytearray(12), memoryview(bytes(12))):
            self.assertIs(compat.conform(array_like, 4, 3), array_like)
            self.assertIs(compat.conform(array_like, 1, 3), array_like)

    def test_a_scalar_uniform_given_a_sequence_is_left_for_moderngl_to_judge(self):
        self.assertEqual(compat.conform((1.0, 2.0), 1, 1), (1.0, 2.0))
        self.assertEqual(compat.conform([1.0, 2.0], 1, 1), [1.0, 2.0])

    def test_the_value_is_not_changed_in_place(self):
        original = ((1.0, 2.0, 3.0), (4.0, 5.0, 6.0))
        compat.conform(original, 2, 3)
        self.assertEqual(original, ((1.0, 2.0, 3.0), (4.0, 5.0, 6.0)))
        inner = [1.0, 2.0, 3.0]
        outer = [inner, [4.0, 5.0, 6.0]]
        compat.conform(outer, 2, 3)
        self.assertIsInstance(outer[0], list)


class InstallTests(unittest.TestCase):
    def test_the_setter_gets_the_shape_it_wants(self):
        cls = stub_class(array_length=4, dimension=3)
        self.assertTrue(compat.install(cls))
        uniform = cls()
        uniform.value = ((0.0, 1.0, 2.0),) * 4
        self.assertIsInstance(uniform.given[-1], list)
        plain = stub_class(array_length=1, dimension=3)
        compat.install(plain)
        uniform = plain()
        uniform.value = [1.0, 2.0, 3.0]
        self.assertEqual(uniform.given[-1], (1.0, 2.0, 3.0))

    def test_what_moderngl_already_took_is_passed_on_as_it_was(self):
        cls = stub_class(array_length=4, dimension=3)
        compat.install(cls)
        uniform = cls()
        value = [(0.0, 1.0, 2.0)] * 4
        uniform.value = value
        self.assertEqual(uniform.given, [value])
        uniform.value = 7
        self.assertEqual(uniform.given[-1], 7)

    def test_the_getter_and_the_doc_are_kept(self):
        cls = stub_class()
        compat.install(cls)
        uniform = cls()
        uniform.value = (1.0,)
        self.assertEqual(uniform.value, (1.0,))
        self.assertEqual(cls.value.__doc__, "the original doc")
        self.assertTrue(hasattr(cls.value.fset, "__wrapped__"))

    def test_install_is_idempotent(self):
        cls = stub_class(array_length=2, dimension=1)
        self.assertTrue(compat.install(cls))
        self.assertFalse(compat.install(cls))
        uniform = cls()
        uniform.value = (1.0, 2.0)
        self.assertEqual(len(uniform.given), 1)                        # wrapped once, so the setter ran once

    def test_a_class_without_a_value_setter_is_left_alone(self):
        class NoProperty:
            value = 3

        class ReadOnly:
            @property
            def value(self):
                return 1

        self.assertFalse(compat.install(NoProperty))
        self.assertFalse(compat.install(ReadOnly))
        self.assertEqual(NoProperty.value, 3)

    def test_the_environment_switch_passes_everything_straight_through(self):
        cls = stub_class(array_length=4, dimension=3)
        compat.install(cls)
        uniform = cls()
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_NO_MODERNGL_COMPAT": "1"}):
            value = ((0.0, 1.0, 2.0),) * 4
            uniform.value = value
        self.assertIs(uniform.given[-1], value)

    def test_a_failure_in_the_shim_never_stops_the_value_reaching_moderngl(self):
        cls = stub_class(array_length=4, dimension=3)
        compat.install(cls)
        uniform = cls()
        value = ((0.0, 1.0, 2.0),) * 4
        with mock.patch.object(compat, "conform", side_effect=RuntimeError("boom")):
            uniform.value = value
        self.assertIs(uniform.given[-1], value)

    def test_the_real_class_is_hooked_once_by_the_time_the_tests_run(self):
        if moderngl is None:
            self.skipTest("no moderngl")
        from moderngl.program_members.uniform import Uniform
        self.assertTrue(getattr(Uniform.__dict__["value"].fset, "_moderngl_compat", False))
        self.assertFalse(compat.install())                              # conftest.py / Client.py already did

    def test_a_missing_moderngl_is_not_an_error(self):
        with mock.patch.dict("sys.modules", {"moderngl.program_members.uniform": None}):
            with self.assertLogs("moderngl_compat", level="WARNING"):
                self.assertFalse(compat.install())


@unittest.skipIf(moderngl is None, "no moderngl")
class RealMetalTests(unittest.TestCase):
    """The real compiled setter, on a real OpenGL context."""

    VERTEX = """
#version 330
in vec2 pos;
uniform vec3 u_suns[4];
uniform float u_f[3];
uniform vec3 one;
uniform mat3 m;
uniform int n;
out vec3 v;
void main() {
    v = u_suns[0] + u_suns[3] + one + m[0] + vec3(u_f[0] + u_f[2] + float(n));
    gl_Position = vec4(pos, 0.0, 1.0);
}
"""
    FRAGMENT = """
#version 330
in vec3 v; out vec4 c;
void main() { c = vec4(v, 1.0); }
"""

    @classmethod
    def setUpClass(cls):
        try:
            cls.ctx = moderngl.create_standalone_context(require=330)
        except Exception as exc:
            raise unittest.SkipTest(f"OpenGL 3.3 context unavailable: {exc}")
        cls.prog = cls.ctx.program(vertex_shader=cls.VERTEX, fragment_shader=cls.FRAGMENT)

    @classmethod
    def tearDownClass(cls):
        cls.ctx.release()

    SUNS = ((1.0, 2.0, 3.0), (4.0, 5.0, 6.0), (0.0, 0.0, 0.0), (0.0, 0.0, 9.0))

    def test_a_tuple_of_tuples_reaches_an_array_uniform_and_reads_back(self):
        self.prog["u_suns"] = self.SUNS
        self.assertEqual(self.prog["u_suns"].value, list(self.SUNS))

    def test_a_list_of_lists_does_too(self):
        self.prog["u_suns"] = [list(v) for v in self.SUNS]
        self.assertEqual(self.prog["u_suns"].value, list(self.SUNS))

    def test_a_list_of_tuples_still_does(self):
        self.prog["u_suns"] = list(self.SUNS)
        self.assertEqual(self.prog["u_suns"].value, list(self.SUNS))

    def test_an_array_of_floats_takes_a_tuple(self):
        self.prog["u_f"] = (1.0, 2.0, 3.0)
        self.assertEqual(self.prog["u_f"].value, [1.0, 2.0, 3.0])

    def test_a_plain_vector_takes_a_list_or_a_tuple(self):
        self.prog["one"] = (1.0, 2.0, 3.0)
        self.assertEqual(self.prog["one"].value, (1.0, 2.0, 3.0))
        self.prog["one"] = [4.0, 5.0, 6.0]
        self.assertEqual(self.prog["one"].value, (4.0, 5.0, 6.0))

    def test_a_matrix_takes_a_list_or_a_tuple(self):
        self.prog["m"] = tuple(float(i) for i in range(9))
        first = self.prog["m"].value
        self.prog["m"] = [float(i) for i in range(9)]
        self.assertEqual(self.prog["m"].value, first)

    def test_scalars_are_as_before(self):
        self.prog["n"] = 3
        self.assertEqual(self.prog["n"].value, 3)

    def test_a_wrong_value_still_raises_moderngls_own_error(self):
        with self.assertRaises(moderngl.Error):
            self.prog["one"] = (1.0, 2.0)                                # a vec3 given two numbers
        with self.assertRaises(moderngl.Error):
            self.prog["u_suns"] = ((1.0, 2.0),) * 4                      # an array of vec3 of pairs

    def test_without_the_shim_this_is_the_error_that_closed_the_client(self):
        with mock.patch.dict(os.environ, {"STAR_EMPIRE_NO_MODERNGL_COMPAT": "1"}):
            with self.assertRaisesRegex(moderngl.Error, "must be a list not tuple"):
                self.prog["u_suns"] = self.SUNS

    def test_the_games_own_sun_upload_runs_on_a_real_program(self):
        """hull_batch's _send_sun, the call that raised, against a program with the uniforms it sets."""
        import hull_light
        from hull_batch import HullSpriteBatch
        prog = self.ctx.program(vertex_shader="""
#version 330
in vec2 pos;
uniform int u_sun_count;
uniform vec3 u_suns[4];
uniform vec3 u_sun_cols[4];
uniform float u_reach;
uniform float u_fade;
uniform float u_floor0;
out vec3 v;
void main() {
    // every element is read, as the real shader's loop does: the driver keeps only the active elements of an array
    v = u_suns[0] + u_suns[1] + u_suns[2] + u_suns[3] + u_sun_cols[0] + u_sun_cols[1] + u_sun_cols[2] + u_sun_cols[3]
        + vec3(float(u_sun_count) + u_reach + u_fade + u_floor0);
    gl_Position = vec4(pos, 0.0, 1.0);
}
""", fragment_shader=self.FRAGMENT)
        sun_a = hull_light.light(-500, 120, (255, 255, 255))
        sun_b = hull_light.light(300, -40, (255, 200, 140))
        batch = SimpleNamespace(prog=prog, _sent={}, _sun=hull_light.as_lights(hull_light.lights([sun_a, sun_b], 5000.0)))
        HullSpriteBatch._send_sun(batch)
        self.assertEqual(prog["u_sun_count"].value, 2)
        self.assertEqual(prog["u_suns"].value[0][:2], (-500.0, 120.0))
        self.assertEqual(prog["u_suns"].value[1][:2], (300.0, -40.0))
        self.assertEqual(prog["u_suns"].value[2:], [(0.0, 0.0, 0.0)] * 2)          # unused slots padded
        self.assertEqual(prog["u_sun_cols"].value[2:], [(1.0, 1.0, 1.0)] * 2)
        self.assertEqual(prog["u_reach"].value, 5000.0)
        HullSpriteBatch._send_sun(batch)                                            # the same again: nothing to send
        batch._sun = None
        HullSpriteBatch._send_sun(batch)
        self.assertEqual(prog["u_sun_count"].value, 0)
        prog.release()


class WhoInstallsItTests(unittest.TestCase):
    """The shim is part of the runtime: the launchers install it before the game starts, and the test run gets it from
    ``modloader.setup_for_tests()``. Not installing it closed the client right after login (the first package launch)."""

    def test_the_dev_launcher_installs_it_before_the_game_starts(self):
        text = (Path(modloader.REPO_DIR) / "custom_build" / "run_client.py").read_text(encoding="utf-8-sig")
        self.assertIn("moderngl_compat.install()", text)
        self.assertLess(text.index("moderngl_compat.install()"), text.index("run_main("))

    def test_the_test_run_installs_it_through_setup_for_tests(self):
        loader = (modloader.INTERNAL_DIR / "modloader.py").read_text(encoding="utf-8-sig")
        self.assertIn("moderngl_compat.install()", loader)
        self.assertIn("modloader.setup_for_tests()", (modloader.INTERNAL_DIR / "conftest.py").read_text(encoding="utf-8-sig"))

    def test_it_is_installed_by_the_time_the_tests_run(self):
        if moderngl is None:
            self.skipTest("no moderngl")
        from moderngl.program_members.uniform import Uniform
        self.assertTrue(getattr(Uniform.__dict__["value"].fset, "_moderngl_compat", False))


if __name__ == "__main__":
    unittest.main()

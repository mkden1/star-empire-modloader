"""combat_log.py / combat_log_render.py: the Combat Log's big blocks of methods live in modules of the mod and are
attached to the game's classes by one-line hooks (see modloader.attach_methods)."""
import unittest

import modloader
import combat_log
import combat_log_render


def non_dunder(cls):
    return {n: v for n, v in vars(cls).items() if not (n.startswith("__") and n.endswith("__"))}


class WindowMethodsTests(unittest.TestCase):
    def test_the_game_window_has_every_method_through_the_hook_at_the_end_of_client_py(self):
        import Client
        for name, value in non_dunder(combat_log.WindowMethods).items():
            with self.subTest(method=name):
                self.assertIs(vars(Client.SolarSystemWindow).get(name), value)
        self.assertEqual(Client.SolarSystemWindow._record_combat_log_hit.__qualname__,
                         "SolarSystemWindow._record_combat_log_hit")

    def test_the_hook_is_one_line_in_the_patched_client_and_the_code_is_not_inlined_any_more(self):
        source = modloader.game_source("Client.py")
        self.assertIn("combat_log.install(SolarSystemWindow)", source)
        self.assertIn("from combat_log import _COMBAT_LOG_MAX_LINES, _HITSPLAT_MAX", source)
        self.assertNotIn("def _record_combat_log_hit", source)

    def test_the_limits_client_trims_with_are_the_modules(self):
        import Client
        self.assertEqual(Client._COMBAT_LOG_MAX_LINES, combat_log._COMBAT_LOG_MAX_LINES)
        self.assertEqual(Client._HITSPLAT_MAX, combat_log._HITSPLAT_MAX)

    def test_the_module_resolves_every_name_it_uses(self):
        self.assertEqual(modloader.missing_globals(combat_log), set())
        self.assertEqual(modloader.attachment_problems(combat_log), [])


class RenderMethodsTests(unittest.TestCase):
    def test_render_mixin_has_the_drawing_methods_the_moment_it_is_imported(self):
        import render_mixin
        for name, value in non_dunder(combat_log_render.RenderMethods).items():
            with self.subTest(method=name):
                self.assertIs(vars(render_mixin.RenderMixin).get(name), value)
        self.assertIn("combat_log_render.install(RenderMixin)", modloader.game_source("render_mixin.py"))
        self.assertTrue(modloader.game_source("render_mixin.py").rstrip().endswith("combat_log_render.install(RenderMixin)"))

    def test_the_drawing_borrows_the_games_own_palette_and_label_table(self):
        import render_mixin
        for name in combat_log_render._BORROWED:
            with self.subTest(name=name):
                self.assertIs(getattr(combat_log_render, name), getattr(render_mixin, name))
                self.assertIsNotNone(getattr(combat_log_render, name))

    def test_the_hitsplat_numbers_use_the_limits_of_combat_log(self):
        self.assertIs(combat_log_render._HITSPLAT_LIFETIME_S, combat_log._HITSPLAT_LIFETIME_S)
        self.assertIs(combat_log_render._HITSPLAT_RISE_PX, combat_log._HITSPLAT_RISE_PX)

    def test_the_module_resolves_every_name_it_uses(self):
        import render_mixin  # noqa: F401  (installing fills in the borrowed names)
        self.assertEqual(modloader.attachment_problems(combat_log_render), [])


class ShieldAttributionTests(unittest.TestCase):
    def test_the_shield_state_has_the_lookup_and_the_dataclasses_stay_importable_from_shield_effects(self):
        import shield_attribution
        import shield_effects
        for name, value in non_dunder(shield_attribution.StateMethods).items():
            with self.subTest(method=name):
                self.assertIs(vars(shield_effects.ShieldEffectState).get(name), value)
        self.assertIs(shield_effects.ResolvedAttribution, shield_attribution.ResolvedAttribution)
        self.assertIs(shield_effects._PendingAttribution, shield_attribution._PendingAttribution)

    def test_the_methods_use_the_games_own_private_names(self):
        import shield_attribution
        import shield_effects
        for name in shield_attribution._BORROWED:
            with self.subTest(name=name):
                self.assertIs(getattr(shield_attribution, name), getattr(shield_effects, name))
                self.assertIsNotNone(getattr(shield_attribution, name))

    def test_the_hook_closes_the_file_and_the_inline_code_is_gone(self):
        source = modloader.game_source("shield_effects.py")
        self.assertTrue(source.rstrip().endswith("shield_attribution.install(ShieldEffectState)"))
        self.assertNotIn("def queue_attribution", source)
        self.assertNotIn("class ResolvedAttribution", source)

    def test_the_module_attaches_cleanly(self):
        import shield_attribution
        import shield_effects  # noqa: F401
        self.assertEqual(modloader.attachment_problems(shield_attribution), [])


if __name__ == "__main__":
    unittest.main()

"""combat_batch_handler.py: the SPACE_COMBAT_BATCH handler is a module of the mod, attached to ProtocolMixin by a one-line hook
at the end of protocol.py (see modloader.attach_methods)."""
import unittest

import modloader
import combat_batch_handler


class CombatBatchHandlerModuleTests(unittest.TestCase):
    def test_protocol_mixin_has_the_handler_the_moment_it_is_imported(self):
        import protocol
        self.assertIs(vars(protocol.ProtocolMixin).get("_on_space_combat_batch"),
                      vars(combat_batch_handler.ProtocolMethods)["_on_space_combat_batch"])

    def test_the_patched_protocol_keeps_the_command_table_entry_and_the_hook_only(self):
        source = modloader.game_source("protocol.py")
        self.assertNotIn("def _on_space_combat_batch", source)
        self.assertIn('"SPACE_COMBAT_BATCH ":  self._on_space_combat_batch,', source)
        self.assertTrue(source.rstrip().endswith("combat_batch_handler.install(ProtocolMixin)"))

    def test_the_module_attaches_cleanly(self):
        import protocol  # noqa: F401
        self.assertEqual(modloader.attachment_problems(combat_batch_handler), [])


if __name__ == "__main__":
    unittest.main()

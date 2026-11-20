import json
import unittest

from combat_batch import EVENT_COMMANDS, decode_combat_batch
from protocol import ProtocolMixin


class _ProtocolProbe(ProtocolMixin):
    def __init__(self):
        self._prefix_handlers = None
        self._in_dungeon = False
        self._solar_window = None
        self._combat_batch_sequences = {}
        self.applied = []

    def _log(self, *_args, **_kwargs):
        pass

    def _network_profile(self):
        return None

    def _on_space_hit(self, payload):
        self.applied.append(("hit", payload))

    def _on_space_state(self, payload, arrival_time=None):
        self.applied.append(("state", payload, arrival_time))

    def _on_space_projectiles(self, payload):
        self.applied.append(("projectiles", payload))

    def _on_space_fire_snd(self, payload):
        self.applied.append(("sounds", payload))


class CombatBatchClientTests(unittest.TestCase):
    def test_decoder_rejects_version_and_opcode_drift(self):
        valid = {"v": 1, "q": 1, "a": "solar:7", "t": 1.0}
        self.assertEqual(decode_combat_batch(valid), valid)
        with self.assertRaises(ValueError):
            decode_combat_batch({**valid, "v": 2})
        with self.assertRaises(ValueError):
            decode_combat_batch({**valid, "e": [[999, {}]]})

    def test_client_applies_events_before_state_lanes_without_reparsing(self):
        probe = _ProtocolProbe()
        payload = {
            "v": 1,
            "q": 8,
            "a": "solar:7",
            "t": 50.0,
            "e": [[2, {"target_id": 4, "damage": 3}]],
            "s": {"entities": []},
            "r": {"d": ["round-1"]},
            "f": [{"ox": 1, "oy": 2, "fire_sound": "shot.wav"}],
        }
        probe._on_space_combat_batch(
            json.dumps(payload, separators=(",", ":")), arrival_time=51.0)

        self.assertEqual(
            [row[0] for row in probe.applied],
            ["hit", "state", "projectiles", "sounds"],
        )
        self.assertIs(probe.applied[0][1], probe.applied[0][1])
        self.assertEqual(probe.applied[1][2], 51.0)
        self.assertEqual(probe._combat_batch_sequences["solar:7"], 8)

    def test_route_registers_the_single_batch_prefix(self):
        probe = _ProtocolProbe()
        payload = json.dumps({
            "v": 1, "q": 1, "a": "solar:7", "t": 1.0,
            "e": [[2, {"target_id": 1}]],
        }, separators=(",", ":"))
        probe._route_game_line("SPACE_COMBAT_BATCH " + payload, 2.0)
        self.assertEqual(probe.applied[0][0], "hit")
        self.assertEqual(EVENT_COMMANDS[2], "SPACE_HIT")


if __name__ == "__main__":
    unittest.main()


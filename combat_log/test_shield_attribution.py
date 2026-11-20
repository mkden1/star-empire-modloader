"""queue_attribution / resolve_attributions (shield_attribution.py): the Combat Log's 'who actually fired this' lookup."""
import unittest
from shield_effects import SHIELD_MATCH_GRACE_S, SHIELD_MATCH_TIMEOUT_S, ShieldEffectState
from test_shield_effects import _target


class AttributionTests(unittest.TestCase):
    """queue_attribution / resolve_attributions -- the combat log's
    'who actually fired this' lookup, independent of the shield-pulse
    pipeline above (see ShieldEffectState.__init__'s own comment)."""

    def test_before_grace_elapses_nothing_resolves(self):
        state = ShieldEffectState()
        _target(state, 1.0)
        state.record_projectile_sample(
            "p:one", 1.0, 90.0, 0.0, owner_id=3, radius=1.0)
        state.mark_projectile_despawn("p:one", 1.01)
        state.queue_attribution(7, 1.01, "entry-1")

        self.assertEqual(
            state.resolve_attributions(1.01 + SHIELD_MATCH_GRACE_S / 2), ())

    def test_projectile_match_resolves_owner_weapon_and_turret_flag(self):
        state = ShieldEffectState()
        _target(state, 0.1, x=20.0)   # near the projectile's own path below
        state.record_projectile_sample(
            "p:bulwark", 0.0, -100.0, 0.0,
            vx=800.0, owner_id="player:99", weapon="Corsair Thumper",
            is_turret=True, radius=1.0)
        state.record_projectile_sample(
            "p:bulwark", 0.1, -20.0, 0.0,
            vx=800.0, owner_id="player:99", weapon="Corsair Thumper",
            is_turret=True, radius=1.0)
        state.mark_projectile_despawn("p:bulwark", 0.1)
        state.queue_attribution(7, 0.1, "entry-1")

        results = state.resolve_attributions(0.17)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].key, "entry-1")
        self.assertEqual(results[0].owner_id, "player:99")
        self.assertEqual(results[0].weapon, "Corsair Thumper")
        self.assertTrue(results[0].is_turret)

    def test_beam_match_resolves_owner_with_no_weapon(self):
        state = ShieldEffectState()
        _target(state, 1.0)
        state.record_beam({
            "owner_id": "npc-42",
            "ox": 0.0, "oy": 0.0,
            "ex": 90.0, "ey": 0.0,
        }, 1.0)
        state.queue_attribution(7, 1.01, "entry-1")

        results = state.resolve_attributions(1.01 + SHIELD_MATCH_GRACE_S + 0.01)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].owner_id, "npc-42")
        self.assertEqual(results[0].weapon, "")
        self.assertFalse(results[0].is_turret)

    def test_no_match_times_out_to_owner_none(self):
        state = ShieldEffectState()
        _target(state, 1.0)   # a target exists, but nothing fired at it
        state.queue_attribution(7, 1.0, "entry-1")

        # Still within the timeout -- not yet given up.
        self.assertEqual(
            state.resolve_attributions(1.0 + SHIELD_MATCH_TIMEOUT_S - 0.01),
            ())
        results = state.resolve_attributions(1.0 + SHIELD_MATCH_TIMEOUT_S + 0.01)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].key, "entry-1")
        self.assertIsNone(results[0].owner_id)
        self.assertEqual(results[0].weapon, "")

    def test_multiple_queued_items_resolve_independently_by_key(self):
        state = ShieldEffectState()
        _target(state, 0.1, target_id=7, x=20.0)   # near p:a's path below
        _target(state, 0.1, target_id=8, x=5000.0)  # nothing tracked nearby
        state.record_projectile_sample(
            "p:a", 0.0, -100.0, 0.0, vx=800.0, owner_id="player:1",
            radius=1.0)
        state.record_projectile_sample(
            "p:a", 0.1, -20.0, 0.0, vx=800.0, owner_id="player:1",
            radius=1.0)
        state.mark_projectile_despawn("p:a", 0.1)
        state.queue_attribution(7, 0.1, "entry-A")
        state.queue_attribution(8, 0.1, "entry-B")   # no projectile near 8

        results = state.resolve_attributions(0.17)

        by_key = {r.key: r for r in results}
        self.assertEqual(by_key["entry-A"].owner_id, "player:1")
        # entry-B's target has nothing tracked nearby -- still pending,
        # not yet timed out at this point (age 0.07s < 0.280s timeout).
        self.assertNotIn("entry-B", by_key)

    def test_reset_clears_pending_attributions(self):
        state = ShieldEffectState()
        _target(state, 1.0)
        state.queue_attribution(7, 1.0, "entry-1")
        self.assertEqual(state.diagnostics()["pending_attributions"], 1)

        state.reset()

        self.assertEqual(state.diagnostics()["pending_attributions"], 0)
        self.assertEqual(state.resolve_attributions(10.0), ())

    def test_matching_does_not_mark_the_projectile_claimed(self):
        # A read-only lookup must not steal a candidate the shield-pulse
        # matcher's own pending hit still needs to match against.
        state = ShieldEffectState()
        _target(state, 0.1, x=20.0)   # near p:bulwark's path below
        state.record_projectile_sample(
            "p:bulwark", 0.0, -100.0, 0.0, vx=800.0, owner_id=3, radius=1.0)
        state.record_projectile_sample(
            "p:bulwark", 0.1, -20.0, 0.0, vx=800.0, owner_id=3, radius=1.0)
        state.mark_projectile_despawn("p:bulwark", 0.1)
        state.queue_attribution(7, 0.1, "entry-1")
        state.resolve_attributions(0.17)

        state.record_hit({"target_id": 7, "damage": 8.0}, 0.1)
        draws = state.step(0.17)
        self.assertEqual(len(draws), 1)
        self.assertEqual(draws[0].match_kind, "projectile_terminal")


if __name__ == "__main__":
    unittest.main()

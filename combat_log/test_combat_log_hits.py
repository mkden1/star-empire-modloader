"""Focused coverage for Client.SolarSystemWindow's combat-log/hitsplat hook.

register_hit() itself needs a huge amount of live SolarSystemWindow state
(locks, prediction history, stasis, audio, ...) that no existing test
constructs directly, so this targets the smaller, genuinely new pieces it
delegates to -- _record_combat_log_hit, _resolve_combat_log_attributions,
and _combat_log_actor_name -- called as unbound functions against a minimal
stand-in object carrying only what those methods actually touch. Mirrors
test_dungeon_hit_hook.py's "fake pygame module" import guard, since Client.py
imports pygame at module scope.

Attribution is a real, two-phase process (stage now, resolve a beat later
via ShieldEffectState's projectile/beam correlation -- see
_record_combat_log_hit's own docstring for why), so _FakeWindow carries a
REAL ShieldEffectState rather than a stub: these tests exercise the actual
queue_attribution -> resolve_attributions round trip, not a mocked stand-in
for it.
"""
import sys
import threading
import types
import unittest

try:
    import pygame  # noqa: F401
except ModuleNotFoundError:
    sys.modules["pygame"] = types.ModuleType("pygame")

from Client import SolarSystemWindow
from shield_effects import (
    SHIELD_MATCH_GRACE_S,
    SHIELD_MATCH_TIMEOUT_S,
    ShieldEffectState,
)


class _FakeWindow:
    """Bare object carrying exactly the state the methods under test
    read or write."""

    def __init__(self):
        self._combat_log = []
        self._combat_log_totals = {
            "dealt": 0.0, "taken": 0.0, "dealt_hits": 0, "taken_hits": 0}
        self._combat_log_session_started_at = None
        self._combat_log_pending = {}
        self._combat_log_pending_seq = 0
        self._hitsplats = []
        self._lock = threading.RLock()
        self._npc_entities = {}
        self._remote_entities = {}
        self._hit_world_pos_result = (100.0, 200.0)
        self._targeted_npc_id = None
        self._targeted_pid = None
        self._local_player_id = "me-pid"
        self._shield_effects = ShieldEffectState()

    def _hit_world_pos(self, hit):
        return self._hit_world_pos_result

    def _combat_log_actor_name(self, tid):
        return SolarSystemWindow._combat_log_actor_name(self, tid)


def _stage(window, hit, tid, is_my_hit, now=1.0, me=None):
    SolarSystemWindow._record_combat_log_hit(
        window, hit, tid, is_my_hit, now, me)


def _resolve(window, now):
    SolarSystemWindow._resolve_combat_log_attributions(window, now)


def _track_target(window, target_id, at, *, x=100.0, y=0.0, radius=10.0):
    window._shield_effects.record_target(
        target_id, at, x, y, radius, 500.0, 300.0, radius, 75.0, 100.0)


def _track_owning_projectile(window, target_id, at, owner_id, *,
                             weapon="", is_turret=False,
                             target_x=20.0):
    """Track a target plus a projectile that terminates right on top of
    it, owned by ``owner_id`` -- the shape _match_projectile_locked needs
    to resolve a match (see test_shield_effects.py's own equivalent
    fixtures for why these specific numbers land a match)."""
    _track_target(window, target_id, at, x=target_x)
    window._shield_effects.record_projectile_sample(
        "p:test", at - 0.1, target_x - 80.0, 0.0,
        vx=800.0, owner_id=owner_id, weapon=weapon, is_turret=is_turret,
        radius=1.0)
    window._shield_effects.record_projectile_sample(
        "p:test", at, target_x - 20.0, 0.0,
        vx=800.0, owner_id=owner_id, weapon=weapon, is_turret=is_turret,
        radius=1.0)
    window._shield_effects.mark_projectile_despawn("p:test", at)


class RecordCombatLogHitStagingTests(unittest.TestCase):
    def test_hit_on_our_current_target_is_staged_and_queued(self):
        w = _FakeWindow()
        w._targeted_pid = "enemy-1"
        _stage(w, {"damage": 142.0, "damage_type": "kinetic"},
               "enemy-1", is_my_hit=False)

        self.assertEqual(len(w._combat_log_pending), 1)
        self.assertEqual(w._combat_log, [])   # not finalized yet
        pending = next(iter(w._combat_log_pending.values()))
        self.assertTrue(pending["provisional_dealt"])
        self.assertEqual(pending["damage"], 142.0)

    def test_taken_hit_is_always_staged_regardless_of_target(self):
        w = _FakeWindow()
        _stage(w, {"damage": 88.0}, "me-pid", is_my_hit=True)
        self.assertEqual(len(w._combat_log_pending), 1)

    def test_hit_on_something_we_are_not_targeting_is_never_staged(self):
        w = _FakeWindow()
        w._targeted_pid = "enemy-1"
        _stage(w, {"damage": 500.0}, "enemy-2", is_my_hit=False)
        self.assertEqual(w._combat_log_pending, {})
        self.assertEqual(w._hitsplats, [])

    def test_zero_or_negative_or_missing_damage_is_never_staged(self):
        w = _FakeWindow()
        w._targeted_pid = "enemy-1"
        for hit in ({"damage": 0.0}, {"damage": -5.0}, {}, {"damage": "x"}):
            _stage(w, hit, "enemy-1", is_my_hit=False)
        self.assertEqual(w._combat_log_pending, {})

    def test_hitsplat_pops_immediately_using_the_provisional_guess(self):
        w = _FakeWindow()
        w._targeted_pid = "enemy-1"
        w._hit_world_pos_result = (321.0, 654.0)
        _stage(w, {"damage": 42.0}, "enemy-1", is_my_hit=False, now=3.5)

        self.assertEqual(len(w._hitsplats), 1)
        splat = w._hitsplats[0]
        self.assertEqual((splat["wx"], splat["wy"]), (321.0, 654.0))
        self.assertTrue(splat["dealt"])
        self.assertEqual(splat["born_at"], 3.5)


class ResolveCombatLogAttributionsTests(unittest.TestCase):
    def test_dealt_hit_falls_back_to_provisional_guess_after_timeout(self):
        w = _FakeWindow()
        w._targeted_pid = "enemy-1"
        w._remote_entities["enemy-1"] = {"username": "Raider Mk2"}
        _stage(w, {"damage": 142.0, "damage_type": "kinetic"},
               "enemy-1", is_my_hit=False, now=1.0,
               me={"active_weapon_name": "Corsair Thumper"})

        _resolve(w, 1.0 + SHIELD_MATCH_TIMEOUT_S + 0.01)

        self.assertEqual(len(w._combat_log), 1)
        entry = w._combat_log[0]
        self.assertTrue(entry["dealt"])
        self.assertEqual(entry["who"], "Raider Mk2")
        self.assertEqual(entry["weapon"], "Corsair Thumper")
        self.assertEqual(w._combat_log_totals["dealt"], 142.0)
        self.assertEqual(w._combat_log_totals["dealt_hits"], 1)
        self.assertEqual(w._combat_log_pending, {})

    def test_taken_hit_falls_back_to_an_enemy_after_timeout(self):
        w = _FakeWindow()
        _stage(w, {"damage": 88.0, "damage_type": "thermal"},
               "me-pid", is_my_hit=True, now=1.0)

        _resolve(w, 1.0 + SHIELD_MATCH_TIMEOUT_S + 0.01)

        entry = w._combat_log[0]
        self.assertFalse(entry["dealt"])
        self.assertEqual(entry["who"], "an enemy")
        self.assertEqual(entry["weapon"], "Unknown")
        self.assertEqual(w._combat_log_totals["taken"], 88.0)

    def test_dealt_hit_confirmed_by_our_own_projectile_uses_real_weapon(self):
        w = _FakeWindow()
        w._targeted_pid = "enemy-1"
        w._remote_entities["enemy-1"] = {"username": "Raider Mk2"}
        _stage(w, {"damage": 50.0}, "enemy-1", is_my_hit=False, now=1.0,
               me={"active_weapon_name": "Fallback Name"})
        _track_owning_projectile(
            w, "enemy-1", 1.0, owner_id="me-pid", weapon="Corsair Thumper")

        _resolve(w, 1.0 + SHIELD_MATCH_GRACE_S + 0.01)

        entry = w._combat_log[0]
        self.assertTrue(entry["dealt"])
        self.assertEqual(entry["weapon"], "Corsair Thumper")

    def test_raw_snake_case_weapon_key_is_prettified_for_display(self):
        w = _FakeWindow()
        w._targeted_pid = "enemy-1"
        _stage(w, {"damage": 50.0}, "enemy-1", is_my_hit=False, now=1.0)
        _track_owning_projectile(
            w, "enemy-1", 1.0, owner_id="me-pid", weapon="quasar_cannon")

        _resolve(w, 1.0 + SHIELD_MATCH_GRACE_S + 0.01)

        self.assertEqual(w._combat_log[0]["weapon"], "Quasar Cannon")

    def test_target_name_is_captured_at_stage_time_not_at_resolve_time(self):
        # The actual live report: "some of the target names are
        # displaying a bit weird" -- raw ids like "8078b481" on OUT rows.
        # Root cause: a strong hit is often the killing blow, so by the
        # time attribution resolved (a few frames later), the target had
        # already been removed from self._remote_entities on death and
        # the name lookup failed. Resolving the name immediately at STAGE
        # time (it doesn't depend on the async owner lookup at all) means
        # the target dying in between can't affect it.
        w = _FakeWindow()
        w._targeted_pid = "enemy-1"
        w._remote_entities["enemy-1"] = {"username": "Ravager"}
        _stage(w, {"damage": 7409.0}, "enemy-1", is_my_hit=False, now=1.0)
        # The killing blow: the target is gone from tracking before
        # resolution ever runs.
        del w._remote_entities["enemy-1"]

        _resolve(w, 1.0 + SHIELD_MATCH_TIMEOUT_S + 0.01)

        self.assertEqual(w._combat_log[0]["who"], "Ravager")

    def test_alt_account_drone_hit_on_our_target_is_excluded(self):
        # The actual bug report this fixes: "the drone damage is all
        # getting logged under my main account even if they were
        # placed on a different account" -- a hit landing on OUR current
        # target, but really fired by a drone owned by a DIFFERENT
        # account, must not be counted as ours once the real owner is
        # known.
        w = _FakeWindow()
        w._targeted_pid = "enemy-1"
        _stage(w, {"damage": 999.0}, "enemy-1", is_my_hit=False, now=1.0)
        _track_owning_projectile(
            w, "enemy-1", 1.0, owner_id="alt-account-pid")

        _resolve(w, 1.0 + SHIELD_MATCH_GRACE_S + 0.01)

        self.assertEqual(w._combat_log, [])
        self.assertEqual(w._combat_log_totals["dealt"], 0.0)
        self.assertEqual(w._combat_log_totals["dealt_hits"], 0)
        self.assertEqual(w._combat_log_pending, {})

    def test_own_drone_hit_on_a_different_marked_enemy_still_counts(self):
        # Not just exclusion -- a confirmed real owner match also WIDENS
        # correct attribution beyond "only our current target": here the
        # hit isn't even on our current target, but real-owner resolution
        # still recognizes it as ours. (provisional_dealt would have been
        # False and this hit would never have queued at all if it weren't
        # ALSO required to at least match our current target to be worth
        # queuing in the first place -- this case specifically confirms
        # that a queued, provisionally-true hit is trusted once confirmed
        # by a REAL owner match, not just left at the provisional guess.)
        w = _FakeWindow()
        w._targeted_pid = "enemy-1"    # what we're queuing against
        w._remote_entities["enemy-1"] = {"username": "Raider Mk2"}
        _stage(w, {"damage": 30.0}, "enemy-1", is_my_hit=False, now=1.0)
        _track_owning_projectile(w, "enemy-1", 1.0, owner_id="me-pid")

        _resolve(w, 1.0 + SHIELD_MATCH_GRACE_S + 0.01)

        entry = w._combat_log[0]
        self.assertTrue(entry["dealt"])

    def test_taken_hit_resolves_real_npc_attacker_name(self):
        # The other bug report this fixes: "the damage received is just
        # logged as 'an enemy' rather than what it was."
        w = _FakeWindow()
        w._npc_entities["npc-7"] = {"display_name": "Valkor Targeting Drone"}
        _stage(w, {"damage": 65.0}, "me-pid", is_my_hit=True, now=1.0)
        _track_owning_projectile(w, "me-pid", 1.0, owner_id="npc-7",
                                 weapon="Ion Cannon")

        _resolve(w, 1.0 + SHIELD_MATCH_GRACE_S + 0.01)

        entry = w._combat_log[0]
        self.assertFalse(entry["dealt"])
        self.assertEqual(entry["who"], "Valkor Targeting Drone")
        self.assertEqual(entry["weapon"], "Ion Cannon")

    def test_taken_hit_resolves_real_player_attacker_name(self):
        w = _FakeWindow()
        w._remote_entities["player:5"] = {"username": "Wolf-3"}
        _stage(w, {"damage": 20.0}, "me-pid", is_my_hit=True, now=1.0)
        _track_owning_projectile(w, "me-pid", 1.0, owner_id="player:5")

        _resolve(w, 1.0 + SHIELD_MATCH_GRACE_S + 0.01)

        self.assertEqual(w._combat_log[0]["who"], "Wolf-3")

    def test_before_grace_elapses_nothing_is_finalized(self):
        w = _FakeWindow()
        w._targeted_pid = "enemy-1"
        _stage(w, {"damage": 10.0}, "enemy-1", is_my_hit=False, now=1.0)
        _track_owning_projectile(w, "enemy-1", 1.0, owner_id="me-pid")

        _resolve(w, 1.0 + SHIELD_MATCH_GRACE_S / 2)

        self.assertEqual(w._combat_log, [])
        self.assertEqual(len(w._combat_log_pending), 1)

    def test_totals_accumulate_across_multiple_resolved_hits(self):
        w = _FakeWindow()
        w._targeted_pid = "enemy-1"
        _stage(w, {"damage": 10.0}, "enemy-1", is_my_hit=False, now=1.0)
        _stage(w, {"damage": 20.0}, "enemy-1", is_my_hit=False, now=1.0)
        _stage(w, {"damage": 5.0}, "me-pid", is_my_hit=True, now=1.0)

        _resolve(w, 1.0 + SHIELD_MATCH_TIMEOUT_S + 0.01)

        self.assertEqual(w._combat_log_totals["dealt"], 30.0)
        self.assertEqual(w._combat_log_totals["dealt_hits"], 2)
        self.assertEqual(w._combat_log_totals["taken"], 5.0)
        self.assertEqual(w._combat_log_totals["taken_hits"], 1)

    def test_history_is_trimmed_to_the_max_on_finalize(self):
        # Staged and resolved in batches well under ShieldEffectState's own
        # _MAX_PENDING_HITS cap on its attribution queue (128), matching
        # how this actually plays out live: resolve_attributions() drains
        # the queue every rendered frame, so it never remotely approaches
        # that cap in practice. A single un-drained burst past 128 is not
        # a realistic scenario this test needs to cover.
        from combat_log import _COMBAT_LOG_MAX_LINES
        w = _FakeWindow()
        w._targeted_pid = "enemy-1"
        batch = 50
        total = _COMBAT_LOG_MAX_LINES + 5
        t = 0.0
        for _ in range(0, total, batch):
            for _ in range(batch):
                _stage(w, {"damage": 1.0}, "enemy-1", is_my_hit=False,
                       now=t)
                t += 1.0
            _resolve(w, t + SHIELD_MATCH_TIMEOUT_S + 0.01)
        self.assertEqual(len(w._combat_log), _COMBAT_LOG_MAX_LINES)
        self.assertEqual(w._combat_log_pending, {})


class CombatLogActorNameTests(unittest.TestCase):
    def test_npc_display_name(self):
        w = _FakeWindow()
        w._npc_entities["npc-3"] = {"display_name": "Hull Breaker"}
        self.assertEqual(
            SolarSystemWindow._combat_log_actor_name(w, "npc-3"),
            "Hull Breaker")

    def test_npc_missing_display_name_falls_back_to_unknown(self):
        w = _FakeWindow()
        w._npc_entities["npc-3"] = {}
        self.assertEqual(
            SolarSystemWindow._combat_log_actor_name(w, "npc-3"), "Unknown")

    def test_remote_entity_username(self):
        w = _FakeWindow()
        w._remote_entities["p-1"] = {"username": "Wolf-3"}
        self.assertEqual(
            SolarSystemWindow._combat_log_actor_name(w, "p-1"), "Wolf-3")

    def test_untracked_id_falls_back_to_an_enemy_not_a_raw_id(self):
        # A live report: even the "npc-"-prefixed second attempt still
        # left raw ids ("-119200"-shaped values) showing in the visible
        # UI after a relaunch. Whatever the real id shape turns out to
        # be, an unresolvable one must never leak into the table again --
        # falls back to the same clean text the fully-unknown-attacker
        # case already used.
        w = _FakeWindow()
        self.assertEqual(
            SolarSystemWindow._combat_log_actor_name(w, 12345), "an enemy")

    def test_none_id_falls_back_to_unknown(self):
        w = _FakeWindow()
        self.assertEqual(
            SolarSystemWindow._combat_log_actor_name(w, None), "Unknown")

    def test_raw_numeric_id_falls_back_to_npc_prefixed_lookup(self):
        # A live report: a resolved attacker owner_id ("-119200") showed
        # up raw instead of a name -- a projectile's owner_id apparently
        # isn't always shaped the same "npc-<id>" way a hit's target_id
        # is. Best-effort second lookup, not a guaranteed fix (this
        # client can't independently confirm the server always does
        # this), but free when it's wrong and correct when it's right.
        w = _FakeWindow()
        w._npc_entities["npc--119200"] = {
            "display_name": "Valkor Targeting Drone"}
        self.assertEqual(
            SolarSystemWindow._combat_log_actor_name(w, -119200),
            "Valkor Targeting Drone")

    def test_npc_prefixed_lookup_is_not_tried_twice(self):
        # An id that's already "npc-"-shaped and genuinely unresolvable
        # must not be re-tried as "npc-npc-<id>" -- still falls back to
        # the same clean text, not a raw id.
        w = _FakeWindow()
        self.assertEqual(
            SolarSystemWindow._combat_log_actor_name(w, "npc-999"),
            "an enemy")


class ResetCombatLogTests(unittest.TestCase):
    def test_reset_clears_history_totals_and_clock(self):
        w = _FakeWindow()
        w._targeted_pid = "enemy-1"
        _stage(w, {"damage": 10.0}, "enemy-1", is_my_hit=False, now=1.0)
        _stage(w, {"damage": 5.0}, "me-pid", is_my_hit=True, now=1.0)
        _resolve(w, 1.0 + SHIELD_MATCH_TIMEOUT_S + 0.01)
        self.assertNotEqual(w._combat_log, [])
        self.assertIsNotNone(w._combat_log_session_started_at)

        SolarSystemWindow._reset_combat_log(w)

        self.assertEqual(w._combat_log, [])
        self.assertEqual(
            w._combat_log_totals,
            {"dealt": 0.0, "taken": 0.0, "dealt_hits": 0, "taken_hits": 0})
        self.assertIsNone(w._combat_log_session_started_at)
        self.assertEqual(w._combat_log_scroll, 0)

    def test_reset_does_not_touch_hitsplats(self):
        w = _FakeWindow()
        w._targeted_pid = "enemy-1"
        _stage(w, {"damage": 10.0}, "enemy-1", is_my_hit=False)
        self.assertNotEqual(w._hitsplats, [])
        SolarSystemWindow._reset_combat_log(w)
        self.assertNotEqual(w._hitsplats, [])


if __name__ == "__main__":
    unittest.main()

"""shield_attribution.py - who fired the shot that produced a hit: the Combat Log's shooter lookup on the shield-effects state.

The game's shield_effects.py matches hits to the projectiles and beams it tracks to draw shield pulses. The Combat Log
wants a different answer from the same evidence: the OWNER of the shot, resolved on its own schedule once per frame. The
two small dataclasses below and the two methods of ``StateMethods`` (``queue_attribution`` / ``resolve_attributions``)
are written exactly as they were in shield_effects.py; ``install`` puts the dataclasses into that module (so
``shield_effects.ResolvedAttribution`` keeps working), copies in the game's own private names the methods use, and
attaches the methods to ``ShieldEffectState`` from a one-line hook at the very end of that file. The state the methods
use (``_pending_attributions`` and its reset) is a few lines in the patch of shield_effects.py.
"""

import sys
from collections import deque
from dataclasses import dataclass
from typing import Hashable

import modloader

@dataclass(frozen=True, slots=True)
class _PendingAttribution:
    """One queued 'who actually fired the shot that produced this hit'
    lookup -- see queue_attribution's own docstring for why this exists
    as a second queue alongside _PendingHit rather than reusing it."""
    sequence: int
    target_id: Hashable
    born_at: float
    key: object


@dataclass(frozen=True, slots=True)
class ResolvedAttribution:
    """The outcome of one queued attribution. ``owner_id`` is None when
    nothing tracked could explain the hit within SHIELD_MATCH_TIMEOUT_S --
    an explicit "couldn't determine" result, not a silently dropped
    request, so the caller always gets an answer to fall back from.
    ``weapon`` is only ever populated from a matched PROJECTILE (beams
    carry no weapon field in this tracking model) and is "" otherwise."""
    key: object
    owner_id: object | None
    weapon: str
    is_turret: bool


# the game's shield_effects.py private names the methods use; install() copies them in from that module
_BORROWED = ("SHIELD_MATCH_GRACE_S", "SHIELD_MATCH_TIMEOUT_S", "_MAX_HITS_PER_STEP", "_MAX_PENDING_HITS", "_PendingHit",
             "_finite")
SHIELD_MATCH_GRACE_S = SHIELD_MATCH_TIMEOUT_S = _MAX_HITS_PER_STEP = _MAX_PENDING_HITS = _PendingHit = _finite = None


class StateMethods:
    """The attribution lookup on ShieldEffectState."""

    def queue_attribution(self, target_id: Hashable, at: float,
                          key: object) -> None:
        """Queue a best-effort 'who actually fired the shot that produced
        this hit' lookup, resolved later by resolve_attributions() using
        the SAME beam/projectile matching this module already performs
        for the shield-ripple visual (see _match_beam_locked /
        _match_projectile_locked) -- just a separate queue and a separate
        consumer, since a caller wanting the OWNER (the combat log) has
        nothing to do with a screen-space pulse. ``key`` is opaque to this
        module: whatever the caller needs to find its own pending record
        back again once resolved (e.g. a sequence number)."""
        if target_id is None or not _finite(at):
            return
        with self._lock:
            self._attribution_sequence += 1
            self._pending_attributions.append(_PendingAttribution(
                self._attribution_sequence, target_id, float(at), key))

    def resolve_attributions(
            self, now: float) -> tuple[ResolvedAttribution, ...]:
        """Resolve as many queued attributions as are ready.

        Waits out the same SHIELD_MATCH_GRACE_S the shield-pulse matcher
        does -- the projectile/beam packet for a hit that just landed may
        not have been processed yet -- then tries the same matchers.
        Times out after SHIELD_MATCH_TIMEOUT_S with owner_id=None: an
        explicit "couldn't determine" result rather than a silently
        dropped request, so the caller always eventually gets an answer
        to fall back from. Safe to call even if step() hasn't run this
        frame (prunes its own stale evidence first).
        """
        if not _finite(now):
            return ()
        current = float(now)
        resolved: list[ResolvedAttribution] = []
        with self._lock:
            self._prune_locked(current)
            retained: deque[_PendingAttribution] = deque(
                maxlen=_MAX_PENDING_HITS)
            attempted = 0
            for item in self._pending_attributions:
                age = current - item.born_at
                if age < SHIELD_MATCH_GRACE_S or attempted >= _MAX_HITS_PER_STEP:
                    retained.append(item)
                    continue
                if age > SHIELD_MATCH_TIMEOUT_S:
                    resolved.append(
                        ResolvedAttribution(item.key, None, "", False))
                    continue
                target = self._targets.get(item.target_id)
                if target is None or not target.samples:
                    retained.append(item)
                    continue
                attempted += 1
                hit = _PendingHit(0, item.target_id, item.born_at, "")
                match = self._match_beam_locked(hit, target)
                if match is None:
                    match = self._match_projectile_locked(hit, target)
                if match is None:
                    retained.append(item)
                    continue
                if match.beam is not None:
                    resolved.append(ResolvedAttribution(
                        item.key, match.beam.owner_id, "", False))
                    continue
                trace = (self._projectiles.get(match.projectile_id)
                         if match.projectile_id is not None else None)
                if trace is None:
                    retained.append(item)
                    continue
                resolved.append(ResolvedAttribution(
                    item.key, trace.owner_id, trace.weapon, trace.is_turret))
            self._pending_attributions = retained
        return tuple(resolved)


def install(target) -> "list[str]":
    """Give ``ShieldEffectState`` (the class in shield_effects.py) the attribution methods."""
    game_module = sys.modules[target.__module__]
    game_module._PendingAttribution = _PendingAttribution
    game_module.ResolvedAttribution = ResolvedAttribution
    return modloader.install_methods(target, StateMethods, globals(), _BORROWED)


# what test_mods_apply checks after every game update: (game module, game class, the class above)
ATTACHES = (("shield_effects", "ShieldEffectState", "StateMethods"),)

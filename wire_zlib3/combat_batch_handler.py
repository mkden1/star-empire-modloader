"""combat_batch_handler.py - applying one SPACE_COMBAT_BATCH envelope on the protocol side (``ProtocolMixin`` method).

``_on_space_combat_batch`` takes the decoded Phase 8 combat/state envelope (see combat_batch.py for the wire contract
and the decoder) and hands each part to the game's own handlers in order. It is written exactly as it was in
protocol.py; ``install`` attaches it to ``ProtocolMixin`` from a one-line hook at the very end of that file, and the
command table in protocol.py (a one-line patch) points ``SPACE_COMBAT_BATCH`` at it.
"""

import json
import logging
import time

import modloader
from combat_batch import EVENT_COMMANDS, decode_combat_batch

logger = logging.getLogger("combat_batch_handler")


class ProtocolMethods:
    """The SPACE_COMBAT_BATCH handler on ProtocolMixin."""

    def _on_space_combat_batch(self, payload,
                               arrival_time: "float | None" = None):
        """Apply one ordered Phase 8 combat/state envelope.

        The server emits this protocol unconditionally.  Old command handlers
        remain implementation functions, but network combat replication no
        longer expands a tick into one line and one JSON parse per event.
        """
        profiler = self._network_profile()
        phase_t0 = time.perf_counter() if profiler else 0.0
        try:
            batch = decode_combat_batch(payload)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.debug("bad payload for _on_space_combat_batch: %s", exc)
            return
        if profiler:
            phase_now = time.perf_counter()
            profiler.record_external(
                "network_decode_combat_batch", phase_now - phase_t0)
            phase_t0 = phase_now

        # A sequence is diagnostic rather than a replay mechanism. TCP already
        # preserves order; application keyframes repair intentionally dropped
        # backpressured deltas. Arena keys prevent a transition reset from being
        # mistaken for reordering.
        sequences = getattr(self, "_combat_batch_sequences", None)
        if sequences is None:
            sequences = {}
            self._combat_batch_sequences = sequences
        arena = batch["a"]
        sequence = batch["q"]
        previous = sequences.get(arena)
        if sequence and previous is not None and sequence > previous + 1:
            logger.debug(
                "combat batch gap for %s: %d -> %d",
                arena, previous, sequence)
        if sequence:
            sequences[arena] = max(sequence, previous or 0)

        event_handlers = {
            "SPACE_BEAM": self._on_space_beam,
            "SPACE_HIT": self._on_space_hit,
            "SPACE_FIRE_SND": self._on_space_fire_snd,
            "SPACE_ENTITY_JOINED": self._on_space_entity_joined,
            "SPACE_ENTITY_LEFT": self._on_space_entity_left,
            "NPC_SHIP_EXPLODED": self._on_npc_ship_exploded,
            "MISSILE_EXPLODED": self._on_missile_exploded,
            "TURRET_BEAM": self._on_turret_beam,
            "STATION_BEAM": self._on_station_beam,
            "STATION_COMBAT_BATCH": self._on_station_combat_batch,
            "STATION_HIT": self._on_station_hit,
            "STATION_SHIELDS": self._on_station_shields,
            "STATION_DESTROYED": self._on_station_destroyed,
            "DUNGEON_AI_BEAM": self._on_dungeon_ai_beam,
            "DUNGEON_AI_HIT": self._on_dungeon_ai_hit,
            "DUNGEON_AI_HIT_ENTITY": self._on_dungeon_ai_hit_entity,
            "DUNGEON_AI_DIED": self._on_dungeon_ai_died,
            "DUNGEON_AI_JOINED": self._on_dungeon_ai_joined,
            "DUNGEON_PEER_BEAM": self._on_dungeon_peer_beam,
            "FIGHTER_WING_CASUALTY": self._on_fighter_wing_casualty,
            "DUNGEON_SPAWN_GATE_OPEN": self._on_dungeon_spawn_gate_open,
            "DUNGEON_SPAWN_GATE_CLOSED": self._on_dungeon_spawn_gate_closed,
            "DUNGEON_GATE_UNLOCKED": self._on_dungeon_gate_unlocked,
            "DUNGEON_ITEM_BOXES": self._on_dungeon_item_boxes,
            "ASTEROID_HIT": self._on_asteroid_hit,
            "ASTEROID_DESTROYED": self._on_asteroid_destroyed,
        }
        # Event order precedes the authoritative state lanes, exactly matching
        # the former arena emit-before-snapshot order.
        for opcode, event_payload in batch.get("e", ()):
            command = EVENT_COMMANDS[opcode]
            handler = event_handlers[command]
            if command == "SPACE_ENTITY_LEFT":
                event_payload = str(event_payload)
            try:
                handler(event_payload)
            except Exception:
                logger.exception(
                    "combat batch event %s failed", command)

        if "d" in batch:
            self._on_dungeon_frame(batch["d"])
        if "s" in batch:
            self._on_space_state(batch["s"], arrival_time)
        if "r" in batch:
            if self._in_dungeon:
                self._on_dungeon_ai_projectiles(batch["r"])
            else:
                self._on_space_projectiles(batch["r"])
        if batch.get("f"):
            self._on_space_fire_snd(batch["f"])
        if profiler:
            profiler.record_external(
                "network_apply_combat_batch", time.perf_counter() - phase_t0)


def install(target) -> "list[str]":
    """Give ``ProtocolMixin`` (the class in protocol.py) this method."""
    return modloader.install_methods(target, ProtocolMethods)


# what test_mods_apply checks after every game update: (game module, game class, the class above)
ATTACHES = (("protocol", "ProtocolMixin", "ProtocolMethods"),)

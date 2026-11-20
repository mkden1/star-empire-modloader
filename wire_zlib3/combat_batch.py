"""Decoder constants and validation for ``SPACE_COMBAT_BATCH`` version 1."""

from __future__ import annotations

import json
import math


COMBAT_BATCH_VERSION = 1

# Reverse of Server/combat_batch.py: this is a shipped wire contract.
EVENT_COMMANDS: dict[int, str] = {
    1: "SPACE_BEAM",
    2: "SPACE_HIT",
    3: "SPACE_FIRE_SND",
    4: "SPACE_ENTITY_JOINED",
    5: "SPACE_ENTITY_LEFT",
    6: "NPC_SHIP_EXPLODED",
    7: "MISSILE_EXPLODED",
    8: "TURRET_BEAM",
    9: "STATION_BEAM",
    10: "STATION_COMBAT_BATCH",
    11: "STATION_HIT",
    12: "STATION_SHIELDS",
    13: "STATION_DESTROYED",
    14: "DUNGEON_AI_BEAM",
    15: "DUNGEON_AI_HIT",
    16: "DUNGEON_AI_HIT_ENTITY",
    17: "DUNGEON_AI_DIED",
    18: "DUNGEON_AI_JOINED",
    19: "DUNGEON_PEER_BEAM",
    20: "FIGHTER_WING_CASUALTY",
    21: "DUNGEON_SPAWN_GATE_OPEN",
    22: "DUNGEON_SPAWN_GATE_CLOSED",
    23: "DUNGEON_GATE_UNLOCKED",
    24: "DUNGEON_ITEM_BOXES",
    25: "ASTEROID_HIT",
    26: "ASTEROID_DESTROYED",
}


def decode_combat_batch(payload) -> dict:
    """Decode and validate one Phase 8 envelope without mutating client state."""
    data = json.loads(payload) if isinstance(payload, (str, bytes, bytearray)) else payload
    if not isinstance(data, dict):
        raise ValueError("combat batch must be a JSON object")
    if data.get("v") != COMBAT_BATCH_VERSION:
        raise ValueError(f"unsupported combat batch version: {data.get('v')!r}")
    sequence = data.get("q")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise ValueError("combat batch sequence must be a non-negative integer")
    arena = data.get("a")
    if not isinstance(arena, str) or not arena:
        raise ValueError("combat batch arena must be a non-empty string")
    try:
        tick_time = float(data.get("t"))
    except (TypeError, ValueError) as exc:
        raise ValueError("combat batch tick_time must be numeric") from exc
    if not math.isfinite(tick_time):
        raise ValueError("combat batch tick_time must be finite")

    events = data.get("e", ())
    if not isinstance(events, (list, tuple)):
        raise ValueError("combat batch event lane must be an array")
    for event in events:
        if not isinstance(event, (list, tuple)) or len(event) != 2:
            raise ValueError("combat batch event must be [opcode, payload]")
        opcode = event[0]
        if isinstance(opcode, bool) or not isinstance(opcode, int):
            raise ValueError("combat batch event opcode must be an integer")
        if opcode not in EVENT_COMMANDS:
            raise ValueError(f"unsupported combat batch event opcode: {opcode}")
    return data


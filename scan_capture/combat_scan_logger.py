"""combat_scan_logger.py — persist raw item/ship-stat payloads for offline DPS/sustain analysis.

The client never computes weapon/plugin/ship-stat numbers itself -- every value (base damage,
effective damage, fire rate, energy cost, shield bank/regen, plugin bonuses) is server-
authoritative and only ever arrives client-side as already-built display data: an item's own
``stats`` dict (built server-side by ``build_item_stats()``, which isn't shipped to the client --
see render_mixin.py's item-popup renderer), SHIP_SPECS_DATA's ``weapons``/``plugins`` arrays,
TURRET_STATE's per-hardpoint snapshot, and SHIP_PLUGIN_DATA's plugin-slot state.

This module hooks each of those and persists the payload close to verbatim -- deliberately RAW,
not filtered down to specific fields, since at this exploratory stage we don't yet know exactly
what each source's ``stats`` dict contains, and trimming fields now would throw away exactly the
thing a later formula-calibration tool (see the planned combat_tool) needs to discover. One JSON
object per line (JSONL) in scan_data/combat_scan.jsonl, upserted in place keyed by (source, key)
for the two high-frequency, well-understood sources (an item's own inventory stats; a SHIP_SPECS
snapshot for one distinct loadout) so repeat sightings -- SHIP_SPECS_DATA polls once/sec while its
tab is open -- update the existing row instead of growing the file unboundedly. The other two
sources (TURRET_STATE, SHIP_PLUGIN_DATA) have an unconfirmed exact shape -- no test fixture or
client-side consumer inspects their fields closely enough to derive a reliable dedup key yet -- so
those are appended instead, accepting some duplication in exchange for never guessing away a real
observation.

**A real player report closed a real gap in that reasoning**: "push events for these are
infrequent" turned out to be false in practice -- confirmed from a real ~34 MB, 4046-row capture
file with 473 TURRET_STATE rows alone, many just 1-5 seconds apart (the Turret Layout screen, like
Ship Specs, pushes on its own cadence while open, not just on "ship entry + real equip changes").
Worse, EVERY SHIP_SPECS_DATA poll -- once a second while that tab is open -- was unconditionally
paying for `_upsert()`'s full read-parse-rewrite of the ENTIRE file, even when nothing had changed
except the reactor's own naturally-ticking ``current_energy`` reading: at 34 MB that's a real,
reported cause of client lag just from opening the Stats page. Every recording function below now
skips its own write entirely when the new payload is identical to the last one THIS PROCESS has
already recorded for the same identity (see ``_unchanged()``'s own docstring) -- a real, distinct
observation is still captured immediately and exactly as before; only genuinely repeat,
nothing-changed pushes are what's now skipped. This keeps the original "never guess away a real
observation" promise (an append-only source still isn't guessing at a dedup KEY, just skipping an
exact repeat) while no longer paying disk-I/O cost for data a UI poll re-sends unchanged.

A fifth source, ``energy_sample``, is the deliberate EXCEPTION to "upsert the high-frequency
ones": every SHIP_SPECS_DATA push also gets its own current_energy reading appended raw (see
record_ship_specs/_record_energy_sample) precisely BECAUSE it's a 1/sec poll -- the ship_specs
upsert above only keeps the latest snapshot per loadout combo, but a run of these live readings
is what actually lets a later tool measure REAL energy drain while firing, instead of trusting a
weapon's own displayed "Energy Cost" stat. This one genuinely does grow unbounded while the Ship
Specs tab stays open (by design -- it's meant for short, deliberate capture sessions, not
always-on background telemetry).

Each record_* function is a best-effort, ``self._solar_window``-adjacent hook: it must never raise,
since the UI paths that receive this data still have to work regardless of whether logging it to
disk succeeds -- same contract as scan_logger.record_scan / station_id_logger.record_stations.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import queue
import threading
import time

import capture_settings
import scan_paths

logger = logging.getLogger(__name__)


def _capturing(out_path: str | None) -> bool:
    """Is combat capture on? Only the real file is gated (see capture_settings.py): a call with
    its own ``out_path`` -- a test -- always records. Checked BEFORE the _unchanged() dedup so
    that switching capture back on records the current state instead of treating it as a repeat."""
    return out_path is not None or capture_settings.enabled("combat")

# Per-process, in-memory only -- deliberately NOT persisted or compared against the file's own
# stored row, since reading the file first is exactly the cost _unchanged() exists to let a caller
# avoid. Reset on client restart; the goal is only ever "skip a back-to-back duplicate push while
# a tab stays open in ONE session," not deduplicating across restarts.
_last_seen: dict[tuple[str, object], object] = {}


def _unchanged(cache_key: tuple[str, object], data) -> bool:
    """True if ``data`` is structurally identical to the last payload recorded for ``cache_key``
    in THIS PROCESS -- lets a caller skip an expensive file write (a full upsert rewrite, or even
    a cheap append) entirely when a UI poll delivers the exact same observation it already sent a
    moment ago. This is the fix for a real player report: their game client lagged badly whenever
    they opened their Stats page, traced to record_ship_specs() rewriting the entire (by then
    ~34 MB) combat_scan.jsonl on every single 1-second SHIP_SPECS_DATA poll, regardless of whether
    anything had actually changed since the last one. Updates the cache as a side effect whenever
    ``data`` is new or has changed, so the NEXT call correctly compares against what was just
    recorded."""
    if cache_key in _last_seen and _last_seen[cache_key] == data:
        return True
    _last_seen[cache_key] = data
    return False


# Confirmed this session, from a real ~680-row capture (mostly a player station's Trade Manager
# catalogue, which lists nearly every item type in the game regardless of relevance): these
# categories are never going to carry weapon/plugin/ship-gear stats, just clutter for what this
# tool actually cares about. Deliberately a DENYLIST, not an allowlist -- an unrecognized category
# still gets logged (visible for review) rather than silently dropped, so a real gear category we
# haven't seen yet is never lost to an incomplete allowlist.
_NON_COMBAT_CATEGORIES = frozenset({
    "blueprint",         # station construction schematics, e.g. "Alpha Station Design Blueprint"
    "station",           # attached/deployable station kits, e.g. "Attached Station Kit III"
    "station_plugin",    # station-level plugins (a station's own bonuses, not a ship's)
    "resource",          # raw trade goods, e.g. "Colonists"
    "scanner",           # planet scanners, e.g. "Planet Scanner Mk I"
    "token",             # skill/class tokens, e.g. "Class Adept Token"
    "cargo_summary",     # client-side UI summary rows, not real items
    "scoop_cargo_summary",
    "fabricator",        # production buildings
    "lootbox",           # loot-crate summaries (drop odds, not the gear itself)
})


def _is_combat_relevant(item: dict) -> bool:
    category = str(item.get("category") or item.get("item_category") or "")
    return category not in _NON_COMBAT_CATEGORIES


def _default_out_path() -> str:
    # Pure string work (scan_paths): callers run on the message thread, which must never wait on
    # the disk. The folder is created by the writer thread, in _write_rows_to_disk/_append_now.
    return scan_paths.path("combat_scan.jsonl")


# Per-process cache of a file's already-parsed (keyed_rows, passthrough_rows), keyed by path.
# Deliberately the same "trust it for the life of this process, reset on restart" contract
# _last_seen above already uses -- NOT revalidated against the file's own mtime/size, because once
# a write can be handed off to the background worker below, this process's own next write may
# still be in flight when the next read comes in, so "does the file on disk match what we expect"
# can no longer answer "is our in-memory copy current" reliably. Nothing else should be writing
# combat_scan.jsonl while the client is running (it's this client's own private capture file), so
# trusting our own writes as the sole source of cache invalidation is a safe trade, matching how
# _last_seen already accepts "reset on restart" rather than re-checking disk on every call.
#
# Confirmed via /profiler: record_inventory_items has no _unchanged() guard (see its own
# docstring -- unlike record_ship_specs/turret_state/ship_plugin_data/mastery_tree, it decides its
# dedup per-item only AFTER reading), so it pays _read_existing_rows' full read-and-json.loads-
# every-line cost on EVERY SHIP_INVENTORY_DATA push, unconditionally. At today's real ~22 MB /
# 3600-row file that alone measured ~500ms, EVERY single call (handler:SHIP_INVENTORY_DATA,
# Min/call 478ms, Max/call 584ms across 21 real calls) -- large enough, on the App thread that
# also drains the network queue, to genuinely freeze the client. Caching the parse means only the
# very first read in a process pays that cost; every read after (including after a write this same
# module performed) reuses the exact objects already in memory, so no real observation is missed.
_rows_cache: dict[str, tuple[dict, list]] = {}


def _read_existing_rows(path: str) -> tuple[dict[tuple[str, str], dict], list[dict]]:
    """Splits the file into ``(keyed_rows, passthrough_rows)``. ``keyed_rows`` is every row with
    a real (non-None) key -- the ones an upsert can safely look up and replace in place, one per
    (source, key). ``passthrough_rows`` is every APPEND-ONLY row (key is None: turret_state,
    ship_plugin_data, mastery_tree, energy_sample), kept verbatim in original file order.

    A real bug found this session: this used to key EVERY row (including append-only ones) into
    one dict by (source, key) -- since every append-only row shares the exact same key (None),
    they all silently collided on one dict slot, and the NEXT upsert to run against the same file
    (record_ship_specs/record_inventory_items/record_skills_data all round-trip through this)
    would rewrite the file from that dict, permanently destroying every prior append-only
    observation but the very last one. Multiple rows sharing (source, None) are genuinely
    DISTINCT real observations, not repeat sightings of the same thing to overwrite -- they must
    never be collapsed, so they're tracked and passed straight back through instead."""
    cached = _rows_cache.get(path)
    if cached is not None:
        return cached
    if not os.path.exists(path):
        empty: tuple[dict, list] = ({}, [])
        _rows_cache[path] = empty
        return empty
    keyed: dict[tuple[str, str], dict] = {}
    passthrough: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(row, dict):
                continue
            if row.get("key") is None:
                passthrough.append(row)
            else:
                keyed[(row.get("source", ""), row.get("key", ""))] = row
    _rows_cache[path] = (keyed, passthrough)
    return keyed, passthrough


def _write_rows_to_disk(path: str, keyed_rows: dict[tuple[str, str], dict],
                         passthrough_rows: list[dict]) -> None:
    """The actual file-write -- extracted so both the synchronous and background-worker paths
    below share exactly one implementation."""
    scan_paths.ensure_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        for row in passthrough_rows:
            f.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        for sort_key in sorted(keyed_rows):
            f.write(json.dumps(keyed_rows[sort_key], sort_keys=True, default=str) + "\n")


# ── The writer thread ────────────────────────────────────────────────────────────────────────
# In production (out_path=None) EVERYTHING that touches combat_scan.jsonl -- the first read that
# fills _rows_cache, every upsert, every append, every full rewrite -- runs on this one thread,
# in the order it was requested; the message thread only does the in-memory dedup check and
# enqueues a point-in-time copy of the payload. Why all of it and not just the rewrite (which is
# what this used to hand over): the freeze watchdog showed the message thread blocked inside this
# file's filesystem calls (the folder check, the per-push append, and the one-time ~13 MB cache
# read that the profiler had already caught as a 2.26 s SHIP_INVENTORY_DATA call), for 1-3 s at a
# time whenever the shared drive hiccuped. One owner thread also means _rows_cache is only ever
# touched from one place, so appends and rewrites can never race each other.
#
# A test (explicit out_path) runs the same functions inline instead, so "the file is fully
# written when record_*() returns" holds there exactly as before.
_write_queue: "queue.Queue | None" = None
_write_thread: "threading.Thread | None" = None
_write_thread_lock = threading.Lock()


def _write_worker_loop() -> None:
    while True:
        fn, args = _write_queue.get()
        try:
            fn(*args)
        except Exception:
            logger.exception("combat_scan_logger: background write failed (%s)",
                             getattr(fn, "__name__", fn))
        finally:
            _write_queue.task_done()


def _ensure_write_worker() -> None:
    global _write_queue, _write_thread
    with _write_thread_lock:
        if _write_queue is None:
            _write_queue = queue.Queue()
        if _write_thread is None or not _write_thread.is_alive():
            _write_thread = threading.Thread(
                target=_write_worker_loop, name="combat-scan-writer", daemon=True)
            _write_thread.start()


def flush() -> None:
    """Block until every write queued so far has been processed (tests; a no-op before the
    writer has ever been started)."""
    if _write_queue is not None:
        _write_queue.join()


def _dispatch(out_path: str | None, fn, *args) -> None:
    """Production (``out_path`` is None): queue ``fn(*args)`` for the writer thread -- never
    blocks, never raises. Test (explicit ``out_path``): run it right here."""
    if out_path is None:
        _ensure_write_worker()
        _write_queue.put((fn, args))
    else:
        fn(*args)


def _snapshot(out_path: str | None, data):
    """A private copy of ``data`` for the writer thread -- the caller's dict is the live decoded
    payload and the UI may mutate it later. Inline (test) runs finish before returning, so they
    can use the object as is."""
    return data if out_path is not None else copy.deepcopy(data)


def _write_rows(path: str, keyed_rows: dict[tuple[str, str], dict],
                 passthrough_rows: list[dict]) -> None:
    """Persist ``keyed_rows``/``passthrough_rows`` to ``path``, then keep the in-memory cache in
    lock-step with them so the next _read_existing_rows() call (even from a different record_*
    function sharing this same file) reuses these exact objects instead of re-reading anything."""
    _write_rows_to_disk(path, keyed_rows, passthrough_rows)
    _rows_cache[path] = (keyed_rows, passthrough_rows)


def _upsert(source: str, key: str, data, out_path: str | None = None) -> None:
    _dispatch(out_path, _upsert_now, source, key, _snapshot(out_path, data), time.time(),
              out_path)


def _upsert_now(source: str, key: str, data, captured_at: float, out_path: str | None) -> None:
    path = out_path or _default_out_path()
    keyed_rows, passthrough_rows = _read_existing_rows(path)
    keyed_rows[(source, key)] = {
        "source": source,
        "key": key,
        "captured_at": captured_at,
        "data": data,
    }
    _write_rows(path, keyed_rows, passthrough_rows)


def _append(source: str, data, out_path: str | None = None) -> None:
    # captured_at is taken HERE, when the observation was made, not when the writer gets to it.
    _dispatch(out_path, _append_now, source, _snapshot(out_path, data), time.time(), out_path)


def _append_now(source: str, data, captured_at: float, out_path: str | None) -> None:
    path = out_path or _default_out_path()
    scan_paths.ensure_dir(path)
    line = json.dumps({
        "source": source,
        "key": None,
        "captured_at": captured_at,
        "data": data,
    }, sort_keys=True, default=str)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    # Keep the parsed copy of the file in step. A full rewrite writes _rows_cache's passthrough
    # rows back out, so a row appended behind the cache's back was silently deleted by the next
    # upsert in the same session (a turret_state / energy_sample row survived only until the next
    # inventory, skills or ship-specs change).
    cached = _rows_cache.get(path)
    if cached is not None:
        cached[1].append(json.loads(line))


def record_inventory_items(items: list, out_path: str | None = None) -> None:
    """One row per distinct item_type, capturing whatever ``stats``/``description`` the server
    sent for it. Owning an item is enough to capture it; it never needs to be equipped -- this is
    the richest source for an item's OWN declared numbers, independent of whatever ship/skill/
    plugin context it might later be combined with.

    Feeds from several different messages that all carry item-shaped payloads (your own
    SHIP_INVENTORY_DATA, an NPC or player station's current trade_inventory, a player station's
    Trade Manager catalogue) -- but NOT all of them are equally rich. Confirmed this session: a
    Trade Manager catalogue entry carries only identity/category/tech/price, no ``stats`` at all,
    while a real trade listing or your own inventory carries the full stats dict. Since every
    source shares the same item_type keyspace, a later, leaner capture (e.g. opening the Trade
    Manager after already having a weapon's full stats from your own cargo hold) would otherwise
    silently overwrite the richer one -- so a capture that carries no ``stats`` is never allowed
    to replace an existing row that has some, even though it's still recorded fresh when it's the
    only thing we have for that item_type at all.

    Also skips anything in _NON_COMBAT_CATEGORIES (blueprints, station kits, resources, and the
    like) -- confirmed this session, opening a station's Trade Manager catalogue floods this with
    hundreds of rows for every item type in the game, the large majority of them irrelevant to
    weapon/plugin/gear analysis.

    An item whose data is byte-identical to what's already stored for that item_type is skipped
    entirely -- not just cosmetically cheaper, it's what keeps re-seeing the SAME cargo hold or
    catalogue (e.g. just glancing at your inventory again) from triggering a real file rewrite for
    no reason (see this module's own docstring for why that matters).

    In production only the cheap in-memory filter (combat-relevant dicts) runs on the calling
    thread; the file work is queued for the writer thread (see _dispatch)."""
    try:
        if not _capturing(out_path):
            return
        relevant = [i for i in (items or []) if isinstance(i, dict) and _is_combat_relevant(i)]
        if relevant:
            _dispatch(out_path, _record_inventory_items_now,
                      _snapshot(out_path, relevant), time.time(), out_path)
    except Exception:
        logger.exception("combat_scan_logger: failed to record inventory items")


def _record_inventory_items_now(items: list, captured_at: float, out_path: str | None) -> None:
    try:
        path = out_path or _default_out_path()
        keyed_rows, passthrough_rows = _read_existing_rows(path)
        changed = False
        for item in items or []:
            if not isinstance(item, dict):
                continue
            if not _is_combat_relevant(item):
                continue
            item_type = str(item.get("item_type") or item.get("cargo_key") or "")
            if not item_type:
                continue
            existing = keyed_rows.get(("inventory_item", item_type))
            if existing and existing.get("data", {}).get("stats") and not item.get("stats"):
                continue
            if existing and existing.get("data") == item:
                continue
            keyed_rows[("inventory_item", item_type)] = {
                "source": "inventory_item",
                "key": item_type,
                "captured_at": captured_at,
                "data": item,
            }
            changed = True
        if changed:
            _write_rows(path, keyed_rows, passthrough_rows)
    except Exception:
        logger.exception("combat_scan_logger: failed to record inventory items")


def _ship_specs_identity(data: dict) -> dict:
    """``data`` with ``energy.current_energy`` stripped out -- used ONLY as the comparison value
    for _unchanged(), never written anywhere. That one field legitimately changes on almost every
    single poll (a reactor draining while firing, or regenerating between fights) even when
    absolutely nothing else about the loadout has -- comparing the raw payload verbatim would
    defeat the entire point of deduping record_ship_specs()'s own upsert, since it would report
    "changed" on nearly every call regardless. A shallow copy (plus a shallow copy of the
    ``energy`` sub-dict) is enough here -- nothing else in ``data`` is mutated or needs deep
    copying just to exclude one key from the comparison."""
    energy = data.get("energy")
    if not isinstance(energy, dict) or "current_energy" not in energy:
        return data
    return {**data, "energy": {k: v for k, v in energy.items() if k != "current_energy"}}


def record_ship_specs(data: dict, out_path: str | None = None) -> None:
    """SHIP_SPECS_DATA -- one row per distinct (hull, equipped weapons, equipped plugins) combo
    actually observed, capturing the full effective-stats snapshot: weapons' base vs buffed
    damage/fire_rate/dps/range/energy_cost, shield bank/regen, energy bank/regen, resistances.
    Keying on the combo (not just the hull) means trying a different plugin loadout on the same
    ship produces its OWN row rather than overwriting the previous one -- exactly the set of
    distinct observations a later calibration tool needs to compare against each other.

    Also appends a lightweight energy sample every call (see _record_energy_sample) -- the
    upsert above only ever keeps the LATEST snapshot per combo, but the Ship Specs tab in the
    Stats panel polls this once a second while open (see protocol.py's own _on_ship_specs_data
    docstring), meaning ``data["energy"]["current_energy"]`` is a genuinely live reading that
    ticks down in real time while a weapon is actually firing. Discarding every reading but the
    last threw away exactly the time series a later tool needs to measure REAL observed energy
    drain during combat, rather than trusting a weapon's own displayed "Energy Cost" stat.

    The upsert itself, though, is skipped whenever this poll's own payload is identical to the
    last one seen for this exact combo EXCEPT for ``current_energy`` (see _unchanged()'s own
    docstring for why this exists at all: a 1-second poll ticking that one live reading used to
    force a full rewrite of the entire scan file every single second the Stats page stayed open,
    a real, reported cause of client lag). The energy sample below is still appended every call
    regardless -- that ticking reading is exactly what it exists to capture."""
    try:
        if not _capturing(out_path):
            return
        if not isinstance(data, dict) or data.get("error"):
            return
        ship = data.get("ship") or {}
        weapons = data.get("weapons") or []
        plugins = data.get("plugins") or []
        hull = str(ship.get("display_name", ""))
        weapon_key = ",".join(sorted(str(w.get("weapon_type", "")) for w in weapons
                                      if isinstance(w, dict)))
        plugin_key = ",".join(sorted(
            str(p.get("plugin_id") or p.get("display_name") or "")
            for p in plugins if isinstance(p, dict)))
        key = f"{hull}|{weapon_key}|{plugin_key}"
        if not _unchanged(("ship_specs", key), _ship_specs_identity(data)):
            _upsert("ship_specs", key, data, out_path)
        _record_energy_sample(data, key, out_path)
    except Exception:
        logger.exception("combat_scan_logger: failed to record ship specs")


def _record_energy_sample(data: dict, combo_key: str, out_path: str | None = None) -> None:
    """Appends one (timestamp, current_energy) point per SHIP_SPECS_DATA push -- deliberately
    APPEND-only, unlike record_ship_specs' own upsert, since the whole point here is keeping
    every single reading rather than just the latest one. ``combo_key`` (the same string
    record_ship_specs keys its own snapshot row on) ties each sample back to exactly which
    reactor/weapons/plugins were equipped when it was taken, without needing to duplicate the
    full snapshot payload into every row. Silently does nothing if this push carried no energy
    block at all (an error payload, or a hull with no reactor fitted) -- there's nothing useful
    to sample in that case, not a failure.

    A caller wanting REAL observed drain (rather than a weapon's own displayed "Energy Cost")
    reads a run of these for one combo_key while the player was actually firing continuously --
    consecutive current_energy readings a real ~1 second apart, so the average slope between two
    far-enough-apart samples is the true net drain (weapon cost minus regen) for that exact
    loadout, no formula guesswork involved."""
    energy = data.get("energy")
    if not isinstance(energy, dict):
        return
    current = energy.get("current_energy")
    if current is None:
        return
    _append("energy_sample", {
        "combo_key": combo_key,
        "current_energy": current,
        "max_energy": energy.get("max_energy"),
        "energy_output": energy.get("energy_output"),
    }, out_path)


def record_turret_state(data: dict, out_path: str | None = None) -> None:
    """TURRET_STATE -- appended rather than upserted: the exact hardpoint/upgrade shape isn't
    confirmed against a real payload yet, so this deliberately doesn't guess at a dedup KEY that
    might silently collide two genuinely different observations.

    That's a different question from whether two CONSECUTIVE pushes are the exact same
    observation repeated, though -- confirmed false to assume "infrequent" here (see this
    module's own docstring): a real ~34 MB capture file had 473 of these, many just 1-5 seconds
    apart, the large majority just the Turret Layout screen re-pushing an unchanged rack while it
    stays open. A byte-identical repeat of the last payload THIS PROCESS saw is skipped entirely
    (see _unchanged()) -- a real CHANGE (a different weapon, a controller added/removed) is still
    appended immediately and unconditionally, exactly as before."""
    try:
        if not _capturing(out_path):
            return
        if not isinstance(data, dict):
            return
        if _unchanged(("turret_state", None), data):
            return
        _append("turret_state", data, out_path)
    except Exception:
        logger.exception("combat_scan_logger: failed to record turret state")


def record_ship_plugin_data(data: dict, out_path: str | None = None) -> None:
    """SHIP_PLUGIN_DATA -- same best-effort append-only treatment as record_turret_state, for
    the same reason (shape not yet confirmed against a real payload) -- and the same "skip an
    exact repeat, never a real change" dedup (see _unchanged())."""
    try:
        if not _capturing(out_path):
            return
        if not isinstance(data, dict):
            return
        if _unchanged(("ship_plugin_data", None), data):
            return
        _append("ship_plugin_data", data, out_path)
    except Exception:
        logger.exception("combat_scan_logger: failed to record ship plugin data")


def record_skills_data(skills: list, mastery_tree: list | None = None,
                        out_path: str | None = None) -> None:
    """SKILLS_DATA -- a player's trained skills, plus the optional Way-of-the class mastery tree.

    Unlike an item's ``stats`` dict (server-built DISPLAY TEXT that has to be parsed back into
    numbers -- see stat_values.py), confirmed this session from render_mixin.py's own Skills tab
    renderer: a skill dict's ``stat_bonus``/``pct_bonus`` (or the specialized
    ``display_stat_bonus``/``trade_bot_pct_bonus``/``station_pct_bonus`` variants) are already
    RAW NUMBERS the client does live arithmetic on (``bonus_per_level * current_level``) -- no
    parsing needed at all, just direct use once captured.

    One row per skill_id, upserted (a skill's own bonus-per-level rate is a stable definition, not
    a time series -- repeated sightings, e.g. after training a level, just refresh the same row
    with its current level) -- skipped entirely when a skill's data is byte-identical to what's
    already stored, the same "don't rewrite the file for a repeat observation" fix as every other
    recorder here (see this module's own docstring). The mastery_tree's own per-entry shape isn't
    confirmed against a real payload yet (only ``level`` is directly read by the code this was
    traced from), so it's appended raw instead, same reasoning as
    record_turret_state/record_ship_plugin_data (including their own identical-repeat dedup)."""
    try:
        if not _capturing(out_path):
            return
        usable = [s for s in (skills or []) if isinstance(s, dict) and s.get("skill_id")]
        if usable:
            _dispatch(out_path, _record_skills_now,
                      _snapshot(out_path, usable), time.time(), out_path)
        if mastery_tree and not _unchanged(("mastery_tree", None), mastery_tree):
            _append("mastery_tree", mastery_tree, out_path)
    except Exception:
        logger.exception("combat_scan_logger: failed to record skills data")


def _record_skills_now(skills: list, captured_at: float, out_path: str | None) -> None:
    try:
        path = out_path or _default_out_path()
        keyed_rows, passthrough_rows = _read_existing_rows(path)
        changed = False
        for skill in skills or []:
            if not isinstance(skill, dict):
                continue
            skill_id = str(skill.get("skill_id") or "")
            if not skill_id:
                continue
            existing = keyed_rows.get(("skill", skill_id))
            if existing and existing.get("data") == skill:
                continue
            keyed_rows[("skill", skill_id)] = {
                "source": "skill",
                "key": skill_id,
                "captured_at": captured_at,
                "data": skill,
            }
            changed = True
        if changed:
            _write_rows(path, keyed_rows, passthrough_rows)
    except Exception:
        logger.exception("combat_scan_logger: failed to record skills data")


def load_combat_scan(path: str | None = None) -> list[dict]:
    """Every captured row, in file order -- the read side for an offline analysis tool. Best-
    effort: a missing/unreadable file just means nothing's been captured yet, not a crash."""
    try:
        p = path or _default_out_path()
        if not os.path.exists(p):
            return []
        rows = []
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(row, dict):
                    rows.append(row)
        return rows
    except Exception:
        logger.exception("combat_scan_logger: failed to load combat scan")
        return []

"""bot_activity.py — turn scan_data/station_logs.csv into per-station/per-bot utilization stats.

``parse_log_line`` classifies one PS_LOGS text line (see station_logs_client.py) --
"Ver-A4 (TB-ExampleAlt3) docked.", "Ver-A3 (TB-ExampleAlt) bought 4,683 Space Corn for
4,683 credits." A bot on LOAD / UNLOAD orders logs "withdrew N X." / "deposited N X."
instead, which counts as bought / sold for no credits. A line that isn't a dock/undock/buy/sell
(e.g. a station-owner inventory action like "SomePlayer withdrew 5 Workers.") isn't bot activity and
returns None -- the caller skips it, that's not a parse error.

``bot_activity_summary`` is pure (rows + auxiliary maps in, JSON-friendly dict out)
and unit tested, mirroring scan_dashboard.galaxy_summary's shape. The auxiliary
maps (station names, bot roster, shuttle targets, trade bot slots) are all
optional and each independently captured -- see station_id_logger.py,
bot_roster_capture.py, shuttle_route_capture.py, trade_bot_slots_capture.py --
so a dashboard consumer still gets useful per-station/per-bot dock/transaction
counts even before any of them exist, just without the cross-references those add.

Each station's real, per-resource extraction rate (``resource_production_per_day``)
comes straight from the SAME colony_scan_rows already used for storage/caps --
specifically each equipped module's own ``stats.Cycle Output``/``stats.Cycle Time``
(e.g. "3 Metal Ore" every "10 seconds" -> 25,920/day), summed across however many
of that module are installed. Only pure extractors count (see
``_extractor_output_per_unit_per_day`` for why a converter like Ration Processor
is deliberately excluded). Crossed against the real bought quantities already
sitting in every "bought"/"sold" log line (previously parsed for a txn COUNT only,
now also kept for qty+resource), this gives ``resource_balance`` -- per resource,
how much the station produces per day vs. how much bots are actually hauling away
per day, and what % of production that represents. A resource a station produces
but nothing ever buys will eventually back up (cross-reference against
``backed_up``); one bots buy faster than it's produced will run dry even while it
still looks fine on a single Storage-tab snapshot.
"""

from __future__ import annotations

import re
import statistics

# A station at or above this hull-fill percentage is flagged "backed up" on
# the dashboard -- matches the mod-penalty threshold scan_dashboard.py's own
# station-detail-row rendering already uses for a near-cap resource, so the
# same number means the same thing everywhere in this tab.
BACKED_UP_HULL_FILL_PCT = 90.0

# The Logs tab hands over one page (about 250 entries) per fetch, so station_logs.csv holds a few short windows per
# station and not a continuous record: the gaps between windows are NOT idle time. Entries less than this far apart
# belong to one window ...
OBSERVED_WINDOW_GAP_SECONDS = 6 * 3600
# ... and the newest window is where "what bots really took" is measured, provided it spans at least this long.
OBSERVED_MIN_WINDOW_SECONDS = 3 * 3600

_DOCK_RE = re.compile(
    r"^(?:(?P<bot>.+?) \((?P<tag>TB-[^)]+)\)|(?P<player>[^()]+?)) "
    r"(?P<action>docked|undocked)\.$")
_TXN_RE = re.compile(
    r"^(?:(?P<bot>.+?) \((?P<tag>TB-[^)]+)\)|(?P<player>[^()]+?)) "
    r"(?P<verb>bought|sold) (?P<qty>[\d,]+) (?P<item>.+?) for (?P<price>[\d,]+) credits\.$")
# A bot on LOAD / UNLOAD orders moves goods for no credits: "Metal Ore (TB-No U) withdrew 32,643 Metal Ore." at the
# station it loads from, "... deposited 15,468 Metal Ore." where it unloads. Bots only: a player's own
# "SomePlayer withdrew 5 Workers." is an inventory action, not bot activity.
_MOVE_RE = re.compile(
    r"^(?P<bot>.+?) \((?P<tag>TB-[^)]+)\) (?P<verb>withdrew|deposited) (?P<qty>[\d,]+) (?P<item>.+?)\.$")
_MOVE_KIND = {"withdrew": "bought", "deposited": "sold"}


def parse_log_line(text: str) -> dict | None:
    """Classify one station-log text line. See module docstring. A bot's withdrew / deposited line counts as bought /
    sold with a price of 0 (``transfer`` is True), so a LOAD / UNLOAD route is measured like a BUY / SELL one."""
    m = _DOCK_RE.match(text)
    if m:
        actor = m.group("bot") or m.group("player")
        return {"actor": actor, "tag": m.group("tag"), "is_bot": m.group("bot") is not None,
                "kind": m.group("action")}
    m = _TXN_RE.match(text)
    if m:
        actor = m.group("bot") or m.group("player")
        return {"actor": actor, "tag": m.group("tag"), "is_bot": m.group("bot") is not None,
                "kind": m.group("verb"),
                "qty": int(m.group("qty").replace(",", "")),
                "item": m.group("item"),
                "price": int(m.group("price").replace(",", ""))}
    m = _MOVE_RE.match(text)
    if m:
        return {"actor": m.group("bot"), "tag": m.group("tag"), "is_bot": True,
                "kind": _MOVE_KIND[m.group("verb")],
                "qty": int(m.group("qty").replace(",", "")),
                "item": m.group("item"), "price": 0, "transfer": True}
    return None


def _newest_window(times: list[int]) -> tuple[int, int] | None:
    """``(start, end)`` of the newest stretch of ``times`` with no gap over OBSERVED_WINDOW_GAP_SECONDS, None when
    it spans less than OBSERVED_MIN_WINDOW_SECONDS (too short to say what a bot takes per day from)."""
    if not times:
        return None
    ordered = sorted(times)
    end = start = ordered[-1]
    for t in reversed(ordered[:-1]):
        if start - t > OBSERVED_WINDOW_GAP_SECONDS:
            break
        start = t
    return (start, end) if end - start >= OBSERVED_MIN_WINDOW_SECONDS else None


_CYCLE_OUTPUT_RE = re.compile(r"^([\d,]+(?:\.\d+)?)\s+(.+)$")
_CYCLE_TIME_RE = re.compile(r"^([\d,]+(?:\.\d+)?)\s*seconds$", re.IGNORECASE)
_SECONDS_PER_DAY = 86400


def _extractor_output_per_unit_per_day(item: dict) -> tuple[str, float] | None:
    """One equipped module's raw resource output, PER INSTALLED UNIT, per day -- from its own
    ``stats.Cycle Output``/``stats.Cycle Time`` (e.g. "3 Metal Ore" every "10 seconds" ->
    25,920/day; confirmed this matches the module's own precomputed ``stats.Production`` string
    exactly, for the extractors that have one). Returns None for anything that isn't a pure
    extractor: needs both stats to produce a measurable rate at all, and a module that ALSO lists
    ``stats["Cycle Input"]`` is a CONVERTER (e.g. Ration Processor consuming Space Corn + credits)
    rather than a raw producer -- its real throughput is capped by available input stock, which
    this deliberately doesn't model (the ask was specifically about extractors)."""
    stats = item.get("stats")
    if not isinstance(stats, dict) or "Cycle Input" in stats:
        return None
    cycle_output, cycle_time = stats.get("Cycle Output"), stats.get("Cycle Time")
    if not cycle_output or not cycle_time:
        return None
    m_out = _CYCLE_OUTPUT_RE.match(str(cycle_output).strip())
    m_time = _CYCLE_TIME_RE.match(str(cycle_time).strip())
    if not m_out or not m_time:
        return None
    try:
        qty_per_cycle = float(m_out.group(1).replace(",", ""))
        seconds_per_cycle = float(m_time.group(1).replace(",", ""))
    except ValueError:
        return None
    if seconds_per_cycle <= 0:
        return None
    return m_out.group(2).strip(), qty_per_cycle / seconds_per_cycle * _SECONDS_PER_DAY


def _resolve_resource_key(display_name: str, known_keys: set[str]) -> str:
    """A free-text resource name (a module's Cycle Output, or a station-log txn's item name --
    "Metal Ore") resolved to the game's own resource item_key ("metal_ore") used everywhere else
    in this module. Tries the plain slug first; only if that exact slug isn't a key any captured
    station's real cargo actually uses does it try adding/stripping a trailing "s" -- covers
    "Ration" (a module's Cycle Output text) vs. the real key "rations" (confirmed against real
    captured data: the game's own text doesn't always agree with itself on singular/plural).
    Falls back to the plain slug when nothing matches, so an unrecognized resource still gets a
    stable, readable key instead of being silently dropped."""
    slug = display_name.strip().lower().replace(" ", "_").replace("-", "_")
    if slug in known_keys:
        return slug
    if slug + "s" in known_keys:
        return slug + "s"
    if slug.endswith("s") and slug[:-1] in known_keys:
        return slug[:-1]
    return slug


def _collect_known_resource_keys(colony_scan_rows: list[dict] | None) -> set[str]:
    """Every real raw-resource item_key seen across ALL captured stations' cargo -- the
    disambiguation set ``_resolve_resource_key`` checks free-text resource names against."""
    keys: set[str] = set()
    for row in colony_scan_rows or []:
        if not isinstance(row, dict) or row.get("source") != "station_setup":
            continue
        for item in (row.get("data") or {}).get("cargo") or []:
            if isinstance(item, dict) and item.get("item_category") == "resource":
                key = item.get("item_key")
                if key:
                    keys.add(str(key))
    return keys


def _shuttle_flow_edges_from_colony_scan(
        colony_scan_rows: list[dict] | None) -> dict[tuple[str, str, str], float]:
    """``{(source_station_id, dest_station_id, resource_key): rate_per_day}`` -- every enabled
    orbital-shuttle route as a directed flow between two stations, from colony_scan_logger's
    "shuttle_network" rows (PS_SHUTTLE_DATA, keyed by the HUB station that owns the equipped
    Orbital Shuttle Network module and whose Shuttle tab was opened). A route's ``direction`` is
    relative to that HUB (confirmed against the module's own description -- "Automates resource
    imports and exports... each configured resource route can move up to N units every M
    seconds"): "import" means the hub pulls the resource IN from ``target_station_id`` (flow
    target -> hub); "export" means the hub pushes its own stock OUT to the target (flow hub ->
    target). Both are real flows and both matter: the sender is being offloaded, and the receiver
    has that much extra supply to move on.

    The rate is the module's own declared cap (transfer_limit / transfer_interval_seconds, scaled
    to a day), the same "assume it runs at its stated capacity" principle used for extractor
    production (_extractor_output_per_unit_per_day); ``_solve_shuttle_flows`` then limits what
    actually moves by what the sender really has. Summed (not maxed) across however many enabled
    routes carry the same (source, dest, resource) edge -- confirmed against real data that a
    hub's own "import" route and the feeder's own "export" route for the same flow are distinct
    routes with distinct ids (kortherxaxar-1/-2/-3), each with its own independent capacity."""
    edges: dict[tuple[str, str, str], float] = {}
    for row in colony_scan_rows or []:
        if not isinstance(row, dict) or row.get("source") != "shuttle_network":
            continue
        hub_id = str(row.get("key", "") or "")
        data = row.get("data") or {}
        try:
            transfer_limit = float(data.get("transfer_limit") or 0)
            transfer_interval = float(data.get("transfer_interval_seconds") or 0)
        except (TypeError, ValueError):
            continue
        if not hub_id or transfer_limit <= 0 or transfer_interval <= 0:
            continue
        rate_per_day = transfer_limit / transfer_interval * _SECONDS_PER_DAY
        for route in data.get("routes") or []:
            if not isinstance(route, dict) or not route.get("enabled", True):
                continue
            direction = route.get("direction")
            target = route.get("target_station_id", route.get("lunar_station_id"))
            resource_key = route.get("resource_key")
            if direction not in ("import", "export") or target is None or not resource_key:
                continue
            target = str(target)
            source, dest = (target, hub_id) if direction == "import" else (hub_id, target)
            edge = (source, dest, str(resource_key))
            edges[edge] = edges.get(edge, 0.0) + rate_per_day
    return edges


# Enough passes for a supply chain several hops deep (feeder -> hub -> hub) to
# settle; a same-resource cycle (A -> B -> A, not seen in real data) can't
# converge, so this also bounds how far one could inflate.
_SHUTTLE_SOLVER_PASSES = 6


def _solve_shuttle_flows(edges: dict[tuple[str, str, str], float],
                          base_supply: dict[tuple[str, str], float]) -> tuple[dict, dict]:
    """Work out how much each shuttle edge really moves, given each station's own supply.

    ``base_supply`` is ``{(station_id, resource_key): per_day}`` -- what a station makes or has
    delivered to it by bots itself (local extraction + bot sales in), for every station whose
    supply is KNOWN. A station's supply also includes whatever other stations shuttle INTO it,
    which is what makes this iterative: a hub's real supply depends on what its feeders can send,
    and a feeder that is itself fed needs THAT settled first.

    Each pass, a station moves ``min(total declared capacity of its outgoing edges, its current
    supply)`` for a resource, split across those edges in proportion to their capacities.
    Nothing moves out of a station whose supply is entirely unknown -- no guessing at the
    sender's stock.

    Returns ``(outflow, inflow)``: ``outflow[(station, resource)] = {"rate": float, "to": {dest:
    rate}}`` and ``inflow[(station, resource)] = {"rate": float, "from": {src: rate}}``."""
    outgoing: dict[tuple[str, str], list[tuple[str, float]]] = {}
    for (source, dest, resource_key), cap in edges.items():
        outgoing.setdefault((source, resource_key), []).append((dest, cap))

    outflow: dict = {}
    inflow: dict = {}
    for _pass in range(_SHUTTLE_SOLVER_PASSES):
        settled_inflow = {key: value["rate"] for key, value in inflow.items()}
        outflow, inflow = {}, {}
        for (station, resource_key), dests in outgoing.items():
            local = base_supply.get((station, resource_key))
            incoming = settled_inflow.get((station, resource_key), 0.0)
            if local is None and not incoming:
                continue
            supply = (local or 0.0) + incoming
            total_cap = sum(cap for _dest, cap in dests)
            moved = min(total_cap, supply)
            if moved <= 0:
                continue
            record = {"rate": moved, "to": {}}
            for dest, cap in dests:
                share = moved * cap / total_cap
                record["to"][dest] = record["to"].get(dest, 0.0) + share
                received = inflow.setdefault((dest, resource_key), {"rate": 0.0, "from": {}})
                received["rate"] += share
                received["from"][station] = received["from"].get(station, 0.0) + share
            outflow[(station, resource_key)] = record
    return outflow, inflow


def _station_storage_from_colony_scan(colony_scan_rows: list[dict] | None) -> dict[str, dict]:
    """Reduce colony_scan_logger.load_colony_scan()'s rows to ``{station_id: {...}}`` for just
    the "station_setup" source (PS_STATION_CARGO -- see that module's own docstring), the one
    that carries hull capacity, per-resource storage caps/reserves, and the station's actual
    current cargo. Keeps only the newest row per station_id (colony_scan_logger upserts by
    station_id itself, so this is normally already true of a fresh load -- defensive here in
    case a caller merges scans from multiple points in time).

    A cargo row's real quantity follows the exact rule Client.py's own Storage-tab renderer
    uses (confirmed against it directly): an ``item_key`` that does NOT start with "item:" is a
    raw resource stack, where ``cargo_size`` IS the stock count; one that DOES start with
    "item:" is an equipped module, where ``quantity`` is the count installed and ``cargo_size``
    is its per-unit space cost. Getting this backwards would silently misreport every station's
    resource stock, so this mirrors that logic rather than re-deriving it from scratch."""
    latest: dict[str, dict] = {}
    for row in colony_scan_rows or []:
        if not isinstance(row, dict) or row.get("source") != "station_setup":
            continue
        station_id = str(row.get("key", "") or "")
        if not station_id:
            continue
        if station_id in latest and latest[station_id].get("captured_at", 0) >= row.get("captured_at", 0):
            continue
        latest[station_id] = row

    known_keys = _collect_known_resource_keys(colony_scan_rows)

    storage: dict[str, dict] = {}
    for station_id, row in latest.items():
        data = row.get("data") or {}
        resources: dict[str, int] = {}
        production: dict[str, float] = {}
        for item in data.get("cargo") or []:
            if not isinstance(item, dict):
                continue
            item_key = str(item.get("item_key", "") or "")
            if not item_key:
                continue
            if item.get("item_category") == "resource":
                is_raw = not item_key.startswith("item:")
                try:
                    qty = int(item.get("cargo_size", 1)) if is_raw else int(item.get("quantity", 1))
                except (TypeError, ValueError):
                    continue
                resources[item_key] = resources.get(item_key, 0) + qty
                continue
            # Not a resource stack -- an equipped module. Only a pure extractor
            # (see _extractor_output_per_unit_per_day) contributes a measurable
            # production rate; everything else (shields, weapons, expanders,
            # the shuttle network module itself) is silently skipped.
            extracted = _extractor_output_per_unit_per_day(item)
            if extracted is None:
                continue
            resource_name, per_unit_per_day = extracted
            try:
                count = int(item.get("quantity", 0) or 0)
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            resource_key = _resolve_resource_key(resource_name, known_keys)
            production[resource_key] = production.get(resource_key, 0.0) + per_unit_per_day * count
        try:
            hull_used = int(data.get("hull_used", 0) or 0)
            hull_space = int(data.get("hull_space", 0) or 0)
        except (TypeError, ValueError):
            hull_used, hull_space = 0, 0
        storage[station_id] = {
            "hull_used": hull_used, "hull_space": hull_space,
            "hull_fill_pct": round(100 * hull_used / hull_space, 1) if hull_space else None,
            "resources": resources,
            "resource_caps": dict(data.get("resource_caps") or {}),
            "resource_reserves": dict(data.get("resource_reserves") or {}),
            "resource_production_per_day": {k: round(v, 1) for k, v in production.items()},
        }
    return storage


def _roster_from_fleet_rows(bot_fleet_rows: list[dict] | None) -> dict[tuple[str, str], dict]:
    """Reduce bot_fleet_scan_logger.load_bot_fleet_scan()'s ``{key, captured_at, context,
    data}`` rows (keyed by bot_pid) to ``{(nickname, account tag): {...}}`` -- station log text
    identifies a bot by its nickname and account tag (see Client._rc_bot_nameplate), not bot_pid,
    so this is the join key a consumer here actually needs. Nicknames alone are NOT unique:
    several accounts each have a bot called "3", or "Metal Ore". The tag is "" for a row captured
    without a username. On the (unexpected) case of two bot_pids sharing a nickname within one
    account, the most recently captured row wins."""
    roster: dict[tuple[str, str], dict] = {}
    for row in bot_fleet_rows or []:
        if not isinstance(row, dict):
            continue
        data = row.get("data") or {}
        nickname = str(data.get("nickname", "") or "").strip()
        if not nickname:
            continue
        username = str((row.get("context") or {}).get("username", "") or "")
        tag = f"TB-{username}" if username else ""
        existing = roster.get((nickname, tag))
        if existing is not None and existing["_captured_at"] >= row.get("captured_at", 0):
            continue
        roster[(nickname, tag)] = {
            "tag": tag,
            "ship_type": data.get("ship_type"),
            "state": data.get("state"),
            "cargo_cap": data.get("bot_cargo_cap"),
            "cargo_used": data.get("bot_cargo_used"),
            "haul_order_count": len(data.get("haul_orders") or []),
            "_captured_at": row.get("captured_at", 0),
        }
    return roster


def bot_activity_summary(log_rows: list[dict], station_names: dict | None = None,
                          bot_fleet_rows: list[dict] | None = None,
                          shuttle_targets: set | None = None,
                          shuttle_data_captured: bool = False,
                          trade_bot_slots: dict | None = None,
                          colony_scan_rows: list[dict] | None = None,
                          station_systems: dict | None = None) -> dict:
    """Return ``{"stations": {...}, "bots": {...}, "accounts": {...}}``.

    ``stations`` is keyed by station name (falling back to the raw station_id
    when ``station_names`` doesn't cover it): bot_docks, unique_bots, txns,
    bot_txns, player_docks (the owner's own manual dock/undock, informational),
    shuttle_covered (True/False once ``shuttle_data_captured`` is True, else
    None for "not checked yet" -- see shuttle_route_capture.py), and system
    (the galaxy the station is in, from ``station_systems`` --
    station_id_logger.load_station_systems() -- None when that station_id
    isn't covered there yet).

    ``bots`` is keyed by the bot's display name (the same name station logs
    show it under) -- or "Name (TB-account)" when several accounts have a bot
    of that name, which would otherwise merge into one (each entry also has
    ``nickname``, and ``bot_breakdown`` is keyed the same way): docks, txns, stations_visited, txn_per_dock, plus whatever
    ``bot_fleet_rows`` (bot_fleet_scan_logger.load_bot_fleet_scan(), the RC_BOT_STATE
    capture) knows about its current ship_type/cargo/haul-order-count/state --
    None for each when that capture doesn't cover this bot yet (its owning
    account hasn't been logged into since bot_fleet_scan_logger existed).

    ``accounts`` is ``trade_bot_slots`` passed through keyed by "TB-<username>"
    (the same tag format bot names use), the one authoritative source for
    slot usage -- slots_used is the SUM OF EACH BOT'S SHIP-TECH COST, not a
    count of bots, so it must come from TRADE_BOT_SLOTS_DATA, never be
    inferred from how many distinct bot names show up in the logs.

    Each station entry also carries hull_used/hull_space/hull_fill_pct and
    resources/resource_caps/resource_reserves from ``colony_scan_rows``
    (colony_scan_logger.load_colony_scan(), PS_STATION_CARGO's own capture)
    -- all None until that station's Cargo/Storage data has actually been
    captured (see _station_storage_from_colony_scan), never defaulted to 0,
    since an uncaptured station and a genuinely-empty one must stay
    distinguishable. A station can appear here from colony_scan_rows alone,
    with zero log activity -- that combination (piling up cargo, no bot ever
    docks) is exactly the signal this was built to surface.

    ``median_dock_gap_seconds`` is the real, per-station cadence: the median
    gap between consecutive bot dock timestamps (any bot, this station),
    None with fewer than two dock events. Deliberately NOT docks divided by
    however much time station_logs.csv happens to span -- that includes
    stretches with no client even logged in and drastically understates how
    often a bot actually shows up (confirmed this session: a station that
    looked like "once per 27 minutes" by that math was really every ~40-50
    real seconds). ``backed_up`` is True once hull_fill_pct reaches
    BACKED_UP_HULL_FILL_PCT, None while hull_fill_pct itself is unknown --
    a HIGH bot_docks count alongside backed_up=True is the real bottleneck
    signal (a bot working flat out that still can't keep the station clear),
    distinct from backed_up=True with bot_docks=0 (nobody servicing it, or
    -- check shuttle_covered first -- fully upstream of a hub that itself
    needs the attention).

    ``resource_production_per_day`` is each resource's real extraction rate
    (see _station_storage_from_colony_scan/_extractor_output_per_unit_per_day)
    -- same None-until-captured rule as hull/resources above.
    ``resource_activity`` is ``{resource_key: {"bought": total_qty, "sold":
    total_qty}}``, summed from every bot txn log line at this station over
    however much of its Logs tab has been captured. ``resource_balance``
    combines that with ``resource_production_per_day`` (and the shuttle
    network -- see below) into ``{resource_key: {"production_per_day",
    "bought_per_day", "sold_per_day", "shuttled_per_day", "shuttled_to",
    "pct_satisfied"}}`` -- bought_per_day is computed PER BOT, then summed:
    each bot's own (bought at this station / that bot's own dock count) x
    that bot's OWN real docks-per-day (86400 / the median of THAT bot's own
    consecutive dock gaps at this station), not a single station-wide average
    blended across every bot. Two real bugs that produced if it were blended
    instead: (1) different bots buy very different amounts per trip, so one
    combined average misrepresents all of them; (2) pooling every bot's dock
    timestamps into one combined median overstates a busy multi-bot hub's
    real rate whenever different bots' visits cluster in real time (confirmed
    against real data: wylvaor-1, 10 different bots all played in the same
    ~1.5-hour session, read as a 9-SECOND combined median -- the tiny gaps
    WITHIN that cluster, not real spacing -- and produced an unbelievable
    996% "bot coverage"). Each bot's own dock sequence is its own signal,
    unmixed with anyone else's; a single-bot station (the common case) gets
    the exact same result this always produced, since there's nothing to
    blend. A bot needs 2+ of its own dock events at this station to
    contribute (same precondition median_dock_gap_seconds itself needs);
    bought_per_day is None only when NO bot at this station clears that bar.
    Still deliberately NOT total bought divided by the log's raw wall-clock
    span (which would count every stretch of real time nobody was logged in
    and understate the true rate -- the "27 minutes" bug).

    The DEMAND side (what needs picking up) isn't only production_per_day.
    Everything arriving at a station has to be moved on just the same, so
    pct_satisfied's real denominator is
    ``effective_supply_per_day = production_per_day + sold_per_day +
    shuttled_in_per_day`` (each of the three shown separately in
    resource_balance so the components stay visible; the sum is not its own
    field). Unknown only when ALL of them are unknown. sold_per_day (a bot
    SELLING this resource TO the station) is real, OBSERVED historical
    quantity, same per-bot methodology as bought_per_day above, never a haul
    order's configured target -- a bot that only ever had a partial load to
    sell is already reflected as exactly that.

    ``shuttled_in_per_day``/``shuttled_from`` and ``shuttled_per_day``/
    ``shuttled_to`` (see _shuttle_flow_edges_from_colony_scan and
    _solve_shuttle_flows) are the orbital shuttle network as real flows
    between stations, in BOTH directions: a feeder whose output is hauled to a
    hub is offloaded (shuttled_per_day, counted alongside bought_per_day as
    offtake -- so a feeder with zero bot docks can still be fully "satisfied",
    REGARDLESS of whether it has any real bot dock data at all), and the hub
    RECEIVING it has that much more supply to clear (shuttled_in_per_day, part
    of effective_supply_per_day). Leaving out the receiving side was a real bug:
    kortherxaxar-1 read 307% "bot coverage" while sitting at its storage cap,
    because its bot was hauling the feeders' shuttled-in stock but only the
    hub's own extraction counted as supply (now ~102%). What actually moves is
    limited by what the SENDER has (see _solve_shuttle_flows), never the
    route's declared transfer rate alone, which is a maximum rather than an
    observed quantity. pct_satisfied is (bought_per_day + shuttled_per_day) as
    a % of effective_supply_per_day, None only when NEITHER side is known (a
    resource bots buy with no known supply of any kind is reported with
    bought_per_day but a None pct_satisfied rather than a misleading 0).

    ``overall_pct_satisfied`` is a single station-wide %: total (bought +
    shuttled)/day of everything this station has real supply of, over total
    effective_supply_per_day -- volume-WEIGHTED across resources (not a
    per-resource min or average), so a trickle side-extractor nobody buys (a
    lone Titanium Extractor making ~9/day) can't drag the number to 0 the way
    it would dominate a naive worst-case; the real economy (wood/ore/silicon/
    space_corn, usually orders of magnitude bigger) is what actually moves
    it. The single number for a dashboard column without expanding the row
    -- the full per-resource breakdown is what resource_balance is for.
    ``bot_breakdown`` is ``{bot_name: {"tag", "docks", "txns", "resources":
    {resource_key: {"bought", "sold"}}}}`` for just the bots that actually
    visited THIS station -- distinct from the top-level ``bots`` dict, which
    aggregates one bot across every station it visits.

    Two rates, because the first assumes the bot never stops. bought_per_day
    (and pct_satisfied, overall_pct_satisfied) is "while running": a bot's load per
    dock times the docks per day its own loop time allows, as if it ran 24 hours.
    A bot whose owner is only logged in part of the day takes far less (real data
    2026-10-31: about a fifth, for the user's own bots too). ``log_window`` is
    ``{"start", "end", "hours"}``, the station's newest stretch of log with no gap
    over OBSERVED_WINDOW_GAP_SECONDS (None when shorter than
    OBSERVED_MIN_WINDOW_SECONDS); inside it every bot dock and txn is on record, so
    what really left is a fact. ``bought_observed_per_day`` / ``sold_observed_per_day``
    / ``pct_observed`` (resource_balance) and ``overall_pct_observed`` are the same
    figures worked out from that window alone -- None without a window. Each
    ``bot_breakdown`` entry also has ``loop_seconds`` (the median gap between its
    own docks here), ``last_dock`` (its newest dock here: a bot that stopped coming is retired or moved on),
    ``window_docks`` and ``resource_rates``: ``{resource_key:
    {"per_dock", "running_per_day", "observed_per_day"}}`` (bought + sold units).
    """
    station_names = station_names or {}
    station_systems = station_systems or {}
    bot_roster = _roster_from_fleet_rows(bot_fleet_rows)
    shuttle_targets = shuttle_targets or set()
    trade_bot_slots = trade_bot_slots or {}
    station_storage = _station_storage_from_colony_scan(colony_scan_rows)
    known_resource_keys = _collect_known_resource_keys(colony_scan_rows)
    shuttle_edges = _shuttle_flow_edges_from_colony_scan(colony_scan_rows)

    def _station_entry(stations: dict, station_id: str) -> dict:
        name = station_names.get(station_id, station_id)
        return stations.setdefault(name, {
            "station_id": station_id, "system": station_systems.get(station_id),
            "bot_docks": 0, "unique_bots": set(),
            "txns": 0, "bot_txns": 0, "player_docks": 0, "log_rows": 0,
            "shuttle_covered": (station_id in shuttle_targets) if shuttle_data_captured else None,
            "hull_used": None, "hull_space": None, "hull_fill_pct": None,
            "resources": None, "resource_caps": None, "resource_reserves": None,
            "resource_production_per_day": None,
            "median_dock_gap_seconds": None, "backed_up": None,
            "resource_activity": {}, "bot_breakdown": {},
            "resource_balance": {}, "overall_pct_satisfied": None,
            "log_window": None, "overall_pct_observed": None,
        })

    def _bot_entry(bots: dict, key: str, tag: str, nickname: str) -> dict:
        entry = bots.setdefault(key, {
            "tag": tag, "docks": 0, "txns": 0, "stations": set(), "nickname": nickname,
        })
        if tag and not entry.get("tag"):
            entry["tag"] = tag
        return entry

    stations: dict[str, dict] = {}
    bots: dict[str, dict] = {}
    dock_times: dict[str, list[int]] = {}
    log_row_counts: dict[str, int] = {}

    parsed_rows = [(row, parse_log_line(row.get("text", ""))) for row in log_rows]
    # Nicknames are not unique across accounts ("Metal Ore" is a bot of three different accounts, "3" of four): a
    # nickname shared that way is told apart by its account tag; any other keeps its bare nickname as key.
    tags_by_nickname: dict[str, set] = {}
    for _row, parsed in parsed_rows:
        if parsed is not None and parsed["is_bot"]:
            tags_by_nickname.setdefault(parsed["actor"], set()).add(parsed["tag"])

    def _bot_key(parsed: dict) -> str:
        return (f"{parsed['actor']} ({parsed['tag']})" if len(tags_by_nickname[parsed["actor"]]) > 1
                else parsed["actor"])

    row_times: dict[str, list[int]] = {}
    for row in log_rows:
        try:
            row_times.setdefault(row.get("station_id", ""), []).append(int(row.get("time", 0)))
        except (TypeError, ValueError):
            pass
    windows = {station_id: window for station_id, window in
               ((station_id, _newest_window(times)) for station_id, times in row_times.items()) if window}

    for row, parsed in parsed_rows:
        log_row_counts[row.get("station_id", "")] = log_row_counts.get(row.get("station_id", ""), 0) + 1
        if parsed is None:
            continue
        station_id = row.get("station_id", "")
        st = _station_entry(stations, station_id)
        window = windows.get(station_id)
        try:
            row_time = int(row.get("time", 0))
        except (TypeError, ValueError):
            row_time = None
        in_window = window is not None and row_time is not None and window[0] <= row_time <= window[1]

        if not parsed["is_bot"]:
            if parsed["kind"] in ("docked", "undocked"):
                st["player_docks"] += 1
            continue

        bot_key = _bot_key(parsed)
        bot = _bot_entry(bots, bot_key, parsed["tag"], parsed["actor"])
        bot["stations"].add(station_id)
        bot_here = st["bot_breakdown"].setdefault(bot_key, {
            "tag": parsed["tag"], "nickname": parsed["actor"], "docks": 0, "txns": 0, "resources": {},
            "_dock_times": [], "window_docks": 0, "_window_resources": {},
        })
        if parsed["tag"] and not bot_here.get("tag"):
            bot_here["tag"] = parsed["tag"]

        if parsed["kind"] == "docked":
            st["bot_docks"] += 1
            st["unique_bots"].add(bot_key)
            bot["docks"] += 1
            bot_here["docks"] += 1
            if in_window:
                bot_here["window_docks"] += 1
            try:
                t = int(row.get("time", 0))
                dock_times.setdefault(station_id, []).append(t)
                bot_here["_dock_times"].append(t)
            except (TypeError, ValueError):
                pass
        elif parsed["kind"] in ("bought", "sold"):
            st["txns"] += 1
            st["bot_txns"] += 1
            bot["txns"] += 1
            bot_here["txns"] += 1
            resource_key = _resolve_resource_key(parsed["item"], known_resource_keys)
            activity = st["resource_activity"].setdefault(resource_key, {"bought": 0, "sold": 0})
            activity[parsed["kind"]] += parsed["qty"]
            bot_resource = bot_here["resources"].setdefault(resource_key, {"bought": 0, "sold": 0})
            bot_resource[parsed["kind"]] += parsed["qty"]
            if in_window:
                window_resource = bot_here["_window_resources"].setdefault(resource_key, {"bought": 0, "sold": 0})
                window_resource[parsed["kind"]] += parsed["qty"]

    # How often ANY bot actually shows up at this station, from the real
    # gaps between consecutive dock timestamps (not docks / whatever span
    # station_logs.csv happens to cover -- that includes stretches where no
    # client was even logged in, and badly understates real cadence; a real
    # bug found this session). The median is used deliberately, not the
    # mean: it stays put even with the occasional multi-minute reconnect
    # gap mixed in with a bot's normal ~seconds-scale cycle time, so no
    # separate "session boundary" cutoff is needed.
    for station_id, times in dock_times.items():
        if len(times) < 2:
            continue
        times.sort()
        gaps = [b - a for a, b in zip(times, times[1:])]
        _station_entry(stations, station_id)["median_dock_gap_seconds"] = \
            round(statistics.median(gaps), 1)

    # A station can have captured Cargo/Storage data with zero log activity
    # (its Logs tab was never opened, or it genuinely has no bot activity) --
    # _station_entry creates it fresh here rather than skipping it, so a
    # piling-up, never-visited station is still surfaced, not silently lost.
    # Must run BEFORE the unique_bots conversion below, or a station created
    # only here keeps unique_bots as a raw (unJSON-serializable) set.
    for station_id, storage in station_storage.items():
        entry = _station_entry(stations, station_id)
        entry.update(storage)
    # How many log lines of ANY kind were fetched for a station: with ``bot_docks`` 0 that is the difference between
    # "its log has never been opened" (0) and "its log shows no bot ever docked" (>0).
    for entry in stations.values():
        entry["log_rows"] = log_row_counts.get(entry["station_id"], 0)
        window = windows.get(entry["station_id"])
        if window:
            entry["log_window"] = {"start": window[0], "end": window[1],
                                   "hours": round((window[1] - window[0]) / 3600.0, 1)}

    # Phase 1 -- every station's OWN real bot rates and own supply. Kept apart
    # from phase 2 (the per-resource balance) because a hub's supply also
    # includes whatever OTHER stations shuttle into it, and that can only be
    # worked out once every station's own supply is known.
    station_rates: dict[str, dict] = {}
    base_supply: dict[tuple[str, str], float] = {}
    for entry in stations.values():
        entry["unique_bots"] = len(entry["unique_bots"])
        if entry["hull_fill_pct"] is not None:
            entry["backed_up"] = entry["hull_fill_pct"] >= BACKED_UP_HULL_FILL_PCT

        # Real docks-per-day and real average-bought-per-dock, computed PER BOT
        # from each bot's OWN consecutive dock gaps and OWN purchases at this
        # station, then summed -- deliberately NOT one blended station-wide
        # average. Pooling every bot's dock timestamps into a single combined
        # median (what this used to do) overstates a busy multi-bot hub's real
        # throughput whenever different bots' visits cluster in real time (e.g.
        # several characters played in the same short session): the combined
        # median ends up reflecting the tiny gaps WITHIN a cluster, not the
        # real spacing of a station's actual service -- confirmed against real
        # data (wylvaor-1, 10 different bots, read as a 9-SECOND combined
        # median -> an extrapolated 9,600 docks/day that produced a 996% "bot
        # coverage" nobody would believe). It also silently blends together
        # bots that buy very different amounts per trip. Per-bot medians don't
        # have either problem: each bot's own dock sequence, and own average
        # purchase, is its own signal, never mixed with anyone else's. A
        # single-bot station (the common case) gets the IDENTICAL result this
        # always produced, since there's nothing to blend.
        resource_bought_per_day: dict[str, float] = {}
        resource_sold_per_day: dict[str, float] = {}
        observed_bought_per_day: dict[str, float] = {}
        observed_sold_per_day: dict[str, float] = {}
        any_bot_rate_known = False
        window = windows.get(entry["station_id"])
        window_seconds = (window[1] - window[0]) if window else None
        for bot_entry in entry["bot_breakdown"].values():
            bot_dock_times = bot_entry.pop("_dock_times", [])
            bot_entry["last_dock"] = max(bot_dock_times) if bot_dock_times else None
            window_resources = bot_entry.pop("_window_resources", {})
            bot_docks = bot_entry["docks"]
            bot_gap = None
            if len(bot_dock_times) >= 2:
                bot_dock_times = sorted(bot_dock_times)
                bot_gaps = [b - a for a, b in zip(bot_dock_times, bot_dock_times[1:])]
                bot_gap = statistics.median(bot_gaps)
            bot_docks_per_day = _SECONDS_PER_DAY / bot_gap if bot_gap else None
            bot_entry["loop_seconds"] = round(bot_gap) if bot_gap else None
            # What this bot moved per resource: per dock, per day if it ran flat out, and per day as really seen
            # in the station's newest log window (None without a window).
            resource_rates: dict[str, dict] = {}
            for resource_key, amounts in bot_entry["resources"].items():
                moved = amounts["bought"] + amounts["sold"]
                seen = window_resources.get(resource_key, {"bought": 0, "sold": 0})
                if window_seconds:
                    observed_bought_per_day[resource_key] = (
                        observed_bought_per_day.get(resource_key, 0.0) + seen["bought"] / window_seconds * _SECONDS_PER_DAY)
                    observed_sold_per_day[resource_key] = (
                        observed_sold_per_day.get(resource_key, 0.0) + seen["sold"] / window_seconds * _SECONDS_PER_DAY)
                resource_rates[resource_key] = {
                    "per_dock": round(moved / bot_docks) if bot_docks else None,
                    "running_per_day": (round(moved / bot_docks * bot_docks_per_day)
                                        if bot_docks and bot_docks_per_day else None),
                    "observed_per_day": (round((seen["bought"] + seen["sold"]) / window_seconds * _SECONDS_PER_DAY)
                                         if window_seconds else None),
                }
            bot_entry["resource_rates"] = resource_rates
            if not (bot_docks and bot_docks_per_day):
                continue
            any_bot_rate_known = True
            for resource_key, amounts in bot_entry["resources"].items():
                resource_bought_per_day[resource_key] = (
                    resource_bought_per_day.get(resource_key, 0.0)
                    + amounts["bought"] / bot_docks * bot_docks_per_day)
                resource_sold_per_day[resource_key] = (
                    resource_sold_per_day.get(resource_key, 0.0)
                    + amounts["sold"] / bot_docks * bot_docks_per_day)

        station_rates[entry["station_id"]] = {
            "bought": resource_bought_per_day, "sold": resource_sold_per_day,
            "any_bot_rate_known": any_bot_rate_known,
            "observed_bought": observed_bought_per_day, "observed_sold": observed_sold_per_day,
            "has_window": window_seconds is not None,
        }
        # What a station has to move on by itself: its own extraction plus
        # whatever bots SELL to it (a bot selling here is real inflow needing
        # the same coverage as if it had been produced locally -- using the
        # real OBSERVED historical sold quantity, never a haul order's
        # configured target, so a bot that only ever had a partial load to
        # sell is already reflected as exactly that).
        production = entry["resource_production_per_day"] or {}
        sold_known = resource_sold_per_day if any_bot_rate_known else {}
        for key in set(production) | set(sold_known):
            base_supply[(entry["station_id"], key)] = production.get(key, 0.0) + sold_known.get(key, 0.0)

    # Phase 2 -- shuttle flows between stations, then each station's balance.
    outflow, inflow = _solve_shuttle_flows(shuttle_edges, base_supply)
    outflow_by_station: dict[str, dict[str, dict]] = {}
    for (station_id, resource_key), record in outflow.items():
        outflow_by_station.setdefault(station_id, {})[resource_key] = record
    inflow_by_station: dict[str, dict[str, dict]] = {}
    for (station_id, resource_key), record in inflow.items():
        inflow_by_station.setdefault(station_id, {})[resource_key] = record

    def _station_labels(rates_by_station: dict[str, float]) -> list[str]:
        ordered = sorted(rates_by_station.items(), key=lambda item: -item[1])
        return [station_names.get(station_id, station_id) for station_id, _rate in ordered]

    for entry in stations.values():
        rates = station_rates[entry["station_id"]]
        any_bot_rate_known = rates["any_bot_rate_known"]
        production = entry["resource_production_per_day"] or {}
        activity = entry["resource_activity"]
        out_here = outflow_by_station.get(entry["station_id"], {})
        in_here = inflow_by_station.get(entry["station_id"], {})
        balance: dict[str, dict] = {}
        # Volume-weighted, not a per-resource min/average -- a station's
        # trickle side-extractors (a single Titanium Extractor making ~9/day
        # that nobody ever buys) would otherwise dominate a naive worst-case
        # and make nearly every station read as "0% satisfied" regardless of
        # how its real economy (wood/ore/silicon/space_corn, orders of
        # magnitude bigger) is actually doing.
        total_supply = 0.0
        total_offtake_of_supply = 0.0
        any_offtake_known = False
        total_supply_observed = 0.0
        total_offtake_observed = 0.0
        has_window = rates["has_window"]
        for key in set(production) | set(activity) | set(out_here) | set(in_here):
            prod = production.get(key)
            bought_per_day = rates["bought"].get(key, 0.0) if any_bot_rate_known else None
            sold_per_day = rates["sold"].get(key, 0.0) if any_bot_rate_known else None
            out_info, in_info = out_here.get(key), in_here.get(key)
            shuttled_out_per_day = out_info["rate"] if out_info else 0.0
            shuttled_in_per_day = in_info["rate"] if in_info else 0.0
            # Everything that has to be moved on: own extraction, plus what
            # bots sell in, plus what other stations shuttle IN -- a hub whose
            # feeders push their output into it has that much more to clear
            # than its own extractors alone (the original bug: kortherxaxar-1
            # read 307% "bot coverage" while sitting at its storage cap,
            # because its bot was hauling the feeders' shuttled-in stock but
            # only the hub's own extraction was counted as supply).
            effective_supply_per_day = None
            if prod is not None or sold_per_day is not None or in_info:
                effective_supply_per_day = (prod or 0.0) + (sold_per_day or 0.0) + shuttled_in_per_day
            # Shuttle outflow is already limited by the sender's own supply in
            # _solve_shuttle_flows -- the route's declared transfer_limit is a
            # MAX throughput, not a real observed quantity (unlike
            # bought_per_day, which comes from real transactions and is
            # legitimately allowed to exceed supply when bots drain existing
            # stock). Without that limit a near-idle route for a trickle
            # resource (a single Copper Extractor making ~9/day, against a
            # 200-units/10s module rating) reported the module's full rating
            # -- pct_satisfied over 20,000,000%.
            #
            # A feeder station with zero bot docks (any_bot_rate_known False)
            # can still be fully "satisfied" if an orbital shuttle route is
            # hauling its output to a hub instead, so offtake data is known
            # whenever EITHER side is, not only when there's real bot dock data.
            has_offtake_data = any_bot_rate_known or out_info is not None
            total_offtake_per_day = (bought_per_day or 0.0) + shuttled_out_per_day if has_offtake_data else None
            pct = None
            if effective_supply_per_day is not None and effective_supply_per_day > 0 \
                    and total_offtake_per_day is not None:
                pct = round(100 * total_offtake_per_day / effective_supply_per_day, 1)
            if effective_supply_per_day is not None and effective_supply_per_day > 0:
                total_supply += effective_supply_per_day
                total_offtake_of_supply += total_offtake_per_day or 0.0
            if has_offtake_data:
                any_offtake_known = True
            # The same balance from the newest log window alone: what bots really took, not what they would take
            # running flat out. A window always knows its bots' txns (none is a fact), so only "no window" is unknown.
            bought_observed = rates["observed_bought"].get(key, 0.0) if has_window else None
            sold_observed = rates["observed_sold"].get(key, 0.0) if has_window else None
            pct_observed = None
            if has_window:
                supply_observed = (prod or 0.0) + (sold_observed or 0.0) + shuttled_in_per_day
                offtake_observed = (bought_observed or 0.0) + shuttled_out_per_day
                if supply_observed > 0:
                    pct_observed = round(100 * offtake_observed / supply_observed, 1)
                    total_supply_observed += supply_observed
                    total_offtake_observed += offtake_observed
            balance[key] = {
                "production_per_day": round(prod, 1) if prod is not None else None,
                "bought_per_day": round(bought_per_day, 1) if bought_per_day is not None else None,
                "sold_per_day": round(sold_per_day, 1) if sold_per_day is not None else None,
                "shuttled_per_day": round(shuttled_out_per_day, 1),
                "shuttled_to": _station_labels(out_info["to"]) if out_info else [],
                "shuttled_in_per_day": round(shuttled_in_per_day, 1),
                "shuttled_from": _station_labels(in_info["from"]) if in_info else [],
                "pct_satisfied": pct,
                "bought_observed_per_day": round(bought_observed, 1) if bought_observed is not None else None,
                "sold_observed_per_day": round(sold_observed, 1) if sold_observed is not None else None,
                "pct_observed": pct_observed,
            }
        entry["resource_balance"] = balance
        entry["overall_pct_observed"] = (
            round(100 * total_offtake_observed / total_supply_observed, 1)
            if total_supply_observed > 0 else None)
        entry["overall_pct_satisfied"] = (
            round(100 * total_offtake_of_supply / total_supply, 1)
            if any_offtake_known and total_supply > 0 else None)

    bot_out: dict[str, dict] = {}
    for name, entry in bots.items():
        docks, txns = entry["docks"], entry["txns"]
        roster = bot_roster.get((entry["nickname"], entry["tag"])) or bot_roster.get((entry["nickname"], ""))
        bot_out[name] = {
            "tag": entry["tag"], "nickname": entry["nickname"], "docks": docks, "txns": txns,
            "stations_visited": len(entry["stations"]),
            "txn_per_dock": round(txns / docks, 2) if docks else None,
            "ship_type": roster["ship_type"] if roster else None,
            "state": roster["state"] if roster else None,
            "cargo_cap": roster["cargo_cap"] if roster else None,
            "cargo_used": roster["cargo_used"] if roster else None,
            "haul_order_count": roster["haul_order_count"] if roster else None,
            "roster_known": roster is not None,
        }

    accounts = {f"TB-{u}": dict(v) for u, v in trade_bot_slots.items()}

    return {"stations": stations, "bots": bot_out, "accounts": accounts}

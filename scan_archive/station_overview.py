"""station_overview.py - the STATIONS tab's data: every station you have docked at, as of its last visit, set against
what the bots do with its production.

Pure: dicts in, a JSON-friendly dict out (scan_dashboard embeds it in the page). Inputs, all optional so a fresh install
still renders:

  profiles         station_profile.load_profiles(): ``{station id: snapshot taken when you left it}``
  activity         bot_activity.bot_activity_summary(): per station, the production per day, what bots bought and
                   shuttles moved out per day, and ``pct_satisfied`` - all worked out from the station logs and the
                   station-setup scans
  bot_fleet_rows   bot_fleet_scan_logger.load_bot_fleet_scan(): every bot's CURRENT haul orders. A bot that BUYs or
                   LOADs a resource at a station is a route for that resource (UNLOAD / SELL stops are not)
  station_names / station_systems   id -> name / system, for stations seen only in the logs

For each station: who owns it, who may dock and manage, its modules, workers, hull, credits, construction queue, trade
entries, and per resource the stock, cap, production per day, how much of it leaves per day (bots + shuttles), which
bots are routed to take it, and a status. What leaves is given two ways: ``bought_observed_per_day`` is what bots
really took in the newest stretch of the station's log (``log_window``), ``bought_per_day`` is what they would take
running non-stop at their loop time. A bot is usually not logged in all day, so the second runs well ahead of the
first. Status, issues and coverage follow the observed figure (``pct_leaves``, with ``basis`` "observed") and fall
back to the non-stop one ("running") for a station with no log window of 3 hours or more. ``bots`` lists every bot
seen taking or leaving something at the station, its loop time and the same two rates, whoever owns it. ``issues`` lists what looks wrong (a produced resource nothing is routed
to take, a resource sitting at its cap, a full hull, a snapshot that is old or missing).

``slots`` checks the extractors against the planet: the scan of the body a station is built on says how many extractor
slots each station on it has per resource (``bodies``: scan_dashboard.planet_records()), and the snapshot's modules say
how many extractors are fitted. A station is "short" when any resource has fewer extractors than slots; the lost output
is estimated from the rate of the extractors already fitted for that resource.

``gear`` says which marked station modules (shield, energy, weapons, sensor, ...: types ending ``_mk1`` / ``_mk2`` /
``_mk3``) are FITTED, from the station's newest station-setup scan. That scan keeps the game's own fitted counts
(``equipped_module_counts``) and is rewritten whenever they change, so it is current as of the last visit that changed
the gear; the snapshot's ``modules`` cannot say, because they list spares in storage too (a colony hub can hold thirty
Mk II shields it does not use). Each fitted module carries ``top``, the highest mark of its family seen anywhere in the
data, and ``behind`` when it is below it; a family with only one mark is never behind.

Production per day comes from the modules' own cycle stats (bot_activity._extractor_output_per_unit_per_day): only pure
extractors count, as in the Bot Activity tab.
"""

from __future__ import annotations

import re
import time

import bot_activity
from scan_data import compact_cargo

MINOR_PER_DAY = 100.0          # a resource producing less than this, or under MINOR_SHARE of the station's output, is minor
MINOR_SHARE = 0.005
STALE_DAYS = 3.0
VERY_STALE_DAYS = 14.0
AT_CAP_PCT = 95.0
BACKED_UP_PCT = bot_activity.BACKED_UP_HULL_FILL_PCT
OK_PCT, PARTIAL_PCT, NO_ROUTE_PCT = 90.0, 50.0, 5.0
PICKUP_MODES = ("BUY", "LOAD")
MIN_PREFIX = 3                 # shortest abbreviation of a planet's name that may stand for it
SHORT_BAD_SHARE = 0.5          # this share of a station's slots, or more, unfilled is a problem and not a warning
USED_FILL_PCT = 25.0           # a resource under this share of its cap ...
USED_WITHIN_DAYS = 2.0         # ... at a station that makes its whole cap in less than this is being used on the spot
# "cryldorwen - 1" or "cryldorwen - 1 - col", and the renamed "C/G/M/O/S/T Norv 3" (resource letters, body, number)
_NAME_PATTERNS = (re.compile(r"^(?P<base>.+?) - \d+(?: - col)?$"), re.compile(r"^(?:[A-Z/]+ )?(?P<base>.+?) \d+$"))


def pretty(key) -> str:
    return str(key).split(":")[-1].replace("_", " ").strip().title() or str(key)


def _sid(value) -> str:
    return "" if value is None else str(value)


def routes_from_fleet(bot_fleet_rows) -> dict:
    """``{(station id, item key): [bot, ...]}`` for every current pickup order. ``item key`` is "" for an order
    that names no item. A bot's latest row wins; a row whose data is empty means the bot is gone."""
    latest: dict = {}
    for row in bot_fleet_rows or ():
        if not isinstance(row, dict):
            continue
        key = _sid(row.get("key"))
        if key and (key not in latest or (row.get("captured_at") or 0) >= (latest[key].get("captured_at") or 0)):
            latest[key] = row
    routes: dict = {}
    for key, row in latest.items():
        data = row.get("data")
        if not isinstance(data, dict):
            continue
        orders = [o for o in (data.get("haul_orders") or ()) if isinstance(o, dict)]
        bot = {"id": key, "nickname": str(data.get("nickname") or key),
               "account": str((row.get("context") or {}).get("username") or ""),
               "ship": str(data.get("ship_type") or ""), "cargo_cap": data.get("bot_cargo_cap") or 0,
               "state": str(data.get("state") or ""), "system": str(data.get("system_name") or ""),
               "stops": len({_sid(o.get("station_id")) for o in orders if o.get("type") == "dock"})}
        seen = set()
        for order in orders:
            if order.get("type") != "trade" or str(order.get("mode", "")).upper() not in PICKUP_MODES:
                continue
            pair = (_sid(order.get("station_id")), str(order.get("item_key") or ""))
            if pair[0] and pair not in seen:
                seen.add(pair)
                routes.setdefault(pair, []).append(bot)
    return routes


def _production(modules, known_keys) -> dict:
    """Raw resource output per day from equipped modules' cycle stats."""
    out: dict = {}
    for module in modules or ():
        result = bot_activity._extractor_output_per_unit_per_day({"stats": module.get("stats") or {}})
        if result is None:
            continue
        name, per_unit = result
        count = int(module.get("quantity") or 0)
        if count > 0:
            key = bot_activity._resolve_resource_key(name, known_keys)
            out[key] = out.get(key, 0.0) + per_unit * count
    return {k: round(v, 1) for k, v in out.items()}


def _body_index(bodies) -> dict:
    return {(str(b.get("system", "")).lower(), str(b.get("planet_name", "")).lower()): b
            for b in bodies or () if isinstance(b, dict)}


def _find_body(profile, station_name, system, index):
    """(scan record or None, body name or None, how it was matched). The snapshot names the body it was taken on;
    older snapshots and scan-only stations fall back to the station's own name."""
    system = str(system or "").lower()
    named = (profile or {}).get("body_name")
    if named:
        return index.get((system, str(named).lower())), str(named), "snapshot"
    bases = []
    for pattern in _NAME_PATTERNS:
        match = pattern.match(station_name or "")
        if match:
            bases.append(match.group("base"))
            if (system, match.group("base").lower()) in index:
                return index[(system, match.group("base").lower())], match.group("base"), "name"
    # a station renamed with an abbreviation ("Norv 3" for Norvendorok): the one body in the system that starts with it
    for base in bases:
        short = base.lower()
        candidates = [b for (sys, planet), b in index.items() if sys == system and len(short) >= MIN_PREFIX
                      and planet.startswith(short)]
        if len(candidates) == 1:
            return candidates[0], str(candidates[0].get("planet_name") or base), "name"
    return None, None, None


def _fitted_extractors(modules, known_keys) -> dict:
    """``{resource: {"count", "per_day"}}``: the extractors fitted and what they make per day together."""
    out: dict = {}
    for module in modules or ():
        result = bot_activity._extractor_output_per_unit_per_day({"stats": module.get("stats") or {}})
        count = int(module.get("quantity") or 0)
        if result is None or count <= 0:
            continue
        key = bot_activity._resolve_resource_key(result[0], known_keys)
        entry = out.setdefault(key, {"count": 0, "per_day": 0.0})
        entry["count"] += count
        entry["per_day"] += result[1] * count
    return out


def _latest_setups(colony_scan_rows) -> dict:
    """``{station id: row}``: each station's newest station-setup scan (colony_scan.jsonl)."""
    latest: dict = {}
    for row in colony_scan_rows or ():
        if isinstance(row, dict) and row.get("source") == "station_setup" and row.get("key"):
            key = _sid(row["key"])
            if key not in latest or (row.get("captured_at") or 0) >= (latest[key].get("captured_at") or 0):
                latest[key] = row
    return latest


def _scan_modules(colony_scan_rows) -> dict:
    """``{station id: [module, ...]}`` from each station's newest station-setup scan (colony_scan.jsonl): what was
    fitted when that scan was last rewritten, which can be well before the last visit."""
    return {key: compact_cargo((row.get("data") or {}).get("cargo"))["modules"]
            for key, row in _latest_setups(colony_scan_rows).items()}


# ---- station gear: which marks are fitted ----

_TYPE_TIER = re.compile(r"^(?P<family>.+)_mk(?P<mark>[0-9]+)$")
_MARK_SUFFIX = re.compile(r"\s+Mk\.?\s*[IVXLCDM0-9]+\s*$", re.IGNORECASE)


def tier_of(item_type):
    """``(family, mark)`` for a marked module type ("station_beam_cannon_mk2" -> ("station_beam_cannon", 2)), else None."""
    match = _TYPE_TIER.match(str(item_type or ""))
    return (match["family"], int(match["mark"])) if match else None


def _fitted_counts(data):
    """``{module type: how many are fitted}`` from a PS_STATION_CARGO payload, or None when it says nothing about it."""
    counts = data.get("equipped_module_counts")
    if isinstance(counts, dict):
        out = {}
        for key, value in counts.items():
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if key and number > 0:
                out[str(key)] = number
        return out
    types = data.get("equipped_module_types")
    if isinstance(types, list):                   # an older server's list of types, one entry per module fitted
        out = {}
        for item_type in types:
            if item_type:
                out[str(item_type)] = out.get(str(item_type), 0) + 1
        return out
    return None


def _scan_gear(latest_setups) -> dict:
    """``{station id: {"counts", "names", "types", "captured_at"}}``: what each station had fitted when its setup scan
    was last rewritten, the display name of each station item in its cargo, and every marked type it held."""
    out: dict = {}
    for key, row in latest_setups.items():
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        counts = _fitted_counts(data)
        if counts is None:
            continue
        names, types = {}, set(counts)
        for item in data.get("cargo") or ():
            if not isinstance(item, dict) or not str(item.get("item_key") or "").startswith("item:station:"):
                continue
            item_type = str(item.get("item_type") or str(item["item_key"]).split(":")[-1])
            if item.get("display_name"):
                names[item_type] = str(item["display_name"])
            types.add(item_type)
        out[key] = {"counts": counts, "names": names, "types": {t for t in types if tier_of(t)},
                    "captured_at": row.get("captured_at")}
    return out


def _family_tops(scan_gear, profiles) -> dict:
    """``{family: highest mark seen}`` over every station's cargo, fitted or in storage, in the scans and snapshots."""
    tops: dict = {}

    def see(item_type):
        parsed = tier_of(item_type)
        if parsed:
            tops[parsed[0]] = max(tops.get(parsed[0], 0), parsed[1])

    for gear in scan_gear.values():
        for item_type in gear["types"]:
            see(item_type)
    for profile in profiles.values():
        for module in profile.get("modules") or ():
            see(str(module.get("item_key") or "").split(":")[-1])
    return tops


def gear_of(scan, tops, now) -> dict:
    """The marked gear fitted at a station: ``{"known": False}`` without a setup scan, else ``known``, ``seen_at`` /
    ``age_days`` of the scan, ``fitted`` (type, family, label, mark, count, top, behind; by label then mark),
    ``marks`` (``{"mark", "count"}`` per mark fitted, lowest first) and ``behind`` (modules below their family's top)."""
    if scan is None:
        return {"known": False}
    fitted = []
    for item_type, count in scan["counts"].items():
        parsed = tier_of(item_type)
        if parsed is None:
            continue
        family, mark = parsed
        label = _MARK_SUFFIX.sub("", scan["names"].get(item_type, "")).strip() or pretty(family)
        top = max(tops.get(family, mark), mark)
        fitted.append({"type": item_type, "family": family, "label": label, "mark": mark, "count": count, "top": top,
                       "behind": mark < top})
    fitted.sort(key=lambda m: (m["label"].lower(), m["mark"]))
    per_mark: dict = {}
    for module in fitted:
        per_mark[module["mark"]] = per_mark.get(module["mark"], 0) + module["count"]
    seen_at = scan.get("captured_at")
    return {"known": True, "seen_at": seen_at, "age_days": round((now - seen_at) / 86400.0, 2) if seen_at else None,
            "fitted": fitted, "marks": [{"mark": m, "count": per_mark[m]} for m in sorted(per_mark)],
            "behind": sum(m["count"] for m in fitted if m["behind"])}


def slot_check(profile, station_name, system, index, known_keys, scan_modules=None) -> dict:
    """Extractors fitted against the slots the planet gives this station. ``status`` is "ok", "short",
    "no-snapshot" (modules unknown), "no-body" (which planet it is on is unknown), "unscanned" (the planet has
    no scan), "no-slots" (the planet has no extractor slots) or "unattached" (a free-standing station: no planet).
    With no snapshot the modules of the older station-setup scan (``scan_modules``) are used, and ``from_scan`` is
    set."""
    modules = (profile or {}).get("modules") if profile else scan_modules
    if modules is None:
        return {"status": "no-snapshot"}
    if profile and "planet_id" in profile and profile["planet_id"] is None and not profile.get("body_name"):
        return {"status": "unattached"}                  # a Central Command or other free-standing station: no planet
    body, name, how = _find_body(profile, station_name, system, index)
    if body is None:
        return {"status": "unscanned" if name else "no-body", "body": name, "from_scan": not profile}
    slots = {k: int(v or 0) for k, v in (body.get("resources") or {}).items() if int(v or 0) > 0}
    fitted = _fitted_extractors(modules, known_keys | set(slots))
    rows = []
    for key in set(slots) | set(fitted):
        have = fitted.get(key, {"count": 0, "per_day": 0.0})
        want = slots.get(key, 0)
        missing = max(0, want - have["count"])
        rate = have["per_day"] / have["count"] if have["count"] else None
        rows.append({"key": key, "label": pretty(key), "slots": want, "fitted": have["count"], "missing": missing,
                     "extra": max(0, have["count"] - want),
                     "lost_per_day": round(missing * rate, 1) if rate is not None else None})
    rows.sort(key=lambda r: (-r["missing"], r["label"]))
    total = sum(r["slots"] for r in rows)
    missing = sum(r["missing"] for r in rows)
    return {"status": "short" if missing else ("ok" if total else "no-slots"), "body": name, "kind": body.get("stations") == 1 and "moon" or "planet",
            "matched": how, "rows": rows, "slots_total": total, "fitted_total": total - missing,
            "missing_total": missing, "from_scan": not profile}


def _modules_summary(modules) -> list:
    totals: dict = {}
    for module in modules or ():
        totals[module.get("name")] = totals.get(module.get("name"), 0) + int(module.get("quantity") or 0)
    return [{"name": str(n), "quantity": q} for n, q in sorted(totals.items(), key=lambda kv: (-kv[1], str(kv[0])))
            if q > 0]


def _status(production, pct, routed, quiet=False, used=False) -> str:
    """ok / partial / low by how much of the production leaves; below NO_ROUTE_PCT it is "used" when the stock shows the
    station consumes it itself, otherwise "idle" when a bot is routed to take it and "no-route" when none is; a resource
    that is only held is "stock". With no figure for what leaves (no station-log data) it is "routed" or "unknown" -
    never "no-route", which would claim more than is known - unless ``quiet``: the station's log was fetched and no bot
    ever docks in it, so nothing leaving is a fact and not a gap."""
    if production <= 0:
        return "stock"
    if pct is None:
        if not quiet:
            return "routed" if routed else "unknown"
        pct = 0.0
    if pct >= OK_PCT:
        return "ok"
    if pct >= PARTIAL_PCT:
        return "partial"
    if pct >= NO_ROUTE_PCT:
        return "low"
    if used:
        return "used"
    return "idle" if routed else "no-route"


def _resource_rows(profile, entry, routes, station_id, known_keys) -> list:
    """One row per resource the station holds, produces or has capped."""
    if profile is not None:
        stock = dict(profile.get("resources") or {})
        caps = dict(profile.get("resource_caps") or {})
        reserves = dict(profile.get("resource_reserves") or {})
        production = _production(profile.get("modules"), known_keys)
    else:
        entry = entry or {}
        stock, caps = dict(entry.get("resources") or {}), dict(entry.get("resource_caps") or {})
        reserves = dict(entry.get("resource_reserves") or {})
        production = dict(entry.get("resource_production_per_day") or {})
    balance = (entry or {}).get("resource_balance") or {}
    quiet = bool((entry or {}).get("log_rows")) and not (entry or {}).get("bot_docks")
    keys = set(production) | {k for k, v in stock.items() if v}
    keys.discard("workers")
    rows = []
    for key in sorted(keys, key=lambda k: (-(production.get(k, 0)), k)):
        info = balance.get(key) or {}
        pct_running, pct_observed = info.get("pct_satisfied"), info.get("pct_observed")
        pct = pct_observed if pct_observed is not None else pct_running
        prod = float(production.get(key, 0.0))
        cap = caps.get(key)
        have = int(stock.get(key, 0) or 0)
        routed = (routes.get((station_id, key)) or []) + (routes.get((station_id, "")) or [])
        fill = round(100.0 * have / cap, 1) if cap else None
        # Only a snapshot's stock is current enough to say this; a scan's may be days old.
        used = profile is not None and bool(cap) and fill < USED_FILL_PCT and prod >= cap / USED_WITHIN_DAYS
        # Bots that, going by the whole log, do take this resource are "routed" for the status even when none is
        # in the user's own fleet scan - and when the newest window shows little leaving, "idle" is the honest word.
        taken = bool(routed) or (pct_running or 0.0) >= NO_ROUTE_PCT
        rows.append({
            "key": key, "label": pretty(key), "stock": have, "cap": cap, "reserve": reserves.get(key),
            "fill_pct": fill, "production_per_day": prod,
            "bought_per_day": float(info.get("bought_per_day") or 0.0),
            "bought_observed_per_day": (None if info.get("bought_observed_per_day") is None
                                        else float(info["bought_observed_per_day"])),
            "shuttled_per_day": float(info.get("shuttled_per_day") or 0.0),
            "shuttled_to": list(info.get("shuttled_to") or []),
            "pct_satisfied": pct_running, "pct_observed": pct_observed, "pct_leaves": pct,
            "basis": "observed" if pct_observed is not None else ("running" if pct_running is not None else None),
            "routes": [{"nickname": b["nickname"], "account": b["account"], "ship": b["ship"],
                        "cargo_cap": b["cargo_cap"], "stops": b["stops"], "state": b["state"]} for b in routed],
            "status": _status(prod, pct, taken, quiet, used),
        })
    return rows


def _leaves_text(row, station) -> str:
    """How much of a resource's production leaves, in words: the newest log window's figure with the non-stop one
    beside it, or just the plain figure when the station has no window to measure."""
    pct = row["pct_leaves"] or 0.0
    window = station.get("log_window")
    if row["basis"] == "observed" and window:
        text = f"{pct:.0f}% of production left in the newest {window['hours']:.0f} h of its log"
        if row["pct_satisfied"] is not None:
            text += f" ({row['pct_satisfied']:.0f}% if its bots ran non-stop)"
        return text
    return f"{pct:.0f}% of production leaves the station"


def _issues(station) -> list:
    """What looks wrong. Warnings that rest on stock figures need a docked snapshot: from an older scan they are only
    ``info``. A resource that is a trickle next to the station's output (copper at 17 a day beside millions of corn) is
    ``info`` as well, so it does not drown the real problems."""
    issues = []
    fresh = station["source"] == "profile"
    total = sum(r["production_per_day"] for r in station["resources"])
    old = "" if fresh else " (from an older scan, may be out of date)"

    def severity(level, row=None):
        minor = row is not None and row["production_per_day"] < max(MINOR_PER_DAY, MINOR_SHARE * total)
        return "info" if minor else level

    age = station["age_days"]
    if station["source"] != "profile":
        issues.append({"code": "no-snapshot", "severity": "info",
                       "text": "No docked snapshot yet - dock here once to capture the full setup."})
    elif age is not None and age >= VERY_STALE_DAYS:
        issues.append({"code": "very-stale", "severity": "bad", "text": f"Snapshot is {age:.0f} days old."})
    elif age is not None and age >= STALE_DAYS:
        issues.append({"code": "stale", "severity": "warn", "text": f"Snapshot is {age:.0f} days old."})
    unknown = [r["label"] for r in station["resources"] if r["status"] in ("unknown", "routed")
               and r["production_per_day"] >= max(MINOR_PER_DAY, MINOR_SHARE * total)]
    if unknown:
        issues.append({"code": "no-log-data", "severity": "info",
                       "text": "No station-log data for " + ", ".join(unknown[:4]) + (", ..." if len(unknown) > 4 else "")
                       + ": what leaves is unknown (open this station's Logs tab once)."})
    slots = station.get("slots") or {}
    if slots.get("status") == "short":
        worst_rows = [r for r in slots["rows"] if r["missing"]][:4]
        lost = sum(r["lost_per_day"] or 0 for r in slots["rows"])
        text = ", ".join(f"{r['label']} {r['fitted']}/{r['slots']}" for r in worst_rows)
        share = slots["missing_total"] / slots["slots_total"] if slots["slots_total"] else 0
        issues.append({"code": "unfilled-slots",
                       "severity": "bad" if share >= SHORT_BAD_SHARE and not slots["from_scan"] else "warn",
                       "text": f"{slots['missing_total']} of {slots['slots_total']} extractor slots on {slots['body']} are "
                               f"empty: {text}{', ...' if len([r for r in slots['rows'] if r['missing']]) > 4 else ''}"
                               + (f" (about {lost:,.0f}/day lost)" if lost else "") + "."
                       + (" From an older scan: dock here to confirm." if slots["from_scan"] else "")})
    elif slots.get("status") == "unscanned":
        issues.append({"code": "unscanned-body", "severity": "info",
                       "text": f"{slots['body']} has no planet scan, so its extractor slots cannot be checked."})
    elif slots.get("status") == "no-body" and station["source"] == "profile" and not slots.get("from_scan"):
        issues.append({"code": "unknown-body", "severity": "info",
                       "text": "Which planet this station is built on is not known (dock again with the system loaded)."})
    fill = station.get("hull_fill_pct")
    if fill is not None and fill >= BACKED_UP_PCT:
        issues.append({"code": "backed-up", "severity": "bad" if fresh else "info",
                       "text": f"Hull {fill:.0f}% full - extraction will stall.{old}"})
    for row in station["resources"]:
        if row["production_per_day"] > 0 and row["fill_pct"] is not None and row["fill_pct"] >= AT_CAP_PCT:
            issues.append({"code": "at-cap", "severity": severity("warn" if fresh else "info", row),
                           "resource": row["key"],
                           "text": f"{row['label']} is at {row['fill_pct']:.0f}% of its cap - production is stalled.{old}"})
        if row["status"] == "no-route":
            seen = (f" Its log ({station['log_rows']:,} entries) shows no bot ever docking."
                    if station.get("log_rows") and not station.get("bot_docks") else "")
            issues.append({"code": "no-route", "severity": severity("bad", row), "resource": row["key"],
                           "text": f"{row['label']}: {row['production_per_day']:,.0f}/day produced, no bot or shuttle takes it."
                           + seen})
        elif row["status"] == "idle":
            issues.append({"code": "idle-route", "severity": severity("warn", row), "resource": row["key"],
                           "text": f"{row['label']}: bots take it but little leaves - {_leaves_text(row, station)}."})
        elif row["status"] == "low":
            issues.append({"code": "low-coverage", "severity": severity("warn", row), "resource": row["key"],
                           "text": f"{row['label']}: only {_leaves_text(row, station)}."})
    return issues


def _station_bots(entry, bots, mine=()) -> list:
    """Every bot seen taking or leaving something at the station (from its log), whoever owns it: loop time here, and
    per resource the load per dock and the units a day it moved - as seen in the newest log window, and if it ran
    non-stop. ``mine``: the bot's account is one of the user's (an account the fleet scan has heard from); ``in_fleet``:
    the bot itself is in that fleet scan - a bot of the user's that is not has been retired or sold (``last_seen``
    is when it last docked here). Busiest in the newest window first, then the most recent."""
    out = []
    for key, bot in ((entry or {}).get("bot_breakdown") or {}).items():
        rates = bot.get("resource_rates") or {}
        if not rates:
            continue
        rows = [{"key": k, "label": pretty(k), "per_dock": r.get("per_dock"),
                 "running_per_day": r.get("running_per_day"), "observed_per_day": r.get("observed_per_day")}
                for k, r in rates.items()]
        rows.sort(key=lambda r: (-(r["observed_per_day"] or 0), -(r["running_per_day"] or 0), r["label"]))
        known = (bots or {}).get(key)
        out.append({"name": key, "nickname": bot.get("nickname") or key, "account": bot.get("tag") or "",
                    "mine": (bot.get("tag") or "") in mine, "last_seen": bot.get("last_dock"),
                    "docks": bot.get("docks") or 0, "window_docks": bot.get("window_docks") or 0,
                    "loop_seconds": bot.get("loop_seconds"),
                    "in_fleet": None if known is None else bool(known.get("roster_known")), "rows": rows})
    out.sort(key=lambda b: (-sum(r["observed_per_day"] or 0 for r in b["rows"]), -(b["last_seen"] or 0),
                            -sum(r["running_per_day"] or 0 for r in b["rows"]), b["name"]))
    return out


def _hauled(row) -> bool:
    """Whether a row counts towards coverage: it makes something, and it is a question of hauling it away - not output
    the station uses itself ("used"), nor one whose fate is not known ("unknown" / "routed": no log figure)."""
    return row["production_per_day"] > 0 and row["status"] not in ("used", "unknown", "routed")


def _coverage(rows):
    rows = [r for r in rows if _hauled(r)]
    total = sum(r["production_per_day"] for r in rows)
    if total <= 0:
        return None
    covered = sum(r["production_per_day"] * min(100.0, (r["pct_leaves"] or 0.0)) / 100.0 for r in rows)
    return round(100.0 * covered / total, 1)


def station_overview(profiles=None, activity=None, bot_fleet_rows=None, station_names=None, station_systems=None,
                     now: "float | None" = None, bodies=None, colony_scan_rows=None) -> dict:
    """See the module docstring."""
    now = time.time() if now is None else now
    profiles = {_sid(k): v for k, v in (profiles or {}).items() if isinstance(v, dict)}
    by_id = {}
    for name, entry in ((activity or {}).get("stations") or {}).items():
        if isinstance(entry, dict) and entry.get("station_id") is not None:
            by_id[_sid(entry["station_id"])] = {**entry, "name": name}
    names, systems = station_names or {}, station_systems or {}
    routes = routes_from_fleet(bot_fleet_rows)
    mine_tags = {f"TB-{(row.get('context') or {}).get('username')}" for row in bot_fleet_rows or ()
                 if isinstance(row, dict) and (row.get("context") or {}).get("username")}
    body_index = _body_index(bodies)
    scan_modules = _scan_modules(colony_scan_rows)
    scan_gear = _scan_gear(_latest_setups(colony_scan_rows))
    gear_tops = _family_tops(scan_gear, profiles)
    known_keys = set()
    for profile in profiles.values():
        known_keys |= set(profile.get("resources") or ())
    for entry in by_id.values():
        known_keys |= set(entry.get("resources") or ())

    stations = []
    for station_id in sorted(set(profiles) | set(by_id), key=lambda s: (len(s), s)):
        profile, entry = profiles.get(station_id), by_id.get(station_id)
        rows = _resource_rows(profile, entry, routes, station_id, known_keys)
        seen_at = profile.get("seen_at") if profile else None
        hull_used = profile.get("hull_used") if profile else (entry or {}).get("hull_used")
        hull_space = profile.get("hull_space") if profile else (entry or {}).get("hull_space")
        fill = round(100.0 * hull_used / hull_space, 1) if hull_used is not None and hull_space else None
        modules = _modules_summary(profile.get("modules")) if profile else []
        station = {
            "id": station_id,
            "name": (profile or {}).get("station_name") or (entry or {}).get("name") or names.get(station_id)
            or f"Station {station_id}",
            "system": (profile or {}).get("system_name") or (entry or {}).get("system") or systems.get(station_id) or "",
            "source": "profile" if profile else ("scan" if entry else "none"),
            "seen_at": seen_at,
            "age_days": round((now - seen_at) / 86400.0, 2) if seen_at else None,
            "owner": (profile or {}).get("owner_name", ""), "taken_by": (profile or {}).get("taken_by", ""),
            "docking": (profile or {}).get("docking"), "management": (profile or {}).get("management"),
            "motd": (profile or {}).get("motd", ""), "has_colony": bool((profile or {}).get("has_colony")),
            "flags": (profile or {}).get("flags") or {},
            "credits": (profile or {}).get("credits"), "workers_used": (profile or {}).get("workers_used"),
            "workers": int(((profile or {}).get("resources") or {}).get("workers", 0) or 0),
            "hull_used": hull_used, "hull_space": hull_space, "hull_fill_pct": fill,
            "modules": modules,
            "extractors": sum(m["quantity"] for m in (profile or {}).get("modules", ())
                              if bot_activity._extractor_output_per_unit_per_day({"stats": m.get("stats") or {}})),
            "construction": (profile or {}).get("construction"),
            "trade_entries": len(profile["trade_entries"]) if profile and profile.get("trade_entries") is not None
            else None,
            "trade_listed": (profile or {}).get("trade_listed"),
            "resources": rows,
            "bot_docks": (entry or {}).get("bot_docks"), "unique_bots": (entry or {}).get("unique_bots"),
            "log_rows": (entry or {}).get("log_rows"), "log_window": (entry or {}).get("log_window"),
            "bots": _station_bots(entry, (activity or {}).get("bots"), mine_tags),
            "shuttle_covered": (entry or {}).get("shuttle_covered"),
            "coverage_pct": _coverage(rows),
        }
        station["slots"] = slot_check(profile, station["name"], station["system"], body_index, known_keys,
                                      scan_modules.get(station_id))
        station["gear"] = gear_of(scan_gear.get(station_id), gear_tops, now)
        station["issues"] = _issues(station)
        stations.append(station)

    by_system: dict = {}
    for station in stations:
        system = by_system.setdefault(station["system"] or "Unknown", {
            "name": station["system"] or "Unknown", "stations": 0, "snapshots": 0, "issues": 0, "bad": 0, "short": 0,
            "production": {}, "removed": {}})
        system["stations"] += 1
        system["snapshots"] += station["source"] == "profile"
        system["short"] = system.get("short", 0) + ((station.get("slots") or {}).get("status") == "short")
        system["issues"] += len([i for i in station["issues"] if i["severity"] != "info"])
        system["bad"] += len([i for i in station["issues"] if i["severity"] == "bad"])
        for row in station["resources"]:
            if _hauled(row):
                system["production"][row["key"]] = system["production"].get(row["key"], 0.0) + row["production_per_day"]
                system["removed"][row["key"]] = system["removed"].get(row["key"], 0.0) + (
                    row["production_per_day"] * min(100.0, row["pct_leaves"] or 0.0) / 100.0)
    return {"generated_at": now, "stations": stations,
            "systems": sorted(by_system.values(), key=lambda s: s["name"]),
            "resources": sorted({r["key"] for s in stations for r in s["resources"]})}

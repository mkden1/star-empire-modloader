"""station_probe.py - /stationprobe: does the server answer a station's data when you are not docked there?

The Logs tab of a station you are docked at sends ``PS_LOGS_REQUEST {station_id, query, before, request_id}`` and
ignores any reply that is not for the station it has open. Docking at a station you manage also sends the plain
commands ``PS_REQUEST_CARGO <id>`` and ``PS_REQUEST_CONSTRUCTION_BAY <id>``. This asks the same question for a
station id you type, once, and says what came back, so it can be found out whether the server only checks ownership or
also needs you docked (the dev agreed to this test, 2026-10-31; the logs answered "no permission" undocked).

  /stationprobe <station id>             send one PS_LOGS_REQUEST for that station and report the answer
  /stationprobe cargo <station id>       send PS_REQUEST_CARGO (only while NOT docked) and report what the reply holds
  /stationprobe construction <station id>   the same for PS_REQUEST_CONSTRUCTION_BAY
  /stationprobe rename <station id>      send PS_RENAME_STATION for one of YOUR stations with the name it already has
  /stationprobe list [text]              your stations (Stats > Possessions > Stations must have loaded) with their ids

The logs request is the one the Logs tab sends (empty search, newest page); only the station is yours to choose. The
reply is read here and never reaches the Logs tab. The cargo and construction replies carry no station id, so they are
only sent while you are not docked (nothing of yours is then being shown that a swallowed reply could leave stale),
and the first PS_STATION_CARGO / PS_CONSTRUCTION_BAY that arrives within TIMEOUT_S is taken as the answer. One probe at
a time, at least MIN_GAP_S apart; no reply within TIMEOUT_S is reported too (a refusal may arrive as a plain line in the
Log chat tab instead). The result also goes to the client log, with the context it was sent in (docked where, or not
docked, and the system).

The rename probe is the one that WRITES, so it is careful: the station must be in your Possessions list (and not a jump
gate), its current name is taken from that list and, when the station is in this system, from the live station list, and
the two must agree (a stale name would really rename it); the name sent is that same name, so if the server accepts it
nothing changes. The Rename dialog's own request is the one sent ({station_id, name}); PS_RENAME_STATION_OK for that
station and name is the answer, and the game's own handler still runs on it. A refusal has no message type of its own
(it shows in the Log tab), so it reads as "no reply". Run it docked at the station first (it should be accepted), then
undocked, the way the logs probe was run.

Hooks (install, Client.py bottom): ``set_station_logs``, ``set_ps_station_cargo`` and ``set_ps_construction_bay`` (to
take the probe's own reply) and ``_station_logs_tick`` (the per-frame timeout check); ``install_protocol`` wraps
ProtocolMixin's ``_on_ps_rename_station_ok`` (the rename answer). Chat command: one marked line in Client.py. Kill switch:
STAR_EMPIRE_NO_STATION_PROBE=1.
"""

from __future__ import annotations

import json
import logging
import os
import time

logger = logging.getLogger(__name__)

REQUEST_ID_BASE = 9_000_000          # the Logs tab counts up from 1: these never collide with its own requests
TIMEOUT_S = 8.0
MIN_GAP_S = 3.0
LIST_MAX = 15

_failed = False


def _switch_off(where: str) -> None:
    global _failed
    if not _failed:
        logger.exception("station_probe: %s failed; the station probe is switched off for this session", where)
    _failed = True


def enabled() -> bool:
    return not _failed and os.environ.get("STAR_EMPIRE_NO_STATION_PROBE") != "1"


def reset_for_tests() -> None:
    global _failed
    _failed = False


def _say(win, text: str, colour=(140, 200, 255)) -> None:
    win.chat_log(text, colour)


def context(win) -> str:
    """Where the player is when the probe goes out: it decides what a result means."""
    with win._lock:
        overlay = getattr(win, "_station_overlay", None)
        docked = None
        if overlay and overlay.get("kind") == "player_station":
            data = overlay.get("data") or {}
            docked = data.get("attached_station_id", data.get("station_id"))
            name = data.get("station_name", "")
        system = (getattr(win, "_layout", None) or {}).get("system_name", "")
    where = f"in {system}" if system else "in an unknown system"
    if docked is None:
        return f"not docked, {where}"
    return f"docked at station {docked}{' (' + name + ')' if name else ''}, {where}"


def station_rows(win, text: str = "") -> tuple:
    """(matching rows, total) of the player's stations from Possessions: [(id, name, system)]."""
    with win._lock:
        rows = list(getattr(win, "_my_stations_data", None) or ())
    wanted = text.strip().lower()
    out = []
    for row in rows:
        if not isinstance(row, dict) or row.get("station_id") is None:
            continue
        name = str(row.get("display_name") or "Station")
        system = str(row.get("system_name") or "")
        if wanted and wanted not in f"{name} {system} {row.get('station_id')}".lower():
            continue
        out.append((row["station_id"], name, system))
    return out, len(rows)


KINDS = {"logs": ("PS_LOGS", "PS_LOGS_REQUEST"), "cargo": ("PS_STATION_CARGO", "PS_REQUEST_CARGO"),
         "construction": ("PS_CONSTRUCTION_BAY", "PS_REQUEST_CONSTRUCTION_BAY"),
         "rename": ("PS_RENAME_STATION_OK", "PS_RENAME_STATION")}


def own_station_name(win, station_id) -> "tuple[str | None, str]":
    """(the current name of one of your stations, "") - or (None, why it is not safe to send that name back).

    Possessions' list says whose it is and what it was called when the list was loaded; the live station list (this
    system only, kept up to date when a rename is confirmed) is fresher. When both know the station they must agree:
    sending a stale name back would rename the station for real."""
    with win._lock:
        rows = list(getattr(win, "_my_stations_data", None) or ())
        live = list(getattr(win, "_deployed_stations", None) or ())
    row = next((r for r in rows if isinstance(r, dict) and str(r.get("station_id")) == str(station_id)), None)
    if row is None:
        return None, (f"Station {station_id} is not in your Possessions list, so it is not sent (open Stats > Possessions "
                      f"> Stations; /stationprobe list shows your ids).")
    if row.get("jump_gate"):
        return None, f"Station {station_id} is a jump gate: it has a nickname, not a name this request changes."
    names = {str(row.get("display_name") or "").strip()}
    deployed = next((d for d in live if isinstance(d, dict)
                     and str(d.get("attached_station_id") or d.get("id")) == str(station_id)), None)
    if deployed is not None:
        names.add(str(deployed.get("display_name") or "").strip())
    names.discard("")
    if not names:
        return None, f"Station {station_id} has no name in the lists, so there is nothing safe to send."
    if len(names) > 1:
        return None, (f"The lists disagree on station {station_id}'s name ({' / '.join(sorted(names))}); open Stats > "
                      f"Possessions > Stations again so the name is current, then retry.")
    return names.pop(), ""


def is_docked(win) -> bool:
    with win._lock:
        overlay = getattr(win, "_station_overlay", None)
        return bool(overlay and overlay.get("kind") in ("player_station", "npc"))


def handle_command(win, message: str = "") -> None:
    """``/stationprobe``: see the module docstring."""
    if not enabled():
        _say(win, "The station probe is switched off.", (255, 180, 60))
        return
    try:
        args = str(message).split()[1:]
        if not args or args[0].lower() in ("help", "?"):
            _say(win, "Usage: /stationprobe <station id>   (asks for that station's activity log once)")
            _say(win, "       /stationprobe cargo <station id>   |   construction <station id>   (undocked only)")
            _say(win, "       /stationprobe rename <station id>   (one of yours: renames it to the name it has now)")
            _say(win, "       /stationprobe list [text]   (your stations and their ids)")
            return
        if args[0].lower() == "list":
            rows, total = station_rows(win, " ".join(args[1:]))
            if not total:
                _say(win, "No station list yet: open Stats > Possessions > Stations once, then try again.",
                     (255, 180, 60))
                return
            for station_id, name, system in rows[:LIST_MAX]:
                _say(win, f"  {station_id}  {name}  ({system})")
            more = len(rows) - LIST_MAX
            _say(win, f"{len(rows)} of {total} station(s) match" + (f"; first {LIST_MAX} shown." if more > 0 else "."))
            return
        kind = "logs"
        if args[0].lower() == "rename":
            kind, args = "rename", args[1:]
            if not args:
                _say(win, "Usage: /stationprobe rename <station id>", (255, 180, 60))
                return
        elif args[0].lower() in ("cargo", "construction"):
            kind, args = args[0].lower(), args[1:]
            if not args:
                _say(win, f"Usage: /stationprobe {kind} <station id>", (255, 180, 60))
                return
            if is_docked(win):
                _say(win, f"The {kind} probe only runs while you are not docked (its reply has no station id).",
                     (255, 180, 60))
                return
        try:
            station_id = int(args[0])
            if station_id < 0:
                raise ValueError
        except ValueError:
            _say(win, f"'{args[0]}' is not a station id. Try /stationprobe list.", (255, 180, 60))
            return
        send(win, station_id, kind)
    except Exception:
        _switch_off("handle_command()")


def send(win, station_id: int, kind: str = "logs") -> bool:
    """Send one request of ``kind`` (logs / cargo / construction) for ``station_id``; False if one is still waiting
    or it was too soon."""
    now = time.monotonic()
    probe = win.__dict__.get("_probe")
    if probe is not None and now - probe["sent"] < TIMEOUT_S:
        _say(win, f"Probe for station {probe['station_id']} is still waiting for its answer.", (255, 180, 60))
        return False
    last = win.__dict__.get("_probe_last_sent", -1e9)
    if now - last < MIN_GAP_S:
        _say(win, "Wait a moment between probes.", (255, 180, 60))
        return False
    name = None
    if kind == "rename":
        name, problem = own_station_name(win, station_id)
        if name is None:
            _say(win, problem, (255, 180, 60))
            return False
    number = win.__dict__.get("_probe_count", 0) + 1
    win._probe_count = number
    request_id = REQUEST_ID_BASE + number
    where = context(win)
    win._probe = {"request_id": request_id, "station_id": station_id, "sent": now, "where": where, "kind": kind,
                  "name": name}
    win._probe_last_sent = now
    if kind == "logs":
        payload = {"station_id": station_id, "query": "", "before": None, "request_id": request_id}
        win._send_json("PS_LOGS_REQUEST", payload)
        sent = "PS_LOGS_REQUEST " + json.dumps(payload, separators=(",", ":"))
    elif kind == "rename":
        payload = {"station_id": station_id, "name": name}
        win._send_json("PS_RENAME_STATION", payload)
        sent = "PS_RENAME_STATION " + json.dumps(payload, separators=(",", ":"))
    else:
        sent = f"{KINDS[kind][1]} {station_id}"
        win._send_fn(sent)
    logger.info("station_probe: sent %s (%s)", sent, where)
    _say(win, f"Probe sent for station {station_id} ({where}): {sent.split(' ')[0]}"
              + (f", to its own name '{name}' (nothing changes if the server accepts it)" if kind == "rename" else "")
              + ". The answer, or a server message, follows.")
    return True


def on_cargo(win, data) -> bool:
    """A PS_STATION_CARGO or PS_CONSTRUCTION_BAY reply while such a probe waits: True (and reported)."""
    probe = win.__dict__.get("_probe")
    if not probe or probe.get("kind") not in ("cargo", "construction") or not isinstance(data, dict):
        return False
    win._probe = None
    if probe["kind"] == "cargo":
        rows = data.get("cargo") or []
        detail = (f"{len(rows)} cargo row(s), hull {data.get('hull_used', '?')}/{data.get('hull_space', '?')}, "
                  f"credits {data.get('station_credits', 'n/a')}")
    else:
        detail = f"{len(data.get('queue') or [])} project(s), {len(data.get('blueprints') or [])} blueprint(s)"
    error = str(data.get("error") or "")
    verdict = f"answered with an error: {error}" if error else f"ANSWERED: {detail}"
    _say(win, f"Probe {probe['kind']} station {probe['station_id']} ({probe['where']}): {verdict}.",
         (255, 170, 80) if error else (120, 220, 160))
    logger.info("station_probe: %s reply for station %s (%s): %s", probe["kind"], probe["station_id"], probe["where"],
                json.dumps(data, separators=(",", ":"), default=str)[:800])
    return True


def on_rename(win, data) -> bool:
    """A PS_RENAME_STATION_OK while a rename probe waits, for the station and name it sent: True (and reported)."""
    probe = win.__dict__.get("_probe")
    if not probe or probe.get("kind") != "rename" or not isinstance(data, dict):
        return False
    if str(data.get("station_id")) != str(probe["station_id"]) or data.get("name") != probe.get("name"):
        return False
    win._probe = None
    _say(win, f"Probe rename station {probe['station_id']} ({probe['where']}): ACCEPTED - the server confirmed the "
              f"name '{probe['name']}' (unchanged).", (120, 220, 160))
    logger.info("station_probe: rename reply for station %s (%s): %s", probe["station_id"], probe["where"],
                json.dumps(data, separators=(",", ":"), default=str)[:800])
    return True


def on_reply(win, data) -> bool:
    """A PS_LOGS reply: True (and reported) if it is the probe's, False for anyone else's."""
    probe = win.__dict__.get("_probe")
    if not probe or probe.get("kind", "logs") != "logs" or data.get("request_id") != probe["request_id"]:
        return False
    win._probe = None
    rows = data.get("rows") or []
    error = str(data.get("error") or "")
    answered = data.get("station_id")
    if rows:
        verdict = f"ANSWERED with {len(rows)} log row(s)" + (", more available" if data.get("has_more") else "")
        colour = (120, 220, 160)
    elif error:
        verdict = f"answered with an error: {error}"
        colour = (255, 170, 80)
    else:
        verdict = "answered with no rows and no error (an empty log, or no access)"
        colour = (255, 200, 100)
    note = "" if answered in (None, probe["station_id"]) else f" [the reply says station {answered}]"
    _say(win, f"Probe station {probe['station_id']} ({probe['where']}): {verdict}.{note}", colour)
    if rows and isinstance(rows[0], dict):
        first = rows[0]
        _say(win, f"  newest: {first.get('time', '?')}  {str(first.get('text', ''))[:110]}")
    logger.info("station_probe: reply for station %s (%s): %s", probe["station_id"], probe["where"],
                json.dumps(data, separators=(",", ":"), default=str)[:800])
    return True


def tick(win, now: "float | None" = None) -> None:
    """Report a probe nothing answered."""
    probe = win.__dict__.get("_probe")
    if not probe or (time.monotonic() if now is None else now) - probe["sent"] < TIMEOUT_S:
        return
    win._probe = None
    reply = KINDS[probe.get("kind", "logs")][0]
    _say(win, f"Probe station {probe['station_id']} ({probe['where']}): no {reply} reply in {TIMEOUT_S:.0f} s. "
              f"Check the Log tab for a server message."
              + (" (The name sent was the station's own, so nothing changed either way.)"
                 if probe.get("kind") == "rename" else ""), (255, 170, 80))
    logger.info("station_probe: no reply for station %s (%s)", probe["station_id"], probe["where"])


def install_protocol(protocol_cls) -> bool:
    """Wrap ProtocolMixin's ``_on_ps_rename_station_ok`` so a rename probe sees its answer; the game's handler always
    runs afterwards, unchanged. Idempotent; True if hooked."""
    original = protocol_cls.__dict__.get("_on_ps_rename_station_ok")
    if original is None:
        logger.warning("station_probe: rename answers not hooked, the protocol class has no _on_ps_rename_station_ok")
        return False
    if getattr(original, "_station_probe", False):
        return False

    def _on_ps_rename_station_ok(self, payload, *args, **kwargs):
        if enabled():
            try:
                win = getattr(self, "_solar_window", None)
                if win is not None and (win.__dict__.get("_probe") or {}).get("kind") == "rename":
                    try:
                        data = json.loads(payload) if isinstance(payload, (str, bytes, bytearray)) else payload
                    except ValueError:
                        data = None            # not JSON: the game's own handler ignores it too
                    on_rename(win, data)
            except Exception:
                _switch_off("on_rename()")
        return original(self, payload, *args, **kwargs)

    _on_ps_rename_station_ok.__name__ = original.__name__
    _on_ps_rename_station_ok.__doc__ = original.__doc__
    _on_ps_rename_station_ok.__wrapped__ = original
    _on_ps_rename_station_ok._station_probe = True
    protocol_cls._on_ps_rename_station_ok = _on_ps_rename_station_ok
    return True


def install(window_cls) -> bool:
    """Wrap ``set_station_logs`` and ``_station_logs_tick``. Idempotent; True if hooked."""
    if window_cls.__dict__.get("_station_probe_installed"):
        return False
    missing = [name for name in ("set_station_logs", "_station_logs_tick", "set_ps_station_cargo",
                                 "set_ps_construction_bay") if not hasattr(window_cls, name)]
    if missing:
        logger.warning("station_probe: not installed, the window has no %s", ", ".join(missing))
        return False
    replies, ticker = window_cls.set_station_logs, window_cls._station_logs_tick
    cargo_setter, bay_setter = window_cls.set_ps_station_cargo, window_cls.set_ps_construction_bay

    def set_station_logs(self, data):
        if enabled() and isinstance(data, dict):
            try:
                if on_reply(self, data):
                    return None
            except Exception:
                _switch_off("on_reply()")
        return replies(self, data)

    def set_ps_station_cargo(self, data):
        if enabled():
            try:
                if (self.__dict__.get("_probe") or {}).get("kind") == "cargo" and on_cargo(self, data):
                    return None
            except Exception:
                _switch_off("on_cargo()")
        return cargo_setter(self, data)

    def set_ps_construction_bay(self, data):
        if enabled():
            try:
                if (self.__dict__.get("_probe") or {}).get("kind") == "construction" and on_cargo(self, data):
                    return None
            except Exception:
                _switch_off("on_cargo()")
        return bay_setter(self, data)

    def _station_logs_tick(self):
        result = ticker(self)
        if enabled():
            try:
                tick(self)
            except Exception:
                _switch_off("tick()")
        return result

    for name, wrapper, original in (("set_station_logs", set_station_logs, replies),
                                    ("_station_logs_tick", _station_logs_tick, ticker),
                                    ("set_ps_station_cargo", set_ps_station_cargo, cargo_setter),
                                    ("set_ps_construction_bay", set_ps_construction_bay, bay_setter)):
        wrapper.__wrapped__ = original
        wrapper.__doc__ = original.__doc__
        setattr(window_cls, name, wrapper)
    window_cls._station_probe_installed = True
    return True

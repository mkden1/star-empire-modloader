"""scan_dashboard.py — render scan_data/planet_scans.csv as a local HTML report.

``build_dashboard_html`` is pure (rows in, HTML string out) and unit tested.
``generate_and_open`` is the thin I/O wrapper the galaxy map's "VIEW SCANS"
button calls: reads the CSV, writes the HTML next to it, and opens it in the
default browser. Best-effort — must never raise, since it runs from a UI
click handler in the middle of a live game session.
"""

from __future__ import annotations

import csv
import html
import json
import logging
import os

import modloader
import webbrowser

from bot_activity import bot_activity_summary
# the read side of every scan file lives in core (scan_data.py), so the archive needs none of the recorder mods
from scan_data import (RESOURCE_KEYS, RESOURCE_LABELS, load_bot_fleet_scan, load_colony_scan, load_profiles,
                       load_shuttle_target_station_ids, load_station_log_rows, load_station_names,
                       load_station_systems, load_trade_bot_slots, shuttle_data_has_been_captured, stations_for_body)
from scan_planner import PLANNER_CSS, PLANNER_HTML, load_catalog, planner_script
from scan_stations import STATIONS_CSS, STATIONS_HTML, stations_script
from station_overview import station_overview

logger = logging.getLogger(__name__)

MODIFIER_CATEGORIES = ("Gravity", "Temperature", "Atmosphere", "Geology", "Ecology")


def _num(value: str):
    """CSV cells are strings; coerce numeric-looking ones back for JS sorting."""
    if value is None or value == "":
        return None
    try:
        f = float(value)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return value


def planet_records(rows: list[dict]) -> list[dict]:
    """Coerce raw CSV rows into JSON-friendly planet records.

    Shared by the HTML dashboard (embedded as a JS array) and
    galaxy_export.py's combined JSON export (dumped directly) — one
    definition of "what a scanned planet looks like as data," reused by
    both consumers.

    ``resources`` is extractor slots PER STATION, exactly as scanned (and as
    planet_scans.csv stores them). ``stations`` is how many stations the body can
    hold -- 3 for a planet, 1 for a moon -- and ``resource_totals`` is
    ``resources`` times that: every slot actually available on the body. The
    dashboard shows the totals; the per-station figure stays for anything that
    wants the raw scan.
    """
    records = []
    for r in rows:
        is_moon = bool(r.get("parent_planet"))
        stations = stations_for_body(is_moon)
        resources = {key: _num(r.get(f"{key}_slots")) or 0 for key in RESOURCE_KEYS}
        rec = {
            "system": r.get("system", ""),
            "planet_name": r.get("planet_name", ""),
            "planet_type": r.get("planet_type", ""),
            "parent_planet": r.get("parent_planet", ""),
            "colonization_index": _num(r.get("colonization_index")),
            "orbit_distance": _num(r.get("orbit_distance")),
            "orbit_period_seconds": _num(r.get("orbit_period_seconds")),
            # Raw orbital elements, alongside the human-facing period above —
            # combined with the export's top-level server_elapsed, these let
            # a planet's position be reconstructed at any moment via the
            # server's own formula (see galaxy_export.py's docstring).
            "orbit_speed": _num(r.get("orbit_speed")),
            "orbit_angle_seed": _num(r.get("orbit_angle_seed")),
            "resources": resources,
            "stations": stations,
            "resource_totals": {key: slots * stations for key, slots in resources.items()},
            "modifiers": [
                {
                    "category": cat,
                    "setting": r.get(f"{cat.lower()}_setting", ""),
                    "penalty_pct": _num(r.get(f"{cat.lower()}_penalty_pct")) or 0,
                }
                for cat in MODIFIER_CATEGORIES
                if r.get(f"{cat.lower()}_setting")
            ],
            "extra_modifiers": r.get("extra_modifiers", ""),
        }
        records.append(rec)
    return records


def galaxy_summary(rows: list[dict], explored_totals: dict | None = None) -> dict:
    """{system: {"total_planets": N, "types": {type: count}, "scanned_planets": N, "scanned": bool,
    "resources": {resource: total}, "hazard_rating": int | None}} -- one entry per system that's
    either been explored (via ``explored_totals``, e.g. export_for_claude.json's own
    "explored_systems" map) or has at least one real scan (via ``rows``, the same CSV rows
    build_dashboard_html already gets).

    ``hazard_rating`` comes only from ``explored_totals`` (a scan alone never tells you a system's
    hazard level) -- None when that system isn't covered there at all, distinct from a real,
    confirmed 0 (a genuinely safe/starter system).

    ``explored_totals`` needs NO scan at all -- the client already gets a full planet-type
    breakdown the moment a system is entered (or restored from the EXPLORED_SYSTEMS login
    snapshot), see galaxy_export.build_export_json's own docstring for where this comes from and
    why it wasn't being surfaced here until this session. When present for a system, it's always
    at least as complete as scan data could tell you, so it WINS over a scan-derived total_planets/
    types -- those are only a fallback for a system explored_totals doesn't cover (e.g. an older
    export, before this existed). Either way moons are excluded from total_planets/types (a
    non-empty parent_planet, or -- for explored_totals -- simply not counted as a "planet_type" by
    the client itself), same convention colony_tool uses.

    ``scanned_planets`` counts real scan rows (moons excluded, so it's directly comparable to
    total_planets as an "X of Y scanned" ratio). ``resources`` sums EVERY scanned row's
    ``resource_totals`` (extractor slots across all the stations a body can hold: 3 on a planet, 1
    on a moon), moons included -- unlike the planet counts, a moon's resources are real and
    minable, so they count toward "how much is actually down there" even though a moon isn't its
    own colonizable planet slot. ``scanned`` is True the moment ANY row (planet or moon) has been
    scanned -- the resource totals are only meaningful once this is True, since an unscanned
    galaxy's totals are just all-zero rather than genuinely "nothing here."."""
    summary: dict[str, dict] = {}

    def _entry(system: str) -> dict:
        return summary.setdefault(system, {
            "total_planets": 0, "types": {}, "scanned_planets": 0, "scanned": False,
            "resources": {k: 0 for k in RESOURCE_KEYS}, "hazard_rating": None,
        })

    for rec in planet_records(rows):
        system = rec["system"]
        if not system:
            continue
        entry = _entry(system)
        entry["scanned"] = True
        if not rec["parent_planet"]:
            entry["scanned_planets"] += 1
            # Fallback only -- overwritten below whenever explored_totals actually covers this
            # system, which is always at least as complete a picture as scan rows alone.
            ptype = rec["planet_type"] or "Unknown"
            entry["types"][ptype] = entry["types"].get(ptype, 0) + 1
            entry["total_planets"] += 1
        for key in RESOURCE_KEYS:
            entry["resources"][key] += rec["resource_totals"].get(key, 0)

    for system, totals in (explored_totals or {}).items():
        if not system:
            continue
        entry = _entry(system)
        types = {str(t): int(c) for t, c in (totals.get("planet_types") or {}).items() if int(c) > 0}
        if types:
            entry["types"] = types
            entry["total_planets"] = sum(types.values())
        if "hazard_rating" in totals:
            try:
                entry["hazard_rating"] = int(totals["hazard_rating"])
            except (TypeError, ValueError):
                pass

    return summary


def build_dashboard_html(rows: list[dict], focus_system: str | None = None,
                          explored_totals: dict | None = None,
                          station_log_rows: list[dict] | None = None,
                          station_names: dict | None = None,
                          bot_fleet_rows: list[dict] | None = None,
                          shuttle_targets: set | None = None,
                          shuttle_data_captured: bool = False,
                          trade_bot_slots: dict | None = None,
                          colony_scan_rows: list[dict] | None = None,
                          station_systems: dict | None = None,
                          station_profiles: dict | None = None) -> str:
    """Render the full self-contained HTML report for the given CSV rows. ``explored_totals``, when
    given (export_for_claude.json's "explored_systems" map), powers the Galaxies tab's per-system
    planet counts/types even for a system that's never been individually scanned -- see
    galaxy_summary(). ``station_log_rows`` (scan_data/station_logs.csv, see
    station_log_capture.py), ``colony_scan_rows`` (scan_data/colony_scan.jsonl, see
    colony_scan_logger.py), ``station_systems`` (station_id_logger.load_station_systems(), which
    galaxy each station is in) and the rest of the keyword args power the Bot Activity tab -- see
    bot_activity.bot_activity_summary() for what each of them means. ``station_profiles``
    (station_profile.load_profiles(): a snapshot of each player station from the last time you left it) feeds the
    Stations tab together with the Bot Activity numbers and the bots' orders -- see station_overview."""
    records = planet_records(rows)
    galaxies = galaxy_summary(rows, explored_totals)
    bot_activity = bot_activity_summary(
        station_log_rows or [], station_names=station_names, bot_fleet_rows=bot_fleet_rows,
        shuttle_targets=shuttle_targets, shuttle_data_captured=shuttle_data_captured,
        trade_bot_slots=trade_bot_slots, colony_scan_rows=colony_scan_rows,
        station_systems=station_systems)

    overview = station_overview(station_profiles, bot_activity, bot_fleet_rows, station_names, station_systems,
                                bodies=records, colony_scan_rows=colony_scan_rows)

    def _js_json(value) -> str:
        # Escape "</" so a data value containing "</script>" can't prematurely
        # close the embedding <script> tag — valid JSON either way, since a
        # backslash-escaped forward slash decodes to a plain "/". "<!--" is escaped too: followed by "<script" it
        # would put the HTML parser in its "double escaped" state, where the real </script> no longer ends the block.
        return json.dumps(value, separators=(",", ":")).replace("</", "<\\/").replace("<!--", "<\\u0021--")

    data_json = _js_json(records)
    galaxy_json = _js_json(galaxies)
    bots_json = _js_json(bot_activity)
    stations_json = _js_json(overview)
    focus_json = _js_json(focus_system or "")
    resource_cols_json = _js_json(
        [{"key": k, "label": RESOURCE_LABELS[k]} for k in RESOURCE_KEYS])
    system_count = len({r["system"] for r in records})
    generated_title = html.escape(
        f"{len(records)} planets across {system_count} systems")

    catalog, catalog_problems = load_catalog()
    # The planner's own markup, styling and script go in LAST, so none of the replacements above can
    # match a placeholder-looking string inside them.
    return _PAGE_TEMPLATE.replace("__DATA_JSON__", data_json) \
        .replace("__GALAXY_JSON__", galaxy_json) \
        .replace("__BOTS_JSON__", bots_json) \
        .replace("__FOCUS_JSON__", focus_json) \
        .replace("__RESOURCE_COLS_JSON__", resource_cols_json) \
        .replace("__SUMMARY__", generated_title) \
        .replace("__STATIONS_CSS__", STATIONS_CSS) \
        .replace("__STATIONS_HTML__", STATIONS_HTML) \
        .replace("__PLANNER_CSS__", PLANNER_CSS) \
        .replace("__PLANNER_HTML__", PLANNER_HTML) \
        .replace("__PLANNER_SCRIPT__", planner_script(catalog, catalog_problems)) \
        .replace("__STATIONS_SCRIPT__", stations_script()) \
        .replace("__STATIONS_JSON__", stations_json)


def _default_csv_path() -> str:
    # scan_data/ is shared with colony_tool/ (a sibling repo) and lives one level above
    # the client's folder, at the true project root.
    root_dir = str(modloader.PROJECT_DIR)
    return os.path.join(root_dir, "scan_data", "planet_scans.csv")


def _default_html_path() -> str:
    # scan_data/ is shared with colony_tool/ (a sibling repo) and lives one level above
    # the client's folder, at the true project root.
    root_dir = str(modloader.PROJECT_DIR)
    return os.path.join(root_dir, "scan_data", "dashboard.html")


def _default_export_path() -> str:
    # scan_data/ is shared with colony_tool/ (a sibling repo) and lives one level above
    # the client's folder, at the true project root.
    root_dir = str(modloader.PROJECT_DIR)
    return os.path.join(root_dir, "scan_data", "export_for_claude.json")


def generate_and_open(focus_system: str | None = None,
                       csv_path: str | None = None,
                       html_path: str | None = None,
                       export_json_path: str | None = None) -> None:
    """Regenerate the dashboard from the latest CSV and open it in a browser."""
    try:
        csv_path = csv_path or _default_csv_path()
        html_path = html_path or _default_html_path()
        export_json_path = export_json_path or _default_export_path()
        if not os.path.exists(csv_path):
            rows = []
        else:
            with open(csv_path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        explored_totals = None
        if os.path.exists(export_json_path):
            with open(export_json_path, encoding="utf-8") as f:
                explored_totals = json.load(f).get("explored_systems", {})
        # Bot Activity tab inputs -- every one of these is independently
        # best-effort/optional (see bot_activity.bot_activity_summary), so a
        # fresh install with none of them captured yet still renders a valid,
        # just-empty tab rather than failing the whole dashboard.
        station_log_rows = load_station_log_rows()
        station_names = load_station_names()
        station_systems = load_station_systems()
        bot_fleet_rows = load_bot_fleet_scan()
        shuttle_targets = load_shuttle_target_station_ids()
        shuttle_data_captured = shuttle_data_has_been_captured()
        trade_bot_slots = load_trade_bot_slots()
        colony_scan_rows = load_colony_scan()
        station_profiles = load_profiles()
        page = build_dashboard_html(
            rows, focus_system, explored_totals,
            station_log_rows=station_log_rows, station_names=station_names,
            bot_fleet_rows=bot_fleet_rows, shuttle_targets=shuttle_targets,
            shuttle_data_captured=shuttle_data_captured, trade_bot_slots=trade_bot_slots,
            colony_scan_rows=colony_scan_rows, station_systems=station_systems,
            station_profiles=station_profiles)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(page)
        webbrowser.open(f"file:///{os.path.abspath(html_path).replace(os.sep, '/')}")
    except Exception:
        logger.exception("scan_dashboard: failed to generate/open dashboard")


_PAGE_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Star Empire — Scan Archive</title>
<style>
  :root {
    --bg: #08080f; --panel: #0d0d1a; --panel2: #111125; --border: #1a1a3a;
    --accent: #3a6fff; --accent-glow: #6699ff; --text: #c8d8ff;
    --text-dim: #4a5a80; --gold: #ffdd44; --danger: #ff4444; --success: #44ff99;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font-family: "Consolas", "SF Mono", Menlo, monospace;
    font-size: 13px;
  }
  header {
    padding: 18px 24px; border-bottom: 1px solid var(--border);
    display: flex; align-items: baseline; justify-content: space-between;
    flex-wrap: wrap; gap: 8px;
  }
  header h1 {
    margin: 0; font-size: 18px; letter-spacing: 2px; font-weight: 700;
    color: var(--text);
  }
  header h1 span { color: var(--accent-glow); }
  header .summary { color: var(--text-dim); font-size: 12px; }
  .tabs {
    padding: 10px 24px 0; display: flex; gap: 4px; border-bottom: 1px solid var(--border);
  }
  .tab {
    background: none; border: 1px solid transparent; border-bottom: none;
    color: var(--text-dim); padding: 8px 16px; font-family: inherit; font-size: 12px;
    letter-spacing: 1px; cursor: pointer; border-radius: 4px 4px 0 0;
  }
  .tab:hover { color: var(--accent-glow); }
  .tab.active {
    color: var(--gold); background: var(--panel2); border-color: var(--border);
  }
  .view { display: none; }
  .view.active { display: block; }
  .dim-note { color: var(--text-dim); font-style: italic; }
  .section-title {
    margin: 28px 0 0; font-size: 13px; letter-spacing: 1px; color: var(--text-dim);
    text-transform: uppercase; font-weight: 700;
  }
  .section-title:first-child { margin-top: 0; }
  .badge { padding: 2px 7px; border-radius: 3px; font-size: 11px; }
  .badge-yes { background: rgba(68, 255, 153, 0.12); color: var(--success); }
  .badge-no { background: rgba(255, 68, 68, 0.12); color: var(--danger); }
  .badge-warn { background: rgba(255, 221, 68, 0.12); color: var(--gold); }
  .controls {
    padding: 14px 24px; display: flex; gap: 10px; flex-wrap: wrap;
    align-items: center; border-bottom: 1px solid var(--border);
  }
  input, select {
    background: var(--panel2); border: 1px solid var(--border); color: var(--text);
    padding: 7px 10px; border-radius: 3px; font-family: inherit; font-size: 12px;
  }
  input:focus, select:focus { outline: none; border-color: var(--accent); }
  #search { width: 240px; }
  .count { color: var(--text-dim); margin-left: auto; font-size: 12px; }
  .note { color: var(--text-dim); font-size: 12px; padding: 8px 24px 0; }
  main { padding: 0 24px 40px; }
  table { width: 100%; border-collapse: collapse; margin-top: 14px; }
  thead th {
    position: sticky; top: 0; background: var(--panel);
    text-align: left; padding: 8px 10px; font-size: 11px; letter-spacing: 1px;
    color: var(--text-dim); border-bottom: 1px solid var(--border);
    cursor: pointer; white-space: nowrap; user-select: none;
  }
  thead th:hover { color: var(--accent-glow); }
  thead th.sorted { color: var(--gold); }
  tbody tr {
    border-bottom: 1px solid #14142a; cursor: pointer;
  }
  tbody tr:hover { background: var(--panel2); }
  tbody tr.expanded { background: #14142a; }
  td { padding: 7px 10px; white-space: nowrap; }
  td.name { color: var(--gold); font-weight: 600; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  td.dim { color: var(--text-dim); }
  .detail-row td {
    background: #0a0a18; white-space: normal; padding: 12px 20px;
  }
  .detail-row { display: none; }
  .detail-row.open { display: table-row; }
  .mod-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 6px 20px;
  }
  .mod-row { display: flex; justify-content: space-between; gap: 10px; }
  .mod-cat { color: var(--text-dim); }
  .mod-penalty { color: var(--danger); }
  .mod-ideal { color: var(--success); }
  .empty { padding: 40px; text-align: center; color: var(--text-dim); }
__PLANNER_CSS__
__STATIONS_CSS__
</style>
</head>
<body>
<header>
  <h1>STAR EMPIRE <span>// SCAN ARCHIVE</span></h1>
  <div class="summary">__SUMMARY__</div>
</header>
<div class="tabs">
  <button class="tab active" id="tabPlanetsBtn">PLANETS</button>
  <button class="tab" id="tabGalaxiesBtn">GALAXIES</button>
  <button class="tab" id="tabBotsBtn">BOT ACTIVITY</button>
  <button class="tab" id="tabStationsBtn">STATIONS</button>
  <button class="tab" id="tabPlannerBtn">PLANNER</button>
</div>
<div id="planetsView" class="view active">
  <div class="controls">
    <input id="search" type="text" placeholder="Search planet or system...">
    <select id="systemFilter"><option value="">All systems</option></select>
    <div class="count" id="rowCount"></div>
  </div>
  <div class="note">Resource columns count extractor slots across every station the body can hold: 3 on a planet, 1 on a moon. Hover a figure for the per-station count.</div>
  <main>
    <table id="tbl">
      <thead><tr id="headerRow"></tr></thead>
      <tbody id="tbody"></tbody>
    </table>
    <div class="empty" id="emptyMsg" style="display:none">No scans recorded yet.</div>
  </main>
</div>
<div id="galaxiesView" class="view">
  <div class="controls">
    <input id="gSearch" type="text" placeholder="Search galaxy...">
    <div class="count" id="gRowCount"></div>
  </div>
  <div class="note">Resource columns count extractor slots across all stations on every scanned body: 3 per planet, 1 per moon.</div>
  <main>
    <table id="gTbl">
      <thead><tr id="gHeaderRow"></tr></thead>
      <tbody id="gTbody"></tbody>
    </table>
    <div class="empty" id="gEmptyMsg" style="display:none">No explored galaxies found.</div>
  </main>
</div>
<div id="botsView" class="view">
  <div class="controls">
    <input id="bSearch" type="text" placeholder="Search station or bot...">
    <div class="count" id="bAccountsSummary"></div>
  </div>
  <main>
    <h2 class="section-title">By station</h2>
    <table id="bsTbl">
      <thead><tr id="bsHeaderRow"></tr></thead>
      <tbody id="bsTbody"></tbody>
    </table>
    <div class="empty" id="bsEmptyMsg" style="display:none">No station activity captured yet -- visit a station's Logs tab in-game.</div>
    <h2 class="section-title">By bot</h2>
    <table id="bbTbl">
      <thead><tr id="bbHeaderRow"></tr></thead>
      <tbody id="bbTbody"></tbody>
    </table>
    <div class="empty" id="bbEmptyMsg" style="display:none">No bot activity captured yet.</div>
  </main>
</div>
__PLANNER_HTML__
__STATIONS_HTML__
<script>
const DATA = __DATA_JSON__;
const GALAXY = __GALAXY_JSON__;
const BOTS = __BOTS_JSON__;
const STATIONS = __STATIONS_JSON__;
const FOCUS_SYSTEM = __FOCUS_JSON__;
const RESOURCE_COLS = __RESOURCE_COLS_JSON__;

const BASE_COLS = [
  {key: "planet_name", label: "Planet", cls: "name"},
  {key: "system", label: "System"},
  {key: "planet_type", label: "Type", cls: "dim"},
  {key: "parent_planet", label: "Moon Of", cls: "dim"},
  {key: "colonization_index", label: "Colony Idx", cls: "num", numeric: true},
  {key: "orbit_distance", label: "Orbit Dist", cls: "num", numeric: true},
  {key: "orbit_period_seconds", label: "Orbit Period", cls: "num", numeric: true,
   fmt: fmtDuration},
  // Worked out from the two columns before it, not scanned: how fast the body travels along its orbit.
  {key: "orbit_speed_ups", label: "Orbit Speed (u/s)", cls: "num", numeric: true,
   get: orbitSpeed, fmt: fmtSpeed,
   tip: r => {
     const s = orbitSpeed(r);
     if (s === null) return "";
     return s.toFixed(2) + " u/s = 2\\u03c0 \\u00d7 " + r.orbit_distance.toLocaleString() + " u in one "
       + fmtDuration(r.orbit_period_seconds) + " orbit"
       + (r.parent_planet ? " (a moon: speed relative to " + r.parent_planet + ")" : "");
   }},
];
// Slots across every station the body can hold (3 on a planet, 1 on a moon); the tooltip shows
// the per-station figure the scan itself reports.
const RES_COLS = RESOURCE_COLS.map(c => ({
  key: "res_" + c.key, label: c.label, cls: "num", numeric: true,
  get: r => r.resource_totals[c.key] || 0,
  tip: r => {
    const per = r.resources[c.key] || 0;
    return per ? per + " per station \\u00d7 " + r.stations + " station" + (r.stations === 1 ? "" : "s") : "";
  },
}));
const COLS = BASE_COLS.concat(RES_COLS);

let sortKey = "planet_name", sortDir = 1, expandedIdx = null;

function fmtDuration(s) {
  if (s === null || s === undefined || s === "") return "\\u2014";
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h) return h + "h " + String(m).padStart(2, "0") + "m";
  if (m) return m + "m " + String(sec).padStart(2, "0") + "s";
  return sec + "s";
}

// Speed along the orbit in units per second: one lap is 2*pi*r, covered in one period. A moon's
// orbit distance is measured from its planet, so its speed is relative to that planet. null when
// the scan lacks either figure or the body does not move (no period).
function orbitSpeed(row) {
  const d = row.orbit_distance, p = row.orbit_period_seconds;
  if (!(d > 0) || !(p > 0)) return null;   // also false for blanks, null and non-numbers
  return 2 * Math.PI * d / p;
}

function fmtSpeed(v) {
  return v === null || v === undefined || v === "" ? "\\u2014" : v.toFixed(1);
}

function cellValue(row, col) {
  return col.get ? col.get(row) : row[col.key];
}

function renderHeader() {
  const tr = document.getElementById("headerRow");
  tr.innerHTML = "";
  COLS.forEach(col => {
    const th = document.createElement("th");
    th.textContent = col.label;
    if (col.key === sortKey) th.classList.add("sorted");
    th.addEventListener("click", () => {
      if (sortKey === col.key) sortDir *= -1; else { sortKey = col.key; sortDir = 1; }
      render();
    });
    tr.appendChild(th);
  });
}

function matchesFilters(row, query, system) {
  if (system && row.system !== system) return false;
  if (!query) return true;
  const q = query.toLowerCase();
  return row.planet_name.toLowerCase().includes(q)
      || row.system.toLowerCase().includes(q)
      || row.planet_type.toLowerCase().includes(q);
}

function populateSystemFilter() {
  const sel = document.getElementById("systemFilter");
  const systems = Array.from(new Set(DATA.map(r => r.system))).sort();
  systems.forEach(s => {
    const opt = document.createElement("option");
    opt.value = s; opt.textContent = s;
    sel.appendChild(opt);
  });
  if (FOCUS_SYSTEM && systems.includes(FOCUS_SYSTEM)) sel.value = FOCUS_SYSTEM;
}

function buildDetailRow(row, colspan) {
  const tr = document.createElement("tr");
  tr.className = "detail-row";
  const td = document.createElement("td");
  td.colSpan = colspan;
  if (row.modifiers.length === 0 && !row.extra_modifiers) {
    td.textContent = "No colonization modifier data.";
  } else {
    const grid = document.createElement("div");
    grid.className = "mod-grid";
    row.modifiers.forEach(m => {
      const div = document.createElement("div");
      div.className = "mod-row";
      const penClass = m.penalty_pct > 0 ? "mod-penalty" : "mod-ideal";
      const penText = m.penalty_pct > 0 ? ("-" + m.penalty_pct + "%") : "ideal";
      div.innerHTML = '<span class="mod-cat">' + m.category + '</span>'
        + '<span class="' + penClass + '">' + m.setting + ' (' + penText + ')</span>';
      grid.appendChild(div);
    });
    td.appendChild(grid);
    if (row.extra_modifiers) {
      const extra = document.createElement("div");
      extra.style.marginTop = "8px";
      extra.style.color = "var(--text-dim)";
      extra.textContent = row.extra_modifiers;
      td.appendChild(extra);
    }
  }
  tr.appendChild(td);
  return tr;
}

function render() {
  const query = document.getElementById("search").value.trim();
  const system = document.getElementById("systemFilter").value;
  let rows = DATA.filter(r => matchesFilters(r, query, system));

  rows.sort((a, b) => {
    const col = COLS.find(c => c.key === sortKey);
    let av = cellValue(a, col), bv = cellValue(b, col);
    if (col.numeric) {
      av = (av === null || av === undefined || av === "") ? -Infinity : av;
      bv = (bv === null || bv === undefined || bv === "") ? -Infinity : bv;
      return (av - bv) * sortDir;
    }
    return String(av).localeCompare(String(bv)) * sortDir;
  });

  document.getElementById("rowCount").textContent = rows.length + " / " + DATA.length + " planets";
  document.getElementById("emptyMsg").style.display = DATA.length === 0 ? "block" : "none";

  const tbody = document.getElementById("tbody");
  tbody.innerHTML = "";
  rows.forEach((row, idx) => {
    const tr = document.createElement("tr");
    COLS.forEach(col => {
      const td = document.createElement("td");
      if (col.cls) td.className = col.cls;
      let v = cellValue(row, col);
      td.textContent = col.fmt ? col.fmt(v) : (v === null || v === undefined || v === "" ? "\\u2014" : v);
      if (col.tip) { const tip = col.tip(row); if (tip) td.title = tip; }
      tr.appendChild(td);
    });
    tr.addEventListener("click", () => {
      expandedIdx = expandedIdx === idx ? null : idx;
      render();
    });
    if (expandedIdx === idx) tr.classList.add("expanded");
    tbody.appendChild(tr);
    const detail = buildDetailRow(row, COLS.length);
    if (expandedIdx === idx) detail.classList.add("open");
    tbody.appendChild(detail);
  });

  renderHeader();
}

populateSystemFilter();
document.getElementById("search").addEventListener("input", render);
document.getElementById("systemFilter").addEventListener("change", render);
render();

// -------- Tab switching --------
function showTab(tab) {
  document.getElementById("tabPlanetsBtn").classList.toggle("active", tab === "planets");
  document.getElementById("tabGalaxiesBtn").classList.toggle("active", tab === "galaxies");
  document.getElementById("tabBotsBtn").classList.toggle("active", tab === "bots");
  document.getElementById("tabStationsBtn").classList.toggle("active", tab === "stations");
  document.getElementById("tabPlannerBtn").classList.toggle("active", tab === "planner");
  document.getElementById("planetsView").classList.toggle("active", tab === "planets");
  document.getElementById("galaxiesView").classList.toggle("active", tab === "galaxies");
  document.getElementById("botsView").classList.toggle("active", tab === "bots");
  document.getElementById("stationsView").classList.toggle("active", tab === "stations");
  document.getElementById("plannerView").classList.toggle("active", tab === "planner");
}
document.getElementById("tabPlanetsBtn").addEventListener("click", () => showTab("planets"));
document.getElementById("tabGalaxiesBtn").addEventListener("click", () => showTab("galaxies"));
document.getElementById("tabBotsBtn").addEventListener("click", () => showTab("bots"));
document.getElementById("tabStationsBtn").addEventListener("click", () => showTab("stations"));
document.getElementById("tabPlannerBtn").addEventListener("click", () => showTab("planner"));

// -------- Galaxies view --------
const GALAXY_TYPE_KEYS = Array.from(
  new Set(Object.values(GALAXY).flatMap(g => Object.keys(g.types || {})))
).sort();

const G_BASE_COLS = [
  {key: "system", label: "System", cls: "name"},
  {key: "total_planets", label: "Planets", cls: "num", numeric: true},
  {key: "scanned_planets", label: "Scanned", cls: "num", numeric: true},
  // null means the export has no explored_systems entry for this galaxy (e.g. a scan-only
  // system from before that data existed) -- rendered as an em-dash below, distinct from a
  // real, confirmed hazard 0.
  {key: "hazard_rating", label: "Hazard", cls: "num", numeric: true},
];
const G_TYPE_COLS = GALAXY_TYPE_KEYS.map(t => ({
  key: "type_" + t, label: t, cls: "num", numeric: true,
  get: g => (g.types || {})[t] || 0,
}));
// null (not a plain 0) means "never scanned" -- rendered as an em-dash below, distinct from a
// real, confirmed-empty deposit.
const G_RES_COLS = RESOURCE_COLS.map(c => ({
  key: "gres_" + c.key, label: c.label, cls: "num", numeric: true,
  get: g => g.scanned ? (g.resources[c.key] || 0) : null,
}));
const G_COLS = G_BASE_COLS.concat(G_TYPE_COLS, G_RES_COLS);

let gSortKey = "total_planets", gSortDir = -1;

function gCellValue(row, col) {
  return col.get ? col.get(row) : row[col.key];
}

function renderGalaxyHeader() {
  const tr = document.getElementById("gHeaderRow");
  tr.innerHTML = "";
  G_COLS.forEach(col => {
    const th = document.createElement("th");
    th.textContent = col.label;
    if (col.key === gSortKey) th.classList.add("sorted");
    th.addEventListener("click", () => {
      if (gSortKey === col.key) gSortDir *= -1; else { gSortKey = col.key; gSortDir = col.numeric ? -1 : 1; }
      renderGalaxies();
    });
    tr.appendChild(th);
  });
}

function renderGalaxies() {
  const query = document.getElementById("gSearch").value.trim().toLowerCase();
  let rows = Object.keys(GALAXY)
    .map(system => Object.assign({system: system}, GALAXY[system]))
    .filter(g => !query || g.system.toLowerCase().includes(query));

  rows.sort((a, b) => {
    const col = G_COLS.find(c => c.key === gSortKey);
    let av = gCellValue(a, col), bv = gCellValue(b, col);
    if (col.numeric) {
      av = (av === null || av === undefined) ? -Infinity : av;
      bv = (bv === null || bv === undefined) ? -Infinity : bv;
      return (av - bv) * gSortDir;
    }
    return String(av).localeCompare(String(bv)) * gSortDir;
  });

  document.getElementById("gRowCount").textContent = rows.length + " galaxies";
  document.getElementById("gEmptyMsg").style.display = rows.length === 0 ? "block" : "none";

  const tbody = document.getElementById("gTbody");
  tbody.innerHTML = "";
  rows.forEach(row => {
    const tr = document.createElement("tr");
    G_COLS.forEach(col => {
      const td = document.createElement("td");
      if (col.cls) td.className = col.cls;
      const v = gCellValue(row, col);
      if (v === null || v === undefined) {
        td.textContent = "\\u2014";
        td.classList.add("dim");
      } else {
        td.textContent = v;
      }
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });

  renderGalaxyHeader();
}

document.getElementById("gSearch").addEventListener("input", renderGalaxies);
renderGalaxies();

// -------- Bot Activity view --------
const BS_COLS = [
  {key: "station", label: "Station", cls: "name"},
  {key: "system", label: "Galaxy", cls: "dim"},
  {key: "bot_docks", label: "Bot Docks", cls: "num", numeric: true},
  {key: "unique_bots", label: "Unique Bots", cls: "num", numeric: true},
  {key: "bot_txns", label: "Bot Txns", cls: "num", numeric: true},
  {key: "median_dock_gap_seconds", label: "Real Dock Cadence", cls: "num", numeric: true,
   // The median gap between real, consecutive dock timestamps -- NOT docks
   // divided by however much of station_logs.csv happens to be captured,
   // which understates true frequency by counting stretches with no
   // client even logged in (a real bug this session: looked like "once
   // per 27 minutes" for a bot really cycling every ~40-50 seconds).
   fmt: fmtDuration},
  {key: "player_docks", label: "Player Docks", cls: "num", numeric: true},
  {key: "overall_pct_observed", label: "Coverage % (observed)", cls: "num", numeric: true,
   // The same figure worked out from what bots REALLY took in the station's newest stretch of log (log_window),
   // not from their loop time run for 24 hours: a bot is usually not logged in all day, so the non-stop column
   // beside it runs well ahead of this one. Blank when no stretch of the log spans 3 hours.
   fmt: v => v === null || v === undefined ? "\\u2014" : v.toFixed(0) + "%"},
  {key: "overall_pct_satisfied", label: "Coverage % (if non-stop)", cls: "num", numeric: true,
   // Volume-weighted across every resource this station is known to produce:
   // total bought/day (real dock cadence x real purchase amounts) over total
   // produced/day. Below 100% means production is piling up faster than bots
   // clear it (feeds into backed_up); well above 100% means bots are draining
   // it faster than it's made (it can run dry even while hull_fill_pct still
   // looks fine on a single snapshot). See the Production vs. Bot Pickup
   // breakdown in the expanded row for the per-resource numbers behind this.
   fmt: v => v === null || v === undefined ? "\\u2014" : v.toFixed(0) + "%"},
  {key: "hull_used", label: "Hull Used", cls: "num", numeric: true},
  {key: "hull_space", label: "Hull Space", cls: "num", numeric: true},
  {key: "hull_fill_pct", label: "Hull Fill %", cls: "num", numeric: true,
   fmt: v => v === null || v === undefined ? "\\u2014" : v.toFixed(1) + "%"},
  {key: "backed_up", label: "Backed Up", numeric: false,
   fmt: v => v === null ? "unknown" : (v ? "backed up" : "ok")},
  {key: "shuttle_covered", label: "Shuttle Network", numeric: false,
   fmt: v => v === null ? "not checked" : (v ? "covered" : "not covered")},
];
const BB_COLS = [
  {key: "bot", label: "Bot", cls: "name"},
  {key: "tag", label: "Account", cls: "dim"},
  {key: "docks", label: "Docks", cls: "num", numeric: true},
  {key: "txns", label: "Txns", cls: "num", numeric: true},
  {key: "txn_per_dock", label: "Txn/Dock", cls: "num", numeric: true},
  {key: "stations_visited", label: "Stations", cls: "num", numeric: true},
  {key: "ship_type", label: "Ship Type", cls: "dim"},
  {key: "cargo_used", label: "Cargo Used", cls: "num", numeric: true},
  {key: "cargo_cap", label: "Cargo Cap", cls: "num", numeric: true},
  {key: "haul_order_count", label: "Haul Orders", cls: "num", numeric: true},
  {key: "state", label: "State", cls: "dim"},
];

let bsSortKey = "bot_docks", bsSortDir = -1;
let bbSortKey = "docks", bbSortDir = -1;
let bsExpandedStation = null;

function botCellValue(row, col) {
  return col.get ? col.get(row) : row[col.key];
}

function fmtResourceLabel(key) {
  return key.replace(/_/g, " ").replace(/\\b\\w/g, c => c.toUpperCase());
}

function fmtRate(v) {
  if (v === null || v === undefined) return "\\u2014";
  return v.toLocaleString(undefined, {maximumFractionDigits: 1}) + "/day";
}

function detailSectionHeading(text) {
  const heading = document.createElement("div");
  heading.className = "section-title";
  heading.style.margin = "14px 0 6px";
  heading.textContent = text;
  return heading;
}

function buildStationDetailRow(row, colspan) {
  const tr = document.createElement("tr");
  tr.className = "detail-row open";
  const td = document.createElement("td");
  td.colSpan = colspan;

  const resources = row.resources, caps = row.resource_caps;
  if (!resources && !caps) {
    const note = document.createElement("div");
    note.className = "dim-note";
    note.textContent = "No Cargo/Storage data captured for this station yet -- dock and view "
      + "its Storage tab in-game.";
    td.appendChild(note);
  } else {
    const keys = Array.from(new Set(
      Object.keys(resources || {}).concat(Object.keys(caps || {})))).sort();
    if (keys.length === 0) {
      const note = document.createElement("div");
      note.className = "dim-note";
      note.textContent = "No resources on hand.";
      td.appendChild(note);
    } else {
      const grid = document.createElement("div");
      grid.className = "mod-grid";
      keys.forEach(key => {
        const qty = (resources || {})[key];
        const cap = (caps || {})[key];
        const div = document.createElement("div");
        div.className = "mod-row";
        const qtyText = qty === undefined ? "\\u2014" : qty.toLocaleString();
        const capText = cap === undefined ? "\\u2014" : cap.toLocaleString();
        let pctText = "", pctClass = "mod-ideal";
        if (qty !== undefined && cap) {
          const pct = 100 * qty / cap;
          pctText = " (" + pct.toFixed(0) + "%)";
          if (pct >= 90) pctClass = "mod-penalty";
        }
        const catSpan = document.createElement("span");
        catSpan.className = "mod-cat";
        catSpan.textContent = fmtResourceLabel(key);
        const valSpan = document.createElement("span");
        valSpan.className = pctClass;
        valSpan.textContent = qtyText + " / " + capText + pctText;
        div.appendChild(catSpan);
        div.appendChild(valSpan);
        grid.appendChild(div);
      });
      td.appendChild(grid);
    }
  }

  const balance = row.resource_balance || {};
  const balanceKeys = Object.keys(balance).sort();
  if (balanceKeys.length > 0) {
    td.appendChild(detailSectionHeading("Production vs. Bot Pickup" + (row.log_window
      ? " (observed over the newest " + row.log_window.hours + " h of the log)" : " (no log stretch of 3 h or more: non-stop figures only)")));
    const grid = document.createElement("div");
    grid.className = "mod-grid";
    balanceKeys.forEach(key => {
      const b = balance[key];
      const div = document.createElement("div");
      div.className = "mod-row";
      const catSpan = document.createElement("span");
      catSpan.className = "mod-cat";
      catSpan.textContent = fmtResourceLabel(key);
      // A middle dot between clauses, not a comma -- a shuttled-in-from list
      // of station names is itself comma-separated, which made the two
      // indistinguishable.
      let text = fmtRate(b.production_per_day) + " produced";
      if (b.sold_per_day) text += " \\u00b7 " + fmtRate(b.sold_per_day) + " sold in by bots";
      if (b.shuttled_in_per_day) {
        text += " \\u00b7 " + fmtRate(b.shuttled_in_per_day) + " shuttled in";
        if (b.shuttled_from && b.shuttled_from.length) text += " from " + b.shuttled_from.join(", ");
      }
      text += " \\u00b7 " + fmtRate(b.bought_per_day) + " bought if non-stop";
      if (b.bought_observed_per_day !== null && b.bought_observed_per_day !== undefined) {
        text += " \\u00b7 " + fmtRate(b.bought_observed_per_day) + " really bought";
      }
      if (b.shuttled_per_day) {
        text += " \\u00b7 " + fmtRate(b.shuttled_per_day) + " shuttled out";
        if (b.shuttled_to && b.shuttled_to.length) text += " to " + b.shuttled_to.join(", ");
      }
      let pctClass = "mod-ideal";
      const hasObserved = b.pct_observed !== null && b.pct_observed !== undefined;
      if (b.pct_satisfied !== null && b.pct_satisfied !== undefined) {
        text += " (" + b.pct_satisfied.toFixed(0) + "% if non-stop" + (hasObserved ? ", " + b.pct_observed.toFixed(0) + "% observed" : "") + ")";
      } else if (hasObserved) {
        text += " (" + b.pct_observed.toFixed(0) + "% observed)";
      }
      const shownPct = hasObserved ? b.pct_observed : b.pct_satisfied;
      if (shownPct !== null && shownPct !== undefined && (shownPct < 50 || shownPct > 150)) pctClass = "mod-penalty";
      const valSpan = document.createElement("span");
      valSpan.className = pctClass;
      valSpan.textContent = text;
      div.appendChild(catSpan);
      div.appendChild(valSpan);
      grid.appendChild(div);
    });
    td.appendChild(grid);
  }

  const botBreakdown = row.bot_breakdown || {};
  const botNames = Object.keys(botBreakdown).sort(
    (a, b) => botBreakdown[b].docks - botBreakdown[a].docks);
  if (botNames.length > 0) {
    td.appendChild(detailSectionHeading("Bots Docking Here"));
    const grid = document.createElement("div");
    grid.className = "mod-grid";
    botNames.forEach(name => {
      const b = botBreakdown[name];
      const resSummary = Object.keys(b.resources || {}).sort().map(key => {
        const r = b.resources[key];
        const parts = [];
        if (r.bought) parts.push(r.bought.toLocaleString() + " bought");
        if (r.sold) parts.push(r.sold.toLocaleString() + " sold");
        const rate = (b.resource_rates || {})[key];
        if (rate) {
          const bits = [];
          if (rate.per_dock !== null && rate.per_dock !== undefined) bits.push(rate.per_dock.toLocaleString() + "/dock");
          if (rate.observed_per_day !== null && rate.observed_per_day !== undefined) bits.push(rate.observed_per_day.toLocaleString() + "/day really");
          if (rate.running_per_day !== null && rate.running_per_day !== undefined) bits.push(rate.running_per_day.toLocaleString() + "/day if non-stop");
          if (bits.length) parts.push("[" + bits.join(", ") + "]");
        }
        return fmtResourceLabel(key) + ": " + parts.join(", ");
      }).join("  \\u00b7  ");
      const div = document.createElement("div");
      div.className = "mod-row";
      const catSpan = document.createElement("span");
      catSpan.className = "mod-cat";
      catSpan.textContent = (b.nickname || name) + (b.tag ? " (" + b.tag + ")" : "") + " \\u2014 " + b.docks + " docks"
        + (b.loop_seconds ? ", loop " + fmtDuration(b.loop_seconds) : "")
        + (b.last_dock ? ", last docked " + new Date(b.last_dock * 1000).toLocaleString() : "");
      const valSpan = document.createElement("span");
      valSpan.className = "dim";
      valSpan.textContent = resSummary || "no transactions";
      div.appendChild(catSpan);
      div.appendChild(valSpan);
      grid.appendChild(div);
    });
    td.appendChild(grid);
  }

  tr.appendChild(td);
  return tr;
}

function renderSortableTable(opts) {
  const {headerId, bodyId, emptyId, cols, rows, sortKey, sortDir, onSort, query, matchField,
         expandable, expandedKey, onToggleExpand, detailBuilder} = opts;
  const filtered = query
    ? rows.filter(r => String(r[matchField]).toLowerCase().includes(query))
    : rows;
  filtered.sort((a, b) => {
    const col = cols.find(c => c.key === sortKey);
    let av = botCellValue(a, col), bv = botCellValue(b, col);
    if (col.numeric) {
      av = (av === null || av === undefined) ? -Infinity : av;
      bv = (bv === null || bv === undefined) ? -Infinity : bv;
      return (av - bv) * sortDir;
    }
    return String(av).localeCompare(String(bv)) * sortDir;
  });

  const headerRow = document.getElementById(headerId);
  headerRow.innerHTML = "";
  cols.forEach(col => {
    const th = document.createElement("th");
    th.textContent = col.label;
    if (col.key === sortKey) th.classList.add("sorted");
    th.addEventListener("click", () => onSort(col));
    headerRow.appendChild(th);
  });

  document.getElementById(emptyId).style.display = rows.length === 0 ? "block" : "none";

  const tbody = document.getElementById(bodyId);
  tbody.innerHTML = "";
  filtered.forEach(row => {
    const tr = document.createElement("tr");
    cols.forEach(col => {
      const td = document.createElement("td");
      if (col.cls) td.className = col.cls;
      const v = botCellValue(row, col);
      if (col.fmt) {
        td.textContent = col.fmt(v);
        if (col.key === "shuttle_covered" || col.key === "backed_up") {
          // classList.add() rejects a single token containing a space --
          // "badge badge-yes" would throw InvalidCharacterError and abort
          // the whole render (real bug this session: it silently emptied
          // both Bot Activity tables the instant any station had a real,
          // non-null value in one of these columns). Pass each class
          // separately. backed_up=true is the BAD outcome (unlike
          // shuttle_covered=true), so its yes/no badge colors are inverted.
          if (v === null) td.classList.add("dim");
          else if (col.key === "backed_up") td.classList.add("badge", v ? "badge-no" : "badge-yes");
          else td.classList.add("badge", v ? "badge-yes" : "badge-no");
        } else if (col.key === "overall_pct_satisfied" || col.key === "overall_pct_observed") {
          // Not a yes/no badge -- three bands. Roughly matched (70-130%) is
          // fine either direction; far below means production is backing up,
          // far above means bots are outrunning it and risk running it dry.
          if (v === null || v === undefined) td.classList.add("dim");
          else if (v >= 70 && v <= 130) td.classList.add("badge", "badge-yes");
          else if (v >= 40 && v <= 200) td.classList.add("badge", "badge-warn");
          else td.classList.add("badge", "badge-no");
        }
      } else if (v === null || v === undefined || v === "") {
        td.textContent = "\\u2014";
        td.classList.add("dim");
      } else {
        td.textContent = v;
      }
      tr.appendChild(td);
    });
    if (expandable) {
      tr.style.cursor = "pointer";
      const rowKey = row[matchField];
      if (expandedKey === rowKey) tr.classList.add("expanded");
      tr.addEventListener("click", () => onToggleExpand(rowKey));
    }
    tbody.appendChild(tr);
    if (expandable && expandedKey === row[matchField]) {
      tbody.appendChild(detailBuilder(row, cols.length));
    }
  });
}

function renderBotActivity() {
  const query = document.getElementById("bSearch").value.trim().toLowerCase();
  const stationRows = Object.keys(BOTS.stations)
    .map(station => Object.assign({station: station}, BOTS.stations[station]));
  const botRows = Object.keys(BOTS.bots)
    .map(bot => Object.assign({bot: bot}, BOTS.bots[bot]));

  renderSortableTable({
    headerId: "bsHeaderRow", bodyId: "bsTbody", emptyId: "bsEmptyMsg",
    cols: BS_COLS, rows: stationRows, sortKey: bsSortKey, sortDir: bsSortDir,
    matchField: "station", query: query,
    onSort: col => {
      if (bsSortKey === col.key) bsSortDir *= -1; else { bsSortKey = col.key; bsSortDir = col.numeric ? -1 : 1; }
      renderBotActivity();
    },
    expandable: true, expandedKey: bsExpandedStation,
    onToggleExpand: key => {
      bsExpandedStation = bsExpandedStation === key ? null : key;
      renderBotActivity();
    },
    detailBuilder: buildStationDetailRow,
  });
  renderSortableTable({
    headerId: "bbHeaderRow", bodyId: "bbTbody", emptyId: "bbEmptyMsg",
    cols: BB_COLS, rows: botRows, sortKey: bbSortKey, sortDir: bbSortDir,
    matchField: "bot", query: query,
    onSort: col => {
      if (bbSortKey === col.key) bbSortDir *= -1; else { bbSortKey = col.key; bbSortDir = col.numeric ? -1 : 1; }
      renderBotActivity();
    },
  });

  const accountEntries = Object.keys(BOTS.accounts);
  document.getElementById("bAccountsSummary").textContent = accountEntries.length === 0
    ? "No trade bot slot data captured yet"
    : accountEntries.map(tag => tag + " " + BOTS.accounts[tag].slots_used + "/" + BOTS.accounts[tag].slots_total).join("  \\u00b7  ");
}

document.getElementById("bSearch").addEventListener("input", renderBotActivity);
renderBotActivity();
</script>
<script>
__PLANNER_SCRIPT__
__STATIONS_SCRIPT__
</script>
</body>
</html>
"""

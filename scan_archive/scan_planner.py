"""scan_planner.py — what the scan archive's PLANNER tab is made of.

The tab itself is ``scan_planner.js`` (planning maths + UI, run in the browser), fed by
``planner_catalog.json`` (which station kits, extractors and gear exist, and what each costs to make).
scan_dashboard.build_dashboard_html() embeds all of it; this module loads and checks the catalogue and
holds the tab's markup and styling.

The catalogue is a hand-edited data file, so it is validated on every build and a mistake in it shows
up as a notice inside the tab rather than breaking the rest of the archive.
"""

from __future__ import annotations

import copy
import json
import logging
import os

logger = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG_PATH = os.path.join(_HERE, "planner_catalog.json")
SCRIPT_PATH = os.path.join(_HERE, "scan_planner.js")

KIT_KINDS = ("planet", "moon", "unattached")
EXTRACTOR_TIERS = ("basic", "advanced", "industrial", "single")

EMPTY_CATALOG = {"version": 1, "kits": [], "extractors": {}, "gear": []}


def _is_int_or_none(value) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool))


def _check_item(item, where: str, seen: set, problems: list) -> None:
    if not isinstance(item, dict):
        problems.append(f"{where}: not an object")
        return
    key = item.get("key")
    if not isinstance(key, str) or not key:
        problems.append(f"{where}: missing key")
        return
    if key in seen:
        problems.append(f"{where}: duplicate key {key!r}")
    seen.add(key)
    for field in ("tech", "size"):
        if not _is_int_or_none(item.get(field)):
            problems.append(f"{key}: {field} must be a whole number or null")
    for field in ("cost", "blueprint_price"):
        cost = item.get(field)
        if cost is None:
            continue
        if not isinstance(cost, dict):
            problems.append(f"{key}: {field} must be null or an object of resource -> amount")
            continue
        for resource, amount in cost.items():
            if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount < 0:
                problems.append(f"{key}: {field} of {resource!r} must be a number, not negative")
    if "note" in item and not isinstance(item["note"], str):
        problems.append(f"{key}: note must be text")
    rate = item.get("per_day")
    if rate is not None and (isinstance(rate, bool) or not isinstance(rate, (int, float)) or rate < 0):
        problems.append(f"{key}: per_day must be a number, not negative, or null")
    if "per_day_inferred" in item and not isinstance(item["per_day_inferred"], bool):
        problems.append(f"{key}: per_day_inferred must be true or false")


def validate_catalog(catalog) -> list[str]:
    """Every problem found in ``catalog`` (an empty list means it is usable)."""
    problems: list[str] = []
    if not isinstance(catalog, dict):
        return ["the catalogue is not a JSON object"]
    seen: set = set()
    kits = catalog.get("kits", [])
    if not isinstance(kits, list):
        problems.append("kits must be a list")
        kits = []
    for i, kit in enumerate(kits):
        _check_item(kit, f"kits[{i}]", seen, problems)
        if isinstance(kit, dict):
            if kit.get("kind") not in KIT_KINDS:
                problems.append(f"{kit.get('key')}: kind must be one of {KIT_KINDS}")
            if not _is_int_or_none(kit.get("station_slots")):
                problems.append(f"{kit.get('key')}: station_slots must be a whole number or null")
    extractors = catalog.get("extractors", {})
    if not isinstance(extractors, dict):
        problems.append("extractors must be an object of resource -> list")
        extractors = {}
    for resource, items in extractors.items():
        if not isinstance(items, list) or not items:
            problems.append(f"extractors[{resource}]: must be a non-empty list")
            continue
        tiers = []
        for i, item in enumerate(items):
            _check_item(item, f"extractors[{resource}][{i}]", seen, problems)
            if isinstance(item, dict):
                if item.get("tier") not in EXTRACTOR_TIERS:
                    problems.append(f"{item.get('key')}: tier must be one of {EXTRACTOR_TIERS}")
                tiers.append(item.get("tier"))
        if len(items) > 1 and "single" in tiers:
            problems.append(f"extractors[{resource}]: a resource with several extractors cannot use tier 'single'")
        if len(tiers) != len(set(tiers)):
            problems.append(f"extractors[{resource}]: two extractors share a tier")
    gear = catalog.get("gear", [])
    if not isinstance(gear, list):
        problems.append("gear must be a list")
        gear = []
    for i, item in enumerate(gear):
        _check_item(item, f"gear[{i}]", seen, problems)
    _check_upgrade_chains(gear, problems)
    return problems


def _check_upgrade_chains(gear: list, problems: list) -> None:
    """``upgrade_from`` names another gear item that the upgrade uses up (``upgrade_qty`` of it);
    the chain must end at an item with no ``upgrade_from`` (the one that is bought) and never loop."""
    by_key = {g["key"]: g for g in gear if isinstance(g, dict) and isinstance(g.get("key"), str)}
    for key, item in by_key.items():
        if "upgrade_from" not in item:
            continue
        source = item["upgrade_from"]
        if not isinstance(source, str) or source not in by_key:
            problems.append(f"{key}: upgrade_from {source!r} is not a gear item in this catalogue")
            continue
        qty = item.get("upgrade_qty", 1)
        if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
            problems.append(f"{key}: upgrade_qty must be a whole number, at least 1")
        for field in ("blueprint", "acquire"):
            if field in item and not isinstance(item[field], str):
                problems.append(f"{key}: {field} must be text")
        walked, at = [key], source
        while at in by_key and "upgrade_from" in by_key[at]:
            if at in walked:
                problems.append(f"{key}: upgrade chain loops back on itself ({' -> '.join(walked + [at])})")
                break
            walked.append(at)
            at = by_key[at]["upgrade_from"]
            if not isinstance(at, str):
                break


def load_catalog(path: str | None = None) -> tuple[dict, list[str]]:
    """``(catalogue, problems)``. Best-effort: a missing or broken file gives an empty catalogue and
    the reasons, never an exception, since this runs while the scan archive is being built."""
    path = path or CATALOG_PATH
    try:
        with open(path, encoding="utf-8") as f:
            catalog = json.load(f)
    except (OSError, ValueError) as exc:
        logger.warning("scan_planner: could not read %s: %s", path, exc)
        return copy.deepcopy(EMPTY_CATALOG), [f"could not read {os.path.basename(path)}: {exc}"]
    problems = validate_catalog(catalog)
    if problems:
        logger.warning("scan_planner: %s has problems: %s", path, "; ".join(problems))
        return copy.deepcopy(EMPTY_CATALOG), problems
    return catalog, []


def planner_script(catalog: dict, problems: list[str], script_path: str | None = None) -> str:
    """The text of the tab's <script>: the catalogue and its problems as constants, then the code.
    ``</`` is escaped in the data so nothing in it can close the tag early."""
    def js(value) -> str:
        return json.dumps(value, separators=(",", ":")).replace("</", "<\\/")

    with open(script_path or SCRIPT_PATH, encoding="utf-8") as f:
        code = f.read()
    return (f"const PLANNER_CATALOG = {js(catalog)};\n"
            f"const PLANNER_PROBLEMS = {js(problems)};\n{code}")


PLANNER_HTML = """<div id="plannerView" class="view">
  <div class="controls">
    <input id="plSystemSearch" type="text" placeholder="Search systems...">
    <div class="count" id="plSummaryLine"></div>
    <button class="btn" id="plCopy">Copy summary</button>
    <button class="btn" id="plExport">Export plan</button>
    <label class="btn" for="plImport">Import plan</label><input type="file" id="plImport" accept=".json,application/json" hidden>
    <button class="btn" id="plReset">Reset</button>
  </div>
  <div class="note">Plan a build-out: tick systems, tick the planets and moons to build on, give each a loadout, and the totals below add up the stations, extractors, gear and materials. Only scanned bodies appear (a scan is what reveals their extractor slots). Your plan is saved in this browser.</div>
  <div id="plNotice"></div>
  <div class="planner-grid">
    <section class="pl-col">
      <h2 class="section-title">Systems <span class="pl-dim" id="plSystemCount"></span> <button class="btn" id="plClearSystems">Clear</button></h2>
      <div id="plSystemList"></div>
    </section>
    <section class="pl-col">
      <h2 class="section-title">Planets and moons</h2>
      <div id="plBodies"></div>
    </section>
    <section class="pl-col">
      <h2 class="section-title">Loadouts</h2>
      <div id="plProfiles"></div>
    </section>
  </div>
  <div class="pl-results">
    <h2 class="section-title">What the plan needs
      <label class="pl-opt"><input type="checkbox" id="plCountBp" data-act="count-blueprints"> Include blueprint purchases in the totals</label></h2>
    <div id="plResults"></div>
  </div>
</div>"""

PLANNER_CSS = """
  .btn {
    background: var(--panel2); border: 1px solid var(--border); color: var(--text);
    padding: 6px 12px; border-radius: 3px; font-family: inherit; font-size: 12px; cursor: pointer;
  }
  .btn:hover { border-color: var(--accent); color: var(--accent-glow); }
  .btn:disabled { opacity: 0.4; cursor: default; }
  .planner-grid {
    display: grid; grid-template-columns: minmax(220px, 290px) minmax(420px, 1fr) minmax(300px, 370px);
    gap: 16px; padding: 16px 24px; align-items: start;
  }
  @media (max-width: 1150px) { .planner-grid { grid-template-columns: 1fr; } }
  .pl-col { background: var(--panel); border: 1px solid var(--border); border-radius: 4px; padding: 12px 14px; min-width: 0; }
  .pl-col .section-title { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
  .pl-col .section-title .btn { margin-left: auto; padding: 3px 8px; font-size: 11px; }
  .pl-dim { color: var(--text-dim); }
  .pl-opt { margin-left: auto; color: var(--text-dim); font-size: 12px; font-weight: 400; letter-spacing: 0; text-transform: none; cursor: pointer; }
  .pl-empty { color: var(--text-dim); padding: 10px 2px; font-style: italic; }
  #plSystemList { max-height: 640px; overflow-y: auto; }
  .pl-sys { display: flex; align-items: baseline; gap: 8px; padding: 5px 6px; border-radius: 3px; cursor: pointer; flex-wrap: wrap; }
  .pl-sys:hover { background: var(--panel2); }
  .pl-sys.on { background: #14142a; }
  .pl-sys-name { color: var(--gold); font-weight: 600; }
  .pl-sysblock { margin-bottom: 18px; }
  .pl-syshead { display: flex; align-items: baseline; gap: 10px; padding: 4px 0; }
  .pl-syshead strong { color: var(--gold); }
  .pl-sysbtns { margin-left: auto; display: flex; gap: 6px; }
  .pl-sysbtns .btn { padding: 3px 8px; font-size: 11px; }
  .pl-table { width: 100%; border-collapse: collapse; margin-top: 6px; }
  .pl-table thead th { position: static; cursor: default; padding: 6px 8px; }
  .pl-table thead th:hover { color: var(--text-dim); }
  .pl-table tbody tr { cursor: default; }
  .pl-table tbody tr:hover { background: transparent; }
  .pl-table th.num { text-align: right; }
  .pl-table td { padding: 5px 8px; vertical-align: top; }
  .pl-body.on { background: #14142a; }
  .pl-body.moon .pl-bname { padding-left: 22px; }
  .pl-bname { white-space: normal; color: var(--text); }
  .pl-chips { white-space: normal; }
  .pl-chip {
    background: var(--panel2); border: 1px solid var(--border); color: var(--text); border-radius: 10px;
    padding: 1px 8px; margin: 1px 3px 1px 0; font-family: inherit; font-size: 11px; cursor: pointer;
  }
  .pl-chip:hover { border-color: var(--accent); }
  .pl-chip.off { color: var(--text-dim); text-decoration: line-through; border-style: dashed; }
  .pl-profhead { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }
  .pl-profhead select { flex: 1 1 140px; min-width: 0; }
  .pl-field { display: grid; grid-template-columns: 90px 1fr; gap: 8px; align-items: center; margin-bottom: 8px; }
  .pl-field label { color: var(--text-dim); }
  .pl-field select { min-width: 0; }
  .pl-subhead { margin: 16px 0 4px; color: var(--text-dim); font-size: 11px; letter-spacing: 1px; text-transform: uppercase; font-weight: 700; }
  .pl-gear input[type=number] { width: 64px; }
  .pl-gear select { max-width: 190px; }
  .pl-applyrow { margin-top: 14px; }
  .pl-results { padding: 0 24px 40px; }
  .pl-assumed { margin-top: 6px; font-size: 12px; }
  .pl-defence { margin-top: 10px; padding: 8px 10px; border: 1px dashed var(--border); border-radius: 4px; }
  .pl-defhead { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
  .pl-defhead .btn { margin-left: auto; padding: 3px 10px; font-size: 11px; }
  .pl-defrow { display: flex; align-items: center; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
  .pl-defrow input[type=number] { width: 64px; }
  .pl-defnone { margin-top: 6px; }
  .pl-results .section-title { display: flex; align-items: center; gap: 10px; }
  .pl-tiles { display: flex; gap: 12px; flex-wrap: wrap; margin: 12px 0; }
  .pl-tile { background: var(--panel); border: 1px solid var(--border); border-radius: 4px; padding: 10px 18px; min-width: 110px; }
  .pl-tile-n { font-size: 22px; color: var(--gold); font-variant-numeric: tabular-nums; }
  .pl-tile-l { color: var(--text-dim); font-size: 11px; letter-spacing: 1px; text-transform: uppercase; }
  .pl-warn { border: 1px solid #6b5a14; background: rgba(255, 221, 68, 0.07); color: var(--gold); padding: 8px 12px; border-radius: 3px; margin: 8px 0; }
  .pl-error { border: 1px solid #6b1c1c; background: rgba(255, 68, 68, 0.08); color: var(--danger); padding: 8px 24px; margin: 8px 24px; border-radius: 3px; }
  .pl-resgrid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 24px; }
  @media (max-width: 1000px) { .pl-resgrid { grid-template-columns: 1fr; } }
  .pl-bybody td { white-space: normal; }
"""

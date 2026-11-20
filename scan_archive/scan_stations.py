"""scan_stations.py - what the scan archive's STATIONS tab is made of: its markup, styling and script.

The data is ``station_overview.station_overview()``; the tab itself is ``scan_stations.js`` (filtering, sorting and
the station detail run in the browser). scan_dashboard.build_dashboard_html() embeds all three.
"""

from __future__ import annotations

import os

_HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT_PATH = os.path.join(_HERE, "scan_stations.js")

STATIONS_HTML = """<div id="stationsView" class="view"></div>"""

STATIONS_CSS = """
  #stationsView { padding: 14px 24px 40px; }
  .st-controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
  .st-controls input, .st-controls select, .st-btn {
    background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 4px;
    padding: 6px 10px; font-family: inherit; font-size: 12px;
  }
  .st-controls input { min-width: 260px; }
  .st-btn { cursor: pointer; }
  .st-btn:hover, .st-controls select:hover, .st-controls input:focus { border-color: var(--accent); color: var(--accent-glow); }
  .st-tiles { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 10px; }
  .st-tile { background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 8px 16px; min-width: 130px; }
  .st-tile .v { font-size: 20px; font-weight: 700; }
  .st-tile .l { font-size: 11px; color: var(--text-dim); letter-spacing: 1px; text-transform: uppercase; }
  .st-tile.ok .v { color: var(--success); } .st-tile.warn .v { color: var(--gold); } .st-tile.bad .v { color: var(--danger); }
  .st-note { color: var(--text-dim); font-size: 12px; margin-bottom: 12px; }
  .st-empty { padding: 40px; text-align: center; color: var(--text-dim); }
  table.st-table, table.st-res { width: 100%; border-collapse: collapse; }
  .st-table th, .st-res th {
    text-align: left; font-size: 11px; letter-spacing: 1px; color: var(--text-dim); font-weight: 600;
    padding: 6px 8px; border-bottom: 1px solid var(--border); white-space: nowrap;
  }
  .st-table td, .st-res td { padding: 6px 8px; border-bottom: 1px solid var(--border); vertical-align: middle; }
  .st-table .num, .st-res .num { text-align: right; font-variant-numeric: tabular-nums; }
  .st-group td { background: var(--panel2); cursor: pointer; font-weight: 700; letter-spacing: 1px; color: var(--accent-glow); }
  .st-group:hover td { color: var(--gold); }
  .st-row { cursor: pointer; }
  .st-row:hover td { background: var(--panel); }
  .st-row.open td { background: var(--panel2); }
  .st-row .name { font-weight: 600; }
  .st-row .sub { font-size: 11px; color: var(--text-dim); font-weight: 400; }
  .dim { color: var(--text-dim); }
  .st-badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 11px; margin-right: 4px;
    border: 1px solid var(--border); white-space: nowrap; }
  .st-badge.ok { color: var(--success); border-color: #1f7a4d; } .st-badge.warn { color: var(--gold); border-color: #7a6a1f; }
  .st-badge.bad { color: var(--danger); border-color: #7a1f1f; } .st-badge.none { color: var(--text-dim); }
  .sbar { display: inline-block; width: 70px; height: 8px; background: var(--border); border-radius: 4px; overflow: hidden;
    vertical-align: middle; margin-right: 6px; }
  .sbar i { display: block; height: 100%; background: var(--accent); }
  .sbar.ok i { background: var(--success); } .sbar.warn i { background: var(--gold); } .sbar.bad i { background: var(--danger); }
  .sbar-n { font-size: 11px; color: var(--text-dim); }
  .st-detail-row td { background: var(--panel); padding: 0; max-width: 0; }   /* the detail never widens the station table: it scrolls inside */
  .st-detail { display: flex; flex-wrap: wrap; gap: 24px; padding: 14px 18px 18px; }
  .st-col { flex: 1 1 280px; min-width: 260px; }
  .st-col.wide { flex: 3 1 520px; min-width: 0; overflow-x: auto; }
  .st-detail h4 { margin: 12px 0 6px; font-size: 11px; letter-spacing: 1.5px; color: var(--text-dim); }
  .st-detail h4:first-child { margin-top: 0; }
  .kv { display: flex; justify-content: space-between; gap: 14px; padding: 2px 0; border-bottom: 1px dotted var(--border); }
  .kv span { color: var(--text-dim); } .kv b { font-weight: 600; text-align: right; }
  .kv.bad b { color: var(--danger); } .kv.warn b { color: var(--gold); }
  .st-issues { margin: 0 0 4px; padding: 0 0 0 18px; }
  .st-issues li.bad { color: var(--danger); } .st-issues li.warn { color: var(--gold); } .st-issues li.info { color: var(--text-dim); }
  .st-res td { font-size: 12px; }
  .st-chip { display: inline-block; background: var(--panel2); border: 1px solid var(--border); border-radius: 4px;
    padding: 0 6px; margin: 1px 2px 1px 0; font-size: 11px; }
  .st-chip i { color: var(--text-dim); font-style: normal; }
"""


def stations_script() -> str:
    with open(SCRIPT_PATH, encoding="utf-8") as handle:
        return handle.read()

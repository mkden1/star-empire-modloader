# Scan archive

The planet, station and bot dashboard built from the data the recorder mods save.

## What it does

- A dashboard in your browser built from the files in `scan_data/`: your scanned planets and their resources, a planner for systems, bodies and loadouts, a STATIONS tab with each station's last-docked snapshot and its bot activity (which bot docked, bought and sold, and how busy each station is), and a galaxy export (`export_for_claude.json`) for pasting into a chat.
- It only reads what is there: it works with whichever of `scan_capture`, `colony_scan`, `station_logs` and `station_profile` you installed, and shows less without the others. A planet appears once its scan has been recorded.

## Using it

- Click **VIEW SCANS** on the full-screen galaxy map (needs the `galaxy_map` mod). It regenerates the dashboard and opens it in the browser.

## Needs

`core`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

Works better with: `scan_capture` (the planet scans); `colony_scan` (colony and shuttle data); `station_logs` (bot activity); `station_profile` (the STATIONS tab).

## Under the hood

- No patches of its own: it is code other mods use.
- Code: `bot_activity.py`, `galaxy_export.py`, `scan_dashboard.py`, `scan_planner.py`, `scan_stations.py`, `station_overview.py`.
- Writes: `scan_data/dashboard.html` and `scan_data/export_for_claude.json`.

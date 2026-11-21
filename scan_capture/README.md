# Scan capture

Passively records scans, inventories, skills, stations and bot state from server messages into `scan_data/`.

## What it does

- It saves what the client already receives, close to verbatim, so it can be analysed offline: planet scans with their resources and extractor slots, station names and ids, the bot fleet and its leg times, trade bot slots, and raw item and ship-stat payloads for damage and sustain analysis.
- It only records. It sends nothing to the server.

## Using it

- Nothing to do: it works in the background. Each recorder has its own switch on the Data Capture page of the ESC menu (needs the `ui_pages` mod): "Trade bot states", "Bot leg times", "Combat & gear capture", "Planet scans", "Station IDs" and "Trade bot slots", under the master switch "All data capture".

## Needs

`core`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

## Under the hood

- Patches the game's `Client.py` (1 hunk), `protocol.py` (17 hunks) (the loader applies them in memory; the game's files are never changed).
- Code: `bot_fleet_scan_logger.py`, `combat_scan_logger.py`, `planet_scan_capture.py`, `scan_logger.py`, `station_id_logger.py`, `trade_bot_slots_capture.py`.
- Writes: Under `scan_data/`: `planet_scans.csv`, `station_ids.csv`, `bot_fleet_scan.jsonl`, `bot_leg_times.jsonl`, `trade_bot_slots.csv`, `combat_scan.jsonl`.

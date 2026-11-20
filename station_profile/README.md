# Station profile snapshots

A snapshot of each player station, saved when you undock.

## What it does

- One small JSON file per station, rewritten whenever you leave a station (and about once a minute while docked when its cargo changes, so a client closed while docked still leaves something recent).
- It holds what the client already knows about the docked station: name, owner, permissions, MOTD, switches, the construction queue, resource stock, caps and reserves, credits, workers, and the equipped modules with their cycle stats.

## Using it

- Nothing to do: it works in the background. It is covered by the "Colony & station state" switch. Its on/off switch is on the Data Capture page of the ESC menu (needs the `ui_pages` mod).

## Needs

`core`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

## Under the hood

- Patches the game's `Client.py` (2 hunks) (the loader applies them in memory; the game's files are never changed).
- Code: `station_profile.py`.
- Writes: `scan_data/station_profiles/<station id>.json`, the input of the scan archive's STATIONS tab.

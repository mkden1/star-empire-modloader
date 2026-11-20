# Station probe

`/stationprobe`: asks the server for another station's logs, cargo and construction, to see whether it answers when you are not docked there.

## What it does

- A test tool. The Logs tab and the cargo and construction requests normally go out only for a station you are docked at. This sends the same request for a station id you type, once, and says what came back.
- When tested (2026-10-31) the server refused or ignored requests for a station you were not docked at, so remote station access is not possible. The mod is kept as the probe that showed it.

## Using it

- `/stationprobe <station id>` sends one logs request and reports the answer.
- `/stationprobe cargo <station id>` and `/stationprobe construction <station id>` do the same for cargo and construction (cargo and construction only while NOT docked).
- `/stationprobe rename <station id>` sends a rename for one of YOUR stations with the name it already has.
- `/stationprobe list [text]` lists your stations with their ids (Stats > Possessions > Stations must have loaded).

## Settings

- `STAR_EMPIRE_NO_STATION_PROBE=1` turns it off.

## Needs

Nothing else.

## Under the hood

- Patches the game's `Client.py` (3 hunks) (the loader applies them in memory; the game's files are never changed).
- Code: `station_probe.py`.

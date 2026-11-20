# Station gear upgrade and expansion

UPGRADE GEAR and ADD 50 GAMMA / WORKERS buttons in the station prefab dialog.

## What it does

- **UPGRADE GEAR**: a station's single-slot modules (shield, energy core, each weapon family, ...) come in marks (`_mk1`, `_mk2`, `_mk3`). When one is fitted and your hold has a higher mark of the same family, the button does by command what you would do by hand in the Storage tab, one module at a time: transfer the higher mark to the station, fit it, and take the old one back to your hold. Each step waits for the server's cargo update.
- **ADD 50 GAMMA + 50 WORKERS**: deposits workers and Station Expander Gammas from your hold into the station, then fits the expanders in one batch.

## Using it

- Open the station prefab dialog and press the button. If a step fails or a limit is hit it stops and reports it; whatever was left over can be fitted by pressing the button again.
- Caution: it uses the same commands as doing it by hand, but how the server handles its own limits for swaps and workers has not been verified live.

## Settings

- `STAR_EMPIRE_NO_GEAR_UPGRADE=1` turns off UPGRADE GEAR; `STAR_EMPIRE_NO_STATION_EXPANSION=1` turns off ADD 50 GAMMA.

## Needs

`render_alias`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

## Under the hood

- Patches the game's `Client.py` (3 hunks) (the loader applies them in memory; the game's files are never changed).
- Code: `station_expansion.py`, `station_gear_upgrade.py`.

# Station auto name

An AUTO NAME button on the station rename dialog.

## What it does

- Stations are named "<planet> - <n>". The button fills the dialog's name field with the next name of that kind for the station you are docked at.
- <planet> is the planet or moon the station is built on. If stations already named for it spell it differently ("rylvyron - 1" next to a planet called Rylvyron), their spelling wins. <n> is the lowest number no other station in the system already uses with that name; the station being renamed is left out of the count, and a gap left by a demolished station is filled first.

## Using it

- Open a station's rename dialog and click **AUTO NAME**. Nothing is sent until you confirm the rename yourself.

## Settings

- `STAR_EMPIRE_NO_STATION_AUTO_NAME=1` turns it off.

## Needs

`core`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

## Under the hood

- Patches the game's `Client.py` (2 hunks), `render_mixin.py` (2 hunks) (the loader applies them in memory; the game's files are never changed).
- Code: `station_auto_name.py`.

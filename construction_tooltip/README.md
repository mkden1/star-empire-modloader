# Construction cost check

Shows what a selected Construction Bay blueprint costs against the station's stock.

## What it does

- Click a blueprint in the Available Blueprints list and the game's popup shows everything it always did (name, man-hours, the initial and periodic costs, the description), plus what the station has of each cost next to it and a verdict.
- A cost the station is short of is coloured: red for what is needed up front, amber for what is taken as the build goes (a project short of one of those waits for it, it is not refused). The verdict says whether you can start the build, for example "Ready: stock covers 363 builds (Metal)", or names what is missing.

## Using it

- Open a Construction Bay and click a blueprint.

## Settings

- `STAR_EMPIRE_NO_CONSTRUCTION_TOOLTIP=1` turns it off.

## Needs

`render_alias`, `core`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

## Under the hood

- Patches the game's `Client.py` (1 hunk) (the loader applies them in memory; the game's files are never changed).
- Code: `construction_tooltip.py`.

# Construction ETA

An estimated time to complete for each project in a station's Construction Bay.

## What it does

- Every ten seconds or so the server pushes the whole queue: each project with its man-hours done and total, and its blueprint's per-unit costs. Each tick the projects are served in queue order, each taking what it can use of every resource it needs. A project needs all its resources at once, one short of a resource does not hold back the ones behind it, and a paused project is skipped.
- This mod models that and shows an estimate for each project next to its progress bar. Unlike a figure that assumes every resource is in stock, it takes the station's actual stock into account.

## Using it

- Open a station's Construction Bay. The estimate appears beside each project's progress bar.

## Settings

- `STAR_EMPIRE_NO_CONSTRUCTION_ETA=1` turns it off.

## Needs

`render_alias`, `core`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

## Under the hood

- Patches the game's `Client.py` (1 hunk) (the loader applies them in memory; the game's files are never changed).
- Code: `construction_eta.py`.

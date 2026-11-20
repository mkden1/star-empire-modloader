# Target info

Planet orbit period, and a gate's distance and bearing from the sun, in the target panel.

## What it does

- Select a planet and the target panel shows `Orbit: ...`, its orbital period.
- Select a warp gate and it shows `Sun Dist: ... u` and its bearing from the sun, the figures route-time calculations are measured from.

## Using it

- Select a planet or a gate.

## Needs

`core`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

## Under the hood

- Patches the game's `Client.py` (6 hunks) (the loader applies them in memory; the game's files are never changed).

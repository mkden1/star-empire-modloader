# Zoom out further

Zoom out to 0.05 instead of 0.10, and keep the things that scale with the zoom range in step.

## What it does

- The game stops the camera at zoom 0.10. This lets it go to 0.05, and adjusts what depends on the zoom range: the zoom steps, a clamp against the game's own limit, object sizes (tighter when zoomed out), the comet reference zoom and the scale of ambient shuttles.

## Using it

- Zoom out as usual.

## Needs

Nothing else.

## Under the hood

- Patches the game's `Client.py` (4 hunks), `render_mixin.py` (2 hunks) (the loader applies them in memory; the game's files are never changed).

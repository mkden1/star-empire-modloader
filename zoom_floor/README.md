# Zoom out further

Zoom out to 0.025 instead of 0.10, and keep the things that scale with the zoom range in step.

## What it does

- The game stops the camera at zoom 0.10. This lets it go to 0.025: two more wheel notches (0.05, then 0.025), each showing twice the width and height (four times the area). It also adjusts what depends on the zoom range: a clamp against the game's own limit, object sizes (tighter when zoomed out), the comet reference zoom and the scale of ambient shuttles.
- The wheel moves along the game's usual stops (0.40, 0.35 ... 0.10, 0.05) and then the floor, so every stop is a zoom the game's size rounding handles.

## Using it

- Zoom out as usual. The game starts each session fully zoomed out, so it now starts at 0.025.
- The minimap's closest view is "one fully zoomed-out camera", so it is wider than before as well (the minimap's own zoom buttons still work, with fewer steps).
- Zoomed all the way out a busy system draws four times the area it did at 0.05, so it can feel heavier there.

## Needs

Nothing else.

## Under the hood

- Patches the game's `Client.py` (6 hunks), `render_mixin.py` (3 hunks) (the loader applies them in memory; the game's files are never changed).
- The wheel's stops are `zoom_floor.py`: the floor, then every multiple of 0.05 (the game's own step and rounding grid).
- The floor is `CAM_MIN_ZOOM` in `Client.py.patch`, and `_COMET_REF_ZOOM` in `render_mixin.py.patch` has to be the same number (a class attribute cannot read the window's). `test_zoom_floor.py` checks that they agree and that every stop survives the game's rounding.

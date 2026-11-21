"""zoom_floor.py - the mouse wheel's zoom stops, now that the camera can zoom out below the game's 0.05 grid.

The game moves the zoom by 0.05 per wheel notch (``zoom + direction * 0.05``) and rounds the zoom to 0.05 wherever a
size or a cache key is made from it. With the floor at 0.05 that is a clean ladder (0.05, 0.10 ... 0.40). With the floor
lower (0.025) the last notch would land off the grid and the next one up would leave it: 0.025 + 0.05 = 0.075, and
0.075 rounds to 0.10 while 0.125 rounds to 0.10 as well, so the sizes would no longer match the zoom.

So the stops are a ladder: the floor, then every multiple of 0.05 up to the ceiling. A notch moves one stop (a fast
scroll that sends several notches moves that many stops). From 0.05 one notch out is the floor, and one notch in from the
floor is 0.05 again, so every stop the player can reach is a multiple of 0.05 or the floor itself. The game's rounding
(``round(zoom * 20) / 20``) gives the multiples back unchanged; for the floor the patch keeps it from rounding to 0
(``max(CAM_MIN_ZOOM, round(zoom * 20) / 20)`` in Client.py and render_mixin.py).

Pure functions, no game imports.
"""
from __future__ import annotations

import bisect

GRID = 0.05                # the game's own step and rounding grid
_EPS = 1e-9


def stops(floor: float, ceiling: float) -> "list[float]":
    """The zoom values a wheel can stop at, lowest first: ``floor``, then the multiples of 0.05 above it up to ``ceiling``."""
    ladder = [floor]
    k = 1
    while True:
        value = round(k * GRID, 6)
        if value > ceiling + _EPS:
            break
        if value > floor + _EPS:
            ladder.append(value)
        k += 1
    if ladder[-1] < ceiling - _EPS:
        ladder.append(ceiling)
    return ladder


def step(zoom: float, direction: int, floor: float, ceiling: float) -> float:
    """The zoom after ``direction`` wheel notches (positive zooms IN, as in the game), moving along ``stops``.

    From a zoom between two stops (it cannot happen with the wheel alone) the first notch goes to the next stop in that
    direction. A direction of 0 only clamps. The result is never outside ``[floor, ceiling]``."""
    direction = int(direction)
    if direction == 0:
        return max(floor, min(ceiling, zoom))
    ladder = stops(floor, ceiling)
    index = bisect.bisect_left(ladder, zoom - _EPS)             # the first stop at or above the zoom
    on_stop = index < len(ladder) and abs(ladder[index] - zoom) <= _EPS
    target = index + direction if (on_stop or direction < 0) else index + direction - 1
    return ladder[max(0, min(len(ladder) - 1, target))]

"""Pure color helpers for the Galaxy Map's Survey view.

This module deliberately has no pygame dependency, matching
galaxy_territory.py's own rationale: the color math can be unit-tested in
the server's lightweight Python runtime, and the renderer just calls in.
"""

from __future__ import annotations


# Traffic-light anchors for the terra-planet gradient below. Reused as-is
# (not re-derived) so the render module's own _GM_SURVEY_* constants and
# this module always agree -- render_mixin.py imports these rather than
# keeping a second copy.
TERRA_RED    = (250, 90, 90)
TERRA_YELLOW = (240, 220, 100)
TERRA_GREEN  = (120, 220, 120)

# Highest terra-planet count a system has been observed to carry in this
# game. Fixed rather than derived from whatever systems happen to be
# visited this session -- deriving it from live data would make a system's
# own color drift over time as farther systems are explored, with no
# change to the system itself.
DEFAULT_MAX_TERRA = 8


def lerp_rgb(c0: tuple, c1: tuple, t: float) -> tuple[int, int, int]:
    """Linear-interpolate two RGB triples; `t` is clamped to [0, 1]."""
    t = max(0.0, min(1.0, t))
    return tuple(int(round(c0[i] + (c1[i] - c0[i]) * t)) for i in range(3))


def terra_traffic_light_color(
        terra_count: int,
        max_terra: int = DEFAULT_MAX_TERRA) -> tuple[int, int, int]:
    """Red -> yellow -> green fill for a system's terra-planet count.

    A continuous gradient across the full 0..max_terra range rather than a
    flat 3-tier split (0 / 1 / 2+): once systems can carry several terra
    planets, treating "2" and "8" as the same "green" collapses exactly the
    distinction a traffic light view exists to show. Each integer count
    still gets its own visibly different shade -- 0 is pure red, the
    midpoint is pure yellow, max_terra is pure green, everything else
    blends linearly between its nearest two anchors.
    """
    if max_terra <= 0:
        return TERRA_RED
    t = max(0.0, min(1.0, terra_count / max_terra))
    midpoint = 0.5
    if t <= midpoint:
        return lerp_rgb(TERRA_RED, TERRA_YELLOW, t / midpoint)
    return lerp_rgb(TERRA_YELLOW, TERRA_GREEN,
                    (t - midpoint) / (1.0 - midpoint))

"""window_geometry.py - the geometry and the plate of the movable, resizable HUD windows (core).

Written for the galaxy map's window (galaxy_map_window.py) and shared by the Ship Layouts window, so both move, resize
and look the same and neither needs the other mod: clamping a rectangle to the screen, which edges a press would drag,
moving / resizing a rectangle by a drag, and the panel plate drawn with primitives (a window that is being resized has
a new size every frame, so a baked plate per size would fill the texture atlas).

``EDGE_L / T / R / B`` are bits of the value ``resize_zone`` returns and ``resized_rect`` takes.
"""

from __future__ import annotations

import pygame

EDGE_L, EDGE_T, EDGE_R, EDGE_B = 1, 2, 4, 8


def clamp_rect(rect: pygame.Rect, bounds_w: int, bounds_h: int, min_w: int, min_h: int) -> pygame.Rect:
    """*rect* with at least the minimum size (as far as the screen allows) and fully on screen."""
    width = max(min(int(min_w), int(bounds_w)), min(int(rect.w), int(bounds_w)))
    height = max(min(int(min_h), int(bounds_h)), min(int(rect.h), int(bounds_h)))
    x = max(0, min(int(rect.x), int(bounds_w) - width))
    y = max(0, min(int(rect.y), int(bounds_h) - height))
    return pygame.Rect(x, y, width, height)


def resize_zone(rect: pygame.Rect, pos, grab: int, corner: int) -> int:
    """The edges (EDGE_* bits) a press at *pos* would drag: 0 when it is not near one. A press within *corner*
    pixels of both an x edge and a y edge takes both."""
    if not rect.collidepoint(pos):
        return 0
    x, y = pos
    dl, dr = x - rect.left, rect.right - 1 - x
    dt, db = y - rect.top, rect.bottom - 1 - y
    x_edge = EDGE_L if dl <= dr else EDGE_R
    y_edge = EDGE_T if dt <= db else EDGE_B
    nx, ny = min(dl, dr), min(dt, db)
    if nx < corner and ny < corner:
        return x_edge | y_edge
    if nx < grab:
        return x_edge
    if ny < grab:
        return y_edge
    return 0


def resized_rect(start: pygame.Rect, edges: int, dx: int, dy: int, bounds_w: int, bounds_h: int,
                 min_w: int, min_h: int) -> pygame.Rect:
    """*start* with the dragged *edges* moved by (dx, dy), kept on screen and at least the minimum size."""
    left, top, right, bottom = start.left, start.top, start.right, start.bottom
    min_w, min_h = min(int(min_w), int(bounds_w)), min(int(min_h), int(bounds_h))
    if edges & EDGE_L:
        left = max(0, min(left + dx, right - min_w))
    if edges & EDGE_R:
        right = min(int(bounds_w), max(right + dx, left + min_w))
    if edges & EDGE_T:
        top = max(0, min(top + dy, bottom - min_h))
    if edges & EDGE_B:
        bottom = min(int(bounds_h), max(bottom + dy, top + min_h))
    return pygame.Rect(left, top, right - left, bottom - top)


def moved_rect(start: pygame.Rect, dx: int, dy: int, bounds_w: int, bounds_h: int) -> pygame.Rect:
    x = max(0, min(start.x + dx, int(bounds_w) - start.w))
    y = max(0, min(start.y + dy, int(bounds_h) - start.h))
    return pygame.Rect(x, y, start.w, start.h)


def plate_chamfer(h: int, chamfer_max: int) -> int:
    """The top-right cut of the panel plate (render_cache.build_hud_panel_plate's own rule)."""
    return min(max(8, h // 7), max(8, int(chamfer_max)))


def draw_plate(screen, x: int, y: int, w: int, h: int, header_h: int, chamfer: int) -> None:
    """The panel plate drawn with primitives, step for step what render_cache.build_hud_panel_plate bakes:
    chamfered body, header band with its rule, steel border, powered top edge, bottom-left bracket."""
    from render_cache import _chamfer_points, _row_right_x
    top_cut = chamfer
    pts = [(x + px, y + py) for px, py in _chamfer_points(w, h, tr=top_cut, br=max(4, top_cut // 2))]
    previous = screen.get_clip()                      # the baked plate is clipped to its own surface:
    screen.set_clip(pygame.Rect(x, y, w, h).clip(previous))   # a width-2 line must not spill a pixel outside
    try:
        pygame.draw.polygon(screen, (7, 11, 20, 234), pts)
        for hy in range(0, min(header_h, h)):
            pygame.draw.line(screen, (13, 21, 38, 244), (x, y + hy), (x + _row_right_x(w, hy, top_cut), y + hy))
        pygame.draw.line(screen, (45, 80, 130, 255), (x + 1, y + header_h), (x + w - 2, y + header_h))
        pygame.draw.polygon(screen, (56, 88, 138, 255), pts, 1)
        pygame.draw.line(screen, (95, 170, 255, 255), (x, y), (x + w - 1 - top_cut, y), 2)
        pygame.draw.line(screen, (95, 170, 255, 255), (x + w - 1 - top_cut, y), (x + w - 1, y + top_cut), 2)
        pygame.draw.line(screen, (95, 170, 255, 70), (x, y + 2), (x + w - 1 - top_cut, y + 2))
        bracket = max(6, h // 12)
        pygame.draw.line(screen, (70, 120, 190, 255), (x + 2, y + h - 2), (x + 2 + bracket, y + h - 2))
        pygame.draw.line(screen, (70, 120, 190, 255), (x + 2, y + h - 2), (x + 2, y + h - 2 - bracket))
    finally:
        screen.set_clip(previous)

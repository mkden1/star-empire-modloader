"""mods_page.py - the ESC menu's "Mods" page: which mods are loaded, and why one is not.

Read-only. Every mod folder is a cell in a grid, with a dot: green = in effect, red = switched off because the
game's code changed under one of its patches, grey = turned off on purpose (STAR_EMPIRE_DISABLE_MODS or
"enabled": false), blue = a library of code other mods use (no patch of its own). The box under the grid says what
the mod under the pointer (or the one you clicked) does, which parts of the game it patches, what it needs and, for
a red one, the reason the loader gave. Nothing here changes the game; the state is ``modloader.installed()``.

Drawn from render_mixin's ESC-menu code and clicked from Client.py's event loop through a few one-line hooks, the
same way capture_settings_panel.py's Data Capture page is (see settings_page.py for the shared look).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import pygame

import modloader

ON, FAILED, SKIPPED, LIBRARY = "on", "failed", "skipped", "library"
COLOURS = {ON: (110, 230, 110), FAILED: (255, 110, 110), SKIPPED: (150, 150, 165), LIBRARY: (110, 170, 255)}
WORDS = {ON: "in effect", FAILED: "SWITCHED OFF", SKIPPED: "turned off on purpose", LIBRARY: "library"}
EMPTY_HINT = ("Hover or click a mod to see what it does. A mod is a patch to the game's code kept in mods/; "
              "one that no longer fits after a game update is switched off and the game starts without it "
              "(mods_report.txt has the reason).")


@dataclass
class Entry:
    name: str
    status: str
    description: str = ""
    reasons: "list[str]" = field(default_factory=list)
    patches: str = ""                 # "3 hunks in Client, render_mixin"
    requires: "tuple[str, ...]" = ()


def entries(info=None) -> "list[Entry]":
    """One entry per mod folder, in the loader's order. Empty when the loader never ran."""
    info = info if info is not None else modloader.installed()
    if info is None:
        return []
    result = info.result
    out = []
    for mod in info.mods:
        reasons: "list[str]" = []
        if mod.name in result.failed:
            status, reasons = FAILED, list(result.failed[mod.name])
        elif mod.name in result.skipped:
            status = SKIPPED
        elif mod.name in result.applied:
            status = ON if mod.hunks else LIBRARY
        else:                              # the loader was switched off altogether
            status = SKIPPED
            reasons = ["STAR_EMPIRE_NO_MODS is set: the game runs as the dev shipped it"]
        if mod.name in modloader.ATTACH_FAILURES and status in (ON, LIBRARY):
            # its patches applied, but its code could not be attached to the game's classes at start-up
            status, reasons = FAILED, [modloader.ATTACH_FAILURES[mod.name]]
        files = ", ".join(f[:-3] if f.endswith(".py") else f for f in mod.files)
        n = len(mod.hunks)
        patches = f"{n} hunk{'s' if n != 1 else ''} in {files}" if n else "no patches of its own (code for other mods)"
        out.append(Entry(mod.name, status, mod.description, reasons, patches, tuple(mod.requires)))
    return out


def summary(items: "list[Entry]") -> str:
    if not items:
        return "The mod loader did not run (the game was started without it)."
    counts = {s: sum(1 for e in items if e.status == s) for s in (ON, FAILED, SKIPPED, LIBRARY)}
    parts = [f"{counts[ON] + counts[LIBRARY]} loaded"]
    if counts[FAILED]:
        parts.append(f"{counts[FAILED]} SWITCHED OFF")
    if counts[SKIPPED]:
        parts.append(f"{counts[SKIPPED]} off on purpose")
    return " - ".join(parts)


def _wrap(font, text: str, width: int, max_lines: int) -> "list[str]":
    lines, line = [], ""
    for word in text.split():
        trial = (line + " " + word).strip()
        if font.size(trial)[0] <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" .,;") + " ..."
    return lines


def _fit(font, text: str, width: int) -> str:
    if font.size(text)[0] <= width:
        return text
    while text and font.size(text + "...")[0] > width:
        text = text[:-1]
    return text + "..."


def _layout(s, n: int, sw: int, sh: int):
    """Columns, panel size: the fewest columns that make the panel fit the window."""
    pad, row_h = s(16), s(22)
    header_h, footer_h = s(56), s(34) + s(24)
    best = None
    for col_px, detail_px in ((204, 112), (170, 96), (150, 84)):        # roomy first, then tighter
        col_w, detail_h = s(col_px), s(detail_px)
        for cols in range(3, 9):
            rows_n = max(1, math.ceil(n / cols))
            panel_w = pad * 2 + cols * col_w
            panel_h = header_h + rows_n * row_h + s(10) + detail_h + footer_h
            best = (cols, rows_n, panel_w, panel_h, pad, row_h, col_w, header_h, detail_h)
            if panel_h <= sh - s(12) and panel_w <= sw - s(12):
                return best
    return best                                                       # nothing fits: the tightest one


def draw(win, screen) -> None:
    """Draw the page centred on ``screen``; leaves each cell's rect (and the panel's and Close's) on ``win``."""
    s = win._s
    cached = win._cached_label
    font_med, font_sm = win._F(13), win._F(11)
    sw, sh = screen.get_size()
    items = entries()
    cols, rows_n, panel_w, panel_h, pad, row_h, col_w, header_h, detail_h = _layout(s, len(items), sw, sh)
    px, py = max(0, (sw - panel_w) // 2), max(s(6), (sh - panel_h) // 2)

    dim = pygame.Surface((sw, sh), pygame.SRCALPHA)
    dim.fill((0, 0, 0, 140))
    screen.blit(dim, (0, 0))
    panel = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
    panel.fill((10, 12, 28, 235))
    screen.blit(panel, (px, py))
    pygame.draw.rect(screen, (30, 60, 160), (px, py, panel_w, panel_h), 1, border_radius=6)
    rects = {"panel": pygame.Rect(px, py, panel_w, panel_h)}

    title = cached("MODS", (100, 160, 255), font_med)
    screen.blit(title, (px + (panel_w - title.get_width()) // 2, py + s(10)))
    sub = cached(summary(items), COLOURS[FAILED] if any(e.status == FAILED for e in items) else (120, 140, 190), font_sm)
    screen.blit(sub, (px + (panel_w - sub.get_width()) // 2, py + s(32)))

    mouse = pygame.mouse.get_pos()
    selected = getattr(win, "_mods_page_selected", None)
    shown: Optional[Entry] = None
    y0 = py + header_h
    for index, entry in enumerate(items):
        col, row = divmod(index, rows_n)                       # down each column, then across
        cell = pygame.Rect(px + pad + col * col_w, y0 + row * row_h, col_w - s(6), row_h - s(2))
        rects["cell:" + entry.name] = cell
        hovered = cell.collidepoint(mouse)
        if hovered:
            shown = entry
        elif shown is None and entry.name == selected:
            shown = entry
        if hovered or entry.name == selected:
            pygame.draw.rect(screen, (30, 50, 100) if hovered else (22, 36, 76), cell, border_radius=4)
        pygame.draw.circle(screen, COLOURS[entry.status], (cell.x + s(10), cell.centery), s(5))
        label = cached(_fit(font_med, entry.name, cell.width - s(26)), (200, 215, 245), font_med)
        screen.blit(label, (cell.x + s(22), cell.centery - label.get_height() // 2))
    if shown is None and selected:                             # the pointer left the grid: keep the clicked one
        shown = next((e for e in items if e.name == selected), None)

    box = pygame.Rect(px + pad, y0 + rows_n * row_h + s(10), panel_w - pad * 2, detail_h)
    pygame.draw.rect(screen, (14, 18, 40), box, border_radius=4)
    pygame.draw.rect(screen, (40, 60, 130), box, 1, border_radius=4)
    tx, ty, tw = box.x + s(10), box.y + s(8), box.width - s(20)
    line_h = font_sm.get_linesize()
    if shown is None:
        for text in _wrap(font_sm, EMPTY_HINT, tw, 5):
            screen.blit(cached(text, (140, 150, 185), font_sm), (tx, ty))
            ty += line_h
    else:
        head = cached(f"{shown.name} - {WORDS[shown.status]}", COLOURS[shown.status], font_med)
        screen.blit(head, (tx, ty))
        ty += head.get_height() + s(4)
        facts = [(shown.description or "(no description)", (190, 200, 230), 2), (shown.patches, (140, 150, 185), 1)]
        if shown.requires:
            facts.append(("Needs: " + ", ".join(shown.requires), (140, 150, 185), 1))
        for reason in shown.reasons[:1]:
            facts.append((reason, COLOURS[FAILED] if shown.status == FAILED else (170, 170, 120), 3))
        for text, colour, max_lines in facts:
            for line in _wrap(font_sm, text, tw, max_lines):
                if ty + line_h > box.bottom - s(4):
                    break
                screen.blit(cached(line, colour, font_sm), (tx, ty))
                ty += line_h

    close = pygame.Rect(px + pad, box.bottom + s(12), panel_w - pad * 2, s(34))
    win._btn(screen, close, cached("Close  [ESC]", (120, 150, 220)),
             fill=(20, 20, 50), border=(60, 80, 160), border_w=1, radius=4)
    rects["close"] = close
    win._mods_page_rects = rects


def click(win, pos) -> None:
    """A left click while the page is open (the caller swallows the event): select a mod, or close the page."""
    rects = getattr(win, "_mods_page_rects", None) or {}
    for name, rect in rects.items():
        if name.startswith("cell:") and rect.collidepoint(pos):
            win._mods_page_selected = name[5:]
            return
    close, panel = rects.get("close"), rects.get("panel")
    if (close is not None and close.collidepoint(pos)) or (panel is not None and not panel.collidepoint(pos)):
        close_page(win)


def close_page(win) -> None:
    win._mods_page_open = False
    win._mods_page_selected = None
    win._ui_panels_settings_open = False

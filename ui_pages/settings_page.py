"""settings_page.py — a centred column of ON/OFF buttons, as one of the ESC menu's pages.

The drawing and click handling behind the "Data Capture" page (capture_settings_panel.py): a title,
one button per switch, a few lines of notes and a Close button, in the same style as the "UI Panels"
page. A page is described by a
``PageSpec``; ``win`` is the SolarSystemWindow, which supplies the UI scale ``_s``, fonts ``_F``,
the label cache ``_cached_label`` and the button painter ``_btn``.

Kept out of Client.py / render_mixin.py on purpose: those change in every vanilla update and are
merged by hand when they collide, so a page is reached from them with a few one-line hooks (an
"open" flag and a call to ``draw`` / ``click``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import pygame

GREEN = ((20, 60, 20), (80, 220, 80), (140, 255, 140))       # fill, border, text
AMBER = ((60, 42, 12), (230, 165, 55), (255, 210, 120))
OVERRIDDEN = ((50, 50, 20), (160, 160, 60), (210, 210, 130))  # ON, but a master switch says no
OFF = ((20, 20, 50), (80, 100, 180), (160, 180, 220))


@dataclass
class PageSpec:
    title: str
    subtitle: str
    hint: str
    rows: Callable[[], "list[tuple[str, str]]"]          # (key, label)
    note_lines: Callable[[], "list[str]"]
    state: Callable[[], "dict[str, bool]"]
    toggle: Callable[[str], object]
    rects_attr: str                                       # where draw() leaves the hit rects
    flag_attr: str                                        # the window's "this page is open" flag
    master: Optional[str] = None                          # key of a switch that overrides...
    master_keys: Callable[[], "set[str]"] = field(default=lambda: set())   # ...these
    on_colours: tuple = GREEN


def draw(win, screen, spec: PageSpec) -> None:
    """Draw the page centred on ``screen`` and record each button's rect on ``win``."""
    s = win._s
    cached = win._cached_label
    font_med = win._F(13)
    font_sm = win._F(11)
    sw, sh = screen.get_size()
    state = spec.state()
    master_on = state.get(spec.master, True) if spec.master else True
    overridden_keys = spec.master_keys()
    rows = spec.rows()
    note_lines = spec.note_lines()

    pad = s(16)
    # 24 + 4 px per row: thirteen rows (the Data Capture page) must still fit at the largest UI scale
    button_h = s(24)
    row_gap = s(4)
    row_h = button_h + row_gap
    note_h = font_sm.get_linesize()
    panel_w = s(360)
    panel_h = (s(54) + len(rows) * row_h + s(6)
               + note_h * (1 + len(note_lines)) + s(10) + s(34) + s(14))
    px = (sw - panel_w) // 2
    py = max(s(6), (sh - panel_h) // 2)

    dim = pygame.Surface((sw, sh), pygame.SRCALPHA)
    dim.fill((0, 0, 0, 140))
    screen.blit(dim, (0, 0))
    panel = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
    panel.fill((10, 12, 28, 235))
    screen.blit(panel, (px, py))
    pygame.draw.rect(screen, (30, 60, 160), (px, py, panel_w, panel_h), 1, border_radius=6)

    rects = {"panel": pygame.Rect(px, py, panel_w, panel_h)}

    title = cached(spec.title, (100, 160, 255), font_med)
    screen.blit(title, (px + (panel_w - title.get_width()) // 2, py + s(10)))
    subtitle = cached(spec.subtitle, (120, 140, 190), font_sm)
    screen.blit(subtitle, (px + (panel_w - subtitle.get_width()) // 2, py + s(30)))

    y = py + s(54)
    for key, label in rows:
        on = bool(state.get(key, True))
        live = on and (master_on or key not in overridden_keys)
        rect = pygame.Rect(px + pad, y, panel_w - pad * 2, button_h)
        fill, border, text = (spec.on_colours if live else OVERRIDDEN if on else OFF)
        suffix = "ON" if on else "OFF"
        if on and not live:
            suffix = "ON (master off)"
        win._btn(screen, rect, cached(f"{label}: {suffix}", text, font_med),
                 fill=fill, border=border, border_w=1, radius=4)
        rects["row:" + key] = rect
        y += row_h
    y += s(6)

    hint = cached(spec.hint, (120, 140, 190), font_sm)
    screen.blit(hint, (px + pad, y))
    y += note_h
    for line in note_lines:
        screen.blit(cached(line, (150, 150, 120), font_sm), (px + pad, y))
        y += note_h
    y += s(10)

    close = pygame.Rect(px + pad, y, panel_w - pad * 2, s(34))
    win._btn(screen, close, cached("Close  [ESC]", (120, 150, 220)),
             fill=(20, 20, 50), border=(60, 80, 160), border_w=1, radius=4)
    rects["close"] = close
    setattr(win, spec.rects_attr, rects)


def click(win, pos, spec: PageSpec) -> None:
    """Handle a left click while the page is open (the caller then swallows the event)."""
    rects = getattr(win, spec.rects_attr, None) or {}
    for name, rect in rects.items():
        if name.startswith("row:") and rect.collidepoint(pos):
            spec.toggle(name[4:])
            return
    close = rects.get("close")
    panel = rects.get("panel")
    if (close is not None and close.collidepoint(pos)) or (
            panel is not None and not panel.collidepoint(pos)):
        close_page(win, spec)


def close_page(win, spec: PageSpec) -> None:
    setattr(win, spec.flag_attr, False)
    win._ui_panels_settings_open = False

"""capture_settings_panel.py — the in-game "Data Capture" page (ESC menu -> Data Capture).

A column of ON/OFF buttons, one per data logger plus the master switch and the freeze watchdog,
styled like the "UI Panels" page next to it. The state itself lives in capture_settings.py (a
click calls ``capture_settings.toggle`` -- it takes effect at once and the file is saved on a
background thread, so clicking never touches the disk on the render thread). The drawing and
click handling are the shared ones in settings_page.py.

Kept in its own module on purpose: Client.py and render_mixin.py change in every vanilla update
and are merged by hand when they collide, so the page is reached from them with a handful of
one-line hooks (``_capture_settings_open``) rather than a hundred lines of drawing code.
"""

from __future__ import annotations

import capture_settings
import settings_page

_TITLE = "DATA CAPTURE"
_SUBTITLE = "What this client records to disk"
_HINT = "Changes apply at once and are saved for this client."


def _rows() -> "list[tuple[str, str]]":
    rows = [(capture_settings.MASTER, "All data capture")]
    rows += [(key, label) for key, label, _file, _note in capture_settings.DATA_ENTRIES]
    rows.append((capture_settings.WATCHDOG, "Freeze watchdog"))
    rows.append((capture_settings.CLICK_DIAG, "Click diagnostics"))
    rows.append((capture_settings.SOUND_PREFETCH, "Sound prefetch"))
    return rows


def _note_lines() -> "list[str]":
    """Footnotes, one entry per printed line (a note may span several, split on newlines)."""
    lines: "list[str]" = []
    for _key, _label, _file, note in capture_settings.DATA_ENTRIES:
        if note:
            lines.extend(note.split("\n"))
    return lines


SPEC = settings_page.PageSpec(
    title=_TITLE, subtitle=_SUBTITLE, hint=_HINT,
    rows=_rows, note_lines=_note_lines,
    state=capture_settings.snapshot, toggle=capture_settings.toggle,
    rects_attr="_capture_rects", flag_attr="_capture_settings_open",
    master=capture_settings.MASTER,
    master_keys=lambda: set(capture_settings.DATA_KEYS))


def draw(win, screen) -> None:
    settings_page.draw(win, screen, SPEC)


def click(win, pos) -> None:
    settings_page.click(win, pos, SPEC)


def close_page(win) -> None:
    settings_page.close_page(win, SPEC)

"""capture_settings_panel.py — the in-game "Data Capture" page (ESC menu -> Data Capture).

A column of ON/OFF buttons, one per data logger plus the master switch and the freeze watchdog,
styled like the "UI Panels" page next to it. Only the switches of mods that are in effect are listed (a player who
did not pick a recorder is not shown its options); with none, the page says so. The state itself lives in capture_settings.py (a
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
    mods = capture_settings.installed_mods()
    listed = lambda key: capture_settings.listed(key, mods)
    rows: "list[tuple[str, str]]" = []
    if listed(capture_settings.MASTER):
        rows.append((capture_settings.MASTER, "All data capture"))
    rows += [(key, label) for key, label, _file, _note in capture_settings.DATA_ENTRIES if listed(key)]
    for key, label in ((capture_settings.WATCHDOG, "Freeze watchdog"), (capture_settings.CLICK_DIAG, "Click diagnostics"),
                       (capture_settings.SOUND_PREFETCH, "Sound prefetch")):
        if listed(key):
            rows.append((key, label))
    return rows


def _note_lines() -> "list[str]":
    """Footnotes, one entry per printed line (a note may span several, split on newlines)."""
    mods = capture_settings.installed_mods()
    lines: "list[str]" = []
    for key, _label, _file, note in capture_settings.DATA_ENTRIES:
        if note and capture_settings.listed(key, mods):
            lines.extend(note.split("\n"))
    if not _rows():
        lines = ["No mod that records data or writes diagnostics", "is switched on. Choose Mods.bat lists them."]
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

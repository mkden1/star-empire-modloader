"""station_auto_name.py - an AUTO NAME button on the station rename dialog.

Stations are named "<planet> - <n>" ("Casashius - 1", "Casashius - 2", ...). The button fills the dialog's name
field with the next name of that kind for the station you are docked at:

  * <planet> is the planet or moon the station is built on (station_bodies.body_of in core: the dock payload's planet id
    looked up in the loaded system layout). If the stations already named for it spell it differently ("rylvyron - 1"
    next to a planet called Rylvyron) their spelling wins, so the new name matches theirs;
  * <n> is the lowest number no other station in the system already uses with that name. The station being renamed is
    left out of the count, so pressing the button on "Casashius - 2" while 1 and 3 exist gives "Casashius - 2" back,
    and a gap left by a demolished station is filled before a new number is started.

It only fills the field. CONFIRM (or Enter) still sends the rename, so the suggestion can be edited first. The names
come from the system's deployed-station list, so a station in another system is never counted (nor can it be docked
at while you are in this one).

Hooks (all marked MOD): the button is drawn with the rename dialog in render_mixin.py (``_draw_station_overlay``), and
``click`` is called from ``_handle_ps_rename_pointer_event`` in Client.py. A failure switches the button off for the
session; the dialog works as before. Kill switch: STAR_EMPIRE_NO_STATION_AUTO_NAME=1.
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter

import station_bodies

logger = logging.getLogger(__name__)

LABEL = "AUTO NAME"
NAME_MAX = 40   # the rename field's max_length (TextField(..., max_length=40) where Client.py opens the dialog)

_failed = False


def _switch_off(where: str) -> None:
    global _failed
    if not _failed:
        logger.exception("station_auto_name: %s failed; the AUTO NAME button is switched off for this session", where)
    _failed = True


def enabled() -> bool:
    return not _failed and os.environ.get("STAR_EMPIRE_NO_STATION_AUTO_NAME") != "1"


def reset_for_tests() -> None:
    global _failed
    _failed = False


def _station_id(station: dict):
    return station.get("attached_station_id") or station.get("id")


def next_name(planet_name, stations, own_station_id=None, max_len: int = NAME_MAX) -> "str | None":
    """The next "<planet> - <n>" name, or None when the planet's name is unknown.

    ``stations`` are deployed-station dicts (``display_name`` plus ``attached_station_id`` / ``id``); the one whose id
    is ``own_station_id`` is the station being renamed and does not count."""
    base = str(planet_name or "").strip()
    if not base:
        return None
    pattern = re.compile(r"(" + re.escape(base) + r")\s*-\s*([0-9]+)", re.IGNORECASE)
    taken = set()
    spellings = Counter()
    for station in stations or ():
        if not isinstance(station, dict):
            continue
        sid = _station_id(station)
        if own_station_id is not None and sid is not None and str(sid) == str(own_station_id):
            continue
        match = pattern.fullmatch(str(station.get("display_name") or "").strip())
        if match:
            taken.add(int(match.group(2)))
            spellings[match.group(1)] += 1
    # The commonest existing spelling; on a tie the one met first (Counter keeps insertion order).
    prefix = spellings.most_common(1)[0][0] if spellings else base
    number = 1
    while number in taken:
        number += 1
    suffix = f" - {number}"
    prefix = prefix[:max(0, max_len - len(suffix))].rstrip()
    return prefix + suffix if prefix else None


def suggestion(window, dialog) -> "str | None":
    """What AUTO NAME would put in the field of the open rename ``dialog``, or None when the planet is not known."""
    if not enabled():
        return None
    try:
        with window._lock:
            overlay = getattr(window, "_station_overlay", None)
            if not overlay or overlay.get("kind") != "player_station":
                return None
            data = dict(overlay.get("data") or {})
            layout = getattr(window, "_layout", None) or {}
            my_stations = list(getattr(window, "_my_stations_data", None) or ())
            deployed = list(getattr(window, "_deployed_stations", None) or ())
        station_id = dialog.get("station_id")
        body = station_bodies.body_of(data, layout, my_stations, station_id)
        return next_name(body.get("name"), deployed, station_id)
    except Exception:
        _switch_off("suggestion")
        return None


def click(window, dialog, pos) -> bool:
    """A left press in the rename dialog: True when it hit the AUTO NAME button, whose name is now in the field."""
    rect = dialog.get("auto_rect") if enabled() else None
    if rect is None or not rect.collidepoint(pos):
        return False
    name = suggestion(window, dialog)
    field = dialog.get("field")
    if name and field is not None:
        field.set_text(name)   # v0.5.73: the name box is a TextField - new text drops the selection, caret at the end
    return True

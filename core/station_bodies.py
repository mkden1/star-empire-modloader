"""station_bodies.py - which planet or moon a station is built on (core).

A pure function over the layout the game has loaded and the dock payload. station_profile (the snapshot of each station
you undock from) and station_auto_name (the AUTO NAME button) both need it; the second should not need the first mod,
which records snapshots, just to ask this.
"""

from __future__ import annotations


def same(a, b) -> bool:
    """Two ids are the same whether the server sent them as numbers or as text."""
    try:
        return int(a) == int(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def body_of(data, layout, my_stations, station_id) -> dict:
    """The planet or moon the station is built on, as ``{"name", "kind", "parent"}`` (``kind`` is "planet" / "moon",
    None when unknown). The loaded system layout knows the body by the dock payload's ``planet_id``; the Possessions
    station list (when it has been opened) names it too, and fills in when the layout does not."""
    planets = [p for p in (layout or {}).get("planets", ()) if isinstance(p, dict)]
    planet_id = data.get("planet_id")
    body = next((p for p in planets if planet_id is not None and same(p.get("id"), planet_id)), None)
    if body is not None and body.get("name"):
        parent = next((p for p in planets if body.get("is_moon") and same(p.get("id"), body.get("parent_planet_id"))),
                      None)
        return {"name": str(body["name"]), "kind": "moon" if body.get("is_moon") else "planet",
                "parent": str(parent["name"]) if parent and parent.get("name") else None}
    row = next((r for r in my_stations if isinstance(r, dict) and same(r.get("station_id"), station_id)), None)
    if row is not None and row.get("body_name"):
        kind = str(row.get("body_kind") or "").lower()
        return {"name": str(row["body_name"]), "kind": kind if kind in ("planet", "moon") else None, "parent": None}
    return {"name": None, "kind": None, "parent": None}

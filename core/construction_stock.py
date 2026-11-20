"""construction_stock.py - what the Construction Bay mods share (core).

Reading a number out of a server row, and a station's stock of each resource out of its cargo rows. construction_eta (the
time estimate next to each project) and construction_tooltip (the cost box for a selected blueprint) both need them, and
neither should need the other mod just for these.
"""

from __future__ import annotations

import math


def num(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def stock_from_cargo(cargo) -> "dict[str, float] | None":
    """The station's stock of each resource from its cargo rows (a resource's amount is its cargo_size)."""
    if not isinstance(cargo, (list, tuple)):
        return None
    stock = {}
    for row in cargo:
        if isinstance(row, dict) and row.get("item_category") == "resource" and row.get("item_key") != "workers":
            stock[str(row.get("item_key"))] = num(row.get("cargo_size", row.get("quantity", 0)))
    return stock

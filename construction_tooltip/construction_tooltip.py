"""construction_tooltip.py - what a selected Construction Bay blueprint costs against what the station holds.

Click a blueprint in the Available Blueprints list (the game's own click opens its info popup) and the popup
shows everything it always did - name, man-hours, the INITIAL and PERIODIC costs, the description - plus what
the station has of each next to it and a verdict, so you can see whether you can start the build:

    Station Expander Alpha
    ------------------
      Manhours: 10
    ------------------
      PERIODIC COST
      Credits: 1,000   (in stock: 122,402,974)
      Metal: 50   (in stock: 18,171)
    ------------------
      Ready: stock covers 363 builds (Metal)

A cost the station is short of is coloured (red for what is needed up front, amber for what is taken as the
build goes - a project short of one of those waits for it, it is not refused), and the verdict names what is
missing: "Missing up front: ...", "Can start, but will wait for: ...", "No uses left on this blueprint". Costs are
per ONE build; "stock covers N builds" is the floor of stock / cost over every cost, limited by the blueprint's
remaining uses. Stock: resources from the station cargo, credits from the station's credits, item ingredients
from the cargo's item rows by item type minus the modules equipped on the station (a cargo row's quantity counts
those too). Nothing is guessed: unknown cargo or credits mean no stock text and no "Ready".

How it is hooked: the game draws its Construction Bay popups through ``_draw_cb_side_popup(screen, lines,
anchor_y, ...)`` (the blueprint popup and the selected project's detail popup share it); ``install`` wraps it so
that, for the BLUEPRINT popup, the lines it is given are replaced by ours (and the anchor by the blueprint's row as
it is now, since the list scrolls and the search narrows it). The game still does all the placing, clamping and
drawing, nothing is hidden and no game state is touched, and the project popup (the game's own, with the
server's figures) passes through unchanged. ``STAR_EMPIRE_NO_CONSTRUCTION_TOOLTIP=1`` turns it off; an error
switches it off for the session; either way the game's own popup is what you see.

(Before v0.5.59 this module also drew a "resources left" box for a selected project; the game now has its own
detail popup for that, from the server's estimates, so that part is gone.)
"""

from __future__ import annotations

import logging
import math
import os

from construction_stock import num as _num, stock_from_cargo   # core: shared with construction_eta
from tooltip_formatting import format_tooltip_value

logger = logging.getLogger(__name__)

ENV_OFF = "STAR_EMPIRE_NO_CONSTRUCTION_TOOLTIP"
_failed = False
_SEPARATOR = "------------------"


def _enabled() -> bool:
    return not _failed and not os.environ.get(ENV_OFF)


def _pretty(key: str) -> str:
    return str(key).replace("_", " ").title()


def _amount(value: float) -> str:
    return "<1" if value < 1 else format_tooltip_value(int(math.ceil(value - 1e-9)))


def _have_text(have) -> str:
    """The stock as shown: a number, "<1" for a fraction, "none" for nothing at all."""
    return _amount(_num(have)) if _num(have) > 0 else "none"


def _stock_text(have) -> str:
    return f"   (in stock: {_have_text(have)})"


# ---- the costs of a blueprint against what the station holds ---------------------------------------------

_OLD_SERVER_COSTS = ("metal", "silicon", "copper", "titanium", "gold", "oil", "wood", "microchips", "colonists",
                     "rations")                 # the fixed cost_* fields, in the order the game's popup lists them
_DESCRIPTION_WIDTH = 32                         # where the game's popup wraps a description
_SLACK = 1e-9


def _shown(amount) -> str:
    number = _num(amount)
    return format_tooltip_value(int(number) if number == int(number) else round(number, 3))


def cost_groups(bp: dict) -> "tuple[list, list]":
    """(initial, periodic): what ONE build of a blueprint costs, as [(kind, key, label, amount)] in the order the
    game's popup lists them. kind is "credits", "resource" or "item"; key is the resource name, or the item type
    of an ingredient. Initial = taken when the build is queued (initial resources, item ingredients); periodic =
    taken as it builds (credits and the other resources). An older server without a resource_costs list has only
    the fixed cost_* fields."""
    initial: list = []
    periodic: list = []
    credits = _num(bp.get("cost_credits"))
    if credits > 0:
        periodic.append(("credits", "credits", "Credits", credits))
    if "resource_costs" in bp:
        costs = bp["resource_costs"]
        for cost in costs if isinstance(costs, (list, tuple)) else ():
            if not isinstance(cost, dict) or not (cost.get("resource") or cost.get("display_name")):
                continue
            amount = _num(cost.get("amount"))
            if amount > 0:
                key = str(cost.get("resource") or "")
                label = cost.get("display_name") or _pretty(key)
                (initial if cost.get("timing") == "initial" else periodic).append(("resource", key, label, amount))
    else:
        for key in _OLD_SERVER_COSTS:
            amount = _num(bp.get("cost_" + key))
            if amount > 0:
                periodic.append(("resource", key, _pretty(key), amount))
    ingredients = bp.get("ingredients")
    for ingredient in ingredients if isinstance(ingredients, (list, tuple)) else ():
        if not isinstance(ingredient, dict):
            continue
        amount = _num(ingredient.get("amount"), 1.0)
        if amount > 0:
            item_type = str(ingredient.get("item_type") or "")
            initial.append(("item", item_type, ingredient.get("display_name") or _pretty(item_type) or "?", amount))
    return initial, periodic


def items_from_cargo(cargo, equipped=None) -> "dict[str, float] | None":
    """How many of each item (by item type) the station holds in storage; None when its cargo is not known. A
    cargo row's quantity counts the modules equipped on the station too (the game's storage list subtracts
    them), and a build cannot take those."""
    if not isinstance(cargo, (list, tuple)):
        return None
    held: "dict[str, float]" = {}
    for row in cargo:
        key = row.get("item_key") if isinstance(row, dict) else None
        if not isinstance(key, str) or not key.startswith("item:"):
            continue
        parts = key.split(":")
        item_type = str(row.get("item_type") or (parts[2] if len(parts) > 2 else ""))
        if item_type:
            held[item_type] = held.get(item_type, 0.0) + max(0.0, _num(row.get("quantity"), 1.0))
    if isinstance(equipped, dict):
        for item_type in held:
            held[item_type] = max(0.0, held[item_type] - _num(equipped.get(item_type)))
    return held


def _held(cost, stock, credits, items):
    """What the station has of one cost, or None when that is not known (never a guess)."""
    kind, key = cost[0], cost[1]
    if kind == "credits":
        return None if credits is None else _num(credits)
    if not key:
        return None
    if kind == "item":
        return items.get(key, 0.0) if isinstance(items, dict) else None
    return stock.get(key, 0.0) if isinstance(stock, dict) else None


def _builds(known, uses):
    """(how many builds the stock covers, what limits it) from [(label, amount, have)]; None without a cost."""
    best = None
    for label, amount, have in known:
        count = int((have + _SLACK) // amount)
        if best is None or count < best[0]:
            best = (count, label)
    if best is not None and isinstance(uses, int) and not isinstance(uses, bool) and 0 <= uses < best[0]:
        best = (uses, "blueprint uses")
    return best


def _verdict(missing, known, total, uses, palette) -> list:
    lines: list = []
    if isinstance(uses, int) and not isinstance(uses, bool) and uses == 0:
        lines.append(("  No uses left on this blueprint", palette["short"]))
    for when, heading, colour in (("initial", "  Missing up front:", palette["short"]),
                                  ("periodic", "  Can start, but will wait for:", palette["warn"])):
        if missing[when]:
            lines.append((heading, colour))
            for label, amount, have in missing[when]:
                lines.append((f"    {label}: need {_shown(amount)}, have {_have_text(have)}", colour))
    if not lines and total and len(known) == total:
        count, limit = _builds(known, uses)
        lines.append((f"  Ready: stock covers {count:,} build{'' if count == 1 else 's'} ({limit})", palette["ok"]))
    return lines


def _description(text, palette) -> list:
    lines, current = [], ""
    for word in str(text or "").split():
        if current and len(current) + len(word) + 1 > _DESCRIPTION_WIDTH:
            lines.append(("  " + current.strip(), palette["desc"]))
            current = ""
        current += word + " "
    if current.strip():
        lines.append(("  " + current.strip(), palette["desc"]))
    return lines


def blueprint_lines(bp: dict, stock, credits, items, palette: dict) -> "list[tuple[str, tuple]]":
    """The blueprint popup as [(text, colour)]; the first line is the title. What the game's popup shows, with
    the station's stock beside each cost (when known), a short cost coloured, and a verdict under the costs."""
    initial, periodic = cost_groups(bp)
    lines = [(str(bp.get("display_name") or "?"), palette["title"])]
    hours = _num(bp.get("manhours"))
    if hours > 0:
        lines += [(_SEPARATOR, palette["rule"]), (f"  Manhours: {_shown(hours)}", palette["body"])]
    missing = {"initial": [], "periodic": []}
    known: list = []
    total = 0
    for when, title, group in (("initial", palette["initial_title"], initial),
                               ("periodic", palette["periodic_title"], periodic)):
        if not group:
            continue
        lines += [(_SEPARATOR, palette["rule"]), (f"  {title.upper()}", palette["section"])]
        for cost in group:
            _kind, _key, label, amount = cost
            have = _held(cost, stock, credits, items)
            written = f"x{_shown(amount)}" if cost[0] == "item" else _shown(amount)
            text, colour = f"  {label}: {written}", palette["body"]
            total += 1
            if have is not None:
                text += _stock_text(have)
                known.append((label, amount, have))
                if have + _SLACK < amount:
                    missing[when].append((label, amount, have))
                    colour = palette["short"] if when == "initial" else palette["warn"]
            lines.append((text, colour))
    uses = bp.get("uses_remaining", bp.get("uses"))
    verdict = _verdict(missing, known, total, uses, palette)
    if verdict:
        lines += [(_SEPARATOR, palette["rule"])] + verdict
    description = _description(bp.get("description"), palette)
    if description:
        lines += [(_SEPARATOR, palette["rule"])] + description
    return lines


def _palette(render_module) -> dict:
    def pick(name, default):
        return tuple(getattr(render_module, name, default))[:3]

    return {"title": (*pick("_PS_VALUE", (230, 240, 255)), 255), "body": (180, 220, 255, 255),
            "rule": (60, 90, 130, 255), "section": (*pick("_COST_SECTION_COLOR", (100, 150, 200)), 255),
            "short": (*pick("_PS_WARN", (255, 92, 72)), 255), "warn": (255, 170, 70, 255),
            "ok": (*pick("_PS_SUCCESS", (100, 230, 180)), 255), "desc": (140, 170, 210, 255),
            "initial_title": str(getattr(render_module, "_INITIAL_COST_LABEL", "Initial Cost")),
            "periodic_title": str(getattr(render_module, "_PERIODIC_COST_LABEL", "Periodic Cost"))}


# ---- hooking the game's popup ----------------------------------------------------------------------------

def _blueprint_popup(window):
    """The game's blueprint info popup state when a Construction Bay popup is for a blueprint, else None."""
    overlay = getattr(window, "_station_overlay", None)
    popup = getattr(window, "_ps_cb_bp_popup", None)
    if (overlay and overlay.get("kind") == "player_station"
            and getattr(window, "_ps_tab", None) == "Construction Bay"
            and isinstance(popup, dict) and isinstance(popup.get("bp"), dict)):
        return popup
    return None


def _live_blueprint(window, popup):
    """(the blueprint as the list holds it now, its row rect) - the popup keeps the dict it was clicked with and the
    row's position at the click, but the list is rebuilt every frame (uses go down, the search narrows it, the
    list scrolls). The row rect is None when the blueprint is not on screen."""
    bp = popup["bp"]
    ident = bp.get("id")
    if ident not in (None, ""):
        for _index, rect, live in getattr(window, "_ps_cb_bp_rects", None) or ():
            if isinstance(live, dict) and live.get("id") == ident:
                return live, rect
    return bp, None


def _replacement(window, args: tuple, palette: dict):
    """The (lines, anchor_y) to give the game's popup drawing in place of what it was given, or None when the call
    is not for the blueprint popup. `args` are the positional arguments after self: (screen, lines, anchor_y, ...)."""
    if len(args) < 3 or not args[1]:
        return None
    popup = _blueprint_popup(window)
    # the game draws the project popup only while no blueprint popup is open, but the title says which this is
    if popup is None or args[1][0][0] != popup["bp"].get("display_name", "?"):
        return None
    bp, rect = _live_blueprint(window, popup)
    cargo = getattr(window, "_ps_station_cargo", None)
    lines = blueprint_lines(bp, stock_from_cargo(cargo), getattr(window, "_ps_station_credits", None),
                            items_from_cargo(cargo, getattr(window, "_ps_equipped_module_counts", None)), palette)
    return lines, (rect.top if rect is not None else args[2])


def _switch_off(where: str) -> None:
    global _failed
    _failed = True
    logger.exception("construction_tooltip: switched off for this session after an error %s", where)


def install(window_cls, render_module=None) -> bool:
    """Wrap ``window_cls._draw_cb_side_popup`` (the game's shared Construction Bay popup drawing, on Client.py's
    SolarSystemWindow). Idempotent; False when already hooked or when the game has no such method."""
    if window_cls.__dict__.get("_construction_tooltip_installed"):
        return False
    original = getattr(window_cls, "_draw_cb_side_popup", None)
    if original is None:
        logger.warning("construction_tooltip: the game has no _draw_cb_side_popup; the blueprint stock check is off")
        return False
    palette = _palette(render_module)

    def _draw_cb_side_popup(self, *args, **kwargs):
        if _enabled():
            try:
                found = _replacement(self, args, palette)
                if found is not None:
                    args = (args[0], found[0], found[1]) + tuple(args[3:])
            except Exception:
                _switch_off("while building the blueprint popup")
        return original(self, *args, **kwargs)

    _draw_cb_side_popup.__wrapped__ = original
    _draw_cb_side_popup.__doc__ = original.__doc__
    window_cls._draw_cb_side_popup = _draw_cb_side_popup
    window_cls._construction_tooltip_installed = True
    return True


def reset_for_tests() -> None:
    global _failed
    _failed = False

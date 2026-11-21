"""range_rings.py - range rings drawn around your ship in the world view.

Two rings:

  * WEAPON - the reach of the weapon you have selected: the server's own effective range for it
    (``active_weapon_range`` on your entity, the number firing uses and the minimap's RNG badge shows), so it
    follows plugins, skills and a weapon switch. No range from the server, no ring: a wrong ring is worse than
    none (the minimap makes the same choice).
  * CUSTOM - a ring at a distance you set, in world units (the units of weapon ranges and visibility): how far
    to stay from a boss, a sensor's reach, anything. Or, in ``vis`` mode, your OWN effective visibility times a
    detection rating: the game's rule is that a ship sees you when ``distance <= your visibility x its
    detection`` (stealth.py), so that ring is exactly where a ship with that detection rating starts to see you,
    and it follows your cloak, gear and the spike when you fire.

Set them from chat:

    /rings                      switch every ring on / off (they start OFF)
    /ring                       show what is set
    /ring weapon [on|off]       the weapon ring (no argument: switch it)
    /ring 25000 [label]         the custom ring at 25,000 units (25k works too), and switch it on
    /ring vis [2.5] [label]     the custom ring at your visibility x 2.5 (default x 1)
    /ring on | off              the custom ring on / off, keeping its setting

The choices are kept in ``range_rings.json`` next to the client (git-ignored, written on the background writer
thread). Display only: nothing here reads or sends anything beyond what the client already has.

Hooked in from outside (``install`` is called once from Client.py): ``_draw_turret_aim_overlay``, the game's own
per-frame world overlay, is wrapped to draw the rings after it (the original runs first and unchanged). The one
vanilla line is the ``/ring`` chat command branch, marked ``# MOD``. ``STAR_EMPIRE_NO_RANGE_RINGS=1`` turns the
feature off; any error switches it off for the session.
"""

from __future__ import annotations

import json
import logging
import math
import os

import modloader
import re

import pygame

from scan_writer import submit

logger = logging.getLogger(__name__)

ENV_OFF = "STAR_EMPIRE_NO_RANGE_RINGS"
MAX_LABEL_LEN = 24
MAX_RADIUS = 10_000_000.0          # world units: far beyond any real range, short of float trouble
MAX_MULTIPLIER = 1000.0

WEAPON, CUSTOM = "weapon", "custom"
_COLOURS = {WEAPON: (255, 180, 90), CUSTOM: (255, 96, 96)}      # amber like the minimap's RNG badge; red = keep away
_RING_ALPHA = 215

_failed = False


def _default_path() -> str:
    client_root = str(modloader.REPO_DIR)
    return os.path.join(client_root, "range_rings.json")


_path: str = _default_path()


def _defaults() -> dict:
    # Off until the player asks (/rings): the weapon ring is ready and shows as soon as they do. A saved range_rings.json wins.
    return {"enabled": False, "weapon": True, "custom": False, "mode": "radius", "radius": 0.0, "multiplier": 1.0,
            "label": ""}


settings: dict = _defaults()


def _switch_off(where: str) -> None:
    global _failed
    if not _failed:
        _failed = True
        logger.exception("range_rings: switched off for this session (%s)", where)


def _enabled() -> bool:
    return not _failed and not os.environ.get(ENV_OFF)


# ── settings ─────────────────────────────────────────────────────────────────

def _number(value, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return min(high, max(low, number))


def clean(raw) -> dict:
    """*raw* (anything, e.g. a file's contents) as a valid settings dict; whatever is wrong gets its default."""
    state = _defaults()
    if not isinstance(raw, dict):
        return state
    for key in ("enabled", "weapon", "custom"):
        if isinstance(raw.get(key), bool):
            state[key] = raw[key]
    if raw.get("mode") in ("radius", "vis"):
        state["mode"] = raw["mode"]
    state["radius"] = _number(raw.get("radius"), 0.0, MAX_RADIUS, 0.0)
    state["multiplier"] = _number(raw.get("multiplier"), 0.0, MAX_MULTIPLIER, 1.0)
    label = raw.get("label")
    state["label"] = " ".join(label.split())[:MAX_LABEL_LEN] if isinstance(label, str) else ""
    return state


def load(path: "str | None" = None) -> dict:
    """(Re)read the settings file; a missing or broken one means the defaults. Call at start-up only."""
    global _path, settings
    if path is not None:
        _path = path
    raw = None
    try:
        with open(_path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        logger.warning("range_rings: could not read %s; using defaults", _path)
    settings = clean(raw)
    return dict(settings)


def _write(path: str, state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        logger.exception("range_rings: could not save %s", path)


def save() -> None:
    submit(_write, _path, dict(settings))


def configure_for_tests(path: "str | None") -> None:
    global _path
    _path = _default_path() if path is None else path
    load()


def reset_for_tests() -> None:
    global _failed, settings
    _failed = False
    settings = _defaults()


# ── the rings ────────────────────────────────────────────────────────────────

def format_units(value: float) -> str:
    return f"{value:,.0f}"


def parse_distance(text: str) -> "float | None":
    """``25000``, ``25,000``, ``25k``, ``2.5k``, ``1m`` as world units; None when it is not a positive distance."""
    match = re.fullmatch(r"\s*(\d[\d,]*\.?\d*|\.\d+)\s*([km]?)\s*", str(text).lower())
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    value *= {"": 1.0, "k": 1_000.0, "m": 1_000_000.0}[match.group(2)]
    return value if 0 < value <= MAX_RADIUS else None


class Ring:
    __slots__ = ("kind", "radius", "label")

    def __init__(self, kind: str, radius: float, label: str):
        self.kind, self.radius, self.label = kind, radius, label

    def __repr__(self):
        return f"Ring({self.kind!r}, {self.radius!r}, {self.label!r})"

    def __eq__(self, other):
        return isinstance(other, Ring) and (self.kind, self.radius, self.label) == (other.kind, other.radius, other.label)


def _entity_number(entity, key: str) -> float:
    try:
        number = float((entity or {}).get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number and abs(number) != float("inf") else 0.0


def custom_label(state: dict) -> str:
    if state["label"]:
        return state["label"]
    if state["mode"] == "vis":
        return f"VIS x{state['multiplier']:g}"
    return "CUSTOM"


def rings(state: dict, entity) -> list:
    """The rings to draw for these settings and this ship (the player's entity dict)."""
    if not state["enabled"]:
        return []
    out = []
    if state["weapon"]:
        reach = _entity_number(entity, "active_weapon_range")
        if reach > 0:
            out.append(Ring(WEAPON, reach, "WEAPON"))
    if state["custom"]:
        if state["mode"] == "vis":
            radius = _entity_number(entity, "effective_visibility") * state["multiplier"]
        else:
            radius = state["radius"]
        if 0 < radius <= MAX_RADIUS:
            out.append(Ring(CUSTOM, radius, custom_label(state)))
    return out


def screen_radius(w2s, centre_world, radius: float) -> float:
    """A world distance as screen pixels, through the game's own world-to-screen function."""
    x, y = centre_world
    cx, cy = w2s(x, y)
    ex, ey = w2s(x + radius, y)
    return math.hypot(ex - cx, ey - cy)


def ring_on_screen(centre, radius: float, width: int, height: int) -> bool:
    """Does a circle of *radius* around *centre* cross the screen (a ring bigger than the screen on every
    side, or one that never reaches it, has nothing to draw)?"""
    cx, cy = centre
    nearest_x = min(max(cx, 0), width)
    nearest_y = min(max(cy, 0), height)
    near = math.hypot(cx - nearest_x, cy - nearest_y)
    far = max(math.hypot(cx - px, cy - py) for px in (0, width) for py in (0, height))
    return near <= radius <= far


# the label sits on the ring in the first of these directions (degrees from east, counter-clockwise) that is
# on screen: top right first, so it stays clear of the ship and of the HUD panels' usual corners
_LABEL_ANGLES = (45, 135, -45, -135, 90, 0, 180, -90, 60, 120, 30, 150, -60, -120, -30, -150)


def label_anchor(centre, radius: float, width: int, height: int, margin: int, text_size) -> "tuple | None":
    """Top-left of the label for a ring, or None when no part of the ring where a label fits is on screen."""
    cx, cy = centre
    tw, th = text_size
    for degrees in _LABEL_ANGLES:
        x = cx + radius * math.cos(math.radians(degrees))
        y = cy - radius * math.sin(math.radians(degrees))
        left, top = x - tw / 2, y - th - 3
        if left >= margin and top >= margin and left + tw <= width - margin and top + th <= height - margin:
            return int(left), int(top)
    return None


def _label_surface(win, text: str, colour: tuple):
    cache = win.__dict__.setdefault("_rr_labels", {})
    key = (text, colour)
    surface = cache.get(key)
    if surface is None:
        surface = win._F(11, True).render(text, True, colour)
        try:
            from gl_ui_renderer import mark_static_surface
            mark_static_surface(surface)
        except Exception:
            pass
        if len(cache) >= 64:
            cache.clear()
        cache[key] = surface
    return surface


def draw(win, ctx) -> None:
    """Draw the rings. Not while docked, nor before the ship is known."""
    if getattr(win, "_station_overlay", None) is not None:
        return
    entity = win._find_my_entity()
    if entity is None:
        return
    todo = rings(settings, entity)
    if not todo:
        return
    screen, w2s = ctx.screen, ctx.w2s
    centre = w2s(win._local_x, win._local_y)
    width, height = screen.get_size()
    margin = 6
    for ring in todo:
        radius = screen_radius(w2s, (win._local_x, win._local_y), ring.radius)
        if radius < 3 or not ring_on_screen(centre, radius, width, height):
            continue
        colour = _COLOURS[ring.kind]
        pygame.draw.circle(screen, (*colour, _RING_ALPHA), (centre[0], centre[1]), radius, 2)
        text = f"{ring.label}  {format_units(ring.radius)}"
        label = _label_surface(win, text, colour)
        anchor = label_anchor(centre, radius, width, height, margin, label.get_size())
        if anchor is not None:
            plate = pygame.Rect(anchor[0] - 3, anchor[1] - 1, label.get_width() + 6, label.get_height() + 2)
            pygame.draw.rect(screen, (0, 0, 0, 150), plate)
            screen.blit(label, anchor)


# ── the chat commands ────────────────────────────────────────────────────────

_HELP = ("/rings  on/off   |   /ring weapon [on|off]   |   /ring <distance> [label]   |   "
         "/ring vis [multiplier] [label]   |   /ring on|off")


def describe(state: dict) -> str:
    if not state["enabled"]:
        return "Range rings are off (/rings turns them on)."
    parts = ["weapon ring " + ("on" if state["weapon"] else "off")]
    if state["mode"] == "vis":
        what = f"your visibility x{state['multiplier']:g}"
    elif state["radius"] > 0:
        what = f"{format_units(state['radius'])} units"
    else:
        what = "not set"
    parts.append(f"custom ring {what}" + (f" '{custom_label(state)}'" if state["label"] else "")
                 + (" (on)" if state["custom"] else " (off)"))
    return "Range rings: " + ", ".join(parts) + "."


def apply_command(state: dict, text: str) -> tuple:
    """``(new settings, message, changed)`` for a ``/ring`` / ``/rings`` command line. Pure."""
    state = dict(state)
    words = str(text).split()
    command = words[0].lstrip("/").lower() if words else "ring"
    args = words[1:]
    if command == "rings":
        state["enabled"] = not state["enabled"]
        return state, "Range rings " + ("on." if state["enabled"] else "off."), True
    if not args:
        return state, describe(state) + "  " + _HELP, False
    head = args[0].lower()
    if head == "weapon":
        wanted = args[1].lower() if len(args) > 1 else None
        if wanted not in (None, "on", "off"):
            return state, "Usage: /ring weapon [on|off]", False
        state["weapon"] = (not state["weapon"]) if wanted is None else wanted == "on"
        state["enabled"] = True if state["weapon"] else state["enabled"]
        return state, "Weapon ring " + ("on." if state["weapon"] else "off."), True
    if head in ("on", "off"):
        if head == "on" and state["mode"] == "radius" and state["radius"] <= 0:
            return state, "No custom ring set yet: /ring <distance> [label], or /ring vis [multiplier].", False
        state["custom"] = head == "on"
        state["enabled"] = True if state["custom"] else state["enabled"]
        return state, "Custom ring " + ("on." if state["custom"] else "off."), True
    if head == "vis":
        multiplier = 1.0
        rest = args[1:]
        given = re.fullmatch(r"[x×]?(\d*\.?\d+)", rest[0].lower().replace(",", ".")) if rest else None
        if given:
            multiplier = _number(given.group(1), 0.0, MAX_MULTIPLIER, 1.0)
            rest = rest[1:]
        if multiplier <= 0:
            return state, "The multiplier must be more than 0.", False
        state.update(mode="vis", multiplier=multiplier, custom=True, enabled=True,
                     label=" ".join(rest)[:MAX_LABEL_LEN])
        return state, (f"Custom ring: your visibility x{multiplier:g} - where a ship with that detection rating "
                       f"starts to see you."), True
    distance = parse_distance(head)
    if distance is None:
        return state, "Not a distance: " + args[0] + ".  " + _HELP, False
    state.update(mode="radius", radius=distance, custom=True, enabled=True, label=" ".join(args[1:])[:MAX_LABEL_LEN])
    return state, f"Custom ring at {format_units(distance)} units.", True


def handle_command(win, text: str = "") -> None:
    """``/ring ...`` and ``/rings`` from the chat box."""
    global settings
    if not _enabled():
        win.chat_log("Range rings are switched off.", (255, 180, 60))
        return
    try:
        state, message, changed = apply_command(settings, text)
        if changed:
            settings = clean(state)
            save()
        win.chat_log(message, (100, 200, 255))
    except Exception:
        _switch_off("handle_command()")


# ── hook ─────────────────────────────────────────────────────────────────────

def install(window_cls) -> bool:
    """Wrap ``_draw_turret_aim_overlay`` (the game's per-frame world overlay) to draw the rings after it.
    Idempotent; True if hooked."""
    if window_cls.__dict__.get("_range_rings_installed"):
        return False
    load()
    original = window_cls._draw_turret_aim_overlay

    def _draw_turret_aim_overlay(self, ctx):
        result = original(self, ctx)
        if _enabled():
            try:
                draw(self, ctx)
            except Exception:
                _switch_off("draw()")
        return result

    _draw_turret_aim_overlay.__wrapped__ = original
    _draw_turret_aim_overlay.__doc__ = original.__doc__
    window_cls._draw_turret_aim_overlay = _draw_turret_aim_overlay
    window_cls._range_rings_installed = True
    return True

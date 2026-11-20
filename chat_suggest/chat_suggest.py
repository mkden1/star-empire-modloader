"""chat_suggest.py - suggest system names while you type ``/ap`` in the chat line.

Type ``/ap `` (or ``/ap Alp``) and a small list opens above the chat line with the systems the autopilot can fly
to, best match first:

  * your favourite systems (the Galaxy Map's Favorites list, in the order you keep them) come before every other
    system, each marked with a star;
  * within each group a name that STARTS with what you typed beats one with a word that starts with it, which beats
    one that merely contains it;
  * only systems ``/ap`` would really route to are listed (explored, connected to where you are, not the system you
    are in), each with its jump count; one beyond your Stellar Navigation limit is shown dimmed.

Keys (only while the list is open; with no list the chat line behaves exactly as before):

  * Tab          complete the line with the highlighted system (the first one if none is highlighted);
  * Down / Up    move the highlight (Up with nothing highlighted still recalls the previous message, as always);
  * Enter        with a highlighted system: complete it and send. With none: send what is typed, as always;
  * a click on a row completes it.

Hooked in from outside the vanilla chat code: one call after the event pump (it takes the keys and clicks the list
owns; everything else goes on to the game untouched) and one that queues the list's draw with the other retained
windows - both marked ``# MOD`` in Client.py. Every function the game calls is total: an error switches the feature
off for the session and the chat carries on as before. ``STAR_EMPIRE_NO_CHAT_SUGGEST=1`` turns it off.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Optional, Sequence

import pygame

import text_scale
from gui_window_manager import GuiLayer

logger = logging.getLogger(__name__)

ID = "chat_ap_suggest"
ENV_OFF = "STAR_EMPIRE_NO_CHAT_SUGGEST"
MAX_ROWS = 8
REFRESH_S = 1.0                 # the explored systems and favourites are looked at again this often while it is open
STAR = "*"                      # the favourite marker (a plain character: the UI font has no star glyph guarantee)

_COMMAND = re.compile(r"^/ap(?:\s(.*))?$", re.IGNORECASE | re.DOTALL)

_failed = False


def _enabled() -> bool:
    return not _failed and not os.environ.get(ENV_OFF)


def _switch_off(where: str) -> None:
    global _failed
    if not _failed:
        logger.exception("chat_suggest: error in %s - the /ap suggestions are switched off for this session", where)
    _failed = True


def reset_for_tests() -> None:
    global _failed
    _failed = False


def has_failed() -> bool:
    return _failed


# ── the pure part: what to suggest ────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Suggestion:
    name: str
    favourite: bool
    hops: int
    too_far: bool = False       # beyond the player's jump limit (shown dimmed; /ap would refuse it)
    start: int = 0              # where the typed text sits in the name (to highlight it); -1 = it does not
    length: int = 0


def parse_command(text: str) -> "Optional[str]":
    """The part of the chat line after ``/ap`` (whitespace collapsed, leading space gone), or None when the line is
    not an ``/ap`` command being given a name. ``/ap`` alone, ``/apple`` and ordinary chat give None."""
    match = _COMMAND.match(text or "")
    if match is None or match.group(1) is None:
        return None
    return " ".join(match.group(1).split())


def _match_tier(folded: str, query: str) -> "Optional[int]":
    """0: starts with the query, 1: a word in it does, 2: it contains the query, None: no match. ``query`` is
    already case-folded and non-empty."""
    if folded.startswith(query):
        return 0
    if any(word.startswith(query) for word in re.split(r"[\s\-_']+", folded) if word):
        return 1
    if query in folded:
        return 2
    return None


def rank(partial: str, systems: "dict[str, int]", favourites: Sequence[str], *, max_hops: "Optional[int]" = None,
         limit: "Optional[int]" = None) -> "list[Suggestion]":
    """The systems to suggest for what has been typed after ``/ap``.

    ``systems`` maps each candidate's name to its jump count from where the ship is. ``favourites`` is the Galaxy
    Map's list in the player's order (names compared case-insensitively). Favourites come first, then the other
    systems; each group is ordered by how the name matches, then (favourites) by the player's own order, then by
    name. An empty ``partial`` suggests everything, favourites first."""
    query = " ".join((partial or "").split()).casefold()
    order = {}
    for index, name in enumerate(favourites or ()):
        order.setdefault(str(name).strip().casefold(), index)
    ranked = []
    for name, hops in systems.items():
        folded = name.casefold()
        if query:
            tier = _match_tier(folded, query)
            if tier is None:
                continue
            start = folded.find(query)
        else:
            tier, start = 0, -1
        favourite = folded in order
        key = (0 if favourite else 1, tier, order.get(folded, 0), folded)
        ranked.append((key, Suggestion(name, favourite, int(hops), bool(max_hops is not None and hops > max_hops),
                                       start, len(query) if start >= 0 else 0)))
    ranked.sort(key=lambda pair: pair[0])
    found = [suggestion for _key, suggestion in ranked]
    return found if limit is None else found[:limit]


def only_match_is_typed(partial: str, found: Sequence[Suggestion]) -> bool:
    """True when the one suggestion is exactly what has been typed (nothing left to complete)."""
    return (len(found) == 1 and " ".join((partial or "").split()).casefold() == found[0].name.casefold())


def jump_counts(systems: dict, origin: str) -> "dict[str, int]":
    """Jump count from ``origin`` to every system a route can reach, by the same walk the autopilot makes
    (Client.py ``_engage_route_autopilot_to``: breadth-first over the explored systems' warp gates). ``origin``
    itself is left out."""
    adjacent: dict = {}
    for name, info in systems.items():
        adjacent.setdefault(name, [])
        for gate in (info or {}).get("warp_gates", []) or []:
            target = str(gate.get("target_system", "")).strip()
            if target:
                adjacent[name].append(target)
    hops = {origin: 0}
    queue = [origin]
    while queue:
        node = queue.pop(0)
        for neighbour in adjacent.get(node, []):
            if neighbour not in hops:
                hops[neighbour] = hops[node] + 1
                queue.append(neighbour)
    return {name: count for name, count in hops.items() if name != origin and name in systems}


def complete_line(name: str) -> str:
    return f"/ap {name}"


# ── what the window knows ─────────────────────────────────────────────────────────────────────────────────────

class _State:
    def __init__(self):
        self.text: "Optional[str]" = None      # the line the list was built for
        self.items: "list[Suggestion]" = []
        self.partial: str = ""
        self.selected: "Optional[int]" = None
        self.built_at: float = 0.0
        self.rect: "Optional[pygame.Rect]" = None
        self.rows: "list[tuple[pygame.Rect, Suggestion]]" = []
        self.empty_hint: bool = False           # nothing matches: say so


def _state(win) -> _State:
    state = win.__dict__.get("_cs_state")
    if state is None:
        state = win.__dict__["_cs_state"] = _State()
    return state


def _candidates(win) -> "tuple[dict[str, int], Optional[int]]":
    """The systems ``/ap`` can route to now with their jump counts, and the player's jump limit (None if unknown)."""
    layout = getattr(win, "_layout", None)
    lock = getattr(win, "_lock", None)
    if layout is None and lock is not None:
        with lock:
            layout = getattr(win, "_layout", None)
    snapshot = win._route_systems_snapshot(layout) if hasattr(win, "_route_systems_snapshot") \
        else dict(getattr(win, "_visited_systems", {}) or {})
    origin = win._resolve_route_origin_system(layout, snapshot) if hasattr(win, "_resolve_route_origin_system") \
        else str((layout or {}).get("system_name", ""))
    if not origin:
        return {}, None
    try:
        limit = int(win._mission_max_jumps())
    except Exception:
        limit = None
    return jump_counts(snapshot, origin), limit


def refresh(win, state: _State, *, force: bool = False) -> _State:
    """Bring ``state`` up to date with the chat line: the list for what is typed now, or none."""
    text = getattr(win, "_chat_input", "") if getattr(win, "_chat_focused", False) else ""
    partial = parse_command(text)
    if partial is None or getattr(win, "_chat_popup", None) is not None:
        state.text, state.items, state.partial, state.selected, state.empty_hint = None, [], "", None, False
        return state
    now = time.monotonic()
    if not force and text == state.text and now - state.built_at < REFRESH_S:
        return state
    systems, limit = _candidates(win)
    found = rank(partial, systems, list(getattr(win, "_gm_favorites", ()) or ()), max_hops=limit, limit=None)
    if text != state.text:
        state.selected = None
    state.text, state.partial, state.built_at = text, partial, now
    state.empty_hint = bool(partial) and not found
    state.items = [] if only_match_is_typed(partial, found) else found
    if state.selected is not None and state.selected >= min(len(state.items), MAX_ROWS):
        state.selected = None
    return state


def active(win) -> bool:
    """The list is open: there is something to pick from or to say."""
    if not _enabled():
        return False
    state = _state(win)
    return bool(state.items or state.empty_hint) and state.text is not None


def visible_items(state: _State) -> "list[Suggestion]":
    return state.items[:MAX_ROWS]


def complete(win, name: str) -> None:
    """Put ``/ap <name>`` in the chat line with the caret at the end."""
    line = complete_line(name)
    win._chat_input = line
    win._chat_cursor = len(line)


# ── the keys and clicks ───────────────────────────────────────────────────────────────────────────────────────

def _handle_key(win, state: _State, event) -> bool:
    """A key while the list is open. True when the list took it (Enter completes and goes on to send)."""
    key = event.key
    items = visible_items(state)
    mods = getattr(event, "mod", 0)
    if key == pygame.K_TAB:
        if mods & (pygame.KMOD_CTRL | pygame.KMOD_ALT) or not items:
            return False
        if mods & pygame.KMOD_SHIFT:
            index = (len(items) - 1) if state.selected is None else (state.selected - 1) % len(items)
        else:
            index = 0 if state.selected is None else state.selected
        complete(win, items[index].name)
        state.selected = None
        return True
    if key == pygame.K_DOWN and items:
        state.selected = 0 if state.selected is None else min(state.selected + 1, len(items) - 1)
        return True
    if key == pygame.K_UP and state.selected is not None:
        state.selected = None if state.selected == 0 else state.selected - 1
        return True
    if key in (pygame.K_RETURN, pygame.K_KP_ENTER) and state.selected is not None and items:
        complete(win, items[min(state.selected, len(items) - 1)].name)
        state.selected = None
        return False                      # the Enter itself still goes to the chat, which now sends the full line
    return False


def _handle_pointer(win, state: _State, event) -> bool:
    rect = state.rect
    if rect is None:
        return False
    if event.type == pygame.MOUSEWHEEL:
        return rect.collidepoint(pygame.mouse.get_pos())
    pos = getattr(event, "pos", None)
    if pos is None or not rect.collidepoint(pos):
        return False
    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
        for row, suggestion in state.rows:
            if row.collidepoint(pos):
                complete(win, suggestion.name)
                state.selected = None
                break
    return True                           # the list is a window of its own: nothing under it gets the click


_NOT_EDITING = frozenset((
    pygame.K_LSHIFT, pygame.K_RSHIFT, pygame.K_LCTRL, pygame.K_RCTRL, pygame.K_LALT, pygame.K_RALT,
    pygame.K_LMETA, pygame.K_RMETA, pygame.K_CAPSLOCK, pygame.K_NUMLOCK, pygame.K_SCROLLOCK,
    pygame.K_TAB, pygame.K_DOWN, pygame.K_UP, pygame.K_RETURN, pygame.K_KP_ENTER))


def _edits_line(event) -> bool:
    """A key that may change the chat line. Every key does except modifiers and the ones this list handles itself
    (Tab, Up, Down, Enter): reading the typed character is the shared text box's business, not this module's, so a
    key like Left or Escape counts too - it only makes the rest of that frame's keys go to the chat unhandled."""
    return event.key not in _NOT_EDITING


def filter_events(win, events):
    """Called once per frame (Client.py) with the frame's events: takes out the keys and clicks the suggestion list
    owns, so the game's chat handlers never see them. Total."""
    if not _enabled():
        return events
    try:
        state = refresh(win, _state(win))
        if not (state.items or state.empty_hint):
            state.rect, state.rows = None, []
            return events
        kept = []
        typed = False                     # an earlier event of this frame edits the line: the list is out of date
        for event in events:
            took = False
            if event.type == pygame.KEYDOWN:
                if not typed:
                    took = _handle_key(win, state, event)
                if not took and _edits_line(event):
                    typed = True
            elif event.type == pygame.TEXTINPUT:
                typed = True
            elif event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEWHEEL):
                took = not typed and _handle_pointer(win, state, event)
            if not took:
                kept.append(event)
        return kept if len(kept) != len(events) else events
    except Exception:
        _switch_off("filter_events()")
        return events


# ── the list ──────────────────────────────────────────────────────────────────────────────────────────────────

def _palette(render_module=None) -> dict:
    def pick(name, default):
        return tuple(getattr(render_module, name, default))[:3]

    return {"plate": pick("_GM_PLATE_BG", (7, 11, 20)), "steel": pick("_GM_STEEL", (56, 88, 138)),
            "accent": pick("_GM_ACCENT", (95, 170, 255)), "text": pick("_GM_TEXT", (185, 215, 250)),
            "value": pick("_GM_VALUE", (228, 237, 250)), "caption": pick("_GM_CAPTION", (96, 128, 176)),
            "amber": (255, 190, 70), "gold": (255, 214, 90),
            "hover": pick("_STATS_CONTROL_HOV", (18, 42, 72)), "selected": pick("_STATS_CONTROL_SEL", (18, 58, 94))}


_colours: "Optional[dict]" = None


def _get_colours() -> dict:
    """The colours of the game's own HUD plates (render_mixin's _GM_* / _STATS_CONTROL_*), looked up on first use so
    this module needs no hook of its own for them."""
    global _colours
    if _colours is None:
        import sys
        _colours = _palette(sys.modules.get("render_mixin"))
    return _colours


def _font(win):
    return win._instrument_font(text_scale.font_size(11, getattr(win, "_chat_font_scale", 1.0)), bold=True)


def _label(win, text, colour, font):
    return win._cached_label(text, colour, font)


def layout(win, screen_size) -> "Optional[tuple[pygame.Rect, list]]":
    """The list's rectangle and its rows ``[(rect, suggestion)]`` for the current items, above the chat line (below
    it when the screen ends first); None when it is closed."""
    state = _state(win)
    anchor = getattr(win, "_chat_input_text_rect", None)
    if anchor is None or not (state.items or state.empty_hint):
        return None
    font = _font(win)
    row_h = max(win._s(18), font.get_linesize() + win._s(4))
    pad = win._s(8)
    items = visible_items(state)
    if items:
        widest = 0
        for suggestion in items:
            name_w = font.size((STAR + " " if suggestion.favourite else "") + suggestion.name)[0]
            hops_w = font.size(_hops_text(suggestion))[0]
            widest = max(widest, name_w + win._s(18) + hops_w)
        width = max(win._s(200), widest + pad * 2)
    else:
        width = max(win._s(200), font.size(_empty_text(state))[0] + pad * 2)
    more = len(state.items) - len(items)
    rows_n = max(1, len(items)) + (1 if more > 0 else 0)
    height = row_h * (rows_n + 1) + win._s(4)           # + the header row
    screen_w, screen_h = screen_size
    gap = win._s(3)
    send = getattr(win, "_chat_send_rect", None)
    x = (send.left if send is not None else anchor.left)
    y = anchor.top - gap - height
    if y < 2:
        y = anchor.bottom + gap
    x = max(2, min(screen_w - width - 2, x))
    y = max(2, min(screen_h - height - 2, y))
    rect = pygame.Rect(x, y, width, height)
    rows = [(pygame.Rect(rect.x + 2, rect.y + row_h * (index + 1), rect.width - 4, row_h), suggestion)
            for index, suggestion in enumerate(items)]
    return rect, rows


def _hops_text(suggestion: Suggestion) -> str:
    return f"{suggestion.hops} jump{'s' if suggestion.hops != 1 else ''}" + (" - too far" if suggestion.too_far else "")


def _empty_text(state: _State) -> str:
    return f"no explored system matches '{state.partial}'"


def draw(win, screen) -> None:
    """Draw the list (queued as a retained popover window)."""
    state = _state(win)
    laid = layout(win, screen.get_size())
    if laid is None:
        return
    rect, rows = laid
    state.rect, state.rows = rect, rows
    colours = _get_colours()
    font = _font(win)
    row_h = max(win._s(18), font.get_linesize() + win._s(4))
    pad = win._s(8)
    screen.blit(win._cached_fill(rect.width, rect.height, (*colours["plate"], 250)), rect.topleft)
    pygame.draw.rect(screen, colours["steel"], rect, 1)
    pygame.draw.line(screen, colours["accent"], (rect.x + 1, rect.y + 1), (rect.right - 2, rect.y + 1))
    title = _label(win, "AUTOPILOT TO", colours["caption"], font)
    screen.blit(title, (rect.x + pad, rect.y + (row_h - title.get_height()) // 2 + 1))
    hint = _label(win, "TAB completes", colours["caption"], font)
    if rect.width >= title.get_width() + hint.get_width() + pad * 3:
        screen.blit(hint, (rect.right - pad - hint.get_width(), rect.y + (row_h - hint.get_height()) // 2 + 1))
    if not rows:
        text = _label(win, _empty_text(state), colours["amber"], font)
        screen.blit(text, (rect.x + pad, rect.y + row_h + (row_h - text.get_height()) // 2))
        return
    mouse = pygame.mouse.get_pos()
    for index, (row, suggestion) in enumerate(rows):
        if index == state.selected:
            screen.blit(win._cached_fill(row.width, row.height, (*colours["selected"], 255)), row.topleft)
            pygame.draw.rect(screen, colours["accent"], row, 1)
        elif row.collidepoint(mouse):
            screen.blit(win._cached_fill(row.width, row.height, (*colours["hover"], 255)), row.topleft)
        base = colours["caption"] if suggestion.too_far else colours["text"]
        x = row.x + pad - 2
        if suggestion.favourite:
            star = _label(win, STAR, colours["gold"], font)
            screen.blit(star, (x, row.centery - star.get_height() // 2))
            x += star.get_width() + win._s(4)
        else:
            x += font.size(STAR + " ")[0]
        name = suggestion.name
        if suggestion.start >= 0 and suggestion.length:
            parts = ((name[:suggestion.start], base), (name[suggestion.start:suggestion.start + suggestion.length],
                                                       colours["value"] if not suggestion.too_far else colours["text"]),
                     (name[suggestion.start + suggestion.length:], base))
        else:
            parts = ((name, base),)
        for piece, colour in parts:
            if not piece:
                continue
            surface = _label(win, piece, colour, font)
            screen.blit(surface, (x, row.centery - surface.get_height() // 2))
            x += surface.get_width()
        hops = _label(win, _hops_text(suggestion), colours["amber"] if suggestion.too_far else colours["caption"], font)
        screen.blit(hops, (row.right - pad + 2 - hops.get_width(), row.centery - hops.get_height() // 2))
    more = len(state.items) - len(rows)
    if more > 0:
        text = _label(win, f"+ {more} more: keep typing", colours["caption"], font)
        screen.blit(text, (rect.x + pad, rect.bottom - row_h + (row_h - text.get_height()) // 2 - win._s(2)))


def queue(win, frame) -> None:
    """Called once per frame (Client.py) before the retained windows are flushed: queue the list's draw while it
    is open. Total."""
    if not _enabled():
        return
    try:
        state = refresh(win, _state(win))
        screen = frame.screen
        laid = layout(win, screen.get_size()) if (state.items or state.empty_hint) else None
        if laid is None:
            state.rect, state.rows = None, []
            return
        state.rect, state.rows = laid
        win._gui_windows.queue_draw(
            ID, lambda: draw(win, screen), lambda: _state(win).rect,
            layer=GuiLayer.POPOVER, focusable=False, visible=lambda: _enabled() and _state(win).rect is not None)
    except Exception:
        _switch_off("queue()")

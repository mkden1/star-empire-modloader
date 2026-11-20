"""station_gear_upgrade.py - an UPGRADE GEAR button in the station prefab dialog: swap the gear fitted on the station
for higher marks you carry, and take the old gear back to your hold.

A station's single-slot modules (shield, energy core, each weapon family, ...) come in marks: the module type ends in
``_mk1`` / ``_mk2`` / ``_mk3``. When one is fitted and your hold has a higher mark of the same family, the button does
by command what you would do by hand in the Storage tab, one module at a time:

  1. PS_TRANSFER_TO_STATION   the higher mark, from your hold into the station's storage
  2. PS_EQUIP_MODULE          fit it (the server swaps the old mark out of its slot into storage; if it did not,
                              PS_UNEQUIP_MODULE takes the old one off)
  3. PS_TRANSFER_TO_SHIP      one copy of the old mark, from the station's storage back to your hold

Each step waits for the station's own cargo push to show that it happened (or for PS_EQUIP_MODULE_RESULT to refuse
the equip) before the next one is sent, and gives up after STEP_WAIT_S. The first failure stops the run: if the
equip was refused the new gear is sent back to your hold, otherwise the chat says where the gear is. Nothing is
sent until you press the button, and the button lists the swaps it would make.

Only equipped modules whose station row the server flags ``equip_singleton`` with exactly one fitted are swapped
(stackable modules such as extractors and hull expanders have no marks). Bound gear cannot go into a station and is
named instead of sent. The new gear is taken from the unbound copies in the hold, highest mark first.

``install(SolarSystemWindow, render_module)`` (Client.py, bottom) wraps ``set_ps_station_cargo`` and
``_draw_ps_prefab_dialog``; ``install_protocol(ProtocolMixin)`` wraps ``_on_ps_equip_module_result`` to read a
refusal's reason; ``filter_events`` is one marked line in the event loop (the button's click, and the timeouts). The
game's own methods always run. Any error switches the feature off for the session and ends a running upgrade with a
chat line. Kill switch: STAR_EMPIRE_NO_GEAR_UPGRADE=1.

The same hooks also carry the ADD 50 GAMMA + 50 WORKERS button (station_expansion.py) in the right-hand part of the
dialog's band: its drawing, its click, the cargo pushes and the equip answers are passed to that module, which has its
own switch and is switched off on its own errors. Only one of the two runs at a time.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time

import pygame

from inventory_sections import inventory_stack_quantity

logger = logging.getLogger(__name__)

STEP_WAIT_S = 10.0
SWAP_GRACE_S = 1.5       # both marks fitted after the equip: give the server this long to take the old one out itself
LABEL = "UPGRADE GEAR"
TITLE = "UPGRADE STATION GEAR"
MAX_LINES = 4            # lines of detail under the button; the dialog has room for this many

DEPOSIT_COMMAND = "PS_TRANSFER_TO_STATION"
EQUIP_COMMAND = "PS_EQUIP_MODULE"
UNEQUIP_COMMAND = "PS_UNEQUIP_MODULE"
RETURN_COMMAND = "PS_TRANSFER_TO_SHIP"

_TIER = re.compile(r"^(?P<family>.+)_mk(?P<mark>[0-9]+)$")
_NAME_MARK = re.compile(r"^(?P<stem>.*?)\s*(?P<mark>Mk\.?\s*[IVXLCDM0-9]+)\s*$", re.IGNORECASE)

TIMING_NAME = {"deposit": "moving in", "equip": "fitting", "unequip": "taking off", "return": "moving back"}

STEP_TEXT = {
    "deposit": "Moving the new gear into the station...",
    "equip": "Fitting it...",
    "unequip": "Taking the old gear off...",
    "return": "Moving the old gear back to your hold...",
}

_failed = False
_colours: dict = {}


def _switch_off(where: str) -> None:
    global _failed
    if not _failed:
        logger.exception("station_gear_upgrade: %s failed; the gear upgrade is switched off for this session", where)
    _failed = True


def enabled() -> bool:
    return not _failed and os.environ.get("STAR_EMPIRE_NO_GEAR_UPGRADE") != "1"


def reset_for_tests() -> None:
    global _failed
    _failed = False
    _expansion().reset_for_tests()


def _expansion():
    """The expansion button's module (station_expansion.py). It imports this one for the shared helpers, so this one
    imports it on first use."""
    import station_expansion
    return station_expansion


# ── what can be swapped ──────────────────────────────────────────────────────

def tier_of(item_type):
    """``(family, mark)`` for a marked module type ("station_beam_cannon_mk3" -> ("station_beam_cannon", 3)), else
    None. A CSW-only type keeps its prefix in the family, so it is never swapped for an ordinary one."""
    match = _TIER.match(str(item_type or ""))
    return (match["family"], int(match["mark"])) if match else None


def _int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _same_station(a, b) -> bool:
    try:
        return int(a) == int(b)
    except (TypeError, ValueError):
        return False


def _pretty(key) -> str:
    return str(key).split(":")[-1].replace("_", " ").strip().title() or str(key)


def type_key(cargo_key, item_type) -> str:
    """What PS_TRANSFER_TO_STATION expects for a hold item: the cargo key without its trailing unique id (the same
    derivation the game's own transfer uses)."""
    parts = str(cargo_key or "").split(":")
    key = ":".join(parts[:3]) if len(parts) >= 4 else str(cargo_key or "")
    if not key.startswith("item:") or not key.endswith(":" + str(item_type)):
        return f"item:station:{item_type}"
    return key


def swap_label(swap) -> str:
    """"Station Beam Cannon: Mk II -> Mk III" (or the two full names when they do not share a stem)."""
    old, new = _NAME_MARK.match(swap["old_name"]), _NAME_MARK.match(swap["new_name"])
    if old and new and old["stem"].strip() == new["stem"].strip() and old["stem"].strip():
        return f"{old['stem'].strip()}: {old['mark']} -> {new['mark']}"
    return f"{swap['old_name']} -> {swap['new_name']}"


def build_plan(station_rows, equipped_counts, inventory):
    """The swaps to make and notes on what was left alone: ``(swaps, notes)``.

    Each swap names an equipped, single-slot, marked module of the station and the highest unbound higher mark of
    its family in the hold: ``family, old_type, old_name, old_mark, new_type, new_name, new_mark, new_key``."""
    rows_by_type: dict = {}
    for row in station_rows or ():
        if isinstance(row, dict) and str(row.get("item_key", "")).startswith("item:"):
            rows_by_type.setdefault(str(row.get("item_type") or ""), []).append(row)

    hold: dict = {}
    for row in inventory or ():
        if not isinstance(row, dict) or row.get("item_class") != "cargo_item" or row.get("item_category") != "station":
            continue
        item_type = str(row.get("item_type") or "")
        parsed = tier_of(item_type)
        if parsed is None:
            continue
        entry = hold.setdefault(parsed[0], {}).setdefault(item_type, {
            "mark": parsed[1], "name": str(row.get("display_name") or _pretty(item_type)), "free": 0, "key": None})
        if row.get("bound"):
            continue
        entry["free"] += inventory_stack_quantity(row)
        if entry["key"] is None:
            entry["key"] = type_key(row.get("cargo_key"), item_type)

    swaps, notes = [], []
    for old_type, count in sorted((equipped_counts or {}).items()):
        parsed = tier_of(old_type)
        if parsed is None or _int(count) <= 0:
            continue
        family, old_mark = parsed
        higher = {t: e for t, e in hold.get(family, {}).items() if e["mark"] > old_mark}
        if not higher:
            continue
        old_rows = rows_by_type.get(old_type, [])
        old_name = str(next((r.get("display_name") for r in old_rows if r.get("display_name")), "") or _pretty(old_type))
        if _int(count) != 1 or not any(r.get("equip_singleton") is True for r in old_rows):
            notes.append(f"{old_name} is not single-slot, so it is left alone.")
            continue
        usable = {t: e for t, e in higher.items() if e["free"] > 0}
        if not usable:
            best = higher[max(higher, key=lambda t: higher[t]["mark"])]
            notes.append(f"{best['name']} is bound, so it stays in your hold.")
            continue
        new_type = max(usable, key=lambda t: usable[t]["mark"])
        new = usable[new_type]
        swaps.append({"family": family, "old_type": old_type, "old_name": old_name, "old_mark": old_mark,
                      "new_type": new_type, "new_name": new["name"], "new_mark": new["mark"], "new_key": new["key"]})
    return swaps, notes


def _station_id(win):
    dialog = getattr(win, "_ps_prefab_dialog", None)
    return None if dialog is None else dialog.get("station_id")


def _docked_at(win, station_id) -> bool:
    with win._lock:
        overlay = win._station_overlay
        data = dict(overlay.get("data") or {}) if overlay and overlay.get("kind") == "player_station" else None
    return data is not None and _same_station(data.get("attached_station_id"), station_id)


def dock_problem(win, jump_gate_message="A jump gate has no station modules to upgrade."):
    """``(problem, inventory)`` for the dialog's station: ``problem`` is why a station action cannot start (a sentence),
    else None; ``inventory`` is a copy of the ship's hold rows. Shared with the expansion button."""
    station_id = _station_id(win)
    if station_id is None:
        return "Open the station's prefab dialog first.", []
    with win._lock:
        overlay = win._station_overlay
        data = dict(overlay.get("data") or {}) if overlay and overlay.get("kind") == "player_station" else None
        inventory = list(getattr(win, "_inventory", None) or ())
    if data is None or not _same_station(data.get("attached_station_id"), station_id):
        return "You are not docked at this station.", []
    if data.get("jump_gate_key"):
        return jump_gate_message, []
    if data.get("management_access") is False:
        return "You cannot manage this station.", []
    return None, inventory


def evaluate(win):
    """``(problem, swaps, notes)`` for the dialog's station: ``problem`` is why nothing can be started (a sentence),
    else None."""
    problem, inventory = dock_problem(win)
    if problem is not None:
        return problem, [], []
    swaps, notes = build_plan(getattr(win, "_ps_station_cargo", None), getattr(win, "_ps_equipped_module_counts", None),
                              inventory)
    return None, swaps, notes


# ── the run ──────────────────────────────────────────────────────────────────

def _say(win, text: str, warn: bool = False) -> None:
    win.chat_log(text, (255, 170, 80) if warn else (120, 220, 160))


def _rows(win, item_type):
    return [r for r in (getattr(win, "_ps_station_cargo", None) or ())
            if isinstance(r, dict) and str(r.get("item_key", "")).startswith("item:")
            and str(r.get("item_type") or "") == item_type]


def _held(win, item_type) -> int:
    """How many of this module the station's cargo holds (fitted ones included)."""
    return sum(max(0, _int(r.get("quantity", 1), 1)) for r in _rows(win, item_type))


def _fitted(win, item_type) -> int:
    return _int((getattr(win, "_ps_equipped_module_counts", None) or {}).get(item_type, 0))


def _row_id(win, item_type):
    return next((r.get("row_id") for r in _rows(win, item_type) if r.get("row_id") is not None), None)


def running(win) -> bool:
    return bool(win.__dict__.get("_sgu"))


def start(win) -> bool:
    """The button was pressed: check, then begin with the first swap. True if a run started."""
    if running(win) or _expansion().running(win):
        return False
    problem, swaps, notes = evaluate(win)
    if problem is None and not swaps:
        problem = "Nothing to upgrade: no equipped station gear has a higher mark in your hold."
    if problem is not None:
        _say(win, "Station gear: " + problem, warn=True)
        return False
    now = time.monotonic()
    job = {"station_id": _station_id(win), "queue": list(swaps), "total": len(swaps), "done": [], "cur": swaps[0],
           "step": "", "t": now, "base": 0, "began": now, "spent": {}}
    win._sgu = job
    _say(win, "Station gear: upgrading " + "; ".join(swap_label(s) for s in swaps) + ".")
    _send_deposit(win, job)
    return True


def _lap(job, now) -> None:
    """Add the time since the current step began to that step's total (``job["spent"]``)."""
    if job["step"]:
        job["spent"][job["step"]] = job["spent"].get(job["step"], 0.0) + now - job["t"]


def _enter(job, step, **fields) -> None:
    """Begin timing ``step``; the step before it is closed first."""
    now = time.monotonic()
    _lap(job, now)
    job.update(step=step, t=now, **fields)


def timing_text(job, now) -> str:
    """"in 9.4 s (moving in 2.1 s, fitting 4.0 s, moving back 3.3 s)": where the run's time went. Nearly all of it is
    the server's answer to each command, which is what the run waits for."""
    parts = [f"{TIMING_NAME[step]} {job['spent'][step]:.1f} s" for step in TIMING_NAME if job["spent"].get(step)]
    return f"in {now - job['began']:.1f} s" + (f" ({', '.join(parts)})" if parts else "")


def _send_deposit(win, job) -> None:
    swap = job["cur"]
    _enter(job, "deposit", base=_held(win, swap["new_type"]))
    win._send_json(DEPOSIT_COMMAND, {"station_id": job["station_id"], "item_key": swap["new_key"], "amount": 1})


def _send_equip(win, job) -> None:
    swap = job["cur"]
    row_id = _row_id(win, swap["new_type"])
    if row_id is None:
        _fail(win, job, "the station's cargo has no row for the new gear")
        return
    _enter(job, "equip")
    win._send_equip_json(EQUIP_COMMAND, {"station_id": job["station_id"], "row_id": row_id,
                                         "module_type": swap["new_type"], "count": 1})


def _send_unequip(win, job) -> None:
    swap = job["cur"]
    _enter(job, "unequip")
    win._send_equip_json(UNEQUIP_COMMAND, {"station_id": job["station_id"], "module_type": swap["old_type"],
                                           "count": 1})


def _send_return(win, job) -> None:
    swap = job["cur"]
    row_id = _row_id(win, swap["old_type"])
    if row_id is None:
        _fail(win, job, "the station's cargo has no row for the old gear")
        return
    _enter(job, "return", base=_held(win, swap["old_type"]))
    win._send_json(RETURN_COMMAND, {"station_id": job["station_id"], "row_id": row_id, "amount": 1})


def _finish_swap(win, job) -> None:
    swap = job["queue"].pop(0)
    job["done"].append(swap_label(swap))
    _say(win, f"Station gear: {swap_label(swap)} done - the {swap['old_name']} is back in your hold.")
    if job["queue"]:
        job["cur"] = job["queue"][0]
        _send_deposit(win, job)
        return
    now = time.monotonic()
    _lap(job, now)
    win._sgu = None
    n = len(job["done"])
    text = f"Station gear: upgraded {n} module{'' if n == 1 else 's'} {timing_text(job, now)}."
    logger.info(text)
    _say(win, text)


ADVICE = {
    "deposit": "Nothing was moved.",
    "unequip": "Both marks are fitted; take the old one off in the Storage tab.",
    "return": "The old gear is in the station's storage; move it to your hold in the Storage tab.",
}


def _fail(win, job, why: str, docked: bool = True) -> None:
    """Stop the run: say where the gear is, and send the new gear back if the station refused to fit it."""
    swap, step = job["cur"], job["step"]
    advice = ADVICE.get(step, "")
    if step == "equip":
        row_id = _row_id(win, swap["new_type"])
        if docked and row_id is not None and _held(win, swap["new_type"]) > _fitted(win, swap["new_type"]):
            win._send_json(RETURN_COMMAND, {"station_id": job["station_id"], "row_id": row_id, "amount": 1})
            advice = f"The {swap['new_name']} is being sent back to your hold."
        else:
            advice = f"The {swap['new_name']} is in the station's storage."
    win._sgu = None
    parts = [f"Station gear: stopped at {swap_label(swap)}: {why.rstrip('. ')}.", advice]
    if len(job["queue"]) > 1:
        parts.append(f"{len(job['queue']) - 1} more not started.")
    if job["done"]:
        parts.append(f"Done: {'; '.join(job['done'])}.")
    _say(win, " ".join(part for part in parts if part), warn=True)


TIMEOUT_TEXT = {
    "deposit": "the station did not take it (is its storage full?)",
    "equip": "the station did not fit it (the Log tab has the server's reason)",
    "unequip": "the old gear would not come off",
    "return": "the old gear did not come back to your hold",
}


def advance(win, now: "float | None" = None) -> None:
    """Move the run on if the station's state shows the current step happened; give up on a step that took too long."""
    job = win.__dict__.get("_sgu")
    if not job:
        return
    swap, step = job["cur"], job["step"]
    now = time.monotonic() if now is None else now
    if not _docked_at(win, job["station_id"]):
        _fail(win, job, "you are no longer docked at the station", docked=False)
        return
    if step == "deposit":
        if _held(win, swap["new_type"]) >= job["base"] + 1:
            _send_equip(win, job)
            return
    elif step == "equip":
        if _fitted(win, swap["new_type"]) >= 1:
            if _fitted(win, swap["old_type"]) == 0:
                _send_return(win, job)
                return
            job.setdefault("both_since", now)
            if now - job["both_since"] >= SWAP_GRACE_S:
                _send_unequip(win, job)           # the server did not swap it out by itself
                return
    elif step == "unequip":
        if _fitted(win, swap["old_type"]) == 0:
            _send_return(win, job)
            return
    elif step == "return":
        if _held(win, swap["old_type"]) <= job["base"] - 1:
            _finish_swap(win, job)
            return
    if now - job["t"] >= STEP_WAIT_S:
        _fail(win, job, TIMEOUT_TEXT.get(step, "it took too long"))


def on_equip_result(win, data) -> None:
    """PS_EQUIP_MODULE_RESULT while a fit is awaited: a refusal ends the run with the server's reason."""
    job = win.__dict__.get("_sgu")
    if not job or job["step"] != "equip" or not isinstance(data, dict) or data.get("ok"):
        return
    module = data.get("module_type")
    if module and module != job["cur"]["new_type"]:
        return
    _fail(win, job, str(data.get("reason") or "the server refused to fit it"))


def stop_after_error(win) -> None:
    """The feature just switched itself off: do not leave a run half-way without saying so."""
    job = win.__dict__.get("_sgu") if win is not None else None
    if job:
        win._sgu = None
        _say(win, "Station gear: the upgrade stopped after an error. Check the station's Storage tab for where the "
                  "gear is.", warn=True)


# ── the dialog ───────────────────────────────────────────────────────────────

def panel_state(win) -> dict:
    """What the dialog section shows: ``label``, ``enabled`` and ``lines`` (kind, text); kind is row, dim or warn."""
    job = win.__dict__.get("_sgu")
    if job:
        return {"label": f"UPGRADING...  {len(job['done']) + 1} / {job['total']}", "enabled": False,
                "lines": [("row", swap_label(job["cur"])), ("dim", STEP_TEXT.get(job["step"], ""))]}
    if _expansion().running(win):
        return {"label": "PLEASE WAIT", "enabled": False, "lines": [("dim", "The station expansion is running.")]}
    problem, swaps, notes = evaluate(win)
    note_lines = [("warn", n) for n in notes[:2]]          # why something is left alone outranks a long swap list
    room = MAX_LINES - len(note_lines)
    lines = []
    if problem is not None:
        lines.append(("dim", problem))
    elif swaps:
        shown = swaps if len(swaps) <= room else swaps[:room - 1]
        lines += [("row", swap_label(s)) for s in shown]
        if len(shown) < len(swaps):
            lines.append(("dim", f"+ {len(swaps) - len(shown)} more"))
    else:
        lines.append(("dim", "No equipped station gear has a higher mark in your hold."))
    lines += note_lines
    if problem is None and swaps:
        return {"label": f"{LABEL}  ({len(swaps)})", "enabled": True, "lines": lines}
    return {"label": "NOTHING TO UPGRADE", "enabled": False, "lines": lines}


def _palette(render_module) -> dict:
    def pick(name, default):
        return getattr(render_module, name, default) if render_module is not None else default

    return {"value": pick("_PS_VALUE", (230, 240, 255)), "caption": pick("_PS_CAPTION", (130, 150, 180)),
            "text": pick("_PS_TEXT", (190, 205, 230)), "warn": pick("_PS_WARN", (245, 150, 45)),
            "success": pick("_PS_SUCCESS", (60, 200, 120)), "disabled": pick("_PS_DISABLED", (70, 82, 100)),
            "disabled_bg": pick("_PS_DISABLED_BG", (16, 21, 31)), "dim": pick("_PS_STEEL_DIM", (60, 75, 100))}


def section_rect(dialog, s) -> "pygame.Rect | None":
    """The free band of the game's prefab dialog between its help text and the status line above SAVE / LOAD."""
    header, load = dialog.get("header_rect"), dialog.get("load_rect")
    if header is None or load is None:
        return None
    top, bottom = header.bottom + s(92), load.y - s(26)
    if bottom - top < s(70):
        return None
    return pygame.Rect(header.x, top, header.width, bottom - top)


def _dialog_is_plain(dialog) -> bool:
    return dialog is not None and not dialog.get("name_input") and not dialog.get("delete_confirm") \
        and not dialog.get("dropdown_open")


def columns(area, s):
    """The band as ``(left, right)``: the gear section on the left, the expansion button's on the right."""
    gap = s(12)
    left = pygame.Rect(area.x, area.y, (area.width - gap) * 55 // 100, area.height)
    return left, pygame.Rect(left.right + gap, area.y, area.right - left.right - gap, area.height)


def draw_panel(win, ctx, area, title, hint, state, dialog, rect_key) -> None:
    """Draw one section (title, button, detail lines) into ``area``; an enabled button's rect goes in
    ``dialog[rect_key]``. Shared by the gear section and the expansion button."""
    screen, s, font = ctx.screen, win._s, win._instrument_font
    colours = _colours or _palette(None)

    title_face = font(11, bold=True)
    screen.blit(title_face.render(title, True, colours["text"]), (area.x, area.y))
    hint_face = font(10)
    if hint and title_face.size(title)[0] + s(14) + hint_face.size(hint)[0] <= area.width:   # only when there is room
        text = hint_face.render(hint, True, colours["caption"])
        screen.blit(text, (area.right - text.get_width(), area.y + s(1)))

    button = pygame.Rect(area.x, area.y + s(20), area.width, s(30))
    if state["enabled"]:
        hover = button.collidepoint(pygame.mouse.get_pos())
        fill, border, ink = (28, 82, 58) if hover else (20, 62, 45), colours["success"], colours["value"]
    else:
        fill, border, ink = colours["disabled_bg"], colours["dim"], colours["disabled"]
    label = win._gm_ellipsize(font(12, bold=True), state["label"], area.width - s(8))
    win._btn(screen, button, font(12, bold=True).render(label, True, ink), fill=fill, border=border,
             border_w=1, radius=0)
    if state["enabled"]:
        dialog[rect_key] = button

    body, small = font(11), font(10)
    line_h = body.get_linesize() + s(1)
    y = button.bottom + s(6)
    for kind, text in state["lines"]:
        if y + line_h > area.bottom:
            break
        face = small if kind != "row" else body
        colour = {"row": colours["value"], "warn": colours["warn"]}.get(kind, colours["caption"])
        screen.blit(face.render(win._gm_ellipsize(face, text, area.width), True, colour), (area.x, y))
        y += line_h


def draw_section(win, ctx) -> None:
    """Draw the section into the game's prefab dialog (called after the game's own draw)."""
    dialog = win._ps_prefab_dialog
    if dialog is not None:
        dialog["sgu_rect"] = None
    if not _dialog_is_plain(dialog):
        return
    area = section_rect(dialog, win._s)
    if area is None:
        return
    if _expansion().enabled():
        area = columns(area, win._s)[0]               # the expansion button has the right-hand part
    draw_panel(win, ctx, area, TITLE, "swaps in higher marks from your hold", panel_state(win), dialog, "sgu_rect")


def _expansion_area(win):
    """The right-hand column of the band, where the expansion button goes (None until the dialog has been drawn)."""
    dialog = getattr(win, "_ps_prefab_dialog", None)
    if dialog is None or dialog.get("header_rect") is None:
        return None
    band = section_rect(dialog, win._s)
    return None if band is None else columns(band, win._s)[1]


def _take_press(events, rect):
    """``(kept, pressed)``: the events without a left press on ``rect``, and whether there was one."""
    kept, pressed = [], False
    for event in events:
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and rect.collidepoint(event.pos):
            pressed = True
        else:
            kept.append(event)
    return kept, pressed


def _filter_gear_events(win, events):
    if not enabled():
        return events
    try:
        advance(win)
        dialog = getattr(win, "_ps_prefab_dialog", None)
        rect = dialog.get("sgu_rect") if _dialog_is_plain(dialog) else None
        if rect is None:
            return events
        kept, pressed = _take_press(events, rect)
        if pressed:
            start(win)
            return kept
        return events
    except Exception:
        _switch_off("filter_events()")
        stop_after_error(win)
        return events


def _filter_expansion_events(win, events):
    expansion = _expansion()
    if not expansion.enabled():
        return events
    try:
        expansion.advance(win)
        dialog = getattr(win, "_ps_prefab_dialog", None)
        rect = dialog.get("sxp_rect") if _dialog_is_plain(dialog) else None
        if rect is None:
            return events
        kept, pressed = _take_press(events, rect)
        if pressed:
            expansion.start(win)
            return kept
        return events
    except Exception:
        expansion._switch_off("filter_events()")
        expansion.stop_after_error(win)
        return events


def filter_events(win, events):
    """Called once per frame (Client.py): drives the runs' timeouts and takes a press on either button out of the
    list (the game never sees it) to start its run. Total."""
    return _filter_expansion_events(win, _filter_gear_events(win, events))


# ── hooks ────────────────────────────────────────────────────────────────────

def install(window_cls, render_module=None) -> bool:
    """Wrap the window's cargo push and prefab dialog draw. Idempotent; True if hooked."""
    global _colours
    if window_cls.__dict__.get("_station_gear_upgrade_installed"):
        return False
    needed = ("set_ps_station_cargo", "_draw_ps_prefab_dialog", "_send_json", "_send_equip_json", "chat_log")
    missing = [name for name in needed if not hasattr(window_cls, name)]
    if missing:
        logger.warning("station_gear_upgrade: not installed, the window has no %s", ", ".join(missing))
        return False
    _colours = _palette(render_module)

    def wrap(name, make):
        original = getattr(window_cls, name)
        wrapper = make(original)
        wrapper.__wrapped__ = original
        wrapper.__doc__ = original.__doc__
        wrapper.__name__ = name
        setattr(window_cls, name, wrapper)

    def cargo(original):
        def set_ps_station_cargo(self, data):
            result = original(self, data)
            if enabled():
                try:
                    advance(self)
                except Exception:
                    _switch_off("advance()")
                    stop_after_error(self)
            expansion = _expansion()
            if expansion.enabled():
                try:
                    expansion.advance(self)
                except Exception:
                    expansion._switch_off("advance()")
                    expansion.stop_after_error(self)
            return result
        return set_ps_station_cargo

    def draw(original):
        def _draw_ps_prefab_dialog(self, ctx):
            result = original(self, ctx)
            if enabled():
                try:
                    draw_section(self, ctx)
                except Exception:
                    _switch_off("draw_section()")
                    stop_after_error(self)
            expansion = _expansion()
            if expansion.enabled():
                try:
                    expansion.draw_section(self, ctx, _expansion_area(self))
                except Exception:
                    expansion._switch_off("draw_section()")
                    expansion.stop_after_error(self)
            return result
        return _draw_ps_prefab_dialog

    wrap("set_ps_station_cargo", cargo)
    wrap("_draw_ps_prefab_dialog", draw)
    window_cls._station_gear_upgrade_installed = True
    return True


def install_protocol(protocol_cls) -> bool:
    """Wrap ProtocolMixin's ``_on_ps_equip_module_result`` so a refused fit ends a run with the server's reason; the
    game's handler always runs afterwards, unchanged. Idempotent; True if hooked."""
    original = protocol_cls.__dict__.get("_on_ps_equip_module_result")
    if original is None:
        logger.warning("station_gear_upgrade: equip refusals not hooked, the protocol class has no "
                       "_on_ps_equip_module_result")
        return False
    if getattr(original, "_station_gear_upgrade", False):
        return False

    def _parse(payload):
        try:
            return json.loads(payload) if isinstance(payload, (str, bytes, bytearray)) else payload
        except ValueError:
            return None                    # not JSON: the game's own handler ignores it too

    def _on_ps_equip_module_result(self, payload, *args, **kwargs):
        win = getattr(self, "_solar_window", None)
        if enabled():
            try:
                job = win.__dict__.get("_sgu") if win is not None else None
                if job and job["step"] == "equip":
                    on_equip_result(win, _parse(payload))
            except Exception:
                _switch_off("on_equip_result()")
                stop_after_error(win)
        expansion = _expansion()
        if expansion.enabled():
            try:
                if expansion.awaiting_equip(win):
                    expansion.on_equip_result(win, _parse(payload))
            except Exception:
                expansion._switch_off("on_equip_result()")
                expansion.stop_after_error(win)
        return original(self, payload, *args, **kwargs)

    _on_ps_equip_module_result.__name__ = original.__name__
    _on_ps_equip_module_result.__doc__ = original.__doc__
    _on_ps_equip_module_result.__wrapped__ = original
    _on_ps_equip_module_result._station_gear_upgrade = True
    protocol_cls._on_ps_equip_module_result = _on_ps_equip_module_result
    return True

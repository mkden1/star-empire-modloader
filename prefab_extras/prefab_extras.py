"""prefab_extras.py - station prefabs that also keep what the server's snapshot leaves out, and a viewer for
what a prefab holds.

The server authors a prefab's snapshot (cargo, equipped modules, plugins, resource_caps) and validates it again
on load; this client only stores it in Prefabs.txt. The snapshot has no docking permission, management permission
or Trade Manager entries, but each of those already has an owner command the client sends from its own dialogs.
So, without touching the server's part:

  * when a snapshot arrives to be saved, ``capture`` reads the station's current docking policy, management
    policy, storage caps and trade entries from the client's live state and stores them under
    ``client_extras`` in the same Prefabs.txt row (the Trade Manager's list is requested just before the save
    command, so it is the station's current one);
  * when a prefab is loaded, ``client_extras`` is taken out of what is sent (the server never sees it) and, once
    the station reports it took the load (``prefab_can_load`` goes False in its next cargo push), the client
    sends the same commands the dialogs send: SET_PLAYER_STATION_DOCK_PERMS, SET_PLAYER_STATION_MANAGEMENT_PERMS,
    PS_MERGE_RESOURCE_CAPS and the chunked PS_TRADE_IMPORT. If the load is not confirmed within LOAD_WAIT_S,
    nothing is applied and the chat says so.

The viewer is a panel drawn beside the game's prefab dialog for the selected prefab: station contents, equipped
modules, plugins, storage caps, permissions, trade entries and what loading will do. Mouse wheel over it
scrolls it. The dialog's own panel rect is widened to include it, so clicks on the viewer do not close the dialog.

``install(SolarSystemWindow, render_module)`` (Client.py, bottom) wraps ``_send_json``, ``set_ps_prefab_snapshot``,
``set_ps_station_cargo``, ``_refresh_ps_prefab_dialog``, ``_draw_ps_prefab_dialog`` and
``apply_ps_trade_import_result``; ``filter_events`` is one marked line in the event loop (wheel + the wait's
timer). Any error switches the feature off for the session, and every wrapper still runs the game's own method.
Kill switch: STAR_EMPIRE_NO_PREFAB_EXTRAS=1.
"""

from __future__ import annotations

import logging
import os
import time

import pygame

import prefabs
from storage_caps_clipboard import normalise_storage_caps

logger = logging.getLogger(__name__)

EXTRAS_KEY = "client_extras"
EXTRAS_VERSION = 1
LOAD_WAIT_S = 15.0

DOCK_COMMAND = "SET_PLAYER_STATION_DOCK_PERMS"
MANAGEMENT_COMMAND = "SET_PLAYER_STATION_MANAGEMENT_PERMS"
CAPS_COMMAND = "PS_MERGE_RESOURCE_CAPS"

_failed = False
_colours: dict = {}


def _switch_off(where: str) -> None:
    global _failed
    if not _failed:
        logger.exception("prefab_extras: %s failed; the prefab extras are switched off for this session", where)
    _failed = True


def enabled() -> bool:
    return not _failed and os.environ.get("STAR_EMPIRE_NO_PREFAB_EXTRAS") != "1"


def reset_for_tests() -> None:
    global _failed
    _failed = False


def _store():
    return prefabs.PREFAB_STORE


# ── what a station's settings are ────────────────────────────────────────────

def _text(value) -> str:
    return str(value or "").strip().lower()


def current_policies(data) -> dict:
    """The docking and management policy in a station overlay's data, as the permission dialogs show them:
    "public" (docking only), a coalition rank key, or "owner"."""
    data = data or {}
    docking = "public" if data.get("public_dock") else (_text(data.get("dock_min_rank")) or "owner")
    return {"docking": docking, "management": _text(data.get("management_min_rank")) or "owner"}


def policy_label(policy) -> str:
    policy = _text(policy)
    if policy == "public":
        return "Public (anyone)"
    if policy in ("", "owner"):
        return "Owner only"
    return policy.replace("_", " ").title() + " & above"


def _same_station(a, b) -> bool:
    try:
        return int(a) == int(b)
    except (TypeError, ValueError):
        return False


def trade_rows(config) -> list:
    """The Trade Manager's ``{(category, item_type): {...}}`` as the rows Export / Import use."""
    rows = []
    for key, value in (config or {}).items():
        value = value or {}
        rows.append({"category": key[0], "item_type": key[1],
                     "sell_price": int(value.get("sell_price", 0)), "buy_price": int(value.get("buy_price", 0)),
                     "min_qty": int(value.get("min_qty", 0)), "max_qty": int(value.get("max_qty", 0))})
    rows.sort(key=lambda row: (str(row["category"]), str(row["item_type"])))
    return rows


def capture(win, station_id) -> dict:
    """The settings the server's snapshot leaves out, from the client's live state for this station.
    ``trades`` is None when the Trade Manager's list for this station is not known."""
    extras: dict = {"version": EXTRAS_VERSION}
    with win._lock:
        overlay = win._station_overlay
        data = dict(overlay.get("data") or {}) if overlay and overlay.get("kind") == "player_station" else None
    if data is not None and _same_station(data.get("attached_station_id"), station_id):
        extras.update(current_policies(data))
    caps = normalise_storage_caps(getattr(win, "_ps_resource_caps", None) or {})
    extras["resource_caps"] = dict(caps or {})
    manager = getattr(win, "_ps_trade_mgr", None) or {}
    if _same_station(manager.get("station_id"), station_id):
        extras["trades"] = trade_rows(manager.get("config"))
    else:
        extras["trades"] = None
    return extras


def extras_of(prefab):
    extras = prefab.get(EXTRAS_KEY) if isinstance(prefab, dict) else None
    return extras if isinstance(extras, dict) else None


def strip(snapshot) -> dict:
    """The snapshot as the server sent it: a copy without ``client_extras``."""
    return {key: value for key, value in snapshot.items() if key != EXTRAS_KEY}


# ── applying them after a load ───────────────────────────────────────────────

def apply(win, extras, station_id) -> list:
    """Send the commands that restore ``extras`` on the station; returns the chat lines (what was sent, what
    was skipped and why)."""
    done, skipped = [], []
    in_coalition = bool((getattr(win, "_coalition_state", None) or {}).get("coalition_id"))
    for key, command, noun in (("docking", DOCK_COMMAND, "docking"), ("management", MANAGEMENT_COMMAND, "management")):
        policy = _text(extras.get(key))
        if not policy:
            continue
        if key == "management" and policy == "public":
            continue
        if policy not in ("public", "owner") and not in_coalition:
            skipped.append(f"{noun} {policy_label(policy)} (you are not in a coalition)")
            continue
        win._send_json(command, {"station_id": station_id, "policy": policy})
        done.append(f"{noun} {policy_label(policy)}")
    caps = normalise_storage_caps(extras.get("resource_caps") or {})
    if caps:
        win._send_json(CAPS_COMMAND, {"station_id": station_id, "resource_caps": caps})
        done.append(f"{len(caps)} storage cap{'' if len(caps) == 1 else 's'}")
    rows = [row for row in (extras.get("trades") or ()) if isinstance(row, dict)]
    if rows:
        manager = win._ps_trade_mgr
        if manager.get("import"):
            skipped.append("trade entries (an import is already running)")
        else:
            parts = win._ps_trade_import_parts(rows, win._PS_TRADE_IMPORT_PART_BYTES)
            manager["import"] = {"station_id": station_id, "sent": 0, "parts": parts, "prefab": True}
            win._ps_trade_send_part()
            done.append(f"{len(rows)} trade entr{'y' if len(rows) == 1 else 'ies'}")
    lines = []
    if done:
        lines.append("Prefab: applied " + ", ".join(done) + ".")
    if skipped:
        lines.append("Prefab: skipped " + ", ".join(skipped) + ".")
    return lines


def _say(win, text: str, warn: bool = False) -> None:
    win.chat_log(text, (255, 170, 80) if warn else (120, 220, 160))


def begin_load(win, payload):
    """A PS_PREFAB_LOAD is about to be sent: take our part out of it and wait for the station to take the load.
    Returns the payload to send."""
    snapshot = payload.get("prefab") if isinstance(payload, dict) else None
    extras = extras_of(snapshot)
    if extras is None:
        return payload
    win._pfx_pending = {"station_id": payload.get("station_id"), "name": str(payload.get("name", "")),
                        "extras": extras, "started": time.monotonic()}
    sent = dict(payload)
    sent["prefab"] = strip(snapshot)
    return sent


def on_cargo(win, data) -> None:
    """The station's cargo push after the load: once it says a prefab can no longer be loaded, the load took."""
    pending = win.__dict__.get("_pfx_pending")
    if not pending or getattr(win, "_ps_prefab_can_load", True):
        return
    if isinstance(data, dict) and data.get("station_id") is not None \
            and not _same_station(data.get("station_id"), pending["station_id"]):
        return
    win._pfx_pending = None
    for line in apply(win, pending["extras"], pending["station_id"]):
        _say(win, line, warn="skipped" in line)


def tick(win, now: "float | None" = None) -> None:
    """Give up on a load that was never confirmed."""
    pending = win.__dict__.get("_pfx_pending")
    if not pending:
        return
    if (time.monotonic() if now is None else now) - pending["started"] < LOAD_WAIT_S:
        return
    win._pfx_pending = None
    _say(win, f'Prefab "{pending["name"]}": the load was not confirmed, so its docking and management '
              f"permissions, storage caps and trade entries were not applied.", warn=True)


# ── the viewer ───────────────────────────────────────────────────────────────

def _pretty(key) -> str:
    return str(key).split(":")[-1].replace("_", " ").strip().title() or str(key)


def _money(value) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _extras_summary(extras) -> str:
    """One line at the top of the viewer: what else the prefab keeps besides the station's contents."""
    if extras is None:
        return "No permissions, storage caps or trade entries saved (made before they were kept)."
    parts = []
    if extras.get("docking") or extras.get("management"):
        parts.append("permissions")
    caps = extras.get("resource_caps") or {}
    if caps:
        parts.append(f"{len(caps)} storage cap{'' if len(caps) == 1 else 's'}")
    trades = extras.get("trades")
    if trades:
        parts.append(f"{len(trades)} trade entr{'y' if len(trades) == 1 else 'ies'}")
    elif trades is None:
        parts.append("trade entries NOT captured")
    return "Also saved: " + (", ".join(parts) if parts else "nothing else") + "."


def sections(prefab) -> list:
    """The prefab as display lines ``(kind, left, right)``; kind is title, sub, head, row, dim or warn."""
    if not isinstance(prefab, dict):
        return [("warn", "That prefab could not be read.", "")]
    cargo = [row for row in (prefab.get("cargo") or ()) if isinstance(row, dict)]
    modules = [row for row in (prefab.get("equipped_modules") or ()) if isinstance(row, dict)]
    plugins = list(prefab.get("plugins") or ())
    caps = prefab.get("resource_caps") if isinstance(prefab.get("resource_caps"), dict) else {}
    extras = extras_of(prefab)
    names = {str(row.get("item_type")): row.get("display_name") for row in cargo if row.get("display_name")}
    vacuskin = prefabs.VACUSKIN_ITEM_KEY

    out = [("title", str(prefab.get("name", "")), "")]
    item_count = sum(max(0, int(row.get("quantity", 0) or 0)) for row in cargo if row.get("item_key") != vacuskin)
    out.append(("sub", f"{item_count:,} item{'' if item_count == 1 else 's'}  /  "
                       f"{sum(int(row.get('quantity', 1) or 1) for row in modules)} equipped  /  "
                       f"{len(plugins)} plugin{'' if len(plugins) == 1 else 's'}", ""))
    out.append(("sub", _extras_summary(extras), ""))

    out.append(("head", "STATION CONTENTS", ""))
    for row in cargo:
        label = str(row.get("display_name") or _pretty(row.get("item_key") or row.get("item_type")))
        quantity = f"x{_money(row.get('quantity', 0))}"
        if row.get("item_key") == vacuskin:
            out.append(("dim", label + " (built in)", quantity))
        else:
            out.append(("row", label, quantity))
    if not cargo:
        out.append(("dim", "Nothing saved.", ""))

    out.append(("head", "EQUIPPED MODULES", ""))
    for row in sorted(modules, key=lambda m: (m.get("position") is None, m.get("position") or 0)):
        kind = str(row.get("module_type", ""))
        label = str(names.get(kind) or _pretty(kind))
        position = row.get("position")
        if position is not None:
            label = f"{int(position) + 1}. {label}"
        quantity = int(row.get("quantity", 1) or 1)
        out.append(("row", label, f"x{quantity}" if quantity != 1 else ""))
    if not modules:
        out.append(("dim", "None.", ""))

    out.append(("head", "PLUGINS", ""))
    for plugin in plugins:
        if isinstance(plugin, dict):
            label = plugin.get("display_name") or _pretty(plugin.get("plugin_id") or plugin.get("item_type") or "plugin")
        else:
            label = _pretty(plugin)
        out.append(("row", str(label), ""))
    if not plugins:
        out.append(("dim", "None.", ""))

    own = {str(k): v for k, v in (extras or {}).get("resource_caps", {}).items()} if extras else {}
    all_caps = {**{str(k): v for k, v in caps.items()}, **own}
    out.append(("head", "STORAGE CAPS", ""))
    for key, cap in sorted(all_caps.items(), key=lambda kv: _pretty(kv[0]).lower()):
        out.append(("row", _pretty(key), _money(cap)))
    if not all_caps:
        out.append(("dim", "None set.", ""))

    out.append(("head", "PERMISSIONS", ""))
    if extras is None:
        out.append(("dim", "Not saved (this prefab was made before they were kept).", ""))
    else:
        out.append(("row", "Docking", policy_label(extras.get("docking")) if extras.get("docking") else "not saved"))
        out.append(("row", "Management",
                    policy_label(extras.get("management")) if extras.get("management") else "not saved"))

    out.append(("head", "TRADE BAY", ""))
    trades = extras.get("trades") if extras else None
    if extras is None or trades is None:
        out.append(("dim", "Not saved." if extras is None else "Not captured (open the Trade Manager, save again).",
                    ""))
    else:
        for row in trades:
            label = _pretty(row.get("item_type"))
            out.append(("row", label, f"buy {_money(row.get('buy_price'))} / sell {_money(row.get('sell_price'))}"
                                      f"   {_money(row.get('min_qty'))}-{_money(row.get('max_qty'))}"))
        if not trades:
            out.append(("dim", "No entries.", ""))

    out.append(("head", "WHEN LOADED", ""))
    if extras is None:
        out.append(("dim", "The server restores the station contents only.", ""))
    else:
        steps = []
        if extras.get("docking") or extras.get("management"):
            steps.append("docking / management permissions")
        if extras.get("resource_caps"):
            steps.append("storage caps")
        if trades:
            steps.append("trade entries (replaces the station's list)")
        out.append(("dim", "After the station takes the load: " + (", ".join(steps) if steps else "nothing more."), ""))
    return out


def _palette(render_module) -> dict:
    def pick(name, default):
        return getattr(render_module, name, default) if render_module is not None else default

    return {"value": pick("_PS_VALUE", (230, 240, 255)), "caption": pick("_PS_CAPTION", (130, 150, 180)),
            "text": pick("_PS_TEXT", (190, 205, 230)), "accent": pick("_PS_ACCENT", (90, 170, 255)),
            "warn": pick("_PS_WARN", (245, 150, 45)), "dim": pick("_PS_STEEL_DIM", (60, 75, 100))}


def _view(win) -> dict:
    view = win.__dict__.get("_pfx_view")
    if view is None:
        view = win._pfx_view = {"name": None, "lines": [], "scroll": 0, "max_scroll": 0, "rect": None, "step": 20}
    return view


def invalidate(win) -> None:
    """The catalogue changed (saved, deleted, dialog opened): read the selected prefab again."""
    _view(win)["name"] = None


def _lines_for(win, view, name) -> list:
    if view["name"] != name:
        try:
            prefab = _store().get(name)
        except Exception:
            logger.exception("prefab_extras: could not read the prefab for the viewer")
            prefab = None
        view.update(name=name, lines=sections(prefab), scroll=0)
    return view["lines"]


def viewer_rect(panel, win_w: int, s) -> "pygame.Rect":
    """Where the viewer goes: as tall as the dialog, beside it (right if it fits, else the side with more room),
    narrowed to the room there is. With no room worth using on either side it overlaps the window's right edge."""
    gap, want = s(8), s(400)
    right_room = win_w - panel.right - gap * 2
    left_room = panel.left - gap * 2
    on_right = right_room >= want or right_room >= left_room
    width = min(want, right_room if on_right else left_room)
    if width < s(220):
        width = min(want, max(win_w - gap * 2, s(220)))
        x = max(0, win_w - width - gap)
    elif on_right:
        x = panel.right + gap
    else:
        x = panel.left - gap - width
    return pygame.Rect(x, panel.y, width, panel.height)


def draw_viewer(win, ctx) -> None:
    """Draw the contents panel beside the prefab dialog (called after the game's own draw)."""
    dialog = win._ps_prefab_dialog
    view = _view(win)
    view["rect"] = None
    if dialog is None or dialog.get("name_input") is not None or dialog.get("delete_confirm") is not None:
        return
    name, panel = dialog.get("selected"), dialog.get("panel_rect")
    if not name or panel is None:
        return
    screen, win_w, win_h = ctx.screen, ctx.win_w, ctx.win_h
    s, font = win._s, win._instrument_font
    lines = _lines_for(win, view, name)

    rect = viewer_rect(panel, win_w, s)
    width, height = rect.size
    colours = _colours or _palette(None)
    screen.blit(win._hud_plate_layers(width, height, s(38), chamfer_max=s(12))["plate"], rect.topleft)
    screen.blit(font(14, bold=True).render("PREFAB CONTENTS", True, colours["value"]), (rect.x + s(16), rect.y + s(11)))
    screen.blit(font(10).render("WHAT THE SELECTED PREFAB SAVED", True, colours["caption"]),
                (rect.x + s(16), rect.y + s(32)))

    content = pygame.Rect(rect.x + s(14), rect.y + s(54), width - s(34), height - s(54) - s(14))
    body, bold, small = font(11), font(11, bold=True), font(10)
    line_h = body.get_linesize() + s(2)
    total = 0
    placed = []
    for kind, left, right in lines:
        gap = s(8) if kind == "head" and placed else 0
        total += gap
        placed.append((total, kind, left, right))
        total += line_h
    view["max_scroll"] = max(0, total - content.height)
    view["scroll"] = max(0, min(int(view["scroll"]), view["max_scroll"]))
    view["step"] = line_h * 2

    previous = screen.get_clip()
    screen.set_clip(content)
    for top, kind, left, right in placed:
        y = content.y + top - view["scroll"]
        if y + line_h < content.y or y > content.bottom:
            continue
        if kind == "head":
            screen.blit(bold.render(left, True, colours["accent"]), (content.x, y))
            pygame.draw.line(screen, colours["dim"], (content.x, y + line_h - 1), (content.right, y + line_h - 1))
            continue
        face, colour = {"title": (font(13, bold=True), colours["value"]), "sub": (small, colours["caption"]),
                        "dim": (small, colours["caption"]), "warn": (body, colours["warn"])}.get(
                            kind, (body, colours["text"]))
        right_surface = face.render(right, True, colours["value"] if kind == "row" else colour) if right else None
        room = content.width - (right_surface.get_width() + s(10) if right_surface else 0)
        screen.blit(face.render(win._gm_ellipsize(face, left, room), True, colour), (content.x, y))
        if right_surface:
            screen.blit(right_surface, (content.right - right_surface.get_width(), y))
    screen.set_clip(previous)

    if view["max_scroll"]:
        track = pygame.Rect(rect.right - s(14), content.y, s(4), content.height)
        pygame.draw.rect(screen, colours["dim"], track)
        thumb_h = max(s(18), int(track.height * content.height / (content.height + view["max_scroll"])))
        thumb_y = track.y + int((track.height - thumb_h) * view["scroll"] / view["max_scroll"])
        pygame.draw.rect(screen, colours["accent"], (track.x, thumb_y, track.width, thumb_h))

    view["rect"] = rect
    dialog["panel_rect"] = panel.union(rect)       # clicks on the viewer are clicks inside the dialog


def filter_events(win, events):
    """Called once per frame (Client.py): ends a load wait that timed out and takes the mouse wheel over the viewer
    out of the list so the game never sees it. Total."""
    if not enabled():
        return events
    try:
        tick(win)
        view = win.__dict__.get("_pfx_view")
        if not view or view.get("rect") is None or getattr(win, "_ps_prefab_dialog", None) is None:
            return events
        if not view["rect"].collidepoint(pygame.mouse.get_pos()):
            return events
        kept = []
        for event in events:
            if event.type == pygame.MOUSEWHEEL:
                view["scroll"] = max(0, min(view["max_scroll"], view["scroll"] - int(event.y) * view["step"]))
            else:
                kept.append(event)
        return kept if len(kept) != len(events) else events
    except Exception:
        _switch_off("filter_events()")
        return events


# ── hooks ────────────────────────────────────────────────────────────────────

def install(window_cls, render_module=None) -> bool:
    """Wrap the window's prefab methods (see the module docstring). Idempotent; True if hooked."""
    global _colours
    if window_cls.__dict__.get("_prefab_extras_installed"):
        return False
    needed = ("_send_json", "set_ps_prefab_snapshot", "set_ps_station_cargo", "_refresh_ps_prefab_dialog",
              "_draw_ps_prefab_dialog", "apply_ps_trade_import_result")
    missing = [name for name in needed if not hasattr(window_cls, name)]
    if missing:
        logger.warning("prefab_extras: not installed, the window has no %s", ", ".join(missing))
        return False
    _colours = _palette(render_module)

    def wrap(name, make):
        original = getattr(window_cls, name)
        wrapper = make(original)
        wrapper.__wrapped__ = original
        wrapper.__doc__ = original.__doc__
        wrapper.__name__ = name
        setattr(window_cls, name, wrapper)

    def send_json(original):
        def _send_json(self, cmd, payload):
            if enabled() and cmd in ("PS_PREFAB_SAVE", "PS_PREFAB_LOAD"):
                try:
                    if cmd == "PS_PREFAB_SAVE":
                        self.request_ps_trade_manager()    # before the save, so its answer is the station's current list
                    else:
                        payload = begin_load(self, payload)
                except Exception:
                    _switch_off(f"{cmd} hook")
            return original(self, cmd, payload)
        return _send_json

    def snapshot(original):
        def set_ps_prefab_snapshot(self, data):
            if enabled() and isinstance(data, dict) and isinstance(data.get("prefab"), dict):
                try:
                    extras = capture(self, data["station_id"])
                    data = dict(data)
                    data["prefab"] = {**data["prefab"], EXTRAS_KEY: extras}
                except Exception:
                    _switch_off("capture()")
            return original(self, data)
        return set_ps_prefab_snapshot

    def cargo(original):
        def set_ps_station_cargo(self, data):
            result = original(self, data)
            if enabled():
                try:
                    on_cargo(self, data)
                except Exception:
                    _switch_off("on_cargo()")
            return result
        return set_ps_station_cargo

    def refresh(original):
        def _refresh_ps_prefab_dialog(self):
            result = original(self)
            try:
                invalidate(self)
            except Exception:
                _switch_off("invalidate()")
            return result
        return _refresh_ps_prefab_dialog

    def draw(original):
        def _draw_ps_prefab_dialog(self, ctx):
            result = original(self, ctx)
            if enabled():
                try:
                    draw_viewer(self, ctx)
                except Exception:
                    _switch_off("draw_viewer()")
            return result
        return _draw_ps_prefab_dialog

    def import_result(original):
        def apply_ps_trade_import_result(self, data):
            ours = False
            try:
                imp = (self._ps_trade_mgr or {}).get("import")
                ours = bool(imp and imp.get("prefab") and isinstance(data, dict)
                            and data.get("station_id") == imp.get("station_id"))
            except Exception:
                pass
            result = original(self, data)
            if ours and enabled():
                try:
                    if data.get("ok"):
                        _say(self, f"Prefab: the station took {int(data.get('count', 0))} trade entries.")
                    else:
                        _say(self, f"Prefab: the trade entries were refused ({data.get('reason', 'refused')}).",
                             warn=True)
                except Exception:
                    _switch_off("import result")
            return result
        return apply_ps_trade_import_result

    wrap("_send_json", send_json)
    wrap("set_ps_prefab_snapshot", snapshot)
    wrap("set_ps_station_cargo", cargo)
    wrap("_refresh_ps_prefab_dialog", refresh)
    wrap("_draw_ps_prefab_dialog", draw)
    wrap("apply_ps_trade_import_result", import_result)
    window_cls._prefab_extras_installed = True
    return True

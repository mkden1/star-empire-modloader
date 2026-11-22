"""log_out.py - a SWITCH USER button on the in-game ESC menu: pick one of your saved characters, and the game restarts logged in as them.

The game cannot return to its login screen inside the running process (after login the world window owns the display and the
process ends when it closes), and the saved logins live on the login screen. So Switch User does what Exit Game does, closing the
game the normal way, and starts a fresh client that waits for this one to be gone (relaunch.py). The chosen account's NAME travels
to the new client in an environment variable (never a password: the game's own saved login holds that); the new client's login
screen selects that saved account and presses Connect, once. The last entry of the list, "Login screen...", restarts without
logging in, to type another account in or register one.

The ESC menu is laid out by the game (and by the ui_pages mod, which adds rows of its own), so the button does not take a row of
its own: a small panel of the menu's own look is drawn just BELOW the menu panel by a hook after ``_draw_esc_menu`` (its place is
kept on the window as ``_lo_rect``, never in the game's own button table), and the clicks on it are taken from the frame's events
before the game sees them (one line in Client.py). The list of characters is a panel over the menu; while it is up it takes every
mouse press and the wheel, and a click anywhere outside it closes it. Like Exit Game everything acts when the mouse is RELEASED on
the button, so the button-up stays inside the window. If the restart cannot be set up the game is NOT closed and the chat says so.
Every function the game calls here is total: an error switches the button off for the session.
"""

from __future__ import annotations

import logging
import os
import weakref

import pygame

import relaunch

logger = logging.getLogger(__name__)

ENV_OFF = "STAR_EMPIRE_NO_LOG_OUT"
ENV_AUTO_LOGIN = "STAR_EMPIRE_AUTO_LOGIN"        # the saved account the next client logs in as (a name, never a password)
LABEL = "  Switch User"
OTHER_LABEL = "Login screen..."
_PAGE_FLAGS = ("_options_open", "_ui_panels_settings_open", "_capture_settings_open", "_mods_page_open")

_failed = False
_login_ref = None                                # the game's LoginScreen (it holds the saved accounts), captured when it loads its options


def _switch_off(where: str) -> None:
    global _failed
    if not _failed:
        _failed = True
        logger.exception("log_out: switched off for this session (%s)", where)


def enabled() -> bool:
    return not _failed and not os.environ.get(ENV_OFF)


def reset_for_tests() -> None:
    global _failed, _login_ref
    _failed = False
    _login_ref = None


# ── the saved accounts (the login screen's) ──────────────────────────────────

def remember_login_screen(screen, *args, **kwargs) -> None:
    """After ``LoginScreen.load_options``: keep (weakly) the login screen, which holds the saved accounts for the whole session."""
    global _login_ref
    try:
        _login_ref = weakref.ref(screen)
    except TypeError:
        _login_ref = None


def login_screen():
    return _login_ref() if _login_ref is not None else None


def saved_accounts() -> "list[str]":
    """The names of the saved accounts, in the order the login screen lists them."""
    screen = login_screen()
    try:
        return list(screen._saved_account_names()) if screen is not None else []
    except Exception:
        return []


def auto_login_on_draw(screen, *args, **kwargs) -> None:
    """After ``LoginScreen.draw``: the first time, log in as the account the previous client asked for (once; the variable is
    consumed so a later restart of this client does not repeat it)."""
    if not enabled() or screen.__dict__.get("_lo_auto_done"):
        return
    try:
        screen._lo_auto_done = True
        name = os.environ.pop(ENV_AUTO_LOGIN, "").strip()
        if not name:
            return
        if screen._select_saved_account(name):
            screen._submit()
        else:
            screen._login_status = f"'{name}' is not a saved account: type it in."
            screen._login_err = True
    except Exception:
        _switch_off("auto_login_on_draw()")


# ── geometry ─────────────────────────────────────────────────────────────────

def button_of(win) -> "pygame.Rect | None":
    """Where the Switch User button was drawn last (None when it is not up). Kept on the window under its own name: the game's own
    ``_esc_btn_rects`` holds exactly the game's buttons, and code that walks it must keep seeing only those."""
    return win.__dict__.get("_lo_rect")


def menu_showing(win) -> bool:
    """Is the ESC menu itself (not Options or one of the pages that replace it) what the player is looking at?"""
    return bool(getattr(win, "_esc_menu_open", False)) and not any(getattr(win, flag, False) for flag in _PAGE_FLAGS)


def strip_rect(panel: pygame.Rect, s, screen_h: int) -> pygame.Rect:
    """The button's panel: as wide as the menu panel, just below it (just above it when the screen has no room below)."""
    height = s(48) + s(14) * 2
    gap = s(6)
    rect = pygame.Rect(panel.x, panel.bottom + gap, panel.w, height)
    if rect.bottom > screen_h:
        rect.bottom = panel.top - gap
    rect.y = max(0, rect.y)
    return rect


def _button_rect(strip: pygame.Rect, s) -> pygame.Rect:
    pad = s(14)
    return pygame.Rect(strip.x + pad, strip.y + pad, strip.w - pad * 2, s(48))


def chooser_layout(names, current, screen_size, s, scroll: int = 0):
    """The list of characters: ``(panel, rows, max_scroll, scroll)`` with ``rows`` as ``(rect, account, enabled)`` for the entries in
    view (``account`` None is the "Login screen" entry; the character being played is listed but not clickable). The panel is centred;
    when there are more entries than fit in 90% of the screen's height only some are in view and the wheel moves them."""
    width_px, height_px = screen_size
    entries = [(name, name != current) for name in names] + [(None, True)]
    row_h, gap, pad, title_h = s(40), s(6), s(14), s(34)
    fit = max(1, (int(height_px * 0.9) - pad * 2 - title_h + gap) // (row_h + gap))
    max_scroll = max(0, len(entries) - fit)
    scroll = max(0, min(scroll, max_scroll))
    shown = entries[scroll:scroll + fit]
    width = min(s(320), width_px - s(20))
    height = pad * 2 + title_h + len(shown) * (row_h + gap) - gap
    panel = pygame.Rect((width_px - width) // 2, max(0, (height_px - height) // 2), width, height)
    rows = []
    y = panel.y + pad + title_h
    for account, enabled_ in shown:
        rows.append((pygame.Rect(panel.x + pad, y, width - pad * 2, row_h), account, enabled_))
        y += row_h + gap
    return panel, rows, max_scroll, scroll


def _state(win) -> dict:
    state = win.__dict__.get("_lo_state")
    if state is None:
        state = win._lo_state = {"open": False, "scroll": 0, "panel": None, "rows": [], "armed": None, "swallow_up": False,
                                 "button_armed": False}
    return state


def _current(win) -> "str | None":
    return getattr(win, "_username", None) or None


# ── drawing ──────────────────────────────────────────────────────────────────

def _draw_chooser(win, screen, state: dict) -> None:
    s = win._s
    panel, rows, max_scroll, scroll = chooser_layout(saved_accounts(), _current(win), screen.get_size(), s, state["scroll"])
    state["scroll"], state["panel"], state["rows"] = scroll, panel, rows
    tint = getattr(win, "_draw_screen_tint", None)           # dim the menu behind the list, as the game dims the world behind the menu
    if callable(tint):
        tint(screen, screen.get_width(), screen.get_height(), (0, 0, 0, 150))
    pygame.draw.rect(screen, (10, 12, 28), panel, border_radius=6)           # opaque: nothing of the menu shows through
    pygame.draw.rect(screen, (30, 60, 160), panel, 1, border_radius=6)
    title = win._cached_label("SWITCH TO", (100, 160, 255), win._F(13))
    screen.blit(title, (panel.x + (panel.w - title.get_width()) // 2, panel.y + s(14)))
    font = win._F(13)
    for rect, account, enabled_ in rows:
        if account is None:
            fill, border, colour, text = (20, 40, 90), (60, 120, 220), (160, 210, 255), "  " + OTHER_LABEL
        elif enabled_:
            fill, border, colour, text = (60, 44, 12), (210, 160, 50), (255, 210, 120), "  " + account
        else:
            fill, border, colour, text = (22, 24, 38), (50, 56, 84), (110, 120, 150), f"  {account}  (playing)"
        win._btn(screen, rect, fill=fill, border=border, border_w=1, radius=4)
        label = font.render(text, True, colour)
        screen.blit(label, (rect.x + s(12), rect.y + (rect.h - label.get_height()) // 2))
    if max_scroll:                                           # a thin bar on the right edge says there is more
        track = pygame.Rect(panel.right - s(8), panel.y + s(48), s(3), panel.h - s(60))
        pygame.draw.rect(screen, (40, 56, 96), track, border_radius=2)
        thumb_h = max(s(12), track.h * (len(rows)) // (len(rows) + max_scroll))
        thumb_y = track.y + (track.h - thumb_h) * scroll // max_scroll
        pygame.draw.rect(screen, (110, 160, 240), (track.x, thumb_y, track.w, thumb_h), border_radius=2)


def draw_strip(win, ctx) -> None:
    """After the game has drawn its ESC menu: the Switch User panel (and the list of characters when it is open), and where they are
    for the clicks."""
    rects = getattr(win, "_esc_btn_rects", None)
    state = _state(win)
    if not menu_showing(win) or rects is None or rects.get("panel") is None:
        win._lo_rect = None
        state.update(open=False, panel=None, rows=[], armed=None, button_armed=False)
        return
    screen = ctx.screen
    s = win._s
    strip = strip_rect(rects["panel"], s, screen.get_height())
    button = _button_rect(strip, s)
    panel = pygame.Surface(strip.size, pygame.SRCALPHA)
    panel.fill((10, 12, 28, 230))
    screen.blit(panel, strip.topleft)
    pygame.draw.rect(screen, (30, 60, 160), strip, 1, border_radius=6)
    win._btn(screen, button, fill=(60, 44, 12), border=(210, 160, 50), border_w=1, radius=4)
    label = win._F(13).render(LABEL, True, (255, 210, 120))
    screen.blit(label, (button.x + s(12), button.y + (button.h - label.get_height()) // 2))
    win._lo_rect = button
    if state["open"]:
        _draw_chooser(win, screen, state)
    else:
        state.update(panel=None, rows=[])


def after_draw_esc_menu(win, ctx, *args, **kwargs) -> None:
    if not enabled():
        return
    try:
        draw_strip(win, ctx)
    except Exception:
        _switch_off("after_draw_esc_menu()")


# ── the clicks ───────────────────────────────────────────────────────────────

def _say(win, text: str) -> None:
    chat = getattr(win, "chat_log", None)
    if callable(chat):
        try:
            chat(text, (255, 180, 80))
        except Exception:
            pass


def begin(win, account: "str | None" = None) -> bool:
    """Close the game the way Exit Game does and start a new client behind it that logs in as *account* (None: stops at the login
    screen). False (and the game stays open) when the new client cannot be set up."""
    if win.__dict__.get("_lo_pending"):
        return True
    if not relaunch.restart_after_exit(env={ENV_AUTO_LOGIN: account or ""}):
        _say(win, "Could not set up the restart: use Exit Game, then start the game again.")
        return False
    win._lo_pending = True
    pygame.event.post(pygame.event.Event(pygame.QUIT))
    return True


def _row_at(state: dict, pos):
    for rect, account, enabled_ in state["rows"]:
        if rect.collidepoint(pos):
            return rect, account, enabled_
    return None


def _chooser_event(win, state: dict, event) -> bool:
    """One event while the list of characters is up. True when it was used up (every mouse press / release / wheel is)."""
    kind = event.type
    if kind == pygame.MOUSEWHEEL:
        state["scroll"] = max(0, state["scroll"] - event.y)
        return True
    if kind not in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
        return False
    left = getattr(event, "button", 0) == 1
    if kind == pygame.MOUSEBUTTONDOWN:
        row = _row_at(state, event.pos) if left else None
        if row is not None and row[2]:
            state["armed"] = row[1] if row[1] is not None else ""
        elif left and state["panel"] is not None and state["panel"].collidepoint(event.pos):
            pass                                                     # the title, a gap or the playing character: nothing, and stay open
        else:
            state["open"] = False                                    # a click anywhere else closes the list
            state["swallow_up"] = left
        return True
    # a release
    if left and state["armed"] is not None:
        armed, state["armed"] = state["armed"], None
        row = _row_at(state, event.pos)
        if row is not None and row[2] and (row[1] or "") == armed:
            begin(win, row[1])
        return True
    if left and state["swallow_up"]:
        state["swallow_up"] = False
    return True


def filter_events(win, events):
    """The frame's events, minus the clicks on the Switch User button and on the list of characters (the game never sees them: a press
    there would count as a click outside the menu and close it). Total."""
    if not enabled():
        return events
    try:
        state = _state(win)
        if not menu_showing(win):
            state.update(open=False, armed=None, button_armed=False, swallow_up=False)
            return events
        button = button_of(win)
        if button is None:
            return events
        kept = []
        for event in events:
            if state["open"]:
                if _chooser_event(win, state, event):
                    continue
                kept.append(event)
                continue
            left = getattr(event, "button", 0) == 1
            if state["swallow_up"] and left and event.type == pygame.MOUSEBUTTONUP:
                state["swallow_up"] = False
                continue
            if left and event.type == pygame.MOUSEBUTTONDOWN and button.collidepoint(event.pos):
                state["button_armed"] = True
                continue
            if left and event.type == pygame.MOUSEBUTTONUP and state["button_armed"]:
                state["button_armed"] = False
                if button.collidepoint(event.pos):
                    state.update(open=True, scroll=0, armed=None)
                continue
            kept.append(event)
        return kept
    except Exception:
        _switch_off("filter_events()")
        return events


# ── installing ───────────────────────────────────────────────────────────────

def install(render_cls) -> "list[str]":
    """Hook the game's ESC menu drawing. Idempotent; never raises (see ``modloader.install_methods``)."""
    if render_cls.__dict__.get("_log_out_installed"):
        return []
    import modloader
    problems = modloader.install_methods(render_cls, None, None, (), after={"_draw_esc_menu": after_draw_esc_menu})
    render_cls._log_out_installed = True
    return problems


def install_login(login_cls) -> "list[str]":
    """Hook the login screen: it keeps the saved accounts (``load_options``), and logs in as the account the previous client asked
    for (``draw``, the first time). Idempotent; never raises."""
    if login_cls.__dict__.get("_log_out_installed"):
        return []
    import modloader
    problems = modloader.install_methods(login_cls, None, None, (), after={"load_options": remember_login_screen,
                                                                           "draw": auto_login_on_draw})
    login_cls._log_out_installed = True
    return problems


# what test_mods_apply checks after every game update: (game module, game class, game method, the function that runs around it)
HOOKS = (("render_mixin", "RenderMixin", "_draw_esc_menu", "after_draw_esc_menu"),
         ("login", "LoginScreen", "load_options", "remember_login_screen"),
         ("login", "LoginScreen", "draw", "auto_login_on_draw"))

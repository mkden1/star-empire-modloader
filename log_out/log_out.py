"""log_out.py - a LOG OUT button on the in-game ESC menu: back to the login screen, to pick another character.

The game cannot return to its login screen inside the running process (after login the world window owns the display and the
process ends when it closes), so Log Out does what Exit Game does, closing the game the normal way, and starts a fresh client
that waits for this one to be gone (relaunch.py): it opens on the login screen, where the saved-accounts list picks the character.

The ESC menu is laid out by the game (and by the ui_pages mod, which adds rows of its own), so the button does not take a row of
its own: a small panel of the menu's own look is drawn just BELOW the menu panel by a hook after ``_draw_esc_menu`` (its place is
kept on the window as ``_lo_rect``, never in the game's own button table), and the clicks on it are taken from the frame's events
before the game sees them (one line in Client.py). Like Exit Game it acts when the mouse is RELEASED on it, so the button-up stays
inside the window. If the restart cannot be set up the game is NOT closed and
the chat says so. Every function the game calls here is total: an error switches the button off for the session.
"""

from __future__ import annotations

import logging
import os

import pygame

import relaunch

logger = logging.getLogger(__name__)

ENV_OFF = "STAR_EMPIRE_NO_LOG_OUT"
LABEL = "  Log Out"
_PAGE_FLAGS = ("_options_open", "_ui_panels_settings_open", "_capture_settings_open", "_mods_page_open")

_failed = False


def _switch_off(where: str) -> None:
    global _failed
    if not _failed:
        _failed = True
        logger.exception("log_out: switched off for this session (%s)", where)


def enabled() -> bool:
    return not _failed and not os.environ.get(ENV_OFF)


def reset_for_tests() -> None:
    global _failed
    _failed = False


def button_of(win) -> "pygame.Rect | None":
    """Where the Log Out button was drawn last (None when it is not up). Kept on the window under its own name: the game's own
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


def draw_strip(win, ctx) -> None:
    """After the game has drawn its ESC menu: the Log Out panel, and where it is for the clicks."""
    rects = getattr(win, "_esc_btn_rects", None)
    if not menu_showing(win) or rects is None or rects.get("panel") is None:
        win._lo_rect = None
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


def after_draw_esc_menu(win, ctx, *args, **kwargs) -> None:
    if not enabled():
        return
    try:
        draw_strip(win, ctx)
    except Exception:
        _switch_off("after_draw_esc_menu()")


# ── the click ────────────────────────────────────────────────────────────────

def _say(win, text: str) -> None:
    chat = getattr(win, "chat_log", None)
    if callable(chat):
        try:
            chat(text, (255, 180, 80))
        except Exception:
            pass


def begin(win) -> bool:
    """Close the game the way Exit Game does and start a new client behind it. False (and the game stays open) when the new client
    cannot be set up."""
    if win.__dict__.get("_lo_pending"):
        return True
    if not relaunch.restart_after_exit():
        _say(win, "Could not set up the restart: use Exit Game, then start the game again.")
        return False
    win._lo_pending = True
    pygame.event.post(pygame.event.Event(pygame.QUIT))
    return True


def filter_events(win, events):
    """The frame's events, minus the press and release on the Log Out button (the game never sees them: a press there would count
    as a click outside the menu and close it). Total."""
    if not enabled():
        return events
    try:
        if not menu_showing(win):
            win._lo_armed = False
            return events
        rect = button_of(win)
        if rect is None:
            return events
        kept = []
        for event in events:
            if getattr(event, "button", 0) == 1 and event.type == pygame.MOUSEBUTTONDOWN and rect.collidepoint(event.pos):
                win._lo_armed = True
                continue
            if getattr(event, "button", 0) == 1 and event.type == pygame.MOUSEBUTTONUP and win.__dict__.get("_lo_armed"):
                win._lo_armed = False
                if rect.collidepoint(event.pos):
                    begin(win)
                continue
            kept.append(event)
        return kept
    except Exception:
        _switch_off("filter_events()")
        return events


def install(render_cls) -> "list[str]":
    """Hook the game's ESC menu drawing. Idempotent; never raises (see ``modloader.install_methods``)."""
    if render_cls.__dict__.get("_log_out_installed"):
        return []
    import modloader
    problems = modloader.install_methods(render_cls, None, None, (), after={"_draw_esc_menu": after_draw_esc_menu})
    render_cls._log_out_installed = True
    return problems


# what test_mods_apply checks after every game update: (game module, game class, game method, the function that runs around it)
HOOKS = (("render_mixin", "RenderMixin", "_draw_esc_menu", "after_draw_esc_menu"),)

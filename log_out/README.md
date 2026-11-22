# Log out

A LOG OUT button on the in-game ESC menu: back to the login screen to pick another character.

## What it does

- Adds a **Log Out** button in its own small panel just below the ESC menu (above it if the screen has no room below). It is amber, so it is not mistaken for the red Exit Game.
- Pressing it closes the game the way Exit Game does and starts a new client as soon as the old one is gone. The new client opens on the login screen, where the saved-accounts list lets you pick another character. The official launcher's update check is skipped for that restart.
- Like Exit Game it acts when the mouse button is **released** on it (a press that you drag away from cancels).
- If the restart cannot be set up, the game is **not** closed and the chat says so.
- Why a restart: the game cannot go back to its login screen inside the running process (after login the world window owns the display and the process ends when it closes).

## Using it

- Press Esc, click **Log Out**, log in as whoever you like.

## Settings

- `STAR_EMPIRE_NO_LOG_OUT=1` turns the button off.

## Needs

`core`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

## Under the hood

- Patches the game's `Client.py` (2 hunks: the import and install after the `render_mixin` import, and one line that filters the frame's events). The loader applies them in memory; the game's files are never changed.
- Code: `log_out.py` (the panel, the click, the hand-off) and, in `core`, `relaunch.py` (a small detached helper process waits for this one's PID to end, then starts the launch command again; it falls back to starting anyway after 45 s).
- The ESC menu is laid out by the game (and the UI Panels mod adds rows of its own), so nothing about its rows is changed: a hook after `_draw_esc_menu` draws the panel relative to the menu's own panel, which is why it fits however many rows the menu has. Its clicks are taken from the frame's events before the game sees them, because a press outside the menu panel would otherwise close the menu.

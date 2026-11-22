# Switch user

A SWITCH USER button on the in-game ESC menu: pick one of your saved characters and the game restarts logged in as them. (The mod's folder is still called `log_out`, so an install that already has it keeps working.)

## What it does

- Adds a **Switch User** button in its own small panel just below the ESC menu (above it if the screen has no room below). It is amber, so it is not mistaken for the red Exit Game.
- Clicking it opens a list of your saved characters (the ones in the login screen's saved-accounts list). The character you are playing is shown but cannot be picked. Pick another one and the game closes the way Exit Game does, a new client starts as soon as the old one is gone, and its login screen selects that character and presses Connect for you, once.
- The last entry, **Login screen...**, restarts without logging anyone in, to type another account in or register one.
- Only the character's *name* travels to the new client (in an environment variable that is used up on the first frame): the password stays in the game's own saved login. If the saved account is gone by then, the login screen says so and nothing is sent. The official launcher's update check is skipped for that restart.
- Like Exit Game everything acts when the mouse button is **released** (a press you drag away from cancels). While the list is up it takes every mouse press and the wheel (a long list scrolls); a click anywhere outside it closes it.
- If the restart cannot be set up, the game is **not** closed and the chat says so.
- Why a restart: the game cannot go back to its login screen inside the running process (after login the world window owns the display and the process ends when it closes).

## Using it

- Press Esc, click **Switch User**, click a character. A few seconds later you are logged in as them.

## Settings

- `STAR_EMPIRE_NO_LOG_OUT=1` turns the button and the automatic login off.

## Needs

`core`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

## Under the hood

- Patches the game's `Client.py` (2 hunks: the import and the installs after the `render_mixin` import, and one line that filters the frame's events). The loader applies them in memory; the game's files are never changed.
- Code: `log_out.py` (the panel, the list, the clicks, the hand-off, the login screen's side) and, in `core`, `relaunch.py` (a small detached helper process waits for this one's PID to end, then starts the launch command again with `STAR_EMPIRE_AUTO_LOGIN=<name>`; it falls back to starting anyway after 45 s).
- The ESC menu is laid out by the game (and the UI Panels mod adds rows of its own), so nothing about its rows is changed: a hook after `_draw_esc_menu` draws the panel relative to the menu's own panel, which is why it fits however many rows the menu has. Its place is kept on the window (`_lo_rect`), never in the game's own button table. Clicks are taken from the frame's events before the game sees them, because a press outside the menu panel would otherwise close the menu.
- The list of characters comes from the login screen object (`LoginScreen.load_options` is hooked to keep a weak reference to it; it holds the saved accounts for the whole session). On the next start `LoginScreen.draw` is hooked: its first frame calls the game's own `_select_saved_account(name)` and `_submit()`, which waits for the server's READY line before sending, as a manual login does.
- If `core` is older than this mod (it lacks `relaunch.py`) the mod switches itself off instead of stopping the game from starting, and the Mods page says why.

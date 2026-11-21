# Click diagnostics

Writes one client-log line per left click saying what was in range and what the game picked.

## What it does

- Selecting a warp gate or a bot sometimes does nothing, more often when the camera is zoomed far out. Nothing in the game's own log says why: an object of higher priority can win, a ship's click-assist can be suppressed by something else in range, or a panel can take the click first.
- So each left click is recorded as one line: the zoom, the click position, what was in range (ships, gates, planets, stations, asteroids with their distances) and what the picker decided.

## Using it

- Nothing to do: it works in the background. Look for `click_diag:` lines in the client log (`star_empire_client.log`). Its on/off switch is on the Data Capture page of the ESC menu (needs the `ui_pages` mod), where it is called "Click diagnostics".

## Needs

`core`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

## Under the hood

- Patches the game's `Client.py` (1 hunk) (the loader applies them in memory; the game's files are never changed).
- Code: `click_diag.py`.

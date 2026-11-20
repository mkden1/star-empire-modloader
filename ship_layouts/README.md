# Ship layouts

Save a ship's fit under a name and compare it with the ship you are flying.

## What it does

- Open the SHIP LAYOUTS window from the HUD menu or with `/layouts`. On the left are your saved layouts and the buttons SAVE CURRENT SHIP, UPDATE, RENAME and DELETE. On the right is the comparison of the selected layout against the ship you are flying, live: swap a module and its row turns green.
- Only what differs is listed (SHOW MATCHING lists the rest). The stats table shows saved, now and the change, green when better and red when worse.
- A save asks the server for fresh Ship Specs and waits for the answer, so the stats are the ones the server computes for the setup you are looking at.

## Using it

- `/layouts`, or SHIP LAYOUTS in the HUD menu.

## Settings

- `STAR_EMPIRE_NO_SHIP_LAYOUTS=1` turns it off.

## Needs

`render_alias`, `core`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

## Under the hood

- Patches the game's `Client.py` (9 hunks) (the loader applies them in memory; the game's files are never changed).
- Code: `ship_layouts.py`, `ship_layouts_window.py`.
- Writes: The layouts themselves go in the shared `scan_data/ship_layouts/` folder, and `ship_layouts_window.json` next to the client holds the window's rectangle and last selection.

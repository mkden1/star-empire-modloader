# ESC menu pages (UI Panels, Data Capture, Mods)

Three pages in the ESC menu: hide or show HUD panels, switch the data recorders, and see which mods are loaded.

## What it does

- **UI Panels**: switches to hide or show the Inventory, Target, Turret control, Fighter control, Squad roster, Fighters / Drones, Chat, Minimap, Coalition, Friends and Combat Log panels. Your choices are remembered.
- **Data Capture**: one switch per recorder (planet scans, station logs, colony and station state, the freeze watchdog, click diagnostics, sound prefetch, and so on), so a recorder can be turned off without editing anything. Only the switches of mods you have picked are listed (no `scan_capture`, no planet-scan switch); with none of those mods the page says so.
- **Mods**: a grid of every mod with a dot: green = in effect, red = switched off because the game's code changed under one of its patches (the box says why), grey = turned off on purpose, blue = a library other mods use. The box under the grid says what the hovered or clicked mod does and which parts of the game it patches.

## Using it

- Open the ESC menu and choose the page.

## Settings

- `STAR_EMPIRE_NO_MODS=1` starts the game with no mods at all; `STAR_EMPIRE_DISABLE_MODS=a,b` turns named mods off without deleting them (the Mods page shows them grey).

## Needs

`core`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

## Under the hood

- Patches the game's `Client.py` (24 hunks), `render_mixin.py` (16 hunks) (the loader applies them in memory; the game's files are never changed).
- Code: `capture_settings_panel.py`, `mods_page.py`, `settings_page.py`, `ui_panels_page.py`.

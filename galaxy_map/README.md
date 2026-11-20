# Galaxy map window and Survey view

The galaxy map as a movable, resizable window that stays open while you fly, plus a Survey view.

## What it does

- The game's map is a full-screen modal: it dims the game, takes every click and closes when you set a route from it. This mod adds a second form of the same map: a window you can move and resize, with the game behind it undimmed and clicks outside it going to the game.
- The window is for flying and finding systems: it has the system search and Favorites but not the Coalitions / Territory / Survey tabs or VIEW SCANS, which stay full-screen.
- Survey view (full screen): systems are coloured by how many Terra planets they have, on a red-to-green scale, and a bright ring marks a system whose known planets you have all scanned. Ctrl/Cmd-click systems to compare their combined resources.
- VIEW SCANS (full screen) regenerates the scan archive's dashboard and opens it in the browser. It needs the `scan_archive` mod and is left out without it.

## Using it

- **M** opens the map the way you last used it (window or full screen).
- The button left of the close X switches between FULL SCREEN and WINDOW.
- In the window, drag the header to move it and any edge or corner to resize. Setting an autopilot route from it (right-click a system, or a Favorite) leaves it open, and with Auto-center ticked the map follows you from system to system.

## Settings

- `STAR_EMPIRE_NO_MAP_WINDOW=1` turns the window feature off (the map goes back to the game's full-screen form).

## Needs

`render_alias`, `core`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

Works better with: `scan_archive` (the VIEW SCANS button).

## Under the hood

- Patches the game's `Client.py` (20 hunks), `render_mixin.py` (41 hunks) (the loader applies them in memory; the game's files are never changed).
- Code: `galaxy_map_render.py`, `galaxy_map_scans.py`, `galaxy_map_window.py`, `galaxy_survey.py`.
- Writes: `galaxy_map_window.json` next to the client: the chosen mode and where the window sits.

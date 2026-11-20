# Squad and fighter rosters

The SQUAD and FIGHTERS / DRONES roster panels of the HUD.

## What it does

- Two compact panels: the squad members in this system, and the drones and fighters you have deployed. Each row shows a name on the Shields row with shield and energy bars; the exact numbers appear only while a row is hovered.
- They are movable and can be hidden from ESC → UI Panels (with the `ui_pages` mod).

## Using it

- Nothing to do: it works in the background.

## Needs

Nothing else.

Works better with: `ui_pages` (a switch to hide the panels on ESC → UI Panels).

## Under the hood

- Patches the game's `Client.py` (3 hunks), `render_mixin.py` (7 hunks) (the loader applies them in memory; the game's files are never changed).
- Code: `squad_hud.py`.

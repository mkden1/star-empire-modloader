# Combat Log

A Combat Log window and floating damage numbers.

## What it does

- A movable Combat Log window lists the damage you deal and take, and damage numbers float up from each hit.
- Who fired is worked out through the game's shield-effects matcher, so a hit is credited to the right shooter.
- Hits are always recorded; the window only shows or hides the list.

## Using it

- **Shift+L** shows or hides the Combat Log (plain L is the game's own Skills key).
- The mouse wheel over it scrolls the history; drag it to move it.
- With the `ui_pages` mod there is also a Combat Log switch on ESC → UI Panels.

## Needs

`damage_types`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

Works better with: `ui_pages` (a Combat Log switch on ESC → UI Panels).

## Under the hood

- Patches the game's `Client.py` (10 hunks), `render_mixin.py` (2 hunks), `shield_effects.py` (4 hunks) (the loader applies them in memory; the game's files are never changed).
- Code: `combat_log.py`, `combat_log_render.py`, `shield_attribution.py`.

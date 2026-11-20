# Inventory filter

FILTER buttons and a side tab on ship inventories and station Storage: sort, filter by type and rarity, and show only a saved loadout.

## What it does

- Every ship inventory (yours, and each stored ship's window) and the player station's Storage tab gets a FILTER button at the end of its search bar. It opens a tab docked beside the window.
- The choices combine: within TYPE and within RARITY any ticked value passes, and between the groups every one must pass.

## Using it

- In the tab: **SORT** by name, type, rarity or quantity (press again to reverse, once more to switch off).
- **SHOW** everything, only what is fitted, or only what sits in the hold (in Storage: equipped / in storage).
- **TYPE** and **RARITY** chips for the categories and tiers that window actually holds, in the tier's colour.
- **LOADOUT**: pick a saved Ship Layout (needs the `ship_layouts` mod) and the window lists only the parts of that setup; **SELECT SHOWN** then selects what is listed so you can move it to another ship in one drag.

## Settings

- `STAR_EMPIRE_NO_INVENTORY_FILTER=1` turns it off.

## Needs

`render_alias`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

Works better with: `ship_layouts` (the LOADOUT filter).

## Under the hood

- Patches the game's `Client.py` (4 hunks), `render_mixin.py` (4 hunks) (the loader applies them in memory; the game's files are never changed).
- Code: `inventory_filter.py`, `inventory_filter_model.py`.

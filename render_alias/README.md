# Render alias

A one-line helper: names the game's `render_mixin` module so other mods can attach to it.

## What it does

- Several mods attach methods to the game's render code from a block at the end of `Client.py`. That block needs the module under a name; this mod adds `import render_mixin as _render_mixin_module` for it. It does nothing visible on its own.

## Using it

- It is a dependency: `modctl` copies it when a mod needs it.

## Needs

Nothing else.

Needed by: `autopilot_eta`, `construction_eta`, `construction_tooltip`, `galaxy_map`, `inventory_filter`, `prefab_extras`, `ship_layouts`, `station_gear_upgrade`.

## Under the hood

- Patches the game's `Client.py` (1 hunk) (the loader applies them in memory; the game's files are never changed).

# Station prefab extras

Station prefabs also keep docking and management permissions, storage caps and trade entries, and a viewer shows what a prefab holds.

## What it does

- The server's prefab snapshot (cargo, equipped modules, plugins, resource caps) has no docking permission, management permission or Trade Manager entries. This mod saves them too, from the client's live state, in the same `Prefabs.txt` row under `client_extras`. The server never sees that part.
- When a prefab is loaded and the station confirms it took the load, the client sends the same commands the dialogs send to set the permissions, caps and trade entries. If the load is not confirmed in time nothing is applied and the chat says so.
- A viewer panel is drawn beside the game's prefab dialog for the selected prefab: station contents, equipped modules, plugins, storage caps, permissions, trade entries and what loading will do.

## Using it

- Open the station prefab dialog. The mouse wheel over the viewer scrolls it.

## Settings

- `STAR_EMPIRE_NO_PREFAB_EXTRAS=1` turns it off.

## Needs

`render_alias`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

## Under the hood

- Patches the game's `Client.py` (3 hunks) (the loader applies them in memory; the game's files are never changed).
- Code: `prefab_extras.py`.

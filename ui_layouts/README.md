# UI layouts

Save where the HUD's windows sit under a name and switch between the layouts, for a client played on screens of different sizes.

## What it does

- **Saves the places of your HUD**: every panel the game lays out (Ship Inventory, Turret Control, Fighter Control, Target, Chat, the Quick Actions bar, the minimap, Stats, Squad, Coalition, Friends, ...), the sizes you gave the ones that can be resized, and the window size the layout was made for. The Combat Log, the Galaxy Map window and the Ship Layouts window are included when you have them.
- **Switches between layouts**: LOAD puts every window back where it was saved. UNDO takes the last LOAD back.
- **Only moves windows**: the Quick Actions bindings, chat tabs and colours, Galaxy Map favourites, the hangar sort order, which windows are open and the minimap zoom are never touched.
- **Fits a layout to another window size** when you load one made for a different size: each window keeps its distance from the screen corner it was nearest to, the way the game itself moves windows when you resize it. A layout made for exactly this size comes back exactly.
- **Why it exists**: when the window changes size the game pins every window to the corner it was nearest to, and that is not reversible, so after a trip to a smaller screen and back the windows are somewhere else. Arrange the HUD once on each screen, save it, and load it when you are back on that screen.

## Using it

- Open the window from the HUD menu (UI LAYOUTS) or with `/uilayout`.
- SAVE CURRENT asks for a name (it suggests the window size, such as `2560 x 1440`). Select a layout and press LOAD, or double-click it. A layout made for the window size you have now is marked THIS SIZE, and the window selects it when it opens.
- UPDATE replaces the selected layout with what is on screen now, DELETE removes it (both ask for a second click), RENAME renames it.
- In chat, for when the windows are out of reach: `/uilayout list`, `/uilayout save <name>` (saves, or updates the layout of that name), `/uilayout load <name>` (the name, or its first letters when they are unique).
- Layouts are kept in `ui_layouts.json` next to the client, so every account run from that folder sees the same ones.

## Settings

- `STAR_EMPIRE_NO_UI_LAYOUTS=1` turns it off.

## Needs

`core`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

## Under the hood

- Patches the game's `Client.py` (9 hunks) (the loader applies them in memory; the game's files are never changed).
- Code: `ui_layouts.py` (what a layout holds, fitting, the file), `ui_layouts_window.py` (the window and the chat command).
- It does not know the panels one by one: a layout is the game's own `_panel_layout_dict()` reduced to its places, and loading hands the game's own `apply_panel_layout` the current dict with the saved places laid over it, so a window the game adds later comes along.
- Writes: `ui_layouts.json` next to the client (every write re-reads the file and changes only its own layout, so two clients cannot wipe each other's).

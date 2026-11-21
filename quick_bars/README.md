# Quick bars

Extra quick action bars you can put anywhere on screen, keys you can rebind (the slots, the game's one-press hotkeys, with Ctrl, Shift or Alt if you like), and loadouts: save the whole arrangement under a name and switch between arrangements, e.g. one per class.

## What it does

- **Extra quick action bars**: up to six more rows of ten slots, each its own panel you place anywhere on screen. They work exactly like the game's bar, because it is the game's own bar code that runs them: drag an item from the inventory onto a slot, click a slot to fire it, hold a missile / fighter slot to repeat it, hold a slot and drop it on another to move or swap, right-click to clear (or, on an empty slot, to add a class skill).
- **Each character has its own bars**, as the game keeps each character's bar apart. Every bar has a name, can be hidden (a hidden bar is not drawn and its keys do nothing), and keeps its place as a fraction of the screen, so it stays where you put it, relative to the window, at any size.
- **A key for every slot**: the first extra bar is Shift+1 ... Shift+0, the next Ctrl+1 ... Ctrl+0, then Alt, then pairs, and every slot's key can be changed. The slot's key tab shows it (`s1` for Shift+1, `c1` for Ctrl+1, `F1` for F1).
- **Rebinds the game's quick bar slots and its one-press hotkeys**: dock / undock (R), cycle weapon (E), tractor beam (G), galaxy map (M), missions (J), stats (L), scoop cargo (C), enter / use warp gate (F), interact menu (X), all turrets (O), cloak (K), cycle target (Q), cycle ship target (Z), turret aim overlay (P), fighters focus target (T), fighters recall (Y), inventory panel (I) and autopilot to target (V). Two new actions for the game's bar: next tab and previous tab.
- **Loadouts**: a named snapshot of your game bar (both tabs), your extra bars (names, key sets, places, what is hidden, every slot) and the keys you changed for those bars' slots. Loading one replaces all of that in one step; UNDO puts back what the last load replaced, and it survives a restart. The window tags the loadout that is exactly what you have now (`CURRENT`), so you can see at a glance whether your arrangement is saved. Your game hotkeys (dock, map ...) and the keys of the game's own bar are not part of a loadout: they are how you like to play, not what a class needs.
- **Loadouts are shared by your characters**: save one on a Warden and load it on another character. Each character keeps their own UNDO.
- **One key, one action**: a key press does exactly one thing, as in the game. Nothing here runs several actions from one press, and only one held key repeats at a time, across all bars.
- **A combo has one action**: binding a combo that another action has takes it from that action (the window says so). The old key of an action that moved does nothing, and an unbound action does nothing. Plain bindings keep ignoring Shift and Ctrl, as the game's hotkeys always have; a binding that asks for modifiers needs exactly those, and the most specific binding wins.
- **Left alone**: flight (W, A, S, D, the arrows, Space), chat and menu keys (Enter, Esc, Tab, `/`), zoom and text editing keys cannot be bound.
- With no binding changed and no extra bar, the mod does nothing at all: the game plays exactly as it does without it.

## Using it

- Open the QUICK BARS window from the HUD menu, with `/keys` (the KEYS tab) or `/bars` (the BARS tab).
- **BARS**: ADD BAR asks for a name (it suggests one). Select a bar, then HIDE / SHOW, RENAME, RESET PLACE (back above the game's bar) or DELETE (a second click confirms). Move a bar by dragging its padding or the gaps between its slots. New bars stack above the game's bar with their slots lined up with its own.
- **LOADOUTS**: arrange your bars as you want them, then SAVE CURRENT and name it. Select a loadout and LOAD it (or double-click it), UNDO to take the last load back, UPDATE to store what you have now into it (a second click confirms), RENAME, DELETE (a second click confirms). Two loadout keys in the KEYS tab, `Next loadout` and `Previous loadout`, have no key until you give them one: they step through the saved loadouts in name order (from the one you have now) and say in chat which one they loaded.
- **KEYS**: click an action, then press its new key (hold Ctrl, Shift or Alt with it if it should need them). Esc cancels, Backspace unbinds. UNBIND and RESET act on the selected action, RESET ALL puts every default back (a second click confirms). Amber means you changed it. Each extra bar is a group of its own in the list.
- In chat, for when the window is out of reach: `/keys list`, `/keys reset`, `/bars list`, `/bars add <name>`, `/bars show <name>`, `/bars hide <name>`, `/loadout` (the window), `/loadout list`, `/loadout save <name>` (an existing name is updated), `/loadout load <name>` (the start of a name will do when it is unambiguous), `/loadout undo`, `/loadout next`, `/loadout prev`.
- If you also have the UI layouts mod, a layout saves and restores where the extra bars are (their places only; matched by bar, or by key set for another character's bars). A loadout carries the places too, so loading one puts the bars where they were when you saved it.
- Everything is kept in `quick_bars.json` next to the client: the key bindings (only what you changed), the bars, one entry per character, the loadouts (one entry each, shared), and each character's undo of the last load.

## Settings

- `STAR_EMPIRE_NO_QUICK_BARS=1` turns the whole mod's window off. `STAR_EMPIRE_NO_QUICK_KEYS=1` turns off the key handling only, `STAR_EMPIRE_NO_EXTRA_BARS=1` the extra bars only.

## Needs

`core`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

## Under the hood

- Patches the game's `Client.py` (11 hunks) and `render_mixin.py` (1 hunk) (the loader applies them in memory; the game's files are never changed).
- Code: `keybinds.py` (the actions, the combos, how a key press is matched, the file), `quick_keys.py` (the hook in the event loop), `bars.py` (what a bar and an account's set are), `bar_context.py` (runs the game's bar code on an extra bar), `extra_bars.py` (the bars in the running game), `loadouts.py` (what a loadout is, how two setups are compared, the saved list), `loadout_ops.py` (capture, load, undo and cycle on the running game), `quick_bars_window.py` (the window, `/keys`, `/bars` and `/loadout`).
- An extra bar is not a copy of the game's bar code. `bar_context.in_bar` swaps one bar's state into the handful of window attributes the game's bar code reads (its slots, where it was drawn, the rectangles it published, the press / lift / held-key state) for the length of a call, and puts the game's own back afterwards; the game's own save is replaced meanwhile, so an extra bar can never overwrite the game's saved bar. A test compares that list of attributes with what the game's code really writes, so a game update that adds state says so.
- The keys: one hook sits right after the event loop's repeat guard, where every text field has already had its chance at the key. A rebound hotkey is handed to the game as the key it knows the action by; a slot or tab key is carried out through the game's own bar methods.
- The extra bars are drawn right after the game's bar (one hook), registered with the window manager on the HUD layer (so a window above a bar gets the click first and the world never sees a click on a bar), and handed their mouse events by the window's event filter.
- A loadout is loaded through the game's own bar code: the game bar goes in through the game's `_hotbar_layout_in` (empty slots padded, so slots the loadout leaves out are emptied), the class skills are refreshed and the bar is saved the way the game saves it after any change. A load the game chokes on is rolled back to what you had. Which loadout is `CURRENT` is never remembered, it is worked out by comparing, so it is right after a restart and stops being right the moment you change a slot.
- Writes: `quick_bars.json` next to the client. Every write re-reads the file and changes only its own part, so two clients (two accounts) cannot wipe each other's.

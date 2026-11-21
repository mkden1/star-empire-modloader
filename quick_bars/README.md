# Quick bars

Rebind the quick action slot keys and the game's one-press hotkeys, with Ctrl, Shift or Alt if you like.

## What it does

- **Rebinds the keys of the quick action bar**: each of the ten slots, and two new actions, next tab and previous tab, which have no key until you give them one. Slots can have a combo each, so Shift+1 can be one action while plain 1 stays another.
- **Rebinds the game's one-press hotkeys**: dock / undock (R), cycle weapon (E), tractor beam (G), galaxy map (M), missions (J), stats (L), scoop cargo (C), enter / use warp gate (F), interact menu (X), all turrets (O), cloak (K), cycle target (Q), cycle ship target (Z), turret aim overlay (P), fighters focus target (T), fighters recall (Y), inventory panel (I) and autopilot to target (V).
- **One key, one action**: a key press does exactly one thing, as in the game. Nothing here runs several actions from one press.
- **A combo has one action**: binding a combo that another action has takes it from that action (the window says so). The old key of an action that moved does nothing, and an unbound action does nothing.
- **Plain bindings keep ignoring Shift and Ctrl**, as the game's hotkeys always have: Shift+Z is still Z run backwards. A binding that asks for modifiers needs exactly those, and the most specific binding wins.
- **The slot's key tab shows the key**: `F1`, or `c1` for Ctrl+1, `s1` for Shift+1, `a1` for Alt+1.
- **Left alone**: flight (W, A, S, D, the arrows, Space), chat and menu keys (Enter, Esc, Tab, `/`), zoom and text editing keys cannot be bound. Held missile and fighter slot keys keep repeating as before.
- With no binding changed the mod does nothing at all: the game plays exactly as it does without it.

## Using it

- Open the KEY BINDINGS window from the HUD menu or with `/keys`. Click an action, then press its new key (hold Ctrl, Shift or Alt with it if it should need them). Esc cancels, Backspace unbinds.
- UNBIND and RESET act on the selected action. RESET ALL puts every default back (a second click confirms). Amber means you changed it.
- In chat, for when the window is out of reach: `/keys list` shows what you changed, `/keys reset` puts every default back.
- The bindings are kept in `quick_bars.json` next to the client, so every account run from that folder uses the same keys.

## Settings

- `STAR_EMPIRE_NO_QUICK_BARS=1` turns it off. `STAR_EMPIRE_NO_QUICK_KEYS=1` turns off the key handling only, leaving the window.

## Needs

`core`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

## Under the hood

- Patches the game's `Client.py` (10 hunks) and `render_mixin.py` (1 hunk) (the loader applies them in memory; the game's files are never changed).
- Code: `keybinds.py` (the actions, the combos, how a key press is matched, the file), `quick_keys.py` (the one hook in the event loop), `quick_bars_window.py` (the window and `/keys`).
- The hook sits right after the loop's repeat guard, where every text field has already had its chance at the key. A rebound hotkey is handed to the game as the key the game knows it by, so none of the game's hotkey code is touched; a slot or tab key is carried out through the game's own `_hotbar_activate_slot`, `_hotbar_key_hold_begin` and `_hotbar_page`.
- Writes: `quick_bars.json` next to the client. Only what you changed is saved, so the defaults can move with a game update.

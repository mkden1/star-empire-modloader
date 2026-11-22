# Change mods

A CHANGE MODS button under the login card, and a note of the mods that changed since you last started.

## What it does

- Adds a **CHANGE MODS** button below the login card (it shows how many mods are on). It opens the same mod picker window that `Choose Mods.bat` opens, and the login window stays alive while the picker is open.
- **Apply** in the picker with a different set of mods: the client closes the way Exit Game does and a new one starts as soon as it is gone, so the new mods are in use at once. The new client skips the official launcher's update check (the game was just started).
- **Apply** with nothing changed: a short "No changes." under the button, the login screen stays. Closing the picker without applying does nothing at all.
- If the picker crashes, the button says so and points at `picker_error.txt`. If the restart cannot be set up, nothing is closed: the button says the mods are saved and to start the game again.
- **A note of what changed.** At the top of the login screen, for a while, a short list says which mods were *updated*, are *new* or were *removed* since the last time you started the game ("Mods changed since your last start: updated: quick_bars, ui_layouts"). That is how an update the launch-time refresh brought in, or a mod you copied in by hand, becomes visible. The first start, and any start where nothing changed, show nothing, and a change you make yourself with CHANGE MODS is not reported back to you.
- The button only appears where there is something to pick from: the stand-alone package, with `modloader/modpicker.py` and a `mods_available/` folder. In the developer folder (all mods in `mods/`) or a package older than the picker it is not drawn.

## Using it

- Click CHANGE MODS on the login screen (it is there while the game connects, too), tick what you want, press Apply.

## Settings

- `STAR_EMPIRE_NO_CHANGE_MODS=1` turns the button and the note off.
- `mods_seen.json` (next to the client) is the note's memory: delete it and the next start shows nothing and starts a new one.

## Needs

`core`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

## Under the hood

- Patches the game's `Client.py` (1 hunk: it imports the module and installs the hooks right after `from login import LoginScreen`). The loader applies it in memory; the game's files are never changed.
- Code: `change_mods.py` (the button, the picker hand-off, the comparison of `mods/` before and after) and, in `core`, `relaunch.py` (starts the new client once this one is gone).
- The button is drawn by a hook after `LoginScreen.draw` and clicked through a hook before `LoginScreen.handle_event`, both found by method name (no anchor in the game's text). The card ignores clicks outside its own controls, so the game's handler never sees the click as anything.
- The note compares a hash of each mod folder's code and data (not its README, screenshots or caches, and not file times, so a mod that was only copied again is not "updated") with `mods_seen.json`, which sits next to the client and is rewritten at every start. The hashing runs on a background thread so the login window never waits for the disk. In the developer folder, where the picker is absent, nothing is compared or shown.
- "Did the mods change" after the picker is a fingerprint of `mods/` (every mod folder with each file's size and time; README, screenshots and `.replaced` do not count) taken before the picker opens and after it closes.
- The picker runs as a separate process and is polled once per frame, so the login window never freezes. Exit code 0 means applied, 2 means closed without applying.

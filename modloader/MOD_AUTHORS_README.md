> Notes for writing mods. This is the development README of the mod loader's repository: the tests and the
> `custom_build/` scripts it mentions are not part of this package.

# The mods

This is the `mods_available` branch of the mod loader's repository: one folder per mod, listed at the end of this file.
Clone it as `mods_available` inside the ready-to-run loader (the `modloader` branch) and pick the mods you want with
`Choose Mods.bat` (or `modloader\modctl.bat add NAME` on a console), which copies them and the mods each one needs into `mods/`. Everything below is for people writing or changing mods.

Everything a mod changes in the dev's game code is a **patch**. The dev's files in `_internal/` stay exactly as shipped;
the loader (`_internal/modloader.py` in the development branch, `modloader/modloader.py` in the ready-to-run folder)
builds the patched source in memory when the client starts (and when the tests import the game), so a game update never
has to be merged into our work.

```
mods/
  <name>/
    mod.json            {"description": "...", "requires": ["other_mod"], "category": "features", "recommended": true,
                         "order": 0, "enabled": true}  (all optional but the description; see "The mod picker" below)
    Client.py.patch     changes to the dev's Client.py (one patch file per game file: <File>.py.patch)
    render_mixin.py.patch
    chat_suggest.py     the mod's own code ...
    test_chat_suggest.py ... and its tests
    (a web asset, a data file)   anything else the mod's modules read from their own folder
```

A mod folder is the whole mod: its patches, its modules, its tests, its data. Every folder with `.py` files in it is put
on `sys.path` (switched-off mods too: another mod's module may import it), so the dev's patched code just says
`import chat_suggest` and the tests can import each other. A folder with no patch is a library other mods use
(`scan_archive`: the scan dashboard and its web assets). Code that needs the client's folders asks `modloader`
(`modloader.REPO_DIR`, and `modloader.PROJECT_DIR` for the shared `scan_data/`), never its own `__file__`, so a mod can
be moved without breaking it.

Only the loader and its checks stay in `_internal/` with the dev's files: `modloader.py`, `conftest.py`,
`test_modloader.py`, `test_mods_apply.py`, `test_hotkey_shadowing.py`.

Run the tests with `pytest _internal mods` (`mods/conftest.py` and `_internal/conftest.py` both call
`modloader.setup_for_tests()`).

## Patch format

A unified diff without line numbers. Each hunk says "find these lines, change them like so":

```
@@ a label that shows up in messages @@
 import capture_settings_panel  # MOD: the ESC menu's Data Capture page      <- context: must match exactly once
+import chat_suggest  # MOD: suggest system names while typing /ap            <- added
 import pump_watch                                                           <- context

@@ the zoom floor @@
-    CAM_MIN_ZOOM = 0.10                                                     <- replaced (must match)
+    CAM_MIN_ZOOM = 0.05
```

* A line starting with a space is context, `-` is removed, `+` is added. A blank context line is a single space
  (an empty line in the middle of a hunk counts as one too, in case an editor stripped the space).
* The hunk's context and `-` lines together must match the dev's file **exactly once** (trailing whitespace is
  ignored). Nothing about line numbers matters, so lines added elsewhere in the file never break a hunk.
  If the code you anchor on is common, add more context lines until it is unique.
* Prefer anchoring on a distinctive code line rather than a comment or a bracket: that is what an update is
  least likely to touch.
* `@@ label @@ order=N` sets the sequence of insertions, from any mod, that land at the very same spot (lower
  first). Hunks without it come after those with it, so a new hook at a busy spot just goes last.
* A hunk that only adds lines needs at least one context line to find its place.

## When an update breaks a patch

The client still starts: a mod whose hunk no longer matches (or matches twice), or whose edits overlap another
mod's, is switched off **as a whole**, together with any mod that `requires` it. You get a message box when the
client starts and the reason in `mods_report.txt` (repo root), for example
`Client.py: anchor not found for mods/chat_suggest/Client.py.patch:7 [import chat_suggest ...]`.

1. `custom_build\python.exe -I _internal\modloader.py` prints the same report (exit code 1 when a mod was
   switched off), and `_internal/test_mods_apply.py` fails with the report, so the suite catches it too. In the
   stand-alone package the same check is `Check Mods.bat`.
2. Open the named patch file, find the new place in the dev's file, and change the context lines of that hunk.
   A hunk that applies to both the old and the new version of the game is better than one that fits only the new one.
3. Run the check again, then the suite (`custom_build\python.exe custom_build\run_tests.py`, which compares the
   failures with the dev's own known ones). A dev test that fails with `STAR_EMPIRE_NO_MODS=1` too is not ours.

## Switches

* `STAR_EMPIRE_NO_MODS=1` -- run the game exactly as the dev shipped it ("is this bug mine or the dev's?").
* `STAR_EMPIRE_DISABLE_MODS=chat_suggest,galaxy_map` -- turn named mods off.
* `"enabled": false` in a mod's `mod.json` -- the same, kept.

## Rules

* Never edit the dev's files (`Client.py`, `render_mixin.py`, `protocol.py`, ...). `test_mods_apply.py` fails when a
  `# MOD` marker shows up in one of them. A new hook is a hunk in a mod's patch, and a new feature is a new folder.
* A mod's `.py` code can be as big as it likes; keep the hooks in the dev's files to a line or two, calling into it.
* A test that pins what the game code says (`... .read_text()` on `Client.py` and friends) must read the patched
  text with `modloader.game_source("Client.py")`, not the file on disk.
* Mods are picked one by one, so a mod must work with only what it `requires`. Anything it imports at module level from
  ANOTHER mod's folder (or imports in a line its patch adds to the dev's file) goes in `mod.json`'s `requires`; shared
  helpers live in the `core` mod (`capture_settings`, `scan_paths`, `scan_writer`, `scan_file_lock`, `scan_data`,
  `target_info`, `window_geometry`, `construction_stock`, `station_bodies`), not in a mod that is a feature. Two ways a
  mod used to need much more than it uses, both fixed: it imported a helper from a feature mod (the Ship Layouts window
  took its move / resize helpers from the galaxy map's module; the scan archive and the map took the file READERS from
  the recorder mods, which meant installing every recorder to read their files), and it used another mod's state without
  saying so (the UI Panels page read `_combat_log_open`; it now adds that row only when the attribute exists). A link to a
  mod that is only an extra (the map's VIEW SCANS button, pump_watch telling click_diag about events) is looked up inside
  a function with `try: import x / except ImportError:` and the feature is left out without it. A `try: import` at the top
  of a module still counts as a requirement. `test_mods_apply.py` checks all of this, and builds every mod alone with its
  requirements.

## The dev's tests, and tests that a mod makes fail

The dev's own test files in `_internal/` are never edited either: they run against the patched game, so a mod that changes
behaviour on purpose can make one of them fail. The mod lists that test in its own `expected_failures.txt` (no mod does
today; an earlier one that changed the connection handshake used it):

```
test_some_file.py::SomeClass::test_name  # why this mod makes it fail on purpose
```

Both `conftest.py` files mark it as a *strict* expected failure (`modloader.expected_failures`): the run shows it as `xfailed`,
not a failure, and when it starts passing (the dev changed the test, or the mod went) the run fails until the line is removed.
`test_mods_apply` checks every line names a test that exists. The mod's own version of the test lives in the mod's folder.
Tests a mod adds for the dev's code go in the mod's folder too; they can borrow the dev test class's fixtures
(`setUp = dev.SomeClass.setUp`).

The whole test run uses a throwaway `scan_data/` (`modloader.setup_for_tests`): the dev's tests exercise code the scan mods
hook into, and no test may write the real archive.

## Big blocks of code live in modules, not in patch text

A hook in the dev's file should be a line or two; the code it calls is ordinary Python in the mod's folder.

* **Methods added to a game class** (a combat-log recorder, a survey drawing, a protocol handler): write them in a plain
  class in the mod's module, exactly as they would stand in the game's class body. The module has
  `def install(target): return modloader.install_methods(target, WindowMethods)`, and the patch carries the call
  (`import combat_log` / `combat_log.install(SolarSystemWindow)`) in the block at the end of Client.py, or, for a class in
  another file, a hunk marked `eof` at the very end of that file. List the attachment in the module as
  `ATTACHES = (("Client", "SolarSystemWindow", "WindowMethods"),)`: `test_mods_apply` then checks after every update that
  each method really is on the game's class (the dev did not add one of the same name) and that the module imports or
  defines every name it uses (`modloader.missing_globals`).
* **Private names of the game's file** that such code uses (a palette, a helper): list them in the module's `_BORROWED`
  tuple and pass it to `install_methods`; it copies them in from the game's module at start-up. If an update renames one,
  nothing is attached, the problem is logged and the ESC menu's Mods page shows that mod in red with the reason.
* **Run a function before / after one of the game's methods, found by its NAME** (state a mod sets up after the game's
  own `_init_...`, a saved layout read after `apply_panel_layout`, a server message recorded before the game's handler
  shows it): no patch and no anchor in the dev's text at all, so only a rename of that method can break it.
  `modloader.install_methods(target, WindowMethods, before={"_on_x": fn}, after={"_init_y": fn}, result={"_dict_z": fn})`
  (or `wrap_method(target, "name", after=fn)`): `before(self, *args, **kwargs)` runs ahead of the game's method,
  `after(self, *args, **kwargs)` once it has returned, `result(self, value, *args, **kwargs)` gets what it returned and
  may change it in place or return a replacement. The game's own exceptions stay the game's (and skip `after` / `result`);
  a hook that raises is logged, shown red on the Mods page and skipped, so a bug in a mod never stops the game's method.
  All or nothing: if any named method is gone, nothing is attached or wrapped and the Mods page says which. Several mods
  can hook one method (install order). List them in the module as
  `HOOKS = (("Client", "SolarSystemWindow", "apply_panel_layout", "load_layout"),)` and `test_mods_apply` checks after
  every update that each is in place. Prefer this to a patch hunk whenever the code only needs to run *around* a game
  method; a hunk is for code that must sit *inside* one.
* **A block in the middle of a big function** (the squad / fighter HUD in the run loop): make it a function taking the
  locals it reads (`squad_hud.draw_rosters(self, _frame, entities, ...)`) and leave one call in the patch. Only worth it
  when the block assigns nothing the rest of the function uses afterwards, and it deserves a test with a stand-in window.
* What stays patch text is the glue inside the dev's own functions (an extra `elif`, a changed condition, a value in a
  dict): small, and anchored on the dev's code.

## A README for every mod, and screenshots

Every mod folder has a `README.md`: a title, a one-sentence summary, then **What it does**, **Using it**, **Settings** (when
there are any), **Needs** and **Under the hood**. **Needs** lists exactly what `mod.json` requires (a test in the
development branch fails if they differ), so the mod picker can read the READMEs for names, summaries and requirements. The
list at the end of this file links to each one.

Screenshots go in the mod's own `screenshots/` folder (PNG or JPG, a GIF for a short animation, each under about 300 KB)
and are shown in the README with a relative link, which GitHub displays on the branch page:

    ![The Combat Log window](screenshots/combat-log.png)

Put them in a **Screenshots** section after **Using it**. The mod picker shows every picture in `screenshots/` (in file
name order, scaled to its window) under the mod's summary, with the link's text as the caption, and leaves the
**Screenshots** section itself out. Neither the picker nor `modctl` copies the README or the screenshots into a player's
`mods/` folder: they are for choosing a mod, not for running it.

## The mod picker

The ready-to-run folder has a window (`Choose Mods.bat`, and by itself the first time the game is started) that lists the
mods in `mods_available\`: a tick box for each, its README and screenshots beside it. It reads two keys of `mod.json`:

* `"category"`: where the mod is listed: `features` (things you see and use), `speed` (invisible fixes for freezes and slow
  saves), `recorders` (save game data to files), `diagnostics` (write troubleshooting information), `advanced`, or
  `library` (code other mods need: not listed, only added when a mod that needs it is ticked). Missing = `features`.
* `"recommended"`: `true` if the **Recommended** button ticks it (and it is ticked on the very first start). Missing =
  `false`. Keep it for mods that only show something or fix something invisibly: nothing that aims, fires, targets, moves
  the camera by itself, sends commands to the server or records data, and nothing meant for troubleshooting.

Ticking a mod also adds what it `requires`. **Apply** makes `mods\` match: it copies what is missing, removes what was
unticked and refreshes copies that differ from `mods_available\`. Folders in `mods\` that are not in `mods_available\` are a
player's own mods and are left alone, together with the mods they need.

**Names in the game.** The ESC menu's Mods page lists every mod by its folder name, so the picker shows that name under the
title. Only recorders and diagnostics get a switch on the ESC menu's Data Capture page (an optimisation, category
`speed`, has none: having the mod is its switch), the page lists a switch only when its mod is in effect, and the
mod's README says what the switch is called there (a test checks that, so the two cannot drift apart). Panels a mod
adds to the UI Panels page get a row only when that mod is in effect too.

## License

MIT (`LICENSE`) for the loader, the mods and their tests. The patches contain short lines of the game's own code as anchors;
that code stays the developer's, and so does the game, which is not in this repository.

## The ready-to-run folder

The repository's `modloader` branch is the folder a player uses: the loader, the one launch file (which also runs the
official launcher to update the game and checks every mod on each start), the mod picker window (`Choose Mods.bat`;
`modloader\modctl.bat` does the same on a console), and a copy of the Python runtime and the libraries the game imports, so it
needs nothing installed but the game itself (which is not in it). `mods_available\` there is this branch. It is generated
from the development branch by `custom_build\publish_branch.py`, which builds the package (`build_package.py`: the mods
are put in a stripped copy of the game, with decoy libraries that fail if the game's own are ever used, and the patched
game is imported once per mod, with only that mod and what it needs) and commits the result. Run it again after a
change to the loader, the launcher or the runtime. Nothing personal is copied: the build only copies from lists of what
goes in and refuses saved logins, logs and settings. The loader looks for the game in the folder it sits in, in
`game_dir.txt`, or in `STAR_EMPIRE_GAME_DIR`.

## The mods

| mod | what it does | patches | needs |
|---|---|---|---|
| [`asset_watch`](asset_watch/README.md) | Times every image the window loads on demand (asset_watch.py). | 1 in Client |  |
| [`autopilot_eta`](autopilot_eta/README.md) | The autopilot banner's 'ETA m:ss' (autopilot_eta.py, autopilot_route_sim.py). | 2 in Client | render_alias |
| [`chat_suggest`](chat_suggest/README.md) | Suggests system names (favourites first) while you type /ap in the chat (chat_suggest.py). | 3 in Client |  |
| [`click_diag`](click_diag/README.md) | Click diagnostics: one client-log line per left click saying what was in range (click_diag.py). | 1 in Client | core |
| [`colony_scan`](colony_scan/README.md) | Captures colony station setups and shuttle routes for the scan archive (colony_scan_logger.py, shuttle_route_capture.py). | 6 in Client | core |
| [`combat_log`](combat_log/README.md) | The Combat Log window and floating damage numbers, with shooter attribution through the shield-effects matcher. | 16 in Client, render_mixin, shield_effects | damage_types |
| [`construction_diag`](construction_diag/README.md) | Logs what the server sends about station construction (construction_diag.py). | 1 in Client |  |
| [`construction_eta`](construction_eta/README.md) | Estimated time to complete for each Construction Bay project (construction_eta.py). | 1 in Client | render_alias, core |
| [`construction_tooltip`](construction_tooltip/README.md) | What a selected Construction Bay blueprint costs against the station's stock (construction_tooltip.py). | 1 in Client | render_alias, core |
| [`core`](core/README.md) | Shared helpers other mods import: the Data Capture switches (capture_settings), where scan_data/ lives (scan_paths), the off-thread file writer (scan_writer, scan_file_lock), the read side of every scan file (scan_data), the orbit figures (target_info), the move / resize / plate geometry of the HUD windows (window_geometry), the Construction Bay stock helpers (construction_stock) and which body a station is on (station_bodies). It changes nothing in the game by itself. | (library) |  |
| [`crash_log_thread`](crash_log_thread/README.md) | crash_log writes the client log from a background thread, so file I/O cannot stall the game. | 5 in crash_log |  |
| [`damage_types`](damage_types/README.md) | Imports the damage_types helpers that the combat log uses. | 1 in Client |  |
| [`freeze_watchdog`](freeze_watchdog/README.md) | Stack dump into the log when a thread stalls (freeze_watchdog.py); heartbeats from the main loop and the renderer. | 4 in Client | core |
| [`galaxy_map`](galaxy_map/README.md) | The galaxy map as a movable, resizable window (galaxy_map_window.py), the Survey view and the VIEW SCANS button (which needs the scan_archive mod and is left out without it). | 61 in Client, render_mixin | render_alias, core |
| [`gc_watch`](gc_watch/README.md) | Logs garbage-collection pauses and freezes the heap after start-up (gc_watch.py). | 4 in Client |  |
| [`inventory_filter`](inventory_filter/README.md) | FILTER buttons and a tab on the ship inventories and station Storage (inventory_filter.py). | 8 in Client, render_mixin | render_alias |
| [`music_prefetch`](music_prefetch/README.md) | Reads the next music track ahead and times every track load (music_prefetch.py). | 1 in Client |  |
| [`net_drain`](net_drain/README.md) | Time-boxes the network drain per frame and times each message handler (/profiler), so a backlog cannot freeze the window. | 4 in Client, protocol |  |
| [`options_saver`](options_saver/README.md) | Keeps options.txt saves off the render thread (options_saver.py). | 3 in Client |  |
| [`prefab_extras`](prefab_extras/README.md) | Station prefabs also keep docking/management permissions, storage caps and trade entries (prefab_extras.py). | 3 in Client | render_alias |
| [`pump_watch`](pump_watch/README.md) | Times pygame.event.get() and keeps SDL's joystick detection off the render loop (pump_watch.py). Tells click_diag about each batch of events when that mod is installed. | 3 in Client |  |
| [`range_rings`](range_rings/README.md) | Range rings around the ship: the weapon's reach and a configurable ring (range_rings.py). | 3 in Client | core |
| [`render_alias`](render_alias/README.md) | Names render_mixin as _render_mixin_module for the install blocks at the end of Client.py. | 1 in Client |  |
| [`scan_archive`](scan_archive/README.md) | The scan archive: the planet/station/bot dashboard (scan_dashboard.py, scan_planner.py, scan_stations.py, bot_activity.py, station_overview.py), the galaxy export and their web assets. A library: it reads the files the recorder mods write (through core's scan_data.py), so it works with whichever of scan_capture, colony_scan, station_logs and station_profile you installed; the galaxy map's VIEW SCANS button opens it. | (library) | core |
| [`scan_capture`](scan_capture/README.md) | Passively records scans, inventories, skills, stations and bot state from server messages into scan_data/ (scan_logger.py and the other loggers). | 18 in Client, protocol | core |
| [`ship_layouts`](ship_layouts/README.md) | SHIP LAYOUTS: save a ship's fit under a name and compare it with the ship you fly (ship_layouts_window.py). | 9 in Client | render_alias, core |
| [`sound_prefetch`](sound_prefetch/README.md) | Reads the weapon sound files ahead on a background thread (sound_prefetch.py). | 1 in Client | core |
| [`squad_hud`](squad_hud/README.md) | The squad / fighter roster HUD panel and its compact bar rows. | 10 in Client, render_mixin |  |
| [`station_auto_name`](station_auto_name/README.md) | AUTO NAME button on the station rename dialog (station_auto_name.py). | 4 in Client, render_mixin | core |
| [`station_gear_upgrade`](station_gear_upgrade/README.md) | UPGRADE GEAR and ADD 50 GAMMA / WORKERS buttons in the station prefab dialog (station_gear_upgrade.py, station_expansion.py). | 3 in Client | render_alias |
| [`station_logs`](station_logs/README.md) | Station Logs tab: auto-backfill of older pages and capture of the rows (station_log_capture.py). | 9 in station_logs_client | core |
| [`station_profile`](station_profile/README.md) | A snapshot of each player station when you undock (station_profile.py). | 2 in Client | core |
| [`target_info`](target_info/README.md) | Planet orbit period and a gate's sun distance and bearing in the target panel. | 6 in Client | core |
| [`ui_pages`](ui_pages/README.md) | ESC-menu pages: UI Panels (hide or show HUD panels), Data Capture (capture_settings_panel.py) and Mods (mods_page.py: which mods are loaded, and why one is not). | 40 in Client, render_mixin | core |
| [`zoom_floor`](zoom_floor/README.md) | Zoom out to 0.05 instead of 0.10 (and the things that scale with the zoom range). | 6 in Client, render_mixin |  |

STAR EMPIRE MOD LOADER
======================

Play Star Empire with mods. The game's own files are never changed: the mod loader adds the mods each time you start
the game. Take a mod out and the game is exactly as the developer made it.


TO PLAY
-------
1. Put the game's official launcher (StarEmpireLauncher.exe) in THIS folder. You do not need to run it yourself.
2. Put the mods you want in the "mods" folder (see GETTING MODS below).
3. Double-click "Launch Modded Client.bat".

That one file is all you ever run. Each time it:
  * runs the official launcher, which installs the game or brings it up to date, and closes the plain game the
    launcher opens;
  * checks that every mod still fits this version of the game. A mod that does not is switched off and named in a
    message; the other mods keep working, and the game always starts;
  * starts the game with your mods.

Already have the game installed? Put everything from this folder into your Star Empire folder (next to Client.exe), or
keep this folder where it is and write the path of your Star Empire folder in the file game_dir.txt.

Windows only (64-bit). Nothing else needs installing: Python and the libraries the game needs are in the "runtime"
folder and are used only by this launcher.


GETTING MODS
------------
Every mod is a folder in "mods_available" (39 of them, listed at the end of this file). The game uses the ones in
"mods". Either copy the folders you want from "mods_available" into "mods", or let modctl do it, which also copies the
mods each one needs:

    modctl.bat list                  every mod, what it does, what it needs, which are in use ([x])
    modctl.bat add combat_log        copy a mod into "mods", with the mods it needs
    modctl.bat add all               copy every mod
    modctl.bat remove combat_log     take it out again
If you copy by hand and forget a mod that another one needs, the game still starts and that mod is switched off. The
ESC menu -> Mods page shows what is loaded and why anything is switched off. "mods" starts empty, so the first start is
the plain game: add mods one or a few at a time.


IF SOMETHING GOES WRONG
-----------------------
* mods_report.txt (in this folder) says what the loader did at the last start; launch_log.txt says what the update step
  did.
* To start without running the official launcher first (no internet, or in a hurry):  set STAR_EMPIRE_NO_UPDATE=1
* To start with no mods at all:   set STAR_EMPIRE_NO_MODS=1   before launching, or empty the "mods" folder.
* To switch some off without deleting them:   set STAR_EMPIRE_DISABLE_MODS=combat_log,range_rings
* Mods write their settings (*.json) and recorded data (scan_data) into this folder, never into the game's.


WHAT IS IN THIS FOLDER
----------------------
  Launch Modded Client.bat   the one file to run
  modctl.bat                 picks mods (optional; see GETTING MODS)
  mods\                      the mods the game uses
  modloader\                 the loader, the launcher scripts, the mod manager, notes for mod authors
  runtime\                   Python and the libraries the game needs (see THIRD_PARTY_NOTICES.txt)


A NOTE
------
These are client modifications. Check the game's rules before using any of them; the author of this package is not
the game's developer and cannot speak for what it allows. Some mods record gameplay data to files on your computer
(the "Data Capture" page in the ESC menu switches each recorder off).


LICENSE
-------
The mod loader and the mods are MIT licensed (the file LICENSE). The game belongs to its developer and is not included.
The libraries in the "runtime" folder keep their own licenses (THIRD_PARTY_NOTICES.txt and the "licenses" folder).

THE MODS
--------
  asset_watch
      Times every image the window loads on demand (asset_watch.py).

  autopilot_eta
      The autopilot banner's 'ETA m:ss' (autopilot_eta.py, autopilot_route_sim.py).
      needs: render_alias

  chat_suggest
      Suggests system names (favourites first) while you type /ap in the chat (chat_suggest.py).

  click_diag
      Click diagnostics: one client-log line per left click saying what was in range (click_diag.py).
      needs: core

  colony_scan
      Captures colony station setups and shuttle routes for the scan archive (colony_scan_logger.py, shuttle_route_capture.py).
      needs: core

  combat_log
      The Combat Log window and floating damage numbers, with shooter attribution through the shield-effects matcher.
      needs: damage_types

  construction_diag
      Logs what the server sends about station construction (construction_diag.py).

  construction_eta
      Estimated time to complete for each Construction Bay project (construction_eta.py).
      needs: render_alias, core

  construction_tooltip
      What a selected Construction Bay blueprint costs against the station's stock (construction_tooltip.py).
      needs: render_alias, core

  core
      Shared helpers other mods import: the Data Capture switches (capture_settings), where scan_data/ lives (scan_paths), the off-thread file writer (scan_writer, scan_file_lock), the read side of every scan file (scan_data), the orbit figures (target_info), the move / resize / plate geometry of the HUD windows (window_geometry), the Construction Bay stock helpers (construction_stock) and which body a station is on (station_bodies). It changes nothing in the game by itself.

  crash_log_thread
      crash_log writes the client log from a background thread, so file I/O cannot stall the game.

  damage_types
      Imports the damage_types helpers that the combat log uses.

  freeze_watchdog
      Stack dump into the log when a thread stalls (freeze_watchdog.py); heartbeats from the main loop and the renderer.
      needs: core

  galaxy_map
      The galaxy map as a movable, resizable window (galaxy_map_window.py), the Survey view and the VIEW SCANS button (which needs the scan_archive mod and is left out without it).
      needs: render_alias, core

  gc_watch
      Logs garbage-collection pauses and freezes the heap after start-up (gc_watch.py).

  inventory_filter
      FILTER buttons and a tab on the ship inventories and station Storage (inventory_filter.py).
      needs: render_alias

  music_prefetch
      Reads the next music track ahead and times every track load (music_prefetch.py).

  net_drain
      Time-boxes the network drain per frame and times each message handler (/profiler), so a backlog cannot freeze the window.

  options_saver
      Keeps options.txt saves off the render thread (options_saver.py).

  prefab_extras
      Station prefabs also keep docking/management permissions, storage caps and trade entries (prefab_extras.py).
      needs: render_alias

  pump_watch
      Times pygame.event.get() and keeps SDL's joystick detection off the render loop (pump_watch.py). Tells click_diag about each batch of events when that mod is installed.

  range_rings
      Range rings around the ship: the weapon's reach and a configurable ring (range_rings.py).
      needs: core

  render_alias
      Names render_mixin as _render_mixin_module for the install blocks at the end of Client.py.

  scan_archive
      The scan archive: the planet/station/bot dashboard (scan_dashboard.py, scan_planner.py, scan_stations.py, bot_activity.py, station_overview.py), the galaxy export and their web assets. A library: it reads the files the recorder mods write (through core's scan_data.py), so it works with whichever of scan_capture, colony_scan, station_logs and station_profile you installed; the galaxy map's VIEW SCANS button opens it.
      needs: core

  scan_capture
      Passively records scans, inventories, skills, stations and bot state from server messages into scan_data/ (scan_logger.py and the other loggers).
      needs: core

  ship_layouts
      SHIP LAYOUTS: save a ship's fit under a name and compare it with the ship you fly (ship_layouts_window.py).
      needs: render_alias, core

  sound_prefetch
      Reads the weapon sound files ahead on a background thread (sound_prefetch.py).
      needs: core

  squad_hud
      The squad / fighter roster HUD panel and its compact bar rows.

  station_auto_name
      AUTO NAME button on the station rename dialog (station_auto_name.py).
      needs: core

  station_gear_upgrade
      UPGRADE GEAR and ADD 50 GAMMA / WORKERS buttons in the station prefab dialog (station_gear_upgrade.py, station_expansion.py).
      needs: render_alias

  station_logs
      Station Logs tab: auto-backfill of older pages and capture of the rows (station_log_capture.py).
      needs: core

  station_probe
      /stationprobe: ask for another station's logs, cargo and construction (station_probe.py).

  station_profile
      A snapshot of each player station when you undock (station_profile.py).
      needs: core

  target_info
      Planet orbit period and a gate's sun distance and bearing in the target panel.
      needs: core

  ui_pages
      ESC-menu pages: UI Panels (hide or show HUD panels), Data Capture (capture_settings_panel.py) and Mods (mods_page.py: which mods are loaded, and why one is not).
      needs: core

  wire_zlib3
      The zlib3 wire format and SPACE_COMBAT_BATCH decoding (wire_compression.py, combat_batch.py).

  zoom_floor
      Zoom out to 0.05 instead of 0.10 (and the things that scale with the zoom range).


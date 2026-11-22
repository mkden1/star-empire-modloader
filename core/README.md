# Core (shared helpers)

Code the other mods share. It changes nothing in the game by itself.

## What it does

- `capture_settings`: which data recorders are switched on (the Data Capture page's state).
- `scan_paths`, `scan_writer`, `scan_file_lock`: where `scan_data/` lives and the off-thread file writer the recorders use.
- `scan_data`: the read side of every scan file (planet scans, colony setups, shuttle routes, station names, logs and profiles, the bot fleet, trade bot slots), so the scan archive and the galaxy map can read them without the recorder mods.
- `target_info`: the planet orbit and gate/sun figures. `window_geometry`: the move, resize and plate drawing of the movable HUD windows. `construction_stock` and `station_bodies`: small helpers shared by the construction mods and the station mods.
- `relaunch`: starts the client again once this one has closed (a small detached helper waits for the old process to end). Used by `change_mods` and `log_out`.

## Using it

- Most other mods need it. `modctl add` copies it for you; copying by hand, copy this folder too.

## Needs

Nothing else.

Needed by: `change_mods`, `click_diag`, `colony_scan`, `construction_eta`, `construction_tooltip`, `freeze_watchdog`, `galaxy_map`, `log_out`, `range_rings`, `scan_archive`, `scan_capture`, `ship_layouts`, `sound_prefetch`, `station_auto_name`, `station_logs`, `station_profile`, `target_info`, `ui_pages`.

## Under the hood

- No patches of its own: it is code other mods use.
- Code: `capture_settings.py`, `construction_stock.py`, `relaunch.py`, `scan_data.py`, `scan_file_lock.py`, `scan_paths.py`, `scan_writer.py`, `station_bodies.py`, `target_info.py`, `window_geometry.py`.

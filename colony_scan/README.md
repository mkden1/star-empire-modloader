# Colony and shuttle capture

Records the live state of your colony stations and the wiring of your orbital shuttle networks.

## What it does

- When you view a colony station, its real numbers (population, food, growth, resource caps and stock, equipped modules, workers) are saved, so they can be compared with a plan. The client normally keeps only the most recently viewed station in memory.
- When you open a hub station's Shuttle tab, its routes (which stations it hauls to and from, and what) are saved too. Only stations whose tab you have opened are covered.

## Using it

- Nothing to do: it works in the background. Its switches are on the Data Capture page of the ESC menu (needs the `ui_pages` mod): "Colony & station state" and "Shuttle routes".

## Needs

`core`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

## Under the hood

- Patches the game's `Client.py` (6 hunks) (the loader applies them in memory; the game's files are never changed).
- Code: `colony_scan_logger.py`, `shuttle_route_capture.py`.
- Writes: `scan_data/colony_scan.jsonl` and `scan_data/shuttle_routes.csv`, the input of the scan archive's station views.

# Station logs capture

The Logs tab loads older pages by itself, and every row it shows is saved.

## What it does

- The client keeps only the visible page of a station's Logs tab in memory (at most 1000 rows, wiped when the search changes). This mod saves each row it receives, and makes the tab load older pages automatically, so one visit to a station's Logs tab captures its whole history.
- Each row is a free-text line such as a bot docking or a purchase, with a stable per-station id so it is saved once.

## Using it

- Open a station's Logs tab and let it page back.
- Its switch is on the Data Capture page of the ESC menu (needs the `ui_pages` mod), where it is called "Station logs".

## Needs

`core`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

## Under the hood

- Patches the game's `station_logs_client.py` (9 hunks) (the loader applies them in memory; the game's files are never changed).
- Code: `station_log_capture.py`.
- Writes: `scan_data/station_logs.csv`, the input of the scan archive's bot-activity statistics.

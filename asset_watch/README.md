# Asset watch

Logs every game picture that takes noticeably long to load, so slow-loading images can be found.

## What it does

- Ship, station, planet and drone pictures are large (about 2.7 MB each) and are read from disk and decoded the first time anything needs them, on whichever thread asked. For a ship that is the render thread, in the middle of drawing.
- This mod wraps that loader and writes a line to the client log for every load that took 30 ms or more: the file, its size and the thread.

## Using it

- Nothing to do: it works in the background. The lines appear in the client log (`star_empire_client.log`).

## Needs

Nothing else.

## Under the hood

- Patches the game's `Client.py` (1 hunk) (the loader applies them in memory; the game's files are never changed).
- Code: `asset_watch.py`.

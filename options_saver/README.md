# Background options saver

Saves `options.txt` on a background thread, so closing a window never freezes the game.

## What it does

- Every time you close a window, drop a dragged panel or flip a toggle, the game reads `options.txt` (to merge in the hotbars other running clients saved) and rewrites it, on the render thread. On a shared drive that read took 3 seconds once, and the whole client waited.
- With this mod the call is queued and a background thread makes the real call. Queued calls are merged (the last value of each setting wins), so nothing is lost.

## Using it

- Nothing to do: it works in the background.

## Needs

Nothing else.

## Under the hood

- Patches the game's `Client.py` (3 hunks) (the loader applies them in memory; the game's files are never changed).
- Code: `options_saver.py`.

# Music prefetch

Reads the next music track ahead and times every track load.

## What it does

- The music player starts each track by opening the file on the message thread. On a busy drive the first read of a 3-4 MB track held that thread, and through the GIL the render thread, for 1.2-2.5 seconds.
- This mod reads the next track ahead and logs every track load that was slow, so the file reads stay off the message thread.

## Using it

- Nothing to do: it works in the background.

## Needs

Nothing else.

## Under the hood

- Patches the game's `Client.py` (1 hunk) (the loader applies them in memory; the game's files are never changed).
- Code: `music_prefetch.py`.

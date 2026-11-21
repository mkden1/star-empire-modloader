# Weapon sound prefetch

Reads the weapon sound files ahead on a background thread.

## What it does

- Weapon sounds are loaded the first time they are heard, while the game holds the lock the render thread needs every frame. One cold read of a half-megabyte sound file froze the whole game for 5.4 seconds on a stalling drive.
- This mod warms those files on a background thread, so the first shot does not wait for the disk. It only reads.

## Using it

- Nothing to do: it works in the background. It has no switch (it is an optimisation, not a recorder): take the mod out with Choose Mods.bat to stop it.

## Needs

Nothing else.

## Under the hood

- Patches the game's `Client.py` (1 hunk) (the loader applies them in memory; the game's files are never changed).
- Code: `sound_prefetch.py`.

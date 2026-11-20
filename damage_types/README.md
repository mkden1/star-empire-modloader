# Damage types import

Makes the game's damage-type helpers available to the combat log.

## What it does

- The game has helpers for damage types. This mod only adds the import line in `Client.py` that `combat_log` uses. It does nothing visible on its own.

## Using it

- It is a dependency: the mod picker (or `modctl add combat_log`) copies it for you.

## Needs

Nothing else.

Needed by: `combat_log`.

## Under the hood

- Patches the game's `Client.py` (1 hunk) (the loader applies them in memory; the game's files are never changed).

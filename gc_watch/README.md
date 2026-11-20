# Garbage-collection watch

Logs garbage-collection pauses and freezes the heap shortly after start-up.

## What it does

- Each client holds 1.5-2.5 GB of Python objects, and a garbage collection stops every thread while it runs. A full one can take a good fraction of a second, which showed up as one-off hitches inside sections that normally take a millisecond.
- This mod times every collection and logs the slow ones with the generation, how much it freed and how many objects the process holds, so a freeze can be matched to a collection or ruled out.
- It also freezes the heap a while after start-up, so the objects created while loading are not walked again by later collections.

## Using it

- Nothing to do: it works in the background. Look for the lines in the client log (`star_empire_client.log`).

## Settings

- `STAR_EMPIRE_NO_GC_FREEZE=1` turns off the heap freeze.

## Needs

Nothing else.

## Under the hood

- Patches the game's `Client.py` (4 hunks) (the loader applies them in memory; the game's files are never changed).
- Code: `gc_watch.py`.

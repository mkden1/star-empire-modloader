# Network drain limit

Limits how long one frame may spend on a backlog of server messages, and times each message handler.

## What it does

- A burst of server messages (or a thread that was not scheduled for a while) used to be drained in one uninterruptible loop, holding the lock the render thread needs every frame. One frame measured live spent ten seconds on a backlog of about 13,000 messages, which is what made the window go "Not Responding".
- The drain now has a time budget per frame (8 ms) and spreads a backlog over many fast frames.
- While `/profiler` is running, each message handler is timed as well.

## Using it

- Nothing to do: it works in the background.

## Needs

Nothing else.

## Under the hood

- Patches the game's `Client.py` (3 hunks), `protocol.py` (1 hunk) (the loader applies them in memory; the game's files are never changed).

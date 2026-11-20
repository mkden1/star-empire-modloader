# Background log writer

Writes the client log from a background thread, so a slow drive cannot stall the game.

## What it does

- A normal log handler writes and flushes the file inside every logging call, on the calling thread. When the drive stalls for a second or two, the thread that logs stalls with it.
- With this mod the records go into a queue and a separate thread writes them. The crash hooks wait for the queue to drain, so a crash is never reported as logged while its line is still queued.
- Trade-off: a hard process kill (a native crash) can lose the last few queued lines.

## Using it

- Nothing to do: it works in the background.

## Needs

Nothing else.

## Under the hood

- Patches the game's `crash_log.py` (5 hunks) (the loader applies them in memory; the game's files are never changed).
- Code: `crash_log_thread.py`.

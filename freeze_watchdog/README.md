# Freeze watchdog

Writes a stack dump to the log when a thread stalls, so a freeze explains itself.

## What it does

- The network/message thread and the render thread each report in once per frame. If either goes quiet for the stall time, Python's `faulthandler` (a timer that needs neither thread nor the GIL) writes every thread's current stack to the log. The dump shows the exact line a stuck thread was on: a disk read, a lock wait, a message handler.
- When the thread reports in again a warning gives the time and how long it was stuck. Normal frames (about 17 ms) never get near the threshold.

## Using it

- Nothing to do: it works in the background. Look for the dumps in the client log (`star_empire_client.log`). Its on/off switch is on the Data Capture page of the ESC menu (needs the `ui_pages` mod), where it is called "Freeze watchdog".

## Needs

`core`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

## Under the hood

- Patches the game's `Client.py` (4 hunks) (the loader applies them in memory; the game's files are never changed).
- Code: `freeze_watchdog.py`.

# Construction diagnostics

Logs what the server really sends about station construction.

## What it does

- Records the Construction Bay messages in the client log: the field names of the message, of a queue entry and of a blueprint, the full text of each project the first time it appears, and the station's cargo.
- It was written to find out how the server works out a project's time, which `construction_eta` then models. You do not need it to use the other construction mods.

## Using it

- Nothing to do: it works in the background. Look for `construction_diag:` lines in the client log (`star_empire_client.log`).

## Settings

- `STAR_EMPIRE_NO_CONSTRUCTION_DIAG=1` turns it off.

## Needs

Nothing else.

## Under the hood

- Patches the game's `Client.py` (1 hunk) (the loader applies them in memory; the game's files are never changed).
- Code: `construction_diag.py`.

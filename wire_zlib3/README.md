# zlib3 wire format and combat batches

Speaks the extended `zlib3` wire format on the main connection and decodes `SPACE_COMBAT_BATCH` messages.

## What it does

- The main control connection announces `CAPS zlib3` (an extended-dictionary compression format the control server accepts), and the client decodes the server's batched `SPACE_COMBAT_BATCH` combat and state messages. The game's other connections keep the official `zlib2`.
- This changes the connection handshake. If the game ever fails to connect after an update, turn this mod off first (`STAR_EMPIRE_DISABLE_MODS=wire_zlib3`) to rule it out.

## Using it

- Nothing to do: it works in the background.

## Needs

Nothing else.

## Under the hood

- Patches the game's `arena_connection.py` (2 hunks), `protocol.py` (5 hunks), `wire_compression.py` (4 hunks) (the loader applies them in memory; the game's files are never changed).
- Code: `combat_batch.py`, `combat_batch_handler.py`.

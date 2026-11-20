# Event pump watch

Times `pygame.event.get()` and keeps SDL's joystick detection off the render loop.

## What it does

- Pygame pumps SDL's event queue without releasing Python's GIL, so a slow pump stalls every thread. This mod times each call and, when one was slow (0.25 s or more), logs what the pump returned and what was happening around it.
- The game has no gamepad support, but starting pygame also starts SDL's joystick subsystem, whose device detection runs inside that same event pump and blocked it for 0.4-0.9 seconds at times. This mod shuts the joystick subsystem down after start-up.
- If `click_diag` is installed it is told about each batch of events, so a swallowed click can be reported. It works without it.

## Using it

- Nothing to do: it works in the background.

## Settings

- `STAR_EMPIRE_KEEP_JOYSTICK=1` leaves the joystick subsystem running (if you do use a gamepad).

## Needs

Nothing else.

Works better with: `click_diag` (it is told about the events, so a swallowed click can be reported).

## Under the hood

- Patches the game's `Client.py` (3 hunks) (the loader applies them in memory; the game's files are never changed).
- Code: `pump_watch.py`.

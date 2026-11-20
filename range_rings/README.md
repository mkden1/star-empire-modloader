# Range rings

Range rings around your ship: your weapon's reach and one ring you set.

## What it does

- **Weapon ring**: the reach of the weapon you have selected, using the server's own effective range for it, so it follows plugins, skills and a weapon switch. With no range from the server there is no ring.
- **Custom ring**: a ring at a distance you set, in world units (how far to stay from a boss, a sensor's reach), or in `vis` mode your own effective visibility times a detection rating, which is exactly where a ship with that detection sees you.
- Display only: nothing here reads or sends anything beyond what the client already has.

## Using it

- `/rings` switches every ring on or off.
- `/ring` shows what is set.
- `/ring weapon [on|off]` the weapon ring (no argument: switch it).
- `/ring 25000 [label]` the custom ring at 25,000 units (`25k` works too), and switch it on.
- `/ring vis [2.5] [label]` the custom ring at your visibility times 2.5 (default 1).
- `/ring on|off` the custom ring on or off, keeping its setting.

## Settings

- `STAR_EMPIRE_NO_RANGE_RINGS=1` turns it off.

## Needs

`core`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

## Under the hood

- Patches the game's `Client.py` (3 hunks) (the loader applies them in memory; the game's files are never changed).
- Code: `range_rings.py`.
- Writes: `range_rings.json` next to the client: which rings are shown and the custom ring's distance.

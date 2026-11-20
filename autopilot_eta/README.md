# Autopilot ETA

Adds an estimated time remaining to the route autopilot's banner.

## What it does

- The banner normally reads "Autopiloting to Gamma, 2 jumps left". With this mod it reads "... · ETA 1:42".
- The estimate is a simulation. From your ship's real position, velocity and heading it flies the rest of the route on the game's own flight plan (`flight_plan.steer`, the steering the autopilot uses every frame), including the swing after each warp, the final stop inside the last gate, and about a quarter of a second per warp.
- It only displays an estimate. It never steers or changes what the autopilot does.

## Using it

- Start the route autopilot as usual (`/ap <system>`). The ETA appears in the banner.

## Needs

`render_alias`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

## Under the hood

- Patches the game's `Client.py` (2 hunks) (the loader applies them in memory; the game's files are never changed).
- Code: `autopilot_eta.py`, `autopilot_planner.py`, `autopilot_route_sim.py`.

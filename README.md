# Star Empire mod loader: development branch

A mod loader for the Star Empire client, the mods made with it, and the tools that build the folder players use. The
game's own files are **never edited**: each mod is a folder of patches and code, and `_internal/modloader.py` applies
the patches to the game's code in memory every time the client starts. A mod whose patch no longer fits a game update
is switched off on its own and the others keep working.

**The game is not in this repository.** Install it with the official launcher; the launcher and everything it downloads
are ignored by `.gitignore`.

## Three branches

| branch | what it is |
|---|---|
| `modloader` | The ready-to-run folder for players: the loader, ONE launch file, the mod manager and the bundled Python runtime. **Generated** from this branch by `custom_build\publish_branch.py`; never edited by hand. |
| `mods_available` | The mods: one folder each (patches, code, tests) and `README.md` with the patch format and the list. Players clone it as `mods_available` inside the ready-to-run folder and pick from it with the mod picker. |
| `dev` | This branch: the loader's source, its tests, and the build and publish tools. |

## Working here

1. Check out this branch, install the game with the official launcher into the same folder (it adds `Client.exe` and the
   game's files in `_internal/` beside ours), and get the mods as a second checkout of the same repository:
   `git worktree add mods mods_available`.
2. A Python 3.14 runtime in `custom_build/` (`python.exe`, its DLLs and `.pyd` files, `include/`, `libs/python314.lib`) is
   needed; it is not in git. `fix_runtime.py` (`Fix Runtime.bat`) repairs the game's native libraries for it after each
   launcher update but does not create the interpreter; that first setup is not scripted here.
3. `Launch Modded Client.bat` starts the modded client from this folder.

```
_internal/modloader.py        the loader (source of truth; the ready-to-run folder gets a copy)
_internal/moderngl_compat.py  the runtime shim for this Python's moderngl (also copied into the ready-to-run folder)
_internal/test_*.py           tests of the loader, of "every mod applies / works alone", of the updater and the builders
mods/                         the `mods_available` branch, checked out here (loader looks in mods/)
custom_build/package/         what the ready-to-run folder is made of: the launcher scripts, the mod picker window
                              (modpicker.py = the pygame window, modpicker_model.py = what it knows and does), modctl.py
                              (the same on a console), the README template
custom_build/build_package.py builds that folder from this checkout (and proves each mod works alone)
custom_build/publish_branch.py commits the built folder, without the mods, as the next commit on `modloader`
custom_build/run_client.py    starts the modded client from this folder (Launch Modded Client.bat)
custom_build/run_tests.py     runs the whole suite and compares the failures with the dev's own known ones
custom_build/fix_runtime.py   repairs the libraries of the runtime in custom_build/ (Fix Runtime.bat)
custom_build/exe_tools/       read-only tools for the code frozen inside Client.exe
custom_build/freeze_report.py summarises what the freeze_watchdog mod wrote to the client log
```

## The player's side

A player downloads the `modloader` branch, puts the official launcher in the folder, clones the `mods_available` branch into a
folder of the same name, and double-clicks `Launch Modded Client.bat`. The first time, a window (the mod picker, drawn with
the bundled pygame: no browser, no server) lists the mods with their READMEs and screenshots; they tick what they want and
press "Apply and play". After that the launch file first replaces any mod in `mods/` that differs from its copy in
`mods_available/` (an update: `modctl.refresh_at_launch`; the old copy is kept in `mods/.replaced`, mods the player added
themselves are never touched, `STAR_EMPIRE_NO_MOD_REFRESH=1` skips it), then runs the official launcher (installing or
updating the game and closing the plain client it opens), checks every mod against the game, and starts the game;
`Choose Mods.bat` reopens the window.
The picker groups mods by `"category"` and pre-ticks the `"recommended"` ones, both keys of each `mod.json`
(see `mods/README.md`, "The mod picker"). The update step is `custom_build/package/game_updater.py`; the player-facing
text is `custom_build/package/README.txt.tpl`.

## When the game updates (you, not the player)

The player does nothing: the launch file updates the game and switches off mods that no longer fit. To fix those mods:

1. Run the official launcher here, and close the plain client it opens.
2. `custom_build\python.exe -I _internal\modloader.py` lists the mods that no longer fit and the patch line that does not
   match.
3. Re-point those patches (`mods/README.md`, "When an update breaks a patch") and commit them on the `mods_available` branch.
4. `custom_build\python.exe custom_build\run_tests.py`. A failing dev test that also fails with `STAR_EMPIRE_NO_MODS=1`
   is the dev's own; add it to `KNOWN_UNRELATED_FAILURES` in that script with a note.
5. `custom_build\python.exe custom_build\publish_branch.py` if the loader, launcher or runtime changed.

## Tests

`custom_build\python.exe custom_build\run_tests.py` (the game's tests and ours), or `pytest _internal mods` from a shell
that has the runtime. The game's files must be in `_internal/` for them to run.

## License

MIT (`LICENSE`), the same in all three branches, for the loader, the mods, their tests and the build tools. It does not
cover the game, which belongs to its developer and is not in this repository, nor the patches' short anchor lines of the
game's own code, nor the libraries bundled in the ready-to-run folder, which keep their own licenses
(`THIRD_PARTY_NOTICES.txt` there). Each mod has its own `README.md` in the `mods_available` branch.

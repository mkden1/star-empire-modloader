# Mod updates

Checks GitHub for newer versions of the mods and of the launcher, and asks before updating, on the login screen.

## What it does

- A moment after the login screen opens, it quietly asks GitHub whether the mods or the launcher have changed. If they have, a bar appears at the top of the login screen: **Mod updates available: updated: quick_bars, ui_layouts   new: foo**, with the source it would come from and two buttons, **UPDATE MODS** and **LATER**. When the launcher has changed too, the title is "Updates available", the list ends with **launcher: modctl.py, ...** and the button reads **UPDATE**.
- **Nothing changes until you press the update button.** LATER hides the bar until the next start. With no network, or if the check fails for any reason, nothing is shown and the game starts as usual.
- Updating the mods refreshes the mod catalogue (`mods_available/`). The launcher's own start-up refresh then copies what changed into `mods/`, so when a mod you use is among the updated ones the client restarts at once to use it (after the restart the login screen's "Mods changed since your last start" note lists them); if only mods you do not use changed, there is no restart and the bar says so, and CHANGE MODS shows the new ones.
- An older launcher has no such start-up refresh, so the new versions would never reach `mods/`. When that is the case the update copies them into `mods/` itself (with the launcher's own `modctl update`) before it restarts. If that copy fails the bar says so and what to run (`modctl.bat update`).
- Updating the launcher swaps the files of the package's `modloader/` folder (the loader, the mod picker, `modctl`, the launch file ...) and always restarts, since they are only read at start. It is done first: if it fails, nothing is changed and the mods are not updated on top of an old launcher.
- A mod that was removed from the repository is left where it is (nothing is ever deleted).
- Works without git and without a GitHub account: the repository is public, so the files come over plain HTTPS. If your `mods_available/` is a git clone it uses git for the mods instead (fetch and a fast-forward, with every prompt switched off); a clone with local changes, or one that cannot fast-forward, is reported ("update it by hand with git pull") and never touched, and the launcher can still be updated from the same bar. A package that is itself a git clone is left to git for the launcher.
- A check is made at most every 20 minutes. Each one is a single request of about 600 bytes (the list of the repository's branches and their newest commits, the same thing `git clone` starts with) and the files are downloaded only when something is new. The mods and the launcher are checked independently: when one cannot be, the other still is.
- It deliberately does not use GitHub's REST API for the "what is new" question: that allows an address only 60 anonymous requests an hour, shared with everything else on the machine and with everyone behind the same router, and a check that hits the limit would fail on every start. The one REST call left is the launcher's file list, and only when the launcher's branch has moved. If GitHub says that budget is used up, the launcher is simply left alone until the time GitHub names (the mods are not affected, and nothing is shown), instead of asking again at every start.

## Why it asks first

A mod is Python code that runs inside the game, next to the saved logins, and the launcher is the code that runs the mods. So nothing is installed without your say-so, the bar says where it comes from (`github.com/<owner>/<repo>`, over https), and what is downloaded is treated as untrusted. The mods zip is capped in size, any path that could land outside the folder it is unpacked into is refused, only folders with a `mod.json` are taken, and each mod is swapped in beside the old one first and put back if anything fails (one generation of the old copy stays in `mods_available/.update_old/`). Launcher files are listed for that exact commit, only plain files with a plain name and a `.py`, `.bat` or `.md` extension are taken, each download must match the size and hash GitHub listed for it, every `.py` must compile before anything is touched, and the old files stay in `modloader/.update_old/`. Both downloads are that exact commit, never "the latest".

## Using it

- Start the game. If the bar appears, read what it says and press the update button or LATER.
- If a launcher update ever left the game unable to start, copy the files from `modloader/.update_old/` back over the ones in `modloader/`.

## Settings

- `STAR_EMPIRE_NO_MOD_UPDATES=1` turns the whole thing off.
- `STAR_EMPIRE_NO_LOADER_UPDATES=1` leaves the launcher alone (the mods are still checked).
- `STAR_EMPIRE_MOD_UPDATES_REPO=owner/repo` (or `owner/repo@branch`) follows another repository, e.g. a fork. `STAR_EMPIRE_LOADER_BRANCH=name` names the branch that holds the launcher (default `modloader`).
- `mod_updates.json` (next to the client) remembers the last versions seen, when they were checked and, if GitHub's request limit stopped a launcher check, when to try again. Delete it to check again at once.

## Needs

`core`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too. If `core` is older than this mod (it lacks `mod_digest.py` or `relaunch.py`, as happens when only this folder was copied) the mod switches itself off instead of stopping the game from starting, and the Mods page says why.

## Under the hood

- Patches the game's `Client.py` (1 hunk: it imports the module and installs the hooks right after `from login import LoginScreen`). The loader applies it in memory; the game's files are never changed.
- Code: `mod_updates.py` and, in `core`, `mod_digest.py` (a hash of what a mod folder holds that the game runs: not README, screenshots, tests, caches, file times or line endings, so a Windows git clone and GitHub's zip hash alike) and `relaunch.py` (the restart).
- HTTPS for the mods: `github.com/<repo>.git/info/refs?service=git-upload-pack` for every branch's newest commit (one request answers for the mods and the launcher alike), then `codeload.github.com/<repo>/zip/<sha>` for that commit. Only Python's standard library (`urllib`, `zipfile`), which the bundled runtime has. The requests carry a generic client name and nothing about you.
- HTTPS for the launcher: the `modloader` branch also holds the bundled Python runtime (tens of MB), so there is no zip. The same branch list gives that branch's newest commit, and when it is new `api.github.com/repos/<repo>/contents/modloader?ref=<sha>` lists the folder (name, size, git hash of each file) and only the files whose content differs from yours (line endings ignored) are fetched from `raw.githubusercontent.com/<repo>/<sha>/modloader/<name>`. Files that exist only on your side are left alone.
- What the bar lists for the mods is worked out by comparing, mod by mod, the digests of the zip's mods with the catalogue's, so a commit that only touched a README or a test does not ask anything.
- The check and the update run on background threads; the login window never waits for the network or the disk. The bar tells other mods how tall it is (`_top_banner_h`), so the "mods changed" note sits below it. Long lists are shortened to fewer names plus a count until the whole sentence fits.

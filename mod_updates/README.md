# Mod updates

Checks GitHub for newer versions of the mods and asks before updating, on the login screen.

## What it does

- A moment after the login screen opens, it quietly asks GitHub whether the mods have changed. If they have, a bar appears at the top of the login screen: **Mod updates available: updated: quick_bars, ui_layouts   new: foo**, with the source it would come from and two buttons, **UPDATE MODS** and **LATER**.
- **Nothing changes until you press UPDATE MODS.** LATER hides the bar until the next start. With no network, or if the check fails for any reason, nothing is shown and the game starts as usual.
- UPDATE MODS refreshes the mod catalogue (`mods_available/`). The launcher's own start-up refresh then copies what changed into `mods/`. If a mod you use is among the updated ones the client restarts at once to use it (after the restart the login screen's "Mods changed since your last start" note lists them); if only mods you do not use changed, there is no restart and the bar says so, and CHANGE MODS shows the new ones.
- A mod that was removed from the repository is left where it is (nothing is ever deleted).
- Works without git and without a GitHub account: the repository is public, so the files come over plain HTTPS. If your `mods_available/` is a git clone it uses git instead (fetch and a fast-forward, with every prompt switched off); a clone with local changes, or one that cannot fast-forward, is reported ("update it by hand with git pull") and never touched.
- A check is made at most every 20 minutes, and only a cheap "what is the newest commit" question each time (conditional on an ETag); the files are downloaded only when something is new.

## Why it asks first

A mod is Python code that runs inside the game, next to the saved logins. So nothing is installed without your say-so, the bar says where it comes from (`github.com/<owner>/<repo>`, over https), and the zip is treated as untrusted: it is capped in size, any path that could land outside the folder it is unpacked into is refused, only folders with a `mod.json` are taken, and each mod is swapped in beside the old one first and put back if anything fails (one generation of the old copy stays in `mods_available/.update_old/`). The download is that exact commit, never "the latest".

## Using it

- Start the game. If the bar appears, read what it says and press UPDATE MODS or LATER.

## Settings

- `STAR_EMPIRE_NO_MOD_UPDATES=1` turns the whole thing off.
- `STAR_EMPIRE_MOD_UPDATES_REPO=owner/repo` (or `owner/repo@branch`) follows another repository, e.g. a fork.
- `mod_updates.json` (next to the client) remembers the last version seen and when it was checked. Delete it to check again at once.

## Needs

`core`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.

## Under the hood

- Patches the game's `Client.py` (1 hunk: it imports the module and installs the hooks right after `from login import LoginScreen`). The loader applies it in memory; the game's files are never changed.
- Code: `mod_updates.py` and, in `core`, `mod_digest.py` (a hash of what a mod folder holds that the game runs: not README, screenshots, tests, caches, file times or line endings, so a Windows git clone and GitHub's zip hash alike) and `relaunch.py` (the restart).
- HTTPS: `api.github.com/repos/<repo>/commits/<branch>` with `Accept: application/vnd.github.sha` for the newest commit's SHA, then `codeload.github.com/<repo>/zip/<sha>` for that commit. Only Python's standard library (`urllib`, `zipfile`), which the bundled runtime has. The requests carry a generic client name and nothing about you.
- What the bar lists is worked out by comparing, mod by mod, the digests of the zip's mods with the catalogue's, so a commit that only touched a README or a test does not ask anything.
- The check and the update run on background threads; the login window never waits for the network or the disk. The bar tells other mods how tall it is (`_top_banner_h`), so the "mods changed" note sits below it.

STAR EMPIRE MOD LOADER
======================

Play Star Empire with mods. The game's own files are never changed: the mod loader adds the mods each time you start
the game. Take a mod out and the game is exactly as the developer made it.


TO PLAY
-------
1. Put the game's official launcher (StarEmpireLauncher.exe) in THIS folder. You do not need to run it yourself.
2. Put the mods you want in the "mods" folder (see GETTING MODS below).
3. Double-click "Launch Modded Client.bat".

That one file is all you ever run. Each time it:
  * runs the official launcher, which installs the game or brings it up to date, and closes the plain game the
    launcher opens;
  * checks that every mod still fits this version of the game. A mod that does not is switched off and named in a
    message; the other mods keep working, and the game always starts;
  * starts the game with your mods.

Already have the game installed? Put everything from this folder into your Star Empire folder (next to Client.exe), or
keep this folder where it is and write the path of your Star Empire folder in the file game_dir.txt.

Windows only (64-bit). Nothing else needs installing: Python and the libraries the game needs are in the "runtime"
folder and are used only by this launcher.


GETTING MODS
------------
{{BUNDLED}}
Every mod is a folder in "mods_available" ({{COUNT}} of them, listed at the end of this file). The game uses the ones in
"mods". Either copy the folders you want from "mods_available" into "mods", or let modctl do it, which also copies the
mods each one needs:

    modctl.bat list                  every mod, what it does, what it needs, which are in use ([x])
    modctl.bat add combat_log        copy a mod into "mods", with the mods it needs
    modctl.bat add all               copy every mod
    modctl.bat remove combat_log     take it out again
{{/BUNDLED}}
{{SEPARATE}}
The mods are in their own branch of this project, called "mods". Get it into a folder named "mods_available" here:

    git clone --branch mods --single-branch <the address of this project> mods_available

(or download that branch as a zip and unzip it into a folder called "mods_available"). Every mod is a folder in it.
The game uses the ones in "mods". Either copy the folders you want from "mods_available" into "mods", or let modctl do
it, which also copies the mods each one needs:

    modctl.bat list                  every mod, what it does, what it needs, which are in use ([x])
    modctl.bat add combat_log        copy a mod into "mods", with the mods it needs
    modctl.bat add all               copy every mod
    modctl.bat remove combat_log     take it out again
    modctl.bat update                after fetching newer mods: replace the copies in "mods" that changed
{{/SEPARATE}}
If you copy by hand and forget a mod that another one needs, the game still starts and that mod is switched off. The
ESC menu -> Mods page shows what is loaded and why anything is switched off. "mods" starts empty, so the first start is
the plain game: add mods one or a few at a time.


IF SOMETHING GOES WRONG
-----------------------
* mods_report.txt (in this folder) says what the loader did at the last start; launch_log.txt says what the update step
  did.
* To start without running the official launcher first (no internet, or in a hurry):  set STAR_EMPIRE_NO_UPDATE=1
* To start with no mods at all:   set STAR_EMPIRE_NO_MODS=1   before launching, or empty the "mods" folder.
* To switch some off without deleting them:   set STAR_EMPIRE_DISABLE_MODS=combat_log,range_rings
* Mods write their settings (*.json) and recorded data (scan_data) into this folder, never into the game's.


WHAT IS IN THIS FOLDER
----------------------
  Launch Modded Client.bat   the one file to run
  modctl.bat                 picks mods (optional; see GETTING MODS)
  mods\                      the mods the game uses
  modloader\                 the loader, the launcher scripts, the mod manager, notes for mod authors
  runtime\                   Python and the libraries the game needs (see THIRD_PARTY_NOTICES.txt)


A NOTE
------
These are client modifications. Check the game's rules before using any of them; the author of this package is not
the game's developer and cannot speak for what it allows. Some mods record gameplay data to files on your computer
(the "Data Capture" page in the ESC menu switches each recorder off).


LICENSE
-------
The mod loader and the mods are MIT licensed (the file LICENSE). The game belongs to its developer and is not included.
The libraries in the "runtime" folder keep their own licenses (THIRD_PARTY_NOTICES.txt and the "licenses" folder).
{{BUNDLED}}

THE MODS
--------
{{MODLIST}}
{{/BUNDLED}}

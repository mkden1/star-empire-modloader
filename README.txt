STAR EMPIRE MOD LOADER
======================

Play Star Empire with mods. The game's own files are never changed: the mod loader adds the mods each time you start
the game. Take a mod out and the game is exactly as the developer made it.


TO PLAY
-------
1. Put the game's official launcher (StarEmpireLauncher.exe) in THIS folder. You do not need to run it yourself.
2. Get the mods (see GETTING MODS below).
3. Double-click "Launch Modded Client.bat". The first time, a window opens where you tick the mods you want.

That one file is all you ever run. Each time it:
  * brings the mods you use up to date: a mod in "mods" that differs from its copy in "mods_available" (you updated
    that folder) is replaced by the newer one, and the mods a new version needs are added;
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
The mods are in their own branch of this project, called "mods_available". Clone it into a folder of the same name here:

    git clone --branch mods_available --single-branch <the address of this project> mods_available

(or download that branch as a zip and unzip it into a folder called "mods_available"). Every mod is a folder in it.

CHOOSING THEM: the first time you double-click "Launch Modded Client.bat" a window lists the mods. Click a mod to read what
it does (and see its pictures, where it has them), tick the ones you want, and press "Apply and play". "Recommended" ticks
the usual set. A mod that needs another one brings it along. Mods you leave unticked are not used.

To change your mods later, close the game and double-click "Choose Mods.bat".

To get newer versions of the mods, update the mods_available folder (git pull in it, or unzip a newer download over
it). The next time you launch, every mod you use that changed there is replaced in "mods" by the new version; the old
copy is kept in mods\.replaced (one version back) in case you want it. Mods you put in "mods" yourself, and mods you
edited by hand under a name that is not in mods_available, are never touched or removed. (A hand edit to a mod that IS
in mods_available is replaced: keep it in mods\.replaced, or set STAR_EMPIRE_NO_MOD_REFRESH=1.)

The game uses the folders in "mods". The window fills it for you; you can also copy folders from "mods_available" into
"mods" by hand (copy the ones a mod needs too, the window and the mod's README say which; if you forget one the game still
starts and that mod is switched off). The ESC menu -> Mods page shows what is loaded and why anything is switched off.
Mods of your own that you put in "mods" are left alone by the window.

If you prefer a console, modloader\modctl.bat does the same: "list", "add NAME", "add all", "remove NAME", "update".


IF SOMETHING GOES WRONG
-----------------------
* mods_report.txt (in this folder) says what the loader did at the last start; launch_log.txt says what the update step
  did (it also says which mods were refreshed from mods_available); picker_error.txt appears if the mod window could not open (then copy mod folders into "mods" by hand).
* To start without running the official launcher first (no internet, or in a hurry):  set STAR_EMPIRE_NO_UPDATE=1
* To keep the mods in "mods" exactly as they are (no refresh from mods_available):  set STAR_EMPIRE_NO_MOD_REFRESH=1
* To start with no mods at all:   set STAR_EMPIRE_NO_MODS=1   before launching, or press "None" and then "Apply" in Choose Mods.bat.
* To switch some off without deleting them:   set STAR_EMPIRE_DISABLE_MODS=combat_log,range_rings
* Mods write their settings (*.json) and recorded data (scan_data) into this folder, never into the game's.


WHAT IS IN THIS FOLDER
----------------------
  Launch Modded Client.bat   the one file to run
  Choose Mods.bat            the window for choosing mods (it also opens by itself the first time)
  mods\                      the mods the game uses
  mods_available\            every mod on offer, each with a README.md
  modloader\                 the loader, the launcher scripts, the mod picker, notes for mod authors
  runtime\                   Python and the libraries the game needs (see THIRD_PARTY_NOTICES.txt)


A NOTE
------
These are client modifications. Check the game's rules before using any of them; the author of this package is not
the game's developer and cannot speak for what it allows. Some mods record gameplay data to files on your computer
(the "Data Capture" page in the ESC menu switches each recorder off; none of them is ticked by "Recommended").


LICENSE
-------
The mod loader and the mods are MIT licensed (the file LICENSE). The game belongs to its developer and is not included.
The libraries in the "runtime" folder keep their own licenses (THIRD_PARTY_NOTICES.txt and the "licenses" folder).

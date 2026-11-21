"""Make the repository's ``modloader`` branch: the ready-to-run folder.

    custom_build\\python.exe custom_build\\publish_branch.py [--repo FOLDER] [--branch NAME] [--message TEXT]
                                                           [--no-verify] [--from-build FOLDER]

The ``modloader`` branch is what someone downloads to play with mods: the loader, the one launch file, the mod manager
and the Python runtime, ready to run. It holds no game (the official launcher installs that beside it) and no mods
(those are the ``mods`` branch, copied into mods/ as wanted). It is GENERATED, never edited by hand: this script builds
the package (build_package.py; every mod is built alone to prove it works), takes what a user gets of it, and commits
that as the next commit on the branch, without touching the branch checked out in the repository or its working tree.

Run it from the development checkout (it needs the runtime in custom_build/, the game in _internal/ and the mods/
checkout). ``--from-build`` publishes an already built folder instead of building one.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_package  # noqa: E402

# Not part of the branch: the mods come from their own branch, the marker is the build's, game_dir.txt is per machine.
LEFT_OUT_TOP = {"mods_available", ".built_by_build_package", "game_dir.txt"}

GITIGNORE = """\
# The ready-to-run folder. The game is installed into it by the official launcher, the mods are copied into mods/, and
# mods_available/ is the repository's "mods_available" branch: none of that is part of this branch.
/Client.exe
/version.txt
/prune_list.txt
/StarEmpireLauncher*.exe
/_internal/
/.update_staging/
/mods_available/
/mods/*
!/mods/README.txt
/game_dir.txt

# What the game and the loader write while you play: logs, per-machine settings, recorded data, caches.
*.log
/launch_log.txt
/mods_report.txt
/kept_logs/
/.mod_cache/
/scan_data/
/*.json
/*.json.*.tmp
"""

GITATTRIBUTES = """\
* text=auto
*.bat text eol=crlf
*.patch text eol=lf
"""


def branch_files(built: Path) -> "list[str]":
    """The files of a built package that go on the branch (relative, forward slashes): all but what LEFT_OUT_TOP names,
    and of mods/ only its README."""
    chosen = []
    for path in sorted(built.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(built).as_posix()
        top = rel.split("/")[0]
        if top in LEFT_OUT_TOP or "__pycache__" in path.parts:
            continue
        if top == "mods" and rel != "mods/README.txt":
            continue
        chosen.append(rel)
    return chosen


def git(repo: Path, *args, env=None, data=None, cwd=None) -> str:
    result = subprocess.run(["git", "--git-dir", str(repo / ".git"), *args], capture_output=True, input=data, env=env,
                            cwd=cwd)
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args[:2])} failed: {result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout.decode("utf-8", "replace").strip()


def commit_folder(repo: Path, built: Path, branch: str, message: str) -> "str | None":
    """Commit ``built``'s files (branch_files, plus .gitignore and .gitattributes) as the next commit on ``branch``. Uses a
    temporary index, so the repository's own index, working tree and checked out branch are not touched. Returns the new
    commit, or None when the branch already holds exactly this."""
    (built / ".gitignore").write_text(GITIGNORE, encoding="utf-8", newline="\n")
    (built / ".gitattributes").write_text(GITATTRIBUTES, encoding="utf-8", newline="\n")
    files = branch_files(built) + [".gitignore", ".gitattributes"]
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index"), "GIT_WORK_TREE": str(built)}
        git(repo, "add", "-f", "--pathspec-from-file=-", "--pathspec-file-nul", env=env, cwd=str(built),
            data="\0".join(files).encode("utf-8"))
        tree = git(repo, "write-tree", env=env)
    ref = f"refs/heads/{branch}"
    parent = subprocess.run(["git", "--git-dir", str(repo / ".git"), "rev-parse", "--verify", "--quiet", ref],
                            capture_output=True, text=True).stdout.strip()
    if parent and git(repo, "rev-parse", f"{parent}^{{tree}}") == tree:
        return None
    args = ["commit-tree", tree, "-m", message] + (["-p", parent] if parent else [])
    commit = git(repo, *args)
    git(repo, "update-ref", ref, commit)
    return commit


def main(argv) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default=str(build_package.REPO_ROOT))
    parser.add_argument("--branch", default="modloader")
    parser.add_argument("--message", default="")
    parser.add_argument("--no-verify", action="store_true")
    parser.add_argument("--from-build", default="")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()

    scratch = None
    if args.from_build:
        built = Path(args.from_build).resolve()
    else:
        scratch = Path(tempfile.mkdtemp(prefix="se_publish_"))
        built = scratch / "package"
        names = build_package.build_package(built, with_mods=True)
        build_package.log(f"built {len(names)} mods into {built}")
        if not args.no_verify:
            build_package.log("verifying (every mod picked alone) ...")
            problems = build_package.verify(built, names)
            for problem in problems:
                build_package.log("VERIFY: " + problem)
            if problems:
                shutil.rmtree(scratch, ignore_errors=True)
                print("not published: the build did not verify")
                return 1
    try:
        sha = git(repo, "rev-parse", "--short", "HEAD") if (repo / ".git").exists() else ""
        message = args.message or f"Ready-to-run folder, built from development commit {sha or 'unknown'}"
        commit = commit_folder(repo, built, args.branch, message)
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
    if commit is None:
        print(f"nothing to publish: {args.branch} already holds this")
    else:
        print(f"published {commit[:9]} on {args.branch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

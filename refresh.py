#!/usr/bin/env python3
"""Refresh prices and rebuild the site, optionally publishing it.

The scrape and the icons come from sources that only exist on this machine (the
Ascension client and the TSM SavedVariables), so a host like Vercel cannot
generate them.  The generated data is therefore committed to the repository and
the host only runs the Vite build.

    python refresh.py              # prices -> data -> site
    python refresh.py --push       # ... then commit and push, triggering a deploy
    python refresh.py --full       # also re-scrape the database and icons

Prices only change after you scan in game, ``/reload``, and let the sharing app
upload and download again.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

PYTHON = sys.executable
GENERATED = [
    "web/public/data.json",
    "web/public/history.json",
    "web/public/assets/icons",
    "web/public/assets/icons.json",
    "output/highrisk",
    "output/market",
    "output/tsm",
]


def run(command: list[str], cwd: Path | None = None) -> None:
    printable = " ".join(str(part) for part in command)
    print(f"\n$ {printable}")
    # On Windows `npm` and `git` are .cmd/.exe shims that subprocess will not
    # find without resolving them first.
    resolved = shutil.which(command[0]) or command[0]
    result = subprocess.run([resolved, *command[1:]], cwd=cwd)
    if result.returncode != 0:
        raise SystemExit(f"failed: {printable}")


def prune_stale_bundles(site: Path) -> int:
    """Delete hashed bundles from earlier builds.

    Vite runs with ``emptyOutDir: false`` because the icons and data files in
    that folder come from the Python side, which means each build leaves its
    predecessor's ``index-<hash>.js`` behind to be committed and deployed.
    """
    index = site / "index.html"
    if not index.exists():
        return 0
    html = index.read_text(encoding="utf-8")
    removed = 0
    for asset in (site / "assets").glob("index-*"):
        if asset.name not in html:
            asset.unlink()
            removed += 1
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--full", action="store_true",
                        help="re-scrape db.ascension.gg and re-extract icons")
    parser.add_argument("--push", action="store_true",
                        help="commit the generated data and push")
    parser.add_argument("--realm", default="Rexxar - Conquest of Azeroth")
    parser.add_argument("--message", default="Refresh High Risk data")
    args = parser.parse_args()

    root = Path(__file__).parent

    if args.full:
        run([PYTHON, "highrisk.py"], root)
        run([PYTHON, "icons.py"], root)

    run([PYTHON, "sync_prices.py", "--realm", args.realm], root)
    run([PYTHON, "profit.py", "--realm", args.realm], root)
    run([PYTHON, "tsm_export.py"], root)
    run([PYTHON, "build_data.py"], root)
    run([PYTHON, "history.py", "--realm", args.realm], root)
    run(["npm", "run", "build"], root / "web")
    stale = prune_stale_bundles(root / "output" / "site")
    if stale:
        print(f"\nPruned {stale} stale bundle(s) from earlier builds.")

    if args.push:
        if not (root / ".git").exists():
            raise SystemExit("Not a git repository; run `git init` and add a remote first.")
        run(["git", "add", *GENERATED], root)
        status = subprocess.run(["git", "status", "--porcelain", *GENERATED],
                                cwd=root, capture_output=True, text=True)
        if not status.stdout.strip():
            print("\nNothing changed; skipping commit.")
            return 0
        run(["git", "commit", "-m", args.message], root)
        run(["git", "push"], root)
        print("\nPushed. Vercel will redeploy from the committed data.")

    print("\nDone. Preview locally with:  python -m http.server -d output/site 8000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

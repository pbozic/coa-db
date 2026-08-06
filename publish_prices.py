#!/usr/bin/env python3
"""Publish a fresh price file without rebuilding or redeploying the site.

The catalog (items, recipes, icons, drop sources) changes when the game does.
Prices change every scan.  Keeping them in one file would mean a full rebuild
and redeploy every time you scan the auction house, so prices are published on
their own -- about 5 KB against the catalog's 188 KB.

Two targets, both free:

``branch`` (default)
    Commits ``prices.json`` to an orphan ``data`` branch and pushes it.  The
    site fetches it from raw.githubusercontent.com, which sends
    ``Access-Control-Allow-Origin: *``.  Vercel only builds the production
    branch, so this never triggers a deploy.

``local``
    Only regenerates the file, for when you serve the site yourself.

    python publish_prices.py                 # sync, rebuild prices, push
    python publish_prices.py --target local  # just regenerate
    python publish_prices.py --print-url     # show the URL to configure
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

PRICES = Path("web/public/prices.json")
DATA_BRANCH = "data"


# Run children without a console window: this is called from a scheduled task,
# and a window flashing up every fifteen minutes steals keyboard focus.
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if hasattr(
    subprocess, "CREATE_NO_WINDOW") else {}


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    resolved = shutil.which(command[0]) or command[0]
    return subprocess.run([resolved, *command[1:]], text=True, **NO_WINDOW, **kwargs)


def git(*args: str, check: bool = True, capture: bool = False) -> str:
    result = run(["git", *args], capture_output=capture)
    if check and result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed")
    return (result.stdout or "").strip()


def remote_url() -> str | None:
    result = run(["git", "remote", "get-url", "origin"], capture_output=True)
    return result.stdout.strip() if result.returncode == 0 else None


def raw_url(branch: str = DATA_BRANCH) -> str | None:
    """Work out the raw.githubusercontent.com URL for the published file."""
    url = remote_url()
    if not url:
        return None
    slug = url.removesuffix(".git")
    for prefix in ("git@github.com:", "https://github.com/", "ssh://git@github.com/"):
        if slug.startswith(prefix):
            slug = slug[len(prefix):]
            break
    else:
        return None
    return f"https://raw.githubusercontent.com/{slug}/{branch}/prices.json"


def run_binary(command: list[str], payload: bytes) -> str:
    """Feed git plumbing on stdin without newline translation.

    Text mode would rewrite every ``\\n`` as ``\\r\\n`` on Windows, and
    ``git mktree`` reads its input line by line -- so the stray carriage return
    ends up as part of the filename, producing a file literally called
    ``prices.json\\r`` that no URL can reach.
    """
    resolved = shutil.which(command[0]) or command[0]
    result = subprocess.run([resolved, *command[1:]], input=payload,
                            capture_output=True, **NO_WINDOW)
    if result.returncode != 0:
        raise SystemExit(f"{' '.join(command)} failed: "
                         f"{result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout.decode("utf-8").strip()


def publish_to_branch(branch: str) -> None:
    """Put prices.json alone on an orphan branch, leaving the worktree alone."""
    payload = PRICES.read_bytes()

    # Build the commit with plumbing so the working tree is never touched and
    # no branch checkout is needed.
    blob = run_binary(["git", "hash-object", "-w", "--stdin"], payload)
    tree = run_binary(["git", "mktree"],
                      f"100644 blob {blob}\tprices.json\n".encode())

    parent = run(["git", "rev-parse", f"refs/heads/{branch}"], capture_output=True)
    args = ["git", "commit-tree", tree, "-m", "Update prices"]
    if parent.returncode == 0:
        args += ["-p", parent.stdout.strip()]
    result = run(args, capture_output=True)
    if result.returncode != 0:
        raise SystemExit("could not create prices commit")
    commit = result.stdout.strip()

    git("update-ref", f"refs/heads/{branch}", commit)
    git("push", "-f", "origin", f"{branch}:{branch}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", choices=("branch", "local"), default="branch")
    parser.add_argument("--branch", default=DATA_BRANCH)
    parser.add_argument("--realm", default="Rexxar - Conquest of Azeroth")
    parser.add_argument("--sharing-cache", type=Path,
                        help="update_times.json to read prices from")
    parser.add_argument("--skip-sync", action="store_true",
                        help="use the price store as-is instead of re-reading TSM")
    parser.add_argument("--print-url", action="store_true",
                        help="print the URL to set as VITE_PRICES_URL and exit")
    args = parser.parse_args()

    if args.print_url:
        url = raw_url(args.branch)
        print(url or "No GitHub origin remote found; set VITE_PRICES_URL by hand.")
        return 0 if url else 1

    python = sys.executable
    if not args.skip_sync:
        sync = [python, "sync_prices.py", "--realm", args.realm]
        if args.sharing_cache:
            sync += ["--sharing-cache", str(args.sharing_cache)]
        if run(sync).returncode:
            return 1
        if run([python, "profit.py", "--realm", args.realm]).returncode:
            return 1
    if run([python, "build_data.py"]).returncode:
        return 1

    payload = json.loads(PRICES.read_text(encoding="utf-8"))
    scan = payload.get("scan") or {}
    print(f"\n{len(payload.get('items', {}))} priced items · "
          f"{scan.get('realm', '?')} · scanned {scan.get('scanned_at', '?')}")

    if args.target == "local":
        print(f"Wrote {PRICES}. Serve it next to the site, or copy it to output/site/.")
        shutil.copy(PRICES, Path("output/site/prices.json"))
        return 0

    if not Path(".git").exists():
        raise SystemExit("Not a git repository; run `git init` and add a remote first.")
    publish_to_branch(args.branch)
    url = raw_url(args.branch)
    print(f"Pushed to the '{args.branch}' branch. No site rebuild was triggered.")
    if url:
        print(f"Set VITE_PRICES_URL={url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

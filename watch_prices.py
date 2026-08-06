#!/usr/bin/env python3
"""Watch for new auction scans and publish prices automatically.

Two things feed prices, and they arrive at different times:

* **Your own scan.** TSM flushes SavedVariables on ``/reload``, so scanning and
  reloading makes your scan available immediately -- even mid-session.
* **Everyone else's scans.** The `Ascension TSM Data Sharing App
  <https://github.com/Seminko/Ascension-TSM-Data-Sharing-App>`_ pools scans from
  every player, but it skips both upload and download while ``Ascension.exe`` is
  running, because the game rewrites the WTF folder underneath it. Pooled data
  therefore lands between play sessions.

This polls both, cheaply -- it reads only the ``lastCompleteScan`` timestamps,
not the megabyte of encoded scan data -- and runs the publish pipeline whenever
either moves forward.

    python watch_prices.py                 # poll every 5 minutes
    python watch_prices.py --once          # single check, for Task Scheduler
    python watch_prices.py --target local  # rebuild without pushing
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import tsm_scan

STATE = Path("output/market/.last_published")
LOG_FILE: Path | None = None
SCAN_RE = re.compile(r'\["([^"]+ - [^"]+)"\]\s*=\s*\{.*?\["lastCompleteScan"\]\s*=\s*(\d+)', re.S)


def latest_scan(realm: str, sharing_cache: Path | None = None) -> tuple[int, str]:
    """Newest lastCompleteScan for a realm, and where it came from.

    Only the timestamps are read; decoding every scan just to poll would burn a
    second of CPU each time for nothing.
    """
    best, origin = 0, "none"

    for path in tsm_scan.find_sharing_cache(sharing_cache):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for entry in data.get("latest_data", []):
            if entry.get("realm") == realm:
                stamp = int(entry.get("last_complete_scan") or 0)
                if stamp > best:
                    best, origin = stamp, "sharing app"

    for path in tsm_scan.find_auctiondb(None):
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name, stamp in SCAN_RE.findall(text):
            if name == realm and int(stamp) > best:
                best, origin = int(stamp), f"wtf:{path.parent.parent.name}"
    return best, origin


def say(message: str) -> None:
    """Print, and append to the log file when running unattended."""
    print(message, flush=True)
    if LOG_FILE:
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with LOG_FILE.open("a", encoding="utf-8") as handle:
                handle.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {message}\n")
        except OSError:
            pass


def read_state() -> int:
    try:
        return int(STATE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def write_state(stamp: int) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(str(stamp), encoding="utf-8")


def stamp_text(value: int) -> str:
    return dt.datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M") if value else "never"


def publish(realm: str, target: str, sharing_cache: Path | None) -> bool:
    command = [sys.executable, "publish_prices.py", "--realm", realm, "--target", target]
    if sharing_cache:
        command += ["--sharing-cache", str(sharing_cache)]
    return subprocess.run(command).returncode == 0


def check(realm: str, target: str, force: bool, sharing_cache: Path | None = None) -> bool:
    """One poll. Returns True when something was published."""
    newest, origin = latest_scan(realm, sharing_cache)
    published = read_state()
    now = dt.datetime.now().strftime("%H:%M:%S")

    if not newest:
        say(f"[{now}] no scan data found for {realm!r}")
        return False
    if newest <= published and not force:
        age = (time.time() - newest) / 3600
        say(f"[{now}] no new scan (newest {stamp_text(newest)}, {age:.1f}h old)")
        return False

    say(f"[{now}] new scan from {origin}: {stamp_text(newest)} "
          f"(was {stamp_text(published)}) - publishing")
    if not publish(realm, target, sharing_cache):
        say(f"[{now}] publish failed; will retry next poll")
        return False
    write_state(newest)
    say(f"[{now}] published")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--realm", default="Rexxar - Conquest of Azeroth")
    parser.add_argument("--target", choices=("branch", "local"), default="branch")
    parser.add_argument("--interval", type=float, default=300.0, help="seconds between polls")
    parser.add_argument("--once", action="store_true", help="check once and exit")
    parser.add_argument("--force", action="store_true", help="publish even if unchanged")
    parser.add_argument("--log", type=Path,
                        help="append activity to this file, for unattended runs")
    parser.add_argument("--sharing-cache", type=Path,
                        help="update_times.json to watch, e.g. on a machine that "
                             "runs the sharing app but never runs Ascension")
    args = parser.parse_args()

    global LOG_FILE
    LOG_FILE = args.log

    if args.once:
        check(args.realm, args.target, args.force, args.sharing_cache)
        return 0

    print(f"Watching {args.realm!r} every {args.interval:.0f}s. Ctrl+C to stop.")
    print("Your own scans appear after /reload; pooled scans arrive once "
          "Ascension is closed.\n")
    force = args.force
    try:
        while True:
            check(args.realm, args.target, force, args.sharing_cache)
            force = False
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

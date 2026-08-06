#!/usr/bin/env python3
"""Merge every available TSM price source into one persistent store.

Reads both places the pooled Ascension TSM data lands on this machine:

* the sharing app's own cache (``update_times.json``), which can be read at any
  time, whether or not Ascension is running; and
* ``TradeSkillMaster_AuctionDB.lua`` in each WTF account folder.

Prices are merged into ``output/market/price_db.json`` and kept, so an item that
one scan happens to miss does not lose the price an earlier scan recorded. Each
entry keeps the scan timestamp it came from, so staleness stays visible instead
of being averaged away.

    python sync_prices.py                     # merge and report
    python sync_prices.py --show 1303711      # what do we know about an item
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path

import coadata
import tsm_scan

DEFAULT_REALM = "Rexxar - Conquest of Azeroth"
DEFAULT_STORE = Path("output/market/price_db.json")


def load_store(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"realm": None, "updated": None, "items": {}}


def merge(store: dict, scan: tsm_scan.RealmScan, origin: str,
          authoritative: bool = False) -> tuple[int, int]:
    """Fold a scan into the store. Returns (added, refreshed).

    ``authoritative`` marks the sharing app's pooled download, which aggregates
    scans from many players and so wins ties against this client's own WTF copy.
    """
    items = store.setdefault("items", {})
    added = refreshed = 0
    for item_id, price in scan.prices.items():
        key = str(item_id)
        existing = items.get(key)
        stamp = price.last_scan or scan.last_complete_scan or 0
        if existing is None:
            added += 1
        elif stamp < (existing.get("last_scan") or 0):
            continue                      # we already hold something newer
        elif stamp == (existing.get("last_scan") or 0) and not authoritative:
            continue                      # same age, and this source does not win ties
        else:
            refreshed += 1
        items[key] = {
            "market_value": price.market_value,
            "min_buyout": price.min_buyout,
            "quantity": price.quantity,
            "last_scan": stamp,
            "origin": origin,
        }
    return added, refreshed


def report_by_name(names: list[str], sharing_cache: Path | None,
                   root: Path | None) -> None:
    """Answer "is this listed?" honestly, per realm, with the scan time.

    A scan is a snapshot: absent means that scan did not see it, which is not
    the same as never sold.
    """
    import aowow

    client = aowow.Client(Path("cache"), delay=0.6)
    scans: list[tuple[str, tsm_scan.RealmScan]] = []
    for path in tsm_scan.find_sharing_cache(sharing_cache):
        for scan in tsm_scan.read_sharing_cache(path):
            scans.append(("sharing-app", scan))
    for path in tsm_scan.find_auctiondb(root):
        for scan in tsm_scan.read(path):
            scans.append((path.parent.parent.name, scan))

    for name in names:
        print(f"\n=== {name}")
        try:
            view = aowow.fetch_listview(client, f"?items&filter=na={name}", template="item")
        except Exception as exc:
            print(f"  lookup failed: {exc}")
            continue
        rows = [r for r in (view.rows if view else [])
                if aowow.strip_name_prefix(r["name"])[0].lower() == name.lower()]
        if not rows:
            rows = (view.rows if view else [])[:5]
        if not rows:
            print("  no such item on db.ascension.gg")
            continue
        for row in rows:
            item_id = int(row["id"])
            label = aowow.strip_name_prefix(row["name"])[0]
            print(f"  {label} ({item_id})")
            for origin, scan in scans:
                price = scan.prices.get(item_id)
                state = ("not in this scan" if not price else
                         f"min {coadata.format_money(price.min_buyout)}, "
                         f"qty {price.quantity}")
                print(f"    {scan.realm:<32} {scan.scanned_at}  {state}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--realm", default=DEFAULT_REALM)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--sharing-cache", type=Path, help="update_times.json")
    parser.add_argument("--root", type=Path, help="Ascension client directory")
    parser.add_argument("--no-wtf", action="store_true",
                        help="use only the sharing app's pooled data")
    parser.add_argument("--show", type=int, action="append", default=[],
                        help="print what is known about an item ID")
    parser.add_argument("--find", action="append", default=[],
                        help="look up an item by name across every realm in the "
                             "pooled data; may be repeated")
    args = parser.parse_args()

    store = load_store(args.store)
    if store.get("realm") and store["realm"] != args.realm:
        print(f"Store holds {store['realm']!r}; starting a new one for {args.realm!r}")
        store = {"realm": args.realm, "updated": None, "items": {}}
    store["realm"] = args.realm

    before = len(store.get("items", {}))
    sources = 0

    # The sharing app's pooled download comes first and wins ties: it aggregates
    # scans from every player, whereas the WTF copy is only what this client saw.
    for path in tsm_scan.find_sharing_cache(args.sharing_cache):
        for scan in tsm_scan.read_sharing_cache(path, args.realm):
            added, refreshed = merge(store, scan, "sharing-app", authoritative=True)
            sources += 1
            print(f"sharing app  {path.name:<20} {scan.realm}: {len(scan.prices)} items, "
                  f"scanned {scan.scanned_at}  (+{added} new, {refreshed} refreshed)")

    if args.no_wtf:
        print("Skipping WTF files; using the sharing app's pooled data only.")
    for path in [] if args.no_wtf else tsm_scan.find_auctiondb(args.root):
        for scan in tsm_scan.read(path, args.realm):
            # Labelled without the account name: this file is regenerable and
            # may end up in a repository, and the account name is personal.
            added, refreshed = merge(store, scan, "wtf")
            sources += 1
            print(f"wtf file     {path.parent.parent.name:<20} {scan.realm}: {len(scan.prices)} items, "
                  f"scanned {scan.scanned_at}  (+{added} new, {refreshed} refreshed)")

    if not sources:
        print("No TSM price sources found.")
        return 1

    store["updated"] = int(time.time())
    args.store.parent.mkdir(parents=True, exist_ok=True)
    args.store.write_text(json.dumps(store, indent=1), encoding="utf-8")

    items = store["items"]
    newest = max((v.get("last_scan") or 0) for v in items.values()) if items else 0
    print(f"\n{len(items)} items in {args.store} ({len(items) - before:+d} this run)")
    if newest:
        age_hours = (time.time() - newest) / 3600
        print(f"newest price timestamp: {dt.datetime.fromtimestamp(newest):%Y-%m-%d %H:%M} "
              f"({age_hours:.1f} hours ago)")
        # A rewritten SavedVariables file does not mean a fresh scan: TSM only
        # updates lastCompleteScan when you actually scan the auction house.
        if age_hours > 6:
            print("\n  ! No auction scan in the last few hours, so prices are that old and")
            print("    items listed since will be missing. To refresh:")
            print("      1. scan the auction house in game (TSM > Shopping, or a full scan)")
            print("      2. /reload  so TSM flushes SavedVariables to disk")
            print("      3. re-run this script")

    if args.find:
        report_by_name(args.find, args.sharing_cache, args.root)

    catalog = coadata.load()
    for item_id in args.show:
        entry = items.get(str(item_id))
        name = catalog.name(item_id)
        if not entry:
            print(f"\n{name} ({item_id}): no price recorded in any scan")
            continue
        print(f"\n{name} ({item_id}): "
              f"min buyout {coadata.format_money(entry['min_buyout'])}, "
              f"market {coadata.format_money(entry['market_value'])}, "
              f"qty {entry['quantity']}, "
              f"seen {dt.datetime.fromtimestamp(entry['last_scan']):%Y-%m-%d %H:%M} "
              f"via {entry['origin']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

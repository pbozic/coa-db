#!/usr/bin/env python3
"""Accumulate a price history for the catalog items.

Two sources, because one alone is not enough:

* **TSM's own 14-day series.** Every scan carries a day-keyed market value going
  back a fortnight, so a trend exists immediately rather than after two weeks of
  collecting. It is one point per day.
* **Our own snapshots.** The publisher runs every 15 minutes, which is far finer
  than TSM's daily granularity and catches intraday swings the daily figure
  averages away.

Recent detail matters more than old detail, so raw snapshots are kept for a few
days and older ones collapse to one point per day. Without that the file grows
by ~96 points per item per day and is unusable within a week.

    python history.py            # fold the current scan into the history
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path

import coadata
import tsm_scan

STORE = Path("output/market/history.json")
EPOCH = dt.date(1970, 1, 1)
RAW_WINDOW_DAYS = 3          # keep every snapshot this recent
KEEP_DAYS = 30               # discard anything older than this


def day_to_timestamp(day: int) -> int:
    return int(dt.datetime.combine(EPOCH + dt.timedelta(days=day),
                                   dt.time(12, 0)).timestamp())


def load(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"items": {}}


def compact(points: list[list], now: float) -> list[list]:
    """Thin old points to one a day, keeping recent ones at full resolution."""
    raw_cutoff = now - RAW_WINDOW_DAYS * 86400
    old_cutoff = now - KEEP_DAYS * 86400

    recent = [p for p in points if p[0] >= raw_cutoff]
    by_day: dict[int, list] = {}
    for point in points:
        if point[0] < old_cutoff or point[0] >= raw_cutoff:
            continue
        # Last reading of each day wins; it is the one closest to the boundary.
        by_day[int(point[0] // 86400)] = point

    merged = sorted([*by_day.values(), *recent], key=lambda p: p[0])
    # Guard against duplicate timestamps from repeated runs.
    seen: dict[int, list] = {}
    for point in merged:
        seen[point[0]] = point
    return sorted(seen.values(), key=lambda p: p[0])


def update(catalog: coadata.Catalog, realm: str, store_path: Path,
           sharing_cache: Path | None = None) -> dict:
    wanted = {n["node_id"] for n in catalog.all_nodes() if n["kind"] == "item"}
    wanted |= {s["sale_item_id"] for s in catalog.seeds if s.get("sale_item_id")}

    scans: list[tsm_scan.RealmScan] = []
    for path in tsm_scan.find_sharing_cache(sharing_cache):
        scans += tsm_scan.read_sharing_cache(path, realm)
    for path in tsm_scan.find_auctiondb(None):
        scans += tsm_scan.read(path, realm)
    if not scans:
        raise SystemExit("No TSM scan data found.")

    best = max(scans, key=lambda s: s.last_complete_scan or 0)
    store = load(store_path)
    items = store.setdefault("items", {})
    now = time.time()
    added = seeded = 0

    for item_id in sorted(wanted):
        price = best.prices.get(item_id)
        if not price:
            continue
        key = str(item_id)
        points = items.get(key, [])
        known = {p[0] for p in points}

        # Seed TSM's daily series once; it backfills a fortnight of trend.
        for day, value in sorted(price.history.items()):
            stamp = day_to_timestamp(day)
            if stamp not in known and value:
                points.append([stamp, value, None])
                known.add(stamp)
                seeded += 1

        stamp = price.last_scan or best.last_complete_scan or int(now)
        if stamp not in known:
            points.append([int(stamp), price.market_value, price.min_buyout])
            added += 1

        items[key] = compact(points, now)

    store["updated"] = int(now)
    store["realm"] = realm
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(store, separators=(",", ":")), encoding="utf-8")

    total = sum(len(v) for v in items.values())
    print(f"{len(items)} items, {total} points "
          f"(+{added} from this scan, +{seeded} backfilled from TSM's daily series)")
    print(f"Wrote {store_path} ({store_path.stat().st_size / 1024:.0f} KB)")
    return store


def export(store: dict, path: Path) -> None:
    """Trim to what the browser plots, and write it next to the site."""
    payload = {
        "updated": store.get("updated"),
        "realm": store.get("realm"),
        "items": {k: v for k, v in store["items"].items() if len(v) >= 2},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {path} ({path.stat().st_size / 1024:.0f} KB, "
          f"{len(payload['items'])} items with a trend)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--realm", default="Rexxar - Conquest of Azeroth")
    parser.add_argument("--store", type=Path, default=STORE)
    parser.add_argument("--output", type=Path, default=Path("web/public/history.json"))
    parser.add_argument("--sharing-cache", type=Path)
    args = parser.parse_args()

    catalog = coadata.load()
    store = update(catalog, args.realm, args.store, args.sharing_cache)
    export(store, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

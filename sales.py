#!/usr/bin/env python3
"""Read your own sale history out of TSM Accounting.

Retail TSM shows a sale rate because it crowdsources completed sales and reads
Blizzard's commodity API. Ascension has neither, and TSM3's AuctionDB carries
only market value, lowest buyout, current quantity and a 14-day price history --
no sold-per-day figure exists anywhere in it.

What does exist is TSM Accounting, which logs every auction *you* complete.  That
is a smaller sample than a crowdsourced rate, but it is real evidence of what
sells: ``csvSales`` records what went, ``csvExpired`` what came back unsold, and
the ratio between them is a genuine sell-through rate for your own postings.

    python sales.py                 # summarise per item
    python sales.py --days 14       # only recent history
    python sales.py --json out.json # machine-readable, for build_data.py
"""
from __future__ import annotations

import argparse
import json
import re
import time
from collections import defaultdict
from pathlib import Path

import tsm_scan

ITEM_ID_RE = re.compile(r"item:(\d+)")
TABLES = ("csvSales", "csvExpired", "csvCancelled", "csvBuys")


def accounting_files(root: Path | None = None) -> list[Path]:
    roots = [root] if root else tsm_scan.DEFAULT_ROOTS
    found: list[Path] = []
    for base in roots:
        if base and base.exists():
            found.extend(sorted(base.glob(
                "WTF/Account/*/SavedVariables/TradeSkillMaster_Accounting.lua")))
    return found


def extract_lua_string(text: str, key: str) -> str | None:
    """Pull one long quoted Lua string out without executing anything."""
    marker = f'["{key}"] = "'
    start = text.find(marker)
    if start < 0:
        return None
    i = start + len(marker)
    out: list[str] = []
    while i < len(text):
        char = text[i]
        if char == "\\":
            out.append(text[i:i + 2])
            i += 2
            continue
        if char == '"':
            break
        out.append(char)
        i += 1
    return "".join(out)


def parse_table(blob: str) -> list[dict]:
    rows = [r for r in blob.replace("\\n", "\n").split("\n") if r.strip()]
    if len(rows) < 2:
        return []
    header = rows[0].split(",")
    parsed = []
    for row in rows[1:]:
        values = row.split(",")
        if len(values) != len(header):
            continue
        parsed.append(dict(zip(header, values)))
    return parsed


def summarise(root: Path | None = None, days: float | None = None) -> dict[int, dict]:
    """Per-item totals across every account on this machine."""
    cutoff = time.time() - days * 86400 if days else 0
    stats: dict[int, dict] = defaultdict(
        lambda: {"sold": 0, "expired": 0, "cancelled": 0, "bought": 0,
                 "revenue": 0, "name": ""})

    for path in accounting_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        for table in TABLES:
            blob = extract_lua_string(text, table)
            if not blob:
                continue
            field = {"csvSales": "sold", "csvExpired": "expired",
                     "csvCancelled": "cancelled", "csvBuys": "bought"}[table]
            for row in parse_table(blob):
                match = ITEM_ID_RE.search(row.get("itemString", ""))
                if not match:
                    continue
                try:
                    when = float(row.get("time", 0))
                    quantity = int(row.get("quantity", 0))
                except ValueError:
                    continue
                if when < cutoff:
                    continue
                entry = stats[int(match.group(1))]
                entry[field] += quantity
                entry["name"] = entry["name"] or row.get("itemName", "")
                if table == "csvSales":
                    try:
                        entry["revenue"] += int(float(row.get("price", 0))) * quantity
                    except ValueError:
                        pass

    for entry in stats.values():
        listed = entry["sold"] + entry["expired"]
        entry["sell_through"] = round(entry["sold"] / listed, 3) if listed else None
        entry["avg_price"] = round(entry["revenue"] / entry["sold"]) if entry["sold"] else None
    return dict(stats)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, help="Ascension client directory")
    parser.add_argument("--days", type=float, help="only count the last N days")
    parser.add_argument("--json", type=Path, help="write the summary as JSON")
    args = parser.parse_args()

    files = accounting_files(args.root)
    if not files:
        print("No TradeSkillMaster_Accounting.lua found.")
        return 1
    print(f"Reading {len(files)} accounting file(s)")

    stats = summarise(args.root, args.days)
    traded = {i: s for i, s in stats.items() if s["sold"] or s["expired"]}

    if not traded:
        print("\nNo completed auctions recorded yet.")
        print("TSM logs them as you sell, so this fills in once you trade through it.")
        print("Until then, listing depth (quantity on the auction house) is the only")
        print("liquidity signal available -- there is no crowdsourced sale rate for")
        print("Ascension the way retail TSM has one.")
    else:
        print(f"\n{len(traded)} items with completed auctions\n")
        print(f"{'item':<34} {'sold':>6} {'expired':>8} {'sell-through':>13} {'avg price':>12}")
        print("-" * 78)
        for item_id, entry in sorted(traded.items(), key=lambda kv: -kv[1]["sold"])[:25]:
            rate = entry["sell_through"]
            print(f"{(entry['name'] or item_id):<34} {entry['sold']:>6} {entry['expired']:>8} "
                  f"{f'{rate:.0%}' if rate is not None else '-':>13} "
                  f"{entry['avg_price'] or '-':>12}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(stats, indent=1), encoding="utf-8")
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

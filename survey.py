#!/usr/bin/env python3
"""Reconnaissance pass: how many rows does each relevant listing hold, and
which ones exceed the server-side 1000-row cap?"""
from __future__ import annotations

import logging
from pathlib import Path

import aowow

TARGETS = [
    # label,                       url
    ("Consumable (all)",           "?items=0"),
    ("  Consumables (0.0)",        "?items=0.0"),
    ("  Potions (0.1)",            "?items=0.1"),
    ("  Elixirs (0.2)",            "?items=0.2"),
    ("  Flasks (0.3)",             "?items=0.3"),
    ("  Scrolls (0.4)",            "?items=0.4"),
    ("  Food & Drink (0.5)",       "?items=0.5"),
    ("  Item Enh. perm (0.6)",     "?items=0.6"),
    ("  Bandages (0.7)",           "?items=0.7"),
    ("  Other (0.8)",              "?items=0.8"),
    ("Item Enh. temporary",        "?items=0.-3"),
    ("Enchanting spells",          "?spells=11.333"),
    ("Cooking spells",             "?spells=9.185"),
    ("First Aid spells",           "?spells=9.129"),
    ("Alchemy spells",             "?spells=11.171"),
    ("Enchantments (spell cat)",   "?spells=-4"),
]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    client = aowow.Client(Path("cache"), delay=1.0)

    print(f"{'listing':<28} {'url':<22} {'rows':>6} {'total':>7}  status")
    print("-" * 78)
    for label, url in TARGETS:
        try:
            views = aowow.extract_listviews(client.get(url), client.canonical(url))
        except Exception as exc:
            print(f"{label:<28} {url:<22} {'ERR':>6} {'':>7}  {exc}")
            continue

        data_views = [v for v in views if v.rows or v.total]
        if not data_views:
            print(f"{label:<28} {url:<22} {0:>6} {'':>7}  no listview with data")
            continue
        for view in data_views:
            status = "complete" if view.complete else "TRUNCATED"
            extra = f" [{view.template}/{view.lv_id}]" if len(data_views) > 1 else ""
            print(
                f"{label:<28} {url:<22} {len(view.rows):>6} "
                f"{view.total if view.total is not None else '?':>7}  {status}{extra}"
            )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate the CoAFarm addon's data file.

A 3.3.5 addon cannot make HTTP requests, so everything it knows has to arrive as
a file on disk. Two places are possible and only one is safe:

* ``SavedVariables`` -- **not** used. WoW rewrites that folder on logout and
  reload, so anything written there while the game runs is destroyed. This is
  the same trap the TSM data-sharing app works around by refusing to write while
  Ascension is running.
* ``Interface/AddOns/CoAFarm/Data.lua`` -- used. WoW only ever reads it, so an
  external tool can rewrite it at any moment, and ``/reload`` picks the new data
  up without restarting the game.

    python export_addon.py                       # write into ./addon
    python export_addon.py --install             # also copy into the live client
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
import urllib.request
from pathlib import Path

import coadata

CLIENT_ADDONS = Path("C:/Games/Ascension/Launcher/resources/ascension-live/Interface/AddOns")
ADDON_NAME = "CoAFarm"
PUBLISHED = "https://raw.githubusercontent.com/pbozic/coa-db/data"


def fetch_published(name: str) -> dict | None:
    """Pull a published data file, so the addon can be refreshed from anywhere.

    The catalog still comes from this checkout; only prices and history move
    often enough to be worth downloading.
    """
    try:
        request = urllib.request.Request(f"{PUBLISHED}/{name}",
                                         headers={"User-Agent": "coa-db addon export"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"  ! could not fetch {name}: {exc}")
        return None


def lua_string(value: str) -> str:
    """Quote a Lua string, escaping what Lua's parser cares about."""
    escaped = (value.replace("\\", "\\\\").replace('"', '\\"')
               .replace("\n", "\\n").replace("\r", ""))
    return f'"{escaped}"'


def lua_value(value) -> str:
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(round(value, 2) if isinstance(value, float) else value)
    if isinstance(value, str):
        return lua_string(value)
    if isinstance(value, (list, tuple)):
        return "{" + ",".join(lua_value(v) for v in value) + "}"
    if isinstance(value, dict):
        parts = []
        for key, val in value.items():
            if val is None or val == [] or val == {}:
                continue
            slot = f"[{key}]" if isinstance(key, int) else key
            parts.append(f"{slot}={lua_value(val)}")
        return "{" + ",".join(parts) + "}"
    raise TypeError(f"cannot serialise {type(value)}")


def build(catalog: coadata.Catalog, prices: dict, history: dict) -> dict:
    """Flatten the catalog into the shape the addon indexes at load."""
    price_items = (prices or {}).get("items", {})
    turnover = (history or {}).get("turnover", {})

    items = {}
    for node in catalog.all_nodes():
        if node["kind"] != "item":
            continue
        item_id = node["node_id"]
        price = price_items.get(str(item_id)) or {}
        best_zone = (node.get("zone_sources") or [None])[0]

        entry = {
            "name": node.get("name") or f"Item {item_id}",
            "q": node.get("quality"),
            "buy": price.get("buy"),
            "sell": price.get("sell"),
            "qty": price.get("quantity"),
        }
        if best_zone:
            entry["farm"] = {
                "zone": best_zone["zone"],
                "npc": best_zone.get("npc"),
                "pct": best_zone.get("percent"),
                "lvl": best_zone.get("level"),
                "xy": best_zone.get("spawn"),
            }
        if node.get("gathered_from"):
            entry["gather"] = min(g.get("skill") or 0 for g in node["gathered_from"])
        move = turnover.get(str(item_id))
        if move:
            entry["moves"] = move.get("perDay")
        items[item_id] = entry

    # An enchant is sold as its scroll, and that scroll is not a node in the
    # graph. Without an entry here the addon cannot price it, so every enchant
    # would silently show no profit at all.
    for seed in catalog.seeds:
        sale_id = seed.get("sale_item_id")
        if not sale_id or sale_id in items:
            continue
        price = price_items.get(str(sale_id)) or {}
        items[sale_id] = {
            "name": seed.get("sale_item_name") or seed.get("name"),
            "buy": price.get("buy"),
            "sell": price.get("sell"),
            "qty": price.get("quantity"),
        }

    # Recipes are keyed by product so the addon can answer both directions:
    # "what does this make" and "what goes into this".
    recipes = {}
    for node in catalog.all_nodes():
        craft = node.get("craft")
        if not craft or not craft.get("reagents"):
            continue
        sale_id = node.get("sale_item_id") or (
            node["node_id"] if node["kind"] == "item" else None)
        recipes[node["node_id"]] = {
            "name": node.get("name"),
            "kind": node["kind"],
            "family": node.get("family"),
            "prof": craft.get("profession"),
            "skill": craft.get("learned_at"),
            "yield": craft.get("yield_min", 1),
            "station": craft.get("station"),
            "sale": sale_id,
            "seed": bool(node.get("is_seed")),
            "reagents": [[r[0], r[1]] for r in craft["reagents"]],
        }

    return {
        "generated": int(time.time()),
        "realm": (prices or {}).get("scan", {}).get("realm"),
        "scanned": (prices or {}).get("scan", {}).get("scanned_at"),
        "cut": (prices or {}).get("cut", 0.05),
        "items": items,
        "recipes": recipes,
    }


def write_lua(payload: dict, path: Path) -> None:
    lines = [
        "-- Generated by export_addon.py. Do not edit by hand.",
        "-- Rewrite this file and /reload in game to pick up new prices.",
        f"-- realm {payload.get('realm')}  scanned {payload.get('scanned')}",
        "",
        "CoAFarmData = {",
        f"  generated = {payload['generated']},",
        f"  realm = {lua_value(payload.get('realm'))},",
        f"  scanned = {lua_value(payload.get('scanned'))},",
        f"  cut = {payload['cut']},",
        "  items = {",
    ]
    for item_id, entry in sorted(payload["items"].items()):
        lines.append(f"    [{item_id}]={lua_value(entry)},")
    lines += ["  },", "  recipes = {"]
    for product, entry in sorted(payload["recipes"].items()):
        lines.append(f"    [{product}]={lua_value(entry)},")
    lines += ["  },", "}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=Path("addon") / ADDON_NAME)
    parser.add_argument("--install", action="store_true",
                        help="copy the addon into the Ascension client")
    parser.add_argument("--addons", type=Path, default=CLIENT_ADDONS)
    parser.add_argument("--fetch", action="store_true",
                        help="download the published prices rather than using local ones")
    parser.add_argument("--publish", type=Path,
                        help="also write the payload as JSON, for the sync tool to fetch")
    args = parser.parse_args()

    catalog = coadata.load()
    prices = history = None
    if args.fetch:
        print(f"Fetching published data from {PUBLISHED}")
        prices = fetch_published("prices.json")
        history = fetch_published("history.json")
    if prices is None:
        prices = json.loads(Path("web/public/prices.json").read_text(encoding="utf-8")) \
            if Path("web/public/prices.json").exists() else {}
    if history is None:
        history = json.loads(Path("web/public/history.json").read_text(encoding="utf-8")) \
            if Path("web/public/history.json").exists() else {}

    payload = build(catalog, prices, history)
    args.source.mkdir(parents=True, exist_ok=True)
    target = args.source / "Data.lua"
    write_lua(payload, target)

    if args.publish:
        args.publish.parent.mkdir(parents=True, exist_ok=True)
        args.publish.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        print(f"Wrote {args.publish} "
              f"({args.publish.stat().st_size / 1024:.0f} KB) for the sync tool")

    size = target.stat().st_size / 1024
    farmable = sum(1 for i in payload["items"].values() if i.get("farm"))
    print(f"Wrote {target} ({size:.0f} KB)")
    print(f"  {len(payload['items'])} items, {len(payload['recipes'])} recipes, "
          f"{farmable} with a farm waypoint")

    if args.install:
        if not args.addons.exists():
            print(f"  ! {args.addons} not found; skipping install")
            return 1
        destination = args.addons / ADDON_NAME
        destination.mkdir(parents=True, exist_ok=True)
        for file in args.source.iterdir():
            if file.is_file():
                shutil.copy(file, destination / file.name)
        print(f"Installed to {destination} - /reload in game to pick it up")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

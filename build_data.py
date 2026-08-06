#!/usr/bin/env python3
"""Export the catalog as JSON for the React browser in ``web/``.

The browser recomputes costs itself so the "I will farm this" checkboxes can
update profit live, which means this file ships the whole graph rather than
pre-computed totals: reagents, yields, prices, drop sources and which materials
are farmable.

    python build_data.py            # -> web/public/data.json
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import coadata

WORLD_SOURCES = ("drop", "gathering", "container", "object", "quest objective",
                 "quest reward", "fishing", "disenchanting", "vendor")


def load_icon_manifest(assets: Path) -> dict[int, str]:
    manifest = assets / "icons.json"
    if not manifest.exists():
        return {}
    return {int(k): v for k, v in json.loads(manifest.read_text(encoding="utf-8")).items()}


def farm_zones(node: dict, limit: int = 3) -> list[str]:
    """Best zones to farm this in, already scored by rate against mob level."""
    return [z["zone"] for z in (node.get("zone_sources") or [])[:limit]]


def build(catalog: coadata.Catalog, profit: dict | None,
          icons: dict[int, str]) -> dict:
    prices = (profit or {}).get("prices", {})

    def price_of(item_id: int | None) -> dict | None:
        if item_id is None:
            return None
        entry = prices.get(str(item_id)) or prices.get(item_id)
        if not entry:
            return None
        return {"buy": entry.get("buy"), "sell": entry.get("sell"),
                "market": entry.get("market"), "minBuyout": entry.get("minBuyout"),
                "quantity": entry.get("quantity")}

    items = []
    for node in catalog.all_nodes():
        node_id = node["node_id"]
        craft = node.get("craft")
        sources = node.get("obtained_from") or []

        items.append({
            "id": node_id,
            "kind": node["kind"],
            "key": f"{node['kind']}:{node_id}",
            "name": node.get("name") or f"Item {node_id}",
            "quality": node.get("quality"),
            "icon": icons.get(node_id),
            "family": node.get("family") or "material",
            "seed": bool(node.get("is_seed")),
            "custom": bool(node.get("is_custom")),
            "missing": bool(node.get("missing_from_db")),
            "effect": (node.get("effect") or "").strip(),
            "sources": sources,
            "farmable": any(s in WORLD_SOURCES for s in sources),
            "zones": farm_zones(node),
            "zoneSources": (node.get("zone_sources") or [])[:6],
            "gatheredFrom": (node.get("gathered_from") or [])[:8],
            "containedIn": (node.get("contained_in") or [])[:4],
            "drops": (node.get("drops") or [])[:12],
            "saleItemId": node.get("sale_item_id") or (
                node_id if node["kind"] == "item" else None),
            "saleItemName": node.get("sale_item_name"),
            # An enchant sells as its scroll, and that scroll is not itself a
            # node in the graph, so its price is carried here rather than being
            # looked up by id in the browser.
            "salePrice": (price_of(node.get("sale_item_id")) or {}).get("sell"),
            # Depth of the thing you actually list, which for an enchant is its
            # scroll rather than the spell itself.
            "saleQuantity": (price_of(node.get("sale_item_id")) or {}).get("quantity"),
            "price": price_of(node_id),
            "craft": None if not craft else {
                "spellId": craft["spell_id"],
                "kind": craft.get("craft_kind"),
                "profession": craft.get("profession"),
                "learnedAt": craft.get("learned_at"),
                "yield": craft.get("yield_min", 1),
                "station": craft.get("station"),
                "method": craft.get("method"),
                "recipeItem": craft.get("recipe_item_name"),
                # Only a custom item's own recipe is walked into; Blizzard-era
                # materials are leaves, matching the scraper's rule.
                "expandable": bool(craft.get("reagents") and node.get("is_custom")),
                "reagents": [{"id": r[0], "qty": r[1]} for r in craft["reagents"]],
            },
            "usedIn": [u["node_id"] for u in catalog.used_in(node_id)],
        })

    items.sort(key=lambda e: (e["family"] != "flask", e["family"] != "enchant",
                              e["family"] != "food", e["name"]))
    return {
        "meta": {
            "scope": catalog.scope,
            "iconDir": "assets/icons",
            "cut": (profit or {}).get("cut", 0.05),
            "buySource": (profit or {}).get("buy_source"),
            "outlierFloor": (profit or {}).get("outlier_floor"),
            "sellSource": (profit or {}).get("sell_source"),
            "scan": (profit or {}).get("scan"),
            "counts": {
                "total": len(items),
                "flask": sum(i["family"] == "flask" for i in items),
                "food": sum(i["family"] == "food" for i in items),
                "enchant": sum(i["family"] == "enchant" for i in items),
                "material": sum(i["family"] == "material" for i in items),
            },
        },
        "items": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nodes", type=Path, default=Path("output/highrisk/nodes.json"))
    parser.add_argument("--profit", type=Path, default=Path("output/market/profit.json"))
    parser.add_argument("--assets", type=Path, default=Path("web/public/assets"))
    parser.add_argument("--output", type=Path, default=Path("web/public/data.json"))
    parser.add_argument("--prices-output", type=Path,
                        default=Path("web/public/prices.json"))
    args = parser.parse_args()

    catalog = coadata.load(args.nodes)
    profit = json.loads(args.profit.read_text(encoding="utf-8")) if args.profit.exists() else None
    icons = load_icon_manifest(args.assets)

    payload = build(catalog, profit, icons)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    # Prices are also written on their own. The catalog changes when the game
    # does; prices change every scan, and splitting them means a price refresh
    # is a small file swap rather than a site rebuild.
    #
    # With no profit data -- a catalog-only refresh in CI, which has no access
    # to TSM SavedVariables -- the existing price file is left alone rather than
    # being overwritten with an empty one.
    if not profit:
        print(f"  no profit data; leaving {args.prices_output} untouched")
        return 0
    price_only = {
        "generated": int(time.time()),
        "scan": payload["meta"].get("scan"),
        "buySource": payload["meta"].get("buySource"),
        "sellSource": payload["meta"].get("sellSource"),
        "items": {
            str(item["id"]): {k: item["price"][k] for k in
                              ("buy", "sell", "market", "minBuyout", "quantity")}
            for item in payload["items"] if item.get("price")
        },
    }
    for item in payload["items"]:
        sale_id = item.get("saleItemId")
        if item.get("salePrice") is not None and str(sale_id) not in price_only["items"]:
            price_only["items"][str(sale_id)] = {"buy": None, "sell": item["salePrice"]}
    args.prices_output.parent.mkdir(parents=True, exist_ok=True)
    args.prices_output.write_text(json.dumps(price_only, ensure_ascii=False),
                                  encoding="utf-8")
    print(f"Wrote {args.prices_output} "
          f"({args.prices_output.stat().st_size / 1024:.0f} KB, "
          f"{len(price_only['items'])} priced items)")

    have_icons = sum(1 for i in payload["items"] if i["icon"])
    priced = sum(1 for i in payload["items"] if i.get("price", {}) and i["price"]["buy"])
    size = args.output.stat().st_size / 1024
    print(f"Wrote {args.output} ({size:.0f} KB)")
    print(f"  {payload['meta']['counts']['total']} items, {have_icons} with icons, "
          f"{priced} with an auction price")
    if not profit:
        print("  ! no profit.json - run profit.py for prices")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

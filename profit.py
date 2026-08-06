#!/usr/bin/env python3
"""Rank the High Risk crafts by profit, given auction prices.

Why this exists: a TSM scan gives you prices, but not the cost of these crafts.
The High Risk recipes are absent from CraftingDB until learned and opened, and
the station conversions (Blightroot -> Blightroot Extract at the Sanguine
Workbench) are not TSM crafts at all, so TSM's ``crafting`` price source cannot
resolve them.  This applies the scraped material tree to scanned prices.

Typical use::

    python profit.py --template            # writes a price sheet to fill in
    python profit.py --prices output/market/prices.csv

Prices may be written as ``153g20s``, ``12.5g`` or a bare copper number.
A material with no price is reported as unknown; it is never assumed to be free.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import coadata
import tsm_scan

AUCTION_CUT = 0.05          # TSM/Blizzard AH cut on a successful sale


@dataclass
class Costing:
    """Result of pricing one product."""

    unit_cost: float | None = None          # cost to obtain one unit
    made: bool = False                      # cheaper (or only possible) to craft
    unknown: set[int] = field(default_factory=set)


class Model:
    """Prices materials and products.

    Buying and selling are priced separately: you acquire a material at whatever
    the cheapest listing is, but to actually shift a product you generally have
    to meet the lowest buyout rather than the smoothed market value.
    """

    def __init__(self, catalog: coadata.Catalog, buy: dict[int, int],
                 sell: dict[int, int] | None = None,
                 cut: float = AUCTION_CUT,
                 free: set[int] | None = None) -> None:
        self.catalog = catalog
        self.prices = buy
        self.sell_prices = sell if sell is not None else buy
        self.cut = cut
        # Materials counted as costing nothing because you farm them. Most High
        # Risk drops are never listed on the auction house, so requiring a price
        # for them would leave every recipe unpriced.
        self.free = free or set()
        self._cache: dict[int, Costing] = {}

    def unit_cost(self, item_id: int, path: frozenset[int] = frozenset()) -> Costing:
        """Cheapest way to obtain one unit: buy it, or make it."""
        if item_id in self._cache:
            return self._cache[item_id]
        if item_id in path:                                  # cyclic recipe guard
            return Costing(unit_cost=self.prices.get(item_id))

        node = self.catalog.item(item_id)
        buy = self.prices.get(item_id)
        if buy is None and item_id in self.free:
            buy = 0
        result = Costing(unit_cost=float(buy) if buy is not None else None)

        if self.catalog.expandable(node):
            craft = node["craft"]
            total = 0.0
            unknown: set[int] = set()
            for reagent_id, qty in craft["reagents"]:
                sub = self.unit_cost(reagent_id, path | {item_id})
                unknown |= sub.unknown
                if sub.unit_cost is None:
                    unknown.add(reagent_id)
                else:
                    total += sub.unit_cost * qty
            if not unknown:
                made_cost = total / max(1, craft["yield_min"])
                if result.unit_cost is None or made_cost < result.unit_cost:
                    result = Costing(unit_cost=made_cost, made=True)
            else:
                result.unknown |= unknown

        if result.unit_cost is None:
            result.unknown.add(item_id)
        self._cache[item_id] = result
        return result

    def evaluate(self, seed: dict) -> dict:
        """Cost, revenue and profit for one execution of a seed recipe."""
        craft = seed.get("craft") or {}
        reagents = craft.get("reagents", [])
        yield_n = max(1, craft.get("yield_min", 1))

        total = 0.0
        unknown: set[int] = set()
        breakdown = []
        for reagent_id, qty in reagents:
            sub = self.unit_cost(reagent_id)
            unknown |= sub.unknown
            line_cost = None if sub.unit_cost is None else sub.unit_cost * qty
            if line_cost is None:
                unknown.add(reagent_id)
            else:
                total += line_cost
            breakdown.append({
                "item_id": reagent_id,
                "name": self.catalog.name(reagent_id),
                "quantity": qty,
                "unit_cost": sub.unit_cost,
                "line_cost": line_cost,
                "sourced": "crafted" if sub.made else "bought",
            })

        # Flasks and foods are sold as themselves; an enchant is sold as its
        # scroll, resolved during scraping.
        sale_item = seed.get("sale_item_id")
        if sale_item is None:
            sale_item = seed["node_id"] if seed["kind"] == "item" else craft.get("recipe_item_id")
        sale_price = self.sell_prices.get(sale_item) if sale_item else None
        farmed = sorted(i for i in self.free
                        if any(r[0] == i for r in reagents) or i in
                        self.catalog.raw_materials(seed))

        cost = None if unknown else total
        revenue = None if sale_price is None else sale_price * yield_n * (1 - self.cut)
        profit = None if (cost is None or revenue is None) else revenue - cost
        roi = None if (profit is None or not cost) else profit / cost * 100

        return {
            "family": seed.get("family"),
            "product_id": seed["node_id"],
            "product": seed["name"],
            "kind": seed["kind"],
            "sale_item_id": sale_item,
            "profession": craft.get("profession"),
            "learned_at": craft.get("learned_at"),
            "yield": yield_n,
            "unit_sale_price": sale_price,
            "material_cost_per_craft": cost,
            "cost_per_unit": None if cost is None else cost / yield_n,
            "revenue_after_cut": revenue,
            "profit_per_craft": profit,
            "profit_per_unit": None if profit is None else profit / yield_n,
            "roi_percent": roi,
            "unknown_materials": sorted(unknown),
            "unknown_names": [self.catalog.name(i) for i in sorted(unknown)],
            "farmed_materials": [self.catalog.name(i) for i in farmed],
            "breakdown": breakdown,
        }


# --- price input ------------------------------------------------------------

def write_template(catalog: coadata.Catalog, path: Path) -> int:
    """Emit a price sheet listing every item the model needs a price for."""
    needed: dict[int, str] = {}

    for seed in catalog.seeds:
        craft = seed.get("craft") or {}
        sale_item = seed["node_id"] if seed["kind"] == "item" else craft.get("recipe_item_id")
        if sale_item:
            needed[sale_item] = "sell"
        for mat_id in catalog.raw_materials(seed):
            needed.setdefault(mat_id, "buy")
        for reagent_id, _ in craft.get("reagents", []):
            needed.setdefault(reagent_id, "buy")

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["item_id", "name", "role", "price", "note"])
        for item_id, role in sorted(needed.items(), key=lambda kv: (kv[1], catalog.name(kv[0]))):
            node = catalog.item(item_id)
            writer.writerow([
                item_id, catalog.name(item_id), role, "",
                ", ".join(node.get("obtained_from") or []) if node else "",
            ])
    return len(needed)


def read_prices(path: Path) -> dict[int, int]:
    prices: dict[int, int] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            raw = (row.get("price") or "").strip()
            if not raw:
                continue
            value = coadata.parse_money(raw)
            if value is None:
                print(f"  ! could not read price {raw!r} for item {row.get('item_id')}")
                continue
            try:
                prices[int(row["item_id"])] = value
            except (TypeError, ValueError):
                continue
    return prices


AUCTIONATOR_ENTRY_RE = re.compile(
    r'\["(?P<key>[^"]+)"\]\s*=\s*\{(?P<body>[^{}]*)\}', re.S
)


def read_auctionator(path: Path) -> dict[int, int]:
    """Best-effort reader for an Auctionator price database.

    Auctionator's layout has changed across versions, so this reports how many
    entries it matched.  A count of zero means the format differs -- treat that
    as "unsupported", not as "everything is free".
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    prices: dict[int, int] = {}
    for match in AUCTIONATOR_ENTRY_RE.finditer(text):
        key = match.group("key")
        id_match = re.match(r"^(\d+)(?::|$)", key)
        if not id_match:
            continue
        numbers = [int(n) for n in re.findall(r"\b(\d{2,})\b", match.group("body"))]
        if numbers:
            prices[int(id_match.group(1))] = min(numbers)
    return prices


# --- sourcing ---------------------------------------------------------------

WORLD_SOURCES = ("drop", "gathering", "container", "object", "quest objective",
                 "quest reward", "fishing", "disenchanting", "vendor")


def farmable_items(catalog: coadata.Catalog) -> set[int]:
    """Materials you can obtain yourself, per the database's source listing."""
    return {
        node["node_id"] for node in catalog.all_nodes()
        if node["kind"] == "item"
        and any(s in WORLD_SOURCES for s in (node.get("obtained_from") or []))
    }


def write_sourcing_template(catalog: coadata.Catalog, farmable: set[int],
                            path: Path, priced: set[int] | None = None) -> int:
    """Emit a per-material buy/gather plan for the user to edit."""
    needed: set[int] = set()
    for seed in catalog.seeds:
        needed |= set(catalog.raw_materials(seed))
        needed |= {r[0] for r in (seed.get("craft") or {}).get("reagents", [])}

    priced = priced or set()
    items = {}
    for item_id in sorted(needed, key=catalog.name):
        node = catalog.item(item_id)
        # Default to buying anything actually listed on the auction house, and
        # to gathering what is farmable but never sold. That mix is the one you
        # can really execute today.
        if item_id in priced:
            mode = "buy"
        elif item_id in farmable:
            mode = "gather"
        else:
            mode = "buy"
        items[str(item_id)] = {
            "name": catalog.name(item_id),
            "mode": mode,
            "listed_on_ah": item_id in priced,
            "sources": (node or {}).get("obtained_from") or [],
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "_comment": [
            "Per-material sourcing for the 'plan' scenario.",
            "mode 'gather' costs nothing but your time; mode 'buy' uses the",
            "scanned auction price, and leaves the recipe unpriced if none exists.",
            "The 'buy' and 'gather' scenarios ignore this file entirely.",
        ],
        "default": "buy",
        "items": items,
    }, indent=1, ensure_ascii=False), encoding="utf-8")
    return len(items)


def load_sourcing(path: Path, catalog: coadata.Catalog,
                  farmable: set[int]) -> tuple[set[int], str, dict[int, str]]:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    default = config.get("default", "buy")
    explicit: dict[int, str] = {}
    for key, entry in (config.get("items") or {}).items():
        mode = entry.get("mode") if isinstance(entry, dict) else entry
        if mode in ("buy", "gather"):
            explicit[int(key)] = mode

    free: set[int] = set()
    for node in catalog.all_nodes():
        if node["kind"] != "item":
            continue
        item_id = node["node_id"]
        mode = explicit.get(item_id, default)
        if mode == "gather" and item_id in farmable:
            free.add(item_id)
    return free, default, explicit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nodes", type=Path, default=Path("output/highrisk/nodes.json"))
    parser.add_argument("--output", type=Path, default=Path("output/market"))
    parser.add_argument("--prices", type=Path, help="CSV price sheet (overrides scan data)")
    parser.add_argument("--auctionator", type=Path, help="Auctionator SavedVariables .lua")
    parser.add_argument("--template", action="store_true", help="write a blank price sheet")
    parser.add_argument("--cut", type=float, default=AUCTION_CUT, help="auction house cut")
    parser.add_argument("--tsm-file", type=Path, help="TradeSkillMaster_AuctionDB.lua")
    parser.add_argument("--root", type=Path, help="Ascension client directory")
    parser.add_argument("--realm", default="Rexxar - Conquest of Azeroth",
                        help="realm to price against")
    parser.add_argument("--store", type=Path, default=Path("output/market/price_db.json"),
                        help="merged price store written by sync_prices.py")
    parser.add_argument("--no-store", action="store_true",
                        help="ignore the merged store and read the scan files directly")
    parser.add_argument("--buy-source", choices=("robust", "minbuyout", "market"),
                        default="robust", help="price paid for materials")
    parser.add_argument("--sell-source", choices=("robust", "minbuyout", "market"),
                        default="robust", help="price received for products")
    parser.add_argument("--outlier-floor", type=float, default=tsm_scan.ROBUST_FLOOR,
                        help="with --*-source robust, ignore a buyout below this "
                             "fraction of market value (default 0.5)")
    parser.add_argument("--sourcing", type=Path, default=Path("sourcing.json"),
                        help="per-material buy/gather plan for the 'plan' scenario")
    parser.add_argument("--sourcing-template", action="store_true",
                        help="write a starter sourcing plan and exit")
    parser.add_argument("--rank-by", default="gather",
                        choices=("buy", "gather", "plan"),
                        help="which scenario orders the ranking")
    parser.add_argument("--max-scan-age", type=float, default=7.0,
                        help="warn if the scan is older than this many days")
    args = parser.parse_args()

    catalog = coadata.load(args.nodes)
    args.output.mkdir(parents=True, exist_ok=True)
    if catalog.yield_changes:
        shifts = sorted({"{}->{}".format(c["from"], c["to"]) for c in catalog.yield_changes})
        print(f"Yield overrides applied to {len(catalog.yield_changes)} recipes "
              f"(database value -> corrected): {', '.join(shifts)}")

    if args.template:
        target = args.output / "prices.csv"
        count = write_template(catalog, target)
        print(f"Wrote price sheet with {count} items to {target}")
        print("Fill in the 'price' column (e.g. 153g20s), then re-run with --prices.")
        return 0

    buy: dict[int, int] = {}
    sell: dict[int, int] = {}
    scan_used = None

    # The merged store built by sync_prices.py is preferred: it pools every
    # source and retains prices that a later scan happened to miss.
    if args.store.exists() and not args.no_store:
        store = json.loads(args.store.read_text(encoding="utf-8"))
        newest = 0
        for key, entry in store.get("items", {}).items():
            item_id = int(key)
            price = tsm_scan.Price(
                item_id=item_id,
                market_value=entry.get("market_value"),
                min_buyout=entry.get("min_buyout"),
                quantity=entry.get("quantity"),
                last_scan=entry.get("last_scan"),
            )
            got_buy = price.pick(args.buy_source, args.outlier_floor)
            got_sell = price.pick(args.sell_source, args.outlier_floor)
            if got_buy:
                buy[item_id] = got_buy
            if got_sell:
                sell[item_id] = got_sell
            newest = max(newest, entry.get("last_scan") or 0)
        scan_used = tsm_scan.RealmScan(realm=store.get("realm") or "?",
                                       last_complete_scan=newest or None, prices={})
        age = scan_used.age_days
        print(f"Price store: {args.store} - {len(buy)} items, "
              f"newest {scan_used.scanned_at}"
              f"{f' ({age:.1f} days ago)' if age is not None else ''}")
        if age is not None and age > args.max_scan_age:
            print(f"  ! {age:.0f} days old. Re-scan in game, /reload, then run sync_prices.py")
        print(f"  buying at {args.buy_source}, selling at {args.sell_source}")

    elif not (args.prices or args.auctionator) or args.tsm_file or args.realm or args.root:
        paths = [args.tsm_file] if args.tsm_file else tsm_scan.find_auctiondb(args.root)
        candidates = [s for p in paths for s in tsm_scan.read(p, args.realm)]
        if not candidates:
            print("No TSM scan data found. Pass --tsm-file, or use --prices with a CSV.")
            if not (args.prices or args.auctionator):
                return 1
        else:
            # Prefer the realm with the most recently completed scan.
            scan_used = max(candidates, key=lambda s: s.last_complete_scan or 0)
            realms = sorted({s.realm for s in candidates})
            if args.realm is None and len(realms) > 1:
                print(f"Realms available: {', '.join(realms)}")
                print(f"Using the freshest: {scan_used.realm} (pass --realm to choose)")
            age = scan_used.age_days
            print(f"TSM scan: {scan_used.realm}, {len(scan_used.prices)} items, "
                  f"scanned {scan_used.scanned_at}"
                  f"{f' ({age:.1f} days ago)' if age is not None else ''}")
            if age is not None and age > args.max_scan_age:
                print(f"  ! This scan is {age:.0f} days old. Re-scan before trusting these numbers.")
            for item_id, price in scan_used.prices.items():
                got_buy = price.pick(args.buy_source, args.outlier_floor)
                got_sell = price.pick(args.sell_source, args.outlier_floor)
                if got_buy:
                    buy[item_id] = got_buy
                if got_sell:
                    sell[item_id] = got_sell
            print(f"  buying at {args.buy_source}, selling at {args.sell_source}")

    prices: dict[int, int] = {}
    if args.auctionator:
        found = read_auctionator(args.auctionator)
        print(f"Auctionator: matched {len(found)} item prices")
        if not found:
            print("  ! no entries matched - the file layout is not one this reader knows.")
        prices.update(found)
    if args.prices:
        found = read_prices(args.prices)
        print(f"Price sheet: {len(found)} prices")
        prices.update(found)

    # Hand-entered prices win over scanned ones.
    buy.update(prices)
    sell.update(prices)

    if not buy:
        print("No prices available; nothing to rank.")
        return 1

    farmable = farmable_items(catalog)
    plan_free = None
    if args.sourcing.exists():
        plan_free, default_mode, explicit = load_sourcing(args.sourcing, catalog, farmable)
        print(f"Sourcing plan: {args.sourcing} - default '{default_mode}', "
              f"{len(explicit)} explicit choices, {len(plan_free)} gathered")
    elif args.sourcing_template:
        count = write_sourcing_template(catalog, farmable, args.sourcing, set(buy))
        print(f"Wrote sourcing plan with {count} materials to {args.sourcing}")
        print("Set each to 'buy' or 'gather', then re-run.")
        return 0

    # --- scenarios ---------------------------------------------------------
    # The same recipe is worth different amounts depending on whether you buy
    # its materials or farm them, so both are reported side by side rather than
    # collapsing into one number that hides the assumption.
    scenarios = [
        ("buy", "every material bought at auction", set()),
        ("gather", "everything farmable gathered yourself", farmable),
    ]
    if plan_free is not None:
        scenarios.append(("plan", f"your {args.sourcing} choices", plan_free))

    by_product: dict[int, dict] = {}
    models: dict[str, Model] = {}
    for name, description, free_set in scenarios:
        model = Model(catalog, buy, sell, cut=args.cut, free=free_set)
        models[name] = model
        for seed in catalog.seeds:
            if not seed.get("craft"):
                continue
            row = model.evaluate(seed)
            merged = by_product.setdefault(row["product_id"], {
                "family": row["family"], "product": row["product"],
                "product_id": row["product_id"], "kind": row["kind"],
                "sale_item_id": row["sale_item_id"], "profession": row["profession"],
                "yield": row["yield"], "unit_sale_price": row["unit_sale_price"],
                "scenarios": {},
            })
            merged["scenarios"][name] = {
                "cost": row["material_cost_per_craft"],
                "profit": row["profit_per_craft"],
                "profit_per_unit": row["profit_per_unit"],
                "roi": row["roi_percent"],
                "gathered": row["farmed_materials"],
                "missing": row["unknown_names"],
            }
            merged.setdefault("breakdown", row["breakdown"])

    rank_by = args.rank_by if args.rank_by in dict((n, d) for n, d, _ in scenarios) else "gather"
    results = sorted(by_product.values(),
                     key=lambda r: (r["scenarios"][rank_by]["profit"] is None,
                                    -(r["scenarios"][rank_by]["profit"] or 0)))

    names = [n for n, _, _ in scenarios]
    with (args.output / "profit.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        head = ["rank", "family", "product", "product_id", "profession", "yield",
                "unit_sale_price"]
        for name in names:
            head += [f"{name}_cost", f"{name}_profit", f"{name}_roi"]
        head += ["gathered_materials", "missing_prices"]
        writer.writerow(head)
        for rank, row in enumerate(results, 1):
            cells = [rank, row["family"], row["product"], row["product_id"],
                     row["profession"] or "", row["yield"], row["unit_sale_price"] or ""]
            for name in names:
                sc = row["scenarios"][name]
                cells += [
                    "" if sc["cost"] is None else f"{sc['cost']:.0f}",
                    "" if sc["profit"] is None else f"{sc['profit']:.0f}",
                    "" if sc["roi"] is None else f"{sc['roi']:.1f}",
                ]
            ref = row["scenarios"][rank_by]
            cells += ["; ".join(ref["gathered"]), "; ".join(row["scenarios"]["buy"]["missing"])]
            writer.writerow(cells)

    # Per-item prices for the browser, one entry per scenario so a zero-cost
    # "gathered" assumption stays visible instead of folding into a total.
    price_map = {}
    store_prices = {}
    if args.store.exists() and not args.no_store:
        store_prices = {int(k): v for k, v in
                        json.loads(args.store.read_text(encoding="utf-8"))
                        .get("items", {}).items()}
    # Sale items are included even though they are not graph nodes: an enchant
    # is sold as its scroll, and the browser needs that price to show profit.
    priced_ids = {n["node_id"] for n in catalog.all_nodes() if n["kind"] == "item"}
    priced_ids |= {s["sale_item_id"] for s in catalog.seeds if s.get("sale_item_id")}
    for item_id in sorted(priced_ids):
        raw = store_prices.get(item_id)
        entry = {"buy": buy.get(item_id), "sell": sell.get(item_id),
                 "market": raw.get("market_value") if raw else None,
                 "minBuyout": raw.get("min_buyout") if raw else None,
                 "quantity": raw.get("quantity") if raw else None,
                 "scenarios": {}}
        for name in names:
            cost = models[name].unit_cost(item_id)
            entry["scenarios"][name] = {
                "unit_cost": cost.unit_cost,
                "source": ("scanned" if item_id in buy else
                           "crafted" if cost.made else
                           "gathered" if item_id in dict(
                               (n, f) for n, _, f in scenarios)[name] else "unknown"),
            }
        default = entry["scenarios"][rank_by]
        entry["unit_cost"], entry["source"] = default["unit_cost"], default["source"]
        price_map[item_id] = entry

    (args.output / "profit.json").write_text(json.dumps({
        "cut": args.cut,
        "buy_source": args.buy_source,
        "outlier_floor": args.outlier_floor,
        "sell_source": args.sell_source,
        "ranked_by": rank_by,
        "scenarios": [{"name": n, "description": d, "gathered_items": len(f)}
                      for n, d, f in scenarios],
        "scan": None if not scan_used else {
            "realm": scan_used.realm,
            "scanned_at": scan_used.scanned_at,
            "age_days": scan_used.age_days,
            "items": len(scan_used.prices),
        },
        "prices": price_map,
        "results": results,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    for name, description, free_set in scenarios:
        priced = sum(1 for r in results if r["scenarios"][name]["profit"] is not None)
        print(f"  {name:<7} {description:<38} {priced:>3}/{len(results)} priced")

    print(f"\nRanked by '{rank_by}'. Blank means a material has no recorded price.\n")
    header = f"{'#':>3}  {'product':<36}"
    for name in names:
        header += f" {name + ' profit':>16}"
    print(header)
    print("-" * len(header))
    for rank, row in enumerate(results[:15], 1):
        line = f"{rank:>3}  {row['product'][:36]:<36}"
        for name in names:
            line += f" {coadata.format_money(row['scenarios'][name]['profit']):>16}"
        print(line)
    print(f"\nWrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

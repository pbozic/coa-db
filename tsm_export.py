#!/usr/bin/env python3
"""Build TSM group item lists and operation settings for the High Risk crafts.

Ascension runs TSM3, whose Groups -> Import accepts a comma-separated list of
``item:<id>`` strings pasted into a group you create first.  Both that and the
TSM4 ``i:<id>`` spelling are emitted, because only one of them is right on any
given client and guessing wrong is a silent no-op.

Groups are split by what you *do* with an item, not just what it is:

* ``Sell``      -- the finished flasks, foods and enchant scrolls you list.
* ``Buy`` -> ``Vanilla`` / ``Custom``  -- reagents you acquire.
* ``Convert``   -- items whose only purpose is to be used at a station.
* ``Farm``      -- drop-only materials, which never have a reliable buy price.

Run ``highrisk.py`` first; this reads ``output/highrisk/nodes.json``.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import coadata

ROOT = "CoA`High Risk"

# TSM operation settings.  Crafting cost for these recipes cannot come from TSM
# itself -- the High Risk recipes are not in CraftingDB unless learned -- so the
# auctioning operations lean on dbmarket with a crafting floor where available.
OPERATIONS = """\
# ---------------------------------------------------------------------------
# TSM operations for the CoA`High Risk groups
# ---------------------------------------------------------------------------
# Create these under TSM > Operations, then assign them to the group shown.
#
# IMPORTANT: TSM only knows a recipe's cost after you open the profession
# window with the recipe learned. Until then `crafting` evaluates to nothing and
# any price string relying on it silently falls back. The profit model in
# profit.py does not depend on TSM knowing the recipe.

[Auctioning: "CoA High Risk - Consumables"]   -> CoA`High Risk`Sell`Flasks
                                              -> CoA`High Risk`Sell`Food
  Minimum price      max(110% crafting, 90% dbmarket, 150% vendorsell)
  Normal price       max(130% crafting, dbmarket)
  Maximum price      max(400% crafting, 250% dbmarket)
  Undercut           1c
  Post cap           5
  Keep quantity      0
  Duration           12h

[Auctioning: "CoA High Risk - Enchants"]      -> CoA`High Risk`Sell`Enchants
  Minimum price      max(110% crafting, 90% dbmarket)
  Normal price       max(140% crafting, dbmarket)
  Maximum price      max(500% crafting, 300% dbmarket)
  Undercut           1c
  Post cap           2
  Duration           12h

[Shopping: "CoA High Risk - Restock Mats"]    -> CoA`High Risk`Buy`Vanilla
                                              -> CoA`High Risk`Buy`Custom
  Maximum price      90% dbmarket
  Restock quantity   200

[Crafting: "CoA High Risk"]                   -> CoA`High Risk`Sell`*
  Minimum profit     max(50g, 25% crafting)
  Minimum restock    1
  Maximum restock    10

[Sniper / Dealfinding]                        -> CoA`High Risk`Buy`Custom
  Maximum price      70% dbmarket
  # Custom mats are thin markets. A 30% discount is a real deal, not noise.

# ---------------------------------------------------------------------------
# Groups that intentionally get NO auctioning operation
# ---------------------------------------------------------------------------
# CoA`High Risk`Convert  -- these are inputs to a station click, not products.
#                           Track them so you can price the conversion chain,
#                           but posting them competes with your own crafting.
# CoA`High Risk`Farm     -- drop-only. dbmarket is unreliable when supply is
#                           sporadic; use the profit model's manual price entry.
"""


def classify(catalog: coadata.Catalog, node: dict) -> str | None:
    """Return the group path suffix for a node, or None to leave it ungrouped."""
    if node["kind"] != "item":
        return None

    family = node.get("family")
    if node.get("is_seed"):
        return {"flask": "Sell`Flasks", "food": "Sell`Food"}.get(family)

    if node.get("missing_from_db"):
        return None

    # A material whose only role is to be consumed at a station. The effect
    # keeps its "Use:" prefix, so strip that before matching the verb.
    effect = (node.get("effect") or "").removeprefix("Use:").strip()
    if effect.startswith(("Refined into", "Feed to", "Water an", "Fertilize")):
        return "Convert"

    sources = set(node.get("obtained_from") or [])
    if sources == {"drop"}:
        return "Farm"
    return "Buy`Custom" if node.get("is_custom") else "Buy`Vanilla"


def build_groups(catalog: coadata.Catalog) -> dict[str, set[int]]:
    groups: dict[str, set[int]] = defaultdict(set)

    for node in catalog.all_nodes():
        suffix = classify(catalog, node)
        if suffix:
            groups[f"{ROOT}`{suffix}"].add(node["node_id"])

    # Enchants are spells, not items. The tradeable object is the recipe item
    # that teaches them, so that is what goes in the sell group.
    for node in catalog.seeds:
        if node.get("family") != "enchant":
            continue
        recipe_item = (node.get("craft") or {}).get("recipe_item_id")
        if recipe_item:
            groups[f"{ROOT}`Sell`Enchants"].add(recipe_item)

    return {path: ids for path, ids in groups.items() if ids}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nodes", type=Path, default=Path("output/highrisk/nodes.json"))
    parser.add_argument("--output", type=Path, default=Path("output/tsm"))
    args = parser.parse_args()

    catalog = coadata.load(args.nodes)
    groups = build_groups(catalog)
    args.output.mkdir(parents=True, exist_ok=True)

    for label, prefix in (("tsm3", "item:"), ("tsm4", "i:")):
        lines = [
            f"# TSM group item lists ({label} item syntax)",
            "# Create the group in TSM, then paste the matching line into Groups > Import.",
            "",
        ]
        for path in sorted(groups):
            ids = sorted(groups[path])
            lines += [f"[{path}]  ({len(ids)} items)",
                      ",".join(f"{prefix}{i}" for i in ids), ""]
        (args.output / f"groups_{label}.txt").write_text("\n".join(lines), encoding="utf-8")

    (args.output / "operations.txt").write_text(OPERATIONS, encoding="utf-8")

    (args.output / "groups.json").write_text(json.dumps({
        path: [{"item_id": i, "name": catalog.name(i)} for i in sorted(ids)]
        for path, ids in sorted(groups.items())
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Wrote {args.output}")
    for path in sorted(groups):
        print(f"  {path:<40} {len(groups[path]):>3} items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

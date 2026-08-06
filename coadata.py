#!/usr/bin/env python3
"""Shared loader over ``output/highrisk/nodes.json``.

``highrisk.py`` scrapes the graph; this module gives the TSM exporter, the
profit model and the HTML browser one consistent view of it, so the cost rules
live in exactly one place.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

BLIZZARD_MAX_ITEM_ID = 56815

QUALITY_NAMES = {
    0: "Poor", 1: "Common", 2: "Uncommon", 3: "Rare",
    4: "Epic", 5: "Legendary", 6: "Vanity", 7: "Heirloom",
}
QUALITY_COLORS = {
    0: "#9d9d9d", 1: "#ffffff", 2: "#1eff00", 3: "#0070dd",
    4: "#a335ee", 5: "#ff8000", 6: "#e6cc80", 7: "#00ccff",
}

FAMILY_LABELS = {
    "flask": "Flask", "food": "Food", "enchant": "Enchant", "material": "Material",
}


@dataclass
class Catalog:
    nodes: dict[tuple[str, int], dict] = field(default_factory=dict)
    seeds: list[dict] = field(default_factory=list)
    scope: str = ""
    yield_changes: list[dict] = field(default_factory=list)

    # -- lookup -------------------------------------------------------------
    def item(self, item_id: int) -> dict | None:
        return self.nodes.get(("item", item_id))

    def name(self, item_id: int) -> str:
        node = self.item(item_id)
        return node["name"] if node and node.get("name") else f"Item {item_id}"

    def all_nodes(self) -> list[dict]:
        return list(self.nodes.values())

    # -- graph --------------------------------------------------------------
    def is_custom(self, item_id: int) -> bool:
        return item_id > BLIZZARD_MAX_ITEM_ID

    def expandable(self, node: dict | None) -> bool:
        """True when a node's own recipe should be walked into.

        Mirrors the scraper's rule: Blizzard-era materials are leaves, because
        you farm or buy them and their "recipes" are the circular Alchemy
        transmute chain.
        """
        if not node:
            return False
        craft = node.get("craft")
        return bool(craft and craft.get("reagents") and node.get("is_custom"))

    def used_in(self, item_id: int) -> list[dict]:
        return sorted(
            (n for n in self.nodes.values()
             if n.get("craft") and any(r[0] == item_id for r in n["craft"]["reagents"])),
            key=lambda n: (n.get("family", ""), n.get("name", "")),
        )

    def raw_materials(self, node: dict, runs: float = 1.0, depth: int = 0,
                      path: frozenset[int] = frozenset()) -> dict[int, float]:
        """Materials for running ``node``'s recipe ``runs`` times, fully expanded."""
        craft = node.get("craft")
        if not craft or depth > 8 or node["node_id"] in path:
            return {node["node_id"]: runs}
        totals: dict[int, float] = defaultdict(float)
        for reagent_id, qty in craft["reagents"]:
            need = qty * runs
            child = self.item(reagent_id)
            if not self.expandable(child):
                totals[reagent_id] += need
                continue
            child_runs = need / max(1, child["craft"]["yield_min"])
            for mat_id, mat_qty in self.raw_materials(
                child, child_runs, depth + 1, path | {node["node_id"]}
            ).items():
                totals[mat_id] += mat_qty
        return dict(totals)

    def chain_to_raw(self, item_id: int, depth: int = 0) -> list[dict]:
        """The conversion/craft steps between a raw material and ``item_id``."""
        node = self.item(item_id)
        steps: list[dict] = []
        while node and self.expandable(node) and depth < 8:
            craft = node["craft"]
            steps.append({
                "product_id": node["node_id"],
                "product": node["name"],
                "kind": craft["craft_kind"],
                "method": craft.get("method"),
                "station": craft.get("station"),
                "profession": craft.get("profession"),
                "inputs": [[r[0], r[1], self.name(r[0])] for r in craft["reagents"]],
            })
            nxt = craft["reagents"][0][0] if craft["reagents"] else None
            node = self.item(nxt) if nxt else None
            depth += 1
        return steps


def apply_yield_overrides(catalog: Catalog, path: Path) -> list[dict]:
    """Correct recipe yields that the database reports wrongly.

    db.ascension.gg's ``creates`` count is wrong for the custom High Risk
    recipes -- it claims 10 for Fused foods where the game gives 1.  Yield
    multiplies revenue directly, so a bad value here silently inflates every
    profit figure.  Returns the list of corrections actually applied.
    """
    if not Path(path).exists():
        return []
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    by_family = config.get("by_family", {})
    by_item = {int(k): v for k, v in config.get("by_item", {}).items()}

    changes = []
    for node in catalog.nodes.values():
        craft = node.get("craft")
        if not craft:
            continue
        wanted = by_item.get(node["node_id"], by_family.get(node.get("family")))
        if wanted is None:
            continue
        reported = craft.get("yield_min", 1)
        craft["yield_reported_by_db"] = reported
        craft["yield_source"] = "override" if reported != wanted else "database"
        if reported != wanted:
            craft["yield_min"] = craft["yield_max"] = wanted
            changes.append({"item": node["node_id"], "name": node.get("name"),
                            "from": reported, "to": wanted})
    return changes


def load(path: Path = Path("output/highrisk/nodes.json"),
         overrides: Path = Path("yield_overrides.json")) -> Catalog:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    catalog = Catalog(scope=data.get("scope", ""))
    for node in data["nodes"]:
        catalog.nodes[(node["kind"], node["node_id"])] = node
        if node.get("is_seed"):
            catalog.seeds.append(node)
    catalog.seeds.sort(key=lambda n: (n.get("family", ""), n.get("name", "")))
    catalog.yield_changes = apply_yield_overrides(catalog, overrides)
    return catalog


# --- money ------------------------------------------------------------------

def parse_money(value: str) -> int | None:
    """Parse ``12g30s5c`` / ``12.5g`` / a bare copper amount into copper."""
    text = (value or "").strip().lower().replace(",", "").replace(" ", "")
    if not text:
        return None
    if text.replace(".", "", 1).isdigit():
        return int(round(float(text)))
    total = 0.0
    matched = False
    for unit, mult in (("g", 10000), ("s", 100), ("c", 1)):
        idx = text.find(unit)
        if idx < 0:
            continue
        head = text[:idx]
        number = ""
        for char in reversed(head):
            if char.isdigit() or char == ".":
                number = char + number
            else:
                break
        if number:
            total += float(number) * mult
            matched = True
        text = text[idx + 1:]
    return int(round(total)) if matched else None


def format_money(copper: float | None) -> str:
    if copper is None:
        return "-"
    negative = copper < 0
    copper = abs(int(round(copper)))
    gold, rest = divmod(copper, 10000)
    silver, copper_part = divmod(rest, 100)
    parts = []
    if gold:
        parts.append(f"{gold:,}g")
    if silver or gold:
        parts.append(f"{silver}s")
    if copper_part or not parts:
        parts.append(f"{copper_part}c")
    return ("-" if negative else "") + " ".join(parts)

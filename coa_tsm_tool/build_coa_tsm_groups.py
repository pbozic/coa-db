#!/usr/bin/env python3
"""Build CoA TSM group lists and a crafting catalog from Ascension TSM SavedVariables.

The parser intentionally handles only the stable structures needed here rather than
executing arbitrary Lua. That keeps a SavedVariables file from becoming code execution,
which would be an impressively avoidable security failure.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

ITEM_RE = re.compile(r"item:(\d+)(?::\d+){0,6}")
ACCOUNTING_ENTRY_RE = re.compile(r'\["(?P<name>(?:\\.|[^"\\])*)"\]\s*=\s*"(?P<item>item:\d+(?::\d+){0,6})"')
CRAFT_START_RE = re.compile(r"\[(\d+)\]\s*=\s*\{")

PVP_TERMS = (
    "swiftness", "invisibility", "free action", "living action", "invulnerability",
    "restoration potion", "purification", "protection potion", "rage potion",
    "healing potion", "mana potion", "rejuvenation potion", "thistle tea",
    "sap", "dynamite", "grenade", "bomb", "rocket", "oil", "stone",
)

CATEGORY_RULES = [
    ("Flasks", ("flask",)),
    ("Potions/Healing", ("healing potion", "rejuvenation potion")),
    ("Potions/Mana", ("mana potion",)),
    ("Potions/PvP Utility", ("swiftness potion", "invisibility potion", "free action potion", "living action potion", "invulnerability potion", "restoration potion", "purification potion")),
    ("Potions/Protection", ("protection potion", "resistance potion")),
    ("Potions/Other", ("potion",)),
    ("Elixirs", ("elixir",)),
    ("Weapon Buffs/Oils", ("weapon oil", "wizard oil", "mana oil", "shadow oil", "frost oil", "fire oil", "blackmouth oil")),
    ("Weapon Buffs/Stones", ("sharpening stone", "weightstone",)),
    ("Scrolls", ("scroll of",)),
    ("Engineering/PvP", ("dynamite", "grenade", "bomb", "sapper", "seaforium", "rocket", "reflector")),
    ("Enchants/Formulae", ("formula: enchant",)),
    ("Enchants/Consumable", ("enchant weapon", "enchant chest", "enchant boots", "enchant gloves", "enchant bracer", "enchant cloak", "enchant shield")),
    ("Food & Drink", ("food", "drink", "steak", "stew", "omelet", "chops", "soup", "tea", "rum", "cocktail", "cornbread", "dumpling", "squid", "salmon", "nightfin", "sunscale")),
    ("Bandages", ("bandage",)),
]


def normalize_item(item: str) -> str:
    match = ITEM_RE.search(item)
    if not match:
        raise ValueError(f"Invalid item string: {item!r}")
    return f"item:{match.group(1)}"


def find_matching_brace(text: str, opening: int) -> int:
    depth = 0
    in_string = False
    escaped = False
    for index in range(opening, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("Unbalanced Lua table braces")


def extract_named_table(text: str, key: str) -> str:
    marker = f'["{key}"] = {{'
    start = text.find(marker)
    if start < 0:
        return ""
    opening = text.find("{", start)
    closing = find_matching_brace(text, opening)
    return text[opening + 1:closing]


def extract_crafts(crafting_text: str) -> list[dict[str, Any]]:
    crafts_text = extract_named_table(crafting_text, "crafts")
    crafts: list[dict[str, Any]] = []
    cursor = 0
    while True:
        match = CRAFT_START_RE.search(crafts_text, cursor)
        if not match:
            break
        opening = crafts_text.find("{", match.start())
        closing = find_matching_brace(crafts_text, opening)
        block = crafts_text[opening + 1:closing]
        cursor = closing + 1

        def string_field(name: str) -> str | None:
            field = re.search(rf'\["{re.escape(name)}"\]\s*=\s*"((?:\\.|[^"\\])*)"', block)
            return bytes(field.group(1), "utf-8").decode("unicode_escape") if field else None

        def int_field(name: str, default: int = 0) -> int:
            field = re.search(rf'\["{re.escape(name)}"\]\s*=\s*(-?\d+)', block)
            return int(field.group(1)) if field else default

        mats_block = extract_named_table(block, "mats")
        mats: dict[str, int] = {}
        for mat, count in re.findall(r'\["(item:\d+(?::\d+){0,6})"\]\s*=\s*(\d+)', mats_block):
            mats[normalize_item(mat)] = int(count)

        name = string_field("name")
        item = string_field("itemID")
        profession = string_field("profession")
        if name and item and profession:
            crafts.append({
                "recipe_spell_id": int(match.group(1)),
                "name": name,
                "profession": profession,
                "item": normalize_item(item),
                "num_result": int_field("numResult", 1),
                "queued": int_field("queued", 0),
                "materials": mats,
            })
    return crafts


def extract_accounting_items(accounting_text: str) -> dict[str, str]:
    items: dict[str, str] = {}
    for match in ACCOUNTING_ENTRY_RE.finditer(accounting_text):
        name = bytes(match.group("name"), "utf-8").decode("unicode_escape")
        items[name] = normalize_item(match.group("item"))
    return items


def classify(name: str, profession: str | None = None) -> str:
    lowered = name.casefold()
    if profession == "Enchanting" and not lowered.startswith("formula:"):
        return "Enchants/Crafted"
    for category, terms in CATEGORY_RULES:
        if any(term in lowered for term in terms):
            return category
    if profession == "Alchemy":
        return "Alchemy/Other"
    if profession == "Cooking":
        return "Food & Drink"
    if profession == "Engineering":
        return "Engineering/Other"
    return "Other"


def is_relevant(name: str, profession: str | None = None) -> bool:
    category = classify(name, profession)
    return category != "Other" and not category.startswith("Enchants/Formulae")


def is_high_risk_pvp(name: str, category: str) -> bool:
    lowered = name.casefold()
    return category == "Engineering/PvP" or any(term in lowered for term in PVP_TERMS)


def write_outputs(crafts: list[dict[str, Any]], accounting_items: dict[str, str], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)

    known_items = {craft["item"] for craft in crafts}
    rows: list[dict[str, Any]] = []
    for craft in crafts:
        category = classify(craft["name"], craft["profession"])
        if not is_relevant(craft["name"], craft["profession"]):
            continue
        rows.append({
            **craft,
            "category": category,
            "high_risk_pvp": is_high_risk_pvp(craft["name"], category),
            "custom_id_likely": int(craft["item"].split(":")[1]) > 100000,
            "source": "CraftingDB learned recipe",
        })

    candidates = []
    for name, item in accounting_items.items():
        category = classify(name)
        if category == "Other" or category == "Enchants/Formulae" or item in known_items:
            continue
        candidates.append({
            "name": name,
            "item": item,
            "category": category,
            "high_risk_pvp": is_high_risk_pvp(name, category),
            "custom_id_likely": int(item.split(":")[1]) > 100000,
            "craftable": "unknown",
            "source": "AccountingDB observed item",
        })

    rows.sort(key=lambda row: (row["category"], row["name"]))
    candidates.sort(key=lambda row: (row["category"], row["name"]))

    with (output / "coa_known_crafts.json").open("w", encoding="utf-8") as handle:
        json.dump(rows, handle, indent=2, ensure_ascii=False)
    with (output / "coa_observed_candidates.json").open("w", encoding="utf-8") as handle:
        json.dump(candidates, handle, indent=2, ensure_ascii=False)

    craft_fields = ["recipe_spell_id", "name", "profession", "item", "num_result", "category", "high_risk_pvp", "custom_id_likely", "materials_json", "source"]
    with (output / "coa_known_crafts.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=craft_fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: (json.dumps(row["materials"], sort_keys=True) if field == "materials_json" else row.get(field, "")) for field in craft_fields})

    candidate_fields = ["name", "item", "category", "high_risk_pvp", "custom_id_likely", "craftable", "source"]
    with (output / "coa_observed_candidates.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=candidate_fields)
        writer.writeheader()
        writer.writerows(candidates)

    groups: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        groups[f"CoA`Known Crafts`{row['category'].replace('/', '`')}"] .add(row["item"])
        if row["high_risk_pvp"]:
            groups["CoA`Known Crafts`High Risk PvP"].add(row["item"])
    for row in candidates:
        groups[f"CoA`Observed Items`{row['category'].replace('/', '`')}"] .add(row["item"])
        if row["high_risk_pvp"]:
            groups["CoA`Observed Items`High Risk PvP"].add(row["item"])

    with (output / "tsm_group_item_lists.txt").open("w", encoding="utf-8") as handle:
        handle.write("# Each section is a TSM group path followed by a comma-separated item import list.\n")
        handle.write("# Import the item list into the matching group. Observed Items are candidates, not verified recipes.\n\n")
        for group_name in sorted(groups):
            handle.write(f"[{group_name}]\n")
            handle.write(",".join(sorted(groups[group_name], key=lambda value: int(value.split(":")[1]))) + "\n\n")

    with (output / "profitability_template.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["name", "item", "category", "profession", "num_result", "material_cost_copper", "market_value_each_copper", "auction_cut_percent", "net_revenue_copper", "profit_copper", "roi_percent", "decision"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "name": row["name"], "item": row["item"], "category": row["category"],
                "profession": row["profession"], "num_result": row["num_result"],
                "auction_cut_percent": 5,
            })

    summary = {
        "known_crafts_total": len(crafts),
        "known_relevant_crafts": len(rows),
        "observed_relevant_candidates": len(candidates),
        "groups": {name: len(items) for name, items in sorted(groups.items())},
    }
    with (output / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--crafting", type=Path, required=True)
    parser.add_argument("--accounting", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("coa_tsm_output"))
    args = parser.parse_args()

    crafting_text = args.crafting.read_text(encoding="utf-8", errors="replace")
    accounting_text = args.accounting.read_text(encoding="utf-8", errors="replace")
    crafts = extract_crafts(crafting_text)
    items = extract_accounting_items(accounting_text)
    if not crafts:
        raise SystemExit("No crafts found. Open each profession in-game, then /reload before exporting SavedVariables.")
    write_outputs(crafts, items, args.output)
    print(f"Extracted {len(crafts)} learned crafts and {len(items)} observed item names into {args.output}")


if __name__ == "__main__":
    main()

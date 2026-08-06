#!/usr/bin/env python3
"""Harvest the AtlasLoot "High Risk" (Classic) crafting lists from db.ascension.gg.

Scope is fixed to the three recipe lists shown in AtlasLoot Ascension Edition
under Crafting -> <profession> -> High Risk, expansion "Classic":

* Alchemy    -- the 11 "Distilled Flask of ..." flasks
* Enchanting -- the 10 High Risk "Enchant Weapon - ..." enchants
* Cooking    -- the "Fused ..." foods

Each recipe is expanded into its reagents, and any reagent that is itself
server-added content is expanded in turn, down to materials you gather, buy or
loot.  Two rules keep that expansion meaningful:

* Blizzard-era materials (item ID <= 56815) are treated as leaves.  They are
  farmed or bought, and expanding them only walks into the circular Alchemy
  transmute chain (Essence of Earth -> Fire -> Air -> Water -> Earth).
* Some custom materials are not crafted but *converted*: the source item has a
  "Use: Refined into X at the <station>" effect.  Those are resolved from the
  input side and recorded as conversions rather than recipes.

Quantities are reported per single execution of a recipe, alongside its yield
(a Distilled flask recipe produces 3, a Fused food recipe produces 10).

Outputs land in ``output/highrisk/``.
"""
from __future__ import annotations

import argparse
import csv
import html as html_mod
import json
import logging
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

import aowow

LOG = logging.getLogger("highrisk")

SKILL_NAMES = {
    171: "Alchemy", 164: "Blacksmithing", 333: "Enchanting", 202: "Engineering",
    182: "Herbalism", 773: "Inscription", 755: "Jewelcrafting", 165: "Leatherworking",
    186: "Mining", 393: "Skinning", 197: "Tailoring", 185: "Cooking",
    129: "First Aid", 356: "Fishing",
}

# --- The recipe lists, exactly as they appear in AtlasLoot -------------------

DISTILLED_FLASKS = [
    "Distilled Flask of Manifesting Power",
    "Distilled Flask of the Warsong",
    "Distilled Flask of the Kirin Tor",
    "Distilled Flask of Butchery",
    "Distilled Flask of the Unyielding",
    "Distilled Flask of Unrelenting Power",
    "Distilled Flask of Savage Assault",
    "Distilled Flask of Shattering Thunder",
    "Distilled Flask of the Executioner",
    "Distilled Flask of Deep Meditation",
    "Distilled Flask of Adept Striking",
]

HIGH_RISK_ENCHANTS = [
    "Enchant Weapon - Unstoppable Assault",
    "Enchant Weapon - Lucid Assault",
    "Enchant Weapon - Spellbinder's Rage",
    "Enchant Weapon - Ninja's Focus",
    "Enchant Weapon - Grovewarden's Blessing",
    "Enchant Weapon - Viscious Assault",
    "Enchant Weapon - Arcane Dexterity",
    "Enchant Weapon - Arcane Artillery",
    "Enchant Weapon - Arcane Precision",
    "Enchant Weapon - Brutal Crusader",
]

# The Cooking list spans four AtlasLoot pages; the whole "Fused ..." family is
# taken from the Food & Drink listing instead of transcribing pages that scroll
# off screen.  They occupy one contiguous ID block (967510-967630).
FUSED_FOOD_PREFIX = "Fused "

FLASK_LISTING = "?items=0.3"
FOOD_LISTING = "?items=0.5"
ENCHANT_LISTING = "?spells=11.333"
ENCHANT_SCROLL_LISTING = "?items=0&filter=na=Scroll of Enchant Weapon"
SCROLL_PREFIX = "Scroll of Enchant Weapon - "

RECIPE_ITEM_RE = re.compile(r"^(Recipe|Formula|Plans|Pattern|Schematic|Technique|Design):\s*(.+)$")
STATION_RE = re.compile(r"Requires \[url=\?object=(\d+)\]([^\]]+)\[/url\]")
REFINED_RE = re.compile(r"Use:\s*(.+?)\s+at the\s+(.+?)$", re.M)

SOURCE_LISTVIEWS = {
    "sold-by-npc": "vendor",
    "dropped-by": "drop",
    "contained-in-item": "container",
    "contained-in-object": "object",
    "reward-from-quest": "quest reward",
    "objective-of-quest": "quest objective",
    "created-by": "crafted",
    "milled-from": "milling",
    "prospected-from": "prospecting",
    "disenchanted-from": "disenchanting",
    "fished-from-zone": "fishing",
    "gathered-from-object": "gathering",
}


@dataclass
class Craft:
    """How something is produced: a trade-skill recipe or a station conversion."""

    spell_id: int
    spell_name: str
    craft_kind: str = "recipe"             # "recipe" | "conversion"
    profession: str | None = None
    skill_id: int | None = None
    learned_at: int | None = None
    difficulty: list[int] | None = None    # orange / yellow / green / grey
    creates_item_id: int | None = None
    yield_min: int = 1
    yield_max: int = 1
    reagents: list[list[int]] = field(default_factory=list)   # [[item_id, qty], ...]
    station: str | None = None
    method: str | None = None              # conversions: the input item's "Use:" line
    recipe_item_id: int | None = None
    recipe_item_name: str | None = None
    reagents_resolved: bool = True


@dataclass
class Node:
    """An item, or an enchant spell, in the craft graph."""

    node_id: int
    kind: str                              # "item" | "spell"
    name: str = ""
    quality: int | None = None
    icon: str | None = None
    tooltip: str = ""
    effect: str = ""
    craft: Craft | None = None
    is_seed: bool = False
    family: str = ""                       # flask | food | enchant | material
    obtained_from: list[str] = field(default_factory=list)
    is_custom: bool = False
    missing_from_db: bool = False
    # Where it drops: NPC name, level range, drop rate and zone names.
    drops: list[dict] = field(default_factory=list)
    # Best zone per source, scored on drop rate against mob level.
    zone_sources: list[dict] = field(default_factory=list)
    # Herb/mining nodes it comes from, and containers that hold it.
    gathered_from: list[dict] = field(default_factory=list)
    contained_in: list[dict] = field(default_factory=list)
    # What you actually list on the auction house. For a flask or food that is
    # the item itself; an enchant is sold as its scroll, not as the recipe.
    sale_item_id: int | None = None
    sale_item_name: str | None = None
    warnings: list[str] = field(default_factory=list)


def is_custom_item(item_id: int) -> bool:
    return item_id > aowow.BLIZZARD_MAX_ITEM_ID


def craft_from_row(row: dict) -> Craft:
    skills = row.get("skill") or []
    skill_id = skills[0] if skills else None
    creates = row.get("creates") or []
    colors = row.get("colors") or []
    return Craft(
        spell_id=int(row["id"]),
        spell_name=aowow.strip_name_prefix(row.get("name", ""))[0],
        profession=SKILL_NAMES.get(skill_id) if skill_id else None,
        skill_id=skill_id,
        learned_at=row.get("learnedat"),
        difficulty=list(colors) if colors else None,
        creates_item_id=int(creates[0]) if creates else None,
        yield_min=int(creates[1]) if len(creates) > 1 else 1,
        yield_max=int(creates[2]) if len(creates) > 2 else 1,
        reagents=[[int(i), int(q)] for i, q in (row.get("reagents") or [])],
    )


def summarise_zones(drops: list[dict]) -> list[dict]:
    """Rank zones for farming: drop rate weighted against mob level.

    A slightly worse drop rate on much weaker mobs beats a marginally better one
    on elites you kill slowly, so the score divides the rate by the square root
    of the mob level rather than sorting on rate alone.
    """
    zones: dict[str, dict] = {}
    for drop in drops:
        for zone in drop.get("zones") or []:
            level = drop.get("min_level") or 1
            percent = drop.get("percent") or 0.0
            entry = zones.setdefault(zone, {
                "zone": zone, "percent": 0.0, "level": level,
                "npc": drop["npc"], "npcs": 0, "elite": drop.get("elite", False),
            })
            entry["npcs"] += 1
            if percent > entry["percent"]:
                entry.update(percent=percent, level=level, npc=drop["npc"],
                             elite=drop.get("elite", False))
    for entry in zones.values():
        entry["score"] = round(entry["percent"] / max(1.0, entry["level"]) ** 0.5, 3)
    return sorted(zones.values(), key=lambda z: -z["score"])


def extract_effect(tooltip: str) -> str:
    """Pull the meaningful effect line(s) out of a tooltip."""
    lines = [ln for ln in tooltip.split("\n")]
    keep = [
        ln for ln in lines
        if ln.startswith("Use:") or ln.startswith("Equip:")
        or "Permanently enchant" in ln or ln.startswith("Set:")
    ]
    if keep:
        return " ".join(keep)
    # Food buffs describe themselves without a "Use:" prefix.
    for line in lines[1:]:
        if len(line) > 40 and not line.startswith(("Requires", "Sell Price", '"')):
            return line
    return ""


class Harvester:
    def __init__(self, client: aowow.Client) -> None:
        self.client = client
        self.nodes: dict[tuple[str, int], Node] = {}
        self.names: dict[int, str] = {}        # item id -> name, from any page
        self.spell_cache: dict[int, aowow.Entity] = {}
        self._zones: dict[int, str] = {}

    # -- helpers ------------------------------------------------------------
    def listing_rows(self, url: str, template: str) -> list[dict]:
        view = aowow.fetch_listview(self.client, url, template=template)
        if view is None:
            raise SystemExit(f"No {template} listview at {url}")
        if not view.complete:
            LOG.warning("Listing %s truncated (%s of %s) - seeds may be incomplete",
                        url, view.shown, view.total)
        return view.rows

    def remember_names(self, entity: aowow.Entity) -> None:
        for ent_id, meta in entity.g_tables.get("g_items", {}).items():
            if meta.get("name_enus"):
                self.names.setdefault(int(ent_id), meta["name_enus"])

    def spell_page(self, spell_id: int) -> aowow.Entity | None:
        if spell_id in self.spell_cache:
            return self.spell_cache[spell_id]
        try:
            entity = aowow.fetch_entity(self.client, spell_id, "spell")
        except Exception as exc:
            LOG.error("spell %s: %s", spell_id, exc)
            return None
        self.remember_names(entity)
        self.spell_cache[spell_id] = entity
        return entity

    def name_of(self, item_id: int) -> str:
        node = self.nodes.get(("item", item_id))
        if node and node.name:
            return node.name
        return self.names.get(item_id, f"Item {item_id}")

    # -- seeds --------------------------------------------------------------
    def resolve_seeds(self) -> list[Node]:
        seeds: list[Node] = []

        flasks = {aowow.strip_name_prefix(r["name"])[0]: r
                  for r in self.listing_rows(FLASK_LISTING, "item")}
        for wanted in DISTILLED_FLASKS:
            row = flasks.get(wanted)
            if row is None:
                LOG.error("Flask not found: %s", wanted)
                continue
            seeds.append(self._seed_item(row, "flask"))

        foods = [r for r in self.listing_rows(FOOD_LISTING, "item")
                 if aowow.strip_name_prefix(r["name"])[0].startswith(FUSED_FOOD_PREFIX)]
        for row in sorted(foods, key=lambda r: r["id"]):
            seeds.append(self._seed_item(row, "food"))

        enchants = {aowow.strip_name_prefix(r["name"])[0]: r
                    for r in self.listing_rows(ENCHANT_LISTING, "spell")}
        for wanted in HIGH_RISK_ENCHANTS:
            row = enchants.get(wanted)
            if row is None:
                LOG.error("Enchant not found: %s", wanted)
                continue
            node = Node(node_id=int(row["id"]), kind="spell",
                        name=aowow.strip_name_prefix(row["name"])[0],
                        icon=row.get("icon"), is_seed=True, family="enchant",
                        is_custom=True)
            node.craft = craft_from_row(row)
            self.nodes[("spell", node.node_id)] = node
            seeds.append(node)

        self._attach_enchant_scrolls(seeds)
        for node in seeds:
            if node.kind == "item" and node.sale_item_id is None:
                node.sale_item_id, node.sale_item_name = node.node_id, node.name

        return seeds

    def _attach_enchant_scrolls(self, seeds: list[Node]) -> None:
        """Point each enchant at the scroll item that is actually traded.

        The spell page only names the "Recipe: ..." item that teaches it, so the
        scroll has to be matched separately by name.
        """
        wanted = {n.name: n for n in seeds if n.family == "enchant"}
        if not wanted:
            return
        try:
            rows = self.listing_rows(ENCHANT_SCROLL_LISTING, "item")
        except Exception as exc:
            LOG.error("Could not list enchant scrolls: %s", exc)
            return

        for row in rows:
            name = aowow.strip_name_prefix(row["name"])[0]
            if not name.startswith(SCROLL_PREFIX):
                continue
            enchant_name = name[len(SCROLL_PREFIX):]
            node = wanted.get(f"Enchant Weapon - {enchant_name}")
            if node is not None and node.sale_item_id is None:
                node.sale_item_id = int(row["id"])
                node.sale_item_name = name

        for name, node in wanted.items():
            if node.sale_item_id is None:
                node.warnings.append("no tradeable scroll item found")
                LOG.warning("No scroll item for %s", name)

    def _seed_item(self, row: dict, family: str) -> Node:
        name, quality = aowow.strip_name_prefix(row["name"])
        node = Node(node_id=int(row["id"]), kind="item", name=name,
                    quality=row.get("quality", quality), icon=row.get("icon"),
                    is_seed=True, family=family, is_custom=is_custom_item(int(row["id"])))
        self.nodes[("item", node.node_id)] = node
        return node

    # -- expansion ----------------------------------------------------------
    def expand(self, seeds: list[Node]) -> None:
        queue: list[int] = []

        for node in seeds:
            if node.kind == "item":
                loaded = self.load_item(node.node_id)
                if loaded.craft is None:
                    loaded.warnings.append("seed has no created-by recipe")
            self.enrich_craft(node)
            if node.craft:
                queue += [r[0] for r in node.craft.reagents]

        seen: set[int] = set()
        while queue:
            item_id = queue.pop(0)
            if item_id in seen:
                continue
            seen.add(item_id)

            node = self.load_item(item_id)
            # Blizzard-era materials are leaves: farmed or bought, and their
            # "recipes" are the circular transmute chain.
            if not node.is_custom:
                node.craft = None
                continue
            self.enrich_craft(node)
            if node.craft:
                queue += [r[0] for r in node.craft.reagents]

    def load_item(self, item_id: int) -> Node:
        key = ("item", item_id)
        node = self.nodes.get(key)
        if node is not None and (node.tooltip or node.missing_from_db):
            return node
        if node is None:
            node = Node(node_id=item_id, kind="item", family="material",
                        is_custom=is_custom_item(item_id))
            self.nodes[key] = node

        try:
            entity = aowow.fetch_entity(self.client, item_id, "item")
        except Exception as exc:
            node.missing_from_db = True
            node.name = node.name or self.names.get(item_id, "")
            node.warnings.append(f"item page unavailable ({exc.__class__.__name__}); "
                                 "referenced by a recipe but not present in the database")
            LOG.warning("item %s (%s) has no page", item_id, node.name or "unnamed")
            return node

        self.remember_names(entity)
        node.name = entity.name or node.name or self.names.get(item_id, "")
        node.quality = entity.quality if entity.quality is not None else node.quality
        node.icon = entity.icon or node.icon
        node.tooltip = entity.tooltip_text
        node.effect = extract_effect(entity.tooltip_text)

        for lv_id, label in SOURCE_LISTVIEWS.items():
            view = entity.listviews.get(lv_id)
            if view and view.rows and label not in node.obtained_from:
                node.obtained_from.append(label)

        node.drops = self.collect_drops(entity)
        node.zone_sources = summarise_zones(node.drops)
        node.gathered_from = self.collect_gathering(entity)
        node.contained_in = self.collect_containers(entity)

        created_by = entity.listviews.get("created-by")
        if created_by and created_by.rows:
            if len(created_by.rows) > 1:
                node.warnings.append(
                    f"{len(created_by.rows)} spells create this item; using the first")
            node.craft = craft_from_row(created_by.rows[0])
        return node

    def zone_name(self, zone_id: int) -> str:
        """Zone names live in the page title; the heading is a comment stub."""
        if zone_id in self._zones:
            return self._zones[zone_id]
        name = f"Zone {zone_id}"
        try:
            html = self.client.get(f"?zone={zone_id}")
            match = re.search(r"<title>(.*?)</title>", html, re.S)
            if match:
                # Titles come back double-escaped (Un&amp;#039;Goro Crater).
                name = html_mod.unescape(html_mod.unescape(
                    match.group(1).split(" - ")[0])).strip()
        except Exception as exc:
            LOG.warning("zone %s: %s", zone_id, exc)
        self._zones[zone_id] = name
        return name

    def collect_drops(self, entity: aowow.Entity) -> list[dict]:
        """NPCs that drop this item, with rate and zone."""
        view = entity.listviews.get("dropped-by")
        if not view or not view.rows:
            return []
        drops = []
        for row in sorted(view.rows, key=lambda r: -(r.get("percent") or 0))[:60]:
            zones = [self.zone_name(int(z)) for z in (row.get("location") or [])]
            drops.append({
                "npc_id": int(row["id"]),
                "npc": aowow.strip_name_prefix(row.get("name", ""))[0],
                "min_level": row.get("minlevel"),
                "max_level": row.get("maxlevel"),
                "percent": row.get("percent"),
                "elite": bool(row.get("classification")),
                "zones": zones,
            })
        return drops

    def collect_gathering(self, entity: aowow.Entity) -> list[dict]:
        """Herb or mining nodes this item is gathered from."""
        view = entity.listviews.get("gathered-from-object")
        if not view or not view.rows:
            return []
        # The same herb appears once per object id; keep the easiest instance.
        best: dict[str, dict] = {}
        for row in view.rows:
            name = aowow.strip_name_prefix(row.get("name", ""))[0]
            skill = row.get("skill") or 0
            entry = best.get(name)
            if entry is None or skill < entry["skill"]:
                best[name] = {"name": name, "skill": skill, "percent": row.get("percent")}
        return sorted(best.values(), key=lambda e: e["skill"])

    def collect_containers(self, entity: aowow.Entity) -> list[dict]:
        """Containers that can hold this item."""
        view = entity.listviews.get("contained-in-item")
        if not view or not view.rows:
            return []
        best: dict[str, dict] = {}
        for row in view.rows:
            name = aowow.strip_name_prefix(row.get("name", ""))[0]
            entry = best.setdefault(name, {"name": name, "percent": 0.0})
            entry["percent"] = max(entry["percent"], row.get("percent") or 0)
        return sorted(best.values(), key=lambda e: -e["percent"])[:6]

    def enrich_craft(self, node: Node) -> None:
        """Add spell-page detail: station, recipe item, and conversion inputs."""
        craft = node.craft
        if craft is None:
            return
        entity = self.spell_page(craft.spell_id)
        if entity is None:
            return

        if node.kind == "spell":
            node.tooltip = entity.tooltip_text
            node.effect = extract_effect(entity.tooltip_text)

        html = self.client.get(f"?spell={craft.spell_id}")
        station = STATION_RE.search(html)
        if station:
            craft.station = station.group(2)

        for ent_id, meta in entity.g_tables.get("g_items", {}).items():
            match = RECIPE_ITEM_RE.match(meta.get("name_enus", ""))
            if match and match.group(2) == craft.spell_name:
                craft.recipe_item_id = int(ent_id)
                craft.recipe_item_name = meta["name_enus"]
                break

        if craft.reagents:
            return

        # No reagent list: this is a station conversion.  Find the input by
        # asking each item the spell page mentions whether it feeds this spell.
        craft.craft_kind = "conversion"
        candidates = [
            int(i) for i in entity.g_tables.get("g_items", {})
            if int(i) not in (craft.creates_item_id, craft.recipe_item_id)
        ]
        for candidate in candidates:
            try:
                cand = aowow.fetch_entity(self.client, candidate, "item")
            except Exception:
                continue
            self.remember_names(cand)
            feeds = cand.listviews.get("reagent-for")
            if not feeds or not any(r["id"] == craft.spell_id for r in feeds.rows):
                continue
            craft.reagents = [[candidate, 1]]
            # The input item's own "Use:" line states the mechanism exactly, and
            # covers the cases that are not a workbench at all ("Feed to a
            # Carnivorous Clam", "Water an Ancient Elven Grave").
            for line in cand.tooltip_text.split("\n"):
                if line.startswith("Use:"):
                    craft.method = line[4:].strip()
                    break
            if not craft.station and craft.method:
                refined = REFINED_RE.search(cand.tooltip_text)
                if refined:
                    craft.station = refined.group(2).rstrip(". ")
            break

        if not craft.reagents:
            craft.reagents_resolved = False
            node.warnings.append(
                f"conversion spell {craft.spell_id} ({craft.spell_name}) exposes no input item")


# --- flattening -------------------------------------------------------------

def expandable(harvester: Harvester, node: Node) -> bool:
    return bool(node.craft and node.craft.reagents and node.is_custom)


def raw_materials(harvester: Harvester, node: Node, runs: float = 1.0,
                  depth: int = 0, path: frozenset[int] = frozenset()) -> dict[int, float]:
    """Materials needed to run ``node``'s recipe ``runs`` times, fully expanded."""
    craft = node.craft
    if craft is None or depth > 8 or node.node_id in path:
        return {node.node_id: runs}

    totals: dict[int, float] = defaultdict(float)
    for reagent_id, qty in craft.reagents:
        need = qty * runs
        child = harvester.nodes.get(("item", reagent_id))
        if child is None or not expandable(harvester, child):
            totals[reagent_id] += need
            continue
        child_runs = need / max(1, child.craft.yield_min)
        for mat_id, mat_qty in raw_materials(
            harvester, child, child_runs, depth + 1, path | {node.node_id}
        ).items():
            totals[mat_id] += mat_qty
    return dict(totals)


def render_tree(harvester: Harvester, node: Node, quantity: float | None = None,
                depth: int = 0, path: frozenset[int] = frozenset()) -> list[str]:
    indent = "    " * depth
    prefix = f"{quantity:g}x " if quantity is not None else ""
    label = f"{indent}- {prefix}{node.name or f'Item {node.node_id}'}"

    craft = node.craft
    if craft and depth == 0:
        bits = [craft.profession or "?"]
        if craft.learned_at:
            bits.append(str(craft.learned_at))
        label += f"  _({' '.join(bits)}"
        if craft.yield_min > 1:
            label += f", makes {craft.yield_min}"
        label += ")_"
    elif craft and expandable(harvester, node):
        if craft.craft_kind == "conversion" and craft.method:
            label += f"  _({craft.method})_"
        else:
            label += f"  _({craft.craft_kind}"
            if craft.profession:
                label += f", {craft.profession}"
            if craft.station:
                label += f" at {craft.station}"
            label += ")_"
    elif node.missing_from_db:
        label += "  _(not in database)_"
    elif node.obtained_from:
        label += f"  _({', '.join(node.obtained_from)})_"
    lines = [label]

    if craft and node.node_id not in path and (depth == 0 or expandable(harvester, node)):
        runs = 1.0 if depth == 0 else (quantity or 1) / max(1, craft.yield_min)
        for reagent_id, qty in craft.reagents:
            child = harvester.nodes.get(("item", reagent_id))
            if child is None:
                lines.append(f"{indent}    - {qty * runs:g}x Item {reagent_id}")
                continue
            lines += render_tree(harvester, child, qty * runs, depth + 1,
                                 path | {node.node_id})
    return lines


# --- output -----------------------------------------------------------------

def write_outputs(harvester: Harvester, seeds: list[Node], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    name_of = harvester.name_of

    (out_dir / "nodes.json").write_text(json.dumps({
        "source": "https://db.ascension.gg/",
        "scope": "AtlasLoot Ascension Edition -> Crafting -> High Risk (Classic)",
        "seed_count": len(seeds),
        "nodes": [asdict(n) for _, n in sorted(harvester.nodes.items())],
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    with (out_dir / "recipes.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["family", "is_seed", "product_kind", "product_id", "product_name",
                    "quality", "spell_id", "spell_name", "craft_kind", "profession",
                    "learned_at", "difficulty", "yield", "station", "method",
                    "recipe_item", "reagents", "effect"])
        for _, node in sorted(harvester.nodes.items(), key=lambda kv: (kv[1].family, kv[1].name)):
            c = node.craft
            if not c:
                continue
            w.writerow([
                node.family, int(node.is_seed), node.kind, node.node_id, node.name,
                node.quality if node.quality is not None else "",
                c.spell_id, c.spell_name, c.craft_kind, c.profession or "",
                c.learned_at or "", "/".join(map(str, c.difficulty)) if c.difficulty else "",
                c.yield_min if c.yield_min == c.yield_max else f"{c.yield_min}-{c.yield_max}",
                c.station or "", c.method or "", c.recipe_item_name or "",
                "; ".join(f"{q}x {name_of(i)}" for i, q in c.reagents),
                node.effect,
            ])

    with (out_dir / "reagents.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["product_id", "product_name", "family", "spell_id", "reagent_id",
                    "reagent_name", "quantity_per_craft", "reagent_is_custom",
                    "reagent_craftable", "reagent_sources"])
        for _, node in sorted(harvester.nodes.items(), key=lambda kv: (kv[1].family, kv[1].name)):
            if not node.craft:
                continue
            for reagent_id, qty in node.craft.reagents:
                child = harvester.nodes.get(("item", reagent_id))
                w.writerow([
                    node.node_id, node.name, node.family, node.craft.spell_id,
                    reagent_id, name_of(reagent_id), qty,
                    int(bool(child and child.is_custom)),
                    int(bool(child and expandable(harvester, child))),
                    ", ".join(child.obtained_from) if child else
                    ("not in database" if child and child.missing_from_db else ""),
                ])

    with (out_dir / "raw_materials.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["family", "product_id", "product_name", "yield_per_craft",
                    "material_id", "material_name", "quantity_per_craft",
                    "material_is_custom", "material_sources"])
        for node in seeds:
            if not node.craft:
                continue
            for mat_id, qty in sorted(raw_materials(harvester, node).items(), key=lambda kv: -kv[1]):
                child = harvester.nodes.get(("item", mat_id))
                w.writerow([
                    node.family, node.node_id, node.name, node.craft.yield_min,
                    mat_id, name_of(mat_id), f"{qty:g}",
                    int(bool(child and child.is_custom)),
                    ", ".join(child.obtained_from) if child else "",
                ])

    grand: dict[int, float] = defaultdict(float)
    for node in seeds:
        if node.craft:
            for mat_id, qty in raw_materials(harvester, node).items():
                grand[mat_id] += qty
    with (out_dir / "shopping_list.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["material_id", "material_name", "total_quantity",
                    "is_custom", "sources", "comment"])
        w.writerow(["", "# one craft of each of the %d High Risk recipes" % len(seeds),
                    "", "", "", ""])
        for mat_id, qty in sorted(grand.items(), key=lambda kv: -kv[1]):
            child = harvester.nodes.get(("item", mat_id))
            w.writerow([
                mat_id, name_of(mat_id), f"{qty:g}",
                int(bool(child and child.is_custom)),
                ", ".join(child.obtained_from) if child else "",
                "not in database" if child and child.missing_from_db else "",
            ])

    lines = [
        "# High Risk crafting trees (Classic)", "",
        "Scraped from <https://db.ascension.gg/>. Scope: AtlasLoot Ascension Edition ->",
        "Crafting -> High Risk, expansion Classic.", "",
        "Quantities are per **one execution** of the recipe; the yield is noted on the",
        "product line. Blizzard-era materials are leaves (farm or buy them); custom",
        "materials are expanded to their own source.", "",
    ]
    for family, title in (("flask", "Alchemy - Distilled Flasks"),
                          ("enchant", "Enchanting - High Risk Weapon Enchants"),
                          ("food", "Cooking - Fused Foods")):
        members = [n for n in seeds if n.family == family]
        if not members:
            continue
        lines += [f"## {title} ({len(members)})", ""]
        for node in members:
            lines += render_tree(harvester, node)
            if node.effect:
                lines.append(f"    > {node.effect}")
            if node.craft and node.craft.recipe_item_name:
                lines.append(f"    > Learned from: {node.craft.recipe_item_name}")
            lines.append("")
    (out_dir / "craft_tree.md").write_text("\n".join(lines), encoding="utf-8")

    # Anything the database itself could not answer, kept in one place so the
    # gaps are visible rather than buried in the log.
    gaps = ["# Data gaps", "",
            "Everything below is missing on db.ascension.gg, not in the scraper.", ""]
    missing = sorted((n for n in harvester.nodes.values() if n.missing_from_db),
                     key=lambda n: n.node_id)
    if missing:
        gaps += [f"## Reagents with no item page ({len(missing)})", "",
                 "These IDs appear in recipe reagent lists but `?item=<id>` returns 404,",
                 "so they have no name, icon or source. They render as blanks in the",
                 "in-game tooltip too.", "",
                 "| item ID | used by |", "| --- | --- |"]
        for node in missing:
            users = sorted({
                other.name for other in harvester.nodes.values()
                if other.craft and any(r[0] == node.node_id for r in other.craft.reagents)
            })
            gaps.append(f"| {node.node_id} | {', '.join(users)} |")
        gaps.append("")

    ambiguous = [n for n in harvester.nodes.values()
                 if any("spells create this item" in w for w in n.warnings)]
    if ambiguous:
        gaps += [f"## Materials with several sources ({len(ambiguous)})", "",
                 "More than one spell produces these; the first was recorded. All are",
                 "Blizzard-era materials treated as leaves, so this does not affect any",
                 "quantity above.", ""]
        gaps += [f"- {n.name} ({n.node_id})" for n in sorted(ambiguous, key=lambda n: n.name)]
        gaps.append("")

    gaps += ["## Tooltip variables", "",
             "Some enchant descriptions contain unresolved Aowow variables such as",
             "`$968734d` (a duration read from another spell). They are left verbatim",
             "rather than guessed at.", ""]
    (out_dir / "data_gaps.md").write_text("\n".join(gaps), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/highrisk"))
    parser.add_argument("--cache", type=Path, default=Path("cache"))
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")

    client = aowow.Client(args.cache, delay=args.delay)
    harvester = Harvester(client)

    LOG.info("Resolving seed recipes...")
    seeds = harvester.resolve_seeds()
    LOG.info("Seeds: %d flasks, %d enchants, %d foods",
             sum(n.family == "flask" for n in seeds),
             sum(n.family == "enchant" for n in seeds),
             sum(n.family == "food" for n in seeds))

    LOG.info("Expanding reagent trees...")
    harvester.expand(seeds)
    for (kind, node_id), node in harvester.nodes.items():
        if not node.name and kind == "item":
            node.name = harvester.names.get(node_id, f"Item {node_id}")

    materials = [n for n in harvester.nodes.values() if n.family == "material"]
    LOG.info("Graph: %d nodes, %d materials (%d custom, %d missing from DB)",
             len(harvester.nodes), len(materials),
             sum(n.is_custom for n in materials),
             sum(n.missing_from_db for n in materials))

    write_outputs(harvester, seeds, args.output)
    LOG.info("Wrote %s", args.output)

    problems = [n for n in harvester.nodes.values() if n.warnings]
    if problems:
        LOG.warning("%d nodes carry warnings:", len(problems))
        for node in problems:
            LOG.warning("  %s: %s", node.name or node.node_id, "; ".join(node.warnings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

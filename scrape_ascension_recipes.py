#!/usr/bin/env python3
"""Scrape craft recipes from db.ascension.gg static HTML pages.

The scraper is intentionally conservative:
- caches every response
- throttles requests
- retries transient failures
- extracts entity IDs from listing pages
- parses spell pages into normalized recipe records
- saves unparsed pages for inspection instead of fabricating data

Usage examples:
  python scrape_ascension_recipes.py discover --url 'https://db.ascension.gg/?spells=11.171'
  python scrape_ascension_recipes.py scrape --seed-file seeds.txt
  python scrape_ascension_recipes.py all --config config.json
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import random
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://db.ascension.gg/"
ENTITY_LINK_RE = re.compile(r"[?&](spell|item|skill|npc|object)=(\d+)", re.I)
COUNT_RE = re.compile(r"(?:\(|×|x\s*)(\d+)\)?\s*$", re.I)
SKILL_RE = re.compile(r"\b(Alchemy|Enchanting|Engineering|Cooking|First Aid|Blacksmithing|Tailoring|Leatherworking|Jewelcrafting|Inscription)\b", re.I)
DIFFICULTY_RE = re.compile(r"Difficulty:\s*([\d\s]+)", re.I)
CREATE_ITEM_RE = re.compile(r"Create Item:\s*(.+)", re.I)


@dataclass(slots=True)
class Reagent:
    item_id: int | None
    name: str
    quantity: int = 1


@dataclass(slots=True)
class Recipe:
    spell_id: int
    name: str
    profession: str | None = None
    required_skill: int | None = None
    output_item_id: int | None = None
    output_name: str | None = None
    output_quantity: int = 1
    reagents: list[Reagent] = field(default_factory=list)
    source_url: str = ""
    parse_warnings: list[str] = field(default_factory=list)


class CachedClient:
    def __init__(self, cache_dir: Path, delay: float, timeout: float, user_agent: str) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.delay = max(0.0, delay)
        self.timeout = timeout
        self.last_request_at = 0.0
        self.session = requests.Session()
        retries = Retry(
            total=5,
            connect=5,
            read=5,
            status=5,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retries))
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        })

    @staticmethod
    def _canonical_url(url: str) -> str:
        parsed = urlparse(urljoin(BASE_URL, url))
        query = parse_qs(parsed.query, keep_blank_values=True)
        normalized_query = urlencode(sorted((k, v) for k, values in query.items() for v in values))
        return urlunparse((parsed.scheme or "https", parsed.netloc or "db.ascension.gg", parsed.path or "/", "", normalized_query, ""))

    def _cache_path(self, url: str) -> Path:
        canonical = self._canonical_url(url)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.html"

    def get_text(self, url: str, refresh: bool = False) -> tuple[str, str]:
        canonical = self._canonical_url(url)
        cache_path = self._cache_path(canonical)
        if cache_path.exists() and not refresh:
            return cache_path.read_text(encoding="utf-8", errors="replace"), canonical

        elapsed = time.monotonic() - self.last_request_at
        wait = self.delay - elapsed
        if wait > 0:
            time.sleep(wait + random.uniform(0, min(0.25, self.delay / 4)))

        response = self.session.get(canonical, timeout=self.timeout)
        self.last_request_at = time.monotonic()
        response.raise_for_status()
        response.encoding = response.encoding or "utf-8"
        text = response.text
        cache_path.write_text(text, encoding="utf-8")
        return text, canonical


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def entity_id_from_href(href: str | None, entity: str) -> int | None:
    if not href:
        return None
    match = re.search(rf"[?&]{re.escape(entity)}=(\d+)", href, re.I)
    return int(match.group(1)) if match else None


def discover_links(html: str, page_url: str) -> dict[str, set[int]]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, set[int]] = {"spell": set(), "item": set(), "skill": set(), "npc": set(), "object": set()}
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "")
        for entity, value in ENTITY_LINK_RE.findall(href):
            found[entity.lower()].add(int(value))
    logging.info("Discovered from %s: %s", page_url, {k: len(v) for k, v in found.items()})
    return found


def find_section(soup: BeautifulSoup, heading_pattern: str) -> Tag | None:
    pattern = re.compile(heading_pattern, re.I)
    for heading in soup.find_all(re.compile(r"^h[1-6]$")):
        if pattern.search(clean_text(heading.get_text(" ", strip=True))):
            return heading
    # Some pages use div/tab labels instead of semantic headings.
    for candidate in soup.find_all(["div", "span", "th", "strong"]):
        text = clean_text(candidate.get_text(" ", strip=True))
        if len(text) <= 80 and pattern.fullmatch(text):
            return candidate
    return None


def section_nodes(heading: Tag) -> Iterable[Tag]:
    heading_level = int(heading.name[1]) if re.fullmatch(r"h[1-6]", heading.name or "") else None
    for sibling in heading.next_siblings:
        if not isinstance(sibling, Tag):
            continue
        if heading_level and re.fullmatch(r"h[1-6]", sibling.name or ""):
            if int(sibling.name[1]) <= heading_level:
                break
        yield sibling


def parse_quantity(text: str) -> tuple[str, int]:
    text = clean_text(text)
    match = COUNT_RE.search(text)
    if not match:
        return text, 1
    quantity = int(match.group(1))
    name = clean_text(text[:match.start()].rstrip(" (×x"))
    return name or text, quantity


def linked_items(nodes: Iterable[Tag]) -> list[tuple[int | None, str, int]]:
    results: list[tuple[int | None, str, int]] = []
    seen: set[tuple[int | None, str, int]] = set()
    for node in nodes:
        for anchor in node.find_all("a", href=True):
            item_id = entity_id_from_href(anchor.get("href"), "item")
            if item_id is None:
                continue
            raw = clean_text(anchor.get_text(" ", strip=True))
            if not raw:
                continue
            name, quantity = parse_quantity(raw)
            # Quantity is frequently outside the anchor in a parent table cell.
            parent_text = clean_text(anchor.parent.get_text(" ", strip=True)) if anchor.parent else raw
            parent_name, parent_quantity = parse_quantity(parent_text)
            if parent_quantity > 1 and raw in parent_text:
                quantity = parent_quantity
            key = (item_id, name, quantity)
            if key not in seen:
                seen.add(key)
                results.append(key)
    return results


def parse_recipe_spell(html: str, url: str, spell_id: int) -> Recipe:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    title = clean_text(h1.get_text(" ", strip=True)) if h1 else ""
    if not title and soup.title:
        title = clean_text(soup.title.get_text(" ", strip=True).split(" - ")[0])
    recipe = Recipe(spell_id=spell_id, name=title or f"Spell {spell_id}", source_url=url)

    whole_text = clean_text(soup.get_text(" ", strip=True))
    skill_match = SKILL_RE.search(whole_text)
    if skill_match:
        recipe.profession = skill_match.group(1).title()

    difficulty_match = DIFFICULTY_RE.search(whole_text)
    if difficulty_match:
        values = re.findall(r"\d+", difficulty_match.group(1))
        if values:
            recipe.required_skill = int(values[0])
    elif recipe.profession:
        nearby = re.search(rf"{re.escape(recipe.profession)}\s*\((\d+)\)", whole_text, re.I)
        if nearby:
            recipe.required_skill = int(nearby.group(1))

    reagents_heading = find_section(soup, r"Reagents?")
    if reagents_heading:
        reagent_links = linked_items(section_nodes(reagents_heading))
        recipe.reagents = [Reagent(item_id=i, name=n, quantity=q) for i, n, q in reagent_links]
    else:
        recipe.parse_warnings.append("No reagent section found")

    # Prefer links near an explicit Create Item effect.
    output_candidates: list[tuple[int | None, str, int]] = []
    for element in soup.find_all(string=re.compile(r"Create Item", re.I)):
        parent = element.parent if isinstance(element.parent, Tag) else None
        if parent:
            neighborhood = [parent]
            if parent.parent and isinstance(parent.parent, Tag):
                neighborhood.append(parent.parent)
            output_candidates.extend(linked_items(neighborhood))

    # Fall back to links in Spell Details, excluding known reagents.
    if not output_candidates:
        details_heading = find_section(soup, r"Spell Details")
        if details_heading:
            output_candidates = linked_items(section_nodes(details_heading))

    reagent_ids = {r.item_id for r in recipe.reagents if r.item_id is not None}
    output_candidates = [entry for entry in output_candidates if entry[0] not in reagent_ids]
    if output_candidates:
        item_id, output_name, quantity = output_candidates[0]
        recipe.output_item_id = item_id
        recipe.output_name = output_name
        recipe.output_quantity = quantity
    else:
        text_match = CREATE_ITEM_RE.search(whole_text)
        if text_match:
            output_name, quantity = parse_quantity(text_match.group(1).split(" Spell Details")[0])
            recipe.output_name = output_name
            recipe.output_quantity = quantity
            recipe.parse_warnings.append("Output item ID not found")
        else:
            recipe.parse_warnings.append("No created item found")

    return recipe


def paginate_urls(first_url: str, html: str) -> list[str]:
    """Return explicit pagination URLs found in a listing page.

    The function intentionally follows only same-host links that preserve a list-like
    query and include page/start parameters. This prevents wandering across the DB.
    """
    soup = BeautifulSoup(html, "html.parser")
    urls = {first_url}
    for anchor in soup.find_all("a", href=True):
        href = urljoin(first_url, anchor["href"])
        parsed = urlparse(href)
        if parsed.netloc and parsed.netloc != "db.ascension.gg":
            continue
        query = parse_qs(parsed.query)
        if any(key in query for key in ("page", "start", "offset")) and any(key in query for key in ("spells", "items", "skill")):
            urls.add(href)
    return sorted(urls)


def write_outputs(recipes: list[Recipe], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "recipes.json"
    json_path.write_text(json.dumps([asdict(r) for r in recipes], indent=2, ensure_ascii=False), encoding="utf-8")

    with (output_dir / "recipes.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "spell_id", "name", "profession", "required_skill", "output_item_id",
            "output_name", "output_quantity", "reagents_json", "source_url", "parse_warnings",
        ])
        writer.writeheader()
        for recipe in recipes:
            writer.writerow({
                "spell_id": recipe.spell_id,
                "name": recipe.name,
                "profession": recipe.profession or "",
                "required_skill": recipe.required_skill or "",
                "output_item_id": recipe.output_item_id or "",
                "output_name": recipe.output_name or "",
                "output_quantity": recipe.output_quantity,
                "reagents_json": json.dumps([asdict(r) for r in recipe.reagents], ensure_ascii=False),
                "source_url": recipe.source_url,
                "parse_warnings": " | ".join(recipe.parse_warnings),
            })

    with (output_dir / "reagents.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["spell_id", "recipe_name", "reagent_item_id", "reagent_name", "quantity"])
        writer.writeheader()
        for recipe in recipes:
            for reagent in recipe.reagents:
                writer.writerow({
                    "spell_id": recipe.spell_id,
                    "recipe_name": recipe.name,
                    "reagent_item_id": reagent.item_id or "",
                    "reagent_name": reagent.name,
                    "quantity": reagent.quantity,
                })

    group_items: dict[str, list[str]] = {}
    for recipe in recipes:
        if not recipe.output_item_id:
            continue
        profession = recipe.profession or "Unknown"
        name = (recipe.output_name or recipe.name).lower()
        if "flask" in name:
            category = "Flasks"
        elif "elixir" in name:
            category = "Elixirs"
        elif "potion" in name:
            category = "Potions"
        elif "scroll" in name:
            category = "Scrolls"
        elif any(word in name for word in ("oil", "stone", "weightstone", "sharpen")):
            category = "Weapon Buffs"
        elif profession == "Enchanting" or "enchant" in name:
            category = "Enchants"
        elif profession == "Cooking":
            category = "Food & Drink"
        else:
            category = "Other"
        group_items.setdefault(f"CoA`{profession}`{category}", []).append(f"i:{recipe.output_item_id}")

    with (output_dir / "tsm_groups.txt").open("w", encoding="utf-8") as handle:
        for path, items in sorted(group_items.items()):
            unique = sorted(set(items), key=lambda x: int(x.split(":", 1)[1]))
            handle.write(f"[{path}]\n")
            handle.write(",".join(unique) + "\n\n")


def read_seed_urls(path: Path) -> list[str]:
    urls = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        value = raw.strip()
        if value and not value.startswith("#"):
            urls.append(value)
    return urls


def load_config(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Config root must be a JSON object")
    return data


def run_discovery(client: CachedClient, seed_urls: list[str], refresh: bool) -> set[int]:
    spell_ids: set[int] = set()
    visited: set[str] = set()
    pending = list(seed_urls)
    while pending:
        page_url = pending.pop(0)
        if page_url in visited:
            continue
        visited.add(page_url)
        html, canonical = client.get_text(page_url, refresh=refresh)
        links = discover_links(html, canonical)
        spell_ids.update(links["spell"])
        for page in paginate_urls(canonical, html):
            if page not in visited:
                pending.append(page)
    return spell_ids


def run_scrape(client: CachedClient, spell_ids: Iterable[int], output_dir: Path, refresh: bool) -> list[Recipe]:
    recipes: list[Recipe] = []
    failed_dir = output_dir / "unparsed"
    failed_dir.mkdir(parents=True, exist_ok=True)
    ids = sorted(set(spell_ids))
    for index, spell_id in enumerate(ids, start=1):
        url = f"{BASE_URL}?spell={spell_id}"
        logging.info("[%d/%d] %s", index, len(ids), url)
        try:
            html, canonical = client.get_text(url, refresh=refresh)
            recipe = parse_recipe_spell(html, canonical, spell_id)
            if not recipe.reagents or not recipe.output_name:
                (failed_dir / f"spell_{spell_id}.html").write_text(html, encoding="utf-8")
            recipes.append(recipe)
        except Exception as exc:  # Continue a large crawl, but record the failure.
            logging.exception("Failed spell %s", spell_id)
            recipes.append(Recipe(spell_id=spell_id, name=f"Spell {spell_id}", source_url=url, parse_warnings=[f"Fetch/parse failure: {exc}"]))
        if index % 25 == 0:
            write_outputs(recipes, output_dir)
    write_outputs(recipes, output_dir)
    return recipes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("discover", "scrape", "all"))
    parser.add_argument("--url", action="append", default=[], help="Listing URL; may be repeated")
    parser.add_argument("--seed-file", type=Path, help="Text file containing listing URLs or spell-page URLs")
    parser.add_argument("--config", type=Path, help="JSON configuration file")
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--cache", type=Path, default=Path("cache"))
    parser.add_argument("--delay", type=float, default=1.5, help="Minimum seconds between uncached requests")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--refresh", action="store_true", help="Ignore cached pages")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    config = load_config(args.config) if args.config else {}
    seed_urls = list(config.get("seed_urls", [])) + list(args.url)
    if args.seed_file:
        seed_urls.extend(read_seed_urls(args.seed_file))

    user_agent = config.get("user_agent", "AscensionRecipeResearch/1.0 (+personal market analysis; respectful caching)")
    client = CachedClient(args.cache, float(config.get("delay", args.delay)), float(config.get("timeout", args.timeout)), user_agent)

    direct_spell_ids: set[int] = set()
    listing_urls: list[str] = []
    for url in seed_urls:
        spell_id = entity_id_from_href(url, "spell")
        if spell_id is not None and "spells=" not in url:
            direct_spell_ids.add(spell_id)
        else:
            listing_urls.append(url)

    discovered: set[int] = set()
    if args.command in ("discover", "all"):
        if not listing_urls:
            raise SystemExit("No listing URLs supplied. Use --url, --seed-file, or --config.")
        discovered = run_discovery(client, listing_urls, args.refresh)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "spell_ids.txt").write_text("\n".join(map(str, sorted(discovered))) + "\n", encoding="utf-8")
        logging.info("Wrote %d spell IDs", len(discovered))

    if args.command in ("scrape", "all"):
        ids = direct_spell_ids | discovered
        ids_file = args.output / "spell_ids.txt"
        if not ids and ids_file.exists():
            ids.update(int(line.strip()) for line in ids_file.read_text().splitlines() if line.strip().isdigit())
        if not ids:
            raise SystemExit("No spell IDs available to scrape.")
        run_scrape(client, ids, args.output, args.refresh)

    return 0


if __name__ == "__main__":
    sys.exit(main())

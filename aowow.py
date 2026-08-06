#!/usr/bin/env python3
"""Minimal client for the Aowow-powered Ascension database (db.ascension.gg).

Aowow renders listing pages server-side and embeds the full result set as a
JavaScript ``new Listview({...})`` call.  This module fetches such pages (with
on-disk caching and throttling) and recovers the embedded rows.

Two facts drive the design:

* A listview is capped server-side (currently 1000 rows).  The call carries
  ``"note": $WH.sprintf(LANG.lvnote_itemsfound, <total>, <shown>)`` and
  ``"_truncated": 1`` when that happens, so truncation is always detectable and
  never silently swallowed.
* The embedded object is JavaScript, not JSON: it contains bare identifiers such
  as ``LANG.tab_items`` and calls like ``$WH.sprintf(...)``.  Those are replaced
  with string placeholders before JSON parsing.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://db.ascension.gg/"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Highest item entry shipped by Blizzard in 3.3.5a.  Everything above this is
# server-authored content.  Verified against the live DB in scan_report.md.
BLIZZARD_MAX_ITEM_ID = 56815
BLIZZARD_MAX_SPELL_ID = 80000

LOG = logging.getLogger("aowow")


class Client:
    """Throttled, cached HTTP client."""

    def __init__(
        self,
        cache_dir: Path,
        delay: float = 1.0,
        timeout: float = 45.0,
        user_agent: str = DEFAULT_UA,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.delay = max(0.0, delay)
        self.timeout = timeout
        self._last_request_at = 0.0
        self.session = requests.Session()
        retries = Retry(
            total=5,
            connect=5,
            read=5,
            status=5,
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retries))
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

    @staticmethod
    def canonical(url: str) -> str:
        """Normalise a URL without re-encoding it.

        Aowow reads the raw query string positionally: the page selector
        (``items=0.6``) must stay first, and its filter syntax packs ``=`` and
        ``;`` inside the ``filter`` value (``filter=qu=4;minle=1``).  Reordering
        or percent-encoding either one makes the server return 404, so the query
        is passed through untouched.
        """
        parsed = urlparse(urljoin(BASE_URL, url))
        return urlunparse(
            (
                parsed.scheme or "https",
                parsed.netloc or "db.ascension.gg",
                parsed.path or "/",
                "",
                parsed.query,
                "",
            )
        )

    def _cache_path(self, canonical_url: str) -> Path:
        digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:32]
        return self.cache_dir / f"{digest}.html"

    def get(self, url: str, refresh: bool = False) -> str:
        canonical_url = self.canonical(url)
        path = self._cache_path(canonical_url)
        if path.exists() and not refresh:
            return path.read_text(encoding="utf-8", errors="replace")

        wait = self.delay - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait + random.uniform(0.0, min(0.3, self.delay / 3)))

        LOG.debug("GET %s", canonical_url)
        response = self.session.get(canonical_url, timeout=self.timeout)
        self._last_request_at = time.monotonic()
        response.raise_for_status()
        response.encoding = response.encoding or "utf-8"
        text = response.text
        path.write_text(text, encoding="utf-8")
        return text


# --- Listview extraction ----------------------------------------------------

_NOTE_COUNT_RE = re.compile(r"lvnote_\w+\s*,\s*(\d+)\s*,\s*(\d+)")
_IDENT_START = re.compile(r"[A-Za-z_$]")
_IDENT_PATH = re.compile(r"[A-Za-z_$][A-Za-z0-9_$.]*")
_JSON_KEYWORDS = {"true", "false", "null"}


@dataclass
class Listview:
    template: str
    lv_id: str
    rows: list[dict]
    total: int | None = None          # rows matching the query server-side
    shown: int | None = None          # rows actually embedded
    truncated: bool = False
    source_url: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """True when the page returned every row the query matched."""
        if self.truncated:
            return False
        if self.total is not None and self.shown is not None:
            return self.total == self.shown
        return True


def _match_braces(text: str, start: int) -> int:
    """Return the index just past the object literal opening at ``start``.

    Tracks string state so braces inside strings do not confuse the scan.
    """
    depth = 0
    i = start
    in_str: str | None = None
    escaped = False
    while i < len(text):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == in_str:
                in_str = None
        elif ch in "\"'":
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced braces in Listview literal")


def _js_object_to_json(blob: str) -> dict:
    """Convert an Aowow Listview object literal into a JSON-parsable string.

    Scans character by character so that replacements never touch the inside of
    a string literal -- item tooltips routinely contain commas, braces and
    apostrophes that would otherwise be mistaken for syntax.  Outside strings,
    any bare identifier (``LANG.tab_items``) or call (``$WH.sprintf(...)``) is
    replaced by a quoted placeholder that preserves its original text.
    """
    out: list[str] = []
    i = 0
    n = len(blob)
    while i < n:
        ch = blob[i]

        if ch in "\"'":
            quote = ch
            j = i + 1
            escaped = False
            while j < n:
                if escaped:
                    escaped = False
                elif blob[j] == "\\":
                    escaped = True
                elif blob[j] == quote:
                    break
                j += 1
            literal = blob[i + 1:j]
            # Aowow emits both quoting styles; normalise to a JSON string.
            if quote == "'":
                out.append(json.dumps(literal.replace("\\'", "'")))
            else:
                out.append(blob[i:j + 1])
            i = j + 1
            continue

        if _IDENT_START.match(ch):
            m = _IDENT_PATH.match(blob, i)
            name = m.group(0)
            j = m.end()
            if name in _JSON_KEYWORDS:
                out.append(name)
                i = j
                continue
            # Consume any trailing call or subscript accessors so that
            # expressions like `$WH.sprintf(...)` and `LANG.types[19][2]`
            # collapse into a single placeholder rather than leaving stray
            # brackets behind as invalid JSON.
            k = j
            while True:
                while k < n and blob[k].isspace():
                    k += 1
                if k >= n or blob[k] not in "([":
                    break
                opener, closer = ("(", ")") if blob[k] == "(" else ("[", "]")
                depth = 0
                while k < n:
                    if blob[k] == opener:
                        depth += 1
                    elif blob[k] == closer:
                        depth -= 1
                        if depth == 0:
                            k += 1
                            break
                    k += 1
                j = k
            out.append(json.dumps(name if j == m.end() else blob[i:j]))
            i = j
            continue

        out.append(ch)
        i += 1

    return json.loads("".join(out))


def extract_listviews(html: str, source_url: str = "") -> list[Listview]:
    """Return every ``new Listview({...})`` payload embedded in ``html``."""
    views: list[Listview] = []
    for m in re.finditer(r"new\s+Listview\s*\(\s*(?=\{)", html):
        start = m.end()
        try:
            end = _match_braces(html, start)
            obj = _js_object_to_json(html[start:end])
        except (ValueError, json.JSONDecodeError) as exc:
            LOG.warning("Could not decode a Listview in %s: %s", source_url, exc)
            continue

        data = obj.get("data")
        if not isinstance(data, list):
            continue  # e.g. comments/screenshots views that reference a variable

        view = Listview(
            template=str(obj.get("template", "")),
            lv_id=str(obj.get("id", "")),
            rows=data,
            truncated=bool(obj.get("_truncated")),
            shown=len(data),
            source_url=source_url,
        )
        note = obj.get("note")
        if isinstance(note, str):
            found = _NOTE_COUNT_RE.search(note)
            if found:
                view.total = int(found.group(1))
                view.shown = int(found.group(2))
        if view.truncated and view.total is None:
            view.warnings.append("truncated but no count note found")
        views.append(view)
    return views


def fetch_listview(
    client: Client,
    url: str,
    template: str | None = None,
    lv_id: str | None = None,
    refresh: bool = False,
) -> Listview | None:
    """Fetch ``url`` and return the first listview matching template/id."""
    html = client.get(url, refresh=refresh)
    canonical_url = client.canonical(url)
    for view in extract_listviews(html, canonical_url):
        if template and view.template != template:
            continue
        if lv_id and view.lv_id != lv_id:
            continue
        return view
    return None


def strip_name_prefix(name: str) -> tuple[str, int | None]:
    """Aowow prefixes list names with a quality digit, e.g. ``"4Flask of X"``.

    Trade-skill spell names carry an extra ``@`` marker after that digit.
    """
    quality = None
    if name and name[0].isdigit():
        quality = int(name[0])
        name = name[1:]
    return name.lstrip("@"), quality


# --- Detail pages -----------------------------------------------------------

_G_ENTITY_RE = re.compile(
    r"var\s+_\s*=\s*(g_\w+);(.*?)(?=var\s+_\s*=\s*g_\w+;|</script>)", re.S
)
_G_ASSIGN_RE = re.compile(r"_\[('?)([\w']+?)\1\]\s*=\s*(\{)")
_TOOLTIP_RE = re.compile(r"_\[(\d+)\]\.tooltip_\w+\s*=\s*(\")")
_TAG_RE = re.compile(r"<[^>]+>")


def _read_js_string(text: str, start: int) -> tuple[str, int]:
    """Read the JS string literal beginning at ``start`` (a quote char)."""
    quote = text[start]
    i = start + 1
    escaped = False
    while i < len(text):
        if escaped:
            escaped = False
        elif text[i] == "\\":
            escaped = True
        elif text[i] == quote:
            break
        i += 1
    raw = text[start:i + 1]
    if quote == "'":
        raw = '"' + raw[1:-1].replace('\\"', '"').replace("\\'", "'").replace('"', '\\"') + '"'
    return json.loads(raw), i + 1


def parse_g_entities(html: str) -> dict[str, dict[str, dict]]:
    """Recover Aowow's ``g_items`` / ``g_spells`` / ... lookup tables.

    These blocks name every entity the page references, which is how a reagent
    ID becomes a reagent name without an extra request.
    """
    tables: dict[str, dict[str, dict]] = {}
    for match in _G_ENTITY_RE.finditer(html):
        table_name, body = match.group(1), match.group(2)
        bucket = tables.setdefault(table_name, {})
        for assign in _G_ASSIGN_RE.finditer(body):
            key = assign.group(2)
            try:
                end = _match_braces(body, assign.start(3))
                bucket[key] = _js_object_to_json(body[assign.start(3):end])
            except (ValueError, json.JSONDecodeError):
                continue
    return tables


def tooltip_to_text(tooltip_html: str) -> str:
    """Flatten a tooltip HTML fragment into readable plain text."""
    text = re.sub(r"<br\s*/?>", "\n", tooltip_html, flags=re.I)
    text = re.sub(r"</(tr|table|div|p)>", "\n", text, flags=re.I)
    text = _TAG_RE.sub("", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#039;", "'")
    )
    lines = [ln.strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


@dataclass
class Entity:
    """An item or spell detail page."""

    entity_id: int
    kind: str                     # "item" or "spell"
    name: str = ""
    quality: int | None = None
    icon: str | None = None
    tooltip_html: str = ""
    tooltip_text: str = ""
    listviews: dict[str, Listview] = field(default_factory=dict)
    g_tables: dict[str, dict[str, dict]] = field(default_factory=dict)
    source_url: str = ""

    def named(self, table: str, entity_id: int | str) -> str:
        entry = self.g_tables.get(table, {}).get(str(entity_id))
        return entry.get("name_enus", "") if entry else ""


def parse_entity_page(html: str, entity_id: int, kind: str, url: str = "") -> Entity:
    """Parse an ``?item=<id>`` or ``?spell=<id>`` page."""
    entity = Entity(entity_id=entity_id, kind=kind, source_url=url)
    entity.g_tables = parse_g_entities(html)

    table = "g_items" if kind == "item" else "g_spells"
    own = entity.g_tables.get(table, {}).get(str(entity_id), {})
    entity.name = own.get("name_enus", "")
    entity.quality = own.get("quality")
    entity.icon = own.get("icon")

    if not entity.name:
        heading = re.search(r"<h1>(.*?)</h1>", html, re.S)
        if heading:
            entity.name = tooltip_to_text(heading.group(1))

    for match in _TOOLTIP_RE.finditer(html):
        if int(match.group(1)) != entity_id:
            continue
        value, _ = _read_js_string(html, match.start(2))
        entity.tooltip_html = value
        entity.tooltip_text = tooltip_to_text(value)
        break

    for view in extract_listviews(html, url):
        if view.lv_id:
            entity.listviews[view.lv_id] = view
    return entity


def fetch_entity(client: Client, entity_id: int, kind: str, refresh: bool = False) -> Entity:
    url = f"?{kind}={entity_id}"
    html = client.get(url, refresh=refresh)
    return parse_entity_page(html, entity_id, kind, client.canonical(url))

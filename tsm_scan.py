#!/usr/bin/env python3
"""Read auction prices straight out of TSM's AuctionDB SavedVariables.

Ascension ships TSM3, which stores a whole realm scan as one encoded string in
``AscensionTSM_AuctionDB.lua``.  Nothing in that file is documented, so the
format below was derived from the data and then checked against independent
evidence:

* Item IDs decode to real items (7080 Essence of Water, 967468 Distilled Flask
  of the Executioner, 8838 Sungrass, 13463 Dreamfoil).
* The decoded minimum buyout for Essence of Water is 199500 copper against
  Auctionator's independently recorded 199000 -- a 0.25% difference.
* The per-item price history is keyed by day number, and those keys decode to a
  contiguous 14-day window ending on the scan date, which is exactly the window
  TSM uses to compute market value.
* ``marketValue / mean(price history)`` has a median of 1.000 across the 12712
  items that carry history.

Encoding: base-64, big-endian, over the alphabet below.  A record is
``?<itemId>,~,<marketValue>,<lastScan>,~,<minBuyout>,<history>,<quantity>``
where ``~`` is a structural delimiter present in every record, and history is
``<dayKey>:<value>[@<qty>]`` joined by ``!``.

Run directly to inspect what a file contains::

    python tsm_scan.py --list
    python tsm_scan.py --realm "Rexxar - Conquest of Azeroth"
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

# The final two characters are '_' then '=', not the other way round. Deriving
# them from a timestamp was ambiguous (both orderings put the scan within a
# minute of lastCompleteScan); item IDs settle it, because only this ordering
# makes ids like 1303711 (Blightroot Extract) appear in the scan at all.
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_="
_INDEX = {c: i for i, c in enumerate(ALPHABET)}
_TOKEN_RE = re.compile(rf"^[{re.escape(ALPHABET)}]+$")

# Where the Ascension launcher keeps the game, in likely order.
DEFAULT_ROOTS = [
    Path(r"C:/Games/Ascension/Launcher/resources/ascension-live"),
    Path(r"C:/Program Files/Ascension/Launcher/resources/ascension-live"),
    Path(r"C:/Program Files (x86)/Ascension/Launcher/resources/ascension-live"),
]


class DecodeError(ValueError):
    pass


def decode(token: str) -> int:
    """Decode one base-64 token. Raises rather than guessing on bad input."""
    if not token or not _TOKEN_RE.match(token):
        raise DecodeError(f"not a value token: {token!r}")
    value = 0
    for char in token:
        value = value * 64 + _INDEX[char]
    return value


# A buyout below this fraction of market value is treated as an outlier.
ROBUST_FLOOR = 0.5


@dataclass
class Price:
    item_id: int
    market_value: int | None       # TSM's 14-day smoothed value
    min_buyout: int | None         # cheapest buyout at the last scan
    quantity: int | None
    last_scan: int | None
    history_days: int = 0

    def pick(self, source: str, floor: float = ROBUST_FLOOR) -> int | None:
        """The price to use for this item under a given basis.

        ``robust`` exists because the cheapest listing is often a single
        mispriced unit -- one seller posting at 24s while the item trades at
        22g -- and taking that at face value makes a craft look absurdly
        profitable. TSM's AuctionDB stores only aggregates per item, never the
        individual auctions, so the cheapest N listings cannot be averaged;
        instead the buyout is refused when it sits below ``floor`` of the
        14-day market value, which is what that outlier actually looks like.
        """
        if source == "minbuyout":
            return self.min_buyout or self.market_value
        if source == "market":
            return self.market_value or self.min_buyout
        if source == "robust":
            if self.min_buyout and self.market_value:
                return max(self.min_buyout, int(self.market_value * floor))
            return self.min_buyout or self.market_value
        raise ValueError(f"unknown price source {source!r}")


@dataclass
class RealmScan:
    realm: str
    last_complete_scan: int | None
    prices: dict[int, Price]
    malformed: int = 0

    @property
    def scanned_at(self) -> str:
        if not self.last_complete_scan:
            return "unknown"
        return dt.datetime.fromtimestamp(self.last_complete_scan).strftime("%Y-%m-%d %H:%M")

    @property
    def age_days(self) -> float | None:
        if not self.last_complete_scan:
            return None
        return (dt.datetime.now().timestamp() - self.last_complete_scan) / 86400


SHARING_APP_CACHE = "update_times.json"
SHARING_APP_HINTS = [
    Path.home() / "Desktop",
    Path.home() / "Downloads",
    Path.home() / "Documents",
]


def find_sharing_cache(explicit: Path | None = None) -> list[Path]:
    """Locate the Ascension TSM Data Sharing App's local cache.

    The app keeps the pooled scan it downloaded in ``update_times.json`` next to
    its executable, under ``latest_data``.  Reading that file works whether or
    not Ascension is running -- unlike the WTF copy, which the game rewrites on
    every /reload and which the app may only touch while the game is closed.
    """
    if explicit:
        return [explicit] if explicit.exists() else []
    found: list[Path] = []
    for base in SHARING_APP_HINTS:
        if not base.exists():
            continue
        candidate = base / SHARING_APP_CACHE
        if candidate.exists():
            found.append(candidate)
        found.extend(sorted(base.glob(f"*/{SHARING_APP_CACHE}")))
    return found


def read_sharing_cache(path: Path, realm: str | None = None) -> list[RealmScan]:
    """Parse pooled scans out of the sharing app's JSON cache."""
    import json

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    scans: list[RealmScan] = []
    for entry in data.get("latest_data", []):
        name = entry.get("realm")
        if not name or (realm and name != realm):
            continue
        prices, malformed = parse_scan_data(entry.get("scan_data") or "")
        scans.append(RealmScan(
            realm=name,
            last_complete_scan=entry.get("last_complete_scan"),
            prices=prices,
            malformed=malformed,
        ))
    return scans


def find_auctiondb(root: Path | None = None) -> list[Path]:
    """Locate TradeSkillMaster_AuctionDB.lua files under a client directory."""
    roots = [root] if root else DEFAULT_ROOTS
    found: list[Path] = []
    for base in roots:
        if not base or not base.exists():
            continue
        found.extend(sorted(base.glob("WTF/Account/*/SavedVariables/TradeSkillMaster_AuctionDB.lua")))
        if base.name == "TradeSkillMaster_AuctionDB.lua":
            found.append(base)
    return found


def _extract_realm_blocks(text: str) -> dict[str, dict[str, str]]:
    """Pull each realm's raw fields out of the Lua table without executing it."""
    realms: dict[str, dict[str, str]] = {}
    start = text.find('["realm"]')
    if start < 0:
        return realms
    for match in re.finditer(r'\["([^"]+ - [^"]+)"\]\s*=\s*\{', text[start:]):
        name = match.group(1)
        chunk = text[start + match.end(): start + match.end() + 4_000_000]
        block: dict[str, str] = {}
        scan = re.search(r'\["scanData"\]\s*=\s*"([^"]*)"', chunk)
        stamp = re.search(r'\["lastCompleteScan"\]\s*=\s*(\d+)', chunk)
        if scan:
            block["scanData"] = scan.group(1)
        if stamp:
            block["lastCompleteScan"] = stamp.group(1)
        if block:
            realms[name] = block
    return realms


def parse_scan_data(scan_data: str) -> tuple[dict[int, Price], int]:
    prices: dict[int, Price] = {}
    malformed = 0
    for record in scan_data.split("?"):
        if not record:
            continue
        fields = record.split(",")
        if len(fields) != 8:
            malformed += 1
            continue
        try:
            item_id = decode(fields[0])
        except DecodeError:
            malformed += 1
            continue

        def maybe(index: int) -> int | None:
            token = fields[index]
            if token == "~" or not token:
                return None
            try:
                return decode(token)
            except DecodeError:
                return None

        history_days = 0
        if fields[6] and fields[6] != "~":
            history_days = sum(1 for part in fields[6].split("!") if ":" in part)

        prices[item_id] = Price(
            item_id=item_id,
            market_value=maybe(2),
            min_buyout=maybe(5),
            quantity=maybe(7),
            last_scan=maybe(3),
            history_days=history_days,
        )
    return prices, malformed


def read(path: Path, realm: str | None = None) -> list[RealmScan]:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    scans: list[RealmScan] = []
    for name, block in _extract_realm_blocks(text).items():
        if realm and name != realm:
            continue
        prices, malformed = parse_scan_data(block.get("scanData", ""))
        stamp = block.get("lastCompleteScan")
        scans.append(RealmScan(
            realm=name,
            last_complete_scan=int(stamp) if stamp else None,
            prices=prices,
            malformed=malformed,
        ))
    return scans


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", type=Path, help="TradeSkillMaster_AuctionDB.lua")
    parser.add_argument("--root", type=Path, help="Ascension client directory")
    parser.add_argument("--realm", help="realm name to inspect")
    parser.add_argument("--list", action="store_true", help="list realms and exit")
    parser.add_argument("--item", type=int, action="append", default=[],
                        help="show the price of an item ID; may be repeated")
    args = parser.parse_args()

    paths = [args.file] if args.file else find_auctiondb(args.root)
    if not paths:
        print("No TradeSkillMaster_AuctionDB.lua found. Pass --file or --root.")
        return 1

    for path in paths:
        print(f"\n{path}")
        for scan in read(path, args.realm):
            age = scan.age_days
            print(f"  {scan.realm:<38} {len(scan.prices):>6} items  "
                  f"scanned {scan.scanned_at}"
                  f"{f' ({age:.1f} days ago)' if age is not None else ''}"
                  f"{f'  [{scan.malformed} malformed]' if scan.malformed else ''}")
            for item_id in args.item:
                price = scan.prices.get(item_id)
                print(f"      item {item_id}: "
                      + ("not on the auction house" if not price else
                         f"market={price.market_value} minBuyout={price.min_buyout} "
                         f"qty={price.quantity} history={price.history_days}d"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

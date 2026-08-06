#!/usr/bin/env python3
"""Extract real item icons from the Ascension client into ``site/assets/icons``.

db.ascension.gg has no icon at all for many custom items, and the icon it does
report is sometimes wrong -- it calls the Distilled flasks a leather chest.  The
client is the same source AtlasLoot and TSM read, so the icons come from there:

    item id --> displayid --> ItemDisplayInfo.dbc --> Interface/Icons/<name>.blp

``displayid`` comes from the website's item listings (which expose it for every
item) with the client's own ``itemcache.wdb`` used first where it has an entry.
The DBC lives in ``patch-M.MPQ`` and the icon art in the other patch archives;
later patches win, so archives are searched newest first.

    python icons.py                # -> output/site/assets/icons/*.png + icons.json
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import re
import struct
from pathlib import Path

import blp
import coadata

LOG = logging.getLogger("icons")

CLIENT = Path("C:/Games/Ascension/Launcher/resources/ascension-live")
DBC_PATH = r"DBFilesClient\ItemDisplayInfo.dbc"
ICON_FIELD = 5                      # InventoryIcon[0] in ItemDisplayInfo
SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")


def read_itemcache(path: Path) -> dict[int, int]:
    """item id -> displayid, from the client's own cache of items it has seen."""
    if not path.exists():
        return {}
    data = path.read_bytes()
    if data[:4] != b"BDIW":
        LOG.warning("%s is not an itemcache WDB", path)
        return {}
    out: dict[int, int] = {}
    offset = 24
    while offset + 8 <= len(data):
        entry, size = struct.unpack("<II", data[offset:offset + 8])
        offset += 8
        if size == 0:
            break
        payload = data[offset:offset + size]
        offset += size
        try:
            pos = 12                                   # class, subclass, sound override
            for _ in range(4):                         # four name strings
                pos = payload.index(b"\0", pos) + 1
            out[entry] = struct.unpack("<I", payload[pos:pos + 4])[0]
        except (ValueError, struct.error):
            continue
    return out


def read_display_icons(archives: list[Path]) -> dict[int, str]:
    """displayid -> icon base name, from ItemDisplayInfo.dbc."""
    from mpyq import MPQArchive

    for path in archives:
        try:
            data = MPQArchive(str(path)).read_file(DBC_PATH)
        except Exception:
            continue
        if not data or data[:4] != b"WDBC":
            continue
        rows, fields, rsize, _ = struct.unpack("<IIII", data[4:20])
        records = data[20:20 + rows * rsize]
        strings = data[20 + rows * rsize:]

        def text(off: int) -> str:
            if off <= 0 or off >= len(strings):
                return ""
            return strings[off:strings.index(b"\0", off)].decode("utf-8", "replace")

        icons: dict[int, str] = {}
        for i in range(rows):
            values = struct.unpack(f"<{fields}I",
                                   records[i * rsize:i * rsize + fields * 4])
            name = text(values[ICON_FIELD])
            if name:
                icons[values[0]] = name.rsplit("\\", 1)[-1]
        LOG.info("ItemDisplayInfo.dbc from %s: %d displays", path.name, len(icons))
        return icons
    return {}


def lookup_displayids(catalog: coadata.Catalog, known: dict[int, int],
                      client) -> dict[int, int]:
    """Fill in displayids the client cache does not have, from the website."""
    import aowow

    out: dict[int, int] = {}
    for node in catalog.all_nodes():
        if node["kind"] != "item":
            continue
        item_id = node["node_id"]
        if item_id in known:
            out[item_id] = known[item_id]
            continue
        name = node.get("name") or ""
        if not name or node.get("missing_from_db"):
            continue
        url = f"?items&filter=na={name}"
        try:
            view = aowow.fetch_listview(client, url, template="item")
        except Exception as exc:
            LOG.warning("lookup failed for %s: %s", name, exc)
            continue
        for row in (view.rows if view else []):
            if int(row["id"]) == item_id and row.get("displayid"):
                out[item_id] = int(row["displayid"])
                break
    return out


def extract_icons(names: set[str], archives: list[Path], out_dir: Path) -> dict[str, str]:
    """Decode each icon's BLP into a PNG. Returns icon name -> file name."""
    from mpyq import MPQArchive

    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    pending = set(names)

    for name in list(pending):
        target = out_dir / f"{SAFE_NAME.sub('_', name)}.png"
        if target.exists():
            written[name] = target.name
            pending.discard(name)

    for path in archives:
        if not pending:
            break
        try:
            archive = MPQArchive(str(path))
        except Exception:
            continue
        for name in sorted(pending):
            try:
                data = archive.read_file(f"Interface\\Icons\\{name}.blp")
            except Exception:
                continue
            if not data:
                continue
            try:
                width, height, rgba = blp.decode(data)
            except blp.BlpError as exc:
                LOG.warning("%s: %s", name, exc)
                pending.discard(name)
                continue
            target = out_dir / f"{SAFE_NAME.sub('_', name)}.png"
            blp.write_png(target, width, height, rgba)
            written[name] = target.name
            pending.discard(name)
    if pending:
        LOG.warning("%d icons not found in any archive: %s",
                    len(pending), ", ".join(sorted(pending)[:8]))
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client", type=Path, default=CLIENT)
    parser.add_argument("--nodes", type=Path, default=Path("output/highrisk/nodes.json"))
    parser.add_argument("--assets", type=Path, default=Path("web/public/assets"))
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")

    data_dir = args.client / "Data"
    archives = sorted(
        (Path(p) for p in glob.glob(str(data_dir / "*.MPQ")) + glob.glob(str(data_dir / "*.mpq"))),
        key=os.path.getmtime, reverse=True)
    if not archives:
        print(f"No MPQ archives under {data_dir}")
        return 1
    LOG.info("%d archives under %s", len(archives), data_dir)

    catalog = coadata.load(args.nodes)
    cached = read_itemcache(args.client / "Cache/WDB/enUS/itemcache.wdb")
    LOG.info("itemcache.wdb: %d items", len(cached))

    import aowow
    client = aowow.Client(Path("cache"), delay=0.6)
    displayids = lookup_displayids(catalog, cached, client)
    LOG.info("displayids resolved for %d items", len(displayids))

    display_icons = read_display_icons(archives)
    if not display_icons:
        print("Could not read ItemDisplayInfo.dbc from any archive.")
        return 1

    wanted: dict[int, str] = {}
    for item_id, display in displayids.items():
        name = display_icons.get(display)
        if name:
            wanted[item_id] = name
    LOG.info("icon names for %d items", len(wanted))

    written = extract_icons(set(wanted.values()), archives, args.assets / "icons")

    manifest = {str(i): written[n] for i, n in wanted.items() if n in written}
    (args.assets / "icons.json").write_text(
        json.dumps(manifest, indent=1), encoding="utf-8")

    total = sum(1 for n in catalog.all_nodes() if n["kind"] == "item")
    print(f"\n{len(written)} icons written to {args.assets / 'icons'}")
    print(f"{len(manifest)} of {total} items have an icon")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

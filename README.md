# Ascension database tools

- `aowow.py` — client and parser for db.ascension.gg (an Aowow instance).
- `highrisk.py` — scrapes the AtlasLoot **High Risk (Classic)** lists with full material trees and drop sources.
- `icons.py` + `blp.py` — pull real item icons out of the game client (MPQ → DBC → BLP → PNG).
- `coadata.py` — shared view over the scraped graph, including the yield-override rule.
- `tsm_scan.py` / `sync_prices.py` — decode TSM auction data and merge it into a persistent price store.
- `profit.py` — ranks the crafts by profit across sourcing scenarios.
- `tsm_export.py` — TSM group item lists and operation settings.
- `build_data.py` — exports `web/public/data.json` for the browser.
- `web/` — React (Vite) front end. **Edit this**, not generated HTML.
- `refresh.py` — runs the whole pipeline, optionally committing and pushing.

## Pipeline

```bash
python refresh.py            # prices → data → site
python refresh.py --full     # also re-scrape the database and icons
python refresh.py --push     # ... then commit and push, triggering a deploy
```

Or step by step:

```bash
python highrisk.py       # scrape   -> output/highrisk/
python icons.py          # icons    -> web/public/assets/icons/
python sync_prices.py    # prices   -> output/market/price_db.json
python profit.py         # ranking  -> output/market/
python tsm_export.py     # groups   -> output/tsm/
python build_data.py     # data     -> web/public/data.json
cd web && npm run build  # site     -> output/site/
```

## The browser

`web/` is a normal Vite + React app: `npm run dev` for a live server, `npm run
build` to produce `output/site`. It has two tabs.

**Browse** — every item searchable by name, effect or zone. Each page shows the
effect, how to obtain it, the recipe with a cost and *where to farm* against each
material, the profit breakdown, what drops it (creature, level, rate, zone) and
what it is used in.

**Most profitable** — ranks recipes three ways:

| mode | materials |
| --- | --- |
| Buy what is priced *(default)* | buy every material the last scan priced, farm the rest |
| Buy everything | strict buy → craft → sell; needs a scanned price for every material |
| My farm list | uses the materials you ticked |

Ticking a material on an item page marks it as *"I have this or will farm it"*,
which drops its cost to zero and updates every profit figure live. The list is
kept in `localStorage`, so it survives a reload. The URL hash is the single
source of truth for what is selected, so browser Back and Forward move between
the items you looked at.

Costs are computed in the browser (`web/src/model.js`) rather than baked in,
which is what lets the checkboxes recalculate instantly. It mirrors the rules in
`profit.py`.

## Icons

db.ascension.gg has no icon for many custom items and the one it reports is
sometimes wrong — it calls the Distilled flasks a leather chest. `icons.py`
therefore reads the same source AtlasLoot and TSM use, the game client:

    item id → displayid → ItemDisplayInfo.dbc → Interface/Icons/<name>.blp

`displayid` comes from the website's item listings, with the client's own
`itemcache.wdb` used first where it has an entry. The DBC lives in
`patch-M.MPQ`; the art is spread across the other patch archives, searched
newest first so later patches win. `blp.py` decodes BLP2 (palettised, DXT1/3/5
and raw BGRA) and writes PNG without an imaging library, because Pillow has no
wheel for the Python here. That lifts coverage from 83 to 115 of 121 items; the
remaining 6 are the IDs that 404 on the database and have no display at all.

## Hosting

The site is static, so Vercel, Netlify and Cloudflare Pages all host it free.
The only real problem is keeping prices current, because **the host cannot
generate them**: they come from TSM SavedVariables on your PC, and the icons
come from the game client. Neither exists on a build server.

The data is therefore split by how often it changes:

| file | size | changes | how it gets published |
| --- | --- | --- | --- |
| `data.json` | ~188 KB | when the game's items change | committed, triggers a rebuild |
| `assets/icons/` | ~520 KB | rarely | committed, triggers a rebuild |
| `prices.json` | **~5 KB** | every scan | pushed to a `data` branch, **no rebuild** |

The browser loads the catalog and then overlays the price file, falling back to
the prices baked into the catalog if it cannot fetch one. So a price refresh is
a 5 KB push that appears on the next page load, not a redeploy.

### One-time setup

```bash
git init && git add . && git commit -m "CoA High Risk browser"
git remote add origin git@github.com:<you>/<repo>.git
git push -u origin main
```

Import the repo at vercel.com. `vercel.json` already sets the build command
(`cd web && npm install && npm run build`) and output directory (`output/site`),
so the defaults can be left alone. Then publish prices once and point the site
at them:

```bash
python publish_prices.py            # pushes prices.json to the data branch
python publish_prices.py --print-url
```

Put that URL in Vercel under Settings → Environment Variables as
`VITE_PRICES_URL`, then redeploy once. It looks like:

```
https://raw.githubusercontent.com/<you>/<repo>/data/prices.json
```

raw.githubusercontent.com sends `Access-Control-Allow-Origin: *`, so the browser
can read it directly, and Vercel only builds the production branch — pushing to
`data` never triggers a deploy.

### Keeping prices up to date

Prices are the only part that needs regular attention, and they arrive from two
places at different times:

| source | when it updates | how |
| --- | --- | --- |
| **your own scan** | immediately | scan the auction house, then `/reload` — TSM flushes SavedVariables to the WTF folder |
| **everyone else's scans** | between play sessions | the [sharing app](https://github.com/Seminko/Ascension-TSM-Data-Sharing-App) pools scans from all players |

The sharing app skips **both** upload and download while `Ascension.exe` is
running — the game rewrites the WTF folder underneath it — so pooled prices land
once you close the game. Your own scan does not have to wait for that, because
`/reload` writes it straight to disk.

`watch_prices.py` polls both and publishes whenever either moves forward:

```bash
python watch_prices.py                 # poll every 5 minutes, push on change
python watch_prices.py --once          # single check, for Task Scheduler
python watch_prices.py --target local  # rebuild locally without pushing
```

Polling is cheap: it reads only the `lastCompleteScan` timestamps, not the
megabyte of encoded scan data, and does nothing at all when the scan has not
advanced. Run it as a scheduled task or leave it in a terminal while you play.

For a single manual push, `publish_prices.py` does the same thing once.

When the catalog itself changes — new recipes, items or icons:

```bash
python refresh.py --full --push   # re-scrape, re-extract icons, rebuild, deploy
```

### Running it unattended

Three moving parts, and they are not equally automatable:

| part | can it run in the cloud? | how |
| --- | --- | --- |
| **site + catalog** | yes, fully | `.github/workflows/refresh-catalog.yml` re-scrapes db.ascension.gg weekly and commits; the host redeploys |
| **icons** | not needed | extracted from the game client once, committed, changed only when Ascension adds items |
| **prices** | **no** | they exist only in TSM SavedVariables on a machine that runs the game |

The price half cannot be made serverless, and it is worth being plain about why:
there is no public API for Ascension auction data. The [sharing
app](https://github.com/Seminko/Ascension-TSM-Data-Sharing-App) pools it, but
its endpoints are deliberately kept out of its public repository, so calling
them directly is not something to do uninvited. If you want a cloud service to
pull prices, the honest route is to ask its author on their Discord.

So one machine has to run near the data. Options, cheapest first:

**Your gaming PC** — nothing extra to run or pay for. Register the scheduled
task and forget about it:

```powershell
powershell -ExecutionPolicy Bypass -File agent\install-task.ps1
```

It checks every 15 minutes and publishes only when a scan has actually landed,
so the cost is a file-timestamp read. Prices go stale while the PC is off.

**An always-on box you own** — a Raspberry Pi, an old laptop, a home server.
Run the sharing app there so pooled prices keep arriving even while you play,
and point the watcher at it:

```bash
python watch_prices.py --sharing-cache /path/to/update_times.json --interval 300
```

`agent/coa-prices.service` is a systemd unit for exactly this.

**A free cloud VM** — Oracle Cloud's Always Free tier (4 ARM cores, 24 GB RAM,
genuinely free with no expiry) is the usual pick; AWS and Azure free tiers
expire after 12 months. The sharing app is Windows-only as shipped, though its
source is Python, so a Linux box needs the Windows-specific bits (toast
notifications, Task Scheduler) stubbed out.

One caveat if you go the always-on route: the sharing app both uploads and
downloads. An instance that only ever downloads takes from the pool without
contributing scans back. Running it where you also play, or letting it upload
your own scans, keeps that fair.

### If you would rather not use a data branch

`VITE_PRICES_URL` accepts any URL that allows cross-origin reads, so Cloudflare
R2, Vercel Blob or a Gist all work the same way. Leaving it unset makes the site
read `prices.json` from its own origin, which is what `--target local` writes
next to the built site for self-hosting.

## Why TSM cannot do the profit part alone

A TSM scan gives you prices, not craft costs, for this content:

- The High Risk recipes are absent from `CraftingDB` until the recipe is learned
  *and* the profession window has been opened, so TSM's `crafting` price source
  resolves to nothing.
- The station conversions are not TSM crafts at all. Turning Blightroot into
  Blightroot Extract is a `Use:` effect at the Sanguine Workbench, which no
  addon models as a recipe.

So the groups exist to make scanning and posting easy, and `profit.py` applies
the scraped material tree to those scanned prices.

### Reading TSM's AuctionDB

TSM3 stores a whole realm scan as one encoded string. Nothing about that format
is documented, so it was derived from the data and then checked against
independent evidence before being trusted:

- Item IDs decode to real items (7080 Essence of Water, 967468 Distilled Flask
  of the Executioner, 1303711 Blightroot Extract).
- The last two alphabet characters are `_` then `=`. A timestamp cannot settle
  that ordering — both put the scan within a minute of `lastCompleteScan` — but
  item IDs can: only this ordering makes ids containing those digits resolve to
  items that exist. Getting it backwards silently hides every such item.
- Decoded market values match what TSM shows in game: Blightroot Extract at
  8g55s against 14g59s99c listed at 171% of market (8g53s implied).
- Decoded minimum buyout for Essence of Water is 199500 copper against
  Auctionator's separately recorded 199000 — 0.25% apart.
- The per-item history is keyed by day number, and those keys decode to a
  contiguous 14-day window ending on the scan date, which is the window TSM
  uses for market value.
- `marketValue / mean(history)` has a median of 1.000 across 12712 items.

### Which price to believe

Buying and selling are priced separately (`--buy-source`, `--sell-source`), both
defaulting to **`robust`**.

The cheapest listing is frequently one mispriced unit — somebody posting a
single item at 24s while it trades at 22g — and taking that at face value makes
a craft look wildly profitable on a lot nobody can actually buy. It is not rare:
**30 of the materials here** have a lowest listing under half their 14-day market
value, one at 9% of it.

A true "average the cheapest 20 units" is not possible, because TSM's AuctionDB
stores only aggregates per item (market value, lowest buyout, total quantity) and
never the individual auctions. `robust` gets at the same intent from the data
that does exist: it takes the lowest buyout, but refuses it when it falls below
half the 14-day market value, using that floor instead. `--outlier-floor`
changes the threshold; `--buy-source minbuyout` restores the old behaviour.

Every item page shows all four numbers — the price used, market value, lowest
listing and how many are available — and says so explicitly when the lowest
listing was rejected as an outlier.

### Three sourcing scenarios

The same recipe is worth different amounts depending on where its materials come
from, so all three are reported side by side rather than collapsing into one
number that hides the assumption:

| scenario | materials | reads |
| --- | --- | --- |
| `buy` | everything bought at auction | nothing extra |
| `gather` | everything farmable gathered yourself (cost 0) | nothing extra |
| `plan` | per-material choice | `sourcing.json` |

`buy` prices most recipes; the rest are excluded and each blank row names the
materials responsible. A material missing from a scan means *that scan did not
see it*, not that it is never sold — a fresh scan usually fills several in.

`sourcing.json` gives per-material control. Generate a starter plan with:

```bash
python profit.py --sourcing-template
```

It defaults each material to `buy` when the auction house actually lists it and
`gather` when it does not, which is the mix you can really execute today. Edit
any entry's `mode`, then re-run. `--rank-by buy|gather|plan` chooses which
scenario orders the ranking.

### Yield overrides

`yield_overrides.json` corrects recipe yields, because db.ascension.gg reports
`creates` wrongly for this custom content — it claims 10 for Fused foods where
the game gives 1, and 3 for Distilled flasks. The field is read correctly:
vanilla recipes (Major Healing Potion, Flask of the Titans, Smoked Desert
Dumplings) all report `creates=[id,1,1]`. Yield multiplies revenue directly, so
all three families default to 1 until confirmed in game. Correct any recipe by
adding its item ID under `by_item`.

## High Risk crafting harvest

```bash
python highrisk.py            # writes output/highrisk/
```

Scope is the three AtlasLoot lists under Crafting → *profession* → High Risk, expansion Classic:
11 `Distilled Flask of ...`, 10 High Risk `Enchant Weapon - ...`, and 41 `Fused ...` foods.

Outputs:

| file | contents |
| --- | --- |
| `../site/index.html` | searchable browser: icons, hover tooltips, prices per reagent, profit per sourcing scenario |
| `craft_tree.md` | readable tree per recipe, with effect and where the recipe is learned |
| `recipes.csv` | one row per recipe: spell, profession, skill, yield, station, reagents, effect |
| `reagents.csv` | one row per recipe/reagent pair |
| `raw_materials.csv` | each recipe fully expanded to gatherable/buyable materials |
| `shopping_list.csv` | totals across one craft of all 62 recipes |
| `nodes.json` | the whole graph, including tooltips and item sources |
| `data_gaps.md` | what the database itself could not answer |

### How it reads the site

Aowow renders listings server-side and embeds the result set as a JavaScript
`new Listview({...data:[...]})` call, so a whole category arrives in one request.
Two constraints shape the client:

- Listviews are capped at 1000 rows. The call carries `"_truncated":1` and a
  `lvnote_itemsfound` count, so truncation is detected rather than assumed away.
- The query string is passed through verbatim. Aowow reads it positionally and
  packs `=` and `;` inside the `filter` value (`?items=0.6&filter=qu=4`);
  reordering or percent-encoding it returns 404.

### Two rules that keep material trees meaningful

- **Blizzard-era materials (item ID ≤ 56815) are leaves.** You farm or buy them,
  and expanding them walks straight into the circular Alchemy transmute chain
  (Essence of Earth → Fire → Air → Water → Earth).
- **Some custom materials are converted, not crafted.** Their recipe spell
  carries no reagent list; the source item instead has a `Use:` effect such as
  *"Refined into Blightroot Exract at the Sanguine Workbench"*. These are
  resolved from the input side and recorded as conversions. Every conversion in
  the current output is corroborated by the input item's own tooltip.

## Ascension recipe scraper

`scrape_ascension_recipes.py` discovers recipe spell IDs from listing pages, downloads each spell page, and emits normalized JSON/CSV plus basic TSM item groups.

## Install

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

## First smoke test

Use one known recipe page before crawling a profession:

```bash
python scrape_ascension_recipes.py scrape \
  --url "https://db.ascension.gg/?spell=17556" \
  --output output \
  --verbose
```

Inspect:

- `output/recipes.json`
- `output/recipes.csv`
- `output/reagents.csv`
- `output/unparsed/` when a selector did not match

The known Major Healing Potion page should yield Alchemy, its created item, and Mountain Silversage, Golden Sansam x2, and Crystal Vial. The public page currently exposes those fields in its rendered content.

## Discover and scrape listings

Edit `config.example.json`, then run:

```bash
python scrape_ascension_recipes.py all \
  --config config.example.json \
  --output output \
  --cache cache
```

You can also place one URL per line in `seeds.txt`:

```text
https://db.ascension.gg/?spells=11.171
https://db.ascension.gg/?items=9.6
```

Then:

```bash
python scrape_ascension_recipes.py all --seed-file seeds.txt
```

## Outputs

- `spell_ids.txt`: discovered spell IDs
- `recipes.json`: normalized recipe records
- `recipes.csv`: one row per recipe
- `reagents.csv`: one row per recipe reagent
- `tsm_groups.txt`: basic `i:<itemID>` lists grouped by profession/category
- `unparsed/*.html`: pages needing parser adjustments
- `cache/*.html`: cached responses, allowing repeat parsing without hammering the site

## Important behavior

The parser does not invent missing values. Missing output IDs, reagents, or sections are placed in `parse_warnings`, and suspicious pages are copied to `output/unparsed`.

Start with one or two known pages. If the result is wrong, share the corresponding cached or unparsed HTML and adjust the parser before starting a large crawl.

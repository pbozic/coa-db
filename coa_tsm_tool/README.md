# CoA TSM Crafting Group Builder

This tool builds categorized TSM item lists from Project Ascension TSM SavedVariables without executing the Lua files.

## Run

```bash
python build_coa_tsm_groups.py \
  --crafting TradeSkillMaster_Crafting.lua \
  --accounting TradeSkillMaster_Accounting.lua \
  --output output
```

## Output

- `tsm_group_item_lists.txt`: group paths and comma-separated item strings for TSM import.
- `coa_known_crafts.csv/json`: recipes actually present in CraftingDB, with materials.
- `coa_observed_candidates.csv/json`: consumables/enchants seen by AccountingDB but not verified as learned crafts.
- `profitability_template.csv`: price-input template for external analysis.
- `summary.json`: extraction counts.

## Important limitations

CraftingDB only records professions opened on characters and recipes visible to TSM. Open every relevant profession window, including Alchemy, Enchanting, Engineering, Cooking and First Aid, then run `/reload` before copying the SavedVariables again.

AccountingDB proves that an item name and ID were observed. It does not prove the item is craftable. Keep candidate groups separate until verified.

AuctionDB uses TSM's compressed scan format. This build leaves live price calculation to TSM itself rather than pretending compressed characters are trustworthy copper values.

## Suggested TSM operations

Crafting minimum profit:

```text
max(25g, 20% crafting)
```

Auctioning minimum / normal / maximum:

```text
max(120% crafting, 80% dbmarket)
dbmarket
max(250% crafting, 150% dbmarket)
```

Use smaller restock quantities for expensive flasks and enchants. High Risk PvP can generally tolerate faster restocking because death remains the game's most reliable consumable sink.

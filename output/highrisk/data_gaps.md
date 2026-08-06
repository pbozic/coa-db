# Data gaps

Everything below is missing on db.ascension.gg, not in the scraper.

## Reagents with no item page (6)

These IDs appear in recipe reagent lists but `?item=<id>` returns 404,
so they have no name, icon or source. They render as blanks in the
in-game tooltip too.

| item ID | used by |
| --- | --- |
| 1303149 | Enchant Weapon - Arcane Dexterity, Enchant Weapon - Arcane Precision, Enchant Weapon - Lucid Assault, Enchant Weapon - Viscious Assault |
| 1303160 | Enchant Weapon - Arcane Precision |
| 1303164 | Enchant Weapon - Arcane Dexterity, Enchant Weapon - Lucid Assault, Enchant Weapon - Viscious Assault |
| 1303470 | Fused Blazing Stew, Fused Heightened Wontons, Fused Savory Chops, Fused Savory Steak, Fused Savory Stew, Fused Savory Wontons, Fused Vibrant Chops |
| 1303472 | Fused Heightened Wontons |
| 1303476 | Fused Blazing Stew, Fused Savory Chops, Fused Savory Steak, Fused Savory Stew, Fused Savory Wontons, Fused Vibrant Chops |

## Materials with several sources (5)

More than one spell produces these; the first was recorded. All are
Blizzard-era materials treated as leaves, so this does not affect any
quantity above.

- Animated Bone (1303736)
- Animated Bone Dust (1303709)
- Essence of Earth (7076)
- Essence of Water (7080)
- Large Brilliant Shard (14344)

## Tooltip variables

Some enchant descriptions contain unresolved Aowow variables such as
`$968734d` (a duration read from another spell). They are left verbatim
rather than guessed at.

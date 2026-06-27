# Code Translation

Decision date: 2026-06-28

PUBG telemetry uses internal codes for items, weapons, maps, vehicles, and damage causers. The project should convert
known codes to Korean display labels before showing them in Discord or the local program.

## Rule

- Known code -> Korean label
- Unknown code -> original code unchanged

This keeps new or updated PUBG content visible even before the local dictionary is updated.

Examples:

| Code | Display |
| --- | --- |
| `Item_Weapon_BerylM762_C` | `베릴 M762` |
| `WeapBerylM762_C` | `베릴 M762` |
| `Erangel_Main` | `에란겔` |
| `Item_Weapon_NewThing_C` | `Item_Weapon_NewThing_C` |

## Dictionary Categories

| Category | Examples |
| --- | --- |
| `item` | `item.itemId`, attached item IDs |
| `damage_causer` | `damageCauserName`, weapon/projectile damage sources |
| `map` | match and telemetry map names |
| `vehicle` | vehicle IDs |
| `game_mode` | solo/duo/squad mode strings |
| `death_type` | local death classification strings |

## Update Strategy

The built-in dictionary is intentionally a seed. When PUBG adds new items, the parser should continue to return the
raw code until an override dictionary is added.

Override JSON shape:

```json
{
  "item": {
    "Item_Weapon_NewThing_C": "새 무기"
  },
  "damage_causer": {
    "WeapNewThing_C": "새 무기"
  }
}
```

The local management program can later expose a small dictionary editor, but the parser must never fail because a
code is missing.

## Sources

- Existing local `PUBG_Data.py` was used as a structural reference for the code categories.
- Official PUBG API assets provide authoritative raw code dictionaries.

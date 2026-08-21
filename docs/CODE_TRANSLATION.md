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

## Current Attachment Naming

Telemetry codes and Korean display names must stay distinct when attachments have similar English terms:

| Telemetry code | Korean display |
| --- | --- |
| `Item_Attach_Weapon_Lower_Foregrip_C` | `수직 손잡이` |
| `Item_Attach_Weapon_Lower_AngledForeGrip_C` | `앵글 손잡이` |
| `Item_Attach_Weapon_Lower_TiltedGrip_C` | `틸티드 그립` |

`TiltedGrip` is not an alias for the vertical or angled grip. PUBG Update 41.1 introduced it as a separate lower
attachment under the official Korean name `틸티드 그립`. The angled grip was separately removed from world spawn in
that update.

## Legacy Python Dictionary Import

Existing local dictionary files such as `PUBG_Data.py` can be used as an override source without executing the file.
The loader parses simple dictionary assignments with `ast.literal_eval` and ignores non-string tables such as map
size or map number dictionaries.

Supported legacy names include:

| Legacy name | Normalized category |
| --- | --- |
| `deat_type` | `death_type` |
| `game_mode` | `game_mode` |
| `map_name` | `map` |
| `item_id_list` | `item` |
| `all_item_id_list` | `item` |
| `weapon_id_list` | `damage_causer` |
| `weapon_id_list_2` | `damage_causer` |

## Sources

- Existing local `PUBG_Data.py` was used as a structural reference for the code categories.
- Official PUBG API assets provide authoritative raw code dictionaries.
- PUBG Update 41.1 provides the official Korean in-game name for the Tilted Grip:
  <https://pubg.com/ko/news/9926>.

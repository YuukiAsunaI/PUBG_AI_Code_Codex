# Sources

Initial research date: 2026-06-27. Latest verification: 2026-08-21.

## Official PUBG Sources

- PUBG Open API documentation: https://documentation.pubg.com/en/introduction.html
- Official documentation content repository: https://github.com/pubg/api-documentation-content
- Making requests: https://github.com/pubg/api-documentation-content/blob/master/rst/making-requests.rst
- Rate limits: https://github.com/pubg/api-documentation-content/blob/master/rst/rate-limits.rst
- Players endpoint: https://github.com/pubg/api-documentation-content/blob/master/swagger/en/paths/players.yml
- Player endpoint: https://github.com/pubg/api-documentation-content/blob/master/swagger/en/paths/player.yml
- Match endpoint: https://github.com/pubg/api-documentation-content/blob/master/swagger/en/paths/match.yml
- Telemetry guide: https://github.com/pubg/api-documentation-content/blob/master/rst/telemetry.rst
- Telemetry events: https://github.com/pubg/api-documentation-content/blob/master/rst/telemetry-events.rst
- Player lookup filter parameters: https://github.com/pubg/api-documentation-content/blob/master/swagger/en/parameters/filterPlayerIds.yml
- Official assets and dictionaries: https://github.com/pubg/api-assets
- Official map image assets: https://github.com/pubg/api-assets/tree/master/Assets/Maps
- Game mode dictionary: https://raw.githubusercontent.com/pubg/api-assets/master/dictionaries/gameMode.json
- Map dictionary: https://raw.githubusercontent.com/pubg/api-assets/master/dictionaries/telemetry/mapName.json
- Item ID dictionary: https://raw.githubusercontent.com/pubg/api-assets/master/dictionaries/telemetry/item/itemId.json
- Damage causer dictionary: https://raw.githubusercontent.com/pubg/api-assets/master/dictionaries/telemetry/damageCauserName.json
- Vehicle ID dictionary: https://raw.githubusercontent.com/pubg/api-assets/master/dictionaries/telemetry/vehicle/vehicleId.json
- Update 41.1 Korean attachment names: https://pubg.com/ko/news/9926
- Update 34.1 7.62 mm inventory weight: https://pubg.com/en/news/8170
- Update 42.3 RPD and LMG sustained-fire context: https://pubg.com/en/news/10885
- MG3 introduction and 75-round magazine: https://pubg.com/zh-cn/news/4744
- discord.py hybrid command guide: https://discordpy.readthedocs.io/en/stable/ext/commands/commands.html#hybrid-commands

## Official MySQL Transaction Sources

Reviewed for the schema versions 18-19 temporary-table deletion/rollback and fault-matrix rehearsals on 2026-07-28:

- Statements that cause implicit commits: https://dev.mysql.com/doc/refman/8.4/en/implicit-commit.html
- COMMIT and ROLLBACK statements: https://dev.mysql.com/doc/refman/8.4/en/commit.html
- InnoDB introduction and transaction model: https://dev.mysql.com/doc/refman/8.4/en/innodb-introduction.html

## Reference Projects

- https://github.com/ramonsaraiva/pubg-python
- https://github.com/pubgsh/api
- https://github.com/GavinPower747/pubg-dotnet
- https://github.com/ickerio/pubg.js
- https://github.com/crflynn/chicken-dinner
- https://github.com/bloody-green-tea/pubgapi
- https://github.com/Discord-ian/pubgy
- https://github.com/amn057828-beep/pubg-ai-api
- https://github.com/heversonbenatti/pubg-insight
- https://github.com/theodorosidmar/pubgkt
- https://github.com/smw0807/pubg_your.stat
- https://github.com/smw0807/pubg-api
- https://github.com/smw0807/pubg-kit
- https://github.com/SeatloN/pubg-api

## Additional Korean Reference Articles

- `smw0807/pubg_your.stat`: https://github.com/smw0807/pubg_your.stat
- `smw0807/pubg_your.stat` README: https://github.com/smw0807/pubg_your.stat/blob/main/README.md
- PUBG data analysis series by songmin9813:
  - https://songmin9813.tistory.com/43
  - https://songmin9813.tistory.com/44
  - https://songmin9813.tistory.com/49
  - https://songmin9813.tistory.com/50
- PUBG pro match movement analysis by right1203: https://right1203.tistory.com/2

## Notes

- `SeatloN/pubg-api` could not be resolved through GitHub on 2026-06-27.
- Metadata such as language, stars, forks, default branch, and latest push timestamp was checked with GitHub CLI.
- The research docs intentionally separate official facts from implementation recommendations.

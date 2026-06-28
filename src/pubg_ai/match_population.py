from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping


BOT_ID_PREFIXES = ("ai.",)
BOT_NAME_PREFIXES = ("ai.", "bot.")


@dataclass(frozen=True)
class PlayerPopulationRecord:
    player_key: str
    name: str | None
    account_id: str | None
    player_id: str | None
    is_bot: bool
    detection_source: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MatchPopulationSummary:
    total_players: int
    human_players: int
    bot_players: int
    detection_source_counts: dict[str, int]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def summarize_match_population(players: Iterable[Mapping[str, Any]]) -> MatchPopulationSummary:
    records = population_records(players)
    bot_players = sum(1 for record in records if record.is_bot)
    human_players = len(records) - bot_players
    detection_source_counts: dict[str, int] = {}

    for record in records:
        detection_source_counts[record.detection_source] = (
            detection_source_counts.get(record.detection_source, 0) + 1
        )

    return MatchPopulationSummary(
        total_players=len(records),
        human_players=human_players,
        bot_players=bot_players,
        detection_source_counts=detection_source_counts,
    )


def population_records(players: Iterable[Mapping[str, Any]]) -> list[PlayerPopulationRecord]:
    records: dict[str, PlayerPopulationRecord] = {}

    for index, player in enumerate(players):
        normalized = _normalize_player(player)
        account_id = _string_value(normalized.get("accountId") or normalized.get("account_id"))
        player_id = _string_value(
            normalized.get("playerId")
            or normalized.get("player_id")
            or _nested_stat(normalized, "playerId")
        )
        name = _string_value(normalized.get("name") or _nested_stat(normalized, "name"))
        player_key = account_id or player_id or name or f"unknown-{index}"
        is_bot, detection_source = detect_bot_player(
            account_id=account_id,
            player_id=player_id,
            name=name,
        )

        records.setdefault(
            player_key,
            PlayerPopulationRecord(
                player_key=player_key,
                name=name,
                account_id=account_id,
                player_id=player_id,
                is_bot=is_bot,
                detection_source=detection_source,
            ),
        )

    return list(records.values())


def detect_bot_player(
    *,
    account_id: str | None = None,
    player_id: str | None = None,
    name: str | None = None,
) -> tuple[bool, str]:
    for field, value, prefixes in (
        ("account_id", account_id, BOT_ID_PREFIXES),
        ("player_id", player_id, BOT_ID_PREFIXES),
        ("name", name, BOT_NAME_PREFIXES),
    ):
        if _starts_with_any(value, prefixes):
            return True, f"{field}_prefix"

    return False, "human_default"


def _normalize_player(player: Mapping[str, Any]) -> Mapping[str, Any]:
    character = player.get("character")
    if isinstance(character, Mapping):
        return character

    attributes = player.get("attributes")
    if isinstance(attributes, Mapping):
        stats = attributes.get("stats")
        if isinstance(stats, Mapping):
            return stats

    return player


def _nested_stat(player: Mapping[str, Any], key: str) -> Any:
    stats = player.get("stats")
    if isinstance(stats, Mapping):
        return stats.get(key)
    return None


def _string_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _starts_with_any(value: str | None, prefixes: tuple[str, ...]) -> bool:
    if value is None:
        return False
    normalized = value.lower()
    return any(normalized.startswith(prefix) for prefix in prefixes)

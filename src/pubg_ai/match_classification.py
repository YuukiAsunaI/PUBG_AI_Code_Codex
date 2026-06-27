from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


TeamMode = Literal["solo", "duo", "squad", "unknown"]
Perspective = Literal["fpp", "tpp", "unknown"]


@dataclass(frozen=True)
class MatchClassification:
    match_id: str
    shard: str
    game_mode: str
    match_type: str
    map_name: str
    team_mode: TeamMode
    perspective: Perspective
    is_custom_match: bool
    is_ranked: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def classify_match_payload(
    payload: dict[str, Any],
    fallback_shard: str | None = None,
) -> MatchClassification:
    data = payload.get("data")
    if not isinstance(data, dict):
        data = {}

    attributes = data.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}

    game_mode = _string_attr(attributes, "gameMode", "unknown")
    match_type = _string_attr(attributes, "matchType", "unknown")
    map_name = _string_attr(attributes, "mapName", "unknown")
    shard = _string_attr(attributes, "shardId", fallback_shard or "unknown")
    match_id = _string_attr(data, "id", "unknown")

    return MatchClassification(
        match_id=match_id,
        shard=shard,
        game_mode=game_mode,
        match_type=match_type,
        map_name=map_name,
        team_mode=team_mode_from_game_mode(game_mode),
        perspective=perspective_from_game_mode(game_mode),
        is_custom_match=_bool_attr(attributes, "isCustomMatch", default=False),
        is_ranked=is_ranked_match(game_mode=game_mode, match_type=match_type),
    )


def team_mode_from_game_mode(game_mode: str) -> TeamMode:
    mode = game_mode.lower()
    if "solo" in mode:
        return "solo"
    if "duo" in mode:
        return "duo"
    if "squad" in mode:
        return "squad"
    return "unknown"


def perspective_from_game_mode(game_mode: str) -> Perspective:
    mode = game_mode.lower()
    if "fpp" in mode:
        return "fpp"
    if mode == "unknown":
        return "unknown"
    return "tpp"


def is_ranked_match(game_mode: str, match_type: str) -> bool:
    return "ranked" in game_mode.lower() or "ranked" in match_type.lower()


def _string_attr(values: dict[str, Any], key: str, default: str) -> str:
    value = values.get(key)
    if isinstance(value, str) and value:
        return value
    return default


def _bool_attr(values: dict[str, Any], key: str, default: bool) -> bool:
    value = values.get(key)
    if isinstance(value, bool):
        return value
    return default

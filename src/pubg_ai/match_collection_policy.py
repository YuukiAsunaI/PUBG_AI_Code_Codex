from __future__ import annotations

from dataclasses import dataclass

from pubg_ai.pubg_client import PubgMatchDetails


MATCH_COLLECTION_POLICY_VERSION = "exclude-custom-training-v2"

CUSTOM_MATCH_REASON = "custom_match"
TRAINING_MATCH_REASON = "training_match"

_TRAINING_MATCH_TYPES = frozenset(
    {
        "airoyale",
        "trainingroom",
        "tutorialatoz",
    }
)
_TRAINING_GAME_MODES = frozenset(
    {
        "basic-training",
        "basictraining",
        "practice",
        "training",
        "tutorial",
    }
)
_TRAINING_MAPS = frozenset({"range-main"})


@dataclass(frozen=True)
class MatchCollectionDecision:
    should_collect: bool
    exclusion_reason: str | None = None
    matched_rule: str | None = None

    @property
    def is_excluded(self) -> bool:
        return not self.should_collect


def decide_match_collection(match: PubgMatchDetails) -> MatchCollectionDecision:
    """Decide whether a fetched match may enter raw/normalized storage."""

    return decide_match_collection_attributes(
        is_custom_match=match.is_custom_match,
        match_type=match.match_type,
        game_mode=match.game_mode,
        map_name=match.map_name,
    )


def decide_match_collection_attributes(
    *,
    is_custom_match: bool,
    match_type: str | None,
    game_mode: str | None,
    map_name: str | None,
) -> MatchCollectionDecision:
    if is_custom_match:
        return _excluded(CUSTOM_MATCH_REASON, "isCustomMatch")

    normalized_match_type = _normalize(match_type)
    if normalized_match_type in _TRAINING_MATCH_TYPES:
        return _excluded(TRAINING_MATCH_REASON, f"matchType:{normalized_match_type}")

    normalized_game_mode = _normalize(game_mode)
    if normalized_game_mode in _TRAINING_GAME_MODES:
        return _excluded(TRAINING_MATCH_REASON, f"gameMode:{normalized_game_mode}")

    normalized_map_name = _normalize(map_name)
    if normalized_map_name in _TRAINING_MAPS:
        return _excluded(TRAINING_MATCH_REASON, f"mapName:{normalized_map_name}")

    return MatchCollectionDecision(should_collect=True)


def _excluded(reason: str, matched_rule: str) -> MatchCollectionDecision:
    return MatchCollectionDecision(
        should_collect=False,
        exclusion_reason=reason,
        matched_rule=matched_rule,
    )


def _normalize(value: str | None) -> str:
    return str(value or "").strip().lower().replace("_", "-")

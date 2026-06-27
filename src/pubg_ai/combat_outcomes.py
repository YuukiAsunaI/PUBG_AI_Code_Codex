from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal


CombatOutcomeType = Literal["dbno_win", "dbno_loss"]


@dataclass(frozen=True)
class CombatOutcome:
    match_id: str
    account_id: str
    opponent_account_id: str
    outcome_type: CombatOutcomeType
    event_type: str
    game_mode: str
    dbno_id: str | None = None
    damage_causer_name: str | None = None
    distance: float | None = None

    @property
    def counts_as_fight_win(self) -> bool:
        return self.outcome_type == "dbno_win"

    @property
    def counts_as_fight_loss(self) -> bool:
        return self.outcome_type == "dbno_loss"


def is_dbno_fight_mode(game_mode: str) -> bool:
    mode = game_mode.lower()
    return "duo" in mode or "squad" in mode


def dbno_outcomes_from_event(
    event: dict[str, Any],
    registered_account_ids: Iterable[str],
    game_mode: str,
    match_id: str,
) -> list[CombatOutcome]:
    if event.get("_T") != "LogPlayerMakeGroggy":
        return []

    if not is_dbno_fight_mode(game_mode):
        return []

    registered = set(registered_account_ids)
    attacker_id = _character_account_id(event.get("attacker"))
    victim_id = _character_account_id(event.get("victim"))
    if not attacker_id or not victim_id or attacker_id == victim_id:
        return []

    outcomes: list[CombatOutcome] = []
    common = {
        "match_id": match_id,
        "event_type": "LogPlayerMakeGroggy",
        "game_mode": game_mode,
        "dbno_id": _string_or_none(event.get("dBNOId")),
        "damage_causer_name": _string_or_none(event.get("damageCauserName")),
        "distance": _float_or_none(event.get("distance")),
    }

    if attacker_id in registered:
        outcomes.append(
            CombatOutcome(
                account_id=attacker_id,
                opponent_account_id=victim_id,
                outcome_type="dbno_win",
                **common,
            )
        )

    if victim_id in registered:
        outcomes.append(
            CombatOutcome(
                account_id=victim_id,
                opponent_account_id=attacker_id,
                outcome_type="dbno_loss",
                **common,
            )
        )

    return outcomes


def _character_account_id(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None

    account_id = value.get("accountId")
    if isinstance(account_id, str) and account_id:
        return account_id

    return None


def _string_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None

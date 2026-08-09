from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Literal, Mapping


WeaponFamily = Literal[
    "AR",
    "DMR",
    "SR",
    "SMG",
    "LMG",
    "SHOTGUN",
    "HANDGUN",
    "CROSSBOW",
    "UNCLASSIFIED",
]
AccuracyMetricKind = Literal[
    "estimated_hit_rate",
    "pellet_hits_per_shell",
    "hit_events_per_attack",
    "unavailable",
]


@dataclass(frozen=True)
class WeaponAccuracyMetric:
    weapon_family: WeaponFamily
    fire_unit: str
    hit_unit: str
    attack_events: int
    hit_events: int
    hit_events_per_attack: float | None
    estimated_hit_rate: float | None
    pellet_hits_per_shell: float | None
    metric_kind: AccuracyMetricKind
    metric_label: str
    metric_value: float | None
    is_percentage: bool
    quality: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AccuracyBreakdown:
    attack_events: int
    hit_events: int
    single_projectile_attacks: int
    single_projectile_hit_events: int
    estimated_hit_rate: float | None
    pellet_shells: int
    pellet_hit_events: int
    pellet_hits_per_shell: float | None
    unclassified_attacks: int
    unclassified_hit_events: int
    quality: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def weapon_family(weapon_code: str) -> WeaponFamily:
    code = str(weapon_code or "").strip()
    for family, codes in WEAPON_CODES_BY_FAMILY.items():
        if code in codes:
            return family
    return "UNCLASSIFIED"


def is_ballistic_weapon(weapon_code: str) -> bool:
    return weapon_family(weapon_code) != "UNCLASSIFIED"


def weapon_accuracy_metric(
    weapon_code: str,
    attack_events: int,
    hit_events: int,
) -> WeaponAccuracyMetric:
    attacks = max(0, int(attack_events or 0))
    hits = max(0, int(hit_events or 0))
    family = weapon_family(weapon_code)
    ratio = hits / attacks if attacks > 0 else None

    if family == "SHOTGUN":
        return WeaponAccuracyMetric(
            weapon_family=family,
            fire_unit="shell",
            hit_unit="pellet_hit_event",
            attack_events=attacks,
            hit_events=hits,
            hit_events_per_attack=ratio,
            estimated_hit_rate=None,
            pellet_hits_per_shell=ratio,
            metric_kind="pellet_hits_per_shell" if ratio is not None else "unavailable",
            metric_label="셸당 펠릿 피격",
            metric_value=ratio,
            is_percentage=False,
            quality="ok" if ratio is not None else "no_attack_events",
        )

    if family != "UNCLASSIFIED":
        estimated = ratio if ratio is not None and ratio <= 1.0 else None
        if ratio is None:
            kind: AccuracyMetricKind = "unavailable"
            label = "추정 명중률"
            quality = "no_attack_events"
        elif estimated is None:
            kind = "hit_events_per_attack"
            label = "공격당 피격 이벤트"
            quality = "hit_events_exceed_attacks"
        else:
            kind = "estimated_hit_rate"
            label = "추정 명중률"
            quality = "ok"
        return WeaponAccuracyMetric(
            weapon_family=family,
            fire_unit="bolt" if family == "CROSSBOW" else "round",
            hit_unit="projectile_hit_event",
            attack_events=attacks,
            hit_events=hits,
            hit_events_per_attack=ratio,
            estimated_hit_rate=estimated,
            pellet_hits_per_shell=None,
            metric_kind=kind,
            metric_label=label,
            metric_value=estimated if estimated is not None else ratio,
            is_percentage=estimated is not None,
            quality=quality,
        )

    return WeaponAccuracyMetric(
        weapon_family=family,
        fire_unit="attack",
        hit_unit="hit_event",
        attack_events=attacks,
        hit_events=hits,
        hit_events_per_attack=ratio,
        estimated_hit_rate=None,
        pellet_hits_per_shell=None,
        metric_kind="hit_events_per_attack" if ratio is not None else "unavailable",
        metric_label="공격당 피격 이벤트",
        metric_value=ratio,
        is_percentage=False,
        quality="unclassified_weapon" if ratio is not None else "no_attack_events",
    )


def summarize_accuracy_rows(rows: Iterable[Mapping[str, Any]]) -> AccuracyBreakdown:
    attack_events = 0
    hit_events = 0
    single_attacks = 0
    single_hits = 0
    pellet_shells = 0
    pellet_hits = 0
    unclassified_attacks = 0
    unclassified_hits = 0

    for row in rows:
        attacks = _non_negative_int(row.get("shots_fired"))
        hits = _non_negative_int(row.get("shots_hit"))
        family = weapon_family(str(row.get("weapon_code") or ""))
        attack_events += attacks
        hit_events += hits
        if family == "SHOTGUN":
            pellet_shells += attacks
            pellet_hits += hits
        elif family == "UNCLASSIFIED":
            unclassified_attacks += attacks
            unclassified_hits += hits
        else:
            single_attacks += attacks
            single_hits += hits

    estimated = single_hits / single_attacks if single_attacks > 0 and single_hits <= single_attacks else None
    pellet_ratio = pellet_hits / pellet_shells if pellet_shells > 0 else None
    quality = "ok"
    if single_attacks <= 0:
        quality = "no_single_projectile_attacks"
    elif single_hits > single_attacks:
        quality = "single_projectile_hits_exceed_attacks"
    elif unclassified_attacks or unclassified_hits:
        quality = "contains_unclassified_weapons"

    return AccuracyBreakdown(
        attack_events=attack_events,
        hit_events=hit_events,
        single_projectile_attacks=single_attacks,
        single_projectile_hit_events=single_hits,
        estimated_hit_rate=estimated,
        pellet_shells=pellet_shells,
        pellet_hit_events=pellet_hits,
        pellet_hits_per_shell=pellet_ratio,
        unclassified_attacks=unclassified_attacks,
        unclassified_hit_events=unclassified_hits,
        quality=quality,
    )


def recommendation_accuracy_score(metric: WeaponAccuracyMetric) -> float:
    return metric.estimated_hit_rate or 0.0


def distance_weapon_family(weapon_code: str) -> Literal["AR", "DMR", "SR", "OTHER"]:
    family = weapon_family(weapon_code)
    return family if family in {"AR", "DMR", "SR"} else "OTHER"


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


AR_WEAPONS = frozenset(
    {
        "WeapACE32_C",
        "WeapAK47_C",
        "WeapAUG_C",
        "WeapBerylM762_C",
        "WeapFAMASG2_C",
        "WeapG36C_C",
        "WeapGroza_C",
        "WeapHK416_C",
        "WeapK2_C",
        "WeapM16A4_C",
        "WeapMk47Mutant_C",
        "WeapQBZ95_C",
        "WeapSCAR-L_C",
    }
)

DMR_WEAPONS = frozenset(
    {
        "WeapDragunov_C",
        "WeapFNFal_C",
        "WeapMads_QBU88_C",
        "WeapMini14_C",
        "WeapMk12_C",
        "WeapMk14_C",
        "WeapQBU88_C",
        "WeapSKS_C",
        "WeapVSS_C",
    }
)

SR_WEAPONS = frozenset(
    {
        "WeapAWM_C",
        "WeapKar98k_C",
        "WeapL6_C",
        "WeapM24_C",
        "WeapMosin_C",
        "WeapMosinNagant_C",
        "WeapWin1894_C",
        "WeapWin94_C",
    }
)

SMG_WEAPONS = frozenset(
    {
        "WeapBizonPP19_C",
        "WeapJS9_C",
        "WeapMP5K_C",
        "WeapMP9_C",
        "WeapP90_C",
        "WeapThompson_C",
        "WeapUMP_C",
        "WeapUZI_C",
        "WeapVector_C",
    }
)

LMG_WEAPONS = frozenset({"WeapDP28_C", "WeapM249_C", "WeapMG3_C"})

SHOTGUN_WEAPONS = frozenset(
    {
        "WeapBerreta686_C",
        "WeapDP12_C",
        "WeapOriginS12_C",
        "WeapSaiga12_C",
        "WeapSawnoff_C",
        "WeapWinchester_C",
    }
)

HANDGUN_WEAPONS = frozenset(
    {
        "WeapDesertEagle_C",
        "WeapG18_C",
        "WeapM1911_C",
        "WeapM9_C",
        "WeapNagantM1895_C",
        "WeapRhino_C",
        "Weapvz61Skorpion_C",
    }
)

CROSSBOW_WEAPONS = frozenset({"ProjCrossbow_C", "WeapCrossbow_C"})

WEAPON_CODES_BY_FAMILY: dict[WeaponFamily, frozenset[str]] = {
    "AR": AR_WEAPONS,
    "DMR": DMR_WEAPONS,
    "SR": SR_WEAPONS,
    "SMG": SMG_WEAPONS,
    "LMG": LMG_WEAPONS,
    "SHOTGUN": SHOTGUN_WEAPONS,
    "HANDGUN": HANDGUN_WEAPONS,
    "CROSSBOW": CROSSBOW_WEAPONS,
    "UNCLASSIFIED": frozenset(),
}

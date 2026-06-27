from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


WeaponFamily = Literal["AR", "DMR", "SR", "OTHER"]


@dataclass(frozen=True)
class DistanceBucket:
    label: str
    min_m: int
    max_m: int | None
    weapon_family: WeaponFamily

    @property
    def is_overflow(self) -> bool:
        return self.max_m is None


AR_BREAKS = [0, 5, 10, 15, 20, 25, 50, 75, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
LONG_RANGE_BREAKS = [0, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]


def distance_bucket(distance_m: float, weapon_family: WeaponFamily) -> DistanceBucket:
    if distance_m < 0:
        raise ValueError("distance_m cannot be negative.")

    family = weapon_family.upper()
    if family not in {"AR", "DMR", "SR", "OTHER"}:
        family = "OTHER"

    breaks = AR_BREAKS if family == "AR" else LONG_RANGE_BREAKS
    typed_family = family  # keeps dataclass values constrained after validation

    for start, end in zip(breaks, breaks[1:]):
        if start <= distance_m < end:
            return DistanceBucket(
                label=f"{start}-{end}m",
                min_m=start,
                max_m=end,
                weapon_family=typed_family,  # type: ignore[arg-type]
            )

    return DistanceBucket(
        label="1000m+",
        min_m=1000,
        max_m=None,
        weapon_family=typed_family,  # type: ignore[arg-type]
    )

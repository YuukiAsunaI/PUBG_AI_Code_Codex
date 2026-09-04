from __future__ import annotations

from dataclasses import dataclass
from math import hypot, isfinite, pi
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Literal, Mapping, Sequence
from uuid import uuid4

from pubg_ai.local_settings import LocalSettingsError, LocalSettingsStore
from pubg_ai.map_snapshot_renderer import MAP_WORLD_SIZE_CM
from pubg_ai.time_utils import isoformat_kst


CustomRegionGeometry = Literal["point_radius", "polygon"]
CUSTOM_MAP_REGION_STORE_VERSION = 1
CUSTOM_MAP_REGION_FILE = Path("config/map_regions.local.json")
MAX_CUSTOM_REGION_POINTS = 64
MIN_CUSTOM_REGION_RADIUS_PCT = 0.00001
MAX_CUSTOM_REGION_RADIUS_PCT = 0.5


class CustomMapRegionError(ValueError):
    """Raised when a local custom map region is invalid or unavailable."""


@dataclass(frozen=True)
class CustomMapRegionDefinition:
    region_id: str
    map_name: str
    name_ko: str
    geometry_type: CustomRegionGeometry
    center_x_pct: float
    center_y_pct: float
    radius_pct: float | None
    points_pct: tuple[tuple[float, float], ...]
    priority: int
    enabled: bool
    note: str | None
    created_at_kst: str
    updated_at_kst: str

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> CustomMapRegionDefinition:
        return build_custom_map_region(
            region_id=record.get("region_id"),
            map_name=record.get("map_name"),
            name_ko=record.get("name_ko"),
            geometry_type=record.get("geometry_type"),
            center_x_pct=record.get("center_x_pct"),
            center_y_pct=record.get("center_y_pct"),
            radius_pct=record.get("radius_pct"),
            points_pct=record.get("points_pct"),
            priority=record.get("priority", 100),
            enabled=record.get("enabled", True),
            note=record.get("note"),
            created_at_kst=record.get("created_at_kst"),
            updated_at_kst=record.get("updated_at_kst"),
        )

    @property
    def estimated_area_pct2(self) -> float:
        if self.geometry_type == "point_radius":
            return pi * float(self.radius_pct or 0.0) ** 2
        return abs(_polygon_signed_area(self.points_pct))

    def contains(self, x_pct: float, y_pct: float) -> bool:
        if not self.enabled:
            return False
        if self.geometry_type == "point_radius":
            return hypot(x_pct - self.center_x_pct, y_pct - self.center_y_pct) <= (
                float(self.radius_pct or 0.0) + 1e-12
            )
        return _point_in_polygon(x_pct, y_pct, self.points_pct)

    def to_record(self) -> dict[str, Any]:
        return {
            "region_id": self.region_id,
            "map_name": self.map_name,
            "name": self.name_ko,
            "name_ko": self.name_ko,
            "geometry_type": self.geometry_type,
            "center_x_pct": self.center_x_pct,
            "center_y_pct": self.center_y_pct,
            "radius_pct": self.radius_pct,
            "points_pct": [
                {"x_pct": x_pct, "y_pct": y_pct} for x_pct, y_pct in self.points_pct
            ],
            "priority": self.priority,
            "enabled": self.enabled,
            "note": self.note,
            "created_at_kst": self.created_at_kst,
            "updated_at_kst": self.updated_at_kst,
            "source": "custom",
            "estimated_area_pct2": self.estimated_area_pct2,
        }


def build_custom_map_region(
    *,
    map_name: Any,
    name_ko: Any,
    geometry_type: Any,
    center_x_pct: Any = None,
    center_y_pct: Any = None,
    radius_pct: Any = None,
    points_pct: Any = None,
    priority: Any = 100,
    enabled: Any = True,
    note: Any = None,
    region_id: Any = None,
    created_at_kst: Any = None,
    updated_at_kst: Any = None,
) -> CustomMapRegionDefinition:
    normalized_map_name = str(map_name or "").strip()
    if normalized_map_name not in MAP_WORLD_SIZE_CM:
        raise CustomMapRegionError("지원되는 PUBG 맵을 선택해 주세요.")
    normalized_name = " ".join(str(name_ko or "").split())
    if not normalized_name or len(normalized_name) > 80:
        raise CustomMapRegionError("지역 이름은 1~80자로 입력해 주세요.")
    if any(ord(character) < 32 for character in normalized_name):
        raise CustomMapRegionError("지역 이름에는 제어 문자를 사용할 수 없습니다.")
    normalized_geometry = str(geometry_type or "").strip().lower()
    if normalized_geometry not in {"point_radius", "polygon"}:
        raise CustomMapRegionError("도형은 지점+반경 또는 다각형이어야 합니다.")
    normalized_priority = _bounded_int(priority, minimum=0, maximum=1000, label="우선순위")
    normalized_note = str(note).strip() if note is not None else None
    if normalized_note == "":
        normalized_note = None
    if normalized_note is not None and len(normalized_note) > 300:
        raise CustomMapRegionError("메모는 300자 이하여야 합니다.")

    normalized_points: tuple[tuple[float, float], ...] = ()
    normalized_radius: float | None = None
    if normalized_geometry == "point_radius":
        center_x = _coordinate(center_x_pct, "중심 X")
        center_y = _coordinate(center_y_pct, "중심 Y")
        normalized_radius = _finite_float(radius_pct, "반경")
        if not MIN_CUSTOM_REGION_RADIUS_PCT <= normalized_radius <= MAX_CUSTOM_REGION_RADIUS_PCT:
            raise CustomMapRegionError("반경이 허용 범위를 벗어났습니다.")
    else:
        normalized_points = _normalize_points(points_pct)
        center_x, center_y = _polygon_centroid(normalized_points)
        if _polygon_self_intersects(normalized_points):
            raise CustomMapRegionError("다각형 선이 서로 교차합니다. 꼭짓점 순서를 확인해 주세요.")
        if abs(_polygon_signed_area(normalized_points)) < 1e-8:
            raise CustomMapRegionError("다각형 영역이 너무 작거나 한 직선 위에 있습니다.")

    now = isoformat_kst()
    normalized_id = str(region_id or f"local.{uuid4().hex}").strip()
    if not normalized_id.startswith("local.") or len(normalized_id) > 64:
        raise CustomMapRegionError("사용자 지역 ID 형식이 올바르지 않습니다.")
    return CustomMapRegionDefinition(
        region_id=normalized_id,
        map_name=normalized_map_name,
        name_ko=normalized_name,
        geometry_type=normalized_geometry,  # type: ignore[arg-type]
        center_x_pct=center_x,
        center_y_pct=center_y,
        radius_pct=normalized_radius,
        points_pct=normalized_points,
        priority=normalized_priority,
        enabled=_boolean(enabled),
        note=normalized_note,
        created_at_kst=str(created_at_kst or now),
        updated_at_kst=str(updated_at_kst or now),
    )


def select_custom_map_region(
    definitions: Iterable[CustomMapRegionDefinition],
    *,
    map_name: str,
    x_pct: float,
    y_pct: float,
) -> CustomMapRegionDefinition | None:
    matches = [
        definition
        for definition in definitions
        if definition.map_name == map_name and definition.contains(x_pct, y_pct)
    ]
    if not matches:
        return None
    return min(matches, key=lambda item: (-item.priority, item.estimated_area_pct2, item.region_id))


class CustomMapRegionStore:
    def __init__(self, settings_file: Path, *, base_dir: Path | None = None) -> None:
        self._settings = LocalSettingsStore(settings_file, base_dir=base_dir)
        self._lock = RLock()
        self._cache_signature: tuple[int, int] | None = None
        self._cache: tuple[CustomMapRegionDefinition, ...] = ()

    @property
    def path(self) -> Path:
        return self._settings.settings_file

    def list_regions(
        self,
        *,
        map_name: str | None = None,
        include_disabled: bool = True,
    ) -> tuple[CustomMapRegionDefinition, ...]:
        return tuple(
            region
            for region in self._load()
            if (map_name is None or region.map_name == map_name)
            and (include_disabled or region.enabled)
        )

    def get_region(self, region_id: str) -> CustomMapRegionDefinition | None:
        normalized = str(region_id or "").strip()
        return next((item for item in self._load() if item.region_id == normalized), None)

    def create_region(self, values: Mapping[str, Any]) -> CustomMapRegionDefinition:
        definition = build_custom_map_region(**_definition_values(values))

        def update(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
            parsed = _parse_records(records)
            if any(item.region_id == definition.region_id for item in parsed):
                raise CustomMapRegionError("같은 사용자 지역 ID가 이미 존재합니다.")
            return _sorted_records((*parsed, definition))

        self._settings.update_map_region_override_settings(update)
        self._invalidate()
        return definition

    def update_region(self, region_id: str, values: Mapping[str, Any]) -> CustomMapRegionDefinition:
        existing = self.get_region(region_id)
        if existing is None:
            raise CustomMapRegionError("수정할 사용자 지역을 찾지 못했습니다.")
        merged: dict[str, Any] = {
            "region_id": existing.region_id,
            "map_name": existing.map_name,
            "name_ko": existing.name_ko,
            "geometry_type": existing.geometry_type,
            "center_x_pct": existing.center_x_pct,
            "center_y_pct": existing.center_y_pct,
            "radius_pct": existing.radius_pct,
            "points_pct": existing.points_pct,
            "priority": existing.priority,
            "enabled": existing.enabled,
            "note": existing.note,
            "created_at_kst": existing.created_at_kst,
            "updated_at_kst": isoformat_kst(),
        }
        merged.update(dict(values))
        merged["region_id"] = existing.region_id
        merged["created_at_kst"] = existing.created_at_kst
        merged["updated_at_kst"] = isoformat_kst()
        definition = build_custom_map_region(**merged)

        def update(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
            parsed = _parse_records(records)
            if not any(item.region_id == existing.region_id for item in parsed):
                raise CustomMapRegionError("수정할 사용자 지역을 찾지 못했습니다.")
            return _sorted_records(
                definition if item.region_id == existing.region_id else item for item in parsed
            )

        self._settings.update_map_region_override_settings(update)
        self._invalidate()
        return definition

    def delete_region(self, region_id: str) -> CustomMapRegionDefinition:
        existing = self.get_region(region_id)
        if existing is None:
            raise CustomMapRegionError("삭제할 사용자 지역을 찾지 못했습니다.")

        def update(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
            parsed = _parse_records(records)
            return _sorted_records(item for item in parsed if item.region_id != existing.region_id)

        self._settings.update_map_region_override_settings(update)
        self._invalidate()
        return existing

    def select(self, map_name: str, x_pct: float, y_pct: float) -> CustomMapRegionDefinition | None:
        return select_custom_map_region(
            self.list_regions(map_name=map_name, include_disabled=False),
            map_name=map_name,
            x_pct=x_pct,
            y_pct=y_pct,
        )

    def _load(self) -> tuple[CustomMapRegionDefinition, ...]:
        with self._lock:
            signature = _file_signature(self.path)
            if signature == self._cache_signature:
                return self._cache
            try:
                settings = self._settings.load_map_region_override_settings()
                parsed = _parse_records(settings.regions)
            except LocalSettingsError as exc:
                raise CustomMapRegionError(str(exc)) from exc
            self._cache = tuple(parsed)
            self._cache_signature = signature
            return self._cache

    def _invalidate(self) -> None:
        with self._lock:
            self._cache_signature = None


def _parse_records(records: Iterable[Mapping[str, Any]]) -> list[CustomMapRegionDefinition]:
    parsed: list[CustomMapRegionDefinition] = []
    seen: set[str] = set()
    for record in records:
        definition = CustomMapRegionDefinition.from_record(record)
        if definition.region_id in seen:
            raise CustomMapRegionError("중복된 사용자 지역 ID가 있습니다.")
        seen.add(definition.region_id)
        parsed.append(definition)
    return parsed


def _definition_values(values: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "region_id",
        "map_name",
        "name_ko",
        "geometry_type",
        "center_x_pct",
        "center_y_pct",
        "radius_pct",
        "points_pct",
        "priority",
        "enabled",
        "note",
        "created_at_kst",
        "updated_at_kst",
    }
    return {key: value for key, value in values.items() if key in allowed}


def _sorted_records(definitions: Iterable[CustomMapRegionDefinition]) -> list[dict[str, Any]]:
    return [
        item.to_record()
        for item in sorted(
            definitions,
            key=lambda item: (item.map_name, -item.priority, item.name_ko, item.region_id),
        )
    ]


def _normalize_points(value: Any) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise CustomMapRegionError("다각형 꼭짓점 목록이 필요합니다.")
    if not 3 <= len(value) <= MAX_CUSTOM_REGION_POINTS:
        raise CustomMapRegionError(f"다각형 꼭짓점은 3~{MAX_CUSTOM_REGION_POINTS}개여야 합니다.")
    points: list[tuple[float, float]] = []
    for index, point in enumerate(value, start=1):
        if isinstance(point, Mapping):
            x_value = point.get("x_pct")
            y_value = point.get("y_pct")
        elif isinstance(point, Sequence) and not isinstance(point, (str, bytes)) and len(point) == 2:
            x_value, y_value = point
        else:
            raise CustomMapRegionError(f"{index}번째 꼭짓점 형식이 올바르지 않습니다.")
        points.append((_coordinate(x_value, f"{index}번째 X"), _coordinate(y_value, f"{index}번째 Y")))
    if len(set(points)) < 3:
        raise CustomMapRegionError("서로 다른 꼭짓점이 3개 이상 필요합니다.")
    return tuple(points)


def _polygon_signed_area(points: Sequence[tuple[float, float]]) -> float:
    following = (*points[1:], points[0])
    return 0.5 * sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(points, following))


def _polygon_centroid(points: Sequence[tuple[float, float]]) -> tuple[float, float]:
    signed_area = _polygon_signed_area(points)
    if abs(signed_area) < 1e-12:
        return (
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        )
    factor = 1.0 / (6.0 * signed_area)
    center_x = 0.0
    center_y = 0.0
    for (x1, y1), (x2, y2) in zip(points, (*points[1:], points[0])):
        cross = x1 * y2 - x2 * y1
        center_x += (x1 + x2) * cross
        center_y += (y1 + y2) * cross
    return center_x * factor, center_y * factor


def _point_in_polygon(x_pct: float, y_pct: float, points: Sequence[tuple[float, float]]) -> bool:
    inside = False
    previous = points[-1]
    for current in points:
        if _point_on_segment((x_pct, y_pct), previous, current):
            return True
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y_pct) != (y2 > y_pct):
            boundary_x = (x2 - x1) * (y_pct - y1) / (y2 - y1) + x1
            if x_pct < boundary_x:
                inside = not inside
        previous = current
    return inside


def _polygon_self_intersects(points: Sequence[tuple[float, float]]) -> bool:
    edges = list(zip(points, (*points[1:], points[0])))
    for left_index, left in enumerate(edges):
        for right_index in range(left_index + 1, len(edges)):
            if right_index == left_index + 1 or (left_index == 0 and right_index == len(edges) - 1):
                continue
            right = edges[right_index]
            if _segments_intersect(left[0], left[1], right[0], right[1]):
                return True
    return False


def _segments_intersect(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    def orientation(p: tuple[float, float], q: tuple[float, float], r: tuple[float, float]) -> float:
        return (q[1] - p[1]) * (r[0] - q[0]) - (q[0] - p[0]) * (r[1] - q[1])

    values = (orientation(a, b, c), orientation(a, b, d), orientation(c, d, a), orientation(c, d, b))
    if values[0] * values[1] < 0 and values[2] * values[3] < 0:
        return True
    return any(
        abs(value) < 1e-12 and _point_on_segment(point, start, end)
        for value, point, start, end in (
            (values[0], c, a, b),
            (values[1], d, a, b),
            (values[2], a, c, d),
            (values[3], b, c, d),
        )
    )


def _point_on_segment(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> bool:
    cross = (point[1] - start[1]) * (end[0] - start[0]) - (
        point[0] - start[0]
    ) * (end[1] - start[1])
    if abs(cross) > 1e-10:
        return False
    return (
        min(start[0], end[0]) - 1e-10 <= point[0] <= max(start[0], end[0]) + 1e-10
        and min(start[1], end[1]) - 1e-10 <= point[1] <= max(start[1], end[1]) + 1e-10
    )


def _coordinate(value: Any, label: str) -> float:
    number = _finite_float(value, label)
    if not 0.0 <= number <= 1.0:
        raise CustomMapRegionError(f"{label} 좌표는 0~1 범위여야 합니다.")
    return number


def _finite_float(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CustomMapRegionError(f"{label} 값이 올바르지 않습니다.") from exc
    if not isfinite(number):
        raise CustomMapRegionError(f"{label} 값은 유한한 숫자여야 합니다.")
    return number


def _bounded_int(value: Any, *, minimum: int, maximum: int, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise CustomMapRegionError(f"{label} 값이 올바르지 않습니다.") from exc
    if not minimum <= number <= maximum:
        raise CustomMapRegionError(f"{label}은 {minimum}~{maximum} 범위여야 합니다.")
    return number


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    raise CustomMapRegionError("활성 상태 값이 올바르지 않습니다.")


def _file_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CustomMapRegionError(f"사용자 지역 파일 상태를 확인하지 못했습니다: {exc}") from exc
    return stat.st_mtime_ns, stat.st_size

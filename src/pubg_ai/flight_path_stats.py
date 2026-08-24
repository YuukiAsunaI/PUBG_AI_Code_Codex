from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, time, timedelta
from math import atan2, cos, degrees, floor, hypot, isfinite, pi, sin
from typing import Any, Iterable, Mapping

from pubg_ai.code_translator import translate_code
from pubg_ai.map_snapshot_renderer import MAP_WORLD_SIZE_CM, extend_line_to_world_bounds
from pubg_ai.player_trends import PlayerTrendFilters


@dataclass(frozen=True)
class FlightPathRoute:
    match_id: str
    shard: str
    map_name: str
    map_name_ko: str
    created_at_kst: datetime | None
    sample_count: int
    start_x_pct: float
    start_y_pct: float
    end_x_pct: float
    end_y_pct: float
    physical_angle_degrees: float
    travel_angle_degrees: float
    travel_direction_ko: str
    offset_from_center_m: float
    observed_length_m: float

    def to_record(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "created_at_kst": (
                self.created_at_kst.isoformat() if self.created_at_kst else None
            ),
        }


@dataclass(frozen=True)
class FlightPathCluster:
    cluster_id: str
    map_name: str
    map_name_ko: str
    route_count: int
    map_route_count: int
    map_share: float
    overall_share: float
    start_x_pct: float
    start_y_pct: float
    end_x_pct: float
    end_y_pct: float
    physical_angle_degrees: float
    offset_from_center_m: float
    dominant_direction_ko: str
    dominant_direction_share: float
    forward_count: int
    reverse_count: int
    avg_sample_count: float
    avg_observed_length_m: float
    first_seen_at_kst: datetime | None
    last_seen_at_kst: datetime | None

    def to_record(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "first_seen_at_kst": (
                self.first_seen_at_kst.isoformat() if self.first_seen_at_kst else None
            ),
            "last_seen_at_kst": (
                self.last_seen_at_kst.isoformat() if self.last_seen_at_kst else None
            ),
        }


@dataclass(frozen=True)
class FlightPathMapSummary:
    map_name: str
    map_name_ko: str
    route_count: int
    cluster_count: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FlightPathReport:
    timezone: str
    filters: PlayerTrendFilters
    shard: str | None
    account_id: str | None
    angle_bin_degrees: float
    offset_bin_m: float
    total_route_count: int
    analyzed_route_count: int
    rejected_route_count: int
    available_cluster_count: int
    clusters: list[FlightPathCluster]
    maps: list[FlightPathMapSummary]
    recent_routes: list[FlightPathRoute]
    source_table: str = "match_plane_routes"
    method_ko: str = (
        "텔레메트리에서 관측한 수송기 구간을 맵 경계까지 연장한 뒤 "
        "항로 각도와 맵 중심 기준 수직 거리로 군집화합니다."
    )

    def to_record(self) -> dict[str, Any]:
        return {
            "timezone": self.timezone,
            "filters": self.filters.to_record(),
            "shard": self.shard,
            "account_id": self.account_id,
            "angle_bin_degrees": self.angle_bin_degrees,
            "offset_bin_m": self.offset_bin_m,
            "total_route_count": self.total_route_count,
            "analyzed_route_count": self.analyzed_route_count,
            "rejected_route_count": self.rejected_route_count,
            "available_cluster_count": self.available_cluster_count,
            "returned_cluster_count": len(self.clusters),
            "clusters": [cluster.to_record() for cluster in self.clusters],
            "maps": [item.to_record() for item in self.maps],
            "recent_routes": [route.to_record() for route in self.recent_routes],
            "source_table": self.source_table,
            "method_ko": self.method_ko,
        }


@dataclass(frozen=True)
class _PreparedRoute:
    route: FlightPathRoute
    canonical_angle_radians: float
    offset_from_center_m: float
    direction_sign: int
    center_dx_cm: float
    center_dy_cm: float


class FlightPathStatsService:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def get_report(
        self,
        *,
        shard: str | None = None,
        account_id: str | None = None,
        filters: PlayerTrendFilters | None = None,
        angle_bin_degrees: float = 10.0,
        offset_bin_m: float = 500.0,
        top_per_map: int = 20,
        recent_limit: int = 50,
        route_limit: int = 50000,
    ) -> FlightPathReport:
        normalized_filters = (filters or PlayerTrendFilters()).normalized()
        normalized_angle_bin = _bounded_float(
            angle_bin_degrees, "angle_bin_degrees", 1.0, 45.0
        )
        normalized_offset_bin = _bounded_float(
            offset_bin_m, "offset_bin_m", 50.0, 4000.0
        )
        normalized_top = _bounded_int(top_per_map, "top_per_map", 1, 50)
        normalized_recent = _bounded_int(recent_limit, "recent_limit", 1, 200)
        normalized_route_limit = _bounded_int(route_limit, "route_limit", 100, 100000)
        normalized_shard = _optional_text(shard)
        normalized_account_id = _optional_text(account_id)
        rows = self._get_rows(
            shard=normalized_shard,
            account_id=normalized_account_id,
            filters=normalized_filters,
            route_limit=normalized_route_limit,
        )
        return summarize_flight_paths(
            rows,
            filters=normalized_filters,
            shard=normalized_shard,
            account_id=normalized_account_id,
            angle_bin_degrees=normalized_angle_bin,
            offset_bin_m=normalized_offset_bin,
            top_per_map=normalized_top,
            recent_limit=normalized_recent,
        )

    def _get_rows(
        self,
        *,
        shard: str | None,
        account_id: str | None,
        filters: PlayerTrendFilters,
        route_limit: int,
    ) -> list[dict[str, Any]]:
        conditions = [
            "routes.start_x IS NOT NULL",
            "routes.start_y IS NOT NULL",
            "routes.end_x IS NOT NULL",
            "routes.end_y IS NOT NULL",
            "matches.map_name IS NOT NULL",
        ]
        params: list[Any] = []
        if shard is not None:
            conditions.append("matches.shard = %s")
            params.append(shard.lower())
        if account_id is not None:
            conditions.append(
                "EXISTS ("
                "SELECT 1 FROM match_participants scoped_participant "
                "WHERE scoped_participant.match_id = matches.match_id "
                "AND scoped_participant.account_id = %s"
                ")"
            )
            params.append(account_id)

        for column, value in (
            ("matches.game_mode", filters.game_mode),
            ("matches.team_mode", filters.team_mode),
            ("matches.perspective", filters.perspective),
            ("matches.match_type", filters.match_type),
            ("matches.map_name", filters.map_name),
            ("matches.season_state", filters.season_state),
        ):
            if value is not None:
                conditions.append(f"{column} = %s")
                params.append(value)
        if filters.is_custom_match is not None:
            conditions.append("matches.is_custom_match = %s")
            params.append(1 if filters.is_custom_match else 0)
        for expression, value in (
            ("YEAR(matches.created_at_kst)", filters.year),
            ("QUARTER(matches.created_at_kst)", filters.quarter),
            ("MONTH(matches.created_at_kst)", filters.month),
            ("HOUR(matches.created_at_kst)", filters.hour),
        ):
            if value is not None:
                conditions.append(f"{expression} = %s")
                params.append(value)
        if filters.exact_date_kst is not None:
            conditions.append("matches.created_at_kst >= %s")
            params.append(datetime.combine(filters.exact_date_kst, time.min))
            conditions.append("matches.created_at_kst < %s")
            params.append(
                datetime.combine(filters.exact_date_kst + timedelta(days=1), time.min)
            )
        if filters.from_date_kst is not None:
            conditions.append("matches.created_at_kst >= %s")
            params.append(datetime.combine(filters.from_date_kst, time.min))
        if filters.to_date_kst is not None:
            conditions.append("matches.created_at_kst < %s")
            params.append(
                datetime.combine(filters.to_date_kst + timedelta(days=1), time.min)
            )
        params.append(route_limit)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    routes.match_id,
                    routes.sample_count,
                    routes.start_x,
                    routes.start_y,
                    routes.end_x,
                    routes.end_y,
                    matches.shard,
                    matches.map_name,
                    matches.created_at_kst
                FROM match_plane_routes routes
                INNER JOIN matches
                    ON matches.match_id = routes.match_id
                WHERE """
                + " AND ".join(conditions)
                + """
                ORDER BY matches.created_at_kst DESC, routes.match_id DESC
                LIMIT %s
                """,
                params,
            )
            return [dict(row) for row in cursor.fetchall()]


def summarize_flight_paths(
    rows: Iterable[Mapping[str, Any]],
    *,
    filters: PlayerTrendFilters | None = None,
    shard: str | None = None,
    account_id: str | None = None,
    angle_bin_degrees: float = 10.0,
    offset_bin_m: float = 500.0,
    top_per_map: int = 20,
    recent_limit: int = 50,
) -> FlightPathReport:
    normalized_filters = (filters or PlayerTrendFilters()).normalized()
    angle_bin_count = max(1, round(180.0 / angle_bin_degrees))
    actual_angle_bin = 180.0 / angle_bin_count
    prepared: list[_PreparedRoute] = []
    total = 0
    for row in rows:
        total += 1
        route = _prepare_route(row)
        if route is not None:
            prepared.append(route)

    buckets: dict[tuple[str, int, int], list[_PreparedRoute]] = {}
    for item in prepared:
        angle_degrees = degrees(item.canonical_angle_radians)
        angle_index = int(
            floor((angle_degrees + actual_angle_bin / 2.0) / actual_angle_bin)
        ) % angle_bin_count
        bin_angle = angle_index * actual_angle_bin * pi / 180.0
        bin_normal_x, bin_normal_y = -sin(bin_angle), cos(bin_angle)
        bin_offset_m = (
            item.center_dx_cm * bin_normal_x + item.center_dy_cm * bin_normal_y
        ) / 100.0
        offset_index = int(floor(bin_offset_m / offset_bin_m + 0.5))
        key = (item.route.map_name, angle_index, offset_index)
        buckets.setdefault(key, []).append(
            replace(item, offset_from_center_m=bin_offset_m)
        )

    map_route_counts: dict[str, int] = {}
    for item in prepared:
        map_route_counts[item.route.map_name] = (
            map_route_counts.get(item.route.map_name, 0) + 1
        )

    all_clusters: list[FlightPathCluster] = []
    for (map_name, angle_index, offset_index), items in buckets.items():
        cluster = _build_cluster(
            map_name=map_name,
            angle_index=angle_index,
            offset_index=offset_index,
            items=items,
            map_route_count=map_route_counts[map_name],
            overall_route_count=len(prepared),
            angle_bin_degrees=actual_angle_bin,
            offset_bin_m=offset_bin_m,
        )
        if cluster is not None:
            all_clusters.append(cluster)

    selected_clusters: list[FlightPathCluster] = []
    for map_name in sorted(
        map_route_counts,
        key=lambda key: (-map_route_counts[key], translate_code(key, "map")),
    ):
        map_clusters = sorted(
            (item for item in all_clusters if item.map_name == map_name),
            key=lambda item: (
                -item.route_count,
                -item.dominant_direction_share,
                abs(item.offset_from_center_m),
                item.cluster_id,
            ),
        )
        selected_clusters.extend(map_clusters[:top_per_map])

    maps = [
        FlightPathMapSummary(
            map_name=map_name,
            map_name_ko=translate_code(map_name, "map"),
            route_count=route_count,
            cluster_count=sum(
                1 for cluster in all_clusters if cluster.map_name == map_name
            ),
        )
        for map_name, route_count in sorted(
            map_route_counts.items(),
            key=lambda item: (-item[1], translate_code(item[0], "map")),
        )
    ]
    recent_routes = [
        item.route
        for item in sorted(
            prepared,
            key=lambda item: (
                item.route.created_at_kst or datetime.min,
                item.route.match_id,
            ),
            reverse=True,
        )[:recent_limit]
    ]
    return FlightPathReport(
        timezone="Asia/Seoul",
        filters=normalized_filters,
        shard=shard,
        account_id=account_id,
        angle_bin_degrees=actual_angle_bin,
        offset_bin_m=offset_bin_m,
        total_route_count=total,
        analyzed_route_count=len(prepared),
        rejected_route_count=total - len(prepared),
        available_cluster_count=len(all_clusters),
        clusters=selected_clusters,
        maps=maps,
        recent_routes=recent_routes,
    )


def _prepare_route(row: Mapping[str, Any]) -> _PreparedRoute | None:
    map_name = _optional_text(row.get("map_name"))
    if map_name is None:
        return None
    world_size_cm = MAP_WORLD_SIZE_CM.get(map_name)
    if world_size_cm is None:
        return None
    values = [
        _finite_float(row.get("start_x")),
        _finite_float(row.get("start_y")),
        _finite_float(row.get("end_x")),
        _finite_float(row.get("end_y")),
    ]
    if any(value is None for value in values):
        return None
    start_x, start_y, end_x, end_y = (float(value) for value in values)
    dx, dy = end_x - start_x, end_y - start_y
    observed_length_m = hypot(dx, dy) / 100.0
    if observed_length_m < 1.0:
        return None
    extended = extend_line_to_world_bounds(
        start_x, start_y, end_x, end_y, world_size_cm
    )
    if extended is None:
        return None

    canonical_angle = atan2(dy, dx) % pi
    unit_x, unit_y = cos(canonical_angle), sin(canonical_angle)
    normal_x, normal_y = -unit_y, unit_x
    midpoint_x = (start_x + end_x) / 2.0
    midpoint_y = (start_y + end_y) / 2.0
    center = world_size_cm / 2.0
    offset_m = (
        (midpoint_x - center) * normal_x + (midpoint_y - center) * normal_y
    ) / 100.0
    direction_sign = 1 if dx * unit_x + dy * unit_y >= 0.0 else -1
    travel_angle = atan2(dy, dx) % (2.0 * pi)
    route = FlightPathRoute(
        match_id=str(row.get("match_id") or ""),
        shard=str(row.get("shard") or ""),
        map_name=map_name,
        map_name_ko=translate_code(map_name, "map"),
        created_at_kst=_datetime_or_none(row.get("created_at_kst")),
        sample_count=max(0, _int(row.get("sample_count"))),
        start_x_pct=extended[0] / world_size_cm,
        start_y_pct=extended[1] / world_size_cm,
        end_x_pct=extended[2] / world_size_cm,
        end_y_pct=extended[3] / world_size_cm,
        physical_angle_degrees=degrees(canonical_angle),
        travel_angle_degrees=degrees(travel_angle),
        travel_direction_ko=_direction_name_ko(degrees(travel_angle)),
        offset_from_center_m=offset_m,
        observed_length_m=observed_length_m,
    )
    return _PreparedRoute(
        route=route,
        canonical_angle_radians=canonical_angle,
        offset_from_center_m=offset_m,
        direction_sign=direction_sign,
        center_dx_cm=midpoint_x - center,
        center_dy_cm=midpoint_y - center,
    )


def _build_cluster(
    *,
    map_name: str,
    angle_index: int,
    offset_index: int,
    items: list[_PreparedRoute],
    map_route_count: int,
    overall_route_count: int,
    angle_bin_degrees: float,
    offset_bin_m: float,
) -> FlightPathCluster | None:
    world_size_cm = MAP_WORLD_SIZE_CM[map_name]
    count = len(items)
    cosine_sum = sum(cos(2.0 * item.canonical_angle_radians) for item in items)
    sine_sum = sum(sin(2.0 * item.canonical_angle_radians) for item in items)
    angle = 0.5 * atan2(sine_sum, cosine_sum)
    if angle < 0.0:
        angle += pi
    unit_x, unit_y = cos(angle), sin(angle)
    normal_x, normal_y = -unit_y, unit_x
    avg_offset_m = sum(
        (item.center_dx_cm * normal_x + item.center_dy_cm * normal_y) / 100.0
        for item in items
    ) / count
    center = world_size_cm / 2.0
    point_x = center + avg_offset_m * 100.0 * normal_x
    point_y = center + avg_offset_m * 100.0 * normal_y
    extended = extend_line_to_world_bounds(
        point_x - unit_x * world_size_cm,
        point_y - unit_y * world_size_cm,
        point_x + unit_x * world_size_cm,
        point_y + unit_y * world_size_cm,
        world_size_cm,
    )
    if extended is None:
        return None

    forward_count = sum(1 for item in items if item.direction_sign > 0)
    reverse_count = count - forward_count
    dominant_sign = 1 if forward_count >= reverse_count else -1
    if dominant_sign < 0:
        extended = (extended[2], extended[3], extended[0], extended[1])
    travel_angle = atan2(
        extended[3] - extended[1],
        extended[2] - extended[0],
    ) % (2.0 * pi)
    seen = [
        item.route.created_at_kst
        for item in items
        if item.route.created_at_kst is not None
    ]
    return FlightPathCluster(
        cluster_id=(
            f"{map_name}:angle{angle_index}:offset{offset_index}:"
            f"a{angle_bin_degrees:g}:o{offset_bin_m:g}"
        ),
        map_name=map_name,
        map_name_ko=translate_code(map_name, "map"),
        route_count=count,
        map_route_count=map_route_count,
        map_share=count / max(1, map_route_count),
        overall_share=count / max(1, overall_route_count),
        start_x_pct=extended[0] / world_size_cm,
        start_y_pct=extended[1] / world_size_cm,
        end_x_pct=extended[2] / world_size_cm,
        end_y_pct=extended[3] / world_size_cm,
        physical_angle_degrees=degrees(angle),
        offset_from_center_m=avg_offset_m,
        dominant_direction_ko=_direction_name_ko(degrees(travel_angle)),
        dominant_direction_share=max(forward_count, reverse_count) / count,
        forward_count=forward_count,
        reverse_count=reverse_count,
        avg_sample_count=sum(item.route.sample_count for item in items) / count,
        avg_observed_length_m=(
            sum(item.route.observed_length_m for item in items) / count
        ),
        first_seen_at_kst=min(seen) if seen else None,
        last_seen_at_kst=max(seen) if seen else None,
    )


def _direction_name_ko(angle_degrees: float) -> str:
    labels = (
        "동쪽",
        "남동쪽",
        "남쪽",
        "남서쪽",
        "서쪽",
        "북서쪽",
        "북쪽",
        "북동쪽",
    )
    return labels[int(floor((angle_degrees % 360.0 + 22.5) / 45.0)) % 8]


def _bounded_float(value: Any, label: str, minimum: float, maximum: float) -> float:
    candidate = _finite_float(value)
    if candidate is None or not minimum <= candidate <= maximum:
        raise ValueError(f"{label} must be between {minimum:g} and {maximum:g}.")
    return candidate


def _bounded_int(value: Any, label: str, minimum: int, maximum: int) -> int:
    try:
        candidate = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer.") from exc
    if not minimum <= candidate <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}.")
    return candidate


def _finite_float(value: Any) -> float | None:
    try:
        candidate = float(value)
    except (TypeError, ValueError):
        return None
    return candidate if isfinite(candidate) else None


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _datetime_or_none(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = _optional_text(value)
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from math import floor, isfinite
from typing import Any, Iterable, Mapping

from pubg_ai.code_translator import translate_code
from pubg_ai.flight_path_stats import FlightPathStatsService
from pubg_ai.map_snapshot_renderer import MAP_WORLD_SIZE_CM
from pubg_ai.player_trends import PlayerTrendFilters


@dataclass(frozen=True)
class CircleSample:
    match_id: str
    shard: str
    map_name: str
    map_name_ko: str
    created_at_kst: datetime | None
    phase_number: int
    center_x_pct: float
    center_y_pct: float
    radius_pct: float
    center_x_cm: float
    center_y_cm: float
    radius_m: float


@dataclass(frozen=True)
class CircleCluster:
    cluster_id: str
    map_name: str
    map_name_ko: str
    phase_number: int
    circle_count: int
    phase_circle_count: int
    phase_share: float
    center_x_pct: float
    center_y_pct: float
    radius_pct: float
    center_x_cm: float
    center_y_cm: float
    radius_m: float
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
class CirclePhaseSummary:
    phase_number: int
    circle_count: int
    match_count: int
    cluster_count: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CircleMapSummary:
    map_name: str
    map_name_ko: str
    circle_count: int
    match_count: int
    cluster_count: int
    available_phases: list[int]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CircleReport:
    timezone: str
    filters: PlayerTrendFilters
    shard: str | None
    account_id: str | None
    phase_number: int | None
    flight_cluster_id: str | None
    route_match_count: int | None
    center_bin_m: float
    radius_bin_m: float
    minimum_circle_count: int
    max_clusters_per_phase: int | None
    total_circle_count: int
    analyzed_circle_count: int
    analyzed_match_count: int
    filtered_out_by_route: int
    rejected_circle_count: int
    available_cluster_count: int
    clusters: list[CircleCluster]
    maps: list[CircleMapSummary]
    phases: list[CirclePhaseSummary]
    source_table: str = "match_phase_events"
    method_ko: str = (
        "공식 텔레메트리의 정수 isGame 단계에서 나타난 다음 안전구역의 "
        "poisonGasWarning 위치와 반경을 서클 번호별로 추출하고, 중심 좌표와 "
        "반경이 가까운 서클을 묶어 출현 빈도를 계산합니다."
    )

    def to_record(self) -> dict[str, Any]:
        return {
            "timezone": self.timezone,
            "filters": self.filters.to_record(),
            "shard": self.shard,
            "account_id": self.account_id,
            "phase_number": self.phase_number,
            "flight_cluster_id": self.flight_cluster_id,
            "route_match_count": self.route_match_count,
            "center_bin_m": self.center_bin_m,
            "radius_bin_m": self.radius_bin_m,
            "minimum_circle_count": self.minimum_circle_count,
            "max_clusters_per_phase": self.max_clusters_per_phase,
            "total_circle_count": self.total_circle_count,
            "analyzed_circle_count": self.analyzed_circle_count,
            "analyzed_match_count": self.analyzed_match_count,
            "filtered_out_by_route": self.filtered_out_by_route,
            "rejected_circle_count": self.rejected_circle_count,
            "available_cluster_count": self.available_cluster_count,
            "returned_cluster_count": len(self.clusters),
            "clusters": [cluster.to_record() for cluster in self.clusters],
            "maps": [item.to_record() for item in self.maps],
            "phases": [item.to_record() for item in self.phases],
            "source_table": self.source_table,
            "method_ko": self.method_ko,
        }


class CircleStatsService:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def get_report(
        self,
        *,
        shard: str | None = None,
        account_id: str | None = None,
        filters: PlayerTrendFilters | None = None,
        phase_number: int | None = None,
        flight_cluster_id: str | None = None,
        angle_bin_degrees: float = 10.0,
        offset_bin_m: float = 500.0,
        center_bin_m: float = 500.0,
        radius_bin_m: float = 250.0,
        min_circle_count: int = 1,
        top_per_phase: int = 0,
        circle_limit: int = 100000,
        route_limit: int = 50000,
    ) -> CircleReport:
        normalized_filters = (filters or PlayerTrendFilters()).normalized()
        normalized_phase = (
            _bounded_int(phase_number, "phase_number", 1, 20)
            if phase_number is not None
            else None
        )
        normalized_center_bin = _bounded_float(
            center_bin_m, "center_bin_m", 100.0, 4000.0
        )
        normalized_radius_bin = _bounded_float(
            radius_bin_m, "radius_bin_m", 10.0, 2000.0
        )
        normalized_minimum = _bounded_int(
            min_circle_count, "min_circle_count", 1, 200000
        )
        normalized_top = _bounded_int(top_per_phase, "top_per_phase", 0, 10000)
        normalized_circle_limit = _bounded_int(
            circle_limit, "circle_limit", 100, 200000
        )
        normalized_shard = _optional_text(shard)
        normalized_account_id = _optional_text(account_id)
        normalized_flight_cluster_id = _optional_text(flight_cluster_id)

        route_match_ids: set[str] | None = None
        if normalized_flight_cluster_id is not None:
            memberships = FlightPathStatsService(self.connection).get_cluster_match_ids(
                shard=normalized_shard,
                account_id=normalized_account_id,
                filters=normalized_filters,
                angle_bin_degrees=angle_bin_degrees,
                offset_bin_m=offset_bin_m,
                route_limit=route_limit,
            )
            route_match_ids = memberships.get(normalized_flight_cluster_id, set())

        rows = self._get_rows(
            shard=normalized_shard,
            account_id=normalized_account_id,
            filters=normalized_filters,
            phase_number=normalized_phase,
            match_ids=route_match_ids,
            circle_limit=normalized_circle_limit,
        )
        return summarize_circle_patterns(
            rows,
            filters=normalized_filters,
            shard=normalized_shard,
            account_id=normalized_account_id,
            phase_number=normalized_phase,
            flight_cluster_id=normalized_flight_cluster_id,
            route_match_ids=route_match_ids,
            center_bin_m=normalized_center_bin,
            radius_bin_m=normalized_radius_bin,
            min_circle_count=normalized_minimum,
            top_per_phase=normalized_top,
        )

    def _get_rows(
        self,
        *,
        shard: str | None,
        account_id: str | None,
        filters: PlayerTrendFilters,
        phase_number: int | None,
        match_ids: set[str] | None,
        circle_limit: int,
    ) -> list[dict[str, Any]]:
        conditions = [
            "matches.map_name IS NOT NULL",
            "phases.common_is_game >= 1",
            "phases.common_is_game = FLOOR(phases.common_is_game)",
            "phases.poison_gas_warning_x IS NOT NULL",
            "phases.poison_gas_warning_y IS NOT NULL",
            "phases.poison_gas_warning_radius > 0",
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
        if match_ids is not None:
            if match_ids:
                ordered_match_ids = sorted(match_ids)
                conditions.append(
                    "phases.match_id IN ("
                    + ", ".join("%s" for _item in ordered_match_ids)
                    + ")"
                )
                params.extend(ordered_match_ids)
            else:
                conditions.append("1 = 0")

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
        if phase_number is not None:
            conditions.append("phases.common_is_game = %s")
            params.append(float(phase_number))
        params.append(circle_limit)

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    phases.match_id,
                    phases.common_is_game,
                    phases.poison_gas_warning_x,
                    phases.poison_gas_warning_y,
                    phases.poison_gas_warning_radius,
                    matches.shard,
                    matches.map_name,
                    matches.created_at_kst
                FROM match_phase_events phases
                INNER JOIN (
                    SELECT match_id, common_is_game, MIN(event_index) AS event_index
                    FROM match_phase_events
                    WHERE common_is_game >= 1
                      AND common_is_game = FLOOR(common_is_game)
                      AND poison_gas_warning_x IS NOT NULL
                      AND poison_gas_warning_y IS NOT NULL
                      AND poison_gas_warning_radius > 0
                    GROUP BY match_id, common_is_game
                ) AS first_phase
                    ON first_phase.match_id = phases.match_id
                   AND first_phase.common_is_game = phases.common_is_game
                   AND first_phase.event_index = phases.event_index
                INNER JOIN analysis_matches AS matches
                    ON matches.match_id = phases.match_id
                WHERE """
                + " AND ".join(conditions)
                + """
                ORDER BY matches.created_at_kst DESC, phases.match_id DESC,
                         phases.common_is_game ASC
                LIMIT %s
                """,
                params,
            )
            return [dict(row) for row in cursor.fetchall()]


def summarize_circle_patterns(
    rows: Iterable[Mapping[str, Any]],
    *,
    filters: PlayerTrendFilters | None = None,
    shard: str | None = None,
    account_id: str | None = None,
    phase_number: int | None = None,
    flight_cluster_id: str | None = None,
    route_match_ids: set[str] | None = None,
    center_bin_m: float = 500.0,
    radius_bin_m: float = 250.0,
    min_circle_count: int = 1,
    top_per_phase: int = 0,
) -> CircleReport:
    normalized_filters = (filters or PlayerTrendFilters()).normalized()
    prepared: list[CircleSample] = []
    seen: set[tuple[str, int]] = set()
    total = 0
    filtered_out_by_route = 0
    rejected = 0
    for row in rows:
        total += 1
        match_id = _optional_text(row.get("match_id"))
        if route_match_ids is not None and match_id not in route_match_ids:
            filtered_out_by_route += 1
            continue
        circle = _prepare_circle(row)
        if circle is None:
            rejected += 1
            continue
        if phase_number is not None and circle.phase_number != phase_number:
            continue
        identity = (circle.match_id, circle.phase_number)
        if identity in seen:
            continue
        seen.add(identity)
        prepared.append(circle)

    buckets: dict[tuple[str, int, int, int, int], list[CircleSample]] = {}
    for circle in prepared:
        x_index = int(floor((circle.center_x_cm / 100.0) / center_bin_m + 0.5))
        y_index = int(floor((circle.center_y_cm / 100.0) / center_bin_m + 0.5))
        radius_index = int(floor(circle.radius_m / radius_bin_m + 0.5))
        key = (
            circle.map_name,
            circle.phase_number,
            x_index,
            y_index,
            radius_index,
        )
        buckets.setdefault(key, []).append(circle)

    phase_circle_counts: dict[tuple[str, int], int] = {}
    for circle in prepared:
        key = (circle.map_name, circle.phase_number)
        phase_circle_counts[key] = phase_circle_counts.get(key, 0) + 1

    all_clusters = [
        _build_circle_cluster(
            key=key,
            items=items,
            phase_circle_count=phase_circle_counts[(key[0], key[1])],
            center_bin_m=center_bin_m,
            radius_bin_m=radius_bin_m,
        )
        for key, items in buckets.items()
    ]
    selected_clusters: list[CircleCluster] = []
    for map_name, phase in sorted(
        phase_circle_counts,
        key=lambda item: (translate_code(item[0], "map"), item[1]),
    ):
        candidates = sorted(
            (
                cluster
                for cluster in all_clusters
                if cluster.map_name == map_name
                and cluster.phase_number == phase
                and cluster.circle_count >= min_circle_count
            ),
            key=lambda item: (-item.circle_count, item.cluster_id),
        )
        selected_clusters.extend(
            candidates if top_per_phase == 0 else candidates[:top_per_phase]
        )

    map_names = sorted(
        {circle.map_name for circle in prepared},
        key=lambda value: translate_code(value, "map"),
    )
    maps = [
        CircleMapSummary(
            map_name=map_name,
            map_name_ko=translate_code(map_name, "map"),
            circle_count=sum(1 for item in prepared if item.map_name == map_name),
            match_count=len(
                {item.match_id for item in prepared if item.map_name == map_name}
            ),
            cluster_count=sum(1 for item in all_clusters if item.map_name == map_name),
            available_phases=sorted(
                {item.phase_number for item in prepared if item.map_name == map_name}
            ),
        )
        for map_name in map_names
    ]
    phase_values = sorted({item.phase_number for item in prepared})
    phases = [
        CirclePhaseSummary(
            phase_number=phase,
            circle_count=sum(1 for item in prepared if item.phase_number == phase),
            match_count=len(
                {item.match_id for item in prepared if item.phase_number == phase}
            ),
            cluster_count=sum(
                1 for item in all_clusters if item.phase_number == phase
            ),
        )
        for phase in phase_values
    ]
    return CircleReport(
        timezone="Asia/Seoul",
        filters=normalized_filters,
        shard=shard,
        account_id=account_id,
        phase_number=phase_number,
        flight_cluster_id=flight_cluster_id,
        route_match_count=(len(route_match_ids) if route_match_ids is not None else None),
        center_bin_m=center_bin_m,
        radius_bin_m=radius_bin_m,
        minimum_circle_count=min_circle_count,
        max_clusters_per_phase=(top_per_phase or None),
        total_circle_count=total,
        analyzed_circle_count=len(prepared),
        analyzed_match_count=len({item.match_id for item in prepared}),
        filtered_out_by_route=filtered_out_by_route,
        rejected_circle_count=rejected,
        available_cluster_count=len(all_clusters),
        clusters=selected_clusters,
        maps=maps,
        phases=phases,
    )


def _prepare_circle(row: Mapping[str, Any]) -> CircleSample | None:
    match_id = _optional_text(row.get("match_id"))
    map_name = _optional_text(row.get("map_name"))
    world_size_cm = MAP_WORLD_SIZE_CM.get(map_name or "")
    if match_id is None or map_name is None or world_size_cm is None:
        return None
    phase_value = _optional_float(row.get("common_is_game"))
    center_x = _optional_float(row.get("poison_gas_warning_x"))
    center_y = _optional_float(row.get("poison_gas_warning_y"))
    radius = _optional_float(row.get("poison_gas_warning_radius"))
    if None in (phase_value, center_x, center_y, radius):
        return None
    assert phase_value is not None
    assert center_x is not None
    assert center_y is not None
    assert radius is not None
    phase = int(round(phase_value))
    if (
        phase < 1
        or phase > 20
        or abs(phase_value - phase) > 0.001
        or radius <= 0
        or center_x < 0
        or center_y < 0
        or center_x > world_size_cm
        or center_y > world_size_cm
    ):
        return None
    return CircleSample(
        match_id=match_id,
        shard=_optional_text(row.get("shard")) or "",
        map_name=map_name,
        map_name_ko=translate_code(map_name, "map"),
        created_at_kst=_optional_datetime(row.get("created_at_kst")),
        phase_number=phase,
        center_x_pct=center_x / world_size_cm,
        center_y_pct=center_y / world_size_cm,
        radius_pct=radius / world_size_cm,
        center_x_cm=center_x,
        center_y_cm=center_y,
        radius_m=radius / 100.0,
    )


def _build_circle_cluster(
    *,
    key: tuple[str, int, int, int, int],
    items: list[CircleSample],
    phase_circle_count: int,
    center_bin_m: float,
    radius_bin_m: float,
) -> CircleCluster:
    map_name, phase, x_index, y_index, radius_index = key
    count = len(items)
    seen = [item.created_at_kst for item in items if item.created_at_kst is not None]
    center_x_pct = sum(item.center_x_pct for item in items) / count
    center_y_pct = sum(item.center_y_pct for item in items) / count
    radius_pct = sum(item.radius_pct for item in items) / count
    center_x_cm = sum(item.center_x_cm for item in items) / count
    center_y_cm = sum(item.center_y_cm for item in items) / count
    radius_m = sum(item.radius_m for item in items) / count
    return CircleCluster(
        cluster_id=(
            f"{map_name}:phase{phase}:x{x_index}:y{y_index}:r{radius_index}:"
            f"c{center_bin_m:g}:rb{radius_bin_m:g}"
        ),
        map_name=map_name,
        map_name_ko=translate_code(map_name, "map"),
        phase_number=phase,
        circle_count=count,
        phase_circle_count=phase_circle_count,
        phase_share=count / max(1, phase_circle_count),
        center_x_pct=center_x_pct,
        center_y_pct=center_y_pct,
        radius_pct=radius_pct,
        center_x_cm=center_x_cm,
        center_y_cm=center_y_cm,
        radius_m=radius_m,
        first_seen_at_kst=min(seen) if seen else None,
        last_seen_at_kst=max(seen) if seen else None,
    )


def _bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer.") from exc
    if normalized < minimum or normalized > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}.")
    return normalized


def _bounded_float(value: Any, field: str, minimum: float, maximum: float) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number.") from exc
    if not isfinite(normalized) or normalized < minimum or normalized > maximum:
        raise ValueError(f"{field} must be between {minimum:g} and {maximum:g}.")
    return normalized


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    return normalized if isfinite(normalized) else None


def _optional_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None

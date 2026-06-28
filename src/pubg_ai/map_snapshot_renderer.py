from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable, Mapping
import json
import os
import tempfile
import urllib.error
import urllib.request

from PIL import Image, ImageDraw, ImageFont

from pubg_ai.replay_storage import ReplayArtifactStore, StoredReplayArtifact
from pubg_ai.time_utils import now_kst, to_kst


RENDERER_VERSION = "map-snapshot-v1"
MAP_ASSET_BASE_URL = "https://raw.githubusercontent.com/pubg/api-assets/master/Assets/Maps"

MAP_ASSET_FILENAMES = {
    "Baltic_Main": "Erangel_Main_No_Text_Low_Res.png",
    "Desert_Main": "Miramar_Main_No_Text_Low_Res.png",
    "DihorOtok_Main": "Vikendi_Main_No_Text_Low_Res.png",
    "Erangel_Main": "Erangel_Main_No_Text_Low_Res.png",
    "Heaven_Main": "Haven_Main_No_Text_Low_Res.png",
    "Kiki_Main": "Deston_Main_No_Text_Low_Res.png",
    "Neon_Main": "Rondo_Main_No_Text_Low_Res.png",
    "Range_Main": "Camp_Jackal_Main_No_Text_Low_Res.png",
    "Savage_Main": "Sanhok_Main_No_Text_Low_Res.png",
    "Summerland_Main": "Karakin_Main_No_Text_Low_Res.png",
    "Tiger_Main": "Taego_Main_No_Text_Low_Res.png",
    "Chimera_Main": "Paramo_Main_Low_Res.png",
}

MAP_WORLD_SIZE_CM = {
    "Baltic_Main": 816000.0,
    "Desert_Main": 816000.0,
    "DihorOtok_Main": 612000.0,
    "Erangel_Main": 816000.0,
    "Heaven_Main": 102000.0,
    "Kiki_Main": 816000.0,
    "Neon_Main": 816000.0,
    "Range_Main": 204000.0,
    "Savage_Main": 408000.0,
    "Summerland_Main": 204000.0,
    "Tiger_Main": 816000.0,
    "Chimera_Main": 306000.0,
}

DEFAULT_WORLD_SIZE_CM = 816000.0


class MapSnapshotError(RuntimeError):
    """Raised when replay map snapshots cannot be generated."""


@dataclass(frozen=True)
class SnapshotPoint:
    x: float | None
    y: float | None


@dataclass(frozen=True)
class PositionSample:
    event_index: int
    x: float | None
    y: float | None
    common_is_game: float | None = None


@dataclass(frozen=True)
class CombatLocation:
    action: str
    x: float | None
    y: float | None
    related_x: float | None = None
    related_y: float | None = None
    damage_causer_name: str | None = None
    damage_reason: str | None = None
    distance_m: float | None = None
    is_headshot: bool = False


@dataclass(frozen=True)
class CarePackageLocation:
    event_type: str
    x: float | None
    y: float | None


@dataclass(frozen=True)
class PlaneRoute:
    start_x: float | None
    start_y: float | None
    end_x: float | None
    end_y: float | None


@dataclass(frozen=True)
class MapSnapshotContext:
    match_id: str
    shard: str
    map_name: str | None
    game_mode: str | None
    match_type: str | None
    created_at_kst: datetime | None
    account_id: str
    player_name: str | None
    positions: list[PositionSample]
    landing_points: list[PositionSample]
    combat_locations: list[CombatLocation]
    care_packages: list[CarePackageLocation]
    plane_route: PlaneRoute | None


@dataclass(frozen=True)
class MapSnapshotResult:
    candidate_snapshots: int
    generated_snapshots: int
    skipped_existing: int
    skipped_no_position: int
    failed_snapshots: int
    artifacts: list[StoredReplayArtifact]

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["artifacts"] = [artifact.to_record() for artifact in self.artifacts]
        return record


class MapAssetProvider:
    def __init__(self, cache_root: Path) -> None:
        self.cache_root = cache_root.expanduser()

    def load_map(self, map_name: str | None) -> Image.Image | None:
        filename = MAP_ASSET_FILENAMES.get(map_name or "")
        if filename is None:
            return None

        path = self.cache_root / "map_assets" / filename
        if not path.exists():
            self._download(filename, path)

        try:
            return Image.open(path).convert("RGB")
        except OSError:
            return None

    def _download(self, filename: str, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"{MAP_ASSET_BASE_URL}/{filename}"
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                data = response.read()
        except (urllib.error.URLError, TimeoutError, OSError):
            return

        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                delete=False,
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
            ) as temp_file:
                temp_file.write(data)
                temp_file.flush()
                os.fsync(temp_file.fileno())
                temp_path = Path(temp_file.name)
            os.replace(temp_path, path)
        except OSError:
            return


class MapSnapshotProcessor:
    def __init__(
        self,
        connection: Any,
        replay_store: ReplayArtifactStore,
        asset_provider: MapAssetProvider | None = None,
    ) -> None:
        self.connection = connection
        self.replay_store = replay_store
        self.asset_provider = asset_provider or MapAssetProvider(replay_store.root / "cache")

    def generate_player_snapshots(
        self,
        *,
        limit: int = 10,
        force: bool = False,
    ) -> MapSnapshotResult:
        limit = max(1, min(limit, 200))
        jobs = self._list_snapshot_jobs(limit=limit, force=force)

        generated = 0
        skipped_existing = 0
        skipped_no_position = 0
        failed = 0
        artifacts: list[StoredReplayArtifact] = []

        for job in jobs:
            match_id = str(job["match_id"])
            account_id = str(job["account_id"])

            if not force and self._artifact_exists(match_id=match_id, account_id=account_id):
                skipped_existing += 1
                continue

            try:
                context = self._load_context(job)
                if not context.positions:
                    skipped_no_position += 1
                    continue
                image_bytes = render_player_map_snapshot(context, self.asset_provider)
                stored = self.replay_store.write_bytes(
                    artifact_type="map_snapshot",
                    shard=context.shard,
                    match_id=context.match_id,
                    data=image_bytes,
                    filename=f"player-{_short_account_id(context.account_id)}-route.jpg",
                    content_type="image/jpeg",
                    match_created_at=context.created_at_kst,
                )
                self._upsert_artifact(context=context, stored=stored)
            except Exception:
                failed += 1
                continue

            generated += 1
            artifacts.append(stored)

        return MapSnapshotResult(
            candidate_snapshots=len(jobs),
            generated_snapshots=generated,
            skipped_existing=skipped_existing,
            skipped_no_position=skipped_no_position,
            failed_snapshots=failed,
            artifacts=artifacts,
        )

    def _list_snapshot_jobs(self, *, limit: int, force: bool) -> list[dict[str, Any]]:
        where = ""
        if not force:
            where = """
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM replay_artifacts artifacts
                    WHERE artifacts.match_id = summaries.match_id
                      AND artifacts.account_id = summaries.account_id
                      AND artifacts.artifact_type = 'map_snapshot'
                      AND artifacts.artifact_name = 'player-route'
                )
            """

        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    summaries.match_id,
                    summaries.account_id,
                    matches.shard,
                    matches.map_name,
                    matches.game_mode,
                    matches.match_type,
                    matches.created_at_kst,
                    registered_players.current_name
                FROM player_movement_summaries summaries
                INNER JOIN matches
                    ON matches.match_id = summaries.match_id
                LEFT JOIN registered_players
                    ON registered_players.account_id = summaries.account_id
                   AND registered_players.shard = matches.shard
                {where}
                ORDER BY matches.created_at_kst DESC, summaries.match_id ASC, summaries.account_id ASC
                LIMIT %s
                """,
                (limit,),
            )
            return list(cursor.fetchall())

    def _artifact_exists(self, *, match_id: str, account_id: str) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM replay_artifacts
                WHERE match_id = %s
                  AND account_id = %s
                  AND artifact_type = 'map_snapshot'
                  AND artifact_name = 'player-route'
                LIMIT 1
                """,
                (match_id, account_id),
            )
            return cursor.fetchone() is not None

    def _load_context(self, job: Mapping[str, Any]) -> MapSnapshotContext:
        match_id = str(job["match_id"])
        account_id = str(job["account_id"])

        positions = self._load_positions(match_id=match_id, account_id=account_id)
        landings = self._load_landings(match_id=match_id, account_id=account_id)
        combat_locations = self._load_combat_locations(match_id=match_id, account_id=account_id)
        care_packages = self._load_care_packages(match_id=match_id)
        plane_route = self._load_plane_route(match_id=match_id)

        return MapSnapshotContext(
            match_id=match_id,
            shard=str(job["shard"]),
            map_name=_optional_text(job.get("map_name")),
            game_mode=_optional_text(job.get("game_mode")),
            match_type=_optional_text(job.get("match_type")),
            created_at_kst=_optional_datetime(job.get("created_at_kst")),
            account_id=account_id,
            player_name=_optional_text(job.get("current_name")),
            positions=positions,
            landing_points=landings,
            combat_locations=combat_locations,
            care_packages=care_packages,
            plane_route=plane_route,
        )

    def _load_positions(self, *, match_id: str, account_id: str) -> list[PositionSample]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT event_index, x, y, common_is_game
                FROM player_position_samples
                WHERE match_id = %s AND account_id = %s
                ORDER BY event_index ASC
                """,
                (match_id, account_id),
            )
            return [
                PositionSample(
                    event_index=int(row["event_index"]),
                    x=_optional_float(row.get("x")),
                    y=_optional_float(row.get("y")),
                    common_is_game=_optional_float(row.get("common_is_game")),
                )
                for row in cursor.fetchall()
            ]

    def _load_landings(self, *, match_id: str, account_id: str) -> list[PositionSample]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT event_index, x, y, common_is_game
                FROM player_landing_events
                WHERE match_id = %s AND account_id = %s
                ORDER BY event_index ASC
                """,
                (match_id, account_id),
            )
            return [
                PositionSample(
                    event_index=int(row["event_index"]),
                    x=_optional_float(row.get("x")),
                    y=_optional_float(row.get("y")),
                    common_is_game=_optional_float(row.get("common_is_game")),
                )
                for row in cursor.fetchall()
            ]

    def _load_combat_locations(self, *, match_id: str, account_id: str) -> list[CombatLocation]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT action, x, y, related_x, related_y, damage_causer_name, damage_reason, distance_m, is_headshot
                FROM player_combat_location_events
                WHERE match_id = %s AND account_id = %s
                ORDER BY event_index ASC, action ASC
                """,
                (match_id, account_id),
            )
            return [
                CombatLocation(
                    action=str(row["action"]),
                    x=_optional_float(row.get("x")),
                    y=_optional_float(row.get("y")),
                    related_x=_optional_float(row.get("related_x")),
                    related_y=_optional_float(row.get("related_y")),
                    damage_causer_name=_optional_text(row.get("damage_causer_name")),
                    damage_reason=_optional_text(row.get("damage_reason")),
                    distance_m=_optional_float(row.get("distance_m")),
                    is_headshot=bool(row.get("is_headshot")),
                )
                for row in cursor.fetchall()
            ]

    def _load_care_packages(self, *, match_id: str) -> list[CarePackageLocation]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT event_type, x, y
                FROM match_care_package_events
                WHERE match_id = %s
                ORDER BY event_index ASC
                """,
                (match_id,),
            )
            return [
                CarePackageLocation(
                    event_type=str(row["event_type"]),
                    x=_optional_float(row.get("x")),
                    y=_optional_float(row.get("y")),
                )
                for row in cursor.fetchall()
            ]

    def _load_plane_route(self, *, match_id: str) -> PlaneRoute | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT start_x, start_y, end_x, end_y
                FROM match_plane_routes
                WHERE match_id = %s
                LIMIT 1
                """,
                (match_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return PlaneRoute(
                start_x=_optional_float(row.get("start_x")),
                start_y=_optional_float(row.get("start_y")),
                end_x=_optional_float(row.get("end_x")),
                end_y=_optional_float(row.get("end_y")),
            )

    def _upsert_artifact(self, *, context: MapSnapshotContext, stored: StoredReplayArtifact) -> None:
        source_tables = {
            "renderer": RENDERER_VERSION,
            "tables": [
                "player_position_samples",
                "player_landing_events",
                "player_combat_location_events",
                "match_care_package_events",
                "match_plane_routes",
            ],
        }
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO replay_artifacts (
                    match_id,
                    shard,
                    artifact_type,
                    artifact_name,
                    account_id,
                    storage_backend,
                    storage_root,
                    relative_path,
                    content_type,
                    size_bytes,
                    sha256,
                    renderer_version,
                    source_tables,
                    generated_at_kst
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    shard = VALUES(shard),
                    storage_backend = VALUES(storage_backend),
                    storage_root = VALUES(storage_root),
                    relative_path = VALUES(relative_path),
                    content_type = VALUES(content_type),
                    size_bytes = VALUES(size_bytes),
                    sha256 = VALUES(sha256),
                    renderer_version = VALUES(renderer_version),
                    source_tables = VALUES(source_tables),
                    generated_at_kst = VALUES(generated_at_kst)
                """,
                (
                    context.match_id,
                    context.shard,
                    stored.artifact_type,
                    "player-route",
                    context.account_id,
                    stored.storage_backend,
                    stored.storage_root,
                    stored.relative_path,
                    stored.content_type,
                    stored.size_bytes,
                    stored.sha256,
                    RENDERER_VERSION,
                    json.dumps(source_tables, ensure_ascii=False, separators=(",", ":")),
                    _mysql_kst_now(),
                ),
            )


def render_player_map_snapshot(context: MapSnapshotContext, asset_provider: MapAssetProvider | None = None) -> bytes:
    provider = asset_provider or MapAssetProvider(Path("data/replays/cache"))
    map_size_px = 1200
    padding = 40
    header_height = 86
    footer_height = 132
    canvas_width = map_size_px + padding * 2
    canvas_height = header_height + map_size_px + footer_height
    map_origin = (padding, header_height)

    base_map = provider.load_map(context.map_name)
    if base_map is None:
        base_map = _grid_map(map_size_px)
    else:
        base_map = base_map.resize((map_size_px, map_size_px), Image.Resampling.LANCZOS)

    canvas = Image.new("RGB", (canvas_width, canvas_height), (19, 24, 31))
    canvas.paste(base_map, map_origin)
    draw = ImageDraw.Draw(canvas, "RGBA")
    font = ImageFont.load_default()
    title_font = ImageFont.load_default()
    world_size_cm = MAP_WORLD_SIZE_CM.get(context.map_name or "", DEFAULT_WORLD_SIZE_CM)

    _draw_header(draw, context, title_font, padding, canvas_width)
    _draw_grid_overlay(draw, map_origin, map_size_px)
    _draw_plane_route(draw, context.plane_route, world_size_cm, map_origin, map_size_px)
    _draw_care_packages(draw, context.care_packages, world_size_cm, map_origin, map_size_px)
    _draw_player_route(draw, context, world_size_cm, map_origin, map_size_px)
    _draw_combat_markers(draw, context.combat_locations, world_size_cm, map_origin, map_size_px, font)
    _draw_footer(draw, font, padding, header_height + map_size_px + 16)

    output = BytesIO()
    canvas.save(output, format="JPEG", quality=88, optimize=True)
    return output.getvalue()


def _draw_header(
    draw: ImageDraw.ImageDraw,
    context: MapSnapshotContext,
    font: ImageFont.ImageFont,
    padding: int,
    canvas_width: int,
) -> None:
    match_time = context.created_at_kst.isoformat(sep=" ", timespec="minutes") if context.created_at_kst else "-"
    player = context.player_name or _short_account_id(context.account_id)
    title = f"{player} route snapshot"
    subtitle = (
        f"{context.map_name or 'unknown'} | {context.game_mode or '-'} | "
        f"{context.match_type or '-'} | {match_time} KST"
    )
    draw.rectangle((0, 0, canvas_width, 86), fill=(19, 24, 31, 255))
    draw.text((padding, 20), title, fill=(245, 247, 250, 255), font=font)
    draw.text((padding, 50), subtitle, fill=(185, 194, 204, 255), font=font)


def _draw_grid_overlay(draw: ImageDraw.ImageDraw, origin: tuple[int, int], size: int) -> None:
    x0, y0 = origin
    for index in range(0, 9):
        pos = x0 + int(size * index / 8)
        draw.line((pos, y0, pos, y0 + size), fill=(255, 255, 255, 42), width=1)
        draw.line((x0, y0 + int(size * index / 8), x0 + size, y0 + int(size * index / 8)), fill=(255, 255, 255, 42), width=1)


def _draw_plane_route(
    draw: ImageDraw.ImageDraw,
    route: PlaneRoute | None,
    world_size_cm: float,
    origin: tuple[int, int],
    size: int,
) -> None:
    if route is None:
        return
    start = _point_to_pixel(route.start_x, route.start_y, world_size_cm, origin, size)
    end = _point_to_pixel(route.end_x, route.end_y, world_size_cm, origin, size)
    if start is None or end is None:
        return
    draw.line((*start, *end), fill=(255, 255, 255, 230), width=5)
    draw.line((*start, *end), fill=(53, 162, 235, 220), width=2)
    _draw_circle(draw, start, 8, (255, 255, 255, 255), (25, 115, 206, 255))
    _draw_circle(draw, end, 8, (255, 255, 255, 255), (25, 115, 206, 255))


def _draw_care_packages(
    draw: ImageDraw.ImageDraw,
    care_packages: Iterable[CarePackageLocation],
    world_size_cm: float,
    origin: tuple[int, int],
    size: int,
) -> None:
    for package in care_packages:
        point = _point_to_pixel(package.x, package.y, world_size_cm, origin, size)
        if point is None:
            continue
        x, y = point
        color = (211, 47, 47, 155) if package.event_type == "LogCarePackageLand" else (255, 193, 7, 115)
        draw.rectangle((x - 4, y - 4, x + 4, y + 4), fill=color, outline=(255, 255, 255, 165))


def _draw_player_route(
    draw: ImageDraw.ImageDraw,
    context: MapSnapshotContext,
    world_size_cm: float,
    origin: tuple[int, int],
    size: int,
) -> None:
    landing_index = context.landing_points[0].event_index if context.landing_points else None
    drop_points = [
        sample
        for sample in context.positions
        if landing_index is not None and sample.event_index <= landing_index
    ]
    movement_points = [
        sample
        for sample in context.positions
        if landing_index is None or sample.event_index >= landing_index
    ]
    _draw_polyline(draw, drop_points, world_size_cm, origin, size, fill=(0, 188, 212, 230), width=3)
    _draw_polyline(draw, movement_points, world_size_cm, origin, size, fill=(57, 255, 20, 235), width=4)

    if context.positions:
        start = _point_to_pixel(context.positions[0].x, context.positions[0].y, world_size_cm, origin, size)
        end = _point_to_pixel(context.positions[-1].x, context.positions[-1].y, world_size_cm, origin, size)
        if start:
            _draw_circle(draw, start, 8, (0, 0, 0, 190), (255, 255, 255, 255))
        if end:
            _draw_circle(draw, end, 8, (0, 0, 0, 190), (57, 255, 20, 255))

    for landing in context.landing_points:
        point = _point_to_pixel(landing.x, landing.y, world_size_cm, origin, size)
        if point is None:
            continue
        x, y = point
        draw.polygon((x, y - 12, x - 11, y + 8, x + 11, y + 8), fill=(255, 235, 59, 245), outline=(20, 20, 20, 220))


def _draw_combat_markers(
    draw: ImageDraw.ImageDraw,
    combat_locations: Iterable[CombatLocation],
    world_size_cm: float,
    origin: tuple[int, int],
    size: int,
    font: ImageFont.ImageFont,
) -> None:
    for event in combat_locations:
        point = _point_to_pixel(event.x, event.y, world_size_cm, origin, size)
        related = _point_to_pixel(event.related_x, event.related_y, world_size_cm, origin, size)
        if point is None:
            continue
        if related is not None and event.action in {"dbno_caused", "kill", "finish"}:
            draw.line((*point, *related), fill=(255, 255, 255, 90), width=1)

        if event.action in {"kill", "finish"}:
            _draw_x(draw, point, 10, (244, 67, 54, 255))
            if event.is_headshot:
                _draw_circle(draw, (point[0] + 12, point[1] - 12), 5, (255, 255, 255, 230), (244, 67, 54, 255))
        elif event.action == "dbno_caused":
            _draw_circle(draw, point, 9, (255, 152, 0, 245), (20, 20, 20, 240))
        elif event.action in {"death", "finished_taken", "dbno_taken"}:
            _draw_circle(draw, point, 10, (20, 20, 20, 240), (244, 67, 54, 255))
            draw.text((point[0] - 3, point[1] - 5), "D", fill=(255, 255, 255, 255), font=font)


def _draw_footer(draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont, padding: int, y: int) -> None:
    legend = [
        ("plane", (53, 162, 235, 255)),
        ("drop", (0, 188, 212, 255)),
        ("move", (57, 255, 20, 255)),
        ("landing", (255, 235, 59, 255)),
        ("dbno", (255, 152, 0, 255)),
        ("kill/finish", (244, 67, 54, 255)),
        ("care package", (255, 193, 7, 255)),
    ]
    x = padding
    draw.text((x, y), "Legend", fill=(245, 247, 250, 255), font=font)
    x += 70
    for label, color in legend:
        draw.rectangle((x, y + 2, x + 14, y + 16), fill=color)
        draw.text((x + 20, y), label, fill=(215, 222, 231, 255), font=font)
        x += max(92, len(label) * 7 + 34)
    note = "Generated from completed-match telemetry. Coordinates use PUBG map origin at top-left."
    draw.text((padding, y + 36), note, fill=(155, 166, 178, 255), font=font)
    draw.text((padding, y + 62), RENDERER_VERSION, fill=(115, 126, 138, 255), font=font)


def _draw_polyline(
    draw: ImageDraw.ImageDraw,
    samples: Iterable[PositionSample],
    world_size_cm: float,
    origin: tuple[int, int],
    size: int,
    *,
    fill: tuple[int, int, int, int],
    width: int,
) -> None:
    points = [
        point
        for sample in samples
        for point in [_point_to_pixel(sample.x, sample.y, world_size_cm, origin, size)]
        if point is not None
    ]
    if len(points) >= 2:
        draw.line(points, fill=fill, width=width, joint="curve")


def _grid_map(size: int) -> Image.Image:
    image = Image.new("RGB", (size, size), (46, 61, 69))
    draw = ImageDraw.Draw(image, "RGBA")
    for index in range(0, 17):
        pos = int(size * index / 16)
        alpha = 90 if index % 2 == 0 else 45
        draw.line((pos, 0, pos, size), fill=(255, 255, 255, alpha), width=1)
        draw.line((0, pos, size, pos), fill=(255, 255, 255, alpha), width=1)
    draw.rectangle((0, 0, size - 1, size - 1), outline=(255, 255, 255, 120), width=2)
    return image


def _point_to_pixel(
    x: float | None,
    y: float | None,
    world_size_cm: float,
    origin: tuple[int, int],
    size: int,
) -> tuple[int, int] | None:
    if x is None or y is None or world_size_cm <= 0:
        return None
    px = origin[0] + int(max(0.0, min(world_size_cm, x)) / world_size_cm * size)
    py = origin[1] + int(max(0.0, min(world_size_cm, y)) / world_size_cm * size)
    return px, py


def _draw_circle(
    draw: ImageDraw.ImageDraw,
    point: tuple[int, int],
    radius: int,
    fill: tuple[int, int, int, int],
    outline: tuple[int, int, int, int],
) -> None:
    x, y = point
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill, outline=outline, width=2)


def _draw_x(
    draw: ImageDraw.ImageDraw,
    point: tuple[int, int],
    radius: int,
    fill: tuple[int, int, int, int],
) -> None:
    x, y = point
    draw.line((x - radius, y - radius, x + radius, y + radius), fill=fill, width=5)
    draw.line((x - radius, y + radius, x + radius, y - radius), fill=fill, width=5)
    draw.line((x - radius, y - radius, x + radius, y + radius), fill=(255, 255, 255, 190), width=2)
    draw.line((x - radius, y + radius, x + radius, y - radius), fill=(255, 255, 255, 190), width=2)


def _short_account_id(account_id: str) -> str:
    return account_id.replace("account.", "")[:12] if account_id else "unknown"


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return to_kst(value)
    return None


def _mysql_kst_now() -> datetime:
    return now_kst().replace(tzinfo=None)

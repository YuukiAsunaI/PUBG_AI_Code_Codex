from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ReplayArtifactRecord:
    id: int
    match_id: str
    shard: str
    artifact_type: str
    artifact_name: str
    account_id: str | None
    player_name: str | None
    map_name: str | None
    game_mode: str | None
    match_type: str | None
    match_created_at_kst: datetime | None
    storage_backend: str
    storage_root: str
    relative_path: str
    content_type: str
    size_bytes: int
    sha256: str
    renderer_version: str
    generated_at_kst: datetime

    @property
    def view_url(self) -> str:
        return f"/replay/artifacts/{self.id}/file"

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["match_created_at_kst"] = (
            self.match_created_at_kst.isoformat() if self.match_created_at_kst else None
        )
        record["generated_at_kst"] = self.generated_at_kst.isoformat()
        record["view_url"] = self.view_url
        return record


def list_replay_artifacts(
    connection: Any,
    *,
    limit: int = 50,
    artifact_type: str | None = "map_snapshot",
    match_id: str | None = None,
    account_id: str | None = None,
    registered_guild_id: str | None = None,
) -> list[ReplayArtifactRecord]:
    limit = normalize_artifact_limit(limit)
    where_sql = []
    params: list[Any] = []

    if artifact_type:
        where_sql.append("artifacts.artifact_type = %s")
        params.append(artifact_type)
    if match_id:
        where_sql.append("artifacts.match_id = %s")
        params.append(match_id)
    if account_id:
        where_sql.append("artifacts.account_id = %s")
        params.append(account_id)
    if registered_guild_id:
        where_sql.append("registered_players.registered_guild_id = %s")
        params.append(registered_guild_id)

    where_clause = f"WHERE {' AND '.join(where_sql)}" if where_sql else ""
    params.append(limit)

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                artifacts.id,
                artifacts.match_id,
                artifacts.shard,
                artifacts.artifact_type,
                artifacts.artifact_name,
                artifacts.account_id,
                registered_players.current_name AS player_name,
                matches.map_name,
                matches.game_mode,
                matches.match_type,
                matches.created_at_kst AS match_created_at_kst,
                artifacts.storage_backend,
                artifacts.storage_root,
                artifacts.relative_path,
                artifacts.content_type,
                artifacts.size_bytes,
                artifacts.sha256,
                artifacts.renderer_version,
                artifacts.generated_at_kst
            FROM replay_artifacts artifacts
            INNER JOIN matches
                ON matches.match_id = artifacts.match_id
            LEFT JOIN registered_players
                ON registered_players.account_id = artifacts.account_id
               AND registered_players.shard = artifacts.shard
            {where_clause}
            ORDER BY artifacts.generated_at_kst DESC, artifacts.id DESC
            LIMIT %s
            """,
            params,
        )
        return [_record_from_row(row) for row in cursor.fetchall()]


def get_replay_artifact(connection: Any, artifact_id: int) -> ReplayArtifactRecord | None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                artifacts.id,
                artifacts.match_id,
                artifacts.shard,
                artifacts.artifact_type,
                artifacts.artifact_name,
                artifacts.account_id,
                registered_players.current_name AS player_name,
                matches.map_name,
                matches.game_mode,
                matches.match_type,
                matches.created_at_kst AS match_created_at_kst,
                artifacts.storage_backend,
                artifacts.storage_root,
                artifacts.relative_path,
                artifacts.content_type,
                artifacts.size_bytes,
                artifacts.sha256,
                artifacts.renderer_version,
                artifacts.generated_at_kst
            FROM replay_artifacts artifacts
            INNER JOIN matches
                ON matches.match_id = artifacts.match_id
            LEFT JOIN registered_players
                ON registered_players.account_id = artifacts.account_id
               AND registered_players.shard = artifacts.shard
            WHERE artifacts.id = %s
            LIMIT 1
            """,
            (artifact_id,),
        )
        row = cursor.fetchone()
        return _record_from_row(row) if row else None


def normalize_artifact_limit(value: int) -> int:
    return max(1, min(int(value), 200))


def _record_from_row(row: dict[str, Any]) -> ReplayArtifactRecord:
    account_id = row.get("account_id")
    if account_id == "":
        account_id = None

    return ReplayArtifactRecord(
        id=int(row["id"]),
        match_id=str(row["match_id"]),
        shard=str(row["shard"]),
        artifact_type=str(row["artifact_type"]),
        artifact_name=str(row["artifact_name"]),
        account_id=account_id,
        player_name=row.get("player_name"),
        map_name=row.get("map_name"),
        game_mode=row.get("game_mode"),
        match_type=row.get("match_type"),
        match_created_at_kst=row.get("match_created_at_kst"),
        storage_backend=str(row["storage_backend"]),
        storage_root=str(row["storage_root"]),
        relative_path=str(row["relative_path"]),
        content_type=str(row["content_type"]),
        size_bytes=int(row["size_bytes"]),
        sha256=str(row["sha256"]),
        renderer_version=str(row["renderer_version"]),
        generated_at_kst=row["generated_at_kst"],
    )

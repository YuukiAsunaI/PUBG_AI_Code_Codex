from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Mapping
import gzip

from pubg_ai.parser_policy import CURRENT_TELEMETRY_PARSER_VERSION
from pubg_ai.raw_storage import RawPayloadStore
from pubg_ai.time_utils import now_kst, to_kst


class TelemetryJobProcessingError(RuntimeError):
    """Raised when queued PUBG telemetry jobs cannot be processed."""


@dataclass(frozen=True)
class TelemetryDownload:
    content: bytes
    content_type: str | None
    source_url: str


@dataclass(frozen=True)
class TelemetryJobProcessingResult:
    picked_jobs: int
    downloaded_telemetry: int
    stored_telemetry: int
    skipped_existing: int
    failed_jobs: int
    downloaded_bytes: int
    stored_bytes: int

    def to_record(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class ProcessedTelemetryJob:
    status: str
    downloaded_bytes: int
    stored_bytes: int


class TelemetryJobProcessor:
    def __init__(
        self,
        connection: Any,
        raw_store: RawPayloadStore,
        *,
        timeout_seconds: float = 90.0,
    ) -> None:
        self.connection = connection
        self.raw_store = raw_store
        self.timeout_seconds = timeout_seconds

    def process_queued_telemetry(self, *, limit: int = 5) -> TelemetryJobProcessingResult:
        limit = max(1, min(limit, 200))
        jobs = self._list_queued_telemetry_jobs(limit=limit)

        downloaded_telemetry = 0
        stored_telemetry = 0
        skipped_existing = 0
        failed_jobs = 0
        downloaded_bytes = 0
        stored_bytes = 0

        for job in jobs:
            if not self._mark_job_running(job):
                continue

            try:
                processed = self._process_job(job)
                self._mark_job_succeeded(job["id"])
            except Exception as exc:
                failed_jobs += 1
                self._mark_job_failed(job["id"], exc)
                continue

            if processed.status == "existing":
                skipped_existing += 1
            else:
                downloaded_telemetry += 1
                stored_telemetry += 1
                downloaded_bytes += processed.downloaded_bytes
                stored_bytes += processed.stored_bytes

        return TelemetryJobProcessingResult(
            picked_jobs=len(jobs),
            downloaded_telemetry=downloaded_telemetry,
            stored_telemetry=stored_telemetry,
            skipped_existing=skipped_existing,
            failed_jobs=failed_jobs,
            downloaded_bytes=downloaded_bytes,
            stored_bytes=stored_bytes,
        )

    def list_telemetry_jobs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, job_type, shard, target_id, status, attempts, next_run_at_kst,
                       last_error, created_at_kst, updated_at_kst
                FROM api_fetch_jobs
                WHERE job_type = 'telemetry'
                ORDER BY created_at_kst DESC, id DESC
                LIMIT %s
                """,
                (limit,),
            )
            return list(cursor.fetchall())

    def _process_job(self, job: Mapping[str, Any]) -> ProcessedTelemetryJob:
        shard = _required_job_text(job.get("shard"), "shard")
        match_id = _required_job_text(job.get("target_id"), "target_id")

        match = self._load_match_for_telemetry(match_id=match_id, shard=shard)
        if self._telemetry_payload_exists(match_id):
            return ProcessedTelemetryJob(status="existing", downloaded_bytes=0, stored_bytes=0)

        telemetry_url = _required_job_text(match.get("telemetry_url"), "telemetry_url")
        created_at = _datetime_value(match.get("created_at_kst"))
        download = self._fetch_telemetry(telemetry_url)

        stored = self.raw_store.write_json_bytes(
            "telemetry",
            shard,
            match_id,
            download.content,
            match_created_at=created_at,
        )
        if not self.raw_store.verify(stored):
            raise TelemetryJobProcessingError(f"raw telemetry payload verification failed: {match_id}")

        self._upsert_raw_telemetry_payload(
            match_id=match_id,
            shard=shard,
            asset_url=download.source_url,
            storage_root=stored.storage_root,
            relative_path=stored.relative_path,
            compression=stored.compression,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
        )

        return ProcessedTelemetryJob(
            status="stored",
            downloaded_bytes=len(download.content),
            stored_bytes=stored.size_bytes,
        )

    def _fetch_telemetry(self, telemetry_url: str) -> TelemetryDownload:
        import httpx

        try:
            response = httpx.get(
                telemetry_url,
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "User-Agent": "pubg-ai-local-analytics/0.1",
                },
                timeout=self.timeout_seconds,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            raise TelemetryJobProcessingError(
                f"telemetry download failed: {exc.__class__.__name__}"
            ) from exc

        if response.status_code >= 400:
            raise TelemetryJobProcessingError(
                f"telemetry CDN returned HTTP {response.status_code}"
            )

        content = _maybe_decompress_gzip(response.content)
        if not _looks_like_json_bytes(content):
            content_type = response.headers.get("content-type")
            raise TelemetryJobProcessingError(
                f"telemetry response is not JSON-like; content_type={content_type or 'unknown'}"
            )

        return TelemetryDownload(
            content=content,
            content_type=response.headers.get("content-type"),
            source_url=str(response.url),
        )

    def _list_queued_telemetry_jobs(self, *, limit: int) -> list[dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, job_type, shard, target_id, status, attempts, next_run_at_kst
                FROM api_fetch_jobs
                WHERE job_type = 'telemetry'
                  AND status = 'queued'
                  AND (next_run_at_kst IS NULL OR next_run_at_kst <= %s)
                ORDER BY next_run_at_kst ASC, id ASC
                LIMIT %s
                """,
                (_mysql_kst_now(), limit),
            )
            return list(cursor.fetchall())

    def _mark_job_running(self, job: Mapping[str, Any]) -> bool:
        timestamp = _mysql_kst_now()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE api_fetch_jobs
                SET status = 'running',
                    attempts = attempts + 1,
                    updated_at_kst = %s
                WHERE id = %s AND status = 'queued'
                """,
                (timestamp, job["id"]),
            )
            return cursor.rowcount == 1

    def _mark_job_succeeded(self, job_id: int) -> None:
        timestamp = _mysql_kst_now()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE api_fetch_jobs
                SET status = 'succeeded',
                    next_run_at_kst = NULL,
                    last_error = NULL,
                    rate_limit_limit = NULL,
                    rate_limit_remaining = NULL,
                    rate_limit_reset_epoch = NULL,
                    updated_at_kst = %s
                WHERE id = %s
                """,
                (timestamp, job_id),
            )

    def _mark_job_failed(self, job_id: int, exc: Exception) -> None:
        timestamp = _mysql_kst_now()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE api_fetch_jobs
                SET status = 'failed',
                    last_error = %s,
                    updated_at_kst = %s
                WHERE id = %s
                """,
                (_safe_error(exc), timestamp, job_id),
            )

    def _load_match_for_telemetry(self, *, match_id: str, shard: str) -> dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT match_id, shard, telemetry_url, created_at_kst
                FROM matches
                WHERE match_id = %s AND shard = %s
                LIMIT 1
                """,
                (match_id, shard),
            )
            row = cursor.fetchone()
        if row is None:
            raise TelemetryJobProcessingError(f"match row not found for telemetry job: {match_id}")
        return dict(row)

    def _telemetry_payload_exists(self, match_id: str) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM raw_telemetry_payloads WHERE match_id = %s LIMIT 1",
                (match_id,),
            )
            return cursor.fetchone() is not None

    def _upsert_raw_telemetry_payload(
        self,
        *,
        match_id: str,
        shard: str,
        asset_url: str,
        storage_root: str,
        relative_path: str,
        compression: str,
        size_bytes: int,
        sha256: str,
    ) -> None:
        fetched_at = _mysql_kst_now()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO raw_telemetry_payloads (
                    match_id,
                    shard,
                    asset_url,
                    storage_root,
                    relative_path,
                    compression,
                    size_bytes,
                    sha256,
                    fetched_at_kst,
                    parser_version
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    shard = VALUES(shard),
                    asset_url = VALUES(asset_url),
                    storage_root = VALUES(storage_root),
                    relative_path = VALUES(relative_path),
                    compression = VALUES(compression),
                    size_bytes = VALUES(size_bytes),
                    sha256 = VALUES(sha256),
                    fetched_at_kst = VALUES(fetched_at_kst),
                    parser_version = VALUES(parser_version)
                """,
                (
                    match_id,
                    shard,
                    asset_url,
                    storage_root,
                    relative_path,
                    compression,
                    size_bytes,
                    sha256,
                    fetched_at,
                    CURRENT_TELEMETRY_PARSER_VERSION,
                ),
            )


def _maybe_decompress_gzip(content: bytes) -> bytes:
    if content.startswith(b"\x1f\x8b"):
        try:
            return gzip.decompress(content)
        except OSError:
            return content
    return content


def _looks_like_json_bytes(content: bytes) -> bool:
    if not content:
        return False
    stripped = content.lstrip()
    return stripped.startswith(b"{") or stripped.startswith(b"[")


def _datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return to_kst(value)
    return None


def _mysql_kst_now() -> datetime:
    return now_kst().replace(tzinfo=None)


def _required_job_text(value: Any, label: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise TelemetryJobProcessingError(f"queued telemetry job is missing {label}.")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_error(exc: Exception) -> str:
    message = f"{exc.__class__.__name__}: {exc}"
    return message[:1000]

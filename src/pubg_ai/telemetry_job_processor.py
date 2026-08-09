from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime
from io import BytesIO
from typing import Any, Callable, Mapping
from urllib.parse import urljoin, urlsplit
import gzip

from pubg_ai.api_job_retry import decide_api_job_retry, stale_running_cutoff
from pubg_ai.parser_policy import CURRENT_TELEMETRY_PARSER_VERSION
from pubg_ai.raw_storage import RawPayloadStore
from pubg_ai.time_utils import now_kst, to_kst


ALLOWED_TELEMETRY_HOSTS = {"telemetry-cdn.pubg.com"}
MAX_TELEMETRY_DOWNLOAD_BYTES = 256 * 1024 * 1024
MAX_TELEMETRY_REDIRECTS = 5


class TelemetryJobProcessingError(RuntimeError):
    """Raised when queued PUBG telemetry jobs cannot be processed."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class TelemetryDownload:
    content: bytes
    content_type: str | None
    source_url: str


@dataclass(frozen=True)
class _TelemetryHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    url: str
    content: bytes


@dataclass(frozen=True)
class TelemetryJobProcessingResult:
    picked_jobs: int
    downloaded_telemetry: int
    stored_telemetry: int
    skipped_existing: int
    failed_jobs: int
    downloaded_bytes: int
    stored_bytes: int
    requeued_jobs: int
    terminal_failed_jobs: int
    recovered_stale_jobs: int

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
        request_get: Callable[..., Any] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise TelemetryJobProcessingError("timeout_seconds must be positive.")
        self.connection = connection
        self.raw_store = raw_store
        self.timeout_seconds = timeout_seconds
        self._request_get = request_get

    def process_queued_telemetry(self, *, limit: int = 5) -> TelemetryJobProcessingResult:
        limit = max(1, min(limit, 200))
        recovered_stale_jobs = self._recover_stale_running_jobs()
        jobs = self._list_queued_telemetry_jobs(limit=limit)

        downloaded_telemetry = 0
        stored_telemetry = 0
        skipped_existing = 0
        failed_jobs = 0
        downloaded_bytes = 0
        stored_bytes = 0
        requeued_jobs = 0
        terminal_failed_jobs = 0

        for job in jobs:
            if not self._mark_job_running(job):
                continue

            try:
                processed = self._process_job(job)
                self._mark_job_succeeded(job["id"])
            except Exception as exc:
                failed_jobs += 1
                if self._handle_job_failure(job, exc):
                    requeued_jobs += 1
                else:
                    terminal_failed_jobs += 1
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
            requeued_jobs=requeued_jobs,
            terminal_failed_jobs=terminal_failed_jobs,
            recovered_stale_jobs=recovered_stale_jobs,
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

        request_get = self._request_get
        request_headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": "pubg-ai-local-analytics/0.1",
        }
        current_url = telemetry_url
        response = None
        for redirect_count in range(MAX_TELEMETRY_REDIRECTS + 1):
            _validate_telemetry_url(current_url)
            try:
                if request_get is None:
                    response = _stream_httpx_get(
                        httpx,
                        current_url,
                        headers=request_headers,
                        timeout=self.timeout_seconds,
                        max_bytes=MAX_TELEMETRY_DOWNLOAD_BYTES,
                    )
                else:
                    response = request_get(
                        current_url,
                        headers=request_headers,
                        timeout=self.timeout_seconds,
                        follow_redirects=False,
                    )
            except httpx.HTTPError as exc:
                raise TelemetryJobProcessingError(
                    f"telemetry download failed: {exc.__class__.__name__}",
                    retryable=True,
                ) from exc

            status_code = int(response.status_code)
            if status_code not in {301, 302, 303, 307, 308}:
                break
            if redirect_count >= MAX_TELEMETRY_REDIRECTS:
                raise TelemetryJobProcessingError(
                    "telemetry CDN returned too many redirects",
                    retryable=True,
                )
            location = response.headers.get("location")
            if not location:
                raise TelemetryJobProcessingError(
                    "telemetry CDN redirect is missing a Location header",
                    status_code=status_code,
                    retryable=True,
                )
            next_url = urljoin(current_url, location)
            _validate_telemetry_url(next_url)
            current_url = next_url

        if response is None:
            raise TelemetryJobProcessingError(
                "telemetry download did not return a response",
                retryable=True,
            )
        if response.status_code >= 400:
            status_code = int(response.status_code)
            raise TelemetryJobProcessingError(
                f"telemetry CDN returned HTTP {status_code}",
                status_code=status_code,
                retryable=status_code in {404, 408, 425, 429, 500, 502, 503, 504},
                retry_after_seconds=_retry_after_seconds(response.headers),
            )

        source_url = str(response.url)
        _validate_telemetry_url(source_url)
        if len(response.content) > MAX_TELEMETRY_DOWNLOAD_BYTES:
            raise TelemetryJobProcessingError("telemetry response exceeds the maximum download size")
        try:
            content = _maybe_decompress_gzip(
                response.content,
                max_output_bytes=MAX_TELEMETRY_DOWNLOAD_BYTES,
            )
        except ValueError as exc:
            raise TelemetryJobProcessingError(str(exc)) from exc
        if not _looks_like_json_bytes(content):
            content_type = response.headers.get("content-type")
            raise TelemetryJobProcessingError(
                f"telemetry response is not JSON-like; content_type={content_type or 'unknown'}",
                retryable=True,
            )

        return TelemetryDownload(
            content=content,
            content_type=response.headers.get("content-type"),
            source_url=source_url,
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

    def _handle_job_failure(self, job: Mapping[str, Any], exc: Exception) -> bool:
        timestamp = _mysql_kst_now()
        decision = decide_api_job_retry(job, exc, current_time=timestamp)
        status = "queued" if decision.should_retry else "failed"
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE api_fetch_jobs
                SET status = %s,
                    next_run_at_kst = %s,
                    last_error = %s,
                    updated_at_kst = %s
                WHERE id = %s
                """,
                (
                    status,
                    decision.next_run_at_kst,
                    decision.error,
                    timestamp,
                    job["id"],
                ),
            )
        return decision.should_retry

    def _recover_stale_running_jobs(self) -> int:
        timestamp = _mysql_kst_now()
        cutoff = stale_running_cutoff(current_time=timestamp)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE api_fetch_jobs
                SET status = 'queued',
                    attempts = CASE WHEN attempts > 0 THEN attempts - 1 ELSE 0 END,
                    next_run_at_kst = %s,
                    last_error = 'recovered stale running telemetry job after worker restart',
                    updated_at_kst = %s
                WHERE job_type = 'telemetry'
                  AND status = 'running'
                  AND updated_at_kst < %s
                """,
                (timestamp, timestamp, cutoff),
            )
            return int(cursor.rowcount)

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



def _stream_httpx_get(
    httpx_module: Any,
    url: str,
    *,
    headers: Mapping[str, str],
    timeout: float,
    max_bytes: int,
) -> _TelemetryHttpResponse:
    with httpx_module.stream(
        "GET",
        url,
        headers=headers,
        timeout=timeout,
        follow_redirects=False,
    ) as response:
        status_code = int(response.status_code)
        content = b""
        if 200 <= status_code < 300:
            content = _join_limited_chunks(response.iter_bytes(), max_bytes=max_bytes)
        return _TelemetryHttpResponse(
            status_code=status_code,
            headers=response.headers,
            url=str(response.url),
            content=content,
        )


def _join_limited_chunks(chunks: Iterable[bytes], *, max_bytes: int) -> bytes:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    body = bytearray()
    for chunk in chunks:
        body.extend(chunk)
        if len(body) > max_bytes:
            raise TelemetryJobProcessingError("telemetry response exceeds the maximum download size")
    return bytes(body)


def _maybe_decompress_gzip(
    content: bytes,
    *,
    max_output_bytes: int = MAX_TELEMETRY_DOWNLOAD_BYTES,
) -> bytes:
    if max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be positive")
    if content.startswith(b"\x1f\x8b"):
        try:
            with gzip.GzipFile(fileobj=BytesIO(content), mode="rb") as file:
                decompressed = file.read(max_output_bytes + 1)
        except OSError:
            return content
        if len(decompressed) > max_output_bytes:
            raise ValueError("telemetry decompressed payload exceeds the maximum download size")
        return decompressed
    if len(content) > max_output_bytes:
        raise ValueError("telemetry payload exceeds the maximum download size")
    return content


def _validate_telemetry_url(value: str) -> None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise TelemetryJobProcessingError("telemetry URL is invalid") from exc
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() != "https"
        or hostname not in ALLOWED_TELEMETRY_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise TelemetryJobProcessingError("telemetry URL must use the official PUBG HTTPS CDN")


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


def _retry_after_seconds(headers: Mapping[str, Any]) -> float | None:
    value = None
    for key, candidate in headers.items():
        if str(key).lower() == "retry-after":
            value = candidate
            break
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _safe_error(exc: Exception) -> str:
    message = f"{exc.__class__.__name__}: {exc}"
    return message[:1000]

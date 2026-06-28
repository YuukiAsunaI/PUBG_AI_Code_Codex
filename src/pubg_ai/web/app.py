from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from pubg_ai.config import RuntimeConfig
from pubg_ai.database import connect_mysql, count_tables
from pubg_ai.match_collection import RegisteredPlayerMatchCollector
from pubg_ai.match_job_processor import MatchJobProcessor
from pubg_ai.player_registry import DiscordCommandContext, PlayerRegistry
from pubg_ai.pubg_client import PubgApiClient, PubgApiError
from pubg_ai.raw_storage import RawPayloadStore
from pubg_ai.telemetry_job_processor import TelemetryJobProcessor


class RegisterPlayerRequest(BaseModel):
    account_id: str | None = None
    shard: str = Field(default="steam", min_length=1)
    current_name: str = Field(min_length=1)
    public_profile: bool = True
    discord_user_id: str | None = None
    guild_id: str | None = None
    channel_id: str | None = None


class UnregisterPlayerRequest(BaseModel):
    shard: str = Field(default="steam", min_length=1)
    account_id: str | None = None
    name: str | None = None


class CollectMatchesRequest(BaseModel):
    shard: str | None = None
    limit: int | None = None


class ProcessMatchJobsRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=500)


class ProcessTelemetryJobsRequest(BaseModel):
    limit: int = Field(default=5, ge=1, le=200)


def create_app() -> Any:
    config = RuntimeConfig.from_sources(base_dir=Path.cwd())
    app = FastAPI(
        title="PUBG AI Local Manager",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
    )

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _INDEX_HTML

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "local_only": True,
            "bind_host": "127.0.0.1",
        }

    @app.get("/settings/status")
    def settings_status() -> dict[str, Any]:
        return {
            "raw_data_dir": str(config.app.raw_data_dir),
            "replay_data_dir": str(config.app.replay_data_dir),
            "raw_compression": config.app.raw_compression,
            "collector": {
                "poll_interval_seconds": config.app.collector_poll_interval_seconds,
                "cycle_player_limit": config.app.collector_cycle_player_limit,
                "player_lookup_chunk_size": config.app.player_lookup_chunk_size,
            },
            "database": config.database.safe_record(),
            "secrets": {
                key: status.to_record()
                for key, status in config.secrets.status().items()
            },
        }

    @app.get("/database/status")
    def database_status() -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT DATABASE() AS database_name, VERSION() AS version")
                row = cursor.fetchone()
            return {
                "mysql_connection": "ok",
                "database": row["database_name"],
                "version": row["version"],
                "table_count": count_tables(connection),
            }
        finally:
            connection.close()

    @app.get("/players")
    def list_players(shard: str | None = None, active_only: bool = True, limit: int = 100) -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            players = PlayerRegistry(connection).list_players(
                shard=shard,
                active_only=active_only,
                limit=limit,
            )
            return {"players": [player.to_record() for player in players]}
        finally:
            connection.close()

    @app.post("/players/register")
    def register_player(request: RegisterPlayerRequest) -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            context = DiscordCommandContext(
                user_id=request.discord_user_id,
                guild_id=request.guild_id,
                channel_id=request.channel_id,
            )
            registry = PlayerRegistry(connection)
            if request.account_id:
                player = registry.register_player(
                    account_id=request.account_id,
                    shard=request.shard,
                    current_name=request.current_name,
                    public_profile=request.public_profile,
                    context=context,
                )
            else:
                if not config.secrets.pubg_api_key:
                    raise HTTPException(status_code=500, detail="PUBG_API_KEY is not configured.")
                try:
                    player = registry.register_player_by_name(
                        pubg_client=PubgApiClient(config.secrets.pubg_api_key),
                        shard=request.shard,
                        player_name=request.current_name,
                        public_profile=request.public_profile,
                        context=context,
                    )
                except PubgApiError as exc:
                    raise HTTPException(status_code=502, detail=str(exc)) from exc
            return {"player": player.to_record()}
        finally:
            connection.close()

    @app.post("/players/unregister")
    def unregister_player(request: UnregisterPlayerRequest) -> dict[str, Any]:
        if not request.account_id and not request.name:
            raise HTTPException(status_code=400, detail="account_id or name is required.")

        connection = connect_mysql(config.database)
        try:
            player = PlayerRegistry(connection).unregister_player(
                shard=request.shard,
                account_id=request.account_id,
                name=request.name,
            )
            if player is None:
                raise HTTPException(status_code=404, detail="player not found.")
            return {"player": player.to_record()}
        finally:
            connection.close()

    @app.post("/collection/refresh")
    def refresh_collection(request: CollectMatchesRequest) -> dict[str, Any]:
        if not config.secrets.pubg_api_key:
            raise HTTPException(status_code=500, detail="PUBG_API_KEY is not configured.")

        connection = connect_mysql(config.database)
        try:
            try:
                result = RegisteredPlayerMatchCollector(
                    connection,
                    PubgApiClient(config.secrets.pubg_api_key),
                    lookup_chunk_size=config.app.player_lookup_chunk_size,
                ).collect_active_players(
                    shard=request.shard,
                    limit=request.limit or config.app.collector_cycle_player_limit,
                )
            except PubgApiError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            return {"result": result.to_record()}
        finally:
            connection.close()

    @app.get("/jobs/matches")
    def match_jobs(limit: int = 100) -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            jobs = RegisteredPlayerMatchCollector(connection).list_match_jobs(limit=limit)
            return {"jobs": [_json_ready(job) for job in jobs]}
        finally:
            connection.close()

    @app.post("/jobs/matches/process")
    def process_match_jobs(request: ProcessMatchJobsRequest) -> dict[str, Any]:
        if not config.secrets.pubg_api_key:
            raise HTTPException(status_code=500, detail="PUBG_API_KEY is not configured.")

        connection = connect_mysql(config.database)
        try:
            result = MatchJobProcessor(
                connection,
                PubgApiClient(config.secrets.pubg_api_key),
                RawPayloadStore(
                    config.app.raw_data_dir,
                    compression=config.app.raw_compression,  # type: ignore[arg-type]
                ),
            ).process_queued_matches(limit=request.limit)
            return {"result": result.to_record()}
        finally:
            connection.close()

    @app.get("/jobs/telemetry")
    def telemetry_jobs(limit: int = 100) -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            jobs = TelemetryJobProcessor(
                connection,
                RawPayloadStore(
                    config.app.raw_data_dir,
                    compression=config.app.raw_compression,  # type: ignore[arg-type]
                ),
            ).list_telemetry_jobs(limit=limit)
            return {"jobs": [_json_ready(job) for job in jobs]}
        finally:
            connection.close()

    @app.post("/jobs/telemetry/process")
    def process_telemetry_jobs(request: ProcessTelemetryJobsRequest) -> dict[str, Any]:
        connection = connect_mysql(config.database)
        try:
            result = TelemetryJobProcessor(
                connection,
                RawPayloadStore(
                    config.app.raw_data_dir,
                    compression=config.app.raw_compression,  # type: ignore[arg-type]
                ),
            ).process_queued_telemetry(limit=request.limit)
            return {"result": result.to_record()}
        finally:
            connection.close()

    return app


def _json_ready(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


_INDEX_HTML = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PUBG AI Local Manager</title>
  <style>
    :root {
      color-scheme: light;
      font-family: Arial, "Malgun Gothic", sans-serif;
      --bg: #f4f6f8;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #65727f;
      --line: #d8dee6;
      --accent: #1677c7;
      --danger: #b42318;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--text); }
    header {
      padding: 18px 24px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }
    h1 { margin: 0; font-size: 20px; letter-spacing: 0; }
    main { max-width: 1180px; margin: 0 auto; padding: 24px; display: grid; gap: 18px; }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
    }
    h2 { margin: 0 0 14px; font-size: 16px; letter-spacing: 0; }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
    .kv { border-left: 3px solid var(--accent); padding: 4px 10px; min-width: 0; }
    .kv span { display: block; color: var(--muted); font-size: 12px; }
    .kv strong { display: block; margin-top: 4px; font-size: 14px; overflow-wrap: anywhere; }
    form { display: grid; grid-template-columns: 120px 1fr 1fr 150px auto; gap: 10px; align-items: end; }
    label { display: grid; gap: 6px; color: var(--muted); font-size: 12px; }
    input, select {
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 14px;
      background: #fff;
    }
    button {
      min-height: 38px;
      border: 0;
      border-radius: 6px;
      padding: 8px 12px;
      font-size: 14px;
      background: var(--accent);
      color: #fff;
      cursor: pointer;
    }
    button.secondary { background: #46515c; }
    button.danger { background: var(--danger); }
    table { width: 100%; border-collapse: collapse; table-layout: fixed; }
    th, td { border-bottom: 1px solid var(--line); padding: 10px; text-align: left; font-size: 14px; }
    th { color: var(--muted); font-weight: 600; }
    td { overflow-wrap: anywhere; }
    .actions { display: flex; gap: 8px; justify-content: flex-end; }
    .status { color: var(--muted); font-size: 13px; }
    @media (max-width: 900px) {
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      form { grid-template-columns: 1fr; }
      header { align-items: flex-start; flex-direction: column; }
    }
  </style>
</head>
<body>
  <header>
    <h1>PUBG AI Local Manager</h1>
    <div class="status" id="banner">localhost 전용 관리 화면</div>
  </header>
  <main>
    <section>
      <h2>상태</h2>
      <div class="grid" id="statusGrid"></div>
    </section>
    <section>
      <h2>유저 등록</h2>
    <form id="registerForm">
        <label>플랫폼
          <select name="shard">
            <option value="steam">steam</option>
            <option value="kakao">kakao</option>
          </select>
        </label>
        <label>닉네임
          <input name="current_name" autocomplete="off" required>
        </label>
        <label>Account ID
          <input name="account_id" autocomplete="off" placeholder="자동 조회">
        </label>
        <label>공개 프로필
          <select name="public_profile">
            <option value="true">공개</option>
            <option value="false">비공개</option>
          </select>
        </label>
        <button type="submit">등록</button>
      </form>
    </section>
    <section>
      <h2>등록 유저</h2>
      <div class="actions" style="margin-bottom: 10px;">
        <button type="button" onclick="refreshCollection()">최근 매치 수집</button>
        <button class="secondary" type="button" onclick="loadPlayers()">새로고침</button>
      </div>
      <table>
        <thead>
          <tr>
            <th>플랫폼</th>
            <th>닉네임</th>
            <th>Account ID</th>
            <th>상태</th>
            <th></th>
          </tr>
        </thead>
        <tbody id="playersBody"></tbody>
      </table>
    </section>
    <section>
      <h2>Match 수집 큐</h2>
      <div class="actions" style="margin-bottom: 10px;">
        <button type="button" onclick="processMatchJobs()">상세 저장</button>
        <button class="secondary" type="button" onclick="loadJobs()">새로고침</button>
      </div>
      <table>
        <thead>
          <tr>
            <th>플랫폼</th>
            <th>Match ID</th>
            <th>상태</th>
            <th>시도</th>
            <th>생성</th>
          </tr>
        </thead>
        <tbody id="jobsBody"></tbody>
      </table>
    </section>
    <section>
      <h2>Telemetry 수집 큐</h2>
      <div class="actions" style="margin-bottom: 10px;">
        <button type="button" onclick="processTelemetryJobs()">Telemetry 저장</button>
        <button class="secondary" type="button" onclick="loadTelemetryJobs()">새로고침</button>
      </div>
      <table>
        <thead>
          <tr>
            <th>플랫폼</th>
            <th>Match ID</th>
            <th>상태</th>
            <th>시도</th>
            <th>생성</th>
          </tr>
        </thead>
        <tbody id="telemetryJobsBody"></tbody>
      </table>
    </section>
  </main>
  <script>
    const statusGrid = document.querySelector("#statusGrid");
    const playersBody = document.querySelector("#playersBody");
    const jobsBody = document.querySelector("#jobsBody");
    const telemetryJobsBody = document.querySelector("#telemetryJobsBody");
    const banner = document.querySelector("#banner");

    function cell(label, value) {
      return `<div class="kv"><span>${label}</span><strong>${value}</strong></div>`;
    }

    async function loadStatus() {
      const [settings, database] = await Promise.all([
        fetch("/settings/status").then((r) => r.json()),
        fetch("/database/status").then((r) => r.json()).catch(() => ({ mysql_connection: "error" })),
      ]);
      statusGrid.innerHTML = [
        cell("MySQL", `${database.mysql_connection || "unknown"} / ${database.database || "-"}`),
        cell("PUBG API Key", settings.secrets.PUBG_API_KEY.configured ? "설정됨" : "없음"),
        cell("Discord Token", settings.secrets.DISCORD_BOT_TOKEN.configured ? "설정됨" : "없음"),
        cell("Raw 저장소", settings.raw_data_dir),
        cell("Replay 저장소", settings.replay_data_dir),
        cell("수집 주기", `${settings.collector.poll_interval_seconds}초`),
        cell("주기당 대상", `${settings.collector.cycle_player_limit}명`),
        cell("조회 chunk", `${settings.collector.player_lookup_chunk_size}명`),
      ].join("");
    }

    async function loadPlayers() {
      const payload = await fetch("/players?active_only=false").then((r) => r.json());
      playersBody.innerHTML = payload.players.map((player) => `
        <tr>
          <td>${player.shard}</td>
          <td>${player.current_name}</td>
          <td>${player.account_id}</td>
          <td>${player.active ? "수집중" : "중지"}</td>
          <td>
            <div class="actions">
              <button class="danger" type="button" onclick="unregisterPlayer('${player.shard}', '${player.account_id}')">
                삭제
              </button>
            </div>
          </td>
        </tr>
      `).join("");
    }

    async function loadJobs() {
      const payload = await fetch("/jobs/matches?limit=50").then((r) => r.json());
      jobsBody.innerHTML = payload.jobs.map((job) => `
        <tr>
          <td>${job.shard || ""}</td>
          <td>${job.target_id || ""}</td>
          <td>${job.status || ""}</td>
          <td>${job.attempts || 0}</td>
          <td>${job.created_at_kst || ""}</td>
        </tr>
      `).join("");
    }

    async function loadTelemetryJobs() {
      const payload = await fetch("/jobs/telemetry?limit=50").then((r) => r.json());
      telemetryJobsBody.innerHTML = payload.jobs.map((job) => `
        <tr>
          <td>${job.shard || ""}</td>
          <td>${job.target_id || ""}</td>
          <td>${job.status || ""}</td>
          <td>${job.attempts || 0}</td>
          <td>${job.created_at_kst || ""}</td>
        </tr>
      `).join("");
    }

    async function refreshCollection() {
      banner.textContent = "최근 매치 수집 중";
      const response = await fetch("/collection/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      banner.textContent = `수집 완료: 신규 ${payload.result.queued_match_jobs}개, 기존 ${payload.result.existing_match_jobs}개`;
      await Promise.all([loadPlayers(), loadJobs()]);
    }

    async function processMatchJobs() {
      banner.textContent = "Match 상세 저장 중";
      const response = await fetch("/jobs/matches/process", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit: 10 }),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      banner.textContent = `상세 저장 완료: 저장 ${payload.result.stored_matches}개, telemetry 신규 ${payload.result.queued_telemetry_jobs}개, 실패 ${payload.result.failed_jobs}개`;
      await Promise.all([loadJobs(), loadTelemetryJobs()]);
    }

    async function processTelemetryJobs() {
      banner.textContent = "Telemetry 저장 중";
      const response = await fetch("/jobs/telemetry/process", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit: 5 }),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || response.statusText);
      }
      const payload = await response.json();
      const mb = (payload.result.stored_bytes / 1024 / 1024).toFixed(1);
      banner.textContent = `Telemetry 저장 완료: 저장 ${payload.result.stored_telemetry}개, ${mb}MB, 실패 ${payload.result.failed_jobs}개`;
      await loadTelemetryJobs();
    }

    async function unregisterPlayer(shard, accountId) {
      await fetch("/players/unregister", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ shard, account_id: accountId }),
      });
      await loadPlayers();
    }

    document.querySelector("#registerForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      await fetch("/players/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          shard: form.get("shard"),
          current_name: form.get("current_name"),
          account_id: form.get("account_id") || null,
          public_profile: form.get("public_profile") === "true",
        }),
      });
      event.currentTarget.reset();
      await loadPlayers();
    });

    Promise.all([loadStatus(), loadPlayers(), loadJobs(), loadTelemetryJobs()])
      .then(() => { banner.textContent = "localhost 전용 관리 화면"; })
      .catch((error) => { banner.textContent = `오류: ${error.message}`; });
  </script>
</body>
</html>
"""

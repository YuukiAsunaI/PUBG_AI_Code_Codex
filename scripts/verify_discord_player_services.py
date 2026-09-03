from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Callable
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pubg_ai.config import RuntimeConfig
from pubg_ai.database import connect_mysql
from pubg_ai.discord_bot import (
    build_discord_report_pages,
    format_match_explorer_detail,
    format_player_comparison,
    format_player_drop_zones,
    format_player_intelligence,
    format_player_recommendations,
    format_player_time_insights,
    format_player_weapon_detail,
)
from pubg_ai.match_explorer import MatchExplorerService
from pubg_ai.player_intelligence import PlayerIntelligenceService
from pubg_ai.player_recommendations import PlayerRecommendationService
from pubg_ai.player_stats import PlayerStatsService
from pubg_ai.player_trends import PlayerTrendFilters, PlayerTrendService
from pubg_ai.raw_storage import RawPayloadStore


Formatter = Callable[[Any], str]


def _validate_pages(text: str) -> dict[str, int]:
    pages = build_discord_report_pages(text)
    if not pages:
        raise AssertionError("Discord 보고서 페이지가 생성되지 않았습니다.")
    field_count = 0
    for page in pages:
        title = str(page.get("title") or "")
        description = str(page.get("description") or "")
        fields = list(page.get("fields") or [])
        if len(title) > 256 or len(description) > 4096 or len(fields) > 25:
            raise AssertionError("Discord 임베드 구성 요소 제한을 초과했습니다.")
        total_chars = len(title) + len(description)
        for field_name, field_value in fields:
            if len(str(field_name)) > 256 or len(str(field_value)) > 1024:
                raise AssertionError("Discord 임베드 필드 제한을 초과했습니다.")
            total_chars += len(str(field_name)) + len(str(field_value))
        if total_chars > 6000:
            raise AssertionError("Discord 임베드 한 페이지의 총 문자 제한을 초과했습니다.")
        field_count += len(fields)
    return {"pages": len(pages), "fields": field_count, "characters": len(text)}


def _run_report(
    results: dict[str, Any],
    label: str,
    loader: Callable[[], Any],
    formatter: Formatter,
) -> Any:
    started = perf_counter()
    value = loader()
    if value is None:
        raise AssertionError(f"{label}: 실제 데이터 보고서를 찾지 못했습니다.")
    text = formatter(value)
    page_summary = _validate_pages(text)
    results[label] = {
        "status": "passed",
        "elapsed_seconds": round(perf_counter() - started, 3),
        **page_summary,
    }
    return value


def verify(*, nickname: str, shard: str) -> dict[str, Any]:
    config = RuntimeConfig.from_sources(base_dir=ROOT)
    connection = connect_mysql(config.database)
    results: dict[str, Any] = {
        "player": nickname,
        "shard": shard,
        "checks": {},
    }
    checks = results["checks"]
    filters = PlayerTrendFilters(is_custom_match=False)
    detail_url = config.app.local_web_base_url
    try:
        intelligence = _run_report(
            checks,
            "comprehensive_analysis",
            lambda: PlayerIntelligenceService(connection).get_report(
                shard=shard,
                name=nickname,
                global_scope=True,
                filters=filters,
            ),
            lambda report: format_player_intelligence(report, detail_base_url=detail_url),
        )

        time_report = _run_report(
            checks,
            "time_analysis",
            lambda: PlayerTrendService(connection).get_report(
                shard=shard,
                name=nickname,
                global_scope=True,
                granularity="hour",
                filters=filters,
                bucket_limit=24,
            ),
            lambda report: format_player_time_insights(report, detail_base_url=detail_url),
        )

        _run_report(
            checks,
            "drop_zone_analysis",
            lambda: PlayerRecommendationService(connection).get_drop_zone_analysis(
                shard=shard,
                name=nickname,
                global_scope=True,
                min_matches=1,
                limit=20,
            ),
            lambda report: format_player_drop_zones(report, detail_base_url=detail_url),
        )

        map_report = PlayerTrendService(connection).get_report(
            shard=shard,
            name=nickname,
            global_scope=True,
            granularity="map",
            filters=filters,
            bucket_limit=50,
        )
        if map_report is None or not map_report.buckets:
            raise AssertionError("map_comparison: 비교할 실제 맵 데이터가 없습니다.")
        comparison_text = format_player_comparison(
            [(bucket.period_label, bucket.metrics) for bucket in map_report.buckets],
            title=f"{map_report.player.current_name} · 맵별 성과 비교",
            metric="kda",
            filters=filters,
            detail_base_url=detail_url,
        )
        checks["map_comparison"] = {
            "status": "passed",
            "groups": len(map_report.buckets),
            **_validate_pages(comparison_text),
        }

        catalog = PlayerStatsService(connection).get_lookup_catalog(
            shard=shard,
            name=nickname,
            global_scope=True,
            match_limit=1,
        )
        if catalog is None or not catalog.weapons:
            raise AssertionError("weapon_analysis: 분석 가능한 무기를 찾지 못했습니다.")
        weapon = max(catalog.weapons, key=lambda item: item.match_count)
        weapon_detail = _run_report(
            checks,
            "weapon_analysis",
            lambda: PlayerStatsService(connection).get_weapon_detail(
                shard=shard,
                name=nickname,
                weapon=weapon.weapon_code,
                global_scope=True,
                filters=filters,
            ),
            lambda report: format_player_weapon_detail(report, detail_base_url=detail_url),
        )
        checks["weapon_analysis"]["weapon"] = weapon_detail.weapon_name

        recommendation = _run_report(
            checks,
            "recommendations",
            lambda: PlayerRecommendationService(connection).get_recommendations(
                shard=shard,
                name=nickname,
                global_scope=True,
                min_matches=1,
                limit=5,
                filters=filters,
            ),
            lambda report: format_player_recommendations(report, detail_base_url=detail_url),
        )
        checks["recommendations"]["weapons"] = len(recommendation.weapons)
        checks["recommendations"]["loadouts"] = len(recommendation.loadouts)

        explorer = MatchExplorerService(
            connection,
            RawPayloadStore(
                config.app.raw_data_dir,
                compression=config.app.raw_compression,  # type: ignore[arg-type]
            ),
        )
        match_list = explorer.list_matches(shard=shard, telemetry_only=True, limit=1)
        matches = list(match_list.get("matches") or [])
        if not matches:
            raise AssertionError("full_match_detail: 저장된 텔레메트리 매치를 찾지 못했습니다.")
        match_detail = _run_report(
            checks,
            "full_match_detail",
            lambda: explorer.get_match_detail(str(matches[0]["match_id"])),
            lambda detail: format_match_explorer_detail(detail, detail_base_url=detail_url),
        )
        checks["full_match_detail"]["participants"] = len(match_detail["participants"])
        checks["full_match_detail"]["event_count"] = int(
            match_detail.get("telemetry", {}).get("event_count") or 0
        )

        checks["coverage"] = {
            "status": "passed",
            "matches": int(intelligence.overview.get("matches") or 0),
            "time_buckets": len(time_report.buckets),
        }
    finally:
        connection.close()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="실제 MySQL 데이터로 Discord 플레이어 분석 보고서를 검증합니다."
    )
    parser.add_argument("--nickname", default="Yuuki_Asuna---")
    parser.add_argument("--shard", default="steam")
    args = parser.parse_args()
    print(json.dumps(verify(nickname=args.nickname, shard=args.shard), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

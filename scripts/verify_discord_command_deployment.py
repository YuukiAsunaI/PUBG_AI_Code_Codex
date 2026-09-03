from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import requests

from pubg_ai.config import RuntimeConfig
from pubg_ai.discord_command_catalog import DISCORD_COMMAND_SPECS


FOCUS_COMMANDS = (
    "종합분석",
    "추세",
    "시간대",
    "무기",
    "추천",
    "비교",
    "낙하",
    "매치",
    "매치상세",
    "랭킹",
    "최근스냅샷",
)
PLAYER_PICKER_COMMANDS = (
    "유저조회",
    "전적",
    "종합분석",
    "교전",
    "추세",
    "시간대",
    "무기",
    "추천",
    "낙하",
    "매치",
    "유저삭제",
    "최근스냅샷",
    "pubg-delete-data",
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="앱 관리 Discord 봇의 실제 서버별 명령 메타데이터를 검증합니다."
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="실행 중인 로컬 관리앱 주소",
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=Path.cwd(),
        help=".env가 있는 프로젝트 경로",
    )
    args = parser.parse_args()

    status = _get_json(f"{args.base_url.rstrip('/')}/discord/bot/status")
    bot = dict(status.get("bot") or {})
    settings = dict(status.get("settings") or {})
    application_id = str(bot.get("bot_user_id") or "").strip()
    guild_ids = [str(value).strip() for value in settings.get("managed_guild_ids", [])]
    if not application_id or not application_id.isdigit():
        raise RuntimeError("실행 중인 관리 봇의 애플리케이션 ID를 확인할 수 없습니다.")
    if not guild_ids or any(not value.isdigit() for value in guild_ids):
        raise RuntimeError("검증할 관리 Discord 서버 목록을 확인할 수 없습니다.")

    config = RuntimeConfig.from_sources(base_dir=args.project_dir.resolve())
    token = str(config.secrets.discord_bot_token or "").strip()
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN이 설정되지 않았습니다.")

    headers = {"Authorization": f"Bot {token}"}
    catalogs: dict[str, list[dict[str, Any]]] = {}
    expected_by_guild: dict[str, set[str]] = {}
    for guild_id in guild_ids:
        local_exposure = _get_json(
            f"{args.base_url.rstrip('/')}/discord/bot/guild-commands/{guild_id}"
        )
        expected_by_guild[guild_id] = {
            str(value) for value in local_exposure.get("expected_commands", [])
        }
        catalogs[guild_id] = _get_json(
            (
                "https://discord.com/api/v10/applications/"
                f"{application_id}/guilds/{guild_id}/commands"
            ),
            headers=headers,
        )

    sample_guild_id = max(guild_ids, key=lambda value: len(catalogs[value]))
    sample = {str(item["name"]): item for item in catalogs[sample_guild_id]}
    missing_focus_commands = [name for name in FOCUS_COMMANDS if name not in sample]
    if missing_focus_commands:
        raise RuntimeError(
            "필수 사용자 명령이 배포되지 않았습니다: " + ", ".join(missing_focus_commands)
        )

    all_options = [
        option
        for command in sample.values()
        for option in command.get("options", [])
        if isinstance(option, dict)
    ]
    all_choices = [
        choice
        for option in all_options
        for choice in option.get("choices", [])
        if isinstance(choice, dict)
    ]
    player_picker_options = {
        command_name: next(
            (
                option
                for option in sample[command_name].get("options", [])
                if option.get("name") == "닉네임"
            ),
            None,
        )
        for command_name in PLAYER_PICKER_COMMANDS
    }
    checks = {
        "catalog_count_matches_source": len(sample) == len(DISCORD_COMMAND_SPECS),
        "all_configured_commands_present": all(
            {str(item.get("name")) for item in catalogs[guild_id]}
            == expected_by_guild[guild_id]
            for guild_id in guild_ids
        ),
        "no_blank_or_placeholder_option_descriptions": all(
            _valid_description(option.get("description")) for option in all_options
        ),
        "no_option_uses_more_than_25_static_choices": all(
            len(option.get("choices", [])) <= 25 for option in all_options
        ),
        "no_legacy_english_choice_labels": all(
            str(choice.get("name") or "").strip().casefold()
            not in {"steam", "kakao", "configured", "guild", "global", "raw 매치 데이터"}
            for choice in all_choices
        ),
        "trend_has_no_opaque_options_field": all(
            option.get("name") != "옵션" for option in sample["추세"].get("options", [])
        ),
        "match_id_is_optional_autocomplete": any(
            option.get("name") == "최근_매치"
            and not option.get("required", False)
            and option.get("autocomplete", False)
            for option in sample["매치"].get("options", [])
        ),
        "recommendation_minimum_sample_is_configurable": any(
            option.get("name") == "최소_표본_경기"
            for option in sample["추천"].get("options", [])
        ),
        "weapon_attachment_samples_are_configurable": {
            "개별_파츠_최소_경기",
            "파츠_조합_최소_경기",
            "파츠_표시_수",
        }.issubset(
            {
                str(option.get("name"))
                for option in sample["무기"].get("options", [])
            }
        ),
        "comparison_map_and_mode_filters_are_searchable": all(
            any(
                option.get("name") == option_name
                and option.get("autocomplete", False)
                for option in sample["비교"].get("options", [])
            )
            for option_name in ("맵", "게임_모드")
        ),
        "full_match_uses_search_picker_without_match_id": (
            any(
                option.get("name") == "검색어" and not option.get("required", False)
                for option in sample["매치상세"].get("options", [])
            )
            and all(
                option.get("name") not in {"매치_ID", "match_id"}
                for option in sample["매치상세"].get("options", [])
            )
        ),
        "registered_players_use_paged_search_picker": all(
            option
            and not option.get("required", False)
            and not option.get("autocomplete", False)
            and "페이지·검색" in str(option.get("description") or "")
            for option in player_picker_options.values()
        ),
    }
    result = {
        "application_id": application_id,
        "guild_command_counts": {
            guild_id: len(commands) for guild_id, commands in catalogs.items()
        },
        "guild_expected_counts": {
            guild_id: len(commands) for guild_id, commands in expected_by_guild.items()
        },
        "selected_commands": {
            name: _command_summary(sample[name]) for name in FOCUS_COMMANDS
        },
        "quality_checks": checks,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all(checks.values()) else 1


def _get_json(url: str, *, headers: dict[str, str] | None = None) -> Any:
    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()
    return response.json()


def _valid_description(value: Any) -> bool:
    description = str(value or "").strip()
    return bool(description) and "..." not in description and "…" not in description


def _command_summary(command: dict[str, Any]) -> dict[str, Any]:
    return {
        "description": command.get("description"),
        "options": [
            {
                "name": option.get("name"),
                "description": option.get("description"),
                "required": bool(option.get("required", False)),
                "autocomplete": bool(option.get("autocomplete", False)),
                "choice_count": len(option.get("choices", [])),
            }
            for option in command.get("options", [])
            if isinstance(option, dict)
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Callable, Mapping
from uuid import uuid4

from pubg_ai.time_utils import KST


DISCORD_API_BASE_URL = "https://discord.com/api/v10"
DISCORD_ACCEPTANCE_CONFIRMATION = "SEND_DISCORD_ACCEPTANCE_TEST"
DISCORD_TEXT_CHANNEL_TYPES = frozenset({0, 5})

ADMINISTRATOR = 1 << 3
VIEW_CHANNEL = 1 << 10
SEND_MESSAGES = 1 << 11
READ_MESSAGE_HISTORY = 1 << 16

DiscordRequest = Callable[..., Any]


class DiscordAcceptanceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class DiscordBotIdentity:
    user_id: str
    username: str
    is_bot: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DiscordGuildSummary:
    guild_id: str
    guild_name: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DiscordChannelCandidate:
    guild_id: str
    guild_name: str
    channel_id: str
    channel_name: str
    can_view: bool
    can_send: bool
    can_read_history: bool
    last_message_id: str | None
    position: int
    candidate_score: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DiscordGuildProbe:
    guild_id: str
    guild_name: str
    eligible_channel_count: int
    channels: list[DiscordChannelCandidate]

    def to_record(self) -> dict[str, Any]:
        return {
            "guild_id": self.guild_id,
            "guild_name": self.guild_name,
            "eligible_channel_count": self.eligible_channel_count,
            "channels": [channel.to_record() for channel in self.channels],
        }


@dataclass(frozen=True)
class DiscordProbeReport:
    bot: DiscordBotIdentity
    guild_count: int
    guilds: list[DiscordGuildProbe]

    def to_record(self) -> dict[str, Any]:
        return {
            "bot": self.bot.to_record(),
            "guild_count": self.guild_count,
            "guilds": [guild.to_record() for guild in self.guilds],
        }


@dataclass(frozen=True)
class DiscordAlertDelivery:
    test_id: str
    guild_id: str
    guild_name: str
    channel_id: str
    channel_name: str
    message_id: str
    sent_at_kst: str
    verified: bool

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DiscordCommandRoundTrip:
    guild_id: str
    channel_id: str
    command_message_id: str
    command_author_id: str
    command_name: str
    command_created_at: str | None
    reply_message_id: str
    reply_created_at: str | None
    reply_length: int

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DiscordRoundTripReport:
    bot: DiscordBotIdentity
    guild_id: str
    channel_id: str
    after_message_id: str
    expected_command: str | None
    messages_examined: int
    round_trips: list[DiscordCommandRoundTrip]

    @property
    def verified(self) -> bool:
        return bool(self.round_trips)

    def to_record(self) -> dict[str, Any]:
        return {
            "bot": self.bot.to_record(),
            "guild_id": self.guild_id,
            "channel_id": self.channel_id,
            "after_message_id": self.after_message_id,
            "expected_command": self.expected_command,
            "messages_examined": self.messages_examined,
            "verified": self.verified,
            "round_trips": [item.to_record() for item in self.round_trips],
        }


class DiscordAcceptanceClient:
    def __init__(
        self,
        bot_token: str,
        *,
        base_url: str = DISCORD_API_BASE_URL,
        timeout_seconds: float = 20.0,
        request: DiscordRequest | None = None,
        now: Callable[[], datetime] | None = None,
        test_id_factory: Callable[[], str] | None = None,
    ) -> None:
        token = str(bot_token or "").strip()
        if not token:
            raise DiscordAcceptanceError("DISCORD_BOT_TOKEN is required.")
        if timeout_seconds <= 0:
            raise DiscordAcceptanceError("timeout_seconds must be positive.")
        self._token = token
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._request_func = request
        self._now = now or (lambda: datetime.now(KST))
        self._test_id_factory = test_id_factory or (lambda: uuid4().hex[:12])

    def probe(
        self,
        *,
        guild_id: str | None = None,
        channel_id: str | None = None,
        channel_limit: int = 8,
    ) -> DiscordProbeReport:
        selected_guild_id = _optional_snowflake(guild_id, "guild_id")
        selected_channel_id = _optional_snowflake(channel_id, "channel_id")
        if selected_channel_id and not selected_guild_id:
            raise DiscordAcceptanceError("guild_id is required when channel_id is provided.")
        limit = max(1, min(int(channel_limit), 50))

        bot = self.current_bot()
        guild_records = self._get_json("/users/@me/guilds", params={"limit": "200"}, operation="guild list")
        if not isinstance(guild_records, list):
            raise DiscordAcceptanceError("Discord guild list returned an unexpected payload.")
        all_guild_count = len(guild_records)
        if selected_guild_id:
            guild_records = [item for item in guild_records if str(item.get("id")) == selected_guild_id]
            if not guild_records:
                raise DiscordAcceptanceError("The bot is not a member of the selected Discord guild.")

        probes: list[DiscordGuildProbe] = []
        for guild in sorted(guild_records, key=lambda item: str(item.get("name") or "").casefold()):
            probe = self._probe_guild(
                bot=bot,
                guild=guild,
                selected_channel_id=selected_channel_id,
                channel_limit=limit,
            )
            probes.append(probe)
        return DiscordProbeReport(bot=bot, guild_count=all_guild_count, guilds=probes)

    def list_guilds(self) -> list[DiscordGuildSummary]:
        records = self._get_json("/users/@me/guilds", params={"limit": "200"}, operation="guild list")
        if not isinstance(records, list):
            raise DiscordAcceptanceError("Discord guild list returned an unexpected payload.")

        guilds: list[DiscordGuildSummary] = []
        for record in records:
            if not isinstance(record, Mapping):
                raise DiscordAcceptanceError("Discord guild list returned an unexpected record.")
            guilds.append(
                DiscordGuildSummary(
                    guild_id=_required_snowflake(record.get("id"), "guild id"),
                    guild_name=str(record.get("name") or "").strip(),
                )
            )
        guilds.sort(key=lambda item: (item.guild_name.casefold(), item.guild_id))
        return guilds

    def current_bot(self) -> DiscordBotIdentity:
        payload = self._get_json("/users/@me", operation="current bot")
        if not isinstance(payload, Mapping) or not payload.get("id"):
            raise DiscordAcceptanceError("Discord current bot returned an unexpected payload.")
        return DiscordBotIdentity(
            user_id=str(payload["id"]),
            username=str(payload.get("username") or ""),
            is_bot=bool(payload.get("bot")),
        )

    def send_controlled_alert(
        self,
        *,
        guild_id: str,
        channel_id: str,
        confirmation: str,
    ) -> DiscordAlertDelivery:
        selected_guild_id = _required_snowflake(guild_id, "guild_id")
        selected_channel_id = _required_snowflake(channel_id, "channel_id")
        if confirmation != DISCORD_ACCEPTANCE_CONFIRMATION:
            raise DiscordAcceptanceError(
                f"confirmation must exactly equal {DISCORD_ACCEPTANCE_CONFIRMATION}."
            )
        report = self.probe(
            guild_id=selected_guild_id,
            channel_id=selected_channel_id,
            channel_limit=1,
        )
        guild = report.guilds[0]
        if not guild.channels:
            raise DiscordAcceptanceError("The selected Discord channel is not viewable and sendable by the bot.")
        channel = guild.channels[0]
        test_id = self._test_id_factory()
        sent_at = self._now().astimezone(KST).isoformat(timespec="seconds")
        content = (
            "[PUBG AI Discord acceptance test]\n"
            f"test_id: {test_id}\n"
            "Controlled alert delivery only. No API keys, tokens, player IDs, or storage paths are included."
        )
        created = self._post_json(
            f"/channels/{selected_channel_id}/messages",
            json_body={
                "content": content,
                "allowed_mentions": {"parse": []},
            },
            operation="controlled alert send",
        )
        if not isinstance(created, Mapping) or not created.get("id"):
            raise DiscordAcceptanceError("Discord controlled alert returned an unexpected payload.")
        message_id = str(created["id"])
        persisted = self._get_json(
            f"/channels/{selected_channel_id}/messages/{message_id}",
            operation="controlled alert verification",
        )
        verified = (
            isinstance(persisted, Mapping)
            and str(persisted.get("id")) == message_id
            and str((persisted.get("author") or {}).get("id")) == report.bot.user_id
        )
        if not verified:
            raise DiscordAcceptanceError("Discord controlled alert could not be verified after delivery.")
        return DiscordAlertDelivery(
            test_id=test_id,
            guild_id=selected_guild_id,
            guild_name=guild.guild_name,
            channel_id=selected_channel_id,
            channel_name=channel.channel_name,
            message_id=message_id,
            sent_at_kst=sent_at,
            verified=True,
        )

    def observe_round_trip(
        self,
        *,
        guild_id: str,
        channel_id: str,
        after_message_id: str,
        expected_command: str | None = "배그도움말",
        command_prefix: str = "!",
        limit: int = 50,
    ) -> DiscordRoundTripReport:
        selected_guild_id = _required_snowflake(guild_id, "guild_id")
        selected_channel_id = _required_snowflake(channel_id, "channel_id")
        baseline = _required_snowflake(after_message_id, "after_message_id")
        prefix = str(command_prefix or "").strip()
        if not prefix:
            raise DiscordAcceptanceError("command_prefix is required.")
        expected = str(expected_command or "").strip().casefold() or None
        bounded_limit = max(1, min(int(limit), 100))

        probe = self.probe(
            guild_id=selected_guild_id,
            channel_id=selected_channel_id,
            channel_limit=1,
        )
        guild = probe.guilds[0]
        if not guild.channels or not guild.channels[0].can_read_history:
            raise DiscordAcceptanceError("The bot cannot read message history in the selected channel.")

        params: dict[str, str] = {
            "limit": str(bounded_limit),
            "after": baseline,
        }
        payload = self._get_json(
            f"/channels/{selected_channel_id}/messages",
            params=params,
            operation="command round-trip observation",
        )
        if not isinstance(payload, list):
            raise DiscordAcceptanceError("Discord channel messages returned an unexpected payload.")
        messages = sorted(payload, key=lambda item: int(str(item.get("id") or 0)))
        by_reference: dict[str, list[Mapping[str, Any]]] = {}
        for message in messages:
            if str((message.get("author") or {}).get("id")) != probe.bot.user_id:
                continue
            reference_id = str((message.get("message_reference") or {}).get("message_id") or "")
            if reference_id:
                by_reference.setdefault(reference_id, []).append(message)

        round_trips: list[DiscordCommandRoundTrip] = []
        for message in messages:
            author = message.get("author") or {}
            if str(author.get("id")) == probe.bot.user_id or bool(author.get("bot")):
                continue
            command_name = _command_name(str(message.get("content") or ""), prefix)
            if command_name is None or (expected is not None and command_name.casefold() != expected):
                continue
            replies = by_reference.get(str(message.get("id")), [])
            if not replies:
                continue
            reply = replies[0]
            round_trips.append(
                DiscordCommandRoundTrip(
                    guild_id=selected_guild_id,
                    channel_id=selected_channel_id,
                    command_message_id=str(message["id"]),
                    command_author_id=str(author.get("id") or ""),
                    command_name=command_name,
                    command_created_at=_optional_text(message.get("timestamp")),
                    reply_message_id=str(reply["id"]),
                    reply_created_at=_optional_text(reply.get("timestamp")),
                    reply_length=len(str(reply.get("content") or "")),
                )
            )
        return DiscordRoundTripReport(
            bot=probe.bot,
            guild_id=selected_guild_id,
            channel_id=selected_channel_id,
            after_message_id=baseline,
            expected_command=expected_command,
            messages_examined=len(messages),
            round_trips=round_trips,
        )

    def _probe_guild(
        self,
        *,
        bot: DiscordBotIdentity,
        guild: Mapping[str, Any],
        selected_channel_id: str | None,
        channel_limit: int,
    ) -> DiscordGuildProbe:
        guild_id = _required_snowflake(guild.get("id"), "guild id")
        guild_name = str(guild.get("name") or "")
        member = self._get_json(
            f"/guilds/{guild_id}/members/{bot.user_id}",
            operation="current bot guild member",
        )
        roles = self._get_json(f"/guilds/{guild_id}/roles", operation="guild roles")
        channels = self._get_json(f"/guilds/{guild_id}/channels", operation="guild channels")
        if not isinstance(member, Mapping) or not isinstance(roles, list) or not isinstance(channels, list):
            raise DiscordAcceptanceError("Discord guild permission data returned an unexpected payload.")

        member_role_ids = {guild_id, *(str(role_id) for role_id in member.get("roles") or [])}
        base_permissions = 0
        for role in roles:
            if str(role.get("id")) in member_role_ids:
                base_permissions |= _permission_value(role.get("permissions"))

        candidates: list[DiscordChannelCandidate] = []
        selected_found = False
        for channel in channels:
            current_channel_id = str(channel.get("id") or "")
            if selected_channel_id and current_channel_id != selected_channel_id:
                continue
            if selected_channel_id:
                selected_found = True
            if int(channel.get("type", -1)) not in DISCORD_TEXT_CHANNEL_TYPES:
                continue
            permissions = _apply_channel_overwrites(
                base_permissions,
                channel=channel,
                guild_id=guild_id,
                bot_user_id=bot.user_id,
                member_role_ids=member_role_ids,
            )
            can_view = bool(permissions & VIEW_CHANNEL)
            can_send = bool(permissions & SEND_MESSAGES)
            if not (can_view and can_send):
                continue
            channel_name = str(channel.get("name") or "")
            candidates.append(
                DiscordChannelCandidate(
                    guild_id=guild_id,
                    guild_name=guild_name,
                    channel_id=current_channel_id,
                    channel_name=channel_name,
                    can_view=can_view,
                    can_send=can_send,
                    can_read_history=bool(permissions & READ_MESSAGE_HISTORY),
                    last_message_id=_optional_snowflake(channel.get("last_message_id"), "last_message_id"),
                    position=_int_value(channel.get("position")),
                    candidate_score=_candidate_score(channel_name),
                )
            )
        if selected_channel_id and not selected_found:
            raise DiscordAcceptanceError("The selected channel does not belong to the selected guild.")
        candidates.sort(
            key=lambda item: (-item.candidate_score, item.position, item.channel_name.casefold())
        )
        eligible_count = len(candidates)
        return DiscordGuildProbe(
            guild_id=guild_id,
            guild_name=guild_name,
            eligible_channel_count=eligible_count,
            channels=candidates[:channel_limit],
        )

    def _get_json(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        operation: str,
    ) -> Any:
        return self._request_json("GET", path, params=params, operation=operation)

    def _post_json(self, path: str, *, json_body: Mapping[str, Any], operation: str) -> Any:
        return self._request_json("POST", path, json_body=json_body, operation=operation)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any] | None = None,
        operation: str,
    ) -> Any:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bot {self._token}",
            "User-Agent": "PUBG-AI-Code-Codex/1.0",
        }
        if self._request_func is None:
            import httpx

            response = httpx.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
                timeout=self.timeout_seconds,
            )
        else:
            response = self._request_func(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
                timeout=self.timeout_seconds,
            )
        status_code = int(getattr(response, "status_code", 0) or 0)
        if not 200 <= status_code < 300:
            raise DiscordAcceptanceError(
                f"Discord {operation} failed with HTTP {status_code}.",
                status_code=status_code,
            )
        try:
            return response.json()
        except Exception as exc:
            raise DiscordAcceptanceError(
                f"Discord {operation} returned invalid JSON.",
                status_code=status_code,
            ) from exc


def _apply_channel_overwrites(
    permissions: int,
    *,
    channel: Mapping[str, Any],
    guild_id: str,
    bot_user_id: str,
    member_role_ids: set[str],
) -> int:
    if permissions & ADMINISTRATOR:
        return (1 << 53) - 1
    overwrites = channel.get("permission_overwrites") or []
    if not isinstance(overwrites, list):
        return permissions

    everyone = next(
        (
            item
            for item in overwrites
            if str(item.get("id")) == guild_id and _int_value(item.get("type"), default=-1) == 0
        ),
        None,
    )
    if everyone is not None:
        permissions &= ~_permission_value(everyone.get("deny"))
        permissions |= _permission_value(everyone.get("allow"))

    role_deny = 0
    role_allow = 0
    for overwrite in overwrites:
        if (
            _int_value(overwrite.get("type"), default=-1) == 0
            and str(overwrite.get("id")) in member_role_ids
        ):
            role_deny |= _permission_value(overwrite.get("deny"))
            role_allow |= _permission_value(overwrite.get("allow"))
    permissions &= ~role_deny
    permissions |= role_allow

    member = next(
        (
            item
            for item in overwrites
            if str(item.get("id")) == bot_user_id and _int_value(item.get("type"), default=-1) == 1
        ),
        None,
    )
    if member is not None:
        permissions &= ~_permission_value(member.get("deny"))
        permissions |= _permission_value(member.get("allow"))
    return permissions


def _command_name(content: str, prefix: str) -> str | None:
    normalized = str(content or "").strip()
    if not normalized.startswith(prefix):
        return None
    first_token = normalized.split(maxsplit=1)[0]
    command = first_token[len(prefix) :].strip()
    return command or None


def _candidate_score(channel_name: str) -> int:
    normalized = channel_name.casefold()
    keywords = (
        "pubg",
        "bot",
        "command",
        "test",
        "alert",
        "봇",
        "명령",
        "테스트",
        "알림",
        "일반",
        "general",
    )
    return sum(10 for keyword in keywords if keyword in normalized)


def _required_snowflake(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or not normalized.isdigit():
        raise DiscordAcceptanceError(f"{field_name} must be a numeric Discord ID.")
    return normalized


def _optional_snowflake(value: Any, field_name: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    return _required_snowflake(value, field_name)


def _permission_value(value: Any) -> int:
    return max(0, _int_value(value))


def _int_value(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _optional_text(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None

from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import asdict, dataclass
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any, Callable

from pubg_ai.config import RuntimeConfig
from pubg_ai.discord_bot import create_discord_bot
from pubg_ai.discord_permissions import DiscordPermissionChecker
from pubg_ai.local_settings import LocalSettingsStore
from pubg_ai.time_utils import isoformat_kst


class DiscordBotControllerError(RuntimeError):
    """Raised when the local Discord bot cannot be controlled."""


@dataclass(frozen=True)
class DiscordBotState:
    state: str
    running: bool
    ready: bool
    stop_requested: bool
    command_prefix: str
    bot_user: str | None
    bot_user_id: str | None
    guild_count: int
    started_at_kst: str | None
    stopped_at_kst: str | None
    last_sync_at_kst: str | None
    last_sync: dict[str, int] | None
    last_error: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


ConfigLoader = Callable[[], RuntimeConfig]
BotFactory = Callable[..., Any]


class DiscordBotController:
    def __init__(
        self,
        *,
        config_loader: ConfigLoader,
        settings_store: LocalSettingsStore,
        permission_checker: DiscordPermissionChecker,
        bot_factory: BotFactory = create_discord_bot,
    ) -> None:
        self._config_loader = config_loader
        self._settings_store = settings_store
        self._permission_checker = permission_checker
        self._bot_factory = bot_factory
        self._lock = Lock()
        self._thread: Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_ready = Event()
        self._bot: Any | None = None
        self._token: str | None = None
        self._state = DiscordBotState(
            state="stopped",
            running=False,
            ready=False,
            stop_requested=False,
            command_prefix="!",
            bot_user=None,
            bot_user_id=None,
            guild_count=0,
            started_at_kst=None,
            stopped_at_kst=None,
            last_sync_at_kst=None,
            last_sync=None,
            last_error=None,
        )

    def status(self) -> DiscordBotState:
        with self._lock:
            return self._state

    def start(self) -> DiscordBotState:
        config = self._config_loader()
        token = config.secrets.discord_bot_token
        if not token:
            raise DiscordBotControllerError("Discord 봇 토큰이 설정되지 않았습니다.")
        settings = self._settings_store.load_discord_bot_settings()
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise DiscordBotControllerError("Discord 봇이 이미 실행 중입니다.")
            try:
                bot = self._bot_factory(
                    config=config,
                    permission_checker=self._permission_checker,
                    scope_settings_store=self._settings_store,
                    command_prefix=settings.command_prefix,
                    status_callback=self._handle_bot_event,
                )
            except Exception as exc:
                raise DiscordBotControllerError(_safe_error(exc, token)) from exc
            self._bot = bot
            self._token = token
            self._loop_ready.clear()
            self._state = DiscordBotState(
                state="starting",
                running=True,
                ready=False,
                stop_requested=False,
                command_prefix=settings.command_prefix,
                bot_user=None,
                bot_user_id=None,
                guild_count=0,
                started_at_kst=isoformat_kst(),
                stopped_at_kst=None,
                last_sync_at_kst=self._state.last_sync_at_kst,
                last_sync=self._state.last_sync,
                last_error=None,
            )
            thread = Thread(target=self._run, name="pubg-ai-discord-bot", daemon=True)
            self._thread = thread
            thread.start()
            return self._state

    def stop(self, *, timeout_seconds: float = 15.0) -> DiscordBotState:
        timeout_seconds = max(0.1, float(timeout_seconds))
        deadline = monotonic() + timeout_seconds
        with self._lock:
            thread = self._thread
            loop = self._loop
            bot = self._bot
            if thread is None or not thread.is_alive():
                return self._state
            self._state = _replace_state(
                self._state,
                state="stopping",
                ready=False,
                stop_requested=True,
            )
        if loop is None:
            self._loop_ready.wait(
                timeout=min(2.0, max(0.05, deadline - monotonic()))
            )
            with self._lock:
                loop = self._loop
                bot = self._bot
        if loop is not None and loop.is_running() and bot is not None:
            try:
                future = asyncio.run_coroutine_threadsafe(bot.close(), loop)
                future.result(
                    timeout=max(
                        0.05,
                        min(timeout_seconds / 2, deadline - monotonic()),
                    )
                )
            except FutureTimeoutError:
                # Discord may finish closing after its gateway coroutine returns.
                # The thread deadline below is the authoritative shutdown result.
                pass
            except Exception as exc:
                with self._lock:
                    self._state = _replace_state(
                        self._state,
                        last_error=_safe_error(exc, self._token),
                    )
        thread.join(timeout=max(0.0, deadline - monotonic()))
        with self._lock:
            if thread.is_alive():
                message = "Discord 봇 종료 대기 시간이 초과되었습니다."
                self._state = _replace_state(self._state, last_error=message)
                raise DiscordBotControllerError(message)
            return self._state

    def sync_commands(self, guild_id: str | None = None) -> DiscordBotState:
        with self._lock:
            bot = self._bot
            loop = self._loop
            ready = self._state.ready
        if not ready or bot is None or loop is None or not loop.is_running():
            raise DiscordBotControllerError("Discord 봇을 먼저 실행해 주세요.")
        sync = getattr(bot, "pubg_sync_application_commands", None)
        if not callable(sync):
            raise DiscordBotControllerError("Discord 명령 동기화 기능을 사용할 수 없습니다.")
        guild_ids = [guild_id] if guild_id else None
        try:
            future = asyncio.run_coroutine_threadsafe(sync(guild_ids), loop)
            result = future.result(timeout=60.0)
        except Exception as exc:
            raise DiscordBotControllerError(_safe_error(exc, self._token)) from exc
        with self._lock:
            self._state = _replace_state(
                self._state,
                last_sync_at_kst=isoformat_kst(),
                last_sync={str(key): int(value) for key, value in dict(result or {}).items()},
                last_error=None,
            )
            return self._state

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._lock:
            self._loop = loop
            self._loop_ready.set()
            bot = self._bot
            token = self._token
            self._state = _replace_state(self._state, state="connecting")
        try:
            if bot is None or token is None:
                raise RuntimeError("Discord bot start state is incomplete.")
            loop.run_until_complete(bot.start(token))
        except Exception as exc:
            with self._lock:
                if not self._state.stop_requested:
                    self._state = _replace_state(
                        self._state,
                        state="error",
                        last_error=_safe_error(exc, token),
                    )
        finally:
            try:
                if bot is not None and not bot.is_closed():
                    loop.run_until_complete(bot.close())
            except Exception:
                pass
            loop.close()
            with self._lock:
                previous = self._state
                self._loop = None
                self._loop_ready.clear()
                self._bot = None
                self._token = None
                self._thread = None
                self._state = _replace_state(
                    previous,
                    state="stopped" if previous.stop_requested or not previous.last_error else "error",
                    running=False,
                    ready=False,
                    stopped_at_kst=isoformat_kst(),
                )

    def _handle_bot_event(self, event: str, details: dict[str, Any]) -> None:
        with self._lock:
            if event == "ready":
                self._state = _replace_state(
                    self._state,
                    state="running",
                    running=True,
                    ready=True,
                    bot_user=str(details.get("bot_user") or "") or None,
                    bot_user_id=str(details.get("bot_user_id") or "") or None,
                    guild_count=int(details.get("guild_count") or 0),
                    last_error=None,
                )
            elif event == "disconnected":
                self._state = _replace_state(self._state, state="reconnecting", ready=False)
            elif event == "resumed":
                self._state = _replace_state(
                    self._state,
                    state="running",
                    ready=True,
                    guild_count=int(details.get("guild_count") or self._state.guild_count),
                )
            elif event == "commands_synced":
                counts = details.get("guild_command_counts")
                self._state = _replace_state(
                    self._state,
                    last_sync_at_kst=isoformat_kst(),
                    last_sync={str(key): int(value) for key, value in dict(counts or {}).items()},
                )


def _replace_state(current: DiscordBotState, **changes: Any) -> DiscordBotState:
    values = current.to_record()
    values.update(changes)
    return DiscordBotState(**values)


def _safe_error(exc: BaseException, token: str | None) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    if token:
        message = message.replace(token, "[redacted]")
    return message[:1000]

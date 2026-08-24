from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import socket
from threading import Thread
from time import monotonic, sleep
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen


LOCAL_DESKTOP_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
DEFAULT_DESKTOP_PORT = 8000
DESKTOP_WINDOW_TITLE = "PUBG AI Local Manager"


class DesktopLaunchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DesktopEndpoint:
    host: str
    port: int

    @property
    def base_url(self) -> str:
        display_host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{display_host}:{self.port}"

    @property
    def health_url(self) -> str:
        return f"{self.base_url}/health"


def resolve_desktop_endpoint(
    *,
    configured_base_url: str | None,
    host: str | None = None,
    port: int | None = None,
) -> DesktopEndpoint:
    configured_host: str | None = None
    configured_port: int | None = None
    if configured_base_url:
        parsed = urlsplit(configured_base_url)
        if parsed.scheme in {"http", "https"}:
            configured_host = parsed.hostname
            try:
                configured_port = parsed.port
            except ValueError as exc:
                raise DesktopLaunchError("The configured local web URL contains an invalid port.") from exc

    selected_host = (host or configured_host or "127.0.0.1").strip().lower()
    if selected_host not in LOCAL_DESKTOP_HOSTS:
        raise DesktopLaunchError("Desktop mode only permits a localhost bind address.")

    selected_port = port if port is not None else configured_port or DEFAULT_DESKTOP_PORT
    if not 1 <= selected_port <= 65535:
        raise DesktopLaunchError("Desktop port must be between 1 and 65535.")
    return DesktopEndpoint(host=selected_host, port=selected_port)


def probe_local_manager(endpoint: DesktopEndpoint, *, timeout_seconds: float = 0.75) -> bool:
    try:
        with urlopen(endpoint.health_url, timeout=timeout_seconds) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        return False
    return payload.get("status") == "ok" and payload.get("local_only") is True


def _port_accepts_connections(endpoint: DesktopEndpoint, *, timeout_seconds: float = 0.25) -> bool:
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


class LocalManagerServer:
    def __init__(
        self,
        *,
        endpoint: DesktopEndpoint,
        base_dir: Path,
        env_file: str = ".env",
        health_probe: Callable[[DesktopEndpoint], bool] = probe_local_manager,
    ) -> None:
        self.endpoint = endpoint
        self.base_dir = base_dir.resolve()
        self.env_file = env_file
        self._health_probe = health_probe
        self._server: Any | None = None
        self._thread: Thread | None = None
        self._thread_error: BaseException | None = None
        self.owns_server = False

    def start(self, *, timeout_seconds: float = 15.0) -> bool:
        if self._health_probe(self.endpoint):
            return False
        if _port_accepts_connections(self.endpoint):
            raise DesktopLaunchError(
                f"Port {self.endpoint.port} is already occupied by a service that is not the PUBG local manager."
            )

        try:
            import uvicorn
        except ImportError as exc:
            raise DesktopLaunchError("uvicorn is required to start the desktop manager.") from exc

        from pubg_ai.web.app import create_app

        app = create_app(base_dir=self.base_dir, env_file=self.env_file)
        server_config = uvicorn.Config(
            app,
            host=self.endpoint.host,
            port=self.endpoint.port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(server_config)
        self._thread = Thread(target=self._serve, name="pubg-ai-local-web", daemon=True)
        self._thread.start()

        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            if self._health_probe(self.endpoint):
                self.owns_server = True
                return True
            if self._thread_error is not None:
                raise DesktopLaunchError(f"Local web server failed: {self._thread_error}") from self._thread_error
            if self._thread is not None and not self._thread.is_alive():
                raise DesktopLaunchError("Local web server stopped before it became ready.")
            sleep(0.1)

        self.stop()
        raise DesktopLaunchError(
            f"Local web server did not become ready at {self.endpoint.base_url} within {timeout_seconds:.0f} seconds."
        )

    def stop(self, *, timeout_seconds: float = 10.0) -> None:
        if not self.owns_server and self._thread is None:
            return
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout_seconds)
        self.owns_server = False

    def _serve(self) -> None:
        try:
            self._server.run()
        except BaseException as exc:
            self._thread_error = exc


class DesktopApi:
    _PURPOSES = frozenset({"raw", "replay", "backup", "quarantine"})

    def __init__(self, *, base_dir: Path, endpoint: DesktopEndpoint) -> None:
        self.base_dir = base_dir.resolve()
        self.endpoint = endpoint
        self._window: Any | None = None
        self._webview: Any | None = None

    def bind(self, *, window: Any, webview_module: Any) -> None:
        self._window = window
        self._webview = webview_module

    def runtime_status(self) -> dict[str, Any]:
        return {
            "mode": "desktop",
            "local_only": True,
            "base_url": self.endpoint.base_url,
            "project_dir": str(self.base_dir),
        }

    def choose_directory(self, purpose: str) -> dict[str, Any]:
        if purpose not in self._PURPOSES:
            raise ValueError("Unsupported storage directory purpose.")
        if self._window is None or self._webview is None:
            raise DesktopLaunchError("The desktop window is not ready.")

        dialog_type = getattr(self._webview, "FOLDER_DIALOG", None)
        if dialog_type is None:
            dialog_type = self._webview.FileDialog.FOLDER
        selected = self._window.create_file_dialog(dialog_type, allow_multiple=False)
        selected_path = _first_dialog_path(selected)
        return {
            "purpose": purpose,
            "selected": selected_path is not None,
            "path": selected_path,
        }


def _first_dialog_path(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        value = value[0]
    path = str(value).strip()
    return path or None


def run_desktop_app(
    *,
    base_dir: Path,
    configured_base_url: str | None,
    env_file: str = ".env",
    host: str | None = None,
    port: int | None = None,
    debug: bool = False,
    maximized: bool = False,
) -> None:
    try:
        import webview
    except ImportError as exc:
        raise DesktopLaunchError(
            'Desktop dependencies are missing. Run: python -m pip install -e ".[desktop]"'
        ) from exc

    endpoint = resolve_desktop_endpoint(
        configured_base_url=configured_base_url,
        host=host,
        port=port,
    )
    server = LocalManagerServer(endpoint=endpoint, base_dir=base_dir, env_file=env_file)
    server.start()

    api = DesktopApi(base_dir=base_dir, endpoint=endpoint)
    try:
        window = webview.create_window(
            DESKTOP_WINDOW_TITLE,
            endpoint.base_url,
            js_api=api,
            width=1520,
            height=940,
            min_size=(1040, 700),
            background_color="#0b0d0f",
            maximized=maximized,
        )
        api.bind(window=window, webview_module=webview)
        webview.start(
            debug=debug,
            icon=str(Path(__file__).resolve().parent / "assets" / "app_icon.ico"),
        )
    finally:
        server.stop()

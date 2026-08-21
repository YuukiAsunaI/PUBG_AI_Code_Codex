from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from pubg_ai.desktop import (
    DesktopApi,
    DesktopEndpoint,
    DesktopLaunchError,
    LocalManagerServer,
    _first_dialog_path,
    resolve_desktop_endpoint,
    run_desktop_app,
)
from pubg_ai.desktop_entry import find_project_base_dir, main_entry


class DesktopEndpointTests(unittest.TestCase):
    def test_uses_configured_local_web_url(self) -> None:
        endpoint = resolve_desktop_endpoint(
            configured_base_url="http://127.0.0.1:8018/player",
        )

        self.assertEqual(endpoint, DesktopEndpoint(host="127.0.0.1", port=8018))
        self.assertEqual(endpoint.base_url, "http://127.0.0.1:8018")
        self.assertEqual(endpoint.health_url, "http://127.0.0.1:8018/health")

    def test_explicit_localhost_and_port_override_configuration(self) -> None:
        endpoint = resolve_desktop_endpoint(
            configured_base_url="http://127.0.0.1:8018",
            host="localhost",
            port=9010,
        )

        self.assertEqual(endpoint, DesktopEndpoint(host="localhost", port=9010))

    def test_rejects_non_local_host(self) -> None:
        with self.assertRaisesRegex(DesktopLaunchError, "localhost"):
            resolve_desktop_endpoint(
                configured_base_url=None,
                host="0.0.0.0",
                port=8000,
            )

    def test_rejects_invalid_port(self) -> None:
        with self.assertRaisesRegex(DesktopLaunchError, "between 1 and 65535"):
            resolve_desktop_endpoint(configured_base_url=None, port=70000)


class DesktopApiTests(unittest.TestCase):
    def test_runtime_status_does_not_expose_secrets(self) -> None:
        api = DesktopApi(
            base_dir=Path("C:/workspace"),
            endpoint=DesktopEndpoint("127.0.0.1", 8018),
        )

        status = api.runtime_status()

        self.assertEqual(status["mode"], "desktop")
        self.assertTrue(status["local_only"])
        self.assertEqual(status["base_url"], "http://127.0.0.1:8018")
        self.assertNotIn("token", status)
        self.assertNotIn("api_key", status)

    def test_choose_directory_returns_first_selected_path(self) -> None:
        window = SimpleNamespace(
            create_file_dialog=Mock(return_value=(r"D:\BackUP\replay",)),
        )
        webview = SimpleNamespace(FOLDER_DIALOG="folder")
        api = DesktopApi(
            base_dir=Path.cwd(),
            endpoint=DesktopEndpoint("127.0.0.1", 8018),
        )
        api.bind(window=window, webview_module=webview)

        result = api.choose_directory("replay")

        self.assertEqual(
            result,
            {
                "purpose": "replay",
                "selected": True,
                "path": r"D:\BackUP\replay",
            },
        )
        window.create_file_dialog.assert_called_once_with("folder", allow_multiple=False)

    def test_choose_directory_rejects_unknown_purpose(self) -> None:
        api = DesktopApi(
            base_dir=Path.cwd(),
            endpoint=DesktopEndpoint("127.0.0.1", 8018),
        )

        with self.assertRaisesRegex(ValueError, "Unsupported"):
            api.choose_directory("secrets")

    def test_dialog_path_normalization(self) -> None:
        self.assertIsNone(_first_dialog_path(None))
        self.assertIsNone(_first_dialog_path(()))
        self.assertEqual(_first_dialog_path((r"D:\BackUP\raw",)), r"D:\BackUP\raw")


class DesktopLauncherTests(unittest.TestCase):
    def test_existing_manager_is_reused_without_ownership(self) -> None:
        server = LocalManagerServer(
            endpoint=DesktopEndpoint("127.0.0.1", 8018),
            base_dir=Path.cwd(),
            health_probe=lambda _: True,
        )

        self.assertFalse(server.start())
        self.assertFalse(server.owns_server)

    def test_window_uses_local_endpoint_and_server_is_stopped(self) -> None:
        window = SimpleNamespace()
        fake_webview = SimpleNamespace(
            create_window=Mock(return_value=window),
            start=Mock(),
        )
        fake_server = Mock()
        fake_server.start.return_value = True

        with (
            patch.dict(sys.modules, {"webview": fake_webview}),
            patch("pubg_ai.desktop.LocalManagerServer", return_value=fake_server),
        ):
            run_desktop_app(
                base_dir=Path.cwd(),
                configured_base_url="http://127.0.0.1:8018",
                maximized=True,
            )

        fake_server.start.assert_called_once_with()
        fake_server.stop.assert_called_once_with()
        positional = fake_webview.create_window.call_args.args
        keyword = fake_webview.create_window.call_args.kwargs
        self.assertEqual(positional[1], "http://127.0.0.1:8018")
        self.assertEqual(keyword["background_color"], "#0b0d0f")
        self.assertTrue(keyword["maximized"])
        fake_webview.start.assert_called_once_with(debug=False)

    def test_packaged_entrypoint_finds_project_above_dist_directory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / ".env").write_text("", encoding="utf-8")
            (project_dir / "config").mkdir()
            executable = project_dir / "dist" / "PUBG_AI_Manager.exe"
            executable.parent.mkdir()

            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "executable", str(executable)),
                patch.dict("os.environ", {}, clear=True),
                patch("pathlib.Path.cwd", return_value=executable.parent),
            ):
                resolved = find_project_base_dir()

        self.assertEqual(resolved, project_dir.resolve())

    def test_packaged_entrypoint_honors_explicit_base_directory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch.dict("os.environ", {"PUBG_AI_BASE_DIR": temp_dir}, clear=True):
                resolved = find_project_base_dir()

        self.assertEqual(resolved, Path(temp_dir).resolve())

    def test_packaged_entrypoint_does_not_report_clean_exit_as_error(self) -> None:
        with (
            patch("pubg_ai.desktop_entry.run", return_value=0),
            patch("pubg_ai.desktop_entry._show_startup_error") as show_error,
        ):
            main_entry()

        show_error.assert_not_called()

    def test_packaged_entrypoint_reports_real_startup_failure(self) -> None:
        with (
            patch("pubg_ai.desktop_entry.run", side_effect=RuntimeError("startup failed")),
            patch("pubg_ai.desktop_entry._show_startup_error") as show_error,
            self.assertRaisesRegex(RuntimeError, "startup failed"),
        ):
            main_entry()

        show_error.assert_called_once_with("startup failed")


if __name__ == "__main__":
    unittest.main()

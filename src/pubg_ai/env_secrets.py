from __future__ import annotations

from pathlib import Path
from threading import RLock
import re

from pubg_ai.file_io import atomic_write_bytes


ALLOWED_SECRET_NAMES = frozenset({"PUBG_API_KEY", "DISCORD_BOT_TOKEN"})
_ASSIGNMENT_PATTERN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


class EnvSecretError(RuntimeError):
    """Raised when a write-only application secret cannot be saved."""


class EnvSecretStore:
    def __init__(self, env_file: Path) -> None:
        self.env_file = env_file.resolve()
        self._lock = RLock()

    def set_secret(self, name: str, value: str) -> None:
        secret_name = self._validate_name(name)
        secret_value = self._validate_value(value)
        self._replace(secret_name, _quoted_dotenv_value(secret_value))

    def clear_secret(self, name: str) -> None:
        secret_name = self._validate_name(name)
        self._replace(secret_name, None)

    @staticmethod
    def _validate_name(name: str) -> str:
        normalized = str(name or "").strip()
        if normalized not in ALLOWED_SECRET_NAMES:
            raise EnvSecretError("This secret name cannot be managed by the local application.")
        return normalized

    @staticmethod
    def _validate_value(value: str) -> str:
        if not isinstance(value, str) or not value:
            raise EnvSecretError("Secret value cannot be empty.")
        if len(value) > 4096:
            raise EnvSecretError("Secret value is too long.")
        if any(character in value for character in ("\r", "\n", "\0")):
            raise EnvSecretError("Secret value cannot contain line breaks or null characters.")
        return value

    def _replace(self, name: str, encoded_value: str | None) -> None:
        with self._lock:
            try:
                original = (
                    self.env_file.read_text(encoding="utf-8-sig")
                    if self.env_file.exists()
                    else ""
                )
            except OSError as exc:
                raise EnvSecretError("Unable to read the local secret file.") from exc

            newline = "\r\n" if "\r\n" in original else "\n"
            output: list[str] = []
            replaced = False
            for line in original.splitlines():
                match = _ASSIGNMENT_PATTERN.match(line)
                if match is None or match.group(1) != name:
                    output.append(line)
                    continue
                if not replaced and encoded_value is not None:
                    output.append(f"{name}={encoded_value}")
                    replaced = True

            if encoded_value is not None and not replaced:
                output.append(f"{name}={encoded_value}")

            body = newline.join(output)
            if output:
                body += newline
            try:
                self.env_file.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(self.env_file, body.encode("utf-8"))
            except OSError as exc:
                raise EnvSecretError("Unable to save the local secret file.") from exc


def _quoted_dotenv_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'

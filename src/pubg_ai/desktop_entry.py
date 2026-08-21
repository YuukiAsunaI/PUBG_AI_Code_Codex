from __future__ import annotations

import os
from pathlib import Path
import sys

from pubg_ai.cli import main


def find_project_base_dir() -> Path:
    configured = os.environ.get("PUBG_AI_BASE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    seeds = [Path.cwd()]
    if getattr(sys, "frozen", False):
        seeds.insert(0, Path(sys.executable).resolve().parent)
    else:
        seeds.insert(0, Path(__file__).resolve().parents[2])

    checked: set[Path] = set()
    for seed in seeds:
        for candidate in (seed, *seed.parents):
            resolved = candidate.resolve()
            if resolved in checked:
                continue
            checked.add(resolved)
            if (resolved / ".env").is_file() and (resolved / "config").is_dir():
                return resolved
    return Path.cwd().resolve()


def run() -> int:
    return main(
        [
            "--base-dir",
            str(find_project_base_dir()),
            "run-desktop",
            "--maximized",
        ]
    )


def _show_startup_error(message: str) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0,
            message,
            "PUBG AI Local Manager",
            0x10,
        )
    except (AttributeError, OSError):
        pass


def main_entry() -> None:
    try:
        exit_code = run()
    except Exception as exc:
        _show_startup_error(str(exc))
        raise
    if exit_code not in (None, 0):
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main_entry()

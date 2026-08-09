from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from pubg_ai.replay_storage import ReplayArtifactStore, content_addressed_filename


def test_content_addressed_replay_writes_preserve_previous_artifact() -> None:
    first_body = b"first replay body"
    second_body = b"updated replay body"
    first_name = content_addressed_filename(stem="player-route", data=first_body, suffix="jpg")
    second_name = content_addressed_filename(stem="player-route", data=second_body, suffix=".jpg")

    assert first_name != second_name

    with TemporaryDirectory() as temp_dir:
        store = ReplayArtifactStore(Path(temp_dir))
        created_at = datetime(2026, 8, 10, tzinfo=UTC)
        first = store.write_bytes(
            "map_snapshot",
            "steam",
            "match-1",
            first_body,
            first_name,
            "image/jpeg",
            created_at,
        )
        second = store.write_bytes(
            "map_snapshot",
            "steam",
            "match-1",
            second_body,
            second_name,
            "image/jpeg",
            created_at,
        )

        assert first.sha256 in Path(first.relative_path).name
        assert second.sha256 in Path(second.relative_path).name
        assert store.verify(first)
        assert store.verify(second)

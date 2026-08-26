from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping
import unittest

from pubg_ai.match_collection_policy import CUSTOM_MATCH_REASON, TRAINING_MATCH_REASON
from pubg_ai.match_job_processor import MatchJobProcessor, ProcessedMatchJob
from pubg_ai.pubg_client import PubgMatchDetails, PubgRateLimit


class MatchJobExclusionTests(unittest.TestCase):
    def test_excluded_match_never_enters_raw_or_normalized_storage(self) -> None:
        processor = RecordingProcessor(_match(is_custom_match=True))

        processed = processor._process_job({"shard": "steam", "target_id": "match-1"})

        self.assertEqual(processed.exclusion_reason, CUSTOM_MATCH_REASON)
        self.assertEqual(processed.telemetry_job_status, "excluded")
        self.assertEqual(processor.raw_store.write_calls, 0)
        self.assertEqual(processor.excluded_match_ids, ["match-1"])
        self.assertEqual(processor.normalized_write_calls, 0)

    def test_processing_result_counts_exclusions_without_marking_them_failed(self) -> None:
        processor = AggregateProcessor()

        result = processor.process_queued_matches(limit=10)

        self.assertEqual(result.fetched_matches, 3)
        self.assertEqual(result.stored_matches, 1)
        self.assertEqual(result.excluded_matches, 2)
        self.assertEqual(result.excluded_custom_matches, 1)
        self.assertEqual(result.excluded_training_matches, 1)
        self.assertEqual(result.missing_telemetry_jobs, 0)
        self.assertEqual(result.failed_jobs, 0)


class RecordingProcessor(MatchJobProcessor):
    def __init__(self, match: PubgMatchDetails) -> None:
        self.client = FakeClient(match)
        self.raw_store = FailingRawStore()
        super().__init__(object(), self.client, self.raw_store)  # type: ignore[arg-type]
        self.excluded_match_ids: list[str] = []
        self.normalized_write_calls = 0

    def _upsert_excluded_match(
        self,
        *,
        match: PubgMatchDetails,
        decision: Any,
        detected_at: Any,
    ) -> None:
        self.excluded_match_ids.append(match.match_id)

    def _upsert_match(self, **kwargs: Any) -> None:
        self.normalized_write_calls += 1

    def _upsert_raw_match_payload(self, **kwargs: Any) -> None:
        self.normalized_write_calls += 1

    def _upsert_match_participants(self, match: PubgMatchDetails) -> int:
        self.normalized_write_calls += 1
        return 0

    def _ensure_telemetry_job(self, match: PubgMatchDetails) -> str:
        self.normalized_write_calls += 1
        return "queued"


class AggregateProcessor(MatchJobProcessor):
    def __init__(self) -> None:
        super().__init__(object(), object(), object())  # type: ignore[arg-type]

    def _recover_stale_running_jobs(self) -> int:
        return 0

    def _list_queued_match_jobs(self, *, limit: int) -> list[dict[str, Any]]:
        return [
            {"id": 1, "kind": "custom"},
            {"id": 2, "kind": "training"},
            {"id": 3, "kind": "stored"},
        ]

    def _mark_job_running(self, job: Mapping[str, Any]) -> bool:
        return True

    def _mark_job_succeeded(
        self,
        job_id: int,
        *,
        processed_rate_limit: PubgRateLimit | None,
    ) -> None:
        return None

    def _process_job(self, job: Mapping[str, Any]) -> ProcessedMatchJob:
        kind = str(job["kind"])
        if kind == "custom":
            return ProcessedMatchJob(0, "excluded", PubgRateLimit(), CUSTOM_MATCH_REASON)
        if kind == "training":
            return ProcessedMatchJob(0, "excluded", PubgRateLimit(), TRAINING_MATCH_REASON)
        return ProcessedMatchJob(7, "existing", PubgRateLimit())


class FakeClient:
    def __init__(self, match: PubgMatchDetails) -> None:
        self.match = match

    def fetch_match(self, shard: str, match_id: str) -> PubgMatchDetails:
        return self.match


class FailingRawStore:
    def __init__(self) -> None:
        self.write_calls = 0

    def write_json(self, *args: Any, **kwargs: Any) -> Any:
        self.write_calls += 1
        raise AssertionError("excluded matches must not be written to raw storage")


def _match(**changes: object) -> PubgMatchDetails:
    base = PubgMatchDetails(
        match_id="match-1",
        shard="steam",
        map_name="Baltic_Main",
        game_mode="squad-fpp",
        match_type="official",
        created_at="2026-08-26T00:00:00Z",
        duration_seconds=1800,
        season_state="progress",
        is_custom_match=False,
        telemetry_url="https://telemetry-cdn.pubg.com/match-1",
        participants=[],
        raw_payload={"data": {"id": "match-1"}},
        rate_limit=PubgRateLimit(),
    )
    return replace(base, **changes)


if __name__ == "__main__":
    unittest.main()

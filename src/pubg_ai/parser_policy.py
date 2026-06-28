from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


CURRENT_MATCH_METADATA_PARSER_VERSION = "match-metadata-v1"
CURRENT_TELEMETRY_PARSER_VERSION = "telemetry-parser-v1"
ParseRunStatus = Literal["pending", "running", "succeeded", "failed", "superseded"]


@dataclass(frozen=True)
class ParseRunPolicy:
    current_version: str = CURRENT_TELEMETRY_PARSER_VERSION

    def should_reparse(self, stored_parser_version: str | None) -> bool:
        return stored_parser_version != self.current_version

    def next_status_for_version(self, stored_parser_version: str | None) -> ParseRunStatus:
        return "pending" if self.should_reparse(stored_parser_version) else "succeeded"

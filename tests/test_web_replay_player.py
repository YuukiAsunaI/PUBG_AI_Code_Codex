from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from pubg_ai.web.app import create_app


class WebReplayPlayerTests(unittest.TestCase):
    def test_index_includes_replay_player_controls(self) -> None:
        client = TestClient(create_app())
        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertIn("2D Replay Player", body)
        self.assertIn('id="timelineSelect"', body)
        self.assertIn('id="timelineScrubber"', body)
        self.assertIn('id="timelineEventList"', body)
        self.assertIn('id="timelineEventDetail"', body)
        self.assertIn('id="timelineTeamList"', body)
        self.assertIn('id="timelineShowTeam"', body)
        self.assertIn('id="timelineShowPhase"', body)
        self.assertIn('id="timelineFollowPlayer"', body)
        self.assertIn('id="timelineZoom"', body)
        self.assertIn('id="replayCanvas"', body)
        self.assertIn("loadSelectedTimeline", body)
        self.assertIn("renderTimelineTeamList", body)
        self.assertIn("drawReplayTeamTracks", body)
        self.assertIn("team_tracks", body)
        self.assertIn("replayViewport", body)
        self.assertIn("replayViewportCenter", body)
        self.assertIn("replayZoom", body)
        self.assertIn("canvasPointVisible", body)
        self.assertIn("isReviveAction", body)
        self.assertIn("drawPlus", body)
        self.assertIn("revive_given", body)
        self.assertIn("revive_received", body)
        self.assertIn("phase_events", body)
        self.assertIn("drawReplayPhaseRings", body)
        self.assertIn("activePhaseEvent", body)
        self.assertIn("drawMapCircle", body)
        self.assertIn("renderReplayFrame", body)
        self.assertIn("timelineEvents", body)
        self.assertIn("combatRelatedLabel", body)
        self.assertIn("seekTimelineEvent", body)
        self.assertIn("loadReplayMapImage", body)
        self.assertIn("/replay/map-assets/", body)

    def test_index_includes_lookup_and_replay_section_anchors(self) -> None:
        client = TestClient(create_app())
        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertIn('id="registered-players"', body)
        self.assertIn('id="profile-lookup"', body)
        self.assertIn('id="weapon-lookup"', body)
        self.assertIn('id="recommendation-lookup"', body)
        self.assertIn('id="match-lookup"', body)
        self.assertIn('id="ranking-lookup"', body)
        self.assertIn('id="replay-player"', body)
        self.assertIn('id="replay-artifacts"', body)
        self.assertIn('id="playersBody"', body)
        self.assertIn("loadInitialLookupPrefillFromUrl", body)
        self.assertIn("lookup_target", body)
        self.assertIn("lookup_match_id", body)
        self.assertIn("replay_artifact_id", body)
        self.assertIn("replayArtifactFilter", body)
        self.assertIn("registeredPlayerHighlight", body)
        self.assertIn("ranking_metric", body)
        self.assertIn("ranking-lookup", body)

    def test_unknown_map_asset_returns_404(self) -> None:
        client = TestClient(create_app())
        response = client.get("/replay/map-assets/Unknown_Main")

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()

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
        self.assertIn('id="replayCanvas"', body)
        self.assertIn("loadSelectedTimeline", body)
        self.assertIn("renderReplayFrame", body)
        self.assertIn("timelineEvents", body)
        self.assertIn("seekTimelineEvent", body)
        self.assertIn("loadReplayMapImage", body)
        self.assertIn("/replay/map-assets/", body)

    def test_unknown_map_asset_returns_404(self) -> None:
        client = TestClient(create_app())
        response = client.get("/replay/map-assets/Unknown_Main")

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()

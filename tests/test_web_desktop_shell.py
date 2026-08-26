from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from pubg_ai.web.app import create_app


class WebDesktopShellTests(unittest.TestCase):
    def setUp(self) -> None:
        self.body = TestClient(create_app()).get("/").text

    def test_index_includes_operational_workspace_shell(self) -> None:
        self.assertIn('class="app-header"', self.body)
        self.assertIn('id="workspaceNav"', self.body)
        self.assertIn('class="system-rail"', self.body)
        self.assertIn('id="kstClock"', self.body)
        self.assertIn('id="refreshWorkspace"', self.body)
        for view in (
            "overview",
            "players",
            "replay",
            "collection",
            "discord",
            "operations",
            "settings",
        ):
            self.assertIn(f'data-view-target="{view}"', self.body)
            self.assertIn(f'data-view="{view}"', self.body)
        self.assertIn("workspaceViewFromLocation", self.body)
        self.assertIn('target?.closest("[data-view]")', self.body)
        self.assertIn("syncWorkspaceToLocation", self.body)

    def test_index_includes_desktop_folder_picker_bridge(self) -> None:
        for purpose, input_name in (
            ("raw", "raw_data_dir"),
            ("replay", "replay_data_dir"),
            ("backup", "backup_data_dir"),
            ("quarantine", "quarantine_data_dir"),
        ):
            self.assertIn(f'data-path-purpose="{purpose}"', self.body)
            self.assertIn(f'data-path-input="{input_name}"', self.body)
        self.assertIn("window.pywebview.api.choose_directory", self.body)
        self.assertIn('window.addEventListener("pywebviewready"', self.body)
        self.assertIn("body.desktop-host .desktop-only", self.body)

    def test_shell_has_stable_desktop_and_mobile_layout_rules(self) -> None:
        self.assertIn("grid-template-columns: 218px minmax(0, 1fr) 252px", self.body)
        self.assertIn('body[data-active-view="replay"]', self.body)
        self.assertIn("@media (max-width: 820px)", self.body)
        self.assertIn("@media (max-width: 520px)", self.body)

    def test_workspace_navigation_does_not_scroll_the_document_shell(self) -> None:
        self.assertIn('window.history.scrollRestoration = "manual"', self.body)
        self.assertIn("function resetWorkspaceViewport()", self.body)
        self.assertIn("function enforceDesktopAppShellOrigin()", self.body)
        self.assertIn("function scrollWithinWorkspace(element", self.body)
        self.assertIn("requestAnimationFrame(resetWorkspaceViewport)", self.body)
        self.assertNotIn(
            'document.getElementById(options.focusId)?.scrollIntoView({ block: "start" })',
            self.body,
        )

    def test_watchlist_dialog_markup_keeps_all_workspace_sections_inside_main(self) -> None:
        self.assertIn('<input name="account_id" type="hidden">', self.body)
        self.assertNotIn('<input name="account_id" type="hidden>', self.body)
        main_markup = self.body[
            self.body.index('<main id="workspace">') : self.body.index("</main>")
        ]
        for section_id in (
            "intelligence-analysis",
            "match-job-queue",
            "player-intelligence-audit",
            "replay-player",
        ):
            self.assertIn(f'<section id="{section_id}"', main_markup)

    def test_recommendation_view_supports_large_samples_and_charts(self) -> None:
        self.assertIn('name="min_matches" type="number" min="1" step="1"', self.body)
        self.assertNotIn('name="min_matches" type="number" min="1" max="50"', self.body)
        self.assertIn('data-recommendation-view="summary"', self.body)
        self.assertIn('data-recommendation-view="chart"', self.body)
        self.assertIn("renderRecommendationCharts", self.body)
        self.assertIn("data-recommendation-chart-metric", self.body)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from pubg_ai.web.app import create_app


class WebLocalSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app())

    def test_rejects_untrusted_host(self) -> None:
        response = self.client.get("/health", headers={"host": "evil.example"})

        self.assertEqual(response.status_code, 400)

    def test_rejects_cross_site_state_change(self) -> None:
        response = self.client.post(
            "/collector/worker/stop",
            headers={
                "origin": "https://evil.example",
                "sec-fetch-site": "cross-site",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-frame-options"], "DENY")

    def test_accepts_same_origin_state_change(self) -> None:
        response = self.client.post(
            "/collector/worker/stop",
            headers={
                "origin": "http://testserver",
                "sec-fetch-site": "same-origin",
            },
        )

        self.assertEqual(response.status_code, 200)

    def test_accepts_non_browser_state_change_without_origin(self) -> None:
        response = self.client.post("/collector/worker/stop")

        self.assertEqual(response.status_code, 200)

    def test_sets_browser_security_headers(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")


if __name__ == "__main__":
    unittest.main()

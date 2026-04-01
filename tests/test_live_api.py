import json
import os
import unittest
from pathlib import Path
from urllib import error
from urllib import parse
from urllib import request

from arbitrage_bot.core.env_loader import load_env_file

ENV_FILE_PATH = Path.home() / ".config" / "arbivision" / ".env"


def _base_url():
    host = os.environ.get("APP_HOST", "127.0.0.1")
    port = int(os.environ.get("APP_PORT", "8000"))
    scheme = os.environ.get("APP_SCHEME", "http")
    return f"{scheme}://{host}:{port}"


def _request_json(url, headers=None):
    req = request.Request(url, headers=headers or {})
    with request.urlopen(req, timeout=10) as response:
        payload = response.read().decode("utf-8")
        return response.status, json.loads(payload)


@unittest.skipUnless(
    os.environ.get("RUN_LIVE_TESTS") == "1",
    "set RUN_LIVE_TESTS=1 to run live API smoke tests",
)
class LiveApiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_env_file(ENV_FILE_PATH)
        cls.base_url = _base_url()
        cls.admin_token = os.environ.get("ADMIN_API_TOKEN", "").strip()


    def test_root_returns_expected_navigation_links(self):
        status_code, payload = _request_json(f"{self.base_url}/")

        self.assertEqual(status_code, 200)
        self.assertEqual(payload["message"], "Arbitrage Alert Bot API is running")
        self.assertEqual(payload["health"], "/api/health")
        self.assertEqual(payload["status"], "/api/status")


    def test_health_returns_ok(self):
        status_code, payload = _request_json(f"{self.base_url}/api/health")

        self.assertEqual(status_code, 200)
        self.assertEqual(payload, {"status": "ok"})


    def test_status_returns_runtime_counters(self):
        status_code, payload = _request_json(f"{self.base_url}/api/status")

        self.assertEqual(status_code, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["service"], "arbitrage-alert-bot")
        self.assertIn("market_counts", payload)
        self.assertIn("pair_counts", payload)
        self.assertIn("opportunity_counts", payload)
        self.assertIn("alert_counts", payload)
        self.assertIsInstance(payload["market_counts"]["total"], int)
        self.assertIsInstance(payload["pair_counts"]["total"], int)
        self.assertIsInstance(payload["opportunity_counts"]["total"], int)
        self.assertIsInstance(payload["alert_counts"]["queued"], int)


    def test_admin_pairs_requires_token(self):
        query = parse.urlencode({"status": "auto_approved"})

        with self.assertRaises(error.HTTPError) as ctx:
            _request_json(f"{self.base_url}/api/admin/pairs?{query}")

        self.assertEqual(ctx.exception.code, 401)
        ctx.exception.close()


    def test_admin_pairs_returns_json_for_valid_token(self):
        self.assertTrue(self.admin_token, "ADMIN_API_TOKEN must be set for live admin smoke tests")
        query = parse.urlencode({"status": "auto_approved"})
        headers = {"X-Admin-Token": self.admin_token}

        status_code, payload = _request_json(
            f"{self.base_url}/api/admin/pairs?{query}",
            headers=headers,
        )

        self.assertEqual(status_code, 200)
        self.assertIn("data", payload)
        self.assertIsInstance(payload["data"], list)
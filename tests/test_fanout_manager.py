import unittest
from datetime import timedelta
from datetime import datetime
from datetime import timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch

from arbitrage_bot.models.orm import Alert
from arbitrage_bot.models.orm import Market
from arbitrage_bot.services.fanout_manager import FanoutManager


class FakeScalarResult:
    def __init__(self, items):
        self.items = items


    def scalars(self):
        return self


    def all(self):
        return list(self.items)


class FakeDbSession:
    def __init__(self):
        self.added = []
        self.flush_calls = 0


    def add(self, item):
        self.added.append(item)


    async def flush(self):
        self.flush_calls += 1


    async def execute(self, stmt):
        compiled = str(stmt)
        if "SELECT alerts.telegram_chat_id" in compiled:
            return FakeScalarResult([])
        raise AssertionError(f"unexpected stmt: {compiled}")


class FanoutManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_fanout_creates_alerts_only_for_eligible_targets(self):
        db = FakeDbSession()
        manager = FanoutManager(db)
        opportunity = SimpleNamespace(
            id=7,
            net_roi=0.12,
            capital_required=10.0,
        )
        pair = SimpleNamespace(id=5)
        market_a = Market(
            id=101,
            platform="polymarket",
            platform_market_id="poly-101",
            status="active",
            tradable=True,
            title="market a",
            normalized_title="market a",
            description="",
            outcomes_json=[],
            raw_payload_json={"endDate": "2026-03-25T00:00:00+00:00"},
            category="",
            slug="",
        )
        market_b = Market(
            id=202,
            platform="predict_fun",
            platform_market_id="pf-202",
            status="active",
            tradable=True,
            title="market b",
            normalized_title="market b",
            description="",
            outcomes_json=[],
            raw_payload_json={"resolveDate": "2026-03-26T00:00:00+00:00"},
            category="",
            slug="",
        )

        with patch.object(
            manager,
            "_get_delivery_targets",
            new=AsyncMock(
                return_value=[
                    {
                        "user_id": 11,
                        "subscription_id": 21,
                        "telegram_chat_id": "1001",
                        "preferences": {
                            "min_roi_percent": None,
                            "max_capital_usd": None,
                            "max_days_to_close": None,
                        },
                    },
                    {
                        "user_id": 12,
                        "subscription_id": 22,
                        "telegram_chat_id": "1002",
                        "preferences": {
                            "min_roi_percent": 50.0,
                            "max_capital_usd": None,
                            "max_days_to_close": None,
                        },
                    },
                ]
            ),
        ):
            created_count = await manager._fanout_opportunity(opportunity, pair, market_a, market_b)

        self.assertEqual(created_count, 1)
        self.assertEqual(db.flush_calls, 1)
        self.assertEqual(len(db.added), 1)
        self.assertIsInstance(db.added[0], Alert)
        self.assertEqual(db.added[0].telegram_chat_id, "1001")


class TelegramDeliveryRetryTests(unittest.TestCase):
    def test_mark_alert_retry_sets_retry_with_backoff(self):
        from arbitrage_bot.tg_bot.bot import _mark_alert_retry

        alert = SimpleNamespace(
            status="queued",
            attempt_count=0,
            next_retry_at=None,
            error_message=None,
        )
        now = datetime(2026, 4, 3, tzinfo=timezone.utc)

        with patch("arbitrage_bot.tg_bot.bot.settings.TELEGRAM_DELIVERY_MAX_ATTEMPTS", 3), patch(
            "arbitrage_bot.tg_bot.bot.settings.TELEGRAM_DELIVERY_RETRY_SECONDS",
            15.0,
        ):
            _mark_alert_retry(alert, RuntimeError("boom"), now)

        self.assertEqual(alert.status, "retry")
        self.assertEqual(alert.attempt_count, 1)
        self.assertEqual(alert.next_retry_at, now + timedelta(seconds=15))
        self.assertEqual(alert.error_message, "boom")


    def test_mark_alert_retry_marks_failed_after_limit(self):
        from arbitrage_bot.tg_bot.bot import _mark_alert_retry

        alert = SimpleNamespace(
            status="retry",
            attempt_count=2,
            next_retry_at=None,
            error_message=None,
        )
        now = datetime(2026, 4, 3, tzinfo=timezone.utc)

        with patch("arbitrage_bot.tg_bot.bot.settings.TELEGRAM_DELIVERY_MAX_ATTEMPTS", 3):
            _mark_alert_retry(alert, RuntimeError("boom"), now)

        self.assertEqual(alert.status, "failed")
        self.assertEqual(alert.attempt_count, 3)
        self.assertIsNone(alert.next_retry_at)
import unittest
from datetime import datetime
from datetime import timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch

from arbitrage_bot.services import ingestion as ingestion_module
from arbitrage_bot.services.ingestion import IngestionService


class IngestionOutcomeNormalizationTests(unittest.TestCase):
    def setUp(self):
        ingestion_module._source_last_sync_completed_at.clear()
        self.service = IngestionService(db_session=None)


    def test_normalizes_string_outcomes(self):
        result = self.service._normalize_outcomes(["Yes", "No"])

        self.assertEqual(
            result,
            [
                {"id": "0", "label": "Yes", "slug": "yes"},
                {"id": "1", "label": "No", "slug": "no"},
            ],
        )


    def test_normalizes_dict_outcomes_and_preserves_ids(self):
        result = self.service._normalize_outcomes(
            [
                {"id": 11, "label": "Yes", "token_id": "poly-yes"},
                {"contractId": "pf-no", "name": "No"},
            ]
        )

        self.assertEqual(result[0]["id"], "11")
        self.assertEqual(result[0]["slug"], "yes")
        self.assertEqual(result[0]["token_id"], "poly-yes")
        self.assertEqual(result[1]["id"], "pf-no")
        self.assertEqual(result[1]["slug"], "no")
        self.assertEqual(result[1]["contract_id"], "pf-no")


    def test_parses_json_string_outcomes_before_normalizing(self):
        result = self.service._normalize_outcomes('["Yes", "No"]')

        self.assertEqual(
            result,
            [
                {"id": "0", "label": "Yes", "slug": "yes"},
                {"id": "1", "label": "No", "slug": "no"},
            ],
        )


    def test_maps_predict_fun_active_market_from_trading_status(self):
        mapped = self.service._map_predict_fun_market(
            {
                "id": 77,
                "title": "Spurs",
                "question": "Knicks vs. Spurs",
                "tradingStatus": "OPEN",
                "status": "RESOLVED",
                "categorySlug": "nba",
                "imageUrl": "https://example.test/image",
                "outcomes": [
                    {"onChainId": "yes-1", "name": "Yes"},
                    {"onChainId": "no-1", "name": "No"},
                ],
            }
        )

        self.assertEqual(mapped["status"], "active")
        self.assertTrue(mapped["tradable"])
        self.assertEqual(mapped["title"], "Knicks vs. Spurs")
        self.assertEqual(mapped["category"], "nba")
        self.assertEqual(mapped["outcomes_json"][0]["id"], "yes-1")
        self.assertEqual(mapped["outcomes_json"][1]["slug"], "no")


    def test_map_polymarket_market_keeps_explicit_outcome_token_ids(self):
        mapped = self.service._map_polymarket_market(
            {
                "id": 88,
                "title": "Trail Blazers vs Nuggets",
                "tradable": True,
                "clobTokenIds": '["clob-a", "clob-b"]',
                "outcomes": [
                    {"label": "Trail Blazers", "token_id": "explicit-a"},
                    {"label": "Nuggets", "token_id": "explicit-b"},
                ],
            }
        )

        self.assertEqual(mapped["outcomes_json"][0]["id"], "explicit-a")
        self.assertEqual(mapped["outcomes_json"][1]["id"], "explicit-b")
        self.assertEqual(mapped["outcomes_json"][0]["clob_token_id"], "clob-a")
        self.assertEqual(mapped["outcomes_json"][1]["clob_token_id"], "clob-b")


class IngestionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ingestion_module._source_last_sync_completed_at.clear()


    async def test_mark_missing_markets_closed_marks_absent_active_market_as_closed(self):
        missing_market = SimpleNamespace(
            platform="predict_fun",
            platform_market_id="9212",
            status="active",
            tradable=True,
            updated_at=None,
        )
        present_market = SimpleNamespace(
            platform="predict_fun",
            platform_market_id="9213",
            status="active",
            tradable=True,
            updated_at=None,
        )

        class FakeScalarResult:
            def __init__(self, items):
                self._items = items


            def scalars(self):
                return self


            def all(self):
                return list(self._items)


        class FakeDbSession:
            async def execute(self, stmt):
                return FakeScalarResult([missing_market, present_market])


        service = IngestionService(db_session=FakeDbSession())
        before = datetime.now(timezone.utc)

        await service._mark_missing_markets_closed("predict_fun", {"9213"})

        self.assertEqual(missing_market.status, "closed")
        self.assertFalse(missing_market.tradable)
        self.assertGreaterEqual(missing_market.updated_at, before)
        self.assertEqual(present_market.status, "active")
        self.assertTrue(present_market.tradable)


    async def test_sync_markets_skips_full_resync_within_interval(self):
        class FakeDbSession:
            def __init__(self):
                self.commit_calls = 0
                self.rollback_calls = 0


            async def commit(self):
                self.commit_calls += 1


            async def rollback(self):
                self.rollback_calls += 1


        service = IngestionService(db_session=FakeDbSession())
        service.polymarket.fetch_markets = AsyncMock(return_value=[])
        service.predict_fun.fetch_markets = AsyncMock(return_value=[])
        service._sync_source = AsyncMock(return_value=True)

        with patch.object(ingestion_module.settings, "MARKET_SYNC_INTERVAL_SECONDS", 300.0), patch.object(
            ingestion_module.settings,
            "MARKET_REFRESH_SECONDS",
            60,
        ):
            first = await service.sync_markets()
            second = await service.sync_markets()

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(service.polymarket.fetch_markets.await_count, 1)
        self.assertEqual(service.predict_fun.fetch_markets.await_count, 1)
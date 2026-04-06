from unittest.mock import patch
import unittest
from types import SimpleNamespace

from arbitrage_bot.api.internal import debug_matcher, diagnose_pair, get_pairs, get_runtime_metrics, status_check


class FakeScalars:
    def __init__(self, values):
        self._values = values


    def first(self):
        if isinstance(self._values, list):
            return self._values[0] if self._values else None
        return self._values


    def all(self):
        if isinstance(self._values, list):
            return self._values
        return [self._values]


class FakeResult:
    def __init__(self, row):
        self._row = row


    def one(self):
        return self._row


    def scalar_one(self):
        return self._row


    def scalars(self):
        return FakeScalars(self._row)


class FakeDb:
    def __init__(self, rows):
        self._rows = iter(rows)


    async def execute(self, _stmt):
        return FakeResult(next(self._rows))


class InternalApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_check_returns_compact_runtime_summary(self):
        db = FakeDb(
            [
                SimpleNamespace(total=100, active=42),
                SimpleNamespace(total=12, approved=5),
                3,
                1,
                2,
            ]
        )

        payload = await status_check(db=db)

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["service"], "arbitrage-alert-bot")
        self.assertEqual(payload["market_counts"]["total"], 100)
        self.assertEqual(payload["market_counts"]["active"], 42)
        self.assertEqual(payload["pair_counts"]["total"], 12)
        self.assertEqual(payload["pair_counts"]["approved"], 5)
        self.assertEqual(payload["opportunity_counts"]["total"], 3)
        self.assertEqual(payload["opportunity_counts"]["queued_fanout"], 1)
        self.assertEqual(payload["alert_counts"]["queued"], 2)
        self.assertNotIn("runtime_metrics", payload)


    def test_get_pairs_defaults_to_auto_approved_status(self):
        self.assertEqual(get_pairs.__defaults__[0], "auto_approved")


    async def test_get_runtime_metrics_returns_snapshot_without_reset(self):
        with patch(
            "arbitrage_bot.api.internal.snapshot_counters",
            return_value={"telegram.alert_sent": 7},
        ), patch(
            "arbitrage_bot.api.internal.snapshot_and_reset_counters",
        ) as snapshot_and_reset_mock:
            payload = await get_runtime_metrics()

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["metrics"]["telegram.alert_sent"], 7)
        self.assertFalse(payload["reset_applied"])
        snapshot_and_reset_mock.assert_not_called()


    async def test_get_runtime_metrics_can_reset_after_snapshot(self):
        with patch(
            "arbitrage_bot.api.internal.snapshot_and_reset_counters",
            return_value={"fanout.alert_created": 4},
        ) as snapshot_and_reset_mock, patch(
            "arbitrage_bot.api.internal.snapshot_counters",
        ) as snapshot_mock:
            payload = await get_runtime_metrics(reset=True)

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["metrics"]["fanout.alert_created"], 4)
        self.assertTrue(payload["reset_applied"])
        snapshot_and_reset_mock.assert_called_once()
        snapshot_mock.assert_not_called()


    async def test_debug_matcher_returns_not_found_for_unknown_market(self):
        db = FakeDb([[]])

        payload = await debug_matcher(market_id=999, db=db)

        self.assertEqual(payload, {"status": "not_found", "market_id": 999})


    async def test_debug_matcher_returns_ranked_candidates_with_reject_reason(self):
        source_market = SimpleNamespace(
            id=100,
            platform="polymarket",
            platform_market_id="poly-100",
            title="Grizzlies vs. Hornets",
        )
        candidate_b = SimpleNamespace(
            id=201,
            platform="predict_fun",
            platform_market_id="pf-201",
            title="Grizzlies vs. Hornets",
        )
        candidate_a = SimpleNamespace(
            id=200,
            platform="predict_fun",
            platform_market_id="pf-200",
            title="Grizzlies win conference",
        )
        db = FakeDb([source_market, [candidate_a, candidate_b]])
        signatures = {
            100: {"tokens": {"grizzlies", "hornets"}},
            200: {"tokens": {"grizzlies"}},
            201: {"tokens": {"grizzlies", "hornets"}},
        }
        decisions = {
            200: {
                "matched": False,
                "score": 0.41,
                "reason": {"reject_reason": "market_shape_mismatch"},
            },
            201: {
                "matched": True,
                "score": 0.97,
                "reason": {"reject_reason": None},
            },
        }


        class FakeMatcher:
            def build_market_signature(self, market):
                return signatures[market.id]


            def candidate_rank_score(self, source_signature, candidate_signature, shared_token_count):
                return float(shared_token_count)


            def explain_match(self, source_market, candidate_market, poly_signature=None, pf_signature=None):
                return decisions[candidate_market.id]

        with patch("arbitrage_bot.api.internal.MatcherService", return_value=FakeMatcher()):
            payload = await debug_matcher(market_id=100, limit=1, db=db)

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["source_market"]["id"], 100)
        self.assertEqual(len(payload["data"]), 1)
        self.assertEqual(payload["data"][0]["market_id"], 201)
        self.assertTrue(payload["data"][0]["matched"])
        self.assertIsNone(payload["data"][0]["reject_reason"])


    async def test_diagnose_pair_returns_not_found_for_unknown_pair(self):
        db = FakeDb([[]])

        payload = await diagnose_pair(pair_id=999, db=db)

        self.assertEqual(payload, {"status": "not_found", "pair_id": 999})


    async def test_diagnose_pair_reports_orderbook_reason(self):
        pair = SimpleNamespace(id=7, market_id_a=100, market_id_b=200)
        market_a = SimpleNamespace(id=100, raw_payload_json={})
        market_b = SimpleNamespace(id=200, raw_payload_json={})
        db = FakeDb([pair, [market_a, market_b]])

        fake_service = SimpleNamespace(
            diagnose_pair=patch,
            close=patch,
        )
        fake_service.diagnose_pair = unittest.mock.AsyncMock(
            return_value={
                "stage": "orderbook",
                "reason": "predict_fun_yes_asks_missing",
            }
        )
        fake_service.close = unittest.mock.AsyncMock()

        with patch("arbitrage_bot.api.internal.OrderbookService", return_value=fake_service):
            payload = await diagnose_pair(pair_id=7, db=db)

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["diagnosis"]["stage"], "orderbook")
        self.assertEqual(payload["diagnosis"]["reason"], "predict_fun_yes_asks_missing")


    async def test_diagnose_pair_reports_filter_reasons(self):
        pair = SimpleNamespace(id=7, market_id_a=100, market_id_b=200)
        market_a = SimpleNamespace(id=100, raw_payload_json={})
        market_b = SimpleNamespace(id=200, raw_payload_json={})
        db = FakeDb([pair, [market_a, market_b]])

        fake_service = SimpleNamespace()
        fake_service.diagnose_pair = unittest.mock.AsyncMock(
            return_value={
                "stage": "ready",
                "reason": None,
                "directions": {
                    "A_yes_B_no": {
                        "poly": [(0.4, 10)],
                        "pf": [(0.5, 10)],
                    }
                },
            }
        )
        fake_service.close = unittest.mock.AsyncMock()

        fake_calculator = SimpleNamespace(
            calculate_opportunities=lambda directions: [
                {
                    "direction": "A_yes_B_no",
                    "capital_required": 100.0,
                    "net_profit": 4.0,
                    "net_roi": 0.04,
                }
            ]
        )

        with patch("arbitrage_bot.api.internal.OrderbookService", return_value=fake_service), patch(
            "arbitrage_bot.api.internal.ArbitrageCalculator",
            return_value=fake_calculator,
        ), patch(
            "arbitrage_bot.api.internal._load_diagnostic_targets",
            new=unittest.mock.AsyncMock(
                return_value=[
                    {
                        "telegram_chat_id": "123",
                        "user_id": 1,
                        "preferences": {
                            "min_roi_percent": 5.0,
                            "min_capital_usd": None,
                            "max_capital_usd": None,
                            "min_profit_usd": None,
                            "max_days_to_close": None,
                        },
                    }
                ]
            ),
        ):
            payload = await diagnose_pair(pair_id=7, db=db)

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["diagnosis"]["stage"], "fanout")
        self.assertEqual(payload["diagnosis"]["reason"], "filtered_by_preferences")
        self.assertEqual(payload["diagnosis"]["targets"][0]["results"][0]["reason"], "min_roi")
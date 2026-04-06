from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import func
from sqlalchemy.future import select
from arbitrage_bot.core.config import settings
from arbitrage_bot.core.database import get_db
from arbitrage_bot.core.observability import snapshot_and_reset_counters
from arbitrage_bot.core.observability import snapshot_counters
from arbitrage_bot.models.orm import Alert
from arbitrage_bot.models.orm import ArbOpportunity
from arbitrage_bot.models.orm import Market
from arbitrage_bot.models.orm import MarketPair
from arbitrage_bot.services.calculator import ArbitrageCalculator
from arbitrage_bot.services.matcher import MatcherService
from arbitrage_bot.services.orderbook import OrderbookService
from arbitrage_bot.tg_bot.preferences import filter_reason_for_preferences
from arbitrage_bot.tg_bot.preferences import get_global_preferences
from arbitrage_bot.tg_bot.preferences import get_telegram_alert_targets

router = APIRouter()


def require_admin_token(x_admin_token=Header(default=None)):
    expected_token = settings.ADMIN_API_TOKEN
    if not expected_token or x_admin_token != expected_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
        )


@router.get("/health")
async def health_check():
    return {"status": "ok"}


@router.get("/status")
async def status_check(db=Depends(get_db)):
    markets_stmt = select(
        func.count(Market.id).label("total"),
        func.count().filter(Market.status == "active").label("active"),
    )
    pairs_stmt = select(
        func.count(MarketPair.id).label("total"),
        func.count().filter(
            MarketPair.status.in_(("approved", "auto_approved"))
        ).label("approved"),
    )
    opportunities_stmt = select(func.count(ArbOpportunity.id))
    queued_fanout_stmt = select(func.count(ArbOpportunity.id)).where(ArbOpportunity.fanout_status.in_(("queued", "retry")))
    alerts_stmt = select(func.count(Alert.id)).where(Alert.status == "queued")

    markets_row = (await db.execute(markets_stmt)).one()
    pairs_row = (await db.execute(pairs_stmt)).one()
    opportunities_total = (await db.execute(opportunities_stmt)).scalar_one()
    queued_fanout = (await db.execute(queued_fanout_stmt)).scalar_one()
    queued_alerts = (await db.execute(alerts_stmt)).scalar_one()

    return {
        "status": "ok",
        "service": "arbitrage-alert-bot",
        "market_counts": {
            "total": markets_row.total,
            "active": markets_row.active,
        },
        "pair_counts": {
            "total": pairs_row.total,
            "approved": pairs_row.approved,
        },
        "opportunity_counts": {
            "total": opportunities_total,
            "queued_fanout": queued_fanout,
        },
        "alert_counts": {
            "queued": queued_alerts,
        },
    }


@router.get("/admin/pairs")
async def get_pairs(
    status="auto_approved",
    db=Depends(get_db),
    _=Depends(require_admin_token),
):
    stmt = select(MarketPair).where(MarketPair.status == status)
    result = await db.execute(stmt)
    pairs = result.scalars().all()

    return {
        "data": [
            {
                "id": pair.id,
                "market_id_a": pair.market_id_a,
                "market_id_b": pair.market_id_b,
                "pair_hash": pair.pair_hash,
                "status": pair.status,
                "match_score": pair.match_score,
                "match_reason_json": pair.match_reason_json,
                "created_at": pair.created_at.isoformat() if pair.created_at else None,
            }
            for pair in pairs
        ]
    }


@router.get("/admin/runtime-metrics")
async def get_runtime_metrics(
    reset=False,
    _=Depends(require_admin_token),
):
    if reset:
        metrics = snapshot_and_reset_counters()
    else:
        metrics = snapshot_counters()

    return {
        "status": "ok",
        "metrics": metrics,
        "reset_applied": reset,
    }


@router.post("/admin/pairs/{pair_id}/approve")
async def approve_pair(pair_id, db=Depends(get_db), _=Depends(require_admin_token)):
    stmt = select(MarketPair).where(MarketPair.id == pair_id)
    result = await db.execute(stmt)
    pair = result.scalars().first()

    if not pair:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="pair not found")

    pair.status = "approved"
    await db.commit()
    return {"status": "success", "pair_id": pair_id}


async def _load_diagnostic_targets(db):
    targets = await get_telegram_alert_targets(db)
    if targets:
        return targets

    legacy_preferences = await get_global_preferences(db)
    return [
        {
            "user_id": None,
            "subscription_id": None,
            "telegram_chat_id": chat_id,
            "preferences": legacy_preferences,
        }
        for chat_id in settings.TELEGRAM_DEFAULT_CHAT_IDS
    ]


def _diagnose_filter_reason(target, calc_result, market_a, market_b):
    preferences = target.get("preferences") or {}
    if preferences.get("muted"):
        return "muted"

    opportunity = type("Opportunity", (), calc_result)()
    return filter_reason_for_preferences(
        opportunity,
        market_a,
        market_b,
        preferences,
    )


@router.get("/admin/pairs/{pair_id}/diagnose")
async def diagnose_pair(pair_id, db=Depends(get_db), _=Depends(require_admin_token)):
    pair_stmt = select(MarketPair).where(MarketPair.id == pair_id)
    pair_result = await db.execute(pair_stmt)
    pair = pair_result.scalars().first()
    if pair is None:
        return {"status": "not_found", "pair_id": pair_id}

    markets_stmt = select(Market).where(Market.id.in_([pair.market_id_a, pair.market_id_b]))
    markets_result = await db.execute(markets_stmt)
    markets = {market.id: market for market in markets_result.scalars().all()}
    market_a = markets.get(pair.market_id_a)
    market_b = markets.get(pair.market_id_b)

    orderbook_service = OrderbookService()
    calculator = ArbitrageCalculator()
    try:
        diagnosis = await orderbook_service.diagnose_pair(pair, db)
    finally:
        await orderbook_service.close()

    if diagnosis["stage"] != "ready":
        return {
            "status": "ok",
            "pair_id": pair_id,
            "diagnosis": diagnosis,
        }

    calc_results = calculator.calculate_opportunities(diagnosis["directions"])
    if not calc_results:
        return {
            "status": "ok",
            "pair_id": pair_id,
            "diagnosis": {
                "stage": "calculator",
                "reason": "no_profitable_directions",
                "directions": list(diagnosis["directions"].keys()),
            },
        }

    targets = await _load_diagnostic_targets(db)
    target_diagnostics = []
    eligible_target_count = 0

    for target in targets:
        target_reasons = []
        for calc_result in calc_results:
            reason = _diagnose_filter_reason(
                target,
                calc_result,
                market_a,
                market_b,
            )
            if reason is None:
                eligible_target_count += 1
            target_reasons.append(
                {
                    "direction": calc_result["direction"],
                    "reason": reason,
                }
            )

        target_diagnostics.append(
            {
                "telegram_chat_id": target.get("telegram_chat_id"),
                "user_id": target.get("user_id"),
                "results": target_reasons,
            }
        )

    diagnosis = {
        "stage": "fanout" if eligible_target_count == 0 else "ready",
        "reason": "filtered_by_preferences" if eligible_target_count == 0 else None,
        "opportunities": [
            {
                "direction": result["direction"],
                "capital_required": result["capital_required"],
                "net_profit": result["net_profit"],
                "net_roi": result["net_roi"],
            }
            for result in calc_results
        ],
        "targets": target_diagnostics,
    }

    return {
        "status": "ok",
        "pair_id": pair_id,
        "diagnosis": diagnosis,
    }


@router.get("/admin/matcher/debug")
async def debug_matcher(
    market_id,
    limit=10,
    db=Depends(get_db),
    _=Depends(require_admin_token),
):
    source_stmt = select(Market).where(Market.id == market_id)
    source_result = await db.execute(source_stmt)
    source_market = source_result.scalars().first()

    if source_market is None:
        return {"status": "not_found", "market_id": market_id}

    target_platform = "predict_fun" if source_market.platform == "polymarket" else "polymarket"
    candidate_stmt = select(Market).where(
        Market.platform == target_platform,
        Market.status == "active",
    )
    candidate_result = await db.execute(candidate_stmt)
    candidate_markets = candidate_result.scalars().all()

    matcher = MatcherService()
    source_signature = matcher.build_market_signature(source_market)
    debug_items = []

    for candidate_market in candidate_markets:
        candidate_signature = matcher.build_market_signature(candidate_market)
        shared_token_count = len(source_signature["tokens"].intersection(candidate_signature["tokens"]))
        rank_score = matcher.candidate_rank_score(
            source_signature,
            candidate_signature,
            shared_token_count,
        )

        if source_market.platform == "polymarket":
            decision = matcher.explain_match(
                source_market,
                candidate_market,
                poly_signature=source_signature,
                pf_signature=candidate_signature,
            )
        else:
            decision = matcher.explain_match(
                candidate_market,
                source_market,
                poly_signature=candidate_signature,
                pf_signature=source_signature,
            )

        debug_items.append(
            {
                "market_id": candidate_market.id,
                "platform": candidate_market.platform,
                "platform_market_id": candidate_market.platform_market_id,
                "title": candidate_market.title,
                "rank_score": round(rank_score, 4),
                "shared_token_count": shared_token_count,
                "matched": decision["matched"],
                "match_score": round(decision["score"], 4),
                "reject_reason": decision["reason"]["reject_reason"],
                "match_reason_json": decision["reason"],
            }
        )

    safe_limit = max(1, min(int(limit), 25))
    ranked_items = sorted(
        debug_items,
        key=lambda item: (
            item["rank_score"],
            item["match_score"],
            item["shared_token_count"],
            item["market_id"],
        ),
        reverse=True,
    )

    return {
        "status": "ok",
        "source_market": {
            "id": source_market.id,
            "platform": source_market.platform,
            "platform_market_id": source_market.platform_market_id,
            "title": source_market.title,
        },
        "data": ranked_items[:safe_limit],
    }
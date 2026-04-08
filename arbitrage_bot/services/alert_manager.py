import json

from sqlalchemy import select

from arbitrage_bot.core.config import settings
from arbitrage_bot.core.redis import get_redis
from arbitrage_bot.models.orm import ArbOpportunity


class AlertManager:
    def __init__(self, db_session):
        self.db = db_session
        self.dedupe_ttl = settings.ALERTS_DEDUPE_TTL_SECONDS
        self.delta_profit = settings.ALERTS_DELTA_PROFIT_THRESHOLD_USD
        self.delta_roi = settings.ALERTS_DELTA_ROI_THRESHOLD_PERCENT / 100.0


    async def process_opportunity(self, pair, calc_result, suppress_alert=False, allow_suppressed_promotion=True):
        direction = calc_result["direction"]
        suppressed_opportunity = await self._load_suppressed_opportunity(
            pair.id,
            direction,
        )

        if suppress_alert:
            opportunity = suppressed_opportunity
            if opportunity is None:
                opportunity = self._build_opportunity(
                    pair.id,
                    calc_result,
                    fanout_status="suppressed",
                )
                self.db.add(opportunity)
                await self.db.flush()
            else:
                self._apply_calc_result(opportunity, calc_result)
                opportunity.fanout_status = "suppressed"
                opportunity.fanout_processed_at = None
                opportunity.fanout_error_message = None

            await self._commit_or_rollback()
            self._set_delivery_action(opportunity, "suppressed")
            return opportunity

        redis = await get_redis()
        dedupe_key = f"alert-dedupe:{pair.pair_hash}:{direction}"

        last_alert_data = None
        if redis is not None:
            try:
                last_alert_data = await redis.get(dedupe_key)
            except Exception:
                last_alert_data = None

        if last_alert_data:
            last_state = json.loads(last_alert_data)
            profit_diff = calc_result["net_profit"] - last_state["net_profit"]
            roi_diff = calc_result["net_roi"] - last_state["net_roi"]

            # skip if change is insignificant in both dimensions
            if abs(profit_diff) < self.delta_profit and abs(roi_diff) < self.delta_roi:
                if suppressed_opportunity is not None:
                    self._apply_calc_result(suppressed_opportunity, calc_result)
                    suppressed_opportunity.fanout_status = "suppressed"
                    suppressed_opportunity.fanout_processed_at = None
                    suppressed_opportunity.fanout_error_message = None
                    await self._commit_or_rollback()
                    self._set_delivery_action(suppressed_opportunity, "deferred")
                return False

        opp = suppressed_opportunity
        if opp is not None and not allow_suppressed_promotion:
            self._apply_calc_result(opp, calc_result)
            opp.fanout_status = "suppressed"
            opp.fanout_processed_at = None
            opp.fanout_error_message = None
            await self._commit_or_rollback()
            self._set_delivery_action(opp, "deferred")
            return opp
        if opp is None:
            opp = self._build_opportunity(
                pair.id,
                calc_result,
                fanout_status="queued",
            )
            self.db.add(opp)
            await self.db.flush()
        else:
            self._apply_calc_result(opp, calc_result)
            opp.fanout_status = "queued"
            opp.fanout_processed_at = None
            opp.fanout_error_message = None

        state_to_save = {
            "net_profit": calc_result["net_profit"],
            "net_roi": calc_result["net_roi"],
            "shares": calc_result["shares"]
        }

        await self._commit_or_rollback()

        # write dedupe key after successful commit to prevent
        # skipping alerts when the transaction rolls back
        if redis is not None:
            try:
                await redis.setex(dedupe_key, self.dedupe_ttl, json.dumps(state_to_save))
            except Exception:
                pass
        if suppressed_opportunity is not None:
            self._set_delivery_action(opp, "promoted")
        else:
            self._set_delivery_action(opp, "queued")
        return opp


    async def _load_suppressed_opportunity(self, pair_id, direction):
        stmt = (
            select(ArbOpportunity)
            .where(
                ArbOpportunity.market_pair_id == pair_id,
                ArbOpportunity.direction == direction,
                ArbOpportunity.fanout_status == "suppressed",
            )
            .order_by(ArbOpportunity.id.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalars().first()


    def _build_opportunity(self, pair_id, calc_result, fanout_status):
        return ArbOpportunity(
            market_pair_id=pair_id,
            direction=calc_result["direction"],
            price_leg_1=calc_result["avg_price_leg_1"],
            price_leg_2=calc_result["avg_price_leg_2"],
            avg_price_leg_1=calc_result["avg_price_leg_1"],
            avg_price_leg_2=calc_result["avg_price_leg_2"],
            shares=calc_result["shares"],
            capital_required=calc_result["capital_required"],
            gross_profit=calc_result["gross_profit"],
            net_profit=calc_result["net_profit"],
            gross_roi=calc_result["gross_roi"],
            net_roi=calc_result["net_roi"],
            calculation_json=calc_result,
            fanout_status=fanout_status,
        )


    def _apply_calc_result(self, opportunity, calc_result):
        opportunity.price_leg_1 = calc_result["avg_price_leg_1"]
        opportunity.price_leg_2 = calc_result["avg_price_leg_2"]
        opportunity.avg_price_leg_1 = calc_result["avg_price_leg_1"]
        opportunity.avg_price_leg_2 = calc_result["avg_price_leg_2"]
        opportunity.shares = calc_result["shares"]
        opportunity.capital_required = calc_result["capital_required"]
        opportunity.gross_profit = calc_result["gross_profit"]
        opportunity.net_profit = calc_result["net_profit"]
        opportunity.gross_roi = calc_result["gross_roi"]
        opportunity.net_roi = calc_result["net_roi"]
        opportunity.calculation_json = calc_result


    def _set_delivery_action(self, opportunity, action):
        setattr(opportunity, "_delivery_action", action)


    async def _commit_or_rollback(self):
        try:
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

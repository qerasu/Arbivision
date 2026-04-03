import asyncio

from arbitrage_bot.core.config import settings
from arbitrage_bot.core.database import AsyncSessionLocal
from arbitrage_bot.core.logging import get_logger
from arbitrage_bot.services.fanout_manager import FanoutManager
from arbitrage_bot.services.system_notifier import format_error_details
from arbitrage_bot.services.system_notifier import send_system_error_notification

log = get_logger("fanout_worker")


async def run_fanout_loop():
    while True:
        try:
            async with AsyncSessionLocal() as session:
                manager = FanoutManager(session)
                await manager.process_pending_opportunities()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("fanout loop error", error=format_error_details(exc))
            await send_system_error_notification("fanout", "fanout loop", exc)

        await asyncio.sleep(settings.TELEGRAM_ALERTS_POLL_SECONDS)


if __name__ == "__main__":
    asyncio.run(run_fanout_loop())
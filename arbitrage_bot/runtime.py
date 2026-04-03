import asyncio
from contextlib import asynccontextmanager

from arbitrage_bot.fanout_worker import run_fanout_loop
from arbitrage_bot.services.system_notifier import close_shared_bot
from arbitrage_bot.tg_bot.bot import start_polling
from arbitrage_bot.worker import run_sync_loop


@asynccontextmanager
async def managed_runtime(*coroutines):
    tasks = [asyncio.create_task(coroutine) for coroutine in coroutines]

    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await close_shared_bot()


async def run_worker_runtime():
    await run_sync_loop()


async def run_fanout_runtime():
    await run_fanout_loop()


async def run_telegram_runtime():
    await start_polling()
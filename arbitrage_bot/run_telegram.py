import asyncio

from arbitrage_bot.runtime import run_telegram_runtime


def main():
    asyncio.run(run_telegram_runtime())


if __name__ == "__main__":
    main()
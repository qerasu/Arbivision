import asyncio

from arbitrage_bot.runtime import run_worker_runtime


def main():
    asyncio.run(run_worker_runtime())


if __name__ == "__main__":
    main()
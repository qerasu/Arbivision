import asyncio

from arbitrage_bot.runtime import run_fanout_runtime


def main():
    asyncio.run(run_fanout_runtime())


if __name__ == "__main__":
    main()
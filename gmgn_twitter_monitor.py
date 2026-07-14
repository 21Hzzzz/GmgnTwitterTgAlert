import asyncio

from gmgn_twitter_monitor.app import main

__all__ = ["main"]

if __name__ == "__main__":
    asyncio.run(main())

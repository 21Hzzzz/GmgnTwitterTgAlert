import asyncio

from gmgn_twitter_monitor.app import first_login, main

__all__ = ["main", "first_login"]

if __name__ == "__main__":
    asyncio.run(main())

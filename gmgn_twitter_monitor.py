import asyncio

from gmgn_twitter_monitor.app import main
from gmgn_twitter_monitor.config import AUTH_URL, FIRST_RUN_LOGIN

__all__ = ["main", "FIRST_RUN_LOGIN", "AUTH_URL"]

if __name__ == "__main__":
    asyncio.run(main())

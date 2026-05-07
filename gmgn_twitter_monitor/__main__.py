import asyncio
import os
import sys

from .app import first_login, main


def cli() -> None:
    if len(sys.argv) > 1:
        command = sys.argv[1].strip().lower()
        if command == "first-login":
            auth_url = os.getenv("GMGN_LOGIN_URL", "").strip()
            if not auth_url and len(sys.argv) > 2:
                auth_url = sys.argv[2].strip()
            if not auth_url:
                raise SystemExit("GMGN_LOGIN_URL is required for first-login")
            asyncio.run(first_login(auth_url))
            return
        if command in ("help", "-h", "--help"):
            print("Usage: python -m gmgn_twitter_monitor [first-login]")
            return
        raise SystemExit(f"Unknown command: {command}")

    asyncio.run(main())


if __name__ == "__main__":
    cli()

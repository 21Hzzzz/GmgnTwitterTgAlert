import asyncio
import os
from pathlib import Path

import aiohttp
from dotenv import load_dotenv


async def main() -> None:
    load_dotenv(Path(__file__).resolve().parent / ".env")
    proxy_server = os.getenv("PROXY_SERVER", "").strip()
    if not proxy_server:
        print("PROXY_SERVER is empty; proxy test skipped.")
        return

    connector = None
    request_kwargs = {}
    if proxy_server.startswith(("socks4://", "socks5://")):
        from aiohttp_socks import ProxyConnector

        connector = ProxyConnector.from_url(proxy_server, rdns=True)
    else:
        request_kwargs["proxy"] = proxy_server

    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.get(
            "https://www.cloudflare.com/cdn-cgi/trace",
            **request_kwargs,
        ) as resp:
            print("Status:", resp.status)
            print(await resp.text())


if __name__ == "__main__":
    asyncio.run(main())

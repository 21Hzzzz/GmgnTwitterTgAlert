import asyncio
import aiohttp
from aiohttp_socks import ProxyConnector

async def test():
    connector = ProxyConnector.from_url('socks5://127.0.0.1:40000', rdns=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.get("https://api.deepseek.com") as resp:
            print("Status:", resp.status)

asyncio.run(test())

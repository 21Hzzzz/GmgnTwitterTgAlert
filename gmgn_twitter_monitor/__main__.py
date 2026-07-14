import asyncio
import argparse
import os

from .app import login_only, main


def cli() -> None:
    parser = argparse.ArgumentParser(description="GMGN Telegram 群组推送服务")
    parser.add_argument(
        "--login",
        action="store_true",
        help="使用 GMGN_AUTH_URL 执行一次授权，保存登录态后退出",
    )
    args = parser.parse_args()

    if args.login:
        auth_url = os.getenv("GMGN_AUTH_URL", "")
        if not auth_url.strip():
            parser.error("--login 需要通过环境变量 GMGN_AUTH_URL 提供授权链接")
        asyncio.run(login_only(auth_url))
        return
    asyncio.run(main())

if __name__ == "__main__":
    cli()

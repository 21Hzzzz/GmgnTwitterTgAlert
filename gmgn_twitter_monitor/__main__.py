import asyncio
import os
import sys

from .app import first_login, main


def _prompt_google_verification_code() -> str:
    env_code = os.getenv("GMGN_LOGIN_VERIFICATION_CODE", "").strip()
    if env_code:
        return env_code
    if not sys.stdin.isatty():
        raise RuntimeError(
            "检测到谷歌身份验证弹窗，但当前不是交互式终端；"
            "请在终端运行 gta start，或通过 GMGN_LOGIN_VERIFICATION_CODE 提供 6 位验证码"
        )
    return input("检测到谷歌身份验证弹窗，请输入 6 位动态验证码：").strip()


def cli() -> None:
    if len(sys.argv) > 1:
        command = sys.argv[1].strip().lower()
        if command == "first-login":
            auth_url = os.getenv("GMGN_LOGIN_URL", "").strip()
            if not auth_url and len(sys.argv) > 2:
                auth_url = sys.argv[2].strip()
            if not auth_url:
                raise SystemExit("执行 first-login 需要通过 GMGN_LOGIN_URL 提供授权 URL")
            asyncio.run(first_login(auth_url, _prompt_google_verification_code))
            return
        if command in ("help", "-h", "--help"):
            print("用法：python -m gmgn_twitter_monitor [first-login]")
            return
        raise SystemExit(f"未知命令：{command}")

    asyncio.run(main())


if __name__ == "__main__":
    cli()

#!/usr/bin/env python3
"""安装到服务器上的 gta 中文服务控制命令。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

SERVICE_NAME = "gmgn-twitter-monitor.service"
DEFAULT_PROJECT_DIR = Path("/root/GmgnTwitterTgAlert")
GTA_BIN = Path("/usr/local/bin/gta")


def _detect_project_dir() -> Path:
    env_dir = os.environ.get("GTA_PROJECT_DIR", "").strip()
    if env_dir:
        return Path(env_dir)

    here = Path(__file__).resolve().parent
    if (here / "gmgn_twitter_monitor").is_dir():
        return here

    return DEFAULT_PROJECT_DIR


PROJECT_DIR = _detect_project_dir()
ENV_FILE = PROJECT_DIR / ".env"
ENV_EXAMPLE = PROJECT_DIR / ".env.example"
SERVICE_FILE = PROJECT_DIR / SERVICE_NAME
WARP_SCRIPT = PROJECT_DIR / "scripts" / "install_warp_proxy.sh"


def _uv_bin() -> str:
    return os.environ.get("UV_BIN") or shutil.which("uv") or "/root/.local/bin/uv"


def _is_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return callable(geteuid) and geteuid() == 0


def _sudo(cmd: list[str]) -> list[str]:
    if _is_root() or not cmd or cmd[0] == "sudo":
        return cmd
    return ["sudo", *cmd]


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    replace: bool = False,
) -> int:
    full_cmd = _sudo(cmd)
    if replace:
        if cwd:
            os.chdir(cwd)
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        os.execvpe(full_cmd[0], full_cmd, merged_env)
        return 0

    return subprocess.run(full_cmd, cwd=cwd, env=env, check=False).returncode


def _capture(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def _require_project() -> bool:
    if not PROJECT_DIR.exists():
        print(f"未找到项目目录：{PROJECT_DIR}")
        print("请先运行一键部署脚本。")
        return False
    return True


def _require_env() -> bool:
    if ENV_FILE.exists():
        return True
    print(f"未找到配置文件：{ENV_FILE}")
    if ENV_EXAMPLE.exists():
        print(f"可先复制模板：cp {ENV_EXAMPLE} {ENV_FILE}")
    print("请手动编辑 .env 后再启动服务。")
    return False


def _service_is_active() -> bool:
    result = _capture(["systemctl", "is-active", "--quiet", SERVICE_NAME])
    return result.returncode == 0


def _ask_first_login() -> bool:
    if not sys.stdin.isatty():
        print("检测到非交互式启动，跳过首次登录询问。")
        return False

    answer = input("启动前是否先执行首次 GMGN 授权登录？[y/N]: ").strip().lower()
    return answer in {"y", "yes"}


def _run_first_login() -> bool:
    auth_url = input("请粘贴 GMGN 授权 URL：").strip()
    if not auth_url:
        print("授权 URL 为空，已取消启动。")
        return False

    env = os.environ.copy()
    env["GMGN_LOGIN_URL"] = auth_url
    rc = _run(
        [_uv_bin(), "run", "python", "-m", "gmgn_twitter_monitor", "first-login"],
        cwd=PROJECT_DIR,
        env=env,
    )
    if rc != 0:
        print(f"首次登录失败，退出码：{rc}。服务未启动。")
        return False
    return True


def _install_service_and_gta() -> int:
    rc = _run(["install", "-m", "0644", str(SERVICE_FILE), f"/etc/systemd/system/{SERVICE_NAME}"])
    if rc != 0:
        return rc
    rc = _run(["systemctl", "daemon-reload"])
    if rc != 0:
        return rc
    rc = _run(["chmod", "0755", str(PROJECT_DIR / "ctl.py")])
    if rc != 0:
        return rc
    return _run(["ln", "-sfn", str(PROJECT_DIR / "ctl.py"), str(GTA_BIN)])


def _refresh_dependencies() -> int:
    uv = _uv_bin()
    commands = [
        [uv, "venv"],
        [uv, "pip", "install", "-r", "requirements.txt"],
        [uv, "run", "playwright", "install", "chromium"],
        [uv, "run", "playwright", "install-deps", "chromium"],
    ]
    for command in commands:
        rc = _run(command, cwd=PROJECT_DIR)
        if rc != 0:
            return rc
    return 0


def do_start() -> int:
    if not _require_project() or not _require_env():
        return 1
    print("准备启动服务...")
    if _service_is_active():
        print(f"{SERVICE_NAME} 已在运行。")
        return _run(["systemctl", "status", SERVICE_NAME, "--no-pager", "-l"])

    if _ask_first_login() and not _run_first_login():
        return 1

    rc = _run(["systemctl", "start", SERVICE_NAME])
    if rc == 0:
        print(f"{SERVICE_NAME} 已启动。")
        _run(["systemctl", "status", SERVICE_NAME, "--no-pager", "-l"])
    return rc


def do_stop() -> int:
    print("正在停止服务...")
    rc = _run(["systemctl", "stop", SERVICE_NAME])
    if rc == 0:
        print(f"{SERVICE_NAME} 已停止。")
    return rc


def do_restart() -> int:
    if not _require_project() or not _require_env():
        return 1
    print("正在重启服务...")
    rc = _run(["systemctl", "restart", SERVICE_NAME])
    if rc == 0:
        print(f"{SERVICE_NAME} 已重启，并已重新读取 .env。")
        _run(["systemctl", "status", SERVICE_NAME, "--no-pager", "-l"])
    return rc


def do_status() -> int:
    status_rc = _run(["systemctl", "status", SERVICE_NAME, "--no-pager", "-l"])
    print("\n正在进入实时日志，按 Ctrl+C 退出。\n")
    _run(["journalctl", "-u", SERVICE_NAME, "-f", "--no-pager", "-o", "cat"], replace=True)
    return status_rc


def do_log() -> int:
    print("正在进入实时日志，按 Ctrl+C 退出。\n")
    _run(["journalctl", "-u", SERVICE_NAME, "-f", "--no-pager", "-o", "cat"], replace=True)
    return 0


def do_update() -> int:
    if not _require_project():
        return 1
    print("开始更新项目代码和依赖...")
    if not (PROJECT_DIR / ".git").is_dir():
        print(f"{PROJECT_DIR} 不是 git 仓库，无法安全更新。")
        return 1

    dirty = _capture(["git", "status", "--porcelain", "--untracked-files=no"], cwd=PROJECT_DIR)
    if dirty.returncode != 0:
        print(dirty.stderr.strip() or "git status 执行失败")
        return dirty.returncode
    if dirty.stdout.strip():
        print("检测到已跟踪文件存在本地改动，已停止更新。")
        print("请手动提交、丢弃或暂存这些改动，然后重新运行 `gta update`。")
        print(dirty.stdout.strip())
        return 1

    rc = _run(["git", "pull", "--ff-only"], cwd=PROJECT_DIR)
    if rc != 0:
        return rc
    rc = _refresh_dependencies()
    if rc != 0:
        return rc
    rc = _install_service_and_gta()
    if rc == 0:
        print("更新完成。服务未自动重启；确认无误后请运行 `gta restart`。")
    return rc


def do_warp() -> int:
    if not WARP_SCRIPT.exists():
        print(f"未找到 WARP 安装脚本：{WARP_SCRIPT}")
        return 1
    print("开始安装可选 WARP 本地代理...")
    return _run(["bash", str(WARP_SCRIPT)])


def do_enable() -> int:
    return _run(["systemctl", "enable", SERVICE_NAME])


def do_disable() -> int:
    return _run(["systemctl", "disable", SERVICE_NAME])


COMMANDS = {
    "start": do_start,
    "stop": do_stop,
    "restart": do_restart,
    "status": do_status,
    "log": do_log,
    "logs": do_log,
    "update": do_update,
    "warp": do_warp,
    "install-warp": do_warp,
    "enable": do_enable,
    "disable": do_disable,
}


def print_help() -> None:
    print(
        "用法：gta <命令>\n\n"
        "可用命令：\n"
        "  start         启动服务；可选择先执行首次 GMGN 授权登录\n"
        "  stop          停止服务\n"
        "  restart       重启服务并重新读取 .env\n"
        "  status        查看服务状态，然后进入实时日志\n"
        "  log|logs      进入实时日志\n"
        "  update        拉取最新代码并刷新依赖；不会自动重启\n"
        "  warp          安装可选的 Cloudflare WARP 本地代理\n"
        "  enable        设置开机自启\n"
        "  disable       取消开机自启\n"
    )


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in {"help", "-h", "--help"}:
        print_help()
        return 0

    command = sys.argv[1].strip().lower()
    handler = COMMANDS.get(command)
    if not handler:
        print(f"未知命令：{command}\n")
        print_help()
        return 1
    return handler()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Command-line controller installed as `gta` on the server."""

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
        print(f"Project directory not found: {PROJECT_DIR}")
        print("Run the one-line installer first.")
        return False
    return True


def _require_env() -> bool:
    if ENV_FILE.exists():
        return True
    print(f"Missing config file: {ENV_FILE}")
    if ENV_EXAMPLE.exists():
        print(f"Create it with: cp {ENV_EXAMPLE} {ENV_FILE}")
    print("Edit .env manually before starting the service.")
    return False


def _service_is_active() -> bool:
    result = _capture(["systemctl", "is-active", "--quiet", SERVICE_NAME])
    return result.returncode == 0


def _ask_first_login() -> bool:
    if not sys.stdin.isatty():
        print("Non-interactive start detected; skipping first-login prompt.")
        return False

    answer = input("Run first-login authorization before starting? [y/N]: ").strip().lower()
    return answer in {"y", "yes"}


def _run_first_login() -> bool:
    auth_url = input("Paste GMGN authorization URL: ").strip()
    if not auth_url:
        print("Authorization URL is empty; aborting start.")
        return False

    env = os.environ.copy()
    env["GMGN_LOGIN_URL"] = auth_url
    rc = _run(
        [_uv_bin(), "run", "python", "-m", "gmgn_twitter_monitor", "first-login"],
        cwd=PROJECT_DIR,
        env=env,
    )
    if rc != 0:
        print(f"first-login failed with exit code {rc}; service was not started.")
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
    if _service_is_active():
        print(f"{SERVICE_NAME} is already running.")
        return _run(["systemctl", "status", SERVICE_NAME, "--no-pager", "-l"])

    if _ask_first_login() and not _run_first_login():
        return 1

    rc = _run(["systemctl", "start", SERVICE_NAME])
    if rc == 0:
        print(f"{SERVICE_NAME} started.")
        _run(["systemctl", "status", SERVICE_NAME, "--no-pager", "-l"])
    return rc


def do_stop() -> int:
    return _run(["systemctl", "stop", SERVICE_NAME])


def do_restart() -> int:
    if not _require_project() or not _require_env():
        return 1
    rc = _run(["systemctl", "restart", SERVICE_NAME])
    if rc == 0:
        print(f"{SERVICE_NAME} restarted and reloaded .env.")
        _run(["systemctl", "status", SERVICE_NAME, "--no-pager", "-l"])
    return rc


def do_status() -> int:
    status_rc = _run(["systemctl", "status", SERVICE_NAME, "--no-pager", "-l"])
    print("\nFollowing live logs. Press Ctrl+C to exit.\n")
    _run(["journalctl", "-u", SERVICE_NAME, "-f", "--no-pager", "-o", "cat"], replace=True)
    return status_rc


def do_log() -> int:
    print("Following live logs. Press Ctrl+C to exit.\n")
    _run(["journalctl", "-u", SERVICE_NAME, "-f", "--no-pager", "-o", "cat"], replace=True)
    return 0


def do_update() -> int:
    if not _require_project():
        return 1
    if not (PROJECT_DIR / ".git").is_dir():
        print(f"{PROJECT_DIR} is not a git repository; cannot update safely.")
        return 1

    dirty = _capture(["git", "status", "--porcelain", "--untracked-files=no"], cwd=PROJECT_DIR)
    if dirty.returncode != 0:
        print(dirty.stderr.strip() or "git status failed")
        return dirty.returncode
    if dirty.stdout.strip():
        print("Tracked local changes detected; update stopped.")
        print("Commit, discard, or stash these changes manually, then run `gta update` again.")
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
        print("Update complete. Service was not restarted; run `gta restart` when ready.")
    return rc


def do_warp() -> int:
    if not WARP_SCRIPT.exists():
        print(f"WARP installer not found: {WARP_SCRIPT}")
        return 1
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
        "Usage: gta <command>\n\n"
        "Commands:\n"
        "  start         Start service; optionally run first-login first\n"
        "  stop          Stop service\n"
        "  restart       Restart service and reload .env\n"
        "  status        Show status, then follow live logs\n"
        "  log|logs      Follow live logs\n"
        "  update        Pull latest code and refresh dependencies; no restart\n"
        "  warp          Install optional Cloudflare WARP local proxy\n"
        "  enable        Enable service on boot\n"
        "  disable       Disable service on boot\n"
    )


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in {"help", "-h", "--help"}:
        print_help()
        return 0

    command = sys.argv[1].strip().lower()
    handler = COMMANDS.get(command)
    if not handler:
        print(f"Unknown command: {command}\n")
        print_help()
        return 1
    return handler()


if __name__ == "__main__":
    raise SystemExit(main())

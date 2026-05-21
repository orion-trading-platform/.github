#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

'''
Orion Trading Platform — Local Development Orchestrator

Put this script in a parent directory and run:
    python dev.py

It will clone any missing repos, initialize .env files (asking before
overwriting existing ones), install dependencies, and launch all four
services in one terminal with color-coded, prefixed output.

Use Ctrl+C to stop everything. Run 'python dev.py -f' to skip setup
and just launch.

Prerequisites: Git, Python 3.13+, uv, Node.js 20+
'''

# ─── Environment templates ────────────────────────────────────────────
# Replace placeholder values with your own keys before running.
# See each repo's .env.example for details on where to obtain them.

_DB_ENV = """\
USE_LOCAL_DB=true
BIGTABLE_PROJECT_ID=your-gcp-project-id
BIGTABLE_INSTANCE_ID=your-bigtable-instance
USE_REDIS=false
ACCESS_SECRET_KEY=CHANGE_ME_run_python3_-c_"import secrets; print(secrets.token_hex(32))"
REFRESH_SECRET_KEY=CHANGE_ME_run_python3_-c_"import secrets; print(secrets.token_hex(32))"
SERVICE_AUTH_KEY=internal-service-auth-key
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=30
RESET_TOKEN_EXPIRE_MINUTES=30
RESEND_API_KEY=re_YOUR_RESEND_API_KEY
#FROM_EMAIL=noreply@yourdomain.com
RESET_PASSWORD_URL=http://localhost:5173/#/reset-password
RECAPTCHA_SECRET_KEY=YOUR_RECAPTCHA_SECRET_KEY
"""

_MKT_ENV = """\
API_KEY=YOUR_ALPACA_API_KEY
SECRET_API_KEY=YOUR_ALPACA_SECRET_KEY
DB_API_URL=http://localhost:8000
SERVICE_AUTH_KEY=internal-service-auth-key
HTTP_TIMEOUT=5
"""

_ENG_ENV = """\
DB_API_URL=http://localhost:8000
MARKET_DATA_URL=http://localhost:8001
SERVICE_AUTH_KEY=internal-service-auth-key
HTTP_TIMEOUT=5
"""

_WEB_ENV = """\
VITE_BACKEND_URL=http://localhost:8000
VITE_MARKET_DATA_URL=http://localhost:8001
VITE_TRADING_ENGINE_URL=http://localhost:8002
VITE_GOOGLE_CLIENT_ID=YOUR_GOOGLE_OAUTH_CLIENT_ID
VITE_PORT=5173
VITE_RECAPTCHA_SITE_KEY=YOUR_RECAPTCHA_SITE_KEY
"""


ROOT = Path(__file__).parent

RESET  = "\033[0m"
BOLD   = "\033[1m"
RED    = "\033[31m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
BLUE   = "\033[34m"
CYAN   = "\033[36m"

WIN = sys.platform == "win32"


def _enable_ansi() -> None:
    if not WIN:
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        k32.GetConsoleMode(h, ctypes.byref(mode))
        k32.SetConsoleMode(h, mode.value | 0x0004)
    except Exception:
        pass


def _info(msg: str) -> None:
    print(f"{BOLD}[dev]{RESET} {msg}", flush=True)


def _warn(msg: str) -> None:
    print(f"{BOLD}{YELLOW}[dev]{RESET} {msg}", flush=True)


def _error(msg: str) -> None:
    print(f"{BOLD}{RED}[dev]{RESET} {msg}", flush=True)


def _getchar() -> str:
    if WIN:
        import msvcrt
        ch = msvcrt.getwch().lower()
    else:
        try:
            import tty
            import termios
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                ch = sys.stdin.read(1).lower()
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            ch = (input().strip()[:1] or "n").lower()
    if ch == "\x03":
        raise KeyboardInterrupt
    if ch in ("\r", "\n"):
        return "n"
    return ch


def _ask(prompt: str) -> str:
    print(prompt, end="", flush=True)
    ch = _getchar()
    print(ch, flush=True)
    return ch


SERVICES: list[dict] = [
    {
        "label": "DB ",
        "color": BLUE,
        "repo": "database-system",
        "repo_url": "https://github.com/orion-trading-platform/database-system.git",
        "env_path": ROOT / "database-system" / "api" / ".env",
        "default_env": _DB_ENV,
        "install_cwd": ROOT / "database-system" / "api",
        "install_cmd": ["uv", "sync"],
        "run_cwd": ROOT / "database-system" / "api",
        "run_cmd": ["uv", "run", "uvicorn", "main:app", "--reload", "--port", "8000"],
        "port": 8000,
    },
    {
        "label": "MKT",
        "color": GREEN,
        "repo": "market-data-api",
        "repo_url": "https://github.com/orion-trading-platform/market-data-api.git",
        "env_path": ROOT / "market-data-api" / ".env",
        "default_env": _MKT_ENV,
        "install_cwd": ROOT / "market-data-api",
        "install_cmd": ["uv", "sync"],
        "run_cwd": ROOT / "market-data-api",
        "run_cmd": ["uv", "run", "uvicorn", "alpaca_proxy.main:app", "--reload", "--port", "8001"],
        "port": 8001,
    },
    {
        "label": "ENG",
        "color": YELLOW,
        "repo": "trading-engine",
        "repo_url": "https://github.com/orion-trading-platform/trading-engine.git",
        "env_path": ROOT / "trading-engine" / ".env",
        "default_env": _ENG_ENV,
        "install_cwd": ROOT / "trading-engine",
        "install_cmd": ["uv", "sync"],
        "run_cwd": ROOT / "trading-engine",
        "run_cmd": ["uv", "run", "uvicorn", "main:app", "--reload", "--port", "8002"],
        "port": 8002,
    },
    {
        "label": "WEB",
        "color": CYAN,
        "repo": "frontend",
        "repo_url": "https://github.com/orion-trading-platform/frontend.git",
        "env_path": ROOT / "frontend" / "apps" / "web_client" / ".env",
        "default_env": _WEB_ENV,
        "install_cwd": ROOT / "frontend",
        "install_cmd": ["npm", "install", "--loglevel", "verbose"],
        "run_cwd": ROOT / "frontend" / "apps" / "web_client",
        "run_cmd": ["npm", "run", "dev"],
        "port": 5173,
    },
]


_INSTALL_HINTS: dict[str, str] = {
    "git": (
        "winget install Git.Git" if WIN else
        "brew install git" if sys.platform == "darwin" else
        "sudo apt-get install git"
    ),
    "uv": (
        "powershell -ExecutionPolicy ByPass -c \"irm https://astral.sh/uv/install.ps1 | iex\"" if WIN else
        "curl -LsSf https://astral.sh/uv/install.sh | sh"
    ),
    "node": (
        "winget install OpenJS.NodeJS.LTS" if WIN else
        "brew install node" if sys.platform == "darwin" else
        "curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash  (restart terminal, then: nvm install --lts)"
    ),
    "npm": (
        "winget install OpenJS.NodeJS.LTS  (npm is included with Node.js)" if WIN else
        "brew install node  (npm is included)" if sys.platform == "darwin" else
        "npm comes with Node.js — see node hint above"
    ),
}

def _check_prerequisites() -> bool:
    missing = [t for t in ("git", "uv", "node", "npm") if not shutil.which(t)]
    if missing:
        _error(f"Missing required tools: {', '.join(missing)}")
        for tool in missing:
            _error(f"  {tool}: {_INSTALL_HINTS[tool]}")
        return False
    return True


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _show_env_diff(existing: str, default: str, label: str) -> None:
    diff = list(difflib.unified_diff(
        existing.splitlines(),
        default.splitlines(),
        fromfile=f"Your existing {label} .env",
        tofile=f"This script's default {label} .env",
        lineterm="",
    ))
    for line in diff:
        if line.startswith("+++") or line.startswith("---"):
            print(f"{BOLD}{line}{RESET}")
        elif line.startswith("+"):
            print(f"{GREEN}{line}{RESET}")
        elif line.startswith("-"):
            print(f"{RED}{line}{RESET}")
        elif line.startswith("@@"):
            print(f"{YELLOW}{line}{RESET}")
        else:
            print(line)


def _parse_env(text: str) -> dict[str, str]:
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, val = line.partition("=")
        result[key.strip()] = val.strip()
    return result


def _clone_repos(services: list[dict]) -> None:
    for svc in services:
        repo_path = ROOT / svc["repo"]
        if not repo_path.exists():
            _info(f"Cloning {svc['repo']}...")
            subprocess.run(["git", "clone", svc["repo_url"]], cwd=ROOT, check=True)
        else:
            tag = f"{svc['color']}[{svc['label']}]{RESET}"
            result = subprocess.run(
                ["git", "status", "--porcelain"], cwd=repo_path,
                capture_output=True, text=True,
            )
            if result.stdout.strip():
                _warn(f"{tag} has uncommitted changes, skipping pull")
            else:
                _info(f"{tag} pulling latest changes...")
                subprocess.run(["git", "pull", "--ff-only"], cwd=repo_path, check=False)


def _setup_envs(services: list[dict]) -> None:
    for svc in services:
        env_path: Path = svc["env_path"]
        tag = f"{svc['color']}[{svc['label']}]{RESET}"
        if not env_path.exists():
            env_path.write_text(svc["default_env"], encoding="utf-8")
            _info(f"{tag} created .env with defaults")
        elif _parse_env(env_path.read_text(encoding="utf-8")) != _parse_env(svc["default_env"]):
            _show_env_diff(
                env_path.read_text(encoding="utf-8"),
                svc["default_env"],
                svc["label"].strip(),
            )
            if _ask(f"{BOLD}[dev]{RESET} {tag} .env has custom values. Overwrite with defaults? [y/N, Enter=N] ") == "y":
                env_path.write_text(svc["default_env"], encoding="utf-8")
                _info(f"{tag} .env overwritten")
            else:
                _info(f"{tag} keeping existing .env")


def _deps_exist(svc: dict) -> bool:
    indicator = "node_modules" if svc["install_cmd"][0] == "npm" else ".venv"
    return (svc["install_cwd"] / indicator).exists()


def _install_deps(services: list[dict]) -> None:
    if all(_deps_exist(svc) for svc in services):
        if _ask(f"{BOLD}[dev]{RESET} Reinstall all dependencies? [y/N, Enter=N] ") != "y":
            _info("Skipping dependency install")
            return
    for svc in services:
        tag = f"{svc['color']}[{svc['label']}]{RESET}"
        _info(f"{tag} installing dependencies...")
        cmd = svc["install_cmd"]
        try:
            subprocess.run(
                " ".join(cmd) if WIN else cmd,
                cwd=svc["install_cwd"],
                shell=WIN,
                check=True,
            )
        except subprocess.CalledProcessError:
            indicator = "node_modules" if cmd[0] == "npm" else ".venv"
            _error(f"{tag} install failed — try deleting {svc['install_cwd'] / indicator} and running again")


def _stream(stream, prefix: str) -> None:
    try:
        for line in stream:
            print(prefix + line.rstrip(), flush=True)
    except (ValueError, OSError):
        pass


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if WIN:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
        )
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass


def _launch(services: list[dict]) -> None:
    for svc in services:
        if _port_in_use(svc["port"]):
            _warn(f"Port {svc['port']} already in use — {svc['label'].strip()} may fail to start")

    procs: list[subprocess.Popen] = []
    for svc in services:
        tag = f"{svc['color']}[{svc['label']}]{RESET}"
        cmd = svc["run_cmd"]
        kwargs: dict = dict(
            cwd=svc["run_cwd"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if WIN:
            proc = subprocess.Popen(
                " ".join(cmd),
                shell=True,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                **kwargs,
            )
        else:
            proc = subprocess.Popen(cmd, start_new_session=True, **kwargs)

        prefix = f"{svc['color']}[{svc['label']}]{RESET} "
        threading.Thread(target=_stream, args=(proc.stdout, prefix), daemon=True).start()
        procs.append(proc)
        _info(f"{tag} started on port {svc['port']}")

    _info(f"{BOLD}All servers running. Ctrl+C to stop.{RESET}")

    exited: set[int] = set()
    try:
        while True:
            for i, proc in enumerate(procs):
                if i not in exited and proc.poll() is not None:
                    exited.add(i)
                    _warn(f"[{services[i]['label'].strip()}] exited with code {proc.returncode}")
            if len(exited) == len(procs):
                _warn("All servers have stopped.")
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        print()
        _info("Shutting down...")
    finally:
        for proc in procs:
            _terminate(proc)
        for proc in procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def main() -> None:
    _enable_ansi()

    parser = argparse.ArgumentParser(description="Start all dev services")
    parser.add_argument("-f", action="store_true", help="skip all changes and launch")
    args = parser.parse_args()

    if not _check_prerequisites():
        sys.exit(1)

    if not args.f:
        try:
            _clone_repos(SERVICES)
            _setup_envs(SERVICES)
            _install_deps(SERVICES)
        except KeyboardInterrupt:
            print()
            _info("Aborted.")
            sys.exit(0)

    _launch(SERVICES)


if __name__ == "__main__":
    main()

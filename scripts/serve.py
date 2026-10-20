"""【M7】一键启动脚本（跨平台）。

一条命令完成：应用数据库迁移 → 启动后端 uvicorn → 启动前端 Streamlit。
日志落 `logs/backend.log` 与 `logs/frontend.log`，PID 落 `logs/*.pid`。

用法：
    python scripts/serve.py          # 前台运行，Ctrl+C 一起停（推荐日常开发）

停止：前台模式下直接 Ctrl+C；若终端被强关留下孤儿进程，用 `stop.sh` / `stop.bat`
按 PID 文件清理。需要「启动后脱手」请用 Docker（docker compose up -d）。

端口/地址可用环境变量覆盖：
    BACKEND_PORT（默认 8000）、FRONTEND_PORT（默认 8501）
    BACKEND_HOST（默认 0.0.0.0，改 127.0.0.1 只本机可访问）、FRONTEND_ADDRESS（同）

为什么用 Python 而不是纯 shell：Windows / Linux / macOS 一套逻辑通吃，且能复用
`sys.executable` 精确指向当前 venv 的 Python，不必在脚本里猜 `.venv/bin` 还是 `.venv/Scripts`。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "logs"

BACKEND_HOST = os.environ.get("BACKEND_HOST", "0.0.0.0")
BACKEND_PORT = os.environ.get("BACKEND_PORT", "8000")
FRONTEND_ADDRESS = os.environ.get("FRONTEND_ADDRESS", "0.0.0.0")
FRONTEND_PORT = os.environ.get("FRONTEND_PORT", "8501")


def _pid_path(name: str) -> Path:
    return LOG_DIR / f"{name}.pid"


def _write_pid(name: str, pid: int) -> None:
    _pid_path(name).write_text(str(pid), encoding="ascii")


def _clear_pid(name: str) -> None:
    """删除 PID 文件（尽力而为，失败不阻塞）。

    WorkBuddy 的托管 Python 带「安全删除」钩子，会把 `.unlink()` 拦截成「移入回收站」，
    而 PID 文件可能已被 stop.py 先行删掉，导致钩子在「文件已不存在」时抛 OSError。
    这纯属收尾清理，绝不能因为它让整个停止流程报错——失败就留下，下次启动会覆盖。
    """
    try:
        _pid_path(name).unlink(missing_ok=True)
    except OSError:
        pass


def _terminate(proc: subprocess.Popen) -> None:
    """终止子进程。

    ⚠️ Windows 下的关键差异：本机 `.venv/Scripts/python.exe` 是「重定向器」，它会把
    真正的 Python 作为**子进程**再拉起，所以 `proc.pid` 拿到的是重定向器的 PID，而不是
    真正跑 uvicorn/streamlit 的那个进程。`proc.terminate()` 只杀重定向器，会留下孤儿
    Python 进程继续占着端口。所以 Windows 必须用 `taskkill /F /T` 连进程树一起杀；
    POSIX 没有这层重定向，`proc.terminate()` 就够。
    """
    if os.name == "nt":
        subprocess.call(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        proc.terminate()


def run_migration() -> bool:
    """应用数据库迁移。失败返回 False（此时不该启动服务，库里可能没表）。"""
    print("[1/3] 应用数据库迁移（alembic upgrade head）…")
    code = subprocess.call(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
    )
    return code == 0


def spawn(name: str, args: list[str]) -> subprocess.Popen:
    """起一个子进程，stdout/stderr 都追加到 logs/{name}.log。"""
    LOG_DIR.mkdir(exist_ok=True)
    log = open(LOG_DIR / f"{name}.log", "a", encoding="utf-8")
    proc = subprocess.Popen(
        args,
        cwd=PROJECT_ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    _write_pid(name, proc.pid)
    return proc


def main() -> int:
    if not run_migration():
        print("迁移失败，终止启动（检查 .env 与 alembic 配置）", file=sys.stderr)
        return 1

    print(f"[2/3] 启动后端 uvicorn（http://{BACKEND_HOST}:{BACKEND_PORT}）…")
    backend = spawn(
        "backend",
        [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", BACKEND_HOST, "--port", BACKEND_PORT,
        ],
    )

    print(f"[3/3] 启动前端 Streamlit（http://{FRONTEND_ADDRESS}:{FRONTEND_PORT}）…")
    frontend = spawn(
        "frontend",
        [
            sys.executable, "-m", "streamlit", "run", "frontend/app.py",
            "--server.address", FRONTEND_ADDRESS,
            "--server.port", FRONTEND_PORT,
            "--server.headless", "true",
        ],
    )

    print("\n✅ 已启动：")
    print(f"   前端界面  http://127.0.0.1:{FRONTEND_PORT}")
    print(f"   API 文档  http://127.0.0.1:{BACKEND_PORT}/docs")
    print(f"   日志目录  {LOG_DIR}")
    print("\n按 Ctrl+C 停止前后端。")

    procs = [backend, frontend]
    try:
        # 前台守候：任何子进程意外退出都视为整体结束（比如端口被占、依赖缺失）
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n收到停止信号，正在关闭…")
    finally:
        for _, proc in (("backend", backend), ("frontend", frontend)):
            if proc.poll() is None:
                _terminate(proc)
        for name, proc in (("backend", backend), ("frontend", frontend)):
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
            _clear_pid(name)
        print("已停止前后端。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

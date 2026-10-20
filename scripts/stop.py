"""【M7】停止脚本（跨平台）。

按 `logs/*.pid` 里的 PID 停止前后端进程。用于两种情况：
1. 前台 `serve.py` 被强关（终端直接关掉），留下孤儿进程；
2. 想干净地清一遍残留。

正常前台运行时请优先用 Ctrl+C（serve.py 会自己收尾），本脚本是「兜底清理」。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "logs"


def _stop_by_pid(pid: int) -> bool:
    """按 PID 停进程。Windows 用 taskkill（连带进程树），POSIX 用 SIGTERM。"""
    if os.name == "nt":
        code = subprocess.call(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return code == 0
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except ProcessLookupError:
        return False


def _remove_pid_file(pid_path: Path) -> None:
    """删除 PID 文件（尽力而为）。托管 Python 的「安全删除」钩子可能把它拦成移入回收站
    并抛错，这里吞掉——PID 文件是纯簿记，残留无害，下次启动会覆盖。"""
    try:
        pid_path.unlink(missing_ok=True)
    except OSError:
        pass


def main() -> int:
    stopped_any = False
    for name in ("backend", "frontend"):
        pid_path = LOG_DIR / f"{name}.pid"
        if not pid_path.exists():
            print(f"[跳过] {name}: 未找到 PID 文件")
            continue
        try:
            pid = int(pid_path.read_text(encoding="ascii").strip())
        except (ValueError, OSError):
            print(f"[跳过] {name}: PID 文件内容无效")
            _remove_pid_file(pid_path)
            continue
        if _stop_by_pid(pid):
            print(f"[停止] {name} (pid={pid})")
            stopped_any = True
        else:
            print(f"[提示] {name} (pid={pid}) 已不在运行（可能早已退出）")
        _remove_pid_file(pid_path)

    if not stopped_any:
        print("没有需要停止的进程。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

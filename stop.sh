#!/usr/bin/env bash
# M7 停止：按 logs/*.pid 清理前后端进程（逻辑在 scripts/stop.py）
set -euo pipefail
cd "$(dirname "$0")"

if [ -f ".venv/Scripts/python.exe" ]; then
    PY=".venv/Scripts/python.exe"
elif [ -f ".venv/bin/python" ]; then
    PY=".venv/bin/python"
else
    PY="python"
fi

exec "$PY" scripts/stop.py "$@"

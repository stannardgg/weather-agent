#!/usr/bin/env bash
# M7 一键启动：迁移 + 后端 + 前端（逻辑在 scripts/serve.py）
set -euo pipefail
cd "$(dirname "$0")"

if [ -f ".venv/Scripts/python.exe" ]; then
    PY=".venv/Scripts/python.exe"          # Windows
elif [ -f ".venv/bin/python" ]; then
    PY=".venv/bin/python"                   # Linux / macOS
else
    echo "未找到 venv。请先：python -m venv .venv && pip install -r requirements.txt" >&2
    exit 1
fi

exec "$PY" scripts/serve.py "$@"

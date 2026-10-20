#!/usr/bin/env bash
# 打包 weather-agent 源码，产出可上传云端的压缩包（排除本地 venv / 数据 / 日志 / 密钥）。
# 用途：在无法本地跑 Docker 的机器（如精简版 Windows）上，把项目发到 Linux 云主机验证。
# 用法（Git Bash 或 Linux）：bash scripts/package_for_cloud.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$(dirname "$ROOT")/weather-agent-docker.tar.gz"

cd "$ROOT"
tar --exclude='.venv' \
    --exclude='venv' \
    --exclude='data' \
    --exclude='logs' \
    --exclude='__pycache__' \
    --exclude='.pytest_cache' \
    --exclude='.ruff_cache' \
    --exclude='.env' \
    --exclude='.git' \
    --exclude='*.pyc' \
    -czf "$OUT" .

echo "已生成：$OUT"
ls -lh "$OUT"

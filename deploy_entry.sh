#!/bin/sh
# 上线专用启动入口（单端口双服务）。
# 后端 FastAPI 只监听容器内 127.0.0.1:8000（不对外），前端 Streamlit 监听对外 $PORT，
# 前端在服务端通过 localhost:8000 调后端 —— 这样只暴露一个公网端口，且无跨域问题。
set -e
cd "$(dirname "$0")"

# 1) 数据库迁移（同步完成后再起服务，避免库里没表）
python -m alembic upgrade head

# 2) 后端（内部端口）
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

# 3) 前端（对外 $PORT，由部署平台注入）
# 关键：部署在反向代理（公网域名）后，浏览器 WebSocket 会带 Origin=公网域名，
# 而网关改写 Host 后 Streamlit 的 XSRF/Origin 防护会把 Origin 判为不匹配 → 403，
# 页面永远 "Please wait..." 转圈。故必须关闭 XSRF 与 CORS 检查（演示应用可接受）。
export API_BASE_URL="http://127.0.0.1:8000/api/v1"
exec python -m streamlit run frontend/app.py \
  --server.address 0.0.0.0 \
  --server.port "$PORT" \
  --server.headless true \
  --server.enableXsrfProtection false \
  --server.enableCORS false

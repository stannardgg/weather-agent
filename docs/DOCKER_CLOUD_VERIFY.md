# Docker 全流程云端验证手册

> 适用场景：本机无法运行 Docker（精简版 Windows、无虚拟化等）时，把项目发到一台 Linux 云主机上，完成 `docker compose up` 的端到端验证。
> 前置条件：一台可 SSH 登录的 Linux 云主机（Ubuntu/Debian 均可，1C2G 足够）。

## 一、本地打包（在 Windows 本机 Git Bash 执行）

```bash
cd /d/cache/.workbuddy/demo1/weather-agent
bash scripts/package_for_cloud.sh
```

产物在 `demo1/weather-agent-docker.tar.gz`（已排除 `.venv`、`data`、`logs`、`.env`、`.git` 等，约几 MB）。

## 二、上传到云主机（替换成你的 IP 和用户）

```bash
scp /d/cache/.workbuddy/demo1/weather-agent-docker.tar.gz ubuntu@YOUR_SERVER_IP:~/
```

## 三、登录云主机并准备环境

```bash
ssh ubuntu@YOUR_SERVER_IP

# 安装 Docker（已装则跳过）
curl -fsSL https://get.docker.com | sh
# 免 sudo 使用 docker（新组生效需重新登录或 newgrp）
sudo usermod -aG docker "$USER" && newgrp docker

# 解压项目
mkdir -p ~/weather-agent && cd ~/weather-agent
tar -xzf ~/weather-agent-docker.tar.gz
```

## 四、配置 .env

```bash
cp .env.example .env
```

分两种情况：

- **仅验证基础设施（镜像构建 / 服务启动 / health / 前端）**：`cp` 后不改任何值即可。因为 `APP_ENV=dev` 允许 `dev-only` 开头的密钥，而 `/health` 不依赖外部天气/LLM 密钥。
- **要跑通天气问答或知识库问答**：用编辑器改 `.env`，至少填：
  - `SECRET_KEY`（生成：`openssl rand -base64 48`）
  - `WEATHER_API_HOST` / `WEATHER_API_KEY`（和风天气，可选）
  - `LLM_API_KEY`（DeepSeek 等，可选）

## 五、构建并启动（首次约 5–10 分钟）

```bash
docker compose up -d --build
```

首次会依次：拉 `python:3.12-slim` 基础镜像 → 装 pip 依赖 → 建库迁移（`alembic upgrade head`）→ 起 backend + frontend。embedding 模型**只有首次实际检索时才下载**（默认走 `hf-mirror.com`），所以纯验证 health 不用等模型。

## 六、验证清单

```bash
# 1) 容器状态：backend / frontend 均为 Up
docker compose ps

# 2) 后端存活探针：期望 {"code":0,...}，milestone 字段 = "M6"
curl -s http://localhost:8000/api/v1/health

# 3) 就绪探针：期望 ready 状态，并报告 llm_configured / weather_configured
curl -s http://localhost:8000/api/v1/health/ready

# 4) 前端页面：期望 HTTP 200
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8501

# 5) 指标端点：期望 Prometheus 文本，含 http_requests_total
curl -s http://localhost:8000/metrics | head -5

# 6) 数据卷落盘：SQLite 已建库
docker compose exec backend ls -l /app/data/weather_agent.db
```

## 七、看日志 / 排障

```bash
docker compose logs -f backend      # 后端实时日志
docker compose logs -f frontend     # 前端实时日志
docker compose logs backend | grep -iE "error|traceback"   # 搜错误
```

常见问题：

| 现象 | 原因 / 处理 |
| --- | --- |
| 端口 8000/8501 被占 | 换 `docker-compose.yml` 里的宿主端口，或 `sudo lsof -i:8000` 查占用 |
| health 通但 weather/llm 报错 | 密钥未填，属预期，填 `.env` 后 `docker compose restart backend` |
| 模型下载卡住 | 海外主机可把 `HF_ENDPOINT` 改成 `https://huggingface.co`，国内保持 `hf-mirror.com` |

## 八、收尾

```bash
docker compose down        # 停止并移除容器（数据留在 ./data，可再次 up 恢复）
docker compose down -v     # 连同数据卷一起删除（验证完想清干净时用）
```

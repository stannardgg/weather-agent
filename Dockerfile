# 企业级天气查询 Agent 后端/前端共用一个镜像（依赖同一份 requirements.txt）。
# 后端与前端只是启动命令不同，由 docker-compose 用 command 区分，避免维护两份镜像。

FROM python:3.12-slim

# onnxruntime / fastembed 的 CPU 推理在 Linux 上依赖 OpenMP 运行库
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先拷依赖清单再装依赖：requirements.txt 不变时这一层命中缓存，改动源码不重装
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 再拷源码（.dockerignore 已排除 .venv / data / logs 等）
COPY . .

# 数据目录（SQLite / Chroma / uploads / 模型缓存）由 docker-compose 挂载卷持久化。
# 模型首次运行才下载（默认走 HF 镜像），落到挂载的 ./data/models/fastembed，之后离线。
EXPOSE 8000 8501

# 默认起后端；前端服务在 compose 里用 command 覆盖
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

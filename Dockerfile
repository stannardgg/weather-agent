# 企业级天气查询 Agent：后端/前端共用一个镜像（依赖同一份 requirements.lock）。
#
# 后端与前端只是启动命令不同，由 docker-compose 用 command 区分，避免维护两份镜像。
#
# 与 requirements.txt 的分工：
#   requirements.txt   → 人写的"需要什么"（版本下界 + 选型理由）
#   requirements.lock  → pip-compile 生成的"实际装什么"（全部 == 精确锁定）
#   镜像只认 lock。按 requirements.txt 装的话构建结果会随时间漂移——
#   同一份代码今天能构建、下个月就可能因为上游发新版而失败或行为改变。

# 解释器版本必须与 .venv、CI、pyproject 的 target-version 一致（都是 3.13）。
# 之前这里写 3.12 而本地是 3.13，属于"本地绿、镜像挂"的经典来源。
FROM python:3.13-slim

# onnxruntime / fastembed 的 CPU 推理在 Linux 上依赖 OpenMP 运行库
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先拷依赖清单再装依赖：lock 不变时这一层命中缓存，改源码不会触发重装
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# 再拷源码（.dockerignore 已排除 .venv / data / logs 等）
COPY . .

# 非 root 运行（CIS Docker Benchmark 4.1）。容器逃逸类利用链几乎都要求进程具备
# root 权限，降权是成本最低的一道闸。
#
# 代价说明：bind mount 的 ./data 属主必须是 UID 10001，否则容器内写不进
# SQLite / 上传文件。Linux 宿主机首次部署需执行一次：
#     sudo chown -R 10001:10001 ./data
# Windows / macOS 的 Docker Desktop 走文件共享层，不涉及该问题。
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

# 数据目录（SQLite / Chroma / uploads / 模型缓存）由 docker-compose 挂载卷持久化。
# 模型首次运行才下载（默认走 HF 镜像），落到挂载的 ./data/models/fastembed，之后离线。
EXPOSE 8000 8501

# 默认起后端；前端服务在 compose 里用 command 覆盖
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

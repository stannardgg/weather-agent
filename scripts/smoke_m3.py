"""M3 端到端冒烟：用真实 uvicorn 进程跑一遍知识库全链路。

和 `tests/test_knowledge.py` 的区别：
- 测试用 ASGITransport（不起真实网络栈、后台任务随请求同步跑完）；
- 本脚本**另起一个 uvicorn 进程**，通过 HTTP 打真实端口，因此能验证
  「进程内 BackgroundTasks 在真实运行时确实会执行」这件事。
  这是 M3-Q3 方案唯一的、测试覆盖不到的盲区。

用法:
    .venv/Scripts/python.exe scripts/smoke_m3.py
    .venv/Scripts/python.exe scripts/smoke_m3.py --real-embedding

默认用占位向量（不联网、快、结果确定）。加 `--real-embedding` 则切到真实向量模型，
用来验证「512 维浮点向量真正写进 Chroma 并能被查询返回」这段链路——
占位向量和真实向量在向量库里的行为并不完全一样，值得单独验一次。

所有数据都落在临时目录里，跑完自动清理，不碰仓库的 data/。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():  # 非 Windows 或 venv 位置不同时的兜底
    PYTHON = Path(sys.executable)

PASS = "  [OK]"
FAIL = "  [!!]"
INFO = "  [--]"


def free_port() -> int:
    """让操作系统分配一个空闲端口，避免撞上正在跑的开发服务器。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_health(base: str, proc: subprocess.Popen, log_path: Path, timeout: float = 60.0) -> dict:
    """轮询存活检查直到服务就绪。

    不睡固定时长，而是循环探测：启动耗时取决于机器，固定 sleep 要么白等要么误判。
    进程提前退出时立刻报错并把日志打出来，否则只能看到一个毫无信息的超时。

    注意路径：所有业务路由都挂在 `/api/v1` 前缀下（见 app/main.py），
    探针地址是 `/api/v1/health` 而不是 `/health`。
    """
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"服务进程提前退出（code={proc.returncode}）：\n{read_log(log_path)}"
            )
        try:
            response = httpx.get(f"{base}/api/v1/health", timeout=2.0)
            if response.status_code == 200:
                return response.json()
            last_error = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.4)
    raise RuntimeError(f"等待服务就绪超时（最后错误：{last_error}）\n--- 服务日志 ---\n{read_log(log_path)}")


def read_log(log_path: Path) -> str:
    if not log_path.exists():
        return "(无日志)"
    return log_path.read_text(encoding="utf-8", errors="replace")[-4000:]


def verify_vector_store(workdir: Path, env: dict, checks: Checks, real_embedding: bool) -> None:
    """服务停掉后打开向量库，核对「真正落库的是什么」。

    为什么要等停掉服务再验：Chroma 的 PersistentClient 是单进程设计
    （见 rag/vectorstore.py 的说明），服务还开着时另起进程去开同一个目录会锁冲突。

    这步是测试覆盖不到的地方——`tests/` 里的断言只证明「我们的代码认为写进去了」，
    这里证明的是「向量库里真的躺着 512 维浮点向量，而且用查询向量能把它排到第一」。
    对 `--real-embedding` 而言，这是项目里第一条端到端验证「检索真的有用」的检查。
    """
    script = workdir / "_verify_store.py"
    script.write_text(
        '''
import json, os, sys
import numpy as np

# 子脚本放在临时目录里，Python 会把**临时目录**而非项目根放进 sys.path[0]，
# 因此这里必须手动补上项目根，否则 import app.* 会失败。
sys.path.insert(0, os.getcwd())

chroma_dir, collection_name = sys.argv[1], sys.argv[2]
real = sys.argv[3] == "1"

import chromadb
from app.rag.embedder import get_embedder

client = chromadb.PersistentClient(path=chroma_dir)
collection = client.get_or_create_collection(
    name=collection_name, metadata={"hnsw:space": "cosine"}
)
total = collection.count()
sample = collection.get(include=["embeddings", "metadatas"], limit=5)
embeddings = sample.get("embeddings")
dim = int(np.array(embeddings).shape[1]) if embeddings is not None and len(embeddings) else 0

result = {"count": total, "dim": dim}

if real and total:
    embedder = get_embedder()
    query = "住宿费上限是多少？"
    qv = np.array(list(embedder._get_model().embed([query]))[0])
    hits = collection.query(query_embeddings=[qv.tolist()], n_results=3)
    docs = hits.get("metadatas", [[]])[0]
    dists = hits.get("distances", [[]])[0]
    result["top"] = [
        {"document_id": d.get("document_id"), "distance": round(float(x), 4)}
        for d, x in zip(docs, dists)
    ]
    # 同时确认元数据里带了 user_id —— M4 检索时按它过滤（M3-Q4 的兑现点）
    result["metadata_keys"] = sorted((docs[0] or {}).keys()) if docs else []

# 用固定前缀标记，避免与第三方库的日志/进度条混在一起
print("RESULT_JSON:" + json.dumps(result, ensure_ascii=False))
''',
        encoding="utf-8",
    )

    proc = subprocess.run(
        [str(PYTHON), str(script), (workdir / "chroma").as_posix(), "smoke_m3", "1" if real_embedding else "0"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        checks.check("向量库可被独立进程打开并读取", False, (proc.stderr or proc.stdout)[-400:])
        return

    marker = "RESULT_JSON:"
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith(marker)), None)
    if line is None:
        checks.check("向量库自检脚本输出可解析", False, proc.stdout[-300:])
        return
    info = json.loads(line[len(marker):])

    print(f"{INFO} 向量库内容 = {json.dumps(info, ensure_ascii=False)}")
    checks.check("向量已真正落库", info["count"] > 0, f"count={info['count']}")
    checks.check("落库向量维度为 512", info["dim"] == 512, f"实际 {info['dim']}")

    if real_embedding:
        checks.check(
            "元数据带 user_id（M4 按用户过滤的依据）",
            "user_id" in info.get("metadata_keys", []),
            str(info.get("metadata_keys")),
        )
        top = info.get("top") or []
        checks.check(
            "用查询向量检索能命中（真实向量端到端可用）",
            bool(top),
            str(top),
        )
        if top:
            # 余弦距离：0 = 完全相同，1 = 正交。相关文档应当明显小于无关文档。
            best = top[0]["distance"]
            print(f"{INFO} 最相似片段的余弦距离 = {best:.4f}（越小越像）")
            checks.check(
                "最相似片段确实与查询相关（距离显著小于正交的 0.5）",
                best < 0.5,
                f"距离 {best} 偏大，说明向量没有语义",
            )


class Checks:
    """收集检查结果，最后统一汇总——单点失败不应该中断后面的验证。"""

    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        if condition:
            self.passed += 1
            print(f"{PASS} {name}")
        else:
            self.failed += 1
            print(f"{FAIL} {name}" + (f" —— {detail}" if detail else ""))
        return condition


def main() -> int:
    parser = argparse.ArgumentParser(description="M3 知识库端到端冒烟")
    parser.add_argument(
        "--real-embedding",
        action="store_true",
        help="用真实向量模型（fastembed）而非占位向量，验证真实向量写入向量库的链路",
    )
    args = parser.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="smoke_m3_"))
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    checks = Checks()

    print("=" * 72)
    print("M3 冒烟测试：知识库入库全链路（真实 uvicorn 进程）")
    print(f"向量化方式: {'真实模型 fastembed' if args.real_embedding else '占位向量 placeholder'}")
    print(f"临时数据目录: {workdir}")
    print(f"服务地址   : {base}")
    print("=" * 72)

    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "dev",
            # 必须给足 32 字节，否则 Settings 的启动校验会直接拒绝
            "SECRET_KEY": "smoke-test-secret-key-0123456789abcdefghijklmn",
            "DATABASE_URL": f"sqlite+aiosqlite:///{(workdir / 'smoke.db').as_posix()}",
            "UPLOAD_DIR": (workdir / "uploads").as_posix(),
            "CHROMA_DIR": (workdir / "chroma").as_posix(),
            "CHROMA_COLLECTION": "smoke_m3",
            "WEATHER_API_KEY": "smoke-placeholder-key",
            "WEATHER_API_HOST": "devapi.qweather.com",
        }
    )
    if args.real_embedding:
        # 真实模型：维度必须与模型（bge-small-zh-v1.5 = 512）一致，否则启动时会报错
        env.update({"EMBEDDING_PROVIDER": "fastembed", "EMBEDDING_DIM": "512"})
    else:
        env.update({"EMBEDDING_PROVIDER": "placeholder"})

    # 先建库：应用不会自动建表（§11 约定迁移一律走 Alembic），因此这里真的跑一次
    # `alembic upgrade head`——顺带验证 M3 的迁移脚本在真实运行时可用。
    # alembic/env.py 读的是 settings.database_url，所以只要环境变量指到临时目录即可。
    print("\n[0] 用 Alembic 在临时目录建库（顺带验证 M3 迁移可跑）")
    migration = subprocess.run(
        [str(PYTHON), "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if migration.returncode != 0:
        print(f"{FAIL} alembic upgrade head 失败：\n{migration.stdout}\n{migration.stderr}")
        shutil.rmtree(workdir, ignore_errors=True)
        return 1
    print(f"{PASS} 迁移已应用（临时库 {workdir / 'smoke.db'}）")

    # 服务日志写到文件而不是 PIPE：uvicorn 启动期会往 stdout 写日志，
    # 若用 PIPE 且没人及时读，缓冲区一满子进程就阻塞在启动阶段——
    # 表现为「端口已监听、请求却一直没有响应」的超时，非常难排查。
    log_path = workdir / "server.log"
    log_file = log_path.open("w", encoding="utf-8")
    server_ready = False
    proc = subprocess.Popen(
        [
            str(PYTHON),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    try:
        print("\n[1] 启动服务并等待就绪")
        health = wait_health(base, proc, log_path)
        server_ready = True
        print(f"{INFO} /api/v1/health = {json.dumps(health, ensure_ascii=False)}")
        data = health.get("data", health)
        checks.check("存活检查返回 200 且服务名正确", data.get("app") == "weather-agent", str(data))
        checks.check("里程碑标记为 M3", data.get("milestone") == "M3", str(data.get("milestone")))

        ready = httpx.get(f"{base}/api/v1/health/ready", timeout=15.0)
        ready_body = ready.json().get("data", {})
        components = ready_body.get("components", {})
        print(f"{INFO} 组件状态 = {json.dumps(components, ensure_ascii=False)}")
        checks.check(
            "就绪检查：database 必需组件可用",
            components.get("database", {}).get("ready") is True,
            str(components.get("database")),
        )
        checks.check(
            "vector_store 就绪（Chroma 目录可打开，真实探测通过）",
            components.get("vector_store", {}).get("ready") is True,
            str(components.get("vector_store")),
        )
        embedder_component = components.get("embedder", {})
        if args.real_embedding:
            # 真实模型：ready 必须为 True，且不应带告警语
            checks.check(
                "embedder 如实报告真实模型已就绪",
                embedder_component.get("ready") is True
                and "占位向量" not in str(embedder_component.get("note", "")),
                str(embedder_component),
            )
        else:
            # 占位向量的 ready 按设计就是 False：链路能跑通，但检索无语义。
            # 这里断言的是「它被如实标记成未就绪并给出告警」，而不是「它是绿的」。
            checks.check(
                "embedder 如实标记占位向量为未就绪并给出告警",
                embedder_component.get("configured") is True
                and embedder_component.get("ready") is False
                and "占位向量" in str(embedder_component.get("note", "")),
                str(embedder_component),
            )
        checks.check(
            "就绪检查整体返回 200（仅 database 为必需）",
            ready.status_code == 200,
            f"HTTP {ready.status_code}",
        )

        with httpx.Client(base_url=base, timeout=30.0) as client:
            print("\n[2] 注册并登录")
            account = {"username": "smoke_m3", "email": "smoke_m3@example.com", "password": "pw12345678"}
            client.post("/api/v1/auth/register", json=account)
            login = client.post(
                "/api/v1/auth/login",
                json={"username": account["username"], "password": account["password"]},
            )
            checks.check("登录成功并拿到 access_token", login.status_code == 200, login.text[:200])
            token = login.json()["data"]["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            print("\n[3] 未登录访问知识库应被拒")
            anon = client.get("/api/v1/knowledge/documents")
            checks.check("未登录返回 401", anon.status_code == 401, f"实际 {anon.status_code}")

            print("\n[4] 上传 txt 并等待入库完成")
            body = ("关于差旅报销的规定。员工出差前需提交申请，住宿标准按城市分级。" * 40).encode()
            created = client.post(
                "/api/v1/knowledge/documents",
                headers=headers,
                files={"file": ("差旅制度.txt", body, "text/plain")},
            )
            checks.check("上传返回 202（已接收，非已完成）", created.status_code == 202, created.text[:300])
            payload = created.json()["data"]
            checks.check("首次上传 deduplicated=false", payload["deduplicated"] is False, str(payload))
            document_id = payload["document"]["id"]
            checks.check("初始状态为 pending", payload["document"]["status"] == "pending", str(payload["document"]))

            # 轮询等后台任务跑完——这正是「真实进程」与「测试」的区别所在
            status = "pending"
            deadline = time.time() + 30
            while time.time() < deadline:
                detail = client.get(f"/api/v1/knowledge/documents/{document_id}", headers=headers)
                status = detail.json()["data"]["status"]
                if status in ("ready", "failed"):
                    break
                time.sleep(0.3)
            checks.check("后台入库任务在真实进程中执行（状态离开 pending）", status == "ready", f"最终状态 {status}")
            document = detail.json()["data"]
            checks.check("切分结果非空", document["chunk_count"] > 0, str(document["chunk_count"]))
            checks.check(
                "详情返回片段预览且有 total_chunks",
                len(document["chunks"]) > 0 and document["total_chunks"] == document["chunk_count"],
                str(document),
            )
            checks.check("成功路径不残留错误信息", document["error_message"] is None, str(document["error_message"]))
            checks.check(
                "响应体不泄露 stored_path / file_hash",
                "stored_path" not in document and "file_hash" not in document,
                str(list(document)),
            )
            # 切分是否落在句子边界：keep_separator="end" 会让每块以分隔符收尾。
            # 若切分器没配中文分隔符，句子会被从中间硬切开，这里就会失败。
            # 只校验「不是全文最后一块」的片段——最后一块是原文结尾，本就不带标点。
            chunks = document["chunks"]
            last_index = document["total_chunks"] - 1
            non_final = [c["content"] for c in chunks if c["chunk_index"] != last_index]
            checks.check(
                "片段未被从句子中间硬切（非末块以句号收尾）",
                bool(non_final) and all(p.rstrip().endswith(("。", "！", "？")) for p in non_final),
                str([p[-14:] for p in non_final]),
            )

            print("\n[5] 重复上传同一文件应命中查重")
            repeat = client.post(
                "/api/v1/knowledge/documents",
                headers=headers,
                files={"file": ("差旅制度.txt", body, "text/plain")},
            )
            repeat_data = repeat.json()["data"]
            checks.check(
                "二次上传 deduplicated=true 且 id 相同",
                repeat_data["deduplicated"] is True and repeat_data["document"]["id"] == document_id,
                str(repeat_data),
            )

            print("\n[6] 不支持的类型与解析失败")
            bad_type = client.post(
                "/api/v1/knowledge/documents",
                headers=headers,
                files={"file": ("x.exe", b"MZ\x90\x00", "application/octet-stream")},
            )
            checks.check("上传 .exe 返回 415", bad_type.status_code == 415, f"实际 {bad_type.status_code}")

            broken = client.post(
                "/api/v1/knowledge/documents",
                headers=headers,
                files={"file": ("假的.pdf", b"not a pdf at all", "application/pdf")},
            )
            broken_id = broken.json()["data"]["document"]["id"]
            broken_status = "pending"
            for _ in range(40):
                got = client.get(f"/api/v1/knowledge/documents/{broken_id}", headers=headers).json()["data"]
                broken_status = got["status"]
                if broken_status in ("ready", "failed"):
                    break
                time.sleep(0.3)
            checks.check("假 PDF 落到 failed 而非卡在 parsing", broken_status == "failed", f"最终 {broken_status}")
            checks.check("失败带出原因", bool(got["error_message"]), str(got["error_message"])[:160])

            print("\n[7] 跨用户隔离")
            other = {"username": "smoke_other", "email": "smoke_other@example.com", "password": "pw12345678"}
            client.post("/api/v1/auth/register", json=other)
            other_login = client.post(
                "/api/v1/auth/login", json={"username": other["username"], "password": other["password"]}
            )
            other_headers = {"Authorization": f"Bearer {other_login.json()['data']['access_token']}"}
            stolen = client.get(f"/api/v1/knowledge/documents/{document_id}", headers=other_headers)
            checks.check("B 查 A 的文档返回 404", stolen.status_code == 404, f"实际 {stolen.status_code}")
            listing = client.get("/api/v1/knowledge/documents", headers=other_headers).json()["data"]
            checks.check("B 的列表中看不到 A 的文档", listing["total"] == 0, str(listing.get("total")))

            print("\n[8] 删除文档应同时清掉向量")
            before_list = client.get("/api/v1/knowledge/documents", headers=headers).json()["data"]
            deleted = client.delete(f"/api/v1/knowledge/documents/{document_id}", headers=headers)
            checks.check("删除返回 200", deleted.status_code == 200, deleted.text[:200])
            gone = client.get(f"/api/v1/knowledge/documents/{document_id}", headers=headers)
            checks.check("删除后详情返回 404", gone.status_code == 404, f"实际 {gone.status_code}")
            final_list = client.get("/api/v1/knowledge/documents", headers=headers).json()["data"]
            # 注意不能断言 total == 0：步骤 [6] 那个解析失败的假 PDF 属于同一用户，它仍在库里。
            # 真正要验的是「被删的那一条消失了，且只少了这一条」。
            ids_before = {item["id"] for item in before_list["items"]}
            ids_after = {item["id"] for item in final_list["items"]}
            checks.check(
                "被删文档从列表中消失，其余记录不受影响",
                document_id not in ids_after and len(ids_after) == len(ids_before) - 1,
                f"before={sorted(ids_before)} after={sorted(ids_after)}",
            )
            # 磁盘文件也应被清掉
            uploads = workdir / "uploads"
            leftovers = [p for p in uploads.rglob("*") if p.is_file()] if uploads.exists() else []
            checks.check(
                "磁盘上不留孤儿文件",
                all("差旅制度" not in p.name for p in leftovers),
                str([p.name for p in leftovers])[:200],
            )

            print("\n[9] 上传一份「留存文档」，供停服后核对向量库")
            # 刻意不删它：Chroma 是单进程设计，服务还开着时不能另起进程去读；
            # 而上面那份文档已经被删掉了，所以必须留一份，停服后才有的可查。
            keep = client.post(
                "/api/v1/knowledge/documents",
                headers=headers,
                files={
                    "file": (
                        "留存验证.txt",
                        (
                            "一线城市住宿费每晚上限六百元，二线城市为四百元，超出部分自理。"
                            "系统故障分级中，一级故障须在十五分钟内响应。"
                            "客户投诉须在二十四小时内首次响应，三个工作日内给出方案。"
                        ).encode(),
                        "text/plain",
                    )
                },
            )
            keep_id = keep.json()["data"]["document"]["id"]
            for _ in range(60):
                got = client.get(f"/api/v1/knowledge/documents/{keep_id}", headers=headers).json()["data"]
                if got["status"] in ("ready", "failed"):
                    break
                time.sleep(0.3)
            checks.check(
                "留存文档入库成功（停服后可供核对）",
                got["status"] == "ready" and got["chunk_count"] > 0,
                f"status={got['status']} chunks={got.get('chunk_count')}",
            )

    finally:
        print("\n[10] 关闭服务")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_file.close()
        # 只有在服务压根没起来时才倒日志：那种情况下报错信息全在日志里，
        # 而普通的断言失败已经有逐条明细，再倒一遍日志只是噪音。
        if not server_ready:
            tail = read_log(log_path)
            if tail != "(无日志)":
                print(f"{INFO} 服务日志尾部：\n{tail}")
        elif server_ready:
            # 必须等服务真正停掉才能开向量库（单进程约束）
            print("\n[11] 核对向量库里真正落下的内容")
            verify_vector_store(workdir, env, checks, args.real_embedding)

        print("\n[12] 清理临时目录")
        shutil.rmtree(workdir, ignore_errors=True)
        print(f"{INFO} 已清理 {workdir}")

    print("\n" + "=" * 72)
    print(f"结果：{checks.passed} 项通过，{checks.failed} 项失败")
    print("=" * 72)
    return 1 if checks.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

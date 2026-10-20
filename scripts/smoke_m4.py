"""M4 端到端冒烟：用真实 uvicorn 进程跑一遍「问答 + 会话」全链路。

与 `tests/test_chat.py` 的分工：

- 测试用 ASGITransport 跑进程内调用，快、可细粒度断言；
- 本脚本**另起一个 uvicorn 进程**，通过 HTTP 打真实端口，因此能覆盖测试够不到的几件事：
  1. SSE 真的以 `text/event-stream` 逐事件吐出来（响应头、帧格式、事件顺序）；
  2. 流式路径里 service **自建数据库会话**的做法在真实生命周期下成立
     （请求级会话此时已释放——这是最典型的"测试里好好的、一上线就 500"的地方）；
  3. 检索真的落在 Chroma 上（真实向量模型），而不是被替换的假向量库。

## 关于那个本地假上游（重要）

脚本会在本机起一个极小的 HTTP 服务，扮演两个角色：

- **OpenAI 兼容的对话接口**（`POST /v1/chat/completions`）：让整条链路可以
  **完全离线、零额度**地跑完。它会把自己的观测结果写进回答文本
  （`消息数=… ｜ 参考资料=有/无 ｜ 天气数据=有/无`），于是"检索结果到底有没有
  进到 prompt 里"这件事变成了一句可以断言的话，而不是靠人肉看图。
- **和风天气的假上游**（其余路径一律回 401 + RFC 7807 错误体）：
  让"天气取数失败 → 短路成固定文案"这条分支**确定性地**发生，
  不必依赖真实网络，也不会因为哪天上游改行为就悄悄变成"通过了但没验到"。

加 `--real-llm` 则把 LLM 的三个配置项交还给 `.env`（真实 DeepSeek），
用来人工确认"真实模型答得像不像话"。默认走假模型。

## 一条刻意保留的诚实边界

假模型**不产生真实语义**，所以本脚本不能证明"答得准"。它能证明的是结构正确：
路由走对了、检索结果进了 prompt、token 逐段流出来了、短路时**模型一次都没被调用**。
后者是本脚本最有价值的一条断言——详见下面 [6] 与 [10] 的注释。

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
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():  # 非 Windows 或 venv 位置不同时的兜底
    PYTHON = Path(sys.executable)

PASS = "  [OK]"
FAIL = "  [!!]"
INFO = "  [--]"

# ---------------------------------------------------------------------------
# 两个固定文案：与 app/llm/prompts.py 里的常量重复了。
#
# 为什么重复而不 import：本脚本要能在一个**没装依赖、也没跑起来**的环境里启动
# （它的职责是"从外面看应用"，而不是"变成应用的一部分"）。真去 import
# app.llm.prompts 会把 sqlalchemy / chromadb / fastembed 一串子模块拉进来，
# 冒烟脚本自己先变成一个需要完整依赖的程序，得不偿失。
#
# 重复的代价用一条防漂移检查补回来（见 check_prompts_consistent）：
# 只要这两个字符串不再出现在 prompts.py 里，脚本立刻报错提醒同步。
# ---------------------------------------------------------------------------
NO_KNOWLEDGE_ANSWER = (
    "知识库中未找到相关内容。"
    "可以换一种说法再问，或先在知识库页面上传相关文档。"
)
WEATHER_UNAVAILABLE_ANSWER = (
    "暂时没能取到天气数据，可能是气象服务暂时不可用或城市名没识别出来。"
    "请稍后重试，或把城市写得更具体一些（例如「上海市浦东新区」）。"
)

FAKE_MARKER = "（本地假模型）"


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


# ---------------------------------------------------------------------------
# 本地假上游
# ---------------------------------------------------------------------------

# 假模型按这段关键词表来决定"意图分类"该回哪个词。
# 刻意写得比 app/agent/router.py 的词表**更少**：它只需要覆盖本脚本问的那几个问题，
# 覆盖不到的会落到 chat，而 chat 恰好是我们想验证的"兜底"出口。
_FAKE_WEATHER_HINTS = ("天气", "气温", "下雨", "带伞", "温度", "风力")
_FAKE_KNOWLEDGE_HINTS = ("制度", "流程", "规定", "报销", "差旅", "住宿", "预案")
# INTENT_PROMPT 里的一句话，用来区分"这是分类请求"还是"这是生成请求"
_INTENT_SENTINEL = "只输出类别的英文单词"


def _extract_question(text: str) -> str:
    """从 prompt 文本里取出用户真正问的那句话。"""
    for marker in ("【用户问题】", "用户问题："):
        if marker in text:
            return text.rsplit(marker, 1)[1].strip()
    return text.strip()


def _flatten_content(content: object) -> str:
    """把消息内容压成纯文本（兼容新式"内容块"列表结构）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") in ("text", "text_delta")
        )
    return ""


class FakeUpstream(BaseHTTPRequestHandler):
    """一个扮演「OpenAI 兼容接口 + 和风天气」的极小 HTTP 服务。

    它同时充当**观测点**：`chat_calls` 记录被调用了几次生成接口。这一点不是花边，
    而是本脚本最关键的验证手段——"短路时模型一次都没被调用"只能由上游侧观察到，
    应用自己无论怎么断言都无法自证（它连"没调用"这件事都不会产生任何记录）。
    """

    protocol_version = "HTTP/1.1"

    # 跨实例共享的观测数据（HTTP server 每个请求会新建 handler 实例，实例属性存不住）
    stats: dict[str, int] = {"chat_calls": 0, "intent_calls": 0, "weather_calls": 0}

    def log_message(self, *args: object) -> None:  # noqa: D102 —— 静音，避免刷屏
        pass

    # -- 路由 ---------------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802 —— BaseHTTPRequestHandler 的约定方法名
        raw = self._read_body()
        if self.path.rstrip("/").endswith("/chat/completions"):
            self._handle_chat(raw)
            return
        self._handle_weather()

    def do_GET(self) -> None:  # noqa: N802
        self._read_body()
        self._handle_weather()

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    # -- 和风天气的假上游 ---------------------------------------------------

    def _handle_weather(self) -> None:
        """一律回 401 + RFC 7807 错误体。

        为什么用 401 而不是"连不上"：真实项目里"密钥写错"才是最常见的天气失败原因，
        而 401 又恰好是「不重试」的那一类（§11 第 2 条）。用 401 就能让
        「天气取数失败 → 短路」这条分支**又快又确定**地发生，不必等退避重试。
        """
        type(self).stats["weather_calls"] += 1
        body = json.dumps(
            {
                "error": {
                    "status": 401,
                    "type": "https://dev.qweather.com/docs/error/401",
                    "title": "Unauthorized",
                    "detail": "冒烟用假上游：这是刻意的 401",
                }
            }
        ).encode()
        self.send_response(401)
        self.send_header("Content-Type", "application/problem+json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- OpenAI 兼容接口 -----------------------------------------------------

    def _handle_chat(self, raw: bytes) -> None:
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            payload = {}
        messages = payload.get("messages") or []
        texts = [_flatten_content(item.get("content")) for item in messages]
        joined = "\n".join(texts)

        if _INTENT_SENTINEL in joined:
            type(self).stats["intent_calls"] += 1
            reply = self._fake_intent(_extract_question(texts[-1] if texts else ""))
        else:
            type(self).stats["chat_calls"] += 1
            reply = self._fake_answer(messages, texts[-1] if texts else "")

        if payload.get("stream"):
            self._send_openai_stream(reply)
        else:
            self._send_openai_once(reply)

    @staticmethod
    def _fake_intent(question: str) -> str:
        weather = any(word in question for word in _FAKE_WEATHER_HINTS)
        knowledge = any(word in question for word in _FAKE_KNOWLEDGE_HINTS)
        if weather and knowledge:
            return "mixed"
        if weather:
            return "weather"
        if knowledge:
            return "knowledge"
        return "chat"

    @staticmethod
    def _fake_answer(messages: list, last_text: str) -> str:
        """一句可断言的"回答"：把它观测到的 prompt 形状如实报出来。"""
        return (
            f"{FAKE_MARKER}消息数={len(messages)}"
            f"｜参考资料={'有' if '【参考资料】' in last_text else '无'}"
            f"｜天气数据={'有' if '【天气数据】' in last_text else '无'}"
            f"｜天气缺失提示={'有' if '本次未能取到天气数据' in last_text else '无'}"
        )

    def _openai_payload(self, **overrides: object) -> dict:
        body = {
            "id": "chatcmpl-smoke",
            "object": "chat.completion.chunk",
            "created": 1700000000,
            "model": "smoke-fake-model",
            "choices": [],
        }
        body.update(overrides)
        return body

    def _usage(self, prompt: int, completion: int) -> dict:
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        }

    def _send_openai_once(self, reply: str) -> None:
        body = json.dumps(
            self._openai_payload(
                object="chat.completion",
                choices=[
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": reply},
                        "finish_reason": "stop",
                    }
                ],
                usage=self._usage(11, len(reply)),
            )
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_openai_stream(self, reply: str) -> None:
        """按 SSE 逐段吐字。

        `Content-Length` + 逐段 `write` 仍然是**真的分段送达**：
        `BaseHTTPRequestHandler.wfile` 是无缓冲的，每次 write 直接落 socket。
        每段之间睡 10ms，是为了让"流式"这件事在时间上也成立——虽然本脚本的断言
        只检查事件顺序，不检查到达时刻（那需要抓包，收益不抵复杂度）。

        最后那条 `choices: []` 且带 `usage` 的事件是照 OpenAI 真实行为补的：
        用量往往挂在收尾的空 choices 事件上，而不是 finish_reason 那条。
        """
        frames: list[bytes] = []

        def frame(obj: dict) -> bytes:
            return f"data: {json.dumps(obj)}\n\n".encode()

        frames.append(frame(self._openai_payload(choices=[
            {"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}
        ])))
        # 每次 4 个字符：足够切出多个 delta 事件，又不至于把响应拖长
        for start in range(0, len(reply), 4):
            frames.append(frame(self._openai_payload(choices=[
                {"index": 0, "delta": {"content": reply[start:start + 4]}, "finish_reason": None}
            ])))
        frames.append(frame(self._openai_payload(
            choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}],
            usage=self._usage(11, len(reply)),
        )))
        frames.append(frame(self._openai_payload(
            choices=[], usage=self._usage(11, len(reply))
        )))
        frames.append(b"data: [DONE]\n\n")

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(sum(len(item) for item in frames)))
        self.end_headers()
        for item in frames:
            self.wfile.write(item)
            time.sleep(0.01)


def start_fake_upstream() -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeUpstream)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, int(server.server_address[1])


# ---------------------------------------------------------------------------
# 服务起停
# ---------------------------------------------------------------------------


def free_port() -> int:
    """让操作系统分配一个空闲端口，避免撞上正在跑的开发服务器。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def read_log(log_path: Path) -> str:
    if not log_path.exists():
        return "(无日志)"
    return log_path.read_text(encoding="utf-8", errors="replace")[-4000:]


def wait_health(base: str, proc: subprocess.Popen, log_path: Path, timeout: float = 90.0) -> dict:
    """轮询存活检查直到服务就绪（超时给 90s：首次加载真实向量模型要几秒）。"""
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"服务进程提前退出（code={proc.returncode}）：\n{read_log(log_path)}")
        try:
            response = httpx.get(f"{base}/api/v1/health", timeout=2.0)
            if response.status_code == 200:
                return response.json()
            last_error = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.4)
    raise RuntimeError(
        f"等待服务就绪超时（最后错误：{last_error}）\n--- 服务日志 ---\n{read_log(log_path)}"
    )


# ---------------------------------------------------------------------------
# SSE 解析
# ---------------------------------------------------------------------------


def parse_sse(text: str) -> list[tuple[str, dict]]:
    """把 SSE 原文解析成 `[(事件名, 数据), ...]`。

    SSE 的规范是「每条消息以空行结束」，所以按 `\\n\\n` 切块即可。
    这里刻意不用第三方 SSE 库：协议就这么两行，自己解析反而能顺带验证
    "服务器真的按规范补了那个空行"——用库的话，库会替我们容错，
    而前端的浏览器可不会。
    """
    events: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        name = None
        data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = line[len("data:"):].strip()
        if name and data is not None:
            events.append((name, json.loads(data)))
    return events


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def check_prompts_consistent(checks: Checks) -> None:
    """防止本脚本里那两份"抄来的"固定文案与 prompts.py 漂移。

    刻意不做「整串相等」检查：`prompts.py` 里的字符串是**隐式拼接的跨行写法**
    （`("第一句。" "第二句。")`），整串在源码里根本不连续出现，用整串比会永远失败。
    改为各取两段足够特别的片段比对——它们都完整落在某一行里，既能抓住改写，也不会误报。
    """
    prompts_file = PROJECT_ROOT / "app" / "llm" / "prompts.py"
    if not prompts_file.exists():
        checks.check("能找到 app/llm/prompts.py 以核对固定文案", False, str(prompts_file))
        return
    source = prompts_file.read_text(encoding="utf-8")
    fragments = (
        "知识库中未找到相关内容。",
        "或先在知识库页面上传相关文档。",
        "暂时没能取到天气数据",
        "或把城市写得更具体一些",
    )
    missing = [item for item in fragments if item not in source]
    checks.check(
        "冒烟脚本里的固定文案与 prompts.py 一致（防漂移）",
        not missing,
        f"prompts.py 里已找不到：{missing}；文案改过了请同步本脚本顶部的两份常量",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="M4 问答端到端冒烟")
    parser.add_argument(
        "--real-llm",
        action="store_true",
        help="用 .env 里的真实 LLM 配置（默认用本地假模型，离线且零额度）",
    )
    parser.add_argument(
        "--placeholder-embedding",
        action="store_true",
        help="用占位向量（跳过真实向量模型加载，快但检索无语义，仅用于排查环境问题）",
    )
    args = parser.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="smoke_m4_"))
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    checks = Checks()
    fake_server, fake_port = start_fake_upstream()
    fake_base = f"http://127.0.0.1:{fake_port}"

    print("=" * 72)
    print("M4 冒烟测试：问答与会话全链路（真实 uvicorn 进程）")
    print(f"大模型      : {'真实（.env 配置）' if args.real_llm else '本地假模型（离线、零额度）'}")
    print(f"向量化方式  : {'占位向量 placeholder' if args.placeholder_embedding else '真实模型 fastembed'}")
    print(f"假上游      : {fake_base}（同时扮演 OpenAI 兼容接口与和风 401）")
    print(f"临时数据目录: {workdir}")
    print(f"服务地址    : {base}")
    print("=" * 72)

    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "dev",
            # 必须给足 32 字节，否则 Settings 的启动校验会直接拒绝
            "SECRET_KEY": "smoke-m4-secret-key-0123456789abcdefghijklmnop",
            "DATABASE_URL": f"sqlite+aiosqlite:///{(workdir / 'smoke.db').as_posix()}",
            "UPLOAD_DIR": (workdir / "uploads").as_posix(),
            "CHROMA_DIR": (workdir / "chroma").as_posix(),
            "CHROMA_COLLECTION": "smoke_m4",
            # 和风：Host 指向本地假上游，密钥随便给（上游一律 401）
            "WEATHER_API_HOST": fake_base,
            "WEATHER_API_KEY": "smoke-invalid-key",
        }
    )
    if args.real_llm:
        print(f"{INFO} LLM 配置沿用 .env（未覆盖 LLM_BASE_URL / LLM_MODEL / LLM_API_KEY）")
    else:
        # 真实 .env 的值由环境变量覆盖：pydantic-settings 的优先级是「环境变量 > .env」
        env.update(
            {
                "LLM_BASE_URL": f"{fake_base}/v1",
                "LLM_API_KEY": "smoke-fake-key",
                "LLM_MODEL": "smoke-fake-model",
            }
        )
    if args.placeholder_embedding:
        env["EMBEDDING_PROVIDER"] = "placeholder"
    else:
        env["EMBEDDING_PROVIDER"] = "fastembed"
        env["EMBEDDING_DIM"] = "512"

    check_prompts_consistent(checks)

    print("\n[0] 用 Alembic 在临时目录建库")
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
        fake_server.shutdown()
        return 1
    print(f"{PASS} 迁移已应用（临时库 {workdir / 'smoke.db'}）")

    # 服务日志写文件而不是 PIPE（见 smoke_m3.py 的说明：PIPE 会在启动期阻塞子进程）
    log_path = workdir / "server.log"
    log_file = log_path.open("w", encoding="utf-8")
    server_ready = False
    proc = subprocess.Popen(
        [
            str(PYTHON), "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    use_fake = not args.real_llm

    def calls() -> int:
        return FakeUpstream.stats["chat_calls"]

    try:
        print("\n[1] 启动服务并等待就绪")
        health = wait_health(base, proc, log_path)
        server_ready = True
        data = health.get("data", health)
        checks.check("存活检查返回 200 且服务名正确", data.get("app") == "weather-agent", str(data))
        checks.check("里程碑标记为 M6", data.get("milestone") == "M6", str(data.get("milestone")))

        ready = httpx.get(f"{base}/api/v1/health/ready", timeout=30.0)
        components = ready.json().get("data", {}).get("components", {})
        print(f"{INFO} 组件状态 = {json.dumps(components, ensure_ascii=False)}")
        checks.check(
            "就绪检查：database 必需组件可用",
            components.get("database", {}).get("ready") is True,
            str(components.get("database")),
        )
        checks.check(
            "就绪检查：vector_store 可打开",
            components.get("vector_store", {}).get("ready") is True,
            str(components.get("vector_store")),
        )
        checks.check(
            "就绪检查：llm 组件如实报告已配置（且不发起真实调用）",
            components.get("llm", {}).get("configured") is True,
            str(components.get("llm")),
        )

        with httpx.Client(base_url=base, timeout=60.0) as client:
            print("\n[2] 注册并登录 A / B 两个账号")
            account_a = {"username": "smoke_m4_a", "email": "smoke_m4_a@example.com", "password": "pw12345678"}
            account_b = {"username": "smoke_m4_b", "email": "smoke_m4_b@example.com", "password": "pw12345678"}
            for account in (account_a, account_b):
                client.post("/api/v1/auth/register", json=account)
            token_a = self_login(client, account_a)
            token_b = self_login(client, account_b)
            checks.check("两个账号都能登录并拿到 access_token", bool(token_a and token_b))
            headers_a = {"Authorization": f"Bearer {token_a}"}
            headers_b = {"Authorization": f"Bearer {token_b}"}

            print("\n[3] 未登录访问问答应被拒")
            anon = client.post("/api/v1/chat", json={"question": "你好"})
            checks.check("未登录返回 401", anon.status_code == 401, f"实际 {anon.status_code}")

            print("\n[4] 闲聊意图（不带任何资料）非流式问答")
            before = calls()
            first = client.post(
                "/api/v1/chat",
                headers=headers_a,
                json={"question": "你好，请问你是谁？", "stream": False},
            )
            checks.check("非流式返回 200", first.status_code == 200, first.text[:300])
            payload = first.json()["data"]
            print(f"{INFO} 意图={payload['intent']}({payload['intent_source']}) 回答={payload['answer'][:80]!r}")
            # 「你好」是纯问候 → 规则命中 chat，不会为分类多花一次模型调用
            checks.check(
                "闲聊被规则判为 chat（来源 rule，不为分类调用模型）",
                payload["intent"] == "chat" and payload["intent_source"] == "rule",
                f"{payload['intent']}/{payload['intent_source']}",
            )
            checks.check("闲聊问题拿到了模型回答", bool(payload["answer"].strip()), payload["answer"][:120])
            checks.check("闲聊不注入任何依据", payload["sources"] == [], str(payload["sources"]))
            checks.check("闲聊不是短路路径", payload["short_circuit"] is False, str(payload["short_circuit"]))
            if use_fake:
                checks.check(
                    "假模型如实报告：prompt 里既无参考资料也无天气数据，只有 2 条消息（系统+问题）",
                    "消息数=2" in payload["answer"]
                    and "参考资料=无" in payload["answer"]
                    and "天气数据=无" in payload["answer"],
                    payload["answer"],
                )
            checks.check(
                "模型调用次数增加了（这一轮确实走了模型）",
                (calls() > before) if use_fake else True,
                f"before={before} after={calls()}",
            )
            conversation_id = payload["conversation_id"]

            print("\n[5] 同一会话追问一次（验证多轮历史被带上）")
            second = client.post(
                "/api/v1/chat",
                headers=headers_a,
                json={"question": "那我们继续聊点别的吧，随便说两句。", "conversation_id": conversation_id},
            )
            payload2 = second.json()["data"]
            checks.check(
                "追问复用同一个会话（conversation_id 不变）",
                payload2["conversation_id"] == conversation_id,
                f"{payload2['conversation_id']} vs {conversation_id}",
            )
            if use_fake:
                # 第 1 轮 = 系统 + 本轮问题 = 2 条；第 2 轮多出「用户+助手」两条历史 = 4 条。
                # 这一条断言就是"多轮上下文真的接上了"的直接证据。
                checks.check(
                    "第二轮 prompt 里带上了历史（消息数 2 → 4）",
                    "消息数=4" in payload2["answer"],
                    payload2["answer"],
                )

            print("\n[6] 知识库为空时问制度问题 → 必须短路为固定文案，且模型一次都不被调用")
            # 这里刻意在【上传任何文档之前】发问：此时向量库里一条可检索的片段都没有，
            # 「检索为空」是 100% 确定的，不依赖阈值调得多准。
            before = calls()
            empty = client.post(
                "/api/v1/chat",
                headers=headers_a,
                json={
                    "question": "公司的差旅住宿标准是怎么规定的？",
                    "conversation_id": conversation_id,
                },
            ).json()["data"]
            checks.check(
                "判为 knowledge 意图",
                empty["intent"] == "knowledge",
                f"{empty['intent']}/{empty['intent_source']}",
            )
            checks.check(
                "回答是固定文案（未让模型自由发挥）",
                empty["answer"] == NO_KNOWLEDGE_ANSWER,
                repr(empty["answer"][:120]),
            )
            checks.check("标记为短路路径", empty["short_circuit"] is True, str(empty["short_circuit"]))
            if use_fake:
                # ⚠️ 这是全脚本分量最重的一条：把"不编造"从提示词请求升级成代码保证。
                # 应用自己无法自证"没调用模型"（没调用就不会留下任何痕迹），
                # 只有站在上游一侧数请求次数才能证明。
                checks.check(
                    "短路时上游生成接口调用次数为 0（模型没有机会编造）",
                    calls() == before,
                    f"before={before} after={calls()}",
                )

            print("\n[7] 上传一份文档并等待真实向量化入库")
            doc_text = (
                "关于差旅报销的规定。员工出差前须在系统提交申请并经直属主管审批。"
                "住宿标准按城市分级：一线城市住宿费每晚上限为六百元，二线城市为四百元，超出部分自理。"
                "系统故障分级中，一级故障须在十五分钟内响应，二级故障须在一小时内响应。"
                "客户投诉须在二十四小时内首次响应，三个工作日内给出处理方案。"
            ) * 12
            created = client.post(
                "/api/v1/knowledge/documents",
                headers=headers_a,
                files={"file": ("差旅与响应制度.txt", doc_text.encode(), "text/plain")},
            )
            checks.check("上传返回 202", created.status_code == 202, created.text[:300])
            document_id = created.json()["data"]["document"]["id"]
            status = "pending"
            deadline = time.time() + 60
            while time.time() < deadline:
                detail = client.get(
                    f"/api/v1/knowledge/documents/{document_id}", headers=headers_a
                ).json()["data"]
                status = detail["status"]
                if status in ("ready", "failed"):
                    break
                time.sleep(0.3)
            checks.check(
                "文档入库完成且切分非空",
                status == "ready" and detail["chunk_count"] > 0,
                f"status={status} chunks={detail.get('chunk_count')} err={detail.get('error_message')}",
            )

            print("\n[8] 有资料后同样的问题 → 走检索 + 模型，并且来源被标注")
            answered = client.post(
                "/api/v1/chat",
                headers=headers_a,
                json={
                    "question": "一线城市住宿费每晚上限是多少元？",
                    "conversation_id": conversation_id,
                },
            ).json()["data"]
            checks.check(
                "命中知识库后不再是固定文案",
                answered["answer"] != NO_KNOWLEDGE_ANSWER and answered["answer"].strip() != "",
                repr(answered["answer"][:120]),
            )
            checks.check("不再标记为短路", answered["short_circuit"] is False, str(answered["short_circuit"]))
            knowledge_sources = [item for item in answered["sources"] if item["type"] == "knowledge"]
            print(f"{INFO} 依据 = {json.dumps(knowledge_sources[:2], ensure_ascii=False)[:300]}")
            checks.check("依据里带回知识库片段", len(knowledge_sources) >= 1, str(answered["sources"]))
            if knowledge_sources:
                top = knowledge_sources[0]
                checks.check(
                    "依据带来源标注（文件名#第N段）",
                    "#第" in top["label"] and "差旅与响应制度" in top["label"],
                    str(top["label"]),
                )
                checks.check(
                    "依据带相似度分数（越大越相关）",
                    isinstance(top["score"], (int, float)) and top["score"] > 0,
                    str(top["score"]),
                )
                checks.check(
                    "依据带 document_id 可回溯到原文档",
                    top.get("document_id") == document_id,
                    f"{top.get('document_id')} vs {document_id}",
                )
            if use_fake:
                checks.check(
                    "检索到的片段真的进了 prompt（假模型报告参考资料=有）",
                    "参考资料=有" in answered["answer"],
                    answered["answer"],
                )

            print("\n[9] 跨用户隔离：B 问同一个问题，检索必须为空")
            # B 的知识库是空的。如果检索漏了 `where={"user_id": ...}`，
            # 这里会命中 A 的文档片段并给出回答——所以这条断言就是 M3-Q4 在检索侧的兑现点。
            other = client.post(
                "/api/v1/chat",
                headers=headers_b,
                json={"question": "一线城市住宿费每晚上限是多少元？"},
            ).json()["data"]
            checks.check(
                "B 检索不到 A 的文档（回答为固定文案）",
                other["answer"] == NO_KNOWLEDGE_ANSWER,
                repr(other["answer"][:160]),
            )
            checks.check(
                "B 的回答里不含任何知识库依据",
                [item for item in other["sources"] if item["type"] == "knowledge"] == [],
                str(other["sources"]),
            )

            print("\n[10] 天气取数失败 → 短路为固定文案，模型同样一次都不被调用")
            before = calls()
            weather = client.post(
                "/api/v1/chat",
                headers=headers_a,
                json={
                    "question": "明天上海天气怎么样，需要带伞吗？",
                    "conversation_id": conversation_id,
                },
            ).json()["data"]
            checks.check("判为 weather 意图", weather["intent"] == "weather", str(weather["intent"]))
            checks.check(
                "回答是天气兜底固定文案",
                weather["answer"] == WEATHER_UNAVAILABLE_ANSWER,
                repr(weather["answer"][:160]),
            )
            checks.check("标记为短路路径", weather["short_circuit"] is True, str(weather["short_circuit"]))
            if use_fake:
                checks.check(
                    "天气失败短路时上游生成接口调用次数为 0",
                    calls() == before,
                    f"before={before} after={calls()}",
                )
                checks.check(
                    "确实尝试过请求天气上游（假上游收到了请求）",
                    FakeUpstream.stats["weather_calls"] > 0,
                    str(FakeUpstream.stats),
                )

            print("\n[11] SSE 流式：事件顺序、增量拼接、用量统计")
            stream_started = time.perf_counter()
            with client.stream(
                "POST",
                "/api/v1/chat",
                headers=headers_a,
                json={
                    "question": "你好，请随便说一句话。",
                    "stream": True,
                    # 刻意复用同一个会话：流式路径会自建数据库会话，这里顺带验证
                    # 「在已有会话上流式追问」也不会因为会话换手而出问题
                    "conversation_id": conversation_id,
                },
            ) as response:
                checks.check(
                    "流式响应 Content-Type 为 text/event-stream",
                    response.headers.get("content-type", "").startswith("text/event-stream"),
                    response.headers.get("content-type", ""),
                )
                checks.check(
                    "声明了防缓存与禁止代理缓冲（否则流式会被攒成一坨）",
                    response.headers.get("cache-control") == "no-cache"
                    and response.headers.get("x-accel-buffering") == "no",
                    str(dict(response.headers)),
                )
                raw = "".join(response.iter_text())
            elapsed = time.perf_counter() - stream_started
            events = parse_sse(raw)
            names = [name for name, _ in events]
            print(f"{INFO} SSE 事件序列 = {names}（耗时 {elapsed:.2f}s）")

            checks.check("至少收到 meta/delta/done 三类事件", set(names) >= {"meta", "delta", "done"}, str(names))
            checks.check("第一个事件是 meta", names[0] == "meta", str(names[:3]))
            checks.check("最后一个事件是 done", names[-1] == "done", str(names[-3:]))
            delta_count = names.count("delta")
            checks.check("token 是逐段推送的（delta 事件多于 1 个）", delta_count > 1, f"delta 数 = {delta_count}")
            checks.check(
                "事件顺序严格是 meta → delta* → done",
                names == ["meta"] + ["delta"] * delta_count + ["done"],
                str(names),
            )

            meta = events[0][1]
            done = events[-1][1]
            checks.check("meta 里带 conversation_id", isinstance(meta.get("conversation_id"), int), str(meta))
            joined = "".join(item["text"] for name, item in events if name == "delta")
            checks.check(
                "delta 拼接结果与 done.answer 完全一致",
                joined == done["answer"] and bool(joined.strip()),
                f"delta={joined[:60]!r} done={str(done.get('answer'))[:60]!r}",
            )
            checks.check("done 里带 message_id", isinstance(done.get("message_id"), int), str(done))
            usage = done.get("usage") or {}
            print(f"{INFO} 流式用量 = {usage}")
            checks.check(
                "流式路径也拿到了 token 用量（stream_usage=True 这一开关生效）",
                isinstance(usage.get("total_tokens"), int) and usage["total_tokens"] > 0,
                str(done.get("usage")),
            )

            print("\n[12] 会话列表 / 历史消息 / 删除")
            listing = client.get("/api/v1/conversations", headers=headers_a).json()["data"]
            checks.check("会话列表返回 200 且有会话", listing["total"] >= 1, str(listing.get("total")))
            checks.check(
                "列表首条是最近活跃的会话（add_message 顺手更新了 updated_at）",
                bool(listing["items"]) and listing["items"][0]["id"] == conversation_id,
                f"首条={listing['items'][0]['id'] if listing['items'] else None} 期望={conversation_id}",
            )
            checks.check(
                "会话标题由首条消息派生（截前 20 字）",
                listing["items"][0]["title"].startswith("你好，请问你是谁？"),
                str(listing["items"][0]["title"]),
            )

            history = client.get(
                f"/api/v1/conversations/{conversation_id}/messages", headers=headers_a
            ).json()["data"]
            print(f"{INFO} 会话消息 = {[(m['role'], m['content'][:22]) for m in history['items']]}")
            checks.check("历史消息里有问答成对出现", history["total"] >= 4, str(history.get("total")))
            checks.check(
                "助手消息里存了运行痕迹（tool_calls 里能看到意图与短路标记）",
                any(
                    m["role"] == "assistant" and isinstance(m["tool_calls"], dict)
                    and "intent" in m["tool_calls"]
                    for m in history["items"]
                ),
                str([m["role"] for m in history["items"]]),
            )
            checks.check(
                "历史消息不回传 stored_path 之类的内部字段",
                all("stored_path" not in m for m in history["items"]),
                str(sorted(history["items"][0]) if history["items"] else []),
            )

            print("\n[13] 会话越权：B 不能读到或删掉 A 的会话")
            stolen = client.get(
                f"/api/v1/conversations/{conversation_id}/messages", headers=headers_b
            )
            checks.check("B 读 A 的会话返回 404", stolen.status_code == 404, f"实际 {stolen.status_code}")
            stolen_chat = client.post(
                "/api/v1/chat",
                headers=headers_b,
                json={"question": "继续这个话题。", "conversation_id": conversation_id},
            )
            checks.check(
                "B 拿 A 的 conversation_id 提问返回 404",
                stolen_chat.status_code == 404,
                f"实际 {stolen_chat.status_code}",
            )
            stolen_delete = client.delete(f"/api/v1/conversations/{conversation_id}", headers=headers_b)
            checks.check(
                "B 删 A 的会话返回 404",
                stolen_delete.status_code == 404,
                f"实际 {stolen_delete.status_code}",
            )
            checks.check(
                "B 的会话列表里看不到 A 的会话",
                all(item["id"] != conversation_id for item in
                    client.get("/api/v1/conversations", headers=headers_b).json()["data"]["items"]),
                "B 看得到 A 的会话",
            )

            print("\n[14] 删除会话应连消息一起清掉")
            removed = client.delete(f"/api/v1/conversations/{conversation_id}", headers=headers_a)
            checks.check("A 删自己的会话返回 200", removed.status_code == 200, removed.text[:200])
            gone = client.get(f"/api/v1/conversations/{conversation_id}/messages", headers=headers_a)
            checks.check("删除后读该会话返回 404", gone.status_code == 404, f"实际 {gone.status_code}")
            after_delete = client.get("/api/v1/conversations", headers=headers_a).json()["data"]
            checks.check(
                "被删会话从列表中消失（A 全程只用了这一个会话，故列表应清空）",
                all(item["id"] != conversation_id for item in after_delete["items"])
                and after_delete["total"] == 0,
                f"total={after_delete['total']} items={[item['id'] for item in after_delete['items']]}",
            )

    finally:
        print("\n[15] 关闭服务")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_file.close()
        if not server_ready:
            tail = read_log(log_path)
            if tail != "(无日志)":
                print(f"{INFO} 服务日志尾部：\n{tail}")
        fake_server.shutdown()
        fake_server.server_close()

        print("\n[16] 清理临时目录")
        shutil.rmtree(workdir, ignore_errors=True)
        print(f"{INFO} 已清理 {workdir}")

    print("\n" + "=" * 72)
    print(f"结果：{checks.passed} 项通过，{checks.failed} 项失败")
    if use_fake:
        print(f"{INFO} 假上游统计 = {FakeUpstream.stats}")
    print("=" * 72)
    return 1 if checks.failed else 0


def self_login(client: httpx.Client, account: dict) -> str:
    """登录并返回 access_token（注册已在调用方完成）。"""
    response = client.post(
        "/api/v1/auth/login",
        json={"username": account["username"], "password": account["password"]},
    )
    if response.status_code != 200:
        return ""
    return str(response.json()["data"]["access_token"])


if __name__ == "__main__":
    raise SystemExit(main())

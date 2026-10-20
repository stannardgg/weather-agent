"""【M8】打包产物（exe）验收脚本。

打包出 exe 之后**必须真的跑一遍**才算交付完成——静态检查与「能启动」都不足以说明问题。
本脚本在一个干净的临时目录里跑 exe，逐项验证真实链路，最后自动收尾。

用法：
    .venv/Scripts/python.exe scripts/test_exe.py
    .venv/Scripts/python.exe scripts/test_exe.py --exe dist/瓜葛天气助手.exe --keep

检查项（任一项失败即以非零码退出）：

  1. exe 能在干净目录启动，自动补齐 .env / data / logs
  2. 数据库迁移真的跑完（能注册用户即证明表存在）
  3. 后端 `/health` 与 `/health/ready` 正常
  4. 前端首页与 Streamlit 静态资源可访问（防白屏）
  5. 前后端都只监听 127.0.0.1（防局域网暴露）
  6. 完整知识库链路：注册 → 登录 → 上传 → 解析/切分/向量化/写向量库
     （这一项专门覆盖冻结环境下最容易炸的 chromadb）
  7. 通用问答：调用 `/chat`，确认 LLM 链路在冻结环境可用（缺密钥时自动跳过）

为什么只用标准库：这个脚本本身不该引入依赖，否则「验证工具跑不起来」又会成为一个新问题。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXE = PROJECT_ROOT / "dist" / "瓜葛天气助手.exe"

# 默认端口。可用 `--backend-port` / `--frontend-port` 覆盖——这一点是必须的：
# 桌面版经常是「用户已经双击开着一个」，此时验收脚本若死用 8000/8501 就会
# 抢不到端口而误报失败（实际是被测程序没问题，是验收脚本自己没有容身之处）。
BACKEND_PORT = 8000
FRONTEND_PORT = 8501
BACKEND = ""
FRONTEND = ""


def _configure_ports(backend_port: int, frontend_port: int) -> None:
    """把端口写进模块级变量，并刷新由它们拼出来的两个基址。"""
    global BACKEND_PORT, FRONTEND_PORT, BACKEND, FRONTEND
    BACKEND_PORT = backend_port
    FRONTEND_PORT = frontend_port
    BACKEND = f"http://127.0.0.1:{backend_port}/api/v1"
    FRONTEND = f"http://127.0.0.1:{frontend_port}"


_configure_ports(BACKEND_PORT, FRONTEND_PORT)

# 验收脚本会在临时目录里创建用户，用户名带时间戳避免与历史数据冲突。
# ⚠️ 用户名有严格字符限制（^[A-Za-z0-9_]+$，见 schemas/user.py），不能拿邮箱当用户名，
#    否则会得到一个 422「参数校验失败」——这是验收脚本自身的坑，不是被测程序的问题。
_username = f"acc{int(time.time())}"
_email = f"{_username}@example.com"

_pass_count = 0
_fail_count = 0


def check(label: str, ok: bool, detail: str = "") -> bool:
    global _pass_count, _fail_count
    if ok:
        _pass_count += 1
        print(f"  [通过] {label}" + (f"  {detail}" if detail else ""))
    else:
        _fail_count += 1
        print(f"  [失败] {label}" + (f"  {detail}" if detail else ""))
    return ok


def http(method: str, url: str, *, token: str | None = None, data: bytes | None = None,
         headers: dict[str, str] | None = None, timeout: float = 30.0) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=data, method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def get_json(method: str, url: str, payload: dict | None = None, token: str | None = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode() if payload is not None else None
    status, raw = http(method, url, token=token, data=body,
                       headers={"Content-Type": "application/json"} if body else None)
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, {"_raw": raw[:200].decode("utf-8", "replace")}


def wait_port(host: str, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(1.0)
            if sock.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.5)
    return False


def listening_addresses(port: int) -> set[str]:
    """返回该端口实际监听的主机地址。用 netstat 而不是尝试连接：
    我们要确认的是「没有监听 0.0.0.0」，连 127.0.0.1 是连不出这个信息的。

    编码上要注意：中文 Windows 下 netstat 的输出是 GBK，直接 text=True 会让
    子进程读取线程抛 UnicodeDecodeError、返回 None —— 表现为「验收脚本自己崩了」，
    而不是被测程序的问题。所以这里拿 bytes 再手工容错解码。
    """
    addresses: set[str] = set()
    try:
        completed = subprocess.run(["netstat", "-ano"], capture_output=True, timeout=20)
    except Exception:  # noqa: BLE001
        return addresses
    output = (completed.stdout or b"").decode("utf-8", "replace")
    if "LISTENING" not in output.upper():
        output = (completed.stdout or b"").decode("gbk", "replace")
    for line in output.splitlines():
        if "LISTENING" not in line.upper():
            continue
        match = re.search(r"(?P<addr>[\d.:\[\]]+):(?P<port>\d+)\s", line)
        if match and int(match.group("port")) == port:
            addresses.add(match.group("addr"))
    return addresses


def multipart(fields: dict[str, str], filename: str, content: bytes) -> tuple[bytes, str]:
    """手工拼一个 multipart/form-data 请求体（标准库里没有现成的实现）。"""
    boundary = "----acceptance" + str(int(time.time() * 1000))
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: text/plain\r\n\r\n".encode()
    )
    parts.append(content)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def seed_models(run_dir: Path) -> None:
    """把本机已下载的向量模型拷进测试目录。

    这是刻意的：验收关注的不是「能不能联网下载模型」，而是「冻结环境能否跑通向量化」。
    带上模型可以让验收离线、可重复、且更快。
    """
    source = PROJECT_ROOT / "data" / "models" / "fastembed"
    if source.exists():
        target = run_dir / "data" / "models"
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target / "fastembed", dirs_exist_ok=True)
        print(f"  (已内置向量模型缓存，避免验收时联网下载)")


def main() -> int:
    parser = argparse.ArgumentParser(description="验收打包后的 exe")
    parser.add_argument("--exe", default=str(DEFAULT_EXE), help="待验收的 exe 路径")
    parser.add_argument("--keep", action="store_true", help="保留临时目录（默认结束即删除）")
    parser.add_argument("--timeout", type=float, default=180.0, help="等待服务就绪的秒数")
    parser.add_argument("--backend-port", type=int, default=BACKEND_PORT,
                        help="后端端口（默认 8000；被占用时换一个）")
    parser.add_argument("--frontend-port", type=int, default=FRONTEND_PORT,
                        help="前端端口（默认 8501；被占用时换一个）")
    args = parser.parse_args()

    # 端口必须在启动 exe 之前定下来：模块级 BACKEND / FRONTEND 基址也要跟着改。
    _configure_ports(args.backend_port, args.frontend_port)

    exe = Path(args.exe).resolve()
    if not exe.exists():
        print(f"[错误] 找不到 exe：{exe}\n       先执行 scripts/build_exe.py", file=sys.stderr)
        return 2

    run_dir = Path(tempfile.mkdtemp(prefix="weather-agent-accept-"))
    print(f"验收目录：{run_dir}")
    shutil.copy2(exe, run_dir / exe.name)
    seed_models(run_dir)

    env = dict(os.environ)
    env["WEATHER_AGENT_NO_BROWSER"] = "1"  # 验收时不要弹浏览器
    # launcher.py 读这两个环境变量决定监听端口——这样验收可以在非默认端口上跑，
    # 与用户已双击开着的实例和平共处（不去动用户的进程）。
    env["BACKEND_PORT"] = str(args.backend_port)
    env["FRONTEND_PORT"] = str(args.frontend_port)
    log_path = run_dir / "accept.log"
    log_file = open(log_path, "w", encoding="utf-8", errors="replace")

    print(f"启动 {exe.name} …（首次运行需解包，请耐心等待）")
    started = time.monotonic()
    proc = subprocess.Popen([str(run_dir / exe.name)], cwd=run_dir, env=env,
                            stdout=log_file, stderr=subprocess.STDOUT)

    try:
        print("\n--- 1. 服务启动 ---")
        frontend_up = wait_port("127.0.0.1", FRONTEND_PORT, args.timeout)
        check(f"前端 {FRONTEND_PORT} 端口就绪", frontend_up,
              f"耗时 {time.monotonic() - started:.0f}s")
        backend_up = wait_port("127.0.0.1", BACKEND_PORT, 30)
        check(f"后端 {BACKEND_PORT} 端口就绪", backend_up)
        if not (frontend_up and backend_up):
            print("\n[失败] 服务未就绪，控制台输出：")
            print(log_path.read_text(encoding="utf-8", errors="replace")[-4000:])
            return 1

        print("\n--- 2. 数据目录自动初始化 ---")
        check("生成了 .env", (run_dir / ".env").exists())
        check("生成了 data/ 目录", (run_dir / "data").is_dir())
        check("生成了 logs/ 目录", (run_dir / "logs").is_dir())

        print("\n--- 3. 后端健康检查 ---")
        status, body = get_json("GET", f"{BACKEND}/health")
        check("/health 返回 200", status == 200, f"code={body.get('code')}")
        status, body = get_json("GET", f"{BACKEND}/health/ready")
        ready = body.get("data", {}).get("ready")
        check("/health/ready 就绪", status == 200 and ready is True, f"ready={ready}")

        print("\n--- 4. 前端页面（防白屏）---")
        status, _ = http("GET", f"{FRONTEND}/")
        check("前端首页 200", status == 200)
        status, _ = http("GET", f"{FRONTEND}/index.html")
        check("Streamlit 静态资源 index.html 200（缺静态资源会白屏）", status == 200)

        print("\n--- 5. 只监听本机（防局域网暴露）---")
        fe = listening_addresses(FRONTEND_PORT)
        be = listening_addresses(BACKEND_PORT)
        check("前端未监听 0.0.0.0/::", not (fe & {"0.0.0.0", "::"}), f"实际 {sorted(fe)}")
        check("后端未监听 0.0.0.0/::", not (be & {"0.0.0.0", "::"}), f"实际 {sorted(be)}")

        print("\n--- 6. 知识库全链路（冻结环境下的 chromadb 试金石）---")
        status, body = get_json("POST", f"{BACKEND}/auth/register",
                                {"username": _username, "email": _email, "password": "Passw0rd!123"})
        check("注册成功（证明数据库迁移已生效）", status in (200, 201), f"HTTP {status}")

        status, body = get_json("POST", f"{BACKEND}/auth/login",
                                {"username": _username, "password": "Passw0rd!123"})
        token = (body.get("data") or {}).get("access_token")
        check("登录拿到 token", bool(token))

        if token:
            payload, content_type = multipart(
                {}, "acceptance.txt",
                "公司报销标准：差旅住宿费一线城市每晚上限 600 元，其他城市 400 元。\n".encode(),
            )
            status, raw = http("POST", f"{BACKEND}/knowledge/documents", token=token,
                               data=payload, headers={"Content-Type": content_type})
            document = (json.loads(raw).get("data") or {}).get("document", {}) if raw else {}
            doc_id = document.get("id")
            # 202 Accepted 是预期的：入库是后台任务，接口只负责受理
            check("上传文档受理", 200 <= status < 300 and doc_id is not None, f"HTTP {status} doc_id={doc_id}")

            # 入库是后台任务：解析 → 切分 → 向量化 → 写向量库，需要给它时间
            final = "pending"
            for _ in range(40):
                time.sleep(1.5)
                status, body = get_json("GET", f"{BACKEND}/knowledge/documents", token=token)
                items = (body.get("data") or {}).get("items") or []
                match = [item for item in items if item.get("id") == doc_id]
                if match:
                    final = match[0].get("status")
                    if final in ("ready", "failed"):
                        break
            error = ""
            if final == "failed":
                status, body = get_json("GET", f"{BACKEND}/knowledge/documents/{doc_id}", token=token)
                error = (body.get("data") or {}).get("error_message", "")
            check("文档入库状态 = ready（含 chromadb 写入与 fastembed 向量化）",
                  final == "ready", f"status={final} {error}")

        print("\n--- 7. 通用问答（LLM 链路）---")
        # .env 里没配 LLM 密钥时这一项没有意义，直接跳过而不是判失败
        env_text = (run_dir / ".env").read_text(encoding="utf-8", errors="replace") if (run_dir / ".env").exists() else ""
        llm_configured = bool(re.search(r"^LLM_API_KEY=\S+", env_text, re.MULTILINE))
        if not llm_configured:
            print("  [跳过] .env 未配置 LLM_API_KEY")
        else:
            # 两条路径都要验：默认（非流式）返回 JSON 信封，stream=true 才走 SSE。
            # 只测其中一条会漏掉另一条的冻结环境问题（两者的代码路径并不相同）。
            status, raw = http("POST", f"{BACKEND}/chat", token=token,
                               data=json.dumps({"question": "用一句话说明什么是人工智能"}).encode(),
                               headers={"Content-Type": "application/json"}, timeout=120)
            try:
                answer = (json.loads(raw).get("data") or {}).get("answer") or ""
            except json.JSONDecodeError:
                answer = ""
            check("/chat 非流式返回正常（LLM 可用）", status == 200 and bool(answer.strip()),
                  f"HTTP {status}，回答 {len(answer)} 字")

            status, raw = http("POST", f"{BACKEND}/chat", token=token,
                               data=json.dumps(
                                   {"question": "用一句话说明什么是人工智能", "stream": True}
                               ).encode(),
                               headers={"Content-Type": "application/json"}, timeout=120)
            text = raw.decode("utf-8", "replace")
            # SSE 帧格式：`event: <name>\ndata: {...}\n\n`（见 app/api/v1/chat.py 的 sse_frame）。
            # 判「成功」要看是否收到收尾的 done 事件，且中途没有 error 事件——
            # 只看 HTTP 200 是不够的：流式响应一旦开始，状态码早就发出去了。
            answered = status == 200 and "event: done" in text and "event: error" not in text
            check("/chat 流式返回正常（SSE 收尾事件完整）", answered,
                  f"HTTP {status}，响应 {len(text)} 字节")

        print(f"\n{'=' * 62}")
        print(f"验收结果：通过 {_pass_count} 项，失败 {_fail_count} 项")
        print(f"{'=' * 62}")
        if _fail_count:
            print("\nexe 控制台输出（末尾 3000 字）：")
            print(log_path.read_text(encoding="utf-8", errors="replace")[-3000:])
        return 1 if _fail_count else 0

    finally:
        print("\n正在关闭 exe …")
        if proc.poll() is None:
            subprocess.call(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_file.close()
        if args.keep:
            print(f"已保留验收目录：{run_dir}")
        else:
            time.sleep(1.5)  # 等文件句柄释放，否则 Windows 上可能删不干净
            shutil.rmtree(run_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

"""【M8】桌面版启动器（PyInstaller 打包入口）。

这个文件只解决一件事：**把「开发者本机跑得起来」变成「双击一个 exe 就能用」**。
它不引入任何业务逻辑，只做启动期的编排，因此它不属于六层架构的任何一层。

## 为什么是「单进程双服务」而不是像 `scripts/serve.py` 那样起两个子进程

`scripts/serve.py` 用 `subprocess` 起两个 Python 子进程，这在有真实 `python.exe`
的环境里毫无问题。但打成 **PyInstaller 单文件（onefile）** 之后情况全变了：
`sys.executable` 指向的就是这个 exe 自己，每起一个「子进程」都要把整个
bundle（本项目含 onnxruntime/chromadb/streamlit，体积数百 MB）**重新解包一次**到
临时目录。三个进程 = 三次解包，冷启动会被拖到几分钟，完全不可接受。

所以这里的做法是：

- **后端**（FastAPI/uvicorn）：跑在一个 **守护线程** 里。
  uvicorn 的 `Server.serve()` 内部用 `capture_signals()` 包了一层，而该方法在
  非主线程会**直接跳过**信号注册（见 uvicorn/server.py），所以在线程里跑是安全的。
- **前端**（Streamlit）：跑在**主线程**里阻塞。这本来就是 `streamlit run` 的行为，
  最省事也最稳；退出时进程结束，守护线程随之释放，天然没有孤儿进程。

代价是「后端崩了前端还活着」这种情况需要自己盯（见 `_backend_failed`），
但换来的是**只解包一次**——对单文件打包来说这笔账非常划算。

## 路径处理（打包后最容易出错的地方）

所有配置里的路径都是相对的（`./data/weather_agent.db`、`./data/chroma`、
`./data/uploads`、`./logs`），pydantic 的 `env_file=".env"` 也是相对的。
它们**统一相对「当前工作目录」解析**，所以在启动最开始 `os.chdir(可写根目录)`
一次，后面所有相对路径就都对了——不需要去改 `config.py` 里任何一个值。

两类目录要分清：

| 目录 | 打包后位置 | 用途 |
| --- | --- | --- |
| 可写根目录 | exe 同级（不可写则退回 `%LOCALAPPDATA%`） | `.env`、`data/`、`logs/` |
| 只读资源目录 | onefile 的 `sys._MEIPASS` | `frontend/`、`alembic/`、`alembic.ini` |

## 迁移为什么在进程内跑

`python -m alembic upgrade head` 在打包后不可用（没有 `python.exe`，也没有
`-m` 的语义）。所以改成调用 alembic 的 Python API，并把 `script_location`
显式指向资源目录里的 `alembic/`。
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import shutil
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

# ⚠️ 必须在做任何重活之前调用。PyInstaller 打包后，只要程序内部用到 multiprocessing，
# 子进程会被重新引导执行本文件；freeze_support() 负责把这条引导路径接住。
# 现在虽然没用 multiprocessing，但第三方库（如某些日志/序列化库）可能会用，
# 提前放好是零成本的保险。
multiprocessing.freeze_support()

APP_DISPLAY_NAME = "瓜葛天气助手"

BACKEND_HOST = os.environ.get("BACKEND_HOST", "127.0.0.1")
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "8000"))
FRONTEND_HOST = os.environ.get("FRONTEND_ADDRESS", "127.0.0.1")
FRONTEND_PORT = int(os.environ.get("FRONTEND_PORT", "8501"))

# 后端启动失败时把异常记在这里，供主线程判断（子线程异常默认不会打断主线程）
_backend_error: list[BaseException] = []


# --------------------------------------------------------------------------- 路径


def is_frozen() -> bool:
    """是否运行在 PyInstaller 冻结环境里。"""
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """只读资源目录。

    onefile 模式：PyInstaller 会把 bundle 解包到 `sys._MEIPASS`（只读，随进程消失）。
    onedir / 源码运行：与可写根目录一致。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else Path(__file__).resolve().parent


def _is_writable(path: Path) -> bool:
    """真实地写一个探针文件来判断可写，而不是看权限位（Windows 上不可靠）。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
        return True
    except OSError:
        return False


def resolve_writable_root() -> Path:
    """确定可写根目录。

    优先用 exe 同级目录（绿色软件，数据就在旁边，方便备份和迁移）。
    若放在 `C:\\Program Files` 这类需要管理员权限的目录导致不可写，
    则退回到用户数据目录——宁可数据藏深一点，也不能启动即失败。
    """
    if not is_frozen():
        # 源码运行：项目根目录本来就是可写的
        return Path(__file__).resolve().parent

    exe_dir = Path(sys.executable).resolve().parent
    if _is_writable(exe_dir):
        return exe_dir

    fallback = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "GuageWeatherAgent"
    print(f"[提示] 程序目录不可写，数据改存到：{fallback}")
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def prepare_environment() -> Path:
    """把进程的工作目录、目录结构、`.env` 都准备好。返回可写根目录。"""
    base = resolve_writable_root()

    # 关键一步：切到可写根目录。之后所有 `./data/...`、`./logs`、`.env`
    # 以及 pydantic 的 env_file 都会相对这里解析，无需改动 config.py 的默认值。
    os.chdir(base)

    for sub in ("data", "data/chroma", "data/uploads", "data/models/fastembed", "logs"):
        (base / sub).mkdir(parents=True, exist_ok=True)

    # .env 落地：优先用 exe 同级已存在的（用户可自行编辑），
    # 否则从打包时内嵌的那份复制出来——这样首次运行就是「开箱可用」，
    # 同时用户又能看到并修改它（密钥不藏在二进制里这一点很重要）。
    env_path = base / ".env"
    if not env_path.exists():
        bundled = resource_dir() / ".env"
        if bundled.exists():
            try:
                shutil.copyfile(bundled, env_path)
                print(f"[初始化] 已生成配置文件：{env_path}")
            except OSError as exc:
                print(f"[警告] 无法写入 .env（{exc}），将只使用默认配置")

    # 前端通过它找后端；显式设置避免依赖代码里的默认值
    os.environ.setdefault("API_BASE_URL", f"http://{BACKEND_HOST}:{BACKEND_PORT}/api/v1")

    # 无控制台环境下 stdout 可能为 None，日志处理器会在写入时炸掉。
    # 这里统一兜底到日志文件（打包默认带控制台，属于额外保险）。
    if sys.stdout is None or sys.stderr is None:
        sink = open(base / "logs" / "desktop.log", "a", encoding="utf-8")  # noqa: SIM115
        if sys.stdout is None:
            sys.stdout = sink
        if sys.stderr is None:
            sys.stderr = sink

    return base


# --------------------------------------------------------------------------- 迁移


def run_migration() -> None:
    """应用数据库迁移（在启动服务之前同步完成，避免库里没表）。"""
    from alembic import command
    from alembic.config import Config

    from app.core.config import settings

    resources = resource_dir()
    config = Config(str(resources / "alembic.ini"))
    # 显式指定脚本位置与连接串，不依赖 alembic.ini 里的相对路径推算。
    # 连接串从 settings 取，保证「迁移用的库」和「应用跑的库」是同一个。
    config.set_main_option("script_location", str(resources / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")


# --------------------------------------------------------------------------- 后端


def _run_backend() -> None:
    """在守护线程里跑 uvicorn。异常记入 `_backend_error` 而不吞掉。"""
    try:
        import uvicorn

        from app.main import app as fastapi_app

        server = uvicorn.Server(
            uvicorn.Config(
                fastapi_app,
                host=BACKEND_HOST,
                port=BACKEND_PORT,
                log_level="info",
                access_log=False,  # 访问日志由应用中间件统一记录（见 app/main.py）
                loop="asyncio",  # 明确指定，避免打包后 uvloop 探测出意外结果
            )
        )
        # 非主线程里 uvicorn 本来就会跳过信号注册；这里再兜一层，
        # 防止将来换版本后它在主线程执行时抢走 Streamlit 需要的 Ctrl+C。
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        asyncio.run(server.serve())
    except BaseException as exc:  # noqa: BLE001 —— 要把所有异常都带回主线程
        _backend_error.append(exc)
        print(f"[错误] 后端启动失败：{type(exc).__name__}: {exc}", file=sys.stderr)


def _wait_backend_ready(timeout: float = 180.0) -> bool:
    """轮询存活检查接口，直到后端就绪或超时。

    超时给得比较宽（180s）：单文件 exe 首次运行要解包数百 MB，
    之后 FastAPI 还要建连接、跑一次启动补偿，冷启动确实不快。
    """
    url = f"http://{BACKEND_HOST}:{BACKEND_PORT}/api/v1/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _backend_error:
            return False
        try:
            with urlopen(url, timeout=2) as response:  # noqa: S310 —— 本机固定地址
                if response.status == 200:
                    return True
        except (URLError, OSError):
            time.sleep(0.4)
    return False


# --------------------------------------------------------------------------- 前端

# Streamlit 的启动选项。键名是「点分」的配置名，与 config.toml 里的一致。
#
# 逐项理由：
# - headless：由我们自己开浏览器（等端口真的通了再开），不让它开两次。
# - fileWatcherType=none：冻结环境里 watchdog 不可靠，且打包后的代码是只读的，
#   热重载本来也无意义，关掉可以省掉一堆线程与报错。
# - enableXsrfProtection / enableCORS 关闭：与线上部署（deploy_entry.sh）保持一致。
#   桌面版只监听 127.0.0.1，风险面极小；开着反而容易因为 Origin 判定
#   让页面卡在 "Please wait..." 转圈——这个坑在线上部署时已经踩过一次。
# - gatherUsageStats=false：打包的桌面软件不该向外发使用统计。
# - developmentMode=false：关闭开发模式，避免它去找开发态的静态资源。
_FRONTEND_FLAG_OPTIONS: dict[str, object] = {
    "server.port": FRONTEND_PORT,
    "server.address": FRONTEND_HOST,
    "server.headless": True,
    "server.fileWatcherType": "none",
    "server.enableXsrfProtection": False,
    "server.enableCORS": False,
    "browser.gatherUsageStats": False,
    "global.developmentMode": False,
}


def _open_browser_when_ready() -> None:
    """等前端端口真正能连上再开浏览器。

    直接 sleep 固定时长在慢机器上会开出「无法访问」的页面，用户体验很差。
    """
    if os.environ.get("WEATHER_AGENT_NO_BROWSER") == "1":
        # 自动化测试 / 服务器场景不需要弹浏览器
        return
    url = f"http://{FRONTEND_HOST}:{FRONTEND_PORT}"
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2) as response:  # noqa: S310
                if response.status < 500:
                    break
        except (URLError, OSError):
            time.sleep(0.5)
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 —— 没有默认浏览器时不该影响服务本身
        print(f"[提示] 请手动在浏览器打开：{url}")


def _run_frontend() -> None:
    """在主线程阻塞运行 Streamlit（行为等同 `streamlit run`）。"""
    from streamlit.web import bootstrap

    script = resource_dir() / "frontend" / "app.py"
    if not script.exists():
        raise FileNotFoundError(f"找不到前端脚本：{script}")

    # ⚠️ 必须显式调用 load_config_options 应用这些选项。
    # `streamlit run` 是「CLI 先调 load_config_options 应用命令行选项，再调 bootstrap.run」，
    # 而 bootstrap.run 内部**只注册配置文件监听器、不应用选项**。
    # 所以只把字典传给 bootstrap.run 是无效的——这是个不报错、只默默用默认值的坑：
    # 表现为 server.address 被忽略、依然监听 0.0.0.0（局域网可访问）。
    bootstrap.load_config_options(flag_options=_FRONTEND_FLAG_OPTIONS)
    bootstrap.run(str(script), False, [], _FRONTEND_FLAG_OPTIONS)


# --------------------------------------------------------------------------- 主流程


def main() -> int:
    print("=" * 58)
    print(f"  {APP_DISPLAY_NAME}（桌面版）")
    print("=" * 58)

    base = prepare_environment()
    print(f"  数据目录：{base}")

    try:
        print("[1/3] 应用数据库迁移…")
        run_migration()
    except Exception as exc:  # noqa: BLE001
        print(f"[错误] 迁移失败，已终止启动：{type(exc).__name__}: {exc}", file=sys.stderr)
        print("       请检查 .env 中的 DATABASE_URL 与 SECRET_KEY。", file=sys.stderr)
        return 1

    print(f"[2/3] 启动后端服务（http://{BACKEND_HOST}:{BACKEND_PORT}）…")
    threading.Thread(target=_run_backend, name="backend", daemon=True).start()

    if not _wait_backend_ready():
        if _backend_error:
            print(f"[错误] 后端未能就绪：{_backend_error[0]}", file=sys.stderr)
        else:
            print("[错误] 后端在超时时间内未就绪。", file=sys.stderr)
        return 1

    print(f"[3/3] 启动界面服务（http://{FRONTEND_HOST}:{FRONTEND_PORT}）…")
    threading.Thread(target=_open_browser_when_ready, name="browser", daemon=True).start()

    print(f"\n请在浏览器访问：http://127.0.0.1:{FRONTEND_PORT}")
    print("关闭本窗口即可退出程序。\n")

    try:
        _run_frontend()
    except KeyboardInterrupt:
        print("\n收到停止信号，正在退出…")
    return 0


if __name__ == "__main__":
    sys.exit(main())

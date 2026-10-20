"""PyInstaller 运行时钩子（在入口脚本 launcher.py 执行之前自动运行）。

放这里的东西必须是「影响所有进程、且必须在任何业务 import 之前生效」的。
启动编排逻辑仍然在 launcher.py 里，这里只做三件很小但时机敏感的事。
"""

import os
import sys

# 1) 关掉对外遥测与自动开浏览器。
#    用环境变量而不是只靠 Streamlit 配置，是因为环境变量的优先级更高——
#    即使用户机器上恰好有一份 ~/.streamlit/config.toml 把这两项打开，
#    打包出来的桌面版也应当保持「不发数据、不重复开浏览器」的行为。
os.environ.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
os.environ.setdefault("STREAMLIT_SERVER_HEADLESS", "true")

# 2) 无控制台运行时 sys.stdout / sys.stderr 会是 None，
#    而 uvicorn 与 Streamlit 都会往它们写日志，直接 AttributeError 崩在启动阶段。
#    这里兜一个「黑洞」而不是文件，是因为 launcher 随后会把真正的日志文件准备好；
#    这个钩子只负责让「钩子到 launcher 之间」不至于炸掉。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115

# 3) 防御性：确保解包目录在 sys.path 最前。
#    PyInstaller 正常会自动加，但某些第三方库会在 import 期间重排 sys.path，
#    一旦把它挤掉，`import frontend.xxx` 之类的路径解析就会失败，且报错很难指向根因。
_meipass = getattr(sys, "_MEIPASS", None)
if _meipass and _meipass not in sys.path:
    sys.path.insert(0, _meipass)

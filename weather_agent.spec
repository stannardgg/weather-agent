# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（单文件 exe）。

用法（不要直接跑本文件，用构建脚本，它会顺带生成图标并重命名产物）：

    .venv/Scripts/python.exe scripts/build_exe.py

## 这个 spec 在解决什么

本项目是个「全栈 + 本地模型」的应用，打包的难点不在业务代码，而在于
**一大堆第三方库靠「运行时动态导入」和「包内数据文件」活着**。
PyInstaller 的静态分析看不到这些，不显式声明就会打出一个「能启动、一用就炸」的 exe。
下面每一段 `datas` / `hiddenimports` 都对应一个具体的坑，不是抄模板。

## 三类需要手工补的东西

1. **包内数据文件**：streamlit 的前端静态资源、chromadb 的建表 SQL 迁移脚本。
2. **动态导入的模块**：uvicorn 的 loop/protocol 实现、chromadb 的 Rust 绑定、SQLAlchemy
   的异步 SQLite 方言——它们都是按字符串名 importlib 加载的，静态分析抓不到。
3. **元数据**：streamlit 用 `importlib.metadata.version()` 读自己的版本号，
   不带 dist-info 会在启动时报 PackageNotFoundError。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

PROJECT_ROOT = Path(SPECPATH).resolve()

# 打包出来的 exe 名先取 ASCII：PyInstaller 在部分环境下处理非 ASCII 的 name 会出问题
# （内部要拿它当中间文件名）。改成中文名这一步交给构建脚本在产物生成之后做。
EXE_BASENAME = "GuageWeatherAgent"


def _frontend_files() -> list[tuple[str, str]]:
    """收集前端源码，**跳过 `__pycache__`**。

    直接 `(frontend目录, "frontend")` 也能用，但那会把开发期编译出的 `.pyc` 一起打进去：
    既是无用体积，又可能出现「源码更新了、旧 pyc 还在包里」的困惑。
    前端必须以**真实文件**形式存在——Streamlit 是「读脚本文件再 exec」的模型，
    没法从 PyInstaller 的内存包里加载 `app.py`。
    """
    source_root = PROJECT_ROOT / "frontend"
    entries: list[tuple[str, str]] = []
    for path in sorted(source_root.rglob("*")):
        if path.is_dir():
            continue
        if "__pycache__" in path.parts:
            continue
        if path.suffix == ".pyc":
            continue
        relative_parent = path.parent.relative_to(PROJECT_ROOT)
        entries.append((str(path), str(relative_parent)))
    return entries


# --------------------------------------------------------------------------- 数据文件

datas: list[tuple[str, str]] = [
    # 项目自有资源：迁移脚本与前端源码。
    # 明确放成「普通只读文件」：onefile 模式会把它们解包到 _MEIPASS，
    # 对 alembic 与 Streamlit 来说那就是一个再普通不过的目录。
    (str(PROJECT_ROOT / "alembic.ini"), "."),
    (str(PROJECT_ROOT / "alembic"), "alembic"),
]

datas += _frontend_files()

# 配置模板与真实配置：让 exe 首次运行就能用（launcher 会把 .env 复制到 exe 同级，
# 用户随后可自行修改）。.env 不存在时跳过，不报错。
for optional in (".env", ".env.example"):
    path = PROJECT_ROOT / optional
    if path.exists():
        datas.append((str(path), "."))

# streamlit 的前端静态资源（约 23MB，含 JS/CSS/字体）+ 内置示例页 + .agents 目录。
# 没有官方 hook，必须自己收；漏了它页面会白屏（HTML 能返回，但资源 404）。
datas += collect_data_files("streamlit")

# chromadb 的迁移脚本（migrations/**/*.sql）与日志配置。
# 这些 SQL 在「首次创建本地库」时被执行，缺了会直接建不出表。
datas += collect_data_files("chromadb")

# --------------------------------------------------------------------------- 动态导入

hiddenimports: list[str] = [
    # --- 项目自身 ---
    # launcher 里这些 import 都写在函数内部（为了把启动开销推迟到真正需要时），
    # 显式列出来是为了不受静态分析保守程度的影响。
    "app.main",
    "app.models",
    "app.api.v1",
    "app.services.knowledge_service",
    "app.worker.tasks",
    # --- 数据层 ---
    # SQLAlchemy 按 URL 里的方言名动态 import 对应的方言模块，异步驱动尤其隐蔽：
    # 漏了 aiosqlite 的表现是「启动就报 Can't load plugin: sqlalchemy.dialects:sqlite.aiosqlite」。
    "sqlalchemy.dialects.sqlite.aiosqlite",
    "aiosqlite",
    "alembic",
    # --- 向量库 ---
    # chromadb 1.x 的本地向量检索是 Rust 实现，通过 `import chromadb_rust_bindings`
    # 加载一个 63MB 的 .pyd。这是整个包里最大的单个文件，漏了就完全没有检索能力。
    "chromadb_rust_bindings",
    # ⚠️ chromadb 有一批组件是**按字符串路径动态加载**的（见 chromadb/config.py 里的
    #    `chroma_*_impl: str = "chromadb.xxx.Yyy"` 与 get_class()），
    #    静态分析完全看不到。只补一个必然不够——首次踩的是
    #    `chromadb.telemetry.product.posthog`（表现：一上传文档就报
    #    "无法打开向量库目录：ModuleNotFoundError: No module named
    #    'chromadb.telemetry.product.posthog'"）。
    #    所以这里直接全量子模块收集，把这类问题一次性关掉。chromadb 纯 Python 部分
    #    只有 5MB 出头，代价很小。
    *[
        name
        for name in collect_submodules("chromadb")
        if not name.startswith("chromadb.test") and ".test." not in name
    ],
    # --- Web 服务 ---
    # uvicorn 的 loop 实现与协议实现全部是「按配置字符串动态挑选」的，
    # 静态分析看到的只有 importlib，必须显式声明。
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "streamlit.web.bootstrap",
    "streamlit.web.cli",
    "streamlit.runtime.scriptrunner.magic_funcs",
    "websockets",
    "httptools",
    "h11",
    # --- 向量化（本地 ONNX 模型）---
    # fastembed 的三个原生依赖：onnxruntime 是推理引擎，tokenizers 是分词器（Rust），
    # py_rust_stemmers 用于稀疏检索的分词。三者都是编译扩展，必须按模块收集。
    "fastembed",
    "onnxruntime",
    "tokenizers",
    "py_rust_stemmers",
    # --- LLM 编排 ---
    # langgraph/langchain 在 agent/graph.py 里是延迟导入（为了不拖慢启动），
    # 而且它们内部大量使用 entry-point 与字符串路径加载。
    "langgraph",
    "langgraph.graph",
    "langgraph.checkpoint",
    "langgraph.checkpoint.memory",
    "langgraph.prebuilt",
    "langchain_core",
    "langchain_core.messages",
    "langchain_core.language_models",
    "langchain_openai",
    "tiktoken",
    "tiktoken_ext",
    "tiktoken_ext.openai_public",
    # --- 杂项原生扩展 ---
    "orjson",
    "mmh3",
    "pybase64",
    "bcrypt",
    "jwt",
    "pypika",
    "overrides",
    # sqlalchemy 的同步引擎驱动（alembic 在某些路径下会用）
    "sqlite3",
]

# --------------------------------------------------------------------------- 元数据

# 这些包在运行时用 importlib.metadata 读自己的版本号，缺 dist-info 会直接抛异常。
for package in ("streamlit", "chromadb", "fastembed", "onnxruntime", "pandas", "numpy"):
    try:
        datas += copy_metadata(package)
    except Exception:  # noqa: BLE001 —— 包不存在时跳过，不影响主流程
        pass

# --------------------------------------------------------------------------- 排除项

excludes: list[str] = [
    # chromadb 把 k8s 客户端列为依赖，但只有「连远程 chroma 服务」时才用得到；
    # 我们走的是进程内 PersistentClient（见 app/rag/vectorstore.py 的单进程约束），
    # 实测 `import chromadb` 不会 import kubernetes，排掉可以省下几十 MB。
    "kubernetes",
    # 开发/测试期依赖，运行时完全用不到
    "pytest",
    "_pytest",
    "unittest",
    "doctest",
    "IPython",
    "ipykernel",
    "notebook",
    "jupyter",
    "matplotlib",
    "scipy",
    "tkinter",
    "PyQt5",
    "PySide2",
    "PySide6",
    "torch",
    "tensorflow",
    "PyInstaller",
    # 只有在给别人造「给别人的打包工具」时才需要
    "setuptools.command",
    # ⚠️ 不要在这里排 distutils：PyInstaller 自带 pre_safe_import_module/hook-distutils.py
    #    要把它别名到 setuptools._distutils，若先被标成 ExcludedModule 会直接
    #    报 "Target module distutils already imported as ExcludedModule"。这是个
    #    「看起来无害、实际让整个打包在 24 秒内崩掉」的坑，已踩过。
]

# --------------------------------------------------------------------------- 构建

a = Analysis(
    [str(PROJECT_ROOT / "launcher.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(PROJECT_ROOT / "packaging" / "runtime_hook.py")],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=EXE_BASENAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX 压缩在国内杀软眼里是高危特征，且对 .pyd 压缩后可能加载失败，关掉
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # 保留控制台：能看到访问地址与错误，出问题时用户能截图反馈
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / "packaging" / "app.ico"),
)

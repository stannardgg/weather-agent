# 桌面版打包（单文件 exe）

把「瓜葛天气助手」打成一个双击即用的 Windows 可执行文件。本文记录**为什么这么做**、
**怎么构建**、以及**打包后会踩到什么**——尤其是那些「不报错、只是行为不对」的地方。

---

## 1. 一句话结论

```bash
.venv/Scripts/python.exe scripts/build_exe.py
```

产物在 `dist/`：`瓜葛天气助手.exe` + `使用说明.txt`。
把这两个文件（连同首次运行后自动生成的 `data/`、`.env`）放在同一个文件夹里分发即可。

---

## 2. 为什么是「单进程双服务」

开发期用 `scripts/serve.py`：起两个子进程，一个跑 uvicorn、一个跑 Streamlit。
这在有真实 `python.exe` 的环境里没问题，但**打成 onefile 后完全不能这么干**：

> onefile 模式下 `sys.executable` 指向 exe 自己。每 spawn 一个「子进程」，
> 整个 bundle（本项目含 onnxruntime / chromadb Rust 绑定 / streamlit，数百 MB）
> 都要**重新解包一次**到临时目录。三个进程 = 三次解包，冷启动被拖到几分钟。

所以 `launcher.py` 改成：

| 组件 | 运行位置 | 依据 |
| --- | --- | --- |
| 后端 FastAPI | **守护线程** | uvicorn 的 `Server.serve()` 用 `capture_signals()` 包了一层，该方法在非主线程会**直接跳过**信号注册（`uvicorn/server.py`），因此在子线程跑是安全的 |
| 前端 Streamlit | **主线程阻塞** | 就是 `streamlit run` 原本的行为；退出时进程结束，守护线程随之释放，**天然没有孤儿进程** |

代价：后端挂了前端不会自动退（反之亦然）。`launcher.py` 用 `_backend_error` 把子线程异常
带回主线程，在后端未就绪时直接终止启动并打印原因。

---

## 3. 路径是怎么处理的

配置里的路径全是相对的：`./data/weather_agent.db`、`./data/chroma`、`./data/uploads`、
`./logs`，pydantic 的 `env_file=".env"` 也是相对的。它们**统一相对「当前工作目录」解析**。

所以 `launcher.py` 在最开始做一件事：

```python
os.chdir(可写根目录)     # 打包后 = exe 所在目录
```

之后所有相对路径自动落在 exe 旁边，**不需要改 `app/core/config.py` 里任何一个默认值**。
这是整个打包里最省事的一步决策。

两类目录必须分清：

| 目录 | 打包后位置 | 内容 |
| --- | --- | --- |
| **可写根目录** | exe 同级；不可写时退回 `%LOCALAPPDATA%\GuageWeatherAgent` | `.env`、`data/`、`logs/` |
| **只读资源目录** | onefile 的 `sys._MEIPASS` | `frontend/`、`alembic/`、`alembic.ini` |

> `.env` 的落地策略：优先用 exe 同级**已存在**的（用户可以自己改），
> 否则从内嵌的那份复制出来。这样既开箱可用，密钥又不藏在二进制里够不着。

---

## 4. spec 里每一段在解决什么

PyInstaller 的静态分析看不到「运行时动态导入」与「包内数据文件」，不显式声明就会打出
一个**能启动、一用就炸**的 exe。以下都不是模板，逐条对应一个具体的坑：

| 补充项 | 对应的坑 |
| --- | --- |
| `streamlit` 全量 data files | Streamlit 没有官方 hook。漏了它的 `static/`（约 23 MB JS/CSS/字体），页面会**白屏**——HTML 返回 200，但所有资源 404 |
| `chromadb` 全量 data files | 建表用的 `migrations/**/*.sql` 是运行时读取的，缺了直接建不出表 |
| `chromadb_rust_bindings` | chromadb 1.x 的本地向量检索是 **Rust** 实现，是一个 **63 MB 的 `.pyd`**（不是 hnswlib）。漏了就完全没有检索能力 |
| `sqlalchemy.dialects.sqlite.aiosqlite` + `aiosqlite` | SQLAlchemy 按 URL 里的方言名 **动态 import**。漏了报 `Can't load plugin: sqlalchemy.dialects:sqlite.aiosqlite` |
| uvicorn 的 loop / protocol / lifespan 一众子模块 | 全部是「按配置字符串挑实现」的动态导入 |
| `copy_metadata(...)` | streamlit 用 `importlib.metadata.version()` 读自己的版本号，缺 dist-info 会抛 `PackageNotFoundError` |
| `runtime_hooks=[packaging/runtime_hook.py]` | 关遥测、兜住 `sys.stdout is None`（无控制台时会崩在日志初始化） |
| `frontend/` 作为**真实文件** | Streamlit 是「读取并 exec 脚本文件」的模型，**没法从内存包加载 app.py**，必须是磁盘上的文件 |

**排除项**：`kubernetes` 可以安全排掉——chromadb 把它列为依赖，但只有连远程 chroma 服务
时才用；本项目走进程内 `PersistentClient`（见 `app/rag/vectorstore.py` 的单进程约束），
实测 `import chromadb` 不会 import kubernetes。

> ⚠️ **不要**把 `distutils` 加进 `excludes`。PyInstaller 自带的
> `pre_safe_import_module/hook-distutils.py` 要把它别名到 `setuptools._distutils`，
> 一旦先被标成 `ExcludedModule` 就会报
> `Target module "distutils" already imported as "ExcludedModule('distutils',)"`，
> 让整个打包在 20 多秒内崩掉。这个坑已经踩过一次。

---

## 5. 打包后仍然生效的原有约定

打包不改变架构，六层结构与 §11 的硬约定照旧。有两处需要知道：

- **迁移在进程内跑**：`python -m alembic upgrade head` 在打包后不可用（没有 `python.exe`，
  也没有 `-m` 的语义）。所以 `launcher.py` 改用 alembic 的 Python API，并把
  `script_location` 显式指向资源目录里的 `alembic/`，连接串从 `settings.database_url` 取
  ——保证「迁移用的库」和「应用跑的库」是同一个。
- **向量模型缓存**：`FASTEMBED_CACHE_PATH=./data/models/fastembed`。首次运行若本地没有模型，
  会联网从 `HF_ENDPOINT`（默认 `hf-mirror.com`）下载一次，之后完全离线。
  **私有化交付时把整个 `data/models/` 一起拷过去，就能做到零联网启动。**

---

## 6. 打包之后必须验收

```bash
.venv/Scripts/python.exe scripts/test_exe.py            # 默认用 dist/瓜葛天气助手.exe
.venv/Scripts/python.exe scripts/test_exe.py --keep     # 保留临时目录，便于事后翻日志
.venv/Scripts/python.exe scripts/test_exe.py --backend-port 18000 --frontend-port 18501
```

> ⚠️ 若本机已经双击开着这个 exe（占用 8000/8501），**不要**为了验收去关掉它——用
> `--backend-port` / `--frontend-port` 换两个空闲端口即可。脚本会把端口经环境变量透给
> launcher，与手头那个实例和平共处。端口被占用导致的失败是「脚本没有容身之处」，
> 不是被测程序的问题。

脚本会在一个**干净的临时目录**里跑 exe（不污染开发环境），自动完成 17 项检查后收尾：

| 分组 | 检查内容 |
| --- | --- |
| 1 服务启动 | 前后端端口在超时内就绪（顺带报告冷启动耗时） |
| 2 数据初始化 | `.env` / `data/` / `logs/` 自动生成 |
| 3 健康检查 | `/health` 与 `/health/ready` |
| 4 前端页面 | 首页 200 + `index.html` 200（**防白屏**） |
| 5 监听地址 | 前后端都**不得**监听 `0.0.0.0` / `::` |
| 6 知识库全链路 | 注册 → 登录 → 上传 → 后台入库到 `ready`（覆盖 chromadb + fastembed） |
| 7 通用问答 | `/chat` 非流式 JSON 与流式 SSE **两条路径都验**（缺 LLM 密钥时自动跳过） |

> **为什么一定要跑**：本项目第一次打包出来的 exe **能启动、健康检查全绿**，
> 但一上传文档就报 `ModuleNotFoundError`（chromadb 的动态导入）。静态检查与「能启动」
> 都不足以说明可用性，只有真跑一遍链路才能发现。

---

## 7. 已知限制（如实记录）

- **体积大、冷启动慢**：单文件 exe 约数百 MB。每次运行都要先把整个 bundle 解包到临时目录，
  首次启动（含模型加载）可能需要 30 秒到 1 分钟。这是 onefile 的固有代价，不是 bug。
  若无法接受，可把 spec 改成 onedir（去掉单文件），启动会快很多，代价是多一个目录。
- **只保证 Windows**：图标是 `.ico`、`runtime_tmpdir=None`、控制台行为都针对 Windows。
  Linux/macOS 需要各自调整（主要是图标格式与 `console` 行为）。
- **首次运行需要联网**（只为下载向量模型）。给出 `data/models/` 后可离线。
- **端口固定**：后端 8000、界面 8501。被占用时用环境变量 `BACKEND_PORT` / `FRONTEND_PORT` 覆盖（验收时可直接用 `--backend-port` / `--frontend-port`）。
- **杀软误报**：未使用 UPX（已在 spec 里显式 `upx=False`）。若仍被拦截，需在杀软里加白名单。

---

## 8. 排错

| 现象 | 原因 / 处理 |
| --- | --- |
| 控制台一闪而过 | 多为 `SECRET_KEY` 不足 32 字节，或 `.env` 里 `DATABASE_URL` 写错。用 cmd 里 `瓜葛天气助手.exe` 直接跑，看完整报错 |
| 页面白屏、静态资源 404 | `streamlit` 的 data files 没收全（见第 4 节） |
| `Can't load plugin: sqlalchemy.dialects:sqlite.aiosqlite` | 漏了 `aiosqlite` / 方言模块的 hiddenimports |
| `PackageNotFoundError: streamlit` | 漏了 `copy_metadata("streamlit")` |
| 检索永远返回空 | 缺 `chromadb_rust_bindings`，或 `EMBEDDING_DIM` 与模型不匹配（后者有启动期校验会直接报错） |
| 页面卡在 "Please wait..." | Streamlit 的 Origin/XSRF 判定。桌面版本已关闭 `enableXsrfProtection`，若改配置请保持一致 |

---

## 9. 构建环境

- Python 3.13（`.venv` 内）
- `pyinstaller >= 6.11`（本机实测 6.22.3）
- `pillow`（`scripts/build_exe.py` 用它用代码画图标，不依赖任何外部图片素材）
- 打包必须在**与应用同一个 venv** 里跑，否则分析不到依赖

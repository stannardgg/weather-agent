# CLAUDE.md — 企业级天气查询 Agent

> 本文件是本项目的**唯一权威约定文档（Single Source of Truth）**。  
> 每次开发会话开始前必须完整阅读。任何与本文件冲突的临时代码、口头约定一律无效。

- 文档版本：v0.15（更新流程改为「只更新线上」）
- 建立时间：2026-10-11
- 最近更新：2026-10-13（M8 交付；同日还完成通用问答扩展、少女粉主题、对话搜索、删除图标修复、提问后「（思考中）」占位提示、移除流式「▌」光标；并定下「**后续修改只更新线上、不再重建 exe**」的流程，见 §16）
- 当前状态：**M0、M1、M2、M3、M4、M5、M6、M7、M8 已完成**；D1–D8、天气数据源、M3 四项决策、M4 四项决策（Q1B/Q2A/Q3A/Q4C）、M5 四项决策（Q1/Q2/Q3/Q4）、M6 四项决策（Q1–Q4）、M7 三项决策（Q1/Q2/Q3）、M8 一项决策（单文件 exe）均已由用户确认
- ⚠️ **交付主线已切换为线上站点**（2026-10-13）：`https://weather-agent-34403.app.workbuddy.host/`。桌面版 exe **冻结**在该日版本，**后续改动不再重新打包**——流程见 §16。
- ✅ **M8 已交付**：桌面版单文件 exe（`launcher.py` + `weather_agent.spec` + `packaging/runtime_hook.py` + `scripts/build_exe.py` + `scripts/test_exe.py`）。**147 MB 单文件**，双击即用：自动迁移 → 起后端 → 起前端 → 开浏览器。采用「**单进程双服务**」（后端跑守护线程、前端跑主线程）以避开 onefile 重复解包；数据全部落在 exe 同级 `data/`，可整体搬迁备份。验收脚本 **17 项全通过**（含真实 DeepSeek 调用与完整知识库入库链路），冷启动约 **10 秒**。详见 §15 与 `docs/PACKAGING.md`。
- ✅ **M7 已交付**：部署与文档——本地一键启动/停止脚本（`start.sh`/`stop.sh`/`start.bat`/`stop.bat` + `scripts/serve.py`/`stop.py`，跨平台，自动迁移 + 起前后端 + 日志/PID 管理）、Docker 化（`Dockerfile` + `docker-compose.yml` + `.dockerignore`，后端/前端两服务 + `./data` 数据卷）、完整运维手册（README 新增「部署（M7）」章：一键启动 / Docker / 监控 / 备份恢复 / 故障排查 / 升级迁移）、补 `.env.example` 的 M6 配置项。pytest **172 项**无回归，`docker compose config` 校验通过，`serve.py`/`stop.py` 三次端到端冒烟（起停 + 无残留进程）通过。
- ✅ **M6 已交付**：企业级加固四件套——进程内滑动窗口限流（auth/chat/default 三档 + health/metrics 豁免 + 429 统一信封）、审计日志落库（`audit_logs` 表 + `audit_service.record` 失败绝不拖垮主流程）、自写 Prometheus 指标（counter/summary + `GET /metrics`）、LLM 有限重试 + 友好兜底（最终失败返回 `LLM_UNAVAILABLE_ANSWER`，不泄露运维细节）。pytest **172 项**全通过（新增 18 项），迁移 `76b10bcb1154` 已落地 `audit_logs` 表。
- ✅ **M5 已交付**：Streamlit 多页前端（登录 / 对话 / 知识库三页）+ `api_client.py` 后端封装（统一信封解析、SSE 逐事件 yield、统一错误翻译）。pytest **154 项**全通过（新增 16 项前端单测），并用真实后端 + Streamlit 首页 200 做了端到端确认（注册→登录→闲聊、SSE 帧格式逐项核对）。
- ✅ **M4 已交付**：意图路由（规则优先 + LLM 兜底）→ 条件分支（天气 / 检索 / 闲聊 / 混合）→ prompt 组装 → 流式生成 → 落库的完整问答链路。pytest **138 项**全通过，端到端冒烟（`scripts/smoke_m4.py`）**61 项**全通过，并用真实 DeepSeek 凭据完成了非流式与流式的端到端确认。
- ✅ **M3 已交付**：上传 → 解析 → 切分 → 向量化 → 入库全链路可用，含去重、用户隔离、覆盖更新、重建索引、启动补偿。pytest 96 项全通过，端到端冒烟 33 项全通过。
- ✅ **M4 前置两项已解决（2026-10-12）**：
  1. **向量化换成真实模型**（D-M4-1 选 A）：本地 ONNX（`fastembed` + `BAAI/bge-small-zh-v1.5`，512 维）。实测语义有效（相关 0.69 vs 无关 0.32），检索 Recall@1 = 4/4。占位实现保留但只用于离线测试。
  2. **移除已 sunset 的 `langchain-community`**（D-M4-2 选 A）：改为直接用 `pypdf` + `docx2txt` 自写解析，**依赖净减少**（顶层 −1，并连带去掉 `langchain-classic`）。`langchain-text-splitters` 未受影响（单独拆出的包，不在 sunset 范围）。
- ⚠️ **模型名已于 2026-10-12 修正**：`.env` 原写的 `deepseek-chat` **已被上游下线**，现为 `deepseek-flash`（= DeepSeek-V4.1-Flash）；同时 `LLM_BASE_URL` 由 `https://api.deepseek.com/v1` 改为官方文档的 `https://api.deepseek.com`（不带 `/v1`）。**旧名 `deepseek-chat` / `deepseek-reasoner` 现已不可用**，网关层遇到 404 会直接给出候选模型名（见 §11 LLM 网关约定）。
- ⚠️ **有一条环境事实必须知道**：**`huggingface.co` 在本机网络下不可达**（实测 ReadTimeout），
  因此向量模型的下载默认走镜像 `HF_ENDPOINT=https://hf-mirror.com`。模型只在**首次运行**时下载一次
  （约 90MB，落在 `data/models/fastembed`），之后完全离线。私有化离线部署时把该目录整体拷过去即可。
- ✅ **天气字段名已于 2026-10-12 用真实凭据逐项校准**（见 §9.3 / §13）：实时天气与每日预报拿到 HTTP 200 并据此**重写了解析层**——v1 与 v7 是两套完全不同的形状（扁平顶层 + 带单位的嵌套对象 + 0–1 比例 + UTC 时刻），不是简单改字段名；生活指数补上必填的 `type` 参数后可用。
- ⛔ **唯一未通的是城市搜索** `/geo/v2/city/lookup` → **403 Security Restriction**。这不是代码问题：同一 Host/KEY 下另三个接口均为 200，且该路径**不带参数时返回 400**（说明路径与鉴权都已通过，是参数校验之后的安全/权限关卡拦下的）。按官方错误码定义与同类案例，属**凭据的 API 限制/应用限制，或 GeoAPI 权限未生效**，需到控制台处置，步骤见 §9.3.3。

---

## 1. 项目目标

**一句话定位**：一个可私有化部署的「天气查询 + 企业知识库」智能问答 Agent，面向企业内部员工或客服人员。

**核心能力（5 项）**

| 编号 | 能力               | 示例问法                       |
| -- | ---------------- | -------------------------- |
| C1 | 实时天气查询           | "明天上海要带伞吗？"                |
| C2 | 知识库问答（RAG）       | "我们的气象应急预案里，橙色预警对应什么处置流程？" |
| C3 | 混合问答（天气 + 知识库联查） | "按公司台风预案，明天深圳该启动几级响应？"     |
| C4 | 知识库管理            | 上传 / 更新 / 删除文档、重建索引、查看切分结果 |
| C5 | 多轮会话             | 上下文记忆、会话历史持久化、按用户隔离        |

**"企业级"在本项目中的具体含义（不做虚的）**

1. **鉴权与隔离**：用户登录（JWT），会话与知识库按用户/租户隔离。
2. **可观测**：结构化日志 + 请求追踪 ID + 关键指标（LLM 调用次数、token 消耗、检索耗时）。
3. **可运维**：配置全部走环境变量，无硬编码密钥；有健康检查接口；有数据库迁移机制。
4. **稳健**：外部 API 失败有降级与重试；LLM 调用有超时与限流；文档解析走异步任务不阻塞主流程。
5. **可测**：核心逻辑（检索、路由、工具调用）有单元测试。
6. **可交付**：一键启动脚本、部署说明、README。

**明确的非目标（本期不做）**

- 不做多模态（图片/语音输入）
- 不做移动端 App
- 不做复杂权限体系（RBAC 细粒度权限）——只做「用户级隔离」

---

## 2. 协作铁律（最高优先级，任何情况下不得违反）

> 这一节是本项目最重要的部分。用户是初学者，但**技术决策权 100% 归用户**。

### 2.1 技术决策权归属

**所有技术相关事项的最终决定权归用户。** 范围包括但不限于：

- 框架 / 库 / 第三方服务的选择
- 目录结构与模块划分
- 数据模型与数据库表设计
- API 接口的路径、参数、返回格式
- 命名规范、代码风格
- 部署方式、性能优化手段、重构方案
- 是否需要引入某个新依赖

AI **不得**以"这是行业最佳实践""这是标准做法""大家都这么用"为由自行决定并直接实施。

### 2.2 强制输出格式（每次涉及技术选择必须照此输出，不得省略任何一项）

1. **问题陈述**：这个决策要解决什么问题，影响哪些后续开发。
2. **方案清单**：**至少 2 个**（推荐 2–4 个），每个方案必须包含：
   - 方案名 + 一句话说明
   - **核心原理**：用初学者能听懂的白话解释它是怎么工作的
   - **优点**：≥2 条，要具体，禁止空话（反例：「性能好」；正例：「并发 1000 请求时内存占用比 B 低约 60%」）
   - **缺点 / 代价**：≥2 条，必须诚实，包含**学习成本**和**后期运维成本**
   - **适用场景**：什么情况下选它才对
   - **代码量级**：大概要写多少行、新增多少个文件
3. **对比表**：横向列出关键维度的差异。
4. **AI 的倾向性意见**：明确写「我倾向 A，理由是……」，同时声明这只是建议。
5. **暂停等待**：在用户明确回复「选 A」之前，**不得**开始写该部分的代码。

### 2.3 禁止行为清单

- ❌ 用户未选择前，抢先实现该部分
- ❌ 用户选了 A，AI 因自行觉得 B 更好而静默改用 B
- ❌ 一次性引入超出当前开发阶段需要的依赖
- ❌ 使用专业术语却不解释
- ❌ 在方案对比里省略或弱化「缺点」
- ❌ 把多个技术决策打包成一句「我按最佳实践处理了」
- ❌ 只给结论不给理由

### 2.4 变更流程

要改动**已确定**的技术选型，必须：重新走一遍 2.2 流程 → 用户同意 → 在 §13 决策日志追加一行记录（含日期、原方案、新方案、原因）。**不允许**直接改代码而不留记录。

### 2.5 术语解释义务

首次出现的专业术语，必须紧跟一句白话解释，格式：`术语（白话解释）`。  
例：`向量数据库（一种专门存"语义坐标"、能按"意思像不像"来检索的数据库）`。

### 2.6 提问效率约束

用户不喜欢被反复追问同一件事。

- 同一个决策点只问一次。
- 若用户回复「你定」「随便」「按你说的来」，则 AI 定完后**必须**记录到 §13 决策日志，并明确告知「我替你定了 X，理由是……，想改随时说」。
- 禁止在方案对比时反问用户「你觉得呢」而不给出自己的倾向性意见。

### 2.7 沟通风格约定

- 先给结论，再给理由。
- 每次回答必须专业、精简、无废话。
- 涉及流程/架构的说明，优先用**图解**（图 + 简短文字），而非大段描述。
- 假设用户编程基础较浅，但不啰嗦、不重复解释已经确认过的概念。

---

## 3. 技术栈选型（⚠️ 全部待用户拍板，标记 `【待定】`）

> 说明：下列每一项都给出了 2–3 个方案。**在用户逐项拍板前，不得开始写代码。**  
> 表中「推荐」只是 AI 的倾向性意见，不是决定。

### 3.0 决策结果速查（2026-10-11 已确认）

| 决策点          | 结论                         | 状态             |
| ------------ | -------------------------- | -------------- |
| D1 后端 API 框架 | **FastAPI**                | ✅ 用户确认         |
| D2 前端 / 交互层  | **Streamlit**              | ✅ 用户确认         |
| D3 Agent 编排  | **LangGraph**              | ✅ 用户确认         |
| D4 向量库       | **Chroma**                 | ✅ 用户确认         |
| D5 关系型数据库    | **SQLite**                 | ✅ 用户确认         |
| D6 LLM 接入    | OpenAI 兼容统一网关              | ✅ 用户确认         |
| D7 文档解析      | LangChain Document Loaders → **2026-10-12 改为 `pypdf` + `docx2txt` 自写** | ⚠️ 已被 D-M4-2（§13）取代 |
| D8 部署方式      | 本地直跑（venv + uvicorn）       | ✅ 用户确认         |
| 天气数据源        | 和风天气 QWeather              | ✅ 用户确认         |
| 向量化来源        | 本地 ONNX `fastembed` + `BAAI/bge-small-zh-v1.5` | ✅ 用户确认（D-M4-1，§13） |

**M4 的阶段内决策（2026-10-12 已确认，明细见 §13）**

| 决策点 | 结论 | 状态 |
| --- | --- | --- |
| M4-Q1 编排方式 | **LangGraph 意图路由 + 分支节点** | ✅ 用户确认 |
| M4-Q2 LLM 接入实现 | **`langchain-openai`** | ✅ 用户确认 |
| M4-Q3 检索策略 | **纯向量 top_k + 距离阈值**（不做 rerank、不做混合检索） | ✅ 用户确认 |
| M4-Q4 意图识别方式 | **规则优先 + LLM 兜底** | ✅ 用户确认 |
| M4-Q5 生成模型 | **`deepseek-flash`**（原 `deepseek-chat` 已被上游下线） | ✅ 用户确认 |
| M4-Q6 开工时机 | **先写不依赖 key 的部分**，拿到密钥后再联调 | ✅ 用户确认 |

**M5 的阶段内决策（2026-10-12 已确认，明细见 §13）**

| 决策点 | 结论 | 状态 |
| --- | --- | --- |
| M5-Q1 页面结构 | **Streamlit 多页应用**（`st.navigation`：登录 / 对话 / 知识库） | ✅ 用户确认 |
| M5-Q2 流式渲染 | **delta 实时拼接 + done 到达后用完整 `answer` 覆盖兜底** | ✅ 用户确认 |
| M5-Q3 城市 / 日期输入 | **混合输入**：文本为主 + 可折叠的显式控件 | ✅ 用户确认 |
| M5-Q4 知识库页范围 | **完整集**：上传 + 进度轮询 + 覆盖重传 + 删除 + 重建索引 + 失败原因展示 | ✅ 用户确认 |

**M6 的阶段内决策（2026-10-12 已确认，明细见 §13）**

| 决策点 | 结论 | 状态 |
| --- | --- | --- |
| M6-Q1 限流方式 | **进程内滑动窗口**（`deque` 时间戳 + `time.monotonic()`；auth / chat / default 三档，health/metrics 豁免） | ✅ 用户确认 |
| M6-Q2 审计日志 | **落库 `audit_logs` 表**（`user_id` 可空、不加外键；`detail` JSON 列；失败只记警告不拖垮主流程） | ✅ 用户确认 |
| M6-Q3 指标 | **自写计数器 + summary**（零依赖，渲染 Prometheus 文本；HTTP 计数用路由模板避免高基数 label） | ✅ 用户确认 |
| M6-Q4 LLM 弹性 | **有限重试（`max_retries`）+ 友好兜底**（最终失败返回 `LLM_UNAVAILABLE_ANSWER`，技术原因只记日志） | ✅ 用户确认 |

**M7 的阶段内决策（2026-10-12 已确认，明细见 §13）**

| 决策点 | 结论 | 状态 |
| --- | --- | --- |
| M7-Q1 部署形态 | **本地一键脚本 + Docker 都要**（脚本日常开发用，Docker 交付/上服务器用） | ✅ 用户确认 |
| M7-Q2 反向代理/遗留加固 | **本次不引入反向代理层，保持轻量**（单机部署下限流单进程局限可接受；写成 README「可选扩展」） | ✅ 用户确认 |
| M7-Q3 文档范围 | **完整运维手册**（部署 + 监控 + 备份恢复 + 故障排查 + 升级迁移） | ✅ 用户确认 |

> 每个里程碑开工前都会这样追加该阶段的决策表；**未经用户拍板的实现方式不得写进代码**（§2.1）。

**本节确认的技术栈组合（后续所有代码必须基于此）**

```
FastAPI  +  Streamlit  +  LangGraph  +  Chroma  +  SQLite
     +  OpenAI 兼容 LLM 网关  +  pypdf/docx2txt 自写解析  +  fastembed 本地向量  +  本地直跑
```

> ⚠️ **D7 已被取代**：原选「LangChain Document Loaders」，其载体 `langchain-community` 于 2026-05-22 官宣 sunset、仓库已归档只读且不再修复任何问题（含安全漏洞）。2026-10-12 用户按 §2.4 流程改选**自写解析**（D-M4-2，方案 A）。切分仍在用 `langchain-text-splitters`——它是单独拆出的包，不在 sunset 范围。
>
> ✅ D6、D8 与天气数据源已于 2026-10-12 由用户确认沿用本基线。日后如需更改，仍按 §2.4 走变更流程（列方案 → 用户同意 → 追加 §13 记录）。

### D1 — 后端 API 框架

| 方案                  | 核心原理                                | 优点                                                                                               | 缺点                                                                                        | 代码量级 |
| ------------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------- | ---- |
| **A. FastAPI**【推荐】  | 基于 Python 类型注解自动做参数校验，原生异步、原生支持流式输出 | ① 自动生成交互式 API 文档（/docs）② 原生 async，调 LLM / 外部 API 不阻塞 ③ 与 Pydantic 深度绑定，数据校验几乎零成本 ④ 流式响应（SSE）开箱即用 | ① 异步概念对初学者有门槛（async/await）② 没有自带 ORM / 后台管理，要自己拼装 ③ 同步代码写进异步函数会阻塞整个服务（易踩坑）                | 中等   |
| **B. Flask**        | 最传统的同步 Web 框架，一个函数对应一个路由            | ① 概念最简单，几乎没有异步心智负担 ② 中文教程最多 ③ 调试直观                                                               | ① 默认同步，LLM 流式输出与高并发需要额外方案（gevent / 多进程）② 需手工写参数校验 ③ 无自动 API 文档                            | 少    |
| **C. Django + DRF** | 全家桶框架，自带 ORM、Admin 后台、用户系统          | ① 自带用户/权限/后台管理，企业级功能开箱可用 ② 生态成熟，文档齐全                                                             | ① 太重，学习曲线陡（ORM、MVT、settings 体系）② 异步支持是后加的，LLM 流式集成不如 FastAPI 自然 ③ 与本项目"轻量 Agent 服务"的定位不匹配 | 多    |

### D2 — 前端 / 交互层

| 方案                     | 核心原理                                | 优点                                            | 缺点                                                                      | 代码量级 |
| ---------------------- | ----------------------------------- | --------------------------------------------- | ----------------------------------------------------------------------- | ---- |
| **A. Streamlit**【推荐】   | 纯 Python 脚本，脚本每次重跑就重绘界面，无需写 HTML/JS | ① 零前端基础即可用，你已略懂 ② 内置会话状态、文件上传控件、聊天组件 ③ 开发速度最快 | ① 界面样式定制受限，"企业感"偏弱 ② 复杂多页路由较弱 ③ 每次交互整脚本重跑，超复杂页面会卡                       | 少    |
| **B. Gradio**          | 同样纯 Python，以"输入→输出"组件为组织单位          | ① 内置 ChatInterface，几行代码出对话界面 ② 适合快速演示         | ① 多页面 / 复杂布局能力弱于 Streamlit ② 更像 demo 工具，不像产品界面                          | 少    |
| **C. Next.js + React** | 前后端分离，独立前端工程调用 FastAPI              | ① 真正的产品级 UI，完全可定制 ② 可做权限页、管理后台等复杂交互           | ① 要额外学 JavaScript / TypeScript / React，学习成本最高 ② 开发周期显著变长 ③ 需要处理跨域、构建、部署 | 多    |

### D3 — Agent 编排方式

| 方案                        | 核心原理                                      | 优点                                                                   | 缺点                                                                  | 代码量级 |                                           |                                           |   |
| ------------------------- | ----------------------------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------- | ---- | ----------------------------------------- | ----------------------------------------- | - |
| **A. LangGraph**【推荐】      | 把 Agent 画成"状态机"：节点是处理步骤，边是跳转条件，状态在节点间传递   | ① 流程显式可见（节点+边），初学者容易看懂"Agent 到底怎么走的" ② 天然支持多轮、条件分支、人工介入 ③ 支持持久化与断点恢复 | ① 概念比 LangChain 经典链多（State、Node、Edge、Checkpointer）② 版本迭代快，API 有变动风险 | 中等   |                                           |                                           |   |
| **B. LangChain 经典 Chain** | 用 LCEL（管道符 \`                             | \`）把提示词、模型、解析器串成一条链                                                  | ① 写法简洁，\`prompt                                                     | llm  | parser\` 一眼看懂 ② 你已有基础，上手快 ③ 与天气工具、检索器集成简单 | ① 复杂分支 / 循环逻辑写起来别扭 ② 多轮状态管理要额外处理 ③ 调试长链较难 | 少 |
| **C. 纯手写编排（不用编排框架）**      | 自己写 Python 函数：意图判断 → 调工具 → 拼 prompt → 调模型 | ① 完全透明，没有任何黑盒，出问题一眼定位 ② 依赖最少，升级不受框架 break change 影响                  | ① 多轮、状态、重试、流式都要自己实现 ② 代码量最大 ③ 失去 LangChain 生态（现成的检索器、文档加载器）         | 多    |                                           |                                           |   |

### D4 — 向量库（知识库检索用）

| 方案                           | 核心原理                               | 优点                                                          | 缺点                                       |
| ---------------------------- | ---------------------------------- | ----------------------------------------------------------- | ---------------------------------------- |
| **A. Chroma**【推荐，开发起步】       | 嵌入式向量库，数据存本地文件/内存，`pip install` 即用 | ① 零运维，不用起独立服务 ② 你已略懂 ③ 与 LangChain 集成最顺滑                    | ① 单机，海量数据（千万级 chunk）性能有限 ② 多进程并发写入需注意    |
| **B. PostgreSQL + pgvector** | 在关系型数据库里加一个向量字段类型，向量和业务数据放同一个库     | ① 一个数据库同时管业务数据和向量，运维简单 ② 支持 SQL + 向量混合过滤（如"只搜某用户的文档"）③ 事务一致 | ① 需装 pgvector 扩展，配置略麻烦 ② 超大规模检索性能不如专用向量库 |
| **C. Milvus / Qdrant**       | 独立的分布式向量数据库服务                      | ① 生产级性能，支持亿级向量 ② 水平扩展                                       | ① 需独立部署运维，学习成本高 ② 对单人开发的项目属于过度设计         |

### D5 — 关系型数据库（存用户、会话、文档元数据）

| 方案                     | 核心原理               | 优点                                    | 缺点                                     |
| ---------------------- | ------------------ | ------------------------------------- | -------------------------------------- |
| **A. SQLite**【推荐，开发起步】 | 一个文件就是一个数据库，无需安装服务 | ① 零配置，删掉文件就重置 ② 适合开发与单机部署             | ① 并发写入弱 ② 不适合多实例部署                     |
| **B. PostgreSQL**      | 独立数据库服务，功能最全的开源关系库 | ① 生产级，支持并发与 JSON 字段 ② 若选 D4-B 可共用同一个库 | ① 需要装服务或跑 Docker ② 配置成本比 SQLite 高      |
| **C. MySQL**           | 国内企业最常用的关系库        | ① 资料多、招人容易 ② 运维人才充足                   | ① 若同时要向量检索得另配向量库 ② 相较 PostgreSQL 扩展性略弱 |

### D6 — LLM 接入方式

| 方案                       | 核心原理                                          | 优点                                                  | 缺点                                             |
| ------------------------ | --------------------------------------------- | --------------------------------------------------- | ---------------------------------------------- |
| **A. OpenAI 兼容统一网关**【推荐】 | 所有模型都走同一个接口协议（base_url + api_key），换模型只改两个环境变量 | ① 供应商可随时切换（DeepSeek / 通义 / 智谱 / 本地 vLLM 都兼容）② 代码零改动 | ① 各家的高级特性（如原生 function call 细节）可能有差异 ② 依赖中间层协议 |
| **B. 直接绑定某家官方 SDK**      | 直接调用特定厂商的 Python SDK                          | ① 能用到该厂商全部独有能力 ② 少一层抽象，排查问题直接                       | ① 换供应商要重写接入层 ② 被厂商锁定                           |
| **C. 本地 Ollama**         | 在自己机器上跑开源模型                                   | ① 数据不出内网，隐私最好 ② 无 API 费用                            | ① 需要较好的 GPU，你机器可能跑不动大模型 ② 效果通常弱于云端模型           |

### D7 — 文档解析（知识库上传时把 PDF/Word 变成文本）

| 方案                                    | 核心原理                                | 优点                                                                | 缺点                                     |
| ------------------------------------- | ----------------------------------- | ----------------------------------------------------------------- | -------------------------------------- |
| **A. LangChain Document Loaders**【推荐】 | LangChain 内置的各类加载器，统一返回 Document 对象 | ① 格式覆盖广（PDF / DOCX / TXT / MD / CSV / HTML）② 与你已有的 LangChain 知识衔接 | ① 扫描版 PDF（图片型）无法提取 ② 复杂表格解析效果一般        |
| **B. Unstructured.io**                | 专门的文档解析库，对布局识别更强                    | ① 表格、标题层级识别更准 ② 支持 OCR                                            | ① 依赖较重，安装体积大 ② 本地跑 OCR 慢               |
| **C. 自写解析 + 第三方小库**                   | 按格式分别用 pypdf / python-docx 自己写      | ① 完全可控，依赖最轻 ② 能针对公司特有格式做定制                                        | ① 每种格式都要自己处理，代码量大 ② 边界情况（加密、损坏文件）要自己兜底 |

### D8 — 部署方式

| 方案                                  | 核心原理                                  | 优点                       | 缺点                           |
| ----------------------------------- | ------------------------------------- | ------------------------ | ---------------------------- |
| **A. 本地直跑（venv + uvicorn）**【推荐，开发期】 | 直接在机器上跑 Python 进程                     | ① 最简单，改完代码就生效            | ① 环境依赖靠手工维护 ② 不方便迁移          |
| **B. Docker Compose**               | 用一份 yml 描述所有容器（API / Streamlit / 数据库） | ① 一键起全套，环境一致 ② 迁移到服务器很容易 | ① 要学 Docker 基本概念 ② 镜像构建有学习成本 |
| **C. Kubernetes**                   | 容器编排平台                                | ① 生产级弹性伸缩                | ① 对本项目属于严重过度设计               |

### 3.9 组合方案对照（参考用）

> **✅ 已选定组合**（见 §3.0）：D1-A FastAPI + D2-A Streamlit + D3-A LangGraph + D4-A Chroma + D5-A SQLite + D6-A 统一网关 + **D7-A′ pypdf + docx2txt 自写解析**（原 D7-A LangChain Loaders，2026-10-12 被 D-M4-2 取代）+ D8-A 本地直跑；**向量化来源：本地 ONNX fastembed + `BAAI/bge-small-zh-v1.5`**（D-M4-1，见 §13）
>
> **备选升级组合**（未来数据量或并发上来后可考虑，需走变更流程）：D1-A + D2-A + D3-A + D4-B pgvector + D5-B PostgreSQL + D6-A + D7-B Unstructured + D8-B Docker Compose
>
> **升级触发条件（参考）**：单库文档超过 5 万 chunk、或需要多实例部署、或需要按用户做 SQL + 向量混合过滤时，建议评估升级组合。

---

## 4. 系统架构（六层）

```
┌─────────────────────────────────────────────────────────┐
│ L6 交互层   Streamlit Chat UI / 知识库管理台              │
├─────────────────────────────────────────────────────────┤
│ L5 接入层   FastAPI 路由 / 鉴权(JWT) / SSE 流式 / 限流    │
├─────────────────────────────────────────────────────────┤
│ L4 编排层   Agent 状态机（意图识别→工具调用→检索→生成）  │
├─────────────────────────────────────────────────────────┤
│ L3 能力层   天气工具 | RAG 检索器 | 文档解析 Pipeline    │
├─────────────────────────────────────────────────────────┤
│ L2 数据层   向量库(Chroma/pgvector) | 关系库 | 文件存储  │
├─────────────────────────────────────────────────────────┤
│ L1 基建层   LLM 网关 | 配置管理 | 日志/追踪 | 异步任务   │
└─────────────────────────────────────────────────────────┘
```

**层间规则（硬约束）**

1. 依赖只能**自上而下**：L6 → L5 → L4 → L3 → L2 → L1，禁止反向 import。
2. **L1 基建层可被所有层引用**（配置、日志属全局设施，是唯一例外）。
3. **L2 数据层只能被 L3 能力层访问**。L4 / L5 **不得**直接连数据库。
   > **L4 的一个必要澄清（M4 落地时明确）**：`AgentState` 里有一个 `db` 字段，看起来像 L4 碰了数据库，**实际不是**——它只是由 service（L3-L4 之间的编排层）放进来的**会话句柄**，检索节点把它**原样转交**给 L3 的检索器，节点自己既不构造会话也不 import `crud`。SQL 仍然只存在于 `crud/`。所以这条规则约束的是「谁写 SQL」，而不是「谁能持有会话对象」。
4. L5 接入层**不得**包含业务逻辑，只做参数校验、鉴权、调用 L4、返回结果。

---

## 5. 目录结构（草案，待技术栈确认后微调）

```
weather-agent/
├── CLAUDE.md                    # 本文件
├── README.md
├── .env.example                 # 配置模板（不含真实密钥）
├── requirements.txt
├── pyproject.toml
├── app/
│   ├── main.py                  # FastAPI 入口
│   ├── core/                    # 【L1】配置、日志、安全、异常
│   │   ├── config.py            # 环境变量集中管理
│   │   ├── logging.py           # 结构化日志 + trace_id
│   │   ├── security.py          # JWT 签发/校验、密码哈希
│   │   └── exceptions.py        # 统一异常与错误码
│   ├── db/                      # 【L2】数据库连接、会话、迁移
│   │   ├── session.py
│   │   └── base.py
│   ├── models/                  # 【L2】ORM 表模型
│   │   ├── user.py
│   │   ├── conversation.py
│   │   └── document.py
│   ├── schemas/                 # 横切层：请求/响应契约
│   │   ├── chat.py
│   │   ├── knowledge.py
│   │   └── user.py
│   ├── llm/                     # 【L1】LLM 网关（统一抽象）
│   │   ├── gateway.py
│   │   └── prompts.py
│   ├── tools/                   # 【L3】Agent 可调用的工具
│   │   ├── weather.py           # 天气 API 封装
│   │   └── base.py              # 工具基类/协议
│   ├── rag/                     # 【L3】知识库能力
│   │   ├── loader.py            # 文档解析
│   │   ├── splitter.py          # 文本切分
│   │   ├── embedder.py          # 向量化
│   │   ├── vectorstore.py       # 向量库封装
│   │   └── retriever.py         # 检索 + 重排
│   ├── agent/                   # 【L4】编排
│   │   ├── graph.py             # 状态机定义
│   │   ├── state.py             # 状态数据结构
│   │   └── nodes.py             # 各节点实现
│   ├── services/                # 【L3-L4】业务服务层
│   │   ├── chat_service.py
│   │   ├── knowledge_service.py
│   │   └── user_service.py
│   ├── crud/                    # 【L2】数据库唯一出口
│   │   ├── user.py
│   │   ├── conversation.py
│   │   └── document.py
│   ├── api/                     # 【L5】路由
│   │   ├── deps.py              # 依赖注入（当前用户、DB session）
│   │   └── v1/
│   │       ├── chat.py
│   │       ├── knowledge.py
│   │       ├── auth.py
│   │       └── health.py
│   └── worker/                  # 【L1】异步任务（文档解析）
│       └── tasks.py
├── frontend/                    # 【L6】Streamlit（M5 落地）
│   ├── app.py                   # 入口：登录态 + st.navigation 多页
│   ├── api_client.py            # 后端 API 封装（统一信封 / SSE 解析 / 错误翻译）
│   └── views/                   # 三个页面的渲染逻辑
│       ├── login.py             # 登录 / 注册
│       ├── chat.py              # 对话（会话管理 + 流式 + 混合输入）
│       └── knowledge.py         # 知识库管理
├── data/                        # 本地数据（向量库文件、上传文件）
│   ├── uploads/
│   └── chroma/
├── tests/
│   ├── test_rag.py
│   ├── test_tools.py
│   └── test_api.py
└── scripts/
    ├── init_db.py
    └── ingest_sample_docs.py
```


**M1 落地后与上表的差异（2026-10-12 补齐）**

- 新增 `alembic/` 与 `alembic.ini` —— 数据库迁移机制（§1 企业级要求），上表草案未列。
- 新增 `crud/health.py` —— 健康探测的数据库访问，理由见 §13。
- 新增 `pytest.ini`、`tests/conftest.py`、`tests/test_auth.py` —— 测试基建与 M1 用例。
- 新增 `scripts/smoke_m1.py` —— 对运行中的服务跑鉴权主链路的冒烟脚本。因迁移改用 Alembic，上表草案里的 `scripts/init_db.py` 不再需要。
- `api/v1/auth.py`、`api/v1/health.py`、`api/deps.py` 已按 M1 实现；`api/v1/conversations.py` 为 §7 补充项，M4 落地。

**M2 落地后与上表的差异（2026-10-12 补齐）**

- 新增 `tools/cache.py` —— 通用 TTL 缓存（带命中统计）。上表草案未列，但 §1 要求「外部 API 失败有降级」且和风免费版有日配额，缓存是必需件。
- 新增 `tools/weather.py` 除原定「天气 API 封装」外，还内含**门面函数** `get_weather(city, date)`（归一化 + 缓存 + 供应商分发）与 `cache_stats()` / `clear_cache()`。
- 新增 `schemas/weather.py` —— 天气的归一化契约（`ResolvedLocation`/`CurrentWeather`/`DailyForecast`/`LifeIndex`/`WeatherResult`）。放在 `schemas/` 而非 `tools/`，因为它是**跨层对外契约**，M4 的 Agent 与未来可能的 HTTP 端点都读它。
- 新增 `tests/test_weather.py`（40 用例，`httpx.MockTransport` 打桩；current / daily / indices 三个样本已于 2026-10-12 换成**从真实接口逐字节抓取**的响应，错误体样本亦取自真实服务器）与 `scripts/check_weather_api.py`（校准脚本，用于核对路径与字段）。
- **`services/` 与 `api/v1/` 本轮无新增**：M2 交付的是进程内工具，没有 HTTP 端点（理由见 §7 补充约定）。

**M3 落地后与上表的差异（2026-10-12 补齐）**

- `rag/` 四个模块全部落地：`loader.py`（解析）、`splitter.py`（切分）、`embedder.py`（向量化）、`vectorstore.py`（向量库封装）。上表草案里的 `retriever.py`（检索 + 重排）**未落地**——检索属 M4，M3 只做「入库」。
- `rag/embedder.py` 引入 `Embedder` 协议（Protocol）与 `PlaceholderEmbedder` 占位实现，属对 §5 清单的补充，理由见 §13（M3-Q1）。
- 新增 `worker/tasks.py` 落地，但**刻意不含任何业务逻辑**，只有 `run_guarded` / `schedule_guarded` 两个防护函数。理由：§4 规定 L1 不得反向 import L3，若把「解析文档」写进 worker 就破坏了依赖方向。
- 新增 `models/document.py`、`crud/document.py`、`schemas/knowledge.py`、`services/knowledge_service.py`、`api/v1/knowledge.py` —— 即 §5 列的「新增功能模块固定 6 处点位」全部命中。
- 新增 `alembic/versions/*_m3_documents_and_document_chunks.py` —— M3 迁移（2 张表 + 5 索引 + 2 外键）。
- 新增 `tests/test_knowledge.py`（22 用例）与 `scripts/smoke_m3.py`（端到端冒烟，起真实 uvicorn 进程）。
- `data/uploads/` 目录首次真正被使用；文件按 `{UPLOAD_DIR}/{user_id}/{hash前16位}_{安全文件名}` 落盘（每用户一个子目录）。

**M4 落地后与上表的差异（2026-10-12 补齐）**

- `rag/retriever.py` **已落地**（M3 时它被有意留空），但**不含重排 rerank**——M4-Q3 选的是纯向量 top_k + 阈值，做重排属新的技术决策。
- `rag/vectorstore.py` 补上 `query()`（检索）。`where` 被做成必填的关键字参数。
- **新增 `app/llm/` 目录**（`gateway.py` + `prompts.py`）：上表草案未列。理由：D6 选的是「OpenAI 兼容统一网关」，它得有个落点；把网关与提示词单独成层，是为了让 Agent 节点只认 `messages` 进、文本出，不认任何具体厂商。
- **`app/agent/` 比上表草案多两个文件**：`extract.py`（城市/日期启发式抽取，纯函数、可独立单测）与 `graph.py`（拓扑 + 单例）。上表只列了 `graph.py` / `state.py` / `nodes.py`，`extract.py` 属补充。
- 原本列为「可选」的 `router.py`（`agent/router.py`）**已落地**——Q4C 的「规则优先 + LLM 兜底」需要它承载两份词表。
- `crud/conversation.py`、`api/v1/conversations.py`、`services/chat_service.py` 全部从「草案」变为「已实现」（§7 的三个会话端点）。
- `crud/document.py` 补 `list_chunks_by_vector_ids()`：检索拿到 `vector_id` 后要回关系库取正文，且**要再校验一次归属**。
- 新增 `schemas/chat.py`（问答与会话的请求 / 响应 / 4 个 SSE 事件模型）。
- 新增 `tests/test_chat.py`（44 用例）与 `scripts/smoke_m4.py`（61 项端到端冒烟，**内置本地假上游**）。
- `scripts/` 里上表草案的 `init_db.py` / `ingest_sample_docs.py` 仍不落地：建库改由 Alembic 负责，示例文档改由冒烟脚本临时生成（不往仓库塞测试数据）。
- ⚠️ `frontend/`（L6 Streamlit）**仍未落地**，属 M5。因此目前 L6 只有目录占位，问答能力只能经 HTTP 使用。

**M5 落地后与上表的差异（2026-10-12 补齐）**

- `frontend/` **已落地**，但**目录结构与上表草案不同**：上表写的是 `pages/1_chat.py` 这种「旧式多页」目录约定，M5 实际用的是 Streamlit 1.60+ 的 `st.navigation` + `st.Page(callable)` 新多页 API——页面是**函数不是文件**，所以拆成 `views/` 目录放三个 `render()` 函数，`app.py` 把它们包成 `st.Page` 再导航。这样页面逻辑可单测、也不受「文件名前缀决定排序」的旧约束。
- 新增 `tests/test_frontend.py`（16 用例，`httpx.MockTransport` 打桩）：覆盖登录 / 注册、非流式与 SSE 流式解析、统一错误翻译、会话三件套、知识库六件套的请求正确性。
- 上表草案里的 `pages/3_settings.py`（设置页）**未落地**——M5-Q1 拍板的三页是「登录 / 对话 / 知识库」，没有设置页；设置类功能（改后端地址、看日志）留到 M6/M7。
- `requirements.txt` 加 `streamlit>=1.60`。**前端复用后端已有的 `httpx` 依赖**（同步 `httpx.Client`），不新增 HTTP 客户端库。
- 后端 **M5 无任何 API 改动**：前端只是「多了一个调用后端既有接口的壳」。`main.py` 版本号仍为 `0.4.0-M4`（后端功能未变），`health.py` 里程碑字段仍为 `M4`——这两处留到「后端有实质变更」的里程碑再动。

**M6 落地后与上表的差异（2026-10-12 补齐）**

- 新增 `core/rate_limit.py` —— 进程内滑动窗口限流器（`SlidingWindowLimiter`）。上表草案未列，但 §1 要求「外部 API 有降级与自我保护」，限流是 M6 的加固件。
- 新增 `api/middleware.py` —— 限流中间件（`rate_limit_middleware` + `_group_of` 档位划分 + `_client_ip` 提取）。它挂在 `trace_and_access_log` 之后（内层），保证 429 也能进访问日志并拿到 trace_id。
- 新增 `models/audit.py`（`audit_logs` 表）、`crud/audit.py`、`services/audit_service.py` —— 审计日志三件套，命中 §5「新增功能模块固定 6 处点位」里的 models / crud / services 三处；但**刻意不建 `api/v1/audit.py`**（审计是内部埋点，不对外暴露查询接口）。
- 新增 `core/metrics.py` —— 自写 Prometheus 指标（counter / summary + `render()`），零依赖。新增 `api/v1/metrics.py`（`GET /metrics`，唯一不返回统一 JSON 信封的接口）。
- 新增 `alembic/versions/76b10bcb1154_m6_audit_logs.py` —— 建 `audit_logs` 表 + 复合索引 `ix_audit_logs_user_created`（`user_id`, `created_at`）。
- 新增 `tests/test_rate_limit.py`（8 用例）、`tests/test_metrics.py`（5 用例）、`tests/test_audit.py`（3 用例）、`tests/test_llm_resilience.py`（2 用例）—— M6 共 18 项新增单测。
- 改动（埋点/接线，非新增模块）：`core/config.py`（M6 配置段 + `_validate_m6`）、`main.py`（挂限流中间件 + trace 里绑 `client_ip_var` + 埋 http 指标 + **版本 → `0.5.0-M6`**）、`api/v1/health.py`（**里程碑 → M6**）、`core/logging.py`（`client_ip_var` ContextVar）、`schemas/common.py`（`ErrorCode.RATE_LIMITED=3009`）、`llm/gateway.py`（`max_retries` + LLM 指标埋点）、`llm/prompts.py`（`LLM_UNAVAILABLE_ANSWER`）、`services/chat_service.py`（LLM 兜底 + chat 审计埋点）、`services/user_service.py` / `api/v1/auth.py` / `api/v1/conversations.py` / `services/knowledge_service.py`（各处审计埋点）、`rag/retriever.py`（检索耗时）、`tools/weather.py`（天气调用/缓存命中指标）、`api/v1/__init__.py`（挂载 metrics router）、`tests/conftest.py`（autouse 关闭限流）、`scripts/smoke_m4.py`（里程碑断言同步 M6）。
- ⚠️ **兑现了 §5 M5 差异里的一个约定**：M5 时「后端无实质变更，`main.py` 版本与 `health.py` 里程碑不动」。M6 是后端实质变更（新中间件、新端点、新表），故本轮把 `main.py` 版本 `0.4.0-M4` → `0.5.0-M6`、`health.py` 里程碑 `M4` → `M6`，并同步把 `scripts/smoke_m4.py` 的里程碑断言更新为 `M6`（它作为历史冒烟脚本仍可用）。

**M7 落地后与上表的差异（2026-10-12 补齐）**

- 新增 `scripts/serve.py`（跨平台一键启动：迁移 → 后端 uvicorn → 前端 Streamlit，日志落 `logs/`、PID 落 `logs/*.pid`）与 `scripts/stop.py`（按 PID 文件清理残留进程）。
- 新增根目录四件薄封装：`start.sh` / `stop.sh`（Git Bash / Linux / macOS）、`start.bat` / `stop.bat`（Windows cmd）。它们只负责「定位 venv 的 Python」然后转调 `scripts/serve.py` / `stop.py`——真正的逻辑在 Python 里，避免 `.sh`/`.bat` 各写一套。
- 新增 `Dockerfile`（后端/前端共用一镜像，`python:3.12-slim` + `libgomp1`）、`docker-compose.yml`（backend + frontend 两服务，共享 `./data` 数据卷）、`.dockerignore`（排除 `.venv` / `data` / `.env` 等）。
- 新增 `.env.example` 的 **M6 配置段**（`RATE_LIMIT_*` / `METRICS_ENABLED` / `LLM_MAX_RETRIES`）——这是 M6 遗留的文档缺口，本轮补齐。
- 改动 `.gitignore` 加 `logs/`。
- **M7 不新增任何后端代码**（无 models/schemas/crud/services/api 改动），纯部署与文档层：这正是「部署与文档」里程碑的定位。

**新增一个功能模块时需要改动的固定点位**（这是分层架构的"代价"，也是它的价值）：

1. `models/` 加表模型
2. `schemas/` 加请求/响应模型
3. `crud/` 加数据访问函数
4. `services/` 加业务逻辑
5. `api/v1/` 加路由
6. `api/v1/__init__.py` 挂载 router

> M3 是对上面这条「固定点位」清单的第一次完整实操演练：6 处点位全部走到，且还额外命中 `alembic/versions/`（加表必带迁移）与 `tests/`、`scripts/`（新增能力必带用例与冒烟）。

---

## 6. 数据模型（v0.5：五张表全部定稿并落地）

| 表                 | 关键字段                                                                                                                       | 说明                        |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------- | ------------------------- |
| `users`           | id, username, email, hashed_password, is_active, created_at, updated_at                                                     | 用户                        |
| `conversations`   | id, user_id(FK), title, created_at, updated_at                                                                              | 会话                        |
| `messages`        | id, conversation_id(FK), role, content, tool_calls(JSON), tokens, created_at, updated_at                                     | 消息                        |
| `documents`       | id, **user_id(FK)**, filename, **file_hash**, **stored_path**, **content_type**, size, status, chunk_count, **error_message**, created_at, updated_at | 知识库文档                     |
| `document_chunks` | id, document_id(FK), chunk_index, content, **vector_id**, **char_count**, **meta(JSON)**, created_at, updated_at              | 切分后的片段（`vector_id` 指向向量库） |

> 加粗字段是 M3 定稿时相对 §6 旧草稿的**新增/明确项**。

**设计要点**

- `file_hash`（文件内容哈希）用于**去重**：同一文件重复上传直接返回已有记录，不重复消耗 embedding 额度。
- `document.status` 取值：`pending / parsing / embedding / ready / failed`，前端据此显示进度。
- `document_chunks` 在关系库存一份正文原文，向量库只存向量 + `chunk_id`。**理由**：便于按 ID 精确回查与删除，避免只依赖向量库。

**M1 定稿补充（2026-10-12）**

- 已落地并由 Alembic 迁移创建：`users`、`conversations`、`messages`（含 4 个索引、2 个外键级联删除）。
- 三张表都带 `created_at` / `updated_at`，由数据库生成、按 UTC 存储。
- 主键统一为**自增整数**：单机 SQLite 用不上 UUID 的分布式特性，自增更易读、便于排查。
- `messages.role` 取值约定：`user` / `assistant` / `tool`。

**M3 定稿补充（2026-10-12）** —— 字段形状由 M3-Q1~Q4 的四个决策直接决定：

| 定稿项 | 结论 | 对应决策 / 理由 |
| --- | --- | --- |
| 去重唯一键 | **`(user_id, file_hash)` 联合唯一**，不是 `file_hash` 单列 | M3-Q4「每用户私有」。若只按 `file_hash` 去重，B 上传与 A 相同的文件会被判定为「已存在」并拿到 **A 的记录**——直接越权 |
| `content_type` | 新增（可空） | 前端显示文件类型图标 + 排查「为什么这个文件解析失败」 |
| `error_message` | 新增（Text，可空） | 承载 `status=failed` **为什么**失败。没有它，用户只看到「失败」两个字无从下手 |
| `status` 加索引 `ix_documents_status` | 新增索引 | 启动补偿要扫「卡在 parsing/embedding 的任务」，按状态过滤是高频路径 |
| `char_count` | 新增 | 切分质量自检（某块长度异常说明切分参数有问题），不依赖重新解析 |
| `(document_id, chunk_index)` 唯一约束 | 新增 | 防止重复投递导致同一文档写入两份同名片段 |
| `vector_id` 生成规则 | **确定性**：`doc{document_id}-chunk{chunk_index}` | 不是 uuid。重建索引时同一片段得到同一 id，旧向量能被**准确覆盖**，而不是留下一堆孤儿向量 |
| `meta` 用途 | 记录切分参数（`chunk_size` / `chunk_overlap` / 切分器名） | M3-Q2 选的是递归切分，没有标题层级可存；存参数便于判断「改了参数后要不要重建索引」 |
| 外键级联 | 两条都带 `ondelete="CASCADE"` | 数据库层兜底；ORM 层另有 `cascade="all, delete-orphan"` 双保险 |
| 删除语义 | 物理删除 | 与 §7 一致，`documents` 不引入软删除字段 |

**存储路径规则**（M3 定稿）：文件落盘到 `{UPLOAD_DIR}/{user_id}/{file_hash前16位}_{安全文件名}`。

- 按 `user_id` 分子目录：即便有人绕过数据库直接看磁盘，不同用户的文件也不混在一起。
- 文件名带 hash 前缀：同名文件不会互相覆盖。
- 文件名经 `sanitize_filename` 清洗（去路径分隔符、去非法字符、截断但**保留扩展名**），否则 `../../x.dll` 这类文件名可以往项目外写文件。

**M4 定稿补充（2026-10-12）** —— 会话与消息两张表在 M4 开始真正被写入：

| 定稿项 | 结论 | 理由 |
| --- | --- | --- |
| `messages.tool_calls` 的实际内容 | 存**运行痕迹** JSON：`{intent, intent_source, short_circuit, notes, sources}` | 没有它，前端就无法展示「这条回答依据了哪几个片段」「这轮是不是短路的」，而这些信息在图跑完就没了。用 JSON 列存，结构怎么改都不用动表 |
| `messages.tokens` 的取值 | 存 `total_tokens`；**上游没返回用量时存 NULL，不存 0** | 0 会被当成「确实零消耗」，NULL 才是「不知道」——两者在计费对账时含义完全不同 |
| `conversations.updated_at` | **由 `add_message` 显式 UPDATE 一次** | `onupdate=func.now()` 只在会话这一行本身被 UPDATE 时触发；新增消息不碰会话行，于是「会话列表按最近活跃排序」会静默失效（列表顺序永远停在创建时刻） |
| 删除会话 | 对 `messages` / `conversations` 各发一条**批量 DELETE**，随后 `db.expunge()` | ORM 级联只作用于「已加载进会话的子对象」。用 `add_message(conversation_id=...)` 写入的消息**不在** `conversation.messages` 集合里，靠级联删不掉，会留下孤儿消息 |
| 消息排序能力 | `list_messages_page(page=1)` = **最新一页** | 聊天界面的惯例是先看最近说了什么，与普通列表接口「第 1 页是最旧的」相反 |

> ⚠️ **SQLite 时间戳精度限制**：`CURRENT_TIMESTAMP` 只精确到**秒**。同一秒内创建的两个会话时间戳完全相同，排序会退化成按 id 兜底。日常使用（两次提问间隔远超一秒）不受影响，但写测试时必须把参照对象的时间戳挪到不同日期才能观察到差异。

---

## 7. API 契约（v0.7：M1–M4 全部实现；天气仍为进程内工具，无 HTTP 端点）

| 方法     | 路径                                    | 说明                         | 鉴权 | 状态 |
| ------ | ------------------------------------- | -------------------------- | -- | -- |
| POST   | `/api/v1/auth/register`               | 注册                         | 否  | ✅ M1 |
| POST   | `/api/v1/auth/login`                  | 登录，返回 JWT                  | 否  | ✅ M1 |
| GET    | `/api/v1/auth/me`                     | 当前用户信息                     | 是  | ✅ M1 |
| POST   | `/api/v1/chat`                        | 提问（请求体 `stream=true` 时返回 SSE） | 是  | ✅ M4 |
| GET    | `/api/v1/conversations`               | 会话列表（分页）                   | 是  | ✅ M4 |
| GET    | `/api/v1/conversations/{id}/messages` | 历史消息（分页，`page=1` 为最新页）     | 是  | ✅ M4 |
| DELETE | `/api/v1/conversations/{id}`          | 删除会话（连同其消息）                | 是  | ✅ M4 |
| POST   | `/api/v1/knowledge/documents`         | 上传文档（**202**，multipart）    | 是  | ✅ M3 |
| GET    | `/api/v1/knowledge/documents`         | 文档列表（分页）                   | 是  | ✅ M3 |
| GET    | `/api/v1/knowledge/documents/{id}`    | 文档详情（含切分预览）                | 是  | ✅ M3 |
| PUT    | `/api/v1/knowledge/documents/{id}`    | 更新文档（覆盖重解析，**202**，multipart） | 是  | ✅ M3 |
| DELETE | `/api/v1/knowledge/documents/{id}`    | 删除文档及其向量                   | 是  | ✅ M3 |
| POST   | `/api/v1/knowledge/reindex`           | 全量重建索引（**202**）            | 是  | ✅ M3 |
| GET    | `/api/v1/health`                      | 健康检查                       | 否  | ✅ M1 |
| GET    | `/api/v1/health/ready`                | 就绪检查（含 DB / 向量库 / LLM 连通性） | 否  | ✅ M1 |

**统一响应结构**

```json
{ "code": 0, "message": "ok", "data": { }, "trace_id": "..." }
```

**错误码约定**：`0` 成功；`1xxx` 参数错误；`2xxx` 鉴权错误；`3xxx` 业务错误；`5xxx` 服务端错误。

**补充约定（2026-10-12 定稿）**

- 已实现（M1）：`/auth/register`、`/auth/login`、`/auth/me`、`/health`、`/health/ready`。
- 鉴权凭证：`Authorization: Bearer <token>`；`/docs` 右上角 Authorize 可直接填 token 调试。
- 列表接口统一分页：`?page=1&page_size=20`，响应 `data` 为 `{items, total, page, page_size}`（见 `schemas/common.paginated`）。
- `/chat` 的流式与非流式**共用同一路由**：请求体加 `stream: bool`；`true` 返回 SSE（`text/event-stream`），`false` 返回完整 JSON。
- `/conversations` 系列将放在**新增的** `api/v1/conversations.py` —— 这是对 §5 文件清单的补充，M4 落地。
- `conversation.title`：新建时为「新会话」，首条消息落库后截取前 20 字更新。
- `documents` 删除为**物理删除**，同时删除向量库中对应向量，不引入软删除字段。
- `/health/ready`：必需组件全部就绪返回 200，否则 503。M1 的必需组件只有 `database`；`vector_store` / `llm` / `weather` 标 `required=false`，否则会恒返回 503 而失去信号意义。
- **天气能力当前没有 HTTP 端点（M2 定稿）**：`app/tools/weather.py` 的 `get_weather(city, date)` 是进程内工具，由 M4 的 Agent 直接调用，不经过 HTTP。`/health/ready` 中的 `weather` 组件只探测「是否配置了 provider」，不发起真实外部请求。若日后要加 `/api/v1/weather` 供手工演示，属新增接口，须先按 §2.4 决策。

**M3 知识库接口补充约定（2026-10-12）**

- **上传类接口返回 `202 Accepted` 而不是 `201 Created`**。语义差异不是形式主义：文档此刻只是被**接收**了，还在 `pending`，真正的入库在后台跑。前端应轮询 `GET /knowledge/documents/{id}` 看 `status` 从 `pending → parsing → embedding → ready/failed` 的变化。用 201 会暗示「资源已创建完成」，前端可能直接去读 `chunk_count` 而拿到 0。
- **接收方式为 `multipart/form-data`，字段名固定 `file`**。因此这两个接口不能只用 JSON 客户端调，需要 `python-multipart`（已在 requirements）。
- **详情接口的 `chunks` 是「预览」不是全量**：只返回前 `DOCUMENT_PREVIEW_CHUNKS`（默认 5）条，完整数量在 `total_chunks`。一篇 20 页 PDF 可能切出上百块，全量返回会让响应体暴涨，而用户真正想看的只是「切得对不对」。
- **响应体刻意不暴露 `stored_path` 与 `file_hash`**：前者是服务器内部路径（泄露等于告诉别人本机目录结构），后者是内容指纹（对用户无意义，且会让前端有机会探测「某个文件是否已存在」）。
- **「不存在」与「属于别人」返回同一个 404**，不区分。区分开就等于送出一个探测接口：攻击者可用错误码差异枚举出「哪些文档 ID 真实存在」。
- **`POST /knowledge/reindex` 不接受任何参数，且不做破坏性动作**。它是全库行为（不属于某个用户），因此只要求登录、不按 `user_id` 过滤；相应地它只「重新投递排队」，不删任何人的数据。
- 上传错误码：不支持的类型 → **415**（`PARAM_ERROR`）；超过大小上限 → **413**。
- **`GET /knowledge/documents/{id}` 的 `error_message`**：仅 `status=failed` 时有值，说明失败原因（如 `文档解析失败：x.pdf：PdfStreamError: ...`）。

**M4 问答接口补充约定（2026-10-12）**

`POST /api/v1/chat` 的请求体（`ChatRequest`）：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `question` | 是 | 问题正文。空白串直接 400；超过 `CHAT_MAX_QUESTION_CHARS`（默认 2000）也 400 |
| `conversation_id` | 否 | 不传 = 新建会话；传了必须属于当前用户，否则 404（不区分「不存在」与「别人的」） |
| `stream` | 否 | 默认 `false`；`true` 时返回 SSE |
| `city` / `date` | 否 | 显式指定城市 / 日期（`YYYY-MM-DD`）。**显式传入的优先级高于从问题里猜**（见 §8.2 的抽取说明） |

**非流式**（`stream=false`）的 `data` 结构：

```json
{
  "conversation_id": 1, "message_id": 2,
  "answer": "……", "intent": "knowledge", "intent_source": "rule",
  "sources": [{"type": "knowledge", "label": "差旅制度.txt#第1段", "snippet": "…", "score": 0.60, "document_id": 1}],
  "short_circuit": false, "notes": ["…"], "usage": {"prompt_tokens": 11, "completion_tokens": 36, "total_tokens": 47}
}
```

**流式**（`stream=true`）是 SSE，事件顺序**固定**为 `meta → delta* → done`，中途失败则把最后一项换成 `error`：

| 事件 | 数据 |
| --- | --- |
| `meta` | `{conversation_id, intent, intent_source, sources}` |
| `delta` | `{text}` — 一小段文本，0..N 次 |
| `done` | `{conversation_id, message_id, answer, short_circuit, notes, usage}` |
| `error` | `{code, message}` |

- `meta` **必须最先发**：前端拿到 `conversation_id` 才能立刻定下 URL 或侧边栏（新建会话时尤其重要），等全部答完再给会让用户看到几十秒空白。它由 `compose` 节点跑完时发出——那一刻天气与检索结果都已定案，信息量最大。
- **`meta` 与 `delta` 是两类不同性质的数据**，不要合并：`sources` 在 `compose` 就已确定，文本要到 `generate` 才逐段产生。硬合成一个事件，等于把「依据已确定」这件事推迟到第一个 token 出现。
- **短路路径（`short_circuit=true`）也必须发至少一个 `delta`**。此时 `generate` 根本没执行、一个 token 都不会产生；若只发 `meta`+`done`，一个「只认 delta 渲染」的前端会显示空白，而 `done.answer` 里明明有完整答案。`stream_events` 用 `delta_sent` 标志补发这一个 delta。
- **SSE 帧必须带尾随空行**（`event: X\ndata: {...}\n\n`）。少这一个空行，浏览器会把后续事件一直挂起不交付——表现是「服务端明明推了，前端一个都收不到」。
- **响应头必须带 `Cache-Control: no-cache` 与 `X-Accel-Buffering: no`**。前者：逐 token 的东西一旦被缓存，第二个用户会拿到第一个人的回答；后者：Nginx 等反向代理默认会攒够一块再转发，本机直连看不出来，一放到代理后面「流式」就变成「等半天然后一次性出现」。
- **出现 `error` 事件时 HTTP 状态码已经是 200**。流一旦开了，后续失败就不可能再用状态码表达，只能靠这个显式事件告诉前端「这轮没答完」。少了它，前端会把截断的回答当成完整回答存下来。
- **流式路径的数据库写入由 service 自建会话完成**，不复用请求级 `session`（生成器在路由函数返回后才被消费，那时请求级会话可能已关闭）。同理，请求内能做完的事（找会话、取历史、落用户消息）**全部在 `prepare()` 里先做完**。
- **用户消息先落库、助手消息成功后再落库**。反过来的话，答到一半断线会让用户那句话凭空消失（用户会以为是自己的问题）；而现在最坏只留下一条「有问无答」，用户能直观看到上一条没答上。宁可留中间态，也不要丢用户输入。
- `messages` 角色的历史会转成 LangChain 消息喂回模型，但 **`tool` 角色一律丢弃**：工具返回值已经体现在最终回答里，硬塞进去只会让模型看到一堆它没发起过的「工具输出」。
- `/conversations` 系列的三个端点**共用同一个 404**（不存在 / 属于别人 / 已删除），不给出可区分的错误，避免变成枚举会话 ID 的探测接口。
- **`GET /conversations` 的响应刻意不带消息内容**：列表页只需要标题与时间，带上最后一条消息就得为每个会话多查一次；真要看内容，前端点进去调 `/messages`。这让列表接口的响应体大小与会话长度无关。

---

## 8. RAG 流程详解

### 8.1 入库链路（上传 / 更新）—— M3 已落地

```
用户上传文件（multipart，字段名 file）
  → 读入内存（读 limit+1 字节，超出即判定超大 → 413）
  → 校验后缀是否受支持（否则 415）
  → 计算 sha256(file_hash)，按 (user_id, file_hash) 查重
        命中 → 直接返回已有文档，deduplicated=true，不重复消耗 embedding
  → sanitize 文件名 → 落盘 {UPLOAD_DIR}/{user_id}/{hash前16}_{安全名}
  → 创建 document 记录，status=pending
  → 返回 202 + 投递后台任务
      → 解析：loader 提取纯文本                  status=parsing
      → 切分：splitter 递归切分（中文分隔符优先）    （同属 parsing 阶段）
      → 向量化：embedder 批量调用                  status=embedding
      → 写入：先清旧向量（向量库）+ 清旧片段（关系库），再写新的
      → status=ready / failed
  → 前端轮询 GET /knowledge/documents/{id} 看 status 变化
```

**M3 已定稿的参数与实现**（`app/rag/` 与 `knowledge_service.py`）：

| 环节 | 实现 | 关键点 |
| --- | --- | --- |
| 解析 | `rag/loader.py`（自写，D-M4-2） | 文本类（txt/md/csv 等）按 `utf-8 → utf-8-sig → gbk → gb18030` 依次尝试解码（中文 Windows 导出的 csv 常是 gbk，只试 utf-8 会直接失败）；PDF 用 `pypdf` 逐页提取、DOCX 用 `docx2txt`；解析在 `asyncio.to_thread` 里跑，**不在事件循环里做同步阻塞 IO** |
| 切分 | `rag/splitter.py` | `RecursiveCharacterTextSplitter`，`chunk_size=800` / `chunk_overlap=100`，**分隔符列表换成中文优先**：`["\n\n", "\n", "。", "！", "？", "；", "…", "，", "、", " ", ""]`，`keep_separator="end"` |
| 向量化 | `rag/embedder.py` | **本地 ONNX 真实模型**：`fastembed` + `BAAI/bge-small-zh-v1.5`（512 维、90MB、MIT 许可）。见下方说明 |
| 写入 | `rag/vectorstore.py` | Chroma `PersistentClient`，集合 `metadata={"hnsw:space": "cosine"}`；所有方法经 `asyncio.to_thread` 包装 |
| 调度 | `api/v1/knowledge.py` 的 `_dispatcher` | 用 FastAPI `BackgroundTasks`；经 `worker/tasks.run_guarded` 保证异常一定进日志 |

> **为什么切分器必须换中文分隔符**：LangChain 默认分隔符是英文那套（`\n\n`、`\n`、`" "` 等）。中文句子之间**没有空格**，默认配置会在找不到空格时一路退化到「按字符硬切」，把「住宿标准按城市分级」切成「住宿标准按城」+「市分级」。这是中文 RAG 最常见的质量事故，且不会报错，只会让检索质量悄悄变差。

> **✅ 向量化已换成真实模型（D-M4-1 选 A，2026-10-12）**：本地 ONNX 路线（`fastembed` + `BAAI/bge-small-zh-v1.5`，512 维）。选它而不是 PyTorch 的关键原因：**`onnxruntime` 本来就随 chromadb 装进来了**，所以只需多一个包 + 一个 90MB 模型，不必引入 torch 那 2.5GB。
>
> 实测（`scripts/check_embedding.py`，8 项全过）：同一文本向量完全一致（确定性）、语义有效（「住宿费上限」对住宿条款 0.69，对故障分级 0.32）、小语料检索 Recall@1 = 4/4。真实向量写进 Chroma 后仍可被独立进程读回并正确检索（`smoke_m3.py --real-embedding`，33 项全过）。
>
> **两条环境约束必须记住**：
> 1. **`huggingface.co` 在本机不可达**（实测 ReadTimeout）。模型下载默认走镜像 `HF_ENDPOINT=https://hf-mirror.com`。模型只在首次运行时下载，之后完全离线；离线部署把 `data/models/fastembed` 整个目录拷过去即可。
> 2. **换模型必须重建索引**。不同模型的向量不在同一坐标系，混在一起检索毫无意义。`vectorstore.py` 有维度护栏能拦住「维度不同」的情况，但**同维度的不同模型它测不出来**——这条只能靠人记住。
>
> **`PlaceholderEmbedder` 保留但降级为测试专用**：它结构真、语义假，只用于离线测试与不联网的冒烟（`smoke_m3.py` 默认模式）。非 dev 环境仍被配置校验硬拦截。

**关键参数**：`chunk_size` / `chunk_overlap`（已定稿，见上表）、切分策略 = 递归字符切分（不做标题层级切分）、embedding 模型 = **`BAAI/bge-small-zh-v1.5`（已定稿）**。

### 8.2 检索链路（提问）—— M4 已落地

```
用户问题
  → 意图识别（规则优先 → LLM 兜底）           agent/router.py     判出 weather / knowledge / mixed / chat
  → 条件分支（LangGraph 条件边）              agent/graph.py
         weather   → 查天气节点
         knowledge → 检索节点
         mixed     → 先查天气，再检索
         chat      → 直接组装
  → 检索节点：查询向量化 → Chroma top_k（where={"user_id": 当前用户}）
              → 余弦相似度阈值过滤 → 按 vector_id 回关系库取正文
  → 组装 prompt：系统提示 + 天气数据 + 检索片段（带来源标注）+ 对话历史
  → 【短路判断】需要知识库却一条都没检索到 / 只问天气却没取到 → 固定文案，不经模型，直接结束
  → 调用 LLM（流式）→ 逐 token 经 SSE 返回前端
  → 落库：用户消息、助手消息（含意图 / 依据 / 短路标记 / token 消耗）
```

**M4 已定稿的实现**：

| 环节 | 实现 | 关键点 |
| --- | --- | --- |
| 意图识别 | `agent/router.py`（Q4C：规则优先 + LLM 兜底） | 天气词表约 70 词、知识库词表约 80 词，**全部两字以上**（单字太容易误伤：「风险」「风口」会被当成天气）。纯问候另判，省一次模型调用。规则判不出才走 LLM，LLM 不可用则退回 `chat` |
| 城市 / 日期抽取 | `agent/extract.py`（启发式，非语义理解） | 抽取顺序见下；**调用方显式传 `city` / `date` 时优先于抽取** |
| 向量检索 | `rag/retriever.py`（Q3A：纯向量 top_k + 距离阈值） | `where={"user_id": ...}` 是**必填关键字参数**；阈值过滤放在查正文**之前** |
| 图编排 | `agent/state.py` / `nodes.py` / `graph.py`（Q1B） | 5 节点 + 3 条件边；**不用 checkpointer** |
| 模型调用 | `llm/gateway.py`（Q2A：`langchain-openai`）+ `llm/prompts.py` | 消息进、文本/用量出，不认具体厂商；换供应商只改 `.env` 三个值 |

**三类分支的实际走向**（`nodes.route_after_*`）：

- `weather` → 天气节点 → 组装；`knowledge` → 检索节点 → 组装；`mixed` → 天气节点 → **继续**检索节点 → 组装；`chat` → 组装。
- **混合意图先查天气**，且 `fetch_weather` 失败时**不抛异常**、只记进 `notes` 并把 `weather` 置空——否则「天气挂了」会连带把知识库那半边的回答也弄没。
- 混合意图下天气挂了、知识库却有内容时，组装 prompt 会**显式注入一句提示**（「本次未能取到天气数据，请直接说明没能查到天气，不要推测或引用其它来源的数字」）。少了它，模型会顺着上下文把知识库里的天气描述当成实时天气来说。

**M3 已为该链路铺好的地基**：向量库写入时，每个向量的 metadata 都带 `user_id` 与 `document_id`。检索必须带上 `where={"user_id": <当前用户>}`——这是 M3-Q4「每用户私有」在检索侧的兑现点。**仅靠「入库时隔离」是不够的**：没有这个过滤条件，一次向量检索就能命中别人的文档片段。

**用户隔离做在两个独立的地方**（两道锁，不是一道）：

1. 向量库侧：`where={"user_id": ...}` 过滤。它被做成**必填的关键字参数**，漏传直接 `TypeError`——否则「忘了一个过滤条件」这件事不会有任何报错，只会安静地越权。
2. 关系库侧：按 `vector_id` 回查正文时**再带一次 `user_id` 做连表校验**。正文不存在向量库里（见 §6），所以取正文是一次新的数据库访问；两次过滤独立成立，等于把「越权拿到别人文档」压在两个条件上而不是一个。

> `scripts/smoke_m4.py` 的 [9] 就是这条的端到端兑现：**让用户 B 问同一个问题**——B 的知识库为空。若少了过滤，B 会命中 A 的片段并给出回答；实测 B 得到的是固定文案「知识库中未找到相关内容」，且不回传任何知识库依据。

**城市 / 日期抽取的能力边界（必须清楚）**：这是**启发式**，不是语义理解。它做不到：

- **指代消解**：「那后天呢」里的「那」指哪个城市——需要多轮上下文理解。
- **省略主语**：「要带伞吗」没提城市——抽不出来。
- **同音错字、别名**（沪 / 魔都）。

所以有一条兜底：**调用方显式传 `city` / `date` 时优先使用，规则只在没传时才跑**。M5 的 Streamlit 界面会用城市选择器把 `city` 直接传进来，那条路径是准确的；规则抽取服务的是「用户直接打字提问」的场景，够用即可。

**本实现刻意不做的事**（都属于新的技术决策，要做须按 §2.4 走流程）：

- **不做重排（rerank）**。§8.2 把它标为「可选」，Q3A 也没选它。做重排需要额外一个模型（如 bge-reranker）或一次额外的 LLM 调用，收益要靠评测集才能确认——在没有评测集的情况下属于「加了但不知道有没有用」。
- **不做 top_k 扩大后再截断**。Q3A 的定义就是「top_k + 阈值」，先按 top_k 取再过滤，不会出现「阈值过滤掉一些、还能替补上来」的行为。这是有意的：结果数量随问题变化，比「凑满 5 条」更诚实——凑满就得塞进不相关的片段，那正是幻觉的来源。

### 8.3 防幻觉约定

- 检索片段必须带**来源标注**（文件名 + 片段序号），在回答中注明依据来源。来源标注由 `RetrievedChunk.label` 统一生成（形如 `气象应急预案.pdf#第3段`），避免各调用点自己拼、拼出几种格式。
- 检索结果为空时，**必须**明确回答"知识库中未找到相关内容"，禁止编造。
- 天气数据必须来自真实 API 调用，**禁止**模型凭记忆回答实时天气。
- **`document_chunks.meta` 里存了切分参数**，M4 组装来源标注时可判断「这篇文档是用什么参数切的」——参数变过而没重建索引的文档，其片段边界与当前配置不一致，属可疑来源。

**M4 的强化：把「不编造」从提示词请求升级为代码保证**

把「找不到就说找不到」写进系统提示然后照常调用模型，只是**请求**模型不要说谎——它这次听了，下次不一定。M4 改成**短路（short-circuit）**：

| 触发条件 | 行为 |
| --- | --- |
| 意图需要知识库，但一条片段都没检索到 | `compose` 直接把答案定为 `NO_KNOWLEDGE_ANSWER`，**跳转到 END，`generate` 根本不执行** |
| 意图只问天气，但天气取数失败 | 同理，答案定为 `WEATHER_UNAVAILABLE_ANSWER` |

模型没有机会编，因为它**没有被调用**。这不是修辞，而是可验证的：`scripts/smoke_m4.py` 在第 [6] 与第 [10] 步，站在**本地假上游一侧数请求次数**——短路时生成接口调用次数恒为 0，并直接断言这一点。

> 为什么必须站在上游一侧数？因为应用自己无法自证「没调用模型」：没调用就不会留下任何记录。这是全链路里少见的「只能从外部观测」的性质，也是冒烟脚本不可被单元测试替代的原因之一。

**五道防线的分工**（前四道在天气解析，第五道在这里）：

1. `_parse_payload` 错误体优先（RFC 7807）；
2. `_reject_all_none` 拦截「字段全 None 的正常对象」；
3. `_to_str` 拒绝容器类型（`str(dict)` 会绕过第 2 道）；
4. `fetch_daily` 区分「键缺失（报错）vs 空列表（合法）」并拦截「全天日期解析失败」；
5. **`compose` 短路**：宁可说「没找到」，也不给模型编造的机会。

**提示注入的边界**：`build_context_section` 用 `【天气数据】` / `【参考资料】` 两个小标题显式包裹资料区。模型对「这段是原始数据、不是用户说的话」的区分依赖这种显式边界，否则容易把文档里的内容当成新指令。资料放在**最后一条用户消息里**（而不是单独一条 system 消息）：多数模型对「同一条消息内的上下文」贴合度更好，且资料与问题的距离最近。

---

## 9. 天气数据源（v0.5：已确认和风天气 QWeather，M2 已落地）

### 9.1 供应商标选记录

| 方案                       | 说明           | 优点                             | 缺点                       |
| ------------------------ | ------------ | ------------------------------ | ------------------------ |
| **A. 和风天气 QWeather**【✅ 用户选定】 | 国内主流商用天气 API | ① 中文数据源准确，覆盖中国城市好 ② 有免费额度，文档中文 | ① 需注册申请 key ② 免费版有调用次数限制 |
| **B. OpenWeatherMap**    | 国际主流天气 API   | ① 全球覆盖好 ② 免费额度大                | ① 国内城市精度一般 ② 文档英文为主      |
| **C. 中国气象局公开数据**         | 官方数据         | ① 权威                           | ① 接口不稳定、非标准，接入成本高        |

**统一抽象要求**：无论选哪家，必须封装在 `tools/weather.py` 之后，暴露统一接口（如 `get_weather(city, date)`），上层不感知具体供应商。换供应商只改这一个文件。

### 9.2 和风天气 API 现状（2026-10-12 实测确认）

**这是一次破坏性改版，老教程里的路径几乎全部失效**：

| 项目        | 旧（网上多数教程）                     | 新（实测确认）                                                |
| --------- | ---------------------------- | --------------------------------------------------------- |
| 版本前缀      | `/v7/weather/now`            | `/weather/v1/current/{lat}/{lon}`（v1）                     |
| 定位参数      | `?location=101010100`（城市 ID） | **经纬度走路径参数**：`/weather/v1/current/{lat}/{lon}`            |
| API Host  | 全局 `devapi.qweather.com`     | **每个项目独立分配**，形如 `xxxxx.re.qweatherapi.com`，必须走 `WEATHER_API_HOST` |
| 认证        | `?key=<API KEY>`（查询参数）       | **请求头** `X-QW-Api-Key: <key>`；或 JWT（**EdDSA 算法**）             |
| **错误格式**  | `{"code": "401", "message": "..."}` | **RFC 7807 `application/problem+json`**：`{"error": {"status", "type", "title", "detail"}}` |
| API KEY 政策 | 长期有效                         | 2027 年起对 API KEY 逐步限流，官方推荐迁移到 JWT                          |

**判据**（这条让「路径对不对」在无真实 key 时也能判定）：
HTTP **401/403** = 请求已到达认证/授权层 → **路由真实存在**；HTTP **404** = 路由未注册 → **该路径不存在**。

**实测路由地图**：

| 路径 | 实测 | 结论 |
| --- | --- | --- |
| `/weather/v1/current/{lat}/{lon}` | 401 | ✅ 路由存在（在用） |
| `/weather/v1/daily/{lat}/{lon}` | 401 | ✅ 路由存在（在用） |
| `/weather/v1/hourly/{lat}/{lon}` | 401 | ✅ 路由存在（M2 未实现） |
| `/weather/v1/now/{lat}/{lon}` | 404 | ❌ 不存在——别把 v7 的 `current` 写成 v1 的 `now` |
| `/geo/v2/city/lookup` | 403 | ✅ 路由存在（在用） |
| `/geo/v1/city/lookup`、`/v2/city/lookup` | 404 | ❌ 旧城市搜索路径已下线 |
| `/v7/indices/3d`、`/v7/indices/1d` | 403 | ✅ 路由存在（在用 3d） |
| `/v7/indices/7d` | 404 | ❌ 不存在（指数只到 3d） |
| `/indices/v1/3d` | 404 | ❌ **原 AI 猜测，已修正** |
| `/v7/weather/now`、`/v7/weather/3d` | 403 | ✅ 路由存在（旧天气族，本项目不用） |

**⚠️ 两级 Host 策略（最关键的事实）**：同一台共享 Host、同一个假 key 下——

```
/weather/v1/*  → 401 Unauthorized   = 共享 Host 可路由，只是 key 无效
/geo/v2/*      → 403 Invalid Host   = 必须用「项目专属 Host」才放行
/v7/*          → 403 Invalid Host   = 同上
```

也就是说「城市搜索」与「生活指数」在共享 Host 上**必然失败，与 key 是否正确无关**。本地自测时不要因为这两步返回 403 就以为代码写错了。

### 9.3 响应体校准结果（2026-10-12 用真实凭据完成）

**§9.2 的路径层与本节字段层均已校准**，唯一例外是城市搜索（见 §9.3.3）。

#### 9.3.1 先纠正一个认知：v1 与 v7 是两套形状，不只是改了字段名

| 维度 | 旧 v7（网上教程几乎都是这套） | 新 v1（2026-10-12 实测） |
| --- | --- | --- |
| 结构 | 扁平，包在 `{"now": {...}}` 里 | **扁平顶层，无包装键** |
| 数值 | 字符串 `"22"` | **带单位的对象** `{"value": 22.51, "unit": "°C"}` |
| 湿度 | `"65"`（百分数） | `0.71`（**0–1 比例**） |
| 降水概率 | `"80"`（百分数） | `0.7`（**0–1 比例**） |
| 风速 | `"12"`，单位含糊 | `{"value": 0.47, "unit": "m/s"}`（契约要求 km/h，**须 ×3.6**） |
| 风向 | `"东南风"`（中文） | `{"degree": 114, "compass": "ese"}`（**须换算**） |
| 预报日期 | `fxDate: "2026-10-12"` | `forecastStartTime: "2026-10-11T16:00Z"`（**UTC，须转东八区**） |
| 预报列表键 | `daily` | **`days`** |
| 生活指数 | `{"code": "200", "daily": [...]}` | 同左（**这一族仍是旧信封**，不是 RFC 7807） |

**最容易静默出错的三处**——它们都不会报错，只会给出错误数值或日期：

1. **湿度 / 降水概率**：把 `0.71` 直接当百分数会显示成「湿度 1%」；
2. **风速**：`0.47 m/s` 不换算会被当成 `0.47 km/h`（差 3.6 倍）；
3. **预报日期**：UTC 不转东八区会让整条预报**集体差一天**，用户问「明天」会答成今天。

解析层已按实测结构重写，换算集中在 `_ratio_to_pct` / `_speed_to_kmh` / `_local_date_str` /
`_wind_direction` 四个助手里，每个都配了单测（见 §11 第 22–26 条）。

#### 9.3.2 生活指数的两个必填约定

1. **`type` 是必填参数**：缺了直接 `400 Missing Parameter`（`invalidParams: ["location","type"]`）。代码传 `type=0` 表示全部类型，一次返回 16 类 × 3 天 = 48 条。
2. **`location` 用经纬度 `"经度,纬度"` 有效**，不必先拿 LocationID。这一点在当前**尤其重要**——城市搜索不可用时，生活指数仍能独立工作。

#### 9.3.3 ⛔ 城市搜索：403 Security Restriction（唯一未通项）

**现象**：同一 Host、同一 KEY、同一源 IP 下，`/weather/v1/current/*`、`/weather/v1/daily/*`、
`/v7/indices/3d` 全部 **200**，而 `/geo/v2/city/lookup` 返回 **403**。

**关键证据（决定了这不是代码问题）**：该路径**不带 `location` 参数时返回 400 Missing Parameter**。
这说明请求已经通过了路径匹配与鉴权，是在**参数校验之后**的那道安全/权限关卡被拦下的。
另外换过 6 种参数组合、两种鉴权方式（请求头 `X-QW-Api-Key` 与 query `key=`）都无差别。

**结论**：按官方错误码文档，`SECURITY RESTRICTION`（403）的定义是「当前请求违反了你设置的
请求限制」；同类公开案例的结论是「GeoAPI 权限没开或未生效」。

**处置步骤（需要用户到控制台操作）**：

1. 控制台 → **项目管理** → 选中项目 → 选中凭据；
2. 查 **API 限制**：若开了白名单，确认 GeoAPI / 城市搜索在允许列表内（或直接关闭该限制——官方称默认即允许所有 API）；
3. 查 **应用限制**：若设了 **IP 白名单**，本机出口公网 IP 必须在内（家庭宽带 IP 会变，测试期建议先留空 = 允许所有 IP）；若设了 **网址（Referer）限制**，服务端调用不带 Referer，**必然被拒**；
4. 改完**等待权限生效**——官方提示安全设置变更最长 **1–2 小时**生效，新建凭据通常更快。

**影响面**：「城市名 → 经纬度」这一步会抛 `WeatherAuthError`（503），因此**暂时不能用城市名提问**；
其余三个接口不受影响。一旦权限开通，`resolve_city` 无需改代码即可工作（解析逻辑已按 v2 结构写好，
只差实测确认字段名）。

> 校准方式：`.venv/Scripts/python.exe scripts/check_weather_api.py 上海`
> 它打印 4 个接口的原始状态码与原始返回体，并在汇总里按 `error.type` 区分两种 403（`invalid-host` 与 `security-restriction`）。

#### 9.3.4 防「假成功」防线（现为 5 道）

| 防线 | 位置 | 作用 |
| --- | --- | --- |
| 错误体优先解析 | `_parse_payload` | HTTP 200 但 body 带 `error` 时**不当成功** |
| 全 None 拦截 | `_reject_all_none` | 实时天气一个字段都认不出来 → 报错 |
| **容器值不算解析成功** | `_to_str` | 命中 dict 时 `str(dict)` 会产生非空字符串，**曾真实绕过上面那道拦截**；现在 `_to_str` 拒绝容器类型 |
| 键缺失与空列表区分 | `fetch_daily` | 「键不存在」= 猜错 → 报错；「键存在但为空」= 合法 → 正常返回 |
| **全天日期解析失败拦截** | `fetch_daily` | 列表拿到了但每条都认不出日期 → 报错，而不是静默返回空预报 |

> `tests/test_weather.py` 里的 current / daily / indices 样本已是 **2026-10-12 从真实接口
> 逐字节抓取**的响应（含一份昆明的雨天样本，专门验证非零的比例换算——上海那两天恰逢无雨，
> 全是 0 的样本验证不了换算）。仅 `GEO_BODY` 仍是构造样本：该接口拿不到成功响应。

---

## 10. 里程碑与交付物（待用户确认节奏）

| 阶段 | 内容            | 交付物                          |
| -- | ------------- | ---------------------------- |
| M0 | 技术选型确认 + 项目骨架 | CLAUDE.md（定稿）+ 可启动的空壳项目      |
| M1 | 基础设施          | 配置、日志、DB 表、鉴权、健康检查           |
| M2 | 天气工具          | 天气查询 API + 天气工具封装 + 测试       |
| M3 | 知识库入库         | 上传 → 解析 → 切分 → 向量化 → 入库 全链路  |
| M4 | 检索问答          | 检索器 + Agent 编排 + 流式回答        |
| M5 | 前端界面          | Streamlit 登录页 / 对话页 / 知识库管理页 |
| M6 | 企业级加固         | 限流、重试、审计日志、指标、错误兜底           |
| M7 | 部署与文档         | 一键启动脚本 / Dockerfile / README |
| M8 | 桌面版打包         | 单文件 exe（PyInstaller）+ 验收脚本 |

**当前进度**：M0 ✅ 已完成（2026-10-12）｜M1 ✅ 已完成（2026-10-12）｜M2 ✅ 已完成（2026-10-12，字段名已用真实凭据逐项校准；城市搜索待控制台开通权限，见 §9.3.3）｜M3 ✅ 已完成（2026-10-12）｜**M4 ✅ 已完成（2026-10-12）**｜**M5 ✅ 已完成（2026-10-12）**｜**M6 ✅ 已完成（2026-10-12）**｜**M7 ✅ 已完成（2026-10-12）**｜**M8 ✅ 已完成（2026-10-13）**

**M3 交付物清单（2026-10-12）**

| 类别 | 交付物 |
| --- | --- |
| 数据模型 | `models/document.py`（documents + document_chunks）+ Alembic 迁移 `8f5255627c5b`（2 表 / 5 索引 / 2 外键） |
| RAG 能力 | `rag/loader.py`（多编码 + PDF/DOCX）、`rag/splitter.py`（中文分隔符递归切分）、`rag/embedder.py`（Embedder 协议 + 占位实现）、`rag/vectorstore.py`（Chroma 封装） |
| 数据访问 | `crud/document.py`（所有查询带 `user_id` 条件） |
| 契约 | `schemas/knowledge.py`（ChunkOut / DocumentOut / DocumentDetail / UploadResult / ReindexResult） |
| 业务 | `services/knowledge_service.py`（上传 / 处理 / 删除 / 覆盖 / 重建 / 启动补偿） |
| 路由 | `api/v1/knowledge.py`（6 个端点） |
| 基建 | `worker/tasks.py`（`run_guarded` / `schedule_guarded`，不含业务逻辑） |
| 验证 | `tests/test_knowledge.py`（22 用例）+ `tests/test_rag.py`（22 用例，离线）+ `scripts/smoke_m3.py`（端到端冒烟）+ `scripts/check_embedding.py`（向量化自检，8 项） |
| M4 前置改造（2026-10-12） | `rag/embedder.py` 新增 `FastembedEmbedder`；`rag/loader.py` 改为自写解析；`core/config.py` 增 6 项配置 + provider 白名单校验；`requirements.txt` 移除 `langchain-community`、加入 `fastembed` |

**每个阶段的开工前，AI 必须再次列出该阶段的实现方案让用户选择，不得直接开写。**

**M4 交付物清单（2026-10-12）**

| 类别 | 交付物 |
| --- | --- |
| L1 基建 | `llm/gateway.py`（统一网关 + 异常翻译 + 测试替身接缝）、`llm/prompts.py`（系统提示 / 意图提示 / 两个固定文案 / 渲染助手） |
| L2 数据 | `crud/conversation.py`（**新建**：会话与消息的全部读写，一律带 `user_id`）、`crud/document.py` 补 `list_chunks_by_vector_ids` |
| L3 能力 | `rag/retriever.py`（**新建**：`RetrievedChunk` + `retrieve()`）、`rag/vectorstore.py` 补 `query()`（`where` 必填） |
| L4 编排 | `agent/state.py`（`Intent` / `IntentSource` / `AgentState`）、`agent/extract.py`（城市日期启发式抽取）、`agent/router.py`（规则词表 + LLM 兜底）、`agent/nodes.py`（5 节点 + 3 条件边）、`agent/graph.py`（拓扑 + 单例） |
| L5 接入 | `api/v1/chat.py`（含 SSE 帧化）、`api/v1/conversations.py`（**新建**：3 个端点）、`api/v1/__init__.py`（挂载两个 router）、`api/v1/health.py`（`llm` 组件） |
| 横切契约 | `schemas/chat.py`（`ChatRequest` / `ChatAnswer` / 4 个 SSE 事件模型 / 会话三个出参） |
| 业务 | `services/chat_service.py`（`prepare` / `answer` / `stream_events`，含流式自建会话） |
| 变更 | `requirements.txt` 加 `langgraph` / `langchain-openai` / `langchain-core`；`core/config.py` 加 6 项配置 + `_validate_chat`；`main.py` 版本 → `0.4.0-M4`；`api/v1/health.py` 里程碑 → M4；`.env` / `.env.example` 更正模型名与 base_url |
| 验证 | `tests/test_chat.py`（**44 用例**，分 7 组：意图规则 / 抽取 / 检索阈值 / 图分支与短路 / L2 数据访问 / L5 端到端 / LLM 网关）、`scripts/smoke_m4.py`（**61 项**端到端冒烟，内置本地假上游，默认离线且零额度） |

**M4 验收结果（2026-10-12）**

| 项 | 结果 |
| --- | --- |
| pytest 全量 | **138 项通过**（M1–M3 的 94 项无回归 + M4 新增 44 项），全程离线、不下载模型 |
| 端到端冒烟 | `scripts/smoke_m4.py` **61 项通过**（真实 uvicorn 进程 + 真实 512 维向量 + 真实 SSE） |
| 真实 DeepSeek 连通 | ✅ 非流式 `'收到'`，用量 `{prompt: 35, completion: 23, total: 58}`；流式 9 个增量 → `1,2,3,4,5`，用量 `{prompt: 39, completion: 82, total: 121}`。**验证了 `stream_usage=True` 这一易漏开关：流式响应确实带回了 token 用量** |
| 图分支冒烟 | ① chat 规则命中且不查任何东西；② 空检索短路、**模型调用 0 次**；③ 天气失败短路、**模型调用 0 次**；④ 混合意图天气失败但知识库有内容 → 仍走模型 |

**M5 开工前必须重新列方案让用户选择**（前端页面结构、会话交互、流式渲染方式、知识库管理页的呈现），不得直接沿用本文件的任何隐含假设。

**M5 交付物清单（2026-10-12）**

| 类别 | 交付物 |
| --- | --- |
| L6 前端入口 | `frontend/app.py`（登录态判断 + `st.navigation` 多页 + 侧边栏退出登录） |
| L6 后端封装 | `frontend/api_client.py`（`BackendClient`：统一信封 `{code,message,data}` 解析、SSE 逐事件 yield、`BackendError` 统一错误翻译；`default_client()` 读 `API_BASE_URL`） |
| L6 登录页 | `frontend/views/login.py`（登录 / 注册两个 tab；注册成功后**再调 login 拿 token**——后端注册不直接发 token） |
| L6 对话页 | `frontend/views/chat.py`（侧边栏会话管理 + 历史渲染 + `st.empty()` 打字机流式 + delta/done 兜底 + 混合输入） |
| L6 知识库页 | `frontend/views/knowledge.py`（上传 + 就地轮询 + 覆盖重传 + 删除 + 重建索引 + 失败原因展示） |
| L6 视图包 | `frontend/views/__init__.py`（`@st.cache_resource` 跨 rerun 复用 `BackendClient`） |
| 变更 | `requirements.txt` 加 `streamlit>=1.60`（前端复用已有 `httpx`，不新增 HTTP 客户端库） |
| 验证 | `tests/test_frontend.py`（**16 用例**，`httpx.MockTransport` 打桩）；真实后端 + Streamlit 端到端确认（首页 200、注册→登录→闲聊、SSE 帧格式逐项核对） |

**M5 验收结果（2026-10-12）**

| 项 | 结果 |
| --- | --- |
| pytest 全量 | **154 项通过**（M1–M4 的 138 项无回归 + M5 新增 16 项前端单测），全程离线 |
| 前端单测 | `tests/test_frontend.py` **16 项通过**：登录 / 注册（不含 token）/ 非流式 chat / SSE 事件序列 / SSE 坏帧容错 / HTTP 错误翻译 / 业务码翻译 / 连接失败 / 会话三件套 / 知识库六件套 |
| 真实后端连通 | ✅ 起真实 uvicorn + Streamlit：后端 `/health` 200、Streamlit 首页 200、注册→登录→闲聊全链路跑通（`intent=chat`、返回完整回答） |
| SSE 契约核对 | ✅ `event: meta/delta/done` 帧格式与 `_iter_sse` 解析逐项一致（`data` 内含 `event` 字段与 `text` 字段） |

**M6 交付物清单（2026-10-12）**

| 类别 | 交付物 |
| --- | --- |
| 限流（Q1） | `core/rate_limit.py`（`SlidingWindowLimiter`：`deque` 时间戳 + `time.monotonic()`）、`api/middleware.py`（`rate_limit_middleware` + `_group_of` 三档 + `_client_ip`）、`schemas/common.py` 加 `ErrorCode.RATE_LIMITED=3009` |
| 审计（Q2） | `models/audit.py`（`audit_logs` 表，`user_id` 可空、无外键、`detail` JSON）、`crud/audit.py`（`record` / `list_recent`）、`services/audit_service.py`（`record` 包 try/except）、迁移 `76b10bcb1154`；埋点覆盖 register / login / chat / upload / delete / update / delete_conversation |
| 指标（Q3） | `core/metrics.py`（counter / summary + `render()` Prometheus 文本）、`api/v1/metrics.py`（`GET /metrics`）；埋点覆盖 http 请求/耗时（路由模板 label）、LLM 调用/token/错误、检索耗时、天气调用/缓存命中 |
| LLM 弹性（Q4） | `llm/gateway.py` 加 `max_retries`、`llm/prompts.py` 加 `LLM_UNAVAILABLE_ANSWER`、`services/chat_service.py` 非流式/流式两条兜底路径 |
| 验证 | `tests/test_rate_limit.py`（8 用例）+ `tests/test_metrics.py`（5）+ `tests/test_audit.py`（3）+ `tests/test_llm_resilience.py`（2） |

**M6 验收结果（2026-10-12）**

| 项 | 结果 |
| --- | --- |
| pytest 全量 | **172 项通过**（M1–M5 的 154 项无回归 + M6 新增 18 项），全程离线 |
| 限流 | 滑动窗口放行/拒绝/窗口滑动/重置/key 隔离、档位划分、豁免、中间件 429 统一信封、关闭限流 8 项全过 |
| 审计 | register / login 埋点 + trace_id 捕获 3 项全过 |
| 指标 | counter 渲染 / label 排序 / summary sum+count / reset / `/metrics` 端点 5 项全过 |
| LLM 兜底 | 非流式 503 + `LLM_UNAVAILABLE_ANSWER`（不含 `API_KEY`/异常类名）、流式 error 事件同文案 2 项全过 |
| 迁移 | `alembic upgrade head` → `76b10bcb1154`，`audit_logs` 表 + 复合索引 `ix_audit_logs_user_created` 已落地 |

**M7 交付物清单（2026-10-12）**

| 类别 | 交付物 |
| --- | --- |
| 一键启动 | `scripts/serve.py`（迁移 → 后端 → 前端，日志/PID 管理，`sys.executable` 定位 venv）、`scripts/stop.py`（按 PID 清理残留） |
| 薄封装 | `start.sh` / `stop.sh`（Git Bash / Linux / macOS）、`start.bat` / `stop.bat`（Windows cmd） |
| Docker | `Dockerfile`（`python:3.12-slim` + `libgomp1`，前后端共用一镜像）、`docker-compose.yml`（backend + frontend 两服务 + `./data` 数据卷 + 自动迁移）、`.dockerignore` |
| 运维手册 | `README.md` 新增「部署（M7）」章：一键启动 / Docker / 监控（Prometheus 抓取示例）/ 备份恢复 / 故障排查 / 升级迁移 |
| 补缺 | `.env.example` 补 M6 配置段（9 项）；`.gitignore` 加 `logs/` |

**M7 验收结果（2026-10-12）**

| 项 | 结果 |
| --- | --- |
| pytest 全量 | **172 项通过**（M7 无后端代码改动，零回归），全程离线 |
| 一键脚本冒烟 | `serve.py` / `stop.py` **三次端到端**：迁移 → 后端 `/health` 200（milestone=M6）→ 前端首页 200 → 停止后端口释放、无残留进程 |
| Docker | `docker compose config -q` 通过；`docker build` 因本机 Docker Desktop 守护进程未启动而跳过（Dockerfile 为标准写法，交付后在有守护进程的机器上可验证） |
| 文档 | README 部署章 + CLAUDE.md §5/§10/§13/§14 同步 |

---

**M4 前置阻塞项：已于 2026-10-12 全部解除** ✅

| 前置项 | 结论 | 决策 |
| --- | --- | --- |
| 向量化实现 | 本地 ONNX `fastembed` + `BAAI/bge-small-zh-v1.5`（512 维） | D-M4-1 选 A |
| `langchain-community` sunset 处置 | 自写解析，移除该依赖 | D-M4-2 选 A |

**M4 开工所需的 LLM 凭据已于 2026-10-12 获得**：`LLM_BASE_URL=https://api.deepseek.com`、`LLM_MODEL=deepseek-flash`、`LLM_API_KEY`（真实密钥，写在 `.env` 内，已在 `.gitignore` 内）。至此 M4 的四个决策点（Q1B / Q2A / Q3A / Q4C）与全部外部资源都已齐备并落地。

---

## 11. 编码规范

- Python 版本：3.11+（使用类型注解新语法）
- 格式化：`ruff format` + `ruff check`（待确认是否采用）
- 类型注解：所有函数必须标注参数与返回值类型
- 命名：文件/函数 `snake_case`，类 `PascalCase`，常量 `UPPER_SNAKE`
- 注释：解释"为什么"，不解释"是什么"
- 禁止：在代码里硬编码密钥、密码、API key（一律走环境变量）
- 禁止：在异步函数里调用同步阻塞操作（如 `requests`、`time.sleep`）
- 数据库：所有访问必须经 `crud/` 层，禁止在路由或服务层直接写 SQL
- **异步数据库约定（M1 起，硬约束）**：
  1. 涉及数据库的路由与 `crud/` 函数一律 `async def` + `await`，禁止在异步函数里调用同步驱动。
  2. 会话必须 `expire_on_commit=False`（见 `db/session.py`）—— 否则 commit 后再读对象属性会触发懒加载，异步上下文中直接抛 `MissingGreenlet`。
  3. ORM 关系字段必须声明 `lazy="selectin"`，禁止依赖懒加载；需要关联数据时用 `selectinload` 显式加载。
  4. `alembic.ini` 必须保持纯 ASCII：Alembic 用系统区域编码读取它，中文 Windows 下任何非 ASCII 字节都会导致 GBK 解码失败。
  5. 时间戳由数据库生成（`server_default=func.now()`），不用 Python 的 `datetime.now()`，避免应用与数据库时钟/时区不一致。
- JWT：`decode` 必须显式传 `algorithms` 白名单，防止 `alg: none` 伪造攻击；密码哈希只走 bcrypt，`SECRET_KEY` 短于 32 字节直接启动失败。
- **外部 API 调用约定（M2 起）**：
  1. 外部 HTTP 一律用 `httpx.AsyncClient` + `await`，**禁止** `requests`（会阻塞事件循环）。
  2. 重试只针对**可恢复**错误：网络异常与 5xx 才重试，**4xx 一律不重试**（密钥错、参数错重试一万次也不会好，只会烧配额）。退避用指数（0.5s → 1s → 2s）。
  3. 缓存**只存副本、只返副本**：`TTLCache` 里放的对象与返回给调用方的对象必须是不同实例（用 `model_copy(deep=True)`），否则调用方就地改一个字段就永久污染缓存。两条分支（命中 / 未命中）行为必须一致。
  4. 非关键的附加数据（如生活指数）失败必须降级为「该项为空」，**不得让整个天气查询失败**。
  5. 供应商字段名一律经 `_pick(raw, *keys)` 的候选名列表读取，不写 `raw["text"]` 这种单点硬编码——上游改字段名时要能一处改完。
  6. 未能在无凭证条件下核对的上游路径/字段，必须在代码里显式标注「待校准」并配自检脚本，**不得假装已验证**。
  7. **禁止「HTTP 200 即成功」**：错误可能藏在响应体里（和风 v1 用 RFC 7807 的 `{"error": {...}}`）。解析顺序必须是「先看错误体，再看业务码，最后才认成功」。
  8. **禁止返回「字段全为 None 的正常对象」**：那会让上层以为"数据本来就是这样"，用户拿到「今天气温 None 度」。识别不出任何已知识别字段时必须抛错，并把顶层键名带进错误信息，便于校准。
  9. **区分「键不存在」与「键存在但为空」**：前者说明字段名猜错（要报错），后者是合法响应（要正常返回）。
  10. **路径与字段的写法改动必须留下证据**：判定上游路由是否存在，用「401/403 = 路由存在、404 = 路由未注册」这一判据实测，并在代码注释里贴出实测结果，禁止凭直觉抄网上教程。**补充判据（2026-10-12 新增）：「400 = 已通过鉴权与安全校验，只是参数不全」，可用来区分「路由/权限问题」与「参数问题」——城市搜索的 403 就是这样判定的：它不带参数时返回 400，说明卡在参数校验之后。**
  11. **v1 与 v7 的数据形状必须分开对待**：和风 v1（`/weather/v1/*`）返回**扁平顶层 + 带单位的嵌套对象 + 0–1 比例 + UTC 时刻**，与 v7（`{"now": {...}}` + 字符串 + 百分数 + 中文风向）完全不同。解析层不得假设「只是改了字段名」。实测对照表见 §9.3.1。
  12. **比例与百分数必须显式换算，并写成可单测的助手**：上游 `humidity` / `precipitation.probability` 是 0–1 比例，而对外契约（`schemas/weather.py`）是 0–100 整数百分比。`0.71` 直接当百分数会显示「湿度 1%」——**不报错，只是数值错**。统一走 `_ratio_to_pct`。
  13. **带单位的量必须先读 `unit` 再换算，禁止写死系数**：上游风速单位是 `m/s`，契约要求 `km/h`，须 ×3.6。但换算要写成「读 unit → 决定系数」（`_speed_to_kmh`）；写死 ×3.6 的话，上游哪天改成 km/h，7.88 会被算成 28.4。
  14. **UTC 时刻必须转东八区后再取日期**：预报日期是 `forecastStartTime: "2026-10-11T16:00Z"`（= 北京时间 10-12 00:00）。不转时区会让整条预报**集体差一天**，用户问「明天」会答成今天。统一走 `_local_date_str` / `_local_time_str`。
  15. **取值助手必须拒绝容器类型**：`_to_str` 遇到 dict/list 一律返回 `None`。因为 `str({"text": "阴"})` 会得到非空字符串，让「一个字段都没认出来」被误判成「解析成功」，从而绕过全 None 拦截——**这个洞在 2026-10-12 校准中真实存在过**。
  16. **「列表拿到了但一条都没解析出来」同样要报错**：`fetch_daily` 在每条都认不出日期时必须抛错，否则会静默返回空预报，用户问「明天要带伞吗」得到空白却无人察觉。
  17. **必填查询参数不得靠「先跑通再说」省略**：`/v7/indices/3d` 的 `type` 就是必填，缺了返回 `400 Missing Parameter`。凡是凭印象写下的参数，都要用真实凭据回测一遍。
- **知识库入库约定（M3 起）**：
  1. **向量库与文件系统的操作必须全部 `async`**：`chromadb` 与文件读写都是同步阻塞库，直接 `await` 前必须经 `asyncio.to_thread` 包装。在事件循环里同步跑 Chroma，等同于把整个服务的并发能力降到 1。
  2. **`crud/document.py` 的所有查询函数必须强制带 `user_id` 参数**。用户隔离做在数据访问层而不是散落在各处「查完再判断归属」：忘了判断还能跑，忘了传 `user_id` 则函数签名直接报错。仅有的例外是 `get_by_id_any_user`，它**只允许后台任务调用**，路由层禁止使用。
  3. **`vector_id` 必须确定性生成**（`doc{document_id}-chunk{chunk_index}`），禁止用 uuid。重建索引时同一片段要得到同一个 id，旧向量才能被准确覆盖；用 uuid 会在向量库里堆积孤儿向量，且这些孤儿仍会被检索命中。
  4. **删除顺序固定为「先删向量 → 再删记录 → 最后删文件」**。倒过来的话，中途失败就会留下「记录已没了但向量还在」的孤儿向量——它们会被检索命中，用户会看到「来源是已删除的文档」。删文件失败只记警告，不回滚（记录与向量已经清干净，一个残留文件不影响正确性）。
  5. **覆盖重解析 = 先清旧再写新，顺序不能反**。先写新的会撞上 `(document_id, chunk_index)` 唯一约束；且清旧必须同时清「向量库」与「关系库」两边，只清一边就会造成两边不一致。
  6. **后台任务必须幂等**：进程内任务可能被重复投递（重试、补偿、手工触发）。`process_document` 在入口先检查状态，已是 `ready` 直接返回，不重算。
  7. **后台任务必须自建数据库会话**，不得复用请求的 session——请求早已结束、session 已关闭，复用它会在异步上下文里抛 `MissingGreenlet` 之类的诡异错误。
  8. **进程内后台任务的必然代价：进程重启会让任务无声消失**。这是 M3-Q3 方案的已知缺陷，补丁是 `recover_stale_documents()`——启动时把停在 `parsing` / `embedding` 的文档重置为 `pending` 并重新投递。**任何新增「耗时后台任务」都必须同时提供启动补偿**，否则它会永久卡在中间态。
  9. **文件名一律经 `sanitize_filename` 清洗后才能落盘**：必须显式处理反斜杠（POSIX 上 `\` 不是路径分隔符，`Path(...).name` 拦不住 `..\..\x`），截断时保留扩展名，空名回退为 `unnamed`。
  10. **上传读取必须设上限**：`await upload.read(limit + 1)` 而不是无限制 `read()` 或先读全量再判大小——后者在大文件下会直接打爆内存。多读 1 字节是为了区分「刚好等于上限」与「超出上限」。
  11. **`langchain` 系列模块一律延迟导入**（放在函数内部）。它们在 import 期会加载大量子模块，放在模块顶层会让应用启动慢数秒，而多数请求根本用不到解析能力。
  12. **不要用 Python 内置 `hash()` 做任何需要跨进程一致的用途**：`PYTHONHASHSEED` 随机盐会让同一个字符串在不同进程得到不同哈希。需要确定性摘要一律用 `hashlib`。
  13. **同一模块内不要用 `delete` / `select` / `update` 这类与 SQLAlchemy 同名的函数名**。自定义函数会覆盖模块级导入的名字，导致调用点 `TypeError`（本项目的 `crud/document.py` 已踩过一次，现用 `sa_delete` 别名规避）。
- **向量化约定（M4 前置改造起）**：
  14. **向量维度必须从模型元数据派生，禁止硬编码**。`FastembedEmbedder` 通过 `TextEmbedding.list_supported_models()` 读取真实维度（该调用读包内自带清单，**不需要联网也不需要模型文件**），再与 `EMBEDDING_DIM` 交叉校验，不一致时**直接报错**。硬编码 512 在换模型时会悄悄出错。
  15. **模型名必须每次都校验，不能因为调用方传了 `dimension` 就跳过**。跳过的话，模型名拼错要等到第一次真正向量化才炸，且报错来自第三方库内部，没有候选清单有用。（这条是新增测试 `test_unknown_model_error_lists_chinese_candidates` 抓出来的实现缺陷。）
  16. **`embed_documents` 与 `embed_query` 必须是两个方法，不得合并**。多数检索模型对「查询」和「文档」的编码方式本就不对称（如 bge-zh 系列的查询指令前缀）。合并成一个方法，这个差异就没有地方落；而加错位置（比如加到文档上）**不会报错，只会让检索质量下降**。
  17. **`HF_ENDPOINT` 必须在 `import huggingface_hub` 之前设置**。该库在模块导入时就把端点读进常量，之后再改环境变量不生效。实现上放在 `_apply_fastembed_env()` 里、紧跟惰性 import 之前调用，并用 `os.environ.setdefault`（外部显式导出的环境变量优先级更高，保留临时覆盖的调试手段）。
  18. **换向量模型必须重建索引**（`POST /api/v1/knowledge/reindex`）。维度护栏只能拦住「维度不同」，**同维度的不同模型它测不出来**，混用会导致检索结果毫无意义却看不出问题。
  19. **不得重新引入 `langchain-community`**。它已 sunset 且不再接受任何修复（含安全漏洞）。`tests/test_rag.py` 里有护栏测试会拦住回退。`langchain-text-splitters` 是单独拆出的包、不在 sunset 范围，可以继续用。
  20. **解析与向量化的第三方库调用一律延迟导入**（放函数内部）。fastembed 会拉进 onnxruntime / huggingface_hub / tokenizers 一大串，放模块顶层会拖慢启动数百毫秒，而多数请求用不到。
  21. **模型对象必须惰性创建**。`fastembed.TextEmbedding(...)` 会触发模型下载（90MB），不能在应用启动或健康检查时构造，否则启动会被一个下载卡住。
- **LLM 网关约定（M4 起）**：
  22. **上层只认 `messages` 进、文本/用量出，不认任何具体厂商**（D6 的全部价值就在这一条）。换供应商（DeepSeek ↔ 通义 ↔ 本地 vLLM）只需改 `.env` 三个值，代码一行不动。
  23. **`langgraph` / `langchain-openai` / `langchain-core` 一律延迟导入**（放函数内部）。实测首次导入耗时：`langchain_core.messages` 371ms、`langgraph.graph` 1320ms、`langchain_openai` 1868ms。放在模块顶层会让应用启动慢两秒，而多数请求根本用不到问答能力。
  24. **`StateGraph(Schema)` 构造时会对 schema 调 `typing.get_type_hints()`，强制在运行时求值每一个注解**。因此 `from __future__ import annotations` 与 `TYPE_CHECKING` 的字符串注解**都不管用**——`AgentState` 里的消息字段只能写成 `list[Any]`，实际类型写在 docstring 里。这条很容易在"加个类型标注"时踩中，报错是构造期的 `NameError`。
  25. **`stream_usage=True` 必须显式打开**。OpenAI 兼容接口的流式响应**默认不返回 token 用量**，不开这个开关，流式路径的用量会全是 `None`。这是那种「不报错、只是少了一列数据」的问题。
  26. **取用量时「取不到」必须返回空字典而不是 0**，落库时存 NULL。0 会被当成「确实零消耗」，而事实是「不知道」——两者在计费对账时含义完全不同。
  27. **取文本只取 `content`，不取 `reasoning_content`**。思考模式把推理过程放在单独字段里，那是「模型的草稿」不是「给用户的答案」，混进 SSE 会让用户看到一堆没整理过的推理。
  28. **厂商异常必须逐类翻译成带处置建议的自家异常**。只回一句「LLM 调用失败」，用户看到 502 完全不知道该改什么——401（改密钥）/ 404（改模型名）/ 402（充值）/ 429（降频）/ 超时 / 连接失败，处置动作完全不同。**404 的错误信息里必须带出候选模型名**（DeepSeek 当前可用：`deepseek-flash`、`deepseek-v4-pro`；旧名 `deepseek-chat` / `deepseek-reasoner` 已下线）。
  29. **`config.llm_configured` 刻意不做启动期硬校验**。M1–M3 的功能不需要 LLM，缺密钥就拒绝启动等于让一个与 LLM 无关的改动也跑不起来。正确做法是：`/health/ready` 上报状态、`/chat` 被调用时给明确错误——**错要错在真正用到它的那一步**。
- **Agent 编排约定（M4 起）**：
  30. **短路（short-circuit）必须由代码实现，不能只靠提示词**。见 §8.3。判定放在 `compose`，跳转到 END，`generate` 不执行。
  31. **节点不碰数据库**。节点只是把 service 放进 state 的会话**原样转交**给 L3；SQL 只存在于 `crud/`。`AgentState.db` 只是一个句柄，不构成越权。
  32. **`generate` 节点必须用 `astream` 而不是 `ainvoke`**。LangGraph 的 `stream_mode="messages"` 靠 LangChain 回调管理器捕获 token，而回调挂在**当前上下文**上；异步生成器在调用方的上下文里执行，所以逐段 yield 的同时 LangGraph 仍能看到每一段。这是 SSE 能真正逐字吐出的前提（已用假模型实测确认）。
  33. **`generate` 里模型异常刻意不吞**。翻译成一句「生成失败」然后正常返回，会让一次失败的问答看起来像成功，而且会被原样写进历史——这正是 §9.3.4「假成功」的翻版。
  34. **按节点过滤 token**：`stream_mode="messages"` 的 `meta["langgraph_node"]` 必须用来只转发 `generate` 节点的增量。意图分类的 LLM 兜底调用也会产生 token 事件，不过滤会把 `weather` 这种分类结果混进用户看到的回答里。
  35. **意图识别刻意不抛异常**。判不出来时退回 `chat` 仍能正常回答（只是不检索、不查天气），比直接 500 好得多——用户可能只是在闲聊。
  36. **「预警」这类跨语境的词不许放进任何一侧词表**。它在「橙色预警对应什么处置流程」（制度语境）与「暴雨橙色预警」（天气语境）里都常见，放进任一侧都会把另一侧判错；留给两侧各自的其它词去决定，判不出来时交给 LLM 兜底——**这才是兜底该干的活**。
  37. **图刻意不用 checkpointer**。多轮上下文的权威来源是 `messages` 表；再引入一套图状态存储就变成两份对话历史需要同步，而它们必然会不一致。
  38. **`notes` 这类调试字段手工拼接，不用 LangGraph 归约器**。归约器要求该字段在入口 state 里就有初值，少一处初始化就是运行时报错——而「可有可无的调试信息」不该有能力让整条链路起不来。
  39. **条件边返回的是「路由键」而不是节点名**。`short_circuit` 在 LangGraph 里对应 `END` 常量而不是某个节点，由 `graph.py` 统一映射，避免 `nodes.py` 去 import 框架符号（也符合延迟导入约定）。
- **测试约定（M4 起，补充）**：
  40. **假模型（`GenericFakeChatModel`）的 `_stream()` 内部会回调 `_generate()`**，纳秒级产生一条假的 `invoke` 记录；而且 LangGraph 的回调会让图内的 `ainvoke` 也走流式。因此**不要按方法名断言模型被怎么调用**，只断言调用次数；必要时给替身加一个「当前在流式内部」的标志位抑制嵌套计数。
  41. **凡会命中天气意图的用例，必须默认把天气工具打桩掉**。`.env` 里配着真实凭据，不打桩的用例会真的发外部请求——测试应当是离线且可重复的。
- **前端约定（M5 起）**：
  42. **前端用同步 `httpx.Client`，不是后端的 `AsyncClient`**。Streamlit 的脚本模型是「每次交互整段重跑」，跑在普通线程里，没有也不该有事件循环；同步客户端更直接，且能通过 `transport` 注入 `httpx.MockTransport` 做单测（与后端测试的 `AsyncClient + MockTransport` 思路一致，只是同步版）。
  43. **前端只通过 `api_client.py` 访问后端，不散落 `httpx` 调用**。理由与后端的 `crud/` 一样：把「后端返回什么形状、SSE 怎么解析、错误怎么翻译」集中一处，页面只做展示与交互。
  44. **`BackendClient` 用 `@st.cache_resource` 跨 rerun 复用**。Streamlit 每次交互都重跑脚本，若不缓存，每跑一遍就新建一个 `httpx.Client`（底层是连接池）——频繁交互会不断开新连接。
  45. **注册成功后必须再调一次 `login` 拿 token**。后端 `/auth/register` 返回的是 `UserPublic`（用户信息），**不含 `access_token`**；把 register 的返回值直接当 token 存，会让「已登录」判断拿到一个 dict 而失效。两个动作分开，语义更清楚。
  46. **流式渲染必须「delta 实时拼接 + done 用完整 `answer` 覆盖」**。delta 拼接是中间态，`done.answer` 才是权威态；结束时用完整答案覆盖一次，即使丢了一个 delta、或后端短路只补发一个 delta，最终屏幕上也一定是完整答案（M5-Q2 决策的兑现点）。
  47. **前端对日期控件的校验不能替代后端校验**。前端 `YYYY-MM-DD` 正则只是**提前拦截**手滑输入（不拦就发一个必 400 的请求），真正的校验在后端 `ChatRequest.date` 的 `strptime`——前端校验失败只降级为「本次忽略该日期」，不阻塞提问。
  48. **知识库页必须轮询 `status`，不能只信上传返回**。后端入库是后台异步的（上传返回 202、文档停在 `pending`），前端必须轮询 `GET /knowledge/documents/{id}` 到 `ready/failed`，否则会把「正在入库」伪装成「已入库」，失败也浑然不觉。
- **限流约定（M6 起）**：
  49. **限流计数器是进程级单例，跨测试累积**。ASGITransport 下所有测试共享同一 client IP，auth 档「10 次/分钟」会被 `test_auth.py` 的几十个请求迅速打满，之后所有请求都拿 429（`data=None` → `'NoneType' is not subscriptable`）。所以 `tests/conftest.py` 用 autouse fixture 默认 `rate_limit_enabled=False`，限流本身在 `test_rate_limit.py` 单独测（显式开启 + 小阈值 + `_limiter.reset()`）。
  50. **限流窗口用 `time.monotonic()`，不用 `time.time()`**。`time.time()` 是墙上时钟，会随系统调表/夏令时跳动，用它做「多久前」的滑动窗口会在一瞬间放空或锁死。单调时钟只往前走，与「流逝时长」语义一致。
  51. **限流按「IP:分组」打 key，`X-Forwarded-For` 优先但只作单机防护**。反代会改写来源 IP，所以要先读 `X-Forwarded-For`；但这个头可被客户端伪造，进程内限流只能防「打爆自己」，不能防「恶意伪造来源绕过」——后者要交给网关/WAF 层。
  52. **限流中间件必须注册在 trace 之后（内层）**：这样 429 响应也会被记进访问日志、也能拿到 trace_id，否则「谁被限流了」在日志里查不到。
- **审计约定（M6 起）**：
  53. **审计日志的 `user_id` 不加外键、不加单列索引**。不加外键是因为「用户删了，审计还得留着」——审计是历史事实，不随业务数据一起消失；不加单列索引是因为复合索引 `(user_id, created_at)` 的最左前缀已经覆盖了「按 user_id 查」，再建单列是冗余。
  54. **审计失败绝不拖垮主流程**。`audit_service.record` 包 `try/except`，写不进只记 warning——一次审计写失败不该让用户的一次问答跟着失败。审计是「尽力而为」的记录，不是业务前置条件。
  55. **审计的 `ip` / `trace_id` 经 ContextVar 自动读取，不在业务层手传**。`client_ip_var` 由 trace 中间件绑定、审计落库时自动取，业务层只传「做了什么」（action / target），不关心「从哪来」——来源是横切关注点，散落到每个调用点会漏。
- **指标约定（M6 起）**：
  56. **HTTP 指标用路由模板做 label，不用原始路径**。原始路径里的会话/文档 id 是高基数 label，会让指标序列无限膨胀（`/conversations/123` 与 `/conversations/456` 是两个不同的序列）。`request.scope["route"].path` 得到的是 `/conversations/{id}` 模板，一个路由一个序列。
  57. **指标是进程内单例，`reset()` 只供测试隔离**；`render()` 里 label 与序列 key 都按字典序排序，保证同一组数据两次输出字节一致——Prometheus 抓取时 diff 不会抖动。
  58. **`GET /metrics` 是唯一不返回统一 JSON 信封的接口**。Prometheus 抓的是 `name{labels} value` 纯文本，套信封反而没法识别；它也不鉴权（不泄露业务数据、只暴露进程计数，监控系统主动抓取时没有登录态可带）。
- **LLM 弹性约定（M6 起）**：
  59. **有限重试交给 SDK 的 `max_retries`，不自己写重试循环**。`ChatOpenAI(max_retries=...)` 只对网络异常与 5xx/429 重试、4xx 不重试（内部指数退避），与 §11 第 2 条一致。自写 while 重试会绕开 SDK 的退避与错误分类，还容易把「参数错」也重试掉。**显式写出来而不用默认值**：SDK 默认行为会变，写下来才能对照排查。
  60. **LLM 最终失败时，技术原因只记日志，给用户的必须是友好兜底文案**。`LLM_UNAVAILABLE_ANSWER` 不含「检查 LLM_API_KEY」这类运维细节、也不含厂商异常类名——那些不是终端用户能处理的事，反而像把内部实现甩给用户。非流式映射为 503，流式 yield 一个 `ChatErrorEvent`。
  61. **流式兜底分支的顺序不能反**：`LLMGatewayError` 是 `AppError` 的子类，`except gateway.LLMGatewayError` 必须写在 `except AppError` **之前**，否则会先进 AppError 分支、友好兜底失效（把技术细节漏给用户）。
- **部署与脚本约定（M7 起）**：
  62. **一键脚本用 `sys.executable` 定位 Python，不在 `.sh`/`.bat` 里猜 `.venv/bin` 还是 `.venv/Scripts`**。真正的逻辑放在 `scripts/serve.py`（跨平台 Python），根目录的 `start.sh`/`start.bat` 只做「找 venv → 转调 Python」这一件事，避免同一套逻辑在 bash 与 cmd 里各写一遍。
  63. **Windows 托管 Python 的 venv 是「重定向器」：`subprocess.Popen().pid` 拿到的不是真实进程 PID**（实测 `Popen.pid=396` 而子进程 `os.getpid()=14232`）。所以杀子进程必须用 `taskkill /F /T`（连进程树一起杀），`proc.terminate()` 只杀重定向器、会留下孤儿 Python 占着端口。POSIX 没有这层重定向，`proc.terminate()` 即可。
  64. **托管 Python 带「安全删除」钩子，会拦截 `.unlink()` 并可能抛错**（把删除改成「移入回收站」，文件已不存在时抛 `OSError`）。脚本里的收尾删除（如 PID 文件）必须 `try/except OSError` 吞掉——纯簿记文件残留无害，下次启动会覆盖，绝不能让它让整个停止流程报错。
  65. **子进程日志必须落文件，不能用 `subprocess.PIPE` 接 stdout**（PIPE 没人读 → 缓冲区满 → 子进程阻塞在启动期，症状极具误导性）。这与 §九 smoke 脚本的教训一致。
  66. **Docker 镜像里 onnxruntime/fastembed 需要 `libgomp1`**（OpenMP 运行库）；数据与模型缓存都走挂载的 `./data` 卷（删容器不丢数据）；模型首次运行才联网下载、之后离线，离线部署 = 先在联网机下好模型再把 `./data` 整目录拷过去。
  67. **docker-compose 的 frontend 用 `API_BASE_URL=http://backend:8000/api/v1`（容器内服务名）**：Streamlit 是服务端渲染 + 服务端调后端，浏览器不直连 backend，所以不能用 localhost。

---

## 12. 配置项（.env）

| 变量                             | 说明                         |
| ------------------------------ | -------------------------- |
| `APP_ENV`                      | `dev` / `prod`             |
| `SECRET_KEY`                   | JWT 签名密钥，**至少 32 字节**，不足则启动即报错（RFC 7518 §3.2） |
| `JWT_ALGORITHM`                | JWT 签名算法，默认 `HS256`        |
| `ACCESS_TOKEN_EXPIRE_MINUTES`  | access token 有效期（分钟），默认 720 |
| `DATABASE_URL`                 | 数据库连接串，异步驱动须写 `sqlite+aiosqlite:///...` |
| `SQL_ECHO`                     | 是否打印每条 SQL，排查问题时才开，默认 false |
| `VECTOR_STORE_TYPE`            | `chroma` / `pgvector`      |
| `CHROMA_DIR`                   | 向量库持久化目录                   |
| `LLM_BASE_URL`                 | LLM 网关地址                   |
| `LLM_API_KEY`                  | LLM 密钥                     |
| `LLM_MODEL`                    | 模型名                        |
| `EMBEDDING_MODEL`              | 向量化模型名                     |
| `WEATHER_PROVIDER`             | `qweather` / `openweather`，默认 `qweather` |
| `WEATHER_API_KEY`              | 和风天气 API KEY，**仅经 `core/config.py` 读取**；走请求头 `X-QW-Api-Key`，不放查询参数 |
| `WEATHER_API_HOST`             | 和风为**每项目独立分配**的 Host（如 `xxxxx.re.qweatherapi.com`），不填则天气接口报 503。⚠️ `/geo/*` 与 `/v7/*` 用它之外的共享 Host 一定被 403 拒（见 §9.2） |
| `WEATHER_TIMEOUT_SECONDS`      | 单次外部请求超时，默认 `8.0`            |
| `WEATHER_MAX_RETRIES`          | 网络异常/5xx 的最大重试次数，默认 `2`（4xx 不重试） |
| `WEATHER_CACHE_TTL_SECONDS`    | 天气结果内存缓存 TTL，默认 `900`（15 分钟，免费版有日配额） |
| `MAX_UPLOAD_SIZE_MB`           | 单文件上传大小上限                  |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | 切分参数，默认 `800` / `100`；**重叠必须小于块大小**，否则启动即报错 |
| `RETRIEVE_TOP_K`               | 检索返回条数                     |

**M3 新增配置项（2026-10-12）**

| 变量                          | 说明                                                                                        |
| --------------------------- | ----------------------------------------------------------------------------------------- |
| `UPLOAD_DIR`                | 上传文件落盘根目录，默认 `./data/uploads`；实际路径为 `{UPLOAD_DIR}/{user_id}/{hash前16}_{安全名}` |
| `CHROMA_COLLECTION`         | Chroma 集合名，默认 `knowledge`。测试与冒烟会换成独立名字，避免相互污染                                               |
| `DOCUMENT_PREVIEW_CHUNKS`   | 文档详情接口回传几个切分片段供预览，默认 `5`                                                                  |

**向量化配置项（M4 前置改造，2026-10-12）**

| 变量 | 说明 |
| --- | --- |
| `EMBEDDING_PROVIDER` | `fastembed`（默认，本地 ONNX 真实模型）或 `placeholder`（开发替身）。**值不在白名单内会启动失败；非 dev 环境用 `placeholder` 也会启动失败** |
| `EMBEDDING_MODEL` | 模型名，默认 `BAAI/bge-small-zh-v1.5`。**改它必须同时改 `EMBEDDING_DIM` 并重建索引** |
| `EMBEDDING_DIM` | 模型输出维度，默认 `512`。与模型真实维度不一致时构造向量化器会**直接报错**（刻意如此，避免「配置写 256 实际存 512」） |
| `EMBEDDING_BATCH_SIZE` | 批量向量化的批大小，默认 `32`（最小 1，否则启动报错） |
| `EMBEDDING_QUERY_INSTRUCTION` | 检索查询的指令前缀。**不设 = 按模型名自动判断**（bge-zh 系列会加）；**显式设为空字符串 = 强制不加**，用于在自己的真实语料上 A/B。详见 §8.1 的实测记录 |
| `HF_ENDPOINT` | 模型下载源，默认 `https://hf-mirror.com`。**因为 `huggingface.co` 在本机实测不可达**；能直连的环境可改成 `https://huggingface.co` |
| `FASTEMBED_CACHE_PATH` | 模型缓存目录，默认 `./data/models/fastembed`（约 90MB）。放在项目内是为了「私有化离线部署时整个目录拷贝过去」 |

**M4 新增配置项（2026-10-12）**

| 变量 | 说明 |
| --- | --- |
| `LLM_MODEL` | ⚠️ **模型名已变更**：由 `deepseek-chat`（**已被上游下线**）改为 `deepseek-flash`（= DeepSeek-V4.1-Flash，1M 上下文）。当前可用的两个名字是 `deepseek-flash` 与 `deepseek-v4-pro` |
| `LLM_BASE_URL` | ⚠️ **已对齐官方文档**：由 `https://api.deepseek.com/v1` 改为 `https://api.deepseek.com`（**不带 `/v1`**） |
| `LLM_TEMPERATURE` | 生成温度，默认 `0.3`。刻意偏低：天气播报与知识库问答都应「贴着材料说」，温度越高越容易脱离检索片段编内容。合法范围 `[0, 2]`，超出启动报错 |
| `LLM_TIMEOUT_SECONDS` | 单次 LLM 调用超时，默认 `60.0`。⚠️ 流式响应下这是**整体上限**，不是首个 token 的等待上限 |
| `RETRIEVE_SCORE_THRESHOLD` | 检索的**余弦相似度**阈值，默认 `0.45`。⚠️ 比较的是 `score = 1 − 距离`（越大越相关），**不是距离**——方向弄反会把「宁可少答」变成「什么都召回」。合法范围 `(0, 2]`，超出启动报错 |
| `CHAT_HISTORY_MAX_MESSAGES` | 组装 prompt 时最多带回几条历史消息（用户与助手各算一条），默认 `20`。至少为 `1`，否则多轮对话会**静默退化成单轮** |
| `CHAT_MAX_QUESTION_CHARS` | 单次提问的最大字符数，默认 `2000`，超出直接 400（防超长输入烧 token） |
| `CHAT_MAX_RETRIEVED_CHARS` | 检索片段拼进 prompt 的总字符上限，默认 `4000`。超出时按**相关度从低到高逐个整段丢弃尾部**，不截断单段——半截的句子比没有更糟 |

> **`.env` 里两组凭据分属两家不同服务商**，最易混淆：`WEATHER_API_HOST` / `WEATHER_API_KEY` 是**和风天气**的；`LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` 是**大模型**的。`.env` 与 `.env.example` 里都已用注释写明这一点，LLM 未配置时的报错文案里也写了——因为「换了 A 家的密钥去调 B 家」是必然会发生的事。
>
> **其它 OpenAI 兼容网关同样只需改这三个值**（通义 / 智谱 / 本地 vLLM / Ollama）：`LLM_BASE_URL` 填到它的兼容端点，`LLM_MODEL` 填它提供的模型名，`LLM_API_KEY` 填它的密钥即可，代码一行不动。

**启动期校验（M3 起，M4 前置改造与 M4 扩充）**：`app/core/config.py` 的三个 `model_validator` 会在启动时检查上述参数，把「上线才发现」的问题提前到启动：

- `EMBEDDING_PROVIDER` 不在白名单 → 拒绝启动；
- 非 dev 环境 + `EMBEDDING_PROVIDER=placeholder` → 拒绝启动；
- `CHUNK_OVERLAP >= CHUNK_SIZE` → 拒绝启动（否则切分窗口无法向前推进）；
- `EMBEDDING_BATCH_SIZE < 1` → 拒绝启动。

**M4 新增校验（`_validate_chat`）** —— 这几项一旦配错，表现都是「结果不对但不报错」：

- `RETRIEVE_SCORE_THRESHOLD` 不在 `(0, 2]` → 拒绝启动。配成负数会把所有片段都过滤掉（永远答「知识库中未找到」）；
- `LLM_TEMPERATURE` 不在 `[0, 2]` → 拒绝启动（OpenAI 兼容接口的通用范围，超出会被上游直接拒）；
- `LLM_TIMEOUT_SECONDS <= 0` → 拒绝启动；
- `RETRIEVE_TOP_K < 1` → 拒绝启动；
- `CHAT_HISTORY_MAX_MESSAGES < 1` → 拒绝启动（配成 0 会让多轮对话退化成单轮，且不报错）；
- `CHAT_MAX_QUESTION_CHARS < 1` / `CHAT_MAX_RETRIEVED_CHARS < 1` → 拒绝启动。

> `EMBEDDING_DIM` 与模型真实维度的校验**不在** config 里，而在 `rag/embedder.py` 构造向量化器时做——因为读出真实维度需要 import fastembed，放在 config 会让配置层依赖一个重库。

**M6 新增配置项（2026-10-12）**

| 变量 | 说明 |
| --- | --- |
| `RATE_LIMIT_ENABLED` | 是否开启进程内限流，默认 `true`。测试与本地排查可关（`tests/conftest.py` 的 autouse fixture 默认关） |
| `RATE_LIMIT_AUTH_LIMIT` / `RATE_LIMIT_AUTH_WINDOW_SECONDS` | 鉴权接口（注册/登录）的限流，默认 `10` 次 / `60` 秒。防止撞库与批量注册 |
| `RATE_LIMIT_CHAT_LIMIT` / `RATE_LIMIT_CHAT_WINDOW_SECONDS` | 问答接口的限流，默认 `30` 次 / `60` 秒。防止单用户打爆 LLM 配额 |
| `RATE_LIMIT_DEFAULT_LIMIT` / `RATE_LIMIT_DEFAULT_WINDOW_SECONDS` | 其余接口的兜底限流，默认 `120` 次 / `60` 秒。`health` / `metrics` 豁免 |
| `METRICS_ENABLED` | 是否暴露 `GET /metrics`，默认 `true`。关闭时该端点返回 404 |
| `LLM_MAX_RETRIES` | LLM 调用的有限重试次数，默认 `2`（只对网络异常/5xx/429 重试，4xx 不重试）。⚠️ 这里删除了早期草案里的 `LLM_RETRY_BACKOFF_SECONDS`——退避由 SDK 内部指数退避负责，自配反而与 SDK 打架 |

**M6 新增校验（`_validate_m6`）**：三个限流档的 `*_limit` 必须 ≥ 1（配成 0 会让对应接口被永久拒绝）、`*_window_seconds` 必须 > 0（配成 0 会除零或永不滑动）、`LLM_MAX_RETRIES` 必须 ≥ 0。

---

## 13. 决策日志（每确认一项，在此追加一行）

| 日期         | 决策点         | 选定方案                       | 决策人   | 理由 / 备注                      |
| ---------- | ----------- | -------------------------- | ----- | ---------------------------- |
| 2026-10-11 | 项目定位与架构分层   | 六层架构（见 §4）                 | 用户    | 初始框架                         |
| 2026-10-11 | 协作铁律        | 见 §2                       | 用户    | 用户明确要求：所有技术决策必须列方案由用户决定      |
| 2026-10-11 | D1 后端框架     | FastAPI                    | 用户    | 异步原生，与 LLM 流式场景最匹配，自动 API 文档 |
| 2026-10-11 | D2 前端 / 交互层 | Streamlit                  | 用户    | 纯 Python，零前端基础即可用，开发最快       |
| 2026-10-11 | D3 Agent 编排 | LangGraph                  | 用户    | 流程显式可见，天然支持多轮与条件分支           |
| 2026-10-11 | D4 向量库      | Chroma                     | 用户    | 零运维，与 LangChain 集成最顺滑        |
| 2026-10-11 | D5 关系型数据库   | SQLite                     | 用户    | 零配置，适合开发与单机部署                |
| 2026-10-11 | D6 LLM 接入   | OpenAI 兼容统一网关              | AI 暂定 | 换供应商只改 2 个环境变量，避免被厂商锁定       |
| 2026-10-11 | D7 文档解析     | LangChain Document Loaders | AI 暂定 | 与已选 LangChain 生态衔接，格式覆盖广     |
| 2026-10-11 | D8 部署方式     | 本地直跑（venv + uvicorn）       | AI 暂定 | 开发期最快，部署方式留到 M7 再定           |
| 2026-10-11 | 天气数据源       | 和风天气 QWeather              | AI 暂定 | 国内城市数据准确，有中文文档与免费额度          |
| 2026-10-12 | D6–D8 与天气源复核 | 全部沿用 AI 暂定基线                | 用户    | 四项推翻成本均低：D6 只改 2 个环境变量，天气源封装在单文件内，D8 到 M7 才生效 |
| 2026-10-12 | M0 骨架填充度    | 渐进式骨架（12 个核心文件 + 目录占位）      | 用户    | 只装当前阶段依赖，符合 §2.3；不做无实现的全量空壳               |
| 2026-10-12 | 依赖声明方式      | requirements.txt + venv + pip | 用户    | 零额外安装、报错可搜性最高；ruff 延后至 M6/M7               |
| 2026-10-12 | 项目落盘位置      | 本地项目根目录（路径不入库） | 用户 | 与 §5 目录树一致，CLAUDE.md 随代码入库        |
| 2026-10-12 | health/ready 语义 | 未接入组件返回 HTTP 200 + ready=false | AI 实施 | M0 无可连组件，返回 503 会让「启动成功」与「依赖缺失」混淆；M1 接入 DB 后改为缺失即 503 |
| 2026-10-12 | Python 运行时    | 3.13，venv 位于项目内 .venv/        | AI 实施 | 满足 §11 的 3.11+ 要求；项目内 venv 保证环境隔离      |

| 2026-10-12 | M1-D1 数据库访问方式 | SQLAlchemy 2.0 异步 + aiosqlite | 用户 | 与 M4 流式路由天然一致，无需线程池包装；代价是学习成本更高，SQLite 下性能收益近 0 |
| 2026-10-12 | M1-D2 迁移机制 | Alembic（async 模板） | 用户 | 表结构可回滚；因 D1 选了异步，env.py 必须改用 async 模板，已按此实施 |
| 2026-10-12 | M1-D3 鉴权实现 | PyJWT + bcrypt（不用 passlib） | 用户 | 依赖最少、无 passlib/bcrypt 版本告警；M1 只签 access token（12h），refresh token 留到 M6 |
| 2026-10-12 | M1-D4 §6 定稿范围 | users + conversations + messages | 用户 | 三张表字段由需求决定、不依赖 Agent/RAG 实现；documents 系列留到 M3 |
| 2026-10-12 | §7 API 契约补充约定 | 分页 page/page_size、chat 用 stream 参数、新增 conversations.py | AI 实施 | 明细见 §7 补充约定；`api/v1/conversations.py` 属对 §5 清单的补充，M4 落地 |
| 2026-10-12 | 新增 crud/health.py | 健康探测单独成文件 | AI 实施 | `SELECT 1` 属基础设施职责，但又必须满足 §11「访问一律经 crud」，故单独建文件而不塞进 user.py |
| 2026-10-12 | SECRET_KEY 长度校验 | 短于 32 字节直接启动失败 | AI 实施 | PyJWT 对短密钥只发 InsecureKeyLengthWarning，易被忽略后一路带上线，改为硬拦截 |
| 2026-10-12 | health/ready 语义升级 | 必需组件全就绪 200，否则 503 | AI 实施 | M1 必需组件仅 database；其余标 required=false，避免恒返回 503 失去信号意义 |
| 2026-10-12 | M1 交付 | 57 个文件（36 个 .py）；12 项 pytest + 11 项冒烟检查全通过 | AI 实施 | 验证明细见工作区记忆 2026-10-12.md |

| 2026-10-12 | M2-D1 无 key 时如何开工 | **直写真实 API，不写 mock 适配器** | 用户 | ⚠️ **AI 明确标「不推荐」而用户仍选择**。连带缺口：M2 全链路在无真实 key 时无法验证，字段名/路径正确性只能等校准（见 §9.3）。若日后要补 mock，须按 §2.4 重新走变更流程 |
| 2026-10-12 | M2-D2 异步 HTTP 客户端 | httpx（`AsyncClient`） | 用户 | 与 M1 已确定的纯异步栈一致；测试可挂 `httpx.MockTransport`，不必起真服务 |
| 2026-10-12 | M2-D3 功能范围 | 最小集（城市搜索 + 实时天气 + 每日预报）+ 生活指数 | 用户 | 最小集已足够支撑需求 C1「明天上海要带伞吗」（降水概率在每日预报里）；生活指数为附加项，失败降级为空 |
| 2026-10-12 | M2-D4 缓存与重试 | 内存 TTL 缓存 + 有限次重试（指数退避） | 用户 | 和风免费版有日配额，缓存直接省调用；重试只覆盖网络异常与 5xx，4xx 不重试 |
| 2026-10-12 | 天气答案的「待校准适配层」 | 把无法离线核实的路径/字段集中隔离并显式标注 | AI 实施 | §2.5 与「不得假装已验证」的要求：`GEO`/`INDICES` 路径与响应字段名无法在无 key 下确认，故用 `_pick` 兼容多候选名 + 提供 `scripts/check_weather_api.py` |
| 2026-10-12 | 缓存对象隔离 | 存副本 + 返副本（`model_copy(deep=True)`） | AI 实施 | 首版只对缓存命中分支做了拷贝，未命中分支直接返回缓存持有的同一对象，调用方就地改字段会污染缓存（已被测试捕获并修复） |
| 2026-10-12 | 天气能力暂不暴露 HTTP 端点 | M2 只交付进程内工具，不加 `/api/v1/weather` | AI 实施 | M2 开工时向用户列的 9 项交付物不含路由；M4 的 Agent 在进程内直接调工具函数即可，无需绕 HTTP。若需手工演示端点，属新增接口，须按 §2.4 另行决策 |
| 2026-10-12 | M2 交付 | 新增/改动 8 个文件；pytest 34 项全通过 | AI 实施 | 天气模块 21 项 + 既有鉴权 12 项 + 其他 1 项；明细见工作区记忆 2026-10-12.md |

| 2026-10-12 | 用假 key 跑真实 API 验证链路 | 按用户指示「直接按真实 API 写，用假 key 跑」，对真实服务器完成实测 | 用户指示 | 已验证：DNS/TLS/请求头/错误解析/4xx 不重试/降级/失败不入缓存。**未验证：成功响应体字段名**（假 key 过不了认证） |
| 2026-10-12 | 上游错误格式已改为 RFC 7807 | 解析层改为「先看 error 体」，并禁止「HTTP 200 即成功」 | AI 实施 | 实测错误体为 `{"error":{status,type,title,detail}}`（`application/problem+json`），不再是旧 `{"code":"401"}`。旧写法会把「200 + error 体」判成成功，属于"假装成功"；同时把 401/403 从 502（请稍后重试）改为 503 + 明确指向 `.env` 两个变量 |
| 2026-10-12 | 新增 3 道「反假成功」防线 | `_reject_all_none` / `fetch_daily` 键缺失检查 / 错误体优先解析 | AI 实施 | 字段名猜错时必须报错并带出顶层键名，而不是返回全 None 的对象让上层以为"天气就是这样" |
| 2026-10-12 | `INDICES_PATH` 由 `/indices/v1/3d` 改为 `/v7/indices/3d` | 原 AI 猜测被实测推翻 | AI 实施 | 实测 `/indices/v1/3d` 返回 **404（路由不存在）**，而 `/v7/indices/3d` 返回 **403（路由存在）**。同时确认 `/weather/v1/current`、`/weather/v1/daily`、`/geo/v2/city/lookup` 路由均真实存在 |
| 2026-10-12 | 新增「路由存在性判据」 | 401/403 = 路由存在；404 = 路由未注册 | AI 实施 | 这条判据让「路径对不对」在无真实 key 时也能判定，是本次能定位到 `INDICES_PATH` 写错的关键。已写入 §11 作为硬约定 |
| 2026-10-12 | 两级 Host 策略 | 记录为事实，不改代码 | AI 实施 | 同一共享 Host 下：`/weather/v1/*` → 401（可路由，key 无效），`/geo/v2/*` 与 `/v7/*` → 403 Invalid Host（必须项目专属 Host） |
| 2026-10-12 | 校准脚本不再「第一步失败就退出」 | 改为 4 步全跑 + 原始 HTTP 探测 + 汇总判读 | AI 实施 | 原脚本在城市搜索失败时 `return 1`，导致假 key 场景下看不到后 3 条路径的真实状态；校准最需要的恰恰是未翻译的原始状态码 |
| 2026-10-12 | 测试补 9 个用例（共 44 项） | 覆盖 RFC 7807 / 反假成功 / 路径护栏 / 业务码属性 | AI 实施 | 其中 `PROBLEM_401` / `PROBLEM_403` 样本是从真实服务器抓回的**原样**响应，不是构造的 |

| 2026-10-12 | M3-Q1 向量化实现 | **占位向量 PlaceholderEmbedder** | 用户 | ⚠️ **AI 已说明它「结构真、语义假」，检索结果无意义，用户仍选择**。连带缺口：M4 的检索问答在换真实向量模型前没有实际价值。防线已加：非 dev 环境拒绝启动 + `/health/ready` 告警标记（见 §8.1） |
| 2026-10-12 | M3-Q2 切分策略 | 递归字符切分 800 / 100 | 用户 | 通用、不依赖文档结构；代价是失去标题层级语义（不做按标题切分）。分隔符必须换中文优先，否则中文会被硬切（见 §8.1） |
| 2026-10-12 | M3-Q3 后台任务机制 | 进程内 BackgroundTasks + `asyncio.to_thread` | 用户 | 零额外依赖、起步最快；代价是**进程重启任务即丢失**，必须配启动补偿 `recover_stale_documents()`，且无法在多实例间分配任务 |
| 2026-10-12 | M3-Q4 知识库可见范围 | 每用户私有 | 用户 | 越权防线做在 `crud/` 层（所有查询强制带 `user_id`），并把「不存在」与「属于别人」统一返回 404 以防探测 |
| 2026-10-12 | §6 数据模型定稿 | documents + document_chunks（含 9 项相对草稿的调整） | AI 实施 | 逐项明细见 §6「M3 定稿补充」表；核心是 `(user_id, file_hash)` 联合去重与确定性 `vector_id` |
| 2026-10-12 | 上传接口返回 202 而非 201 | `/documents` 与 `/documents/{id}` 的 PUT 均返回 202 | AI 实施 | 文档此刻还在 pending，用 201 会暗示「已创建完成」，前端可能立刻读 `chunk_count` 拿到 0（理由见 §7） |
| 2026-10-12 | 删除顺序固定 | 先删向量 → 再删记录 → 最后删文件 | AI 实施 | 反过来会留下「记录没了但向量还在」的孤儿向量，它们仍会被检索命中，用户会看到「来源是已删除的文档」 |
| 2026-10-12 | `crud/document.py` 的 `delete` 名称冲突 | SQLAlchemy 的 `delete` 改别名 `sa_delete` | AI 实施 | 同文件内自定义了 `async def delete(db, document)`，模块级名字被覆盖，导致 `delete_chunks` 里实际调用的是删整篇文档的协程 → `TypeError`。已写入 §11 硬约定第 13 条 |
| 2026-10-12 | `tests/test_knowledge.py` 移除 `pytestmark` | 删除模块级 `pytestmark = pytest.mark.asyncio` | AI 实施 | `pytest.ini` 已设 `asyncio_mode = auto`，再手工打标记会让同文件的**同步**用例也被当成协程，产生 9 条 PytestWarning |
| 2026-10-12 | 冒烟脚本的服务进程输出方式 | 重定向到日志文件，不用 `subprocess.PIPE` | AI 实施 | 用 PIPE 且不及时读，缓冲区一满子进程就阻塞在启动阶段——表现为「端口已监听、请求一直无响应」的超时，极难排查 |
| 2026-10-12 | M3 交付 | 新增/改动 20 个文件；pytest 66 项 + 冒烟 27 项全通过 | AI 实施 | M3 新增 22 项用例，M1/M2 的 44 项无回归；明细见工作区记忆 2026-10-12.md |
| 2026-10-12 | ⚠️ 依赖告警：`langchain-community` 已 sunset | **暂不处置，上报用户待决策** | AI 上报 | 官方原文：`langchain-community is being sunset and is no longer actively maintained`（见 langchain-ai/langchain-community#674）。D7 选定的「LangChain Document Loaders」当前由该包承载，属已确认选型的可维护性风险。**按 §2.4，需列方案让用户决定是否迁移到独立集成包（`langchain-pypdf` 等）或改回自写解析**——本行仅作记录，不代表已决策。**⚠️ 本行随后被 2026-10-12 的 D-M4-2 决策取代，且其中的「迁到 `langchain-pypdf`」经核实并不存在该包** |

| 2026-10-12 | **D-M4-1 向量化实现** | **本地 ONNX：`fastembed` + `BAAI/bge-small-zh-v1.5`（512 维）** | 用户 | 四个候选（本地 ONNX 轻量 / 本地 ONNX 全量 / 本地 PyTorch / 第三方 API）中选 A。**关键依据：`onnxruntime` 本来就随 chromadb 装进来了**，所以只需多一个包 + 90MB 模型，不必引入 torch 那 2.5GB。用户配置的 DeepSeek 网关**不提供 embedding 接口**，故 D（第三方 API）本就不通顺 |
| 2026-10-12 | **D-M4-2 `langchain-community` sunset 处置** | **自写解析，移除该依赖** | 用户 | 四候选（自写解析 / pin 住不动 / 迁 unstructured / 走 MCP）中选 A。**核实事实**：官方 sunset 公告日 2026-05-22、仓库 2026-06-19 归档只读；官方三条替代路径中适用的是「直接在应用代码里实现简单工具」；**并不存在 `langchain-pypdf` 这类替代包**（拆成独立包的是 chat model / vector store 类 provider 集成）。本项目实际暴露面只有 `rag/loader.py` 的 2 个类，底层库本来就装着 |
| 2026-10-12 | 依赖变化实测 | 移除 `langchain-community` + `langchain-classic`，加入 `fastembed` | AI 实施 | `pip check` 通过。venv 从 494MB → 479MB（**净减少**，因为 fastembed 的依赖大多已存在）。另注：装 fastembed 时 `huggingface-hub` 被从 2.2.0 降到 1.33.0，`pip check` 无冲突 |
| 2026-10-12 | ⚠️ 环境事实：`huggingface.co` 不可达 | 模型下载默认走 `HF_ENDPOINT=https://hf-mirror.com` | AI 实施（实测发现） | 实测 `huggingface.co` 与 `cdn-lfs.huggingface.co` 均 ReadTimeout，而 `hf-mirror.com` 可达并能正常提供 `Qdrant/bge-small-zh-v1.5` 仓库文件。这是**环境事实不是选型偏好**，已在 `.env.example` 注释说明，能直连的环境可改回官方源 |
| 2026-10-12 | 查询指令前缀的实测结论 | **默认启用（bge-zh），可配置关闭** | AI 实测后决定 | 3 组样本、17 条中文查询实测：**加与不加都没有改变排序结果**；在贴近真实的「209 字长 chunk vs 9 字短查询」场景下，加前缀让真答案与最强干扰的平均分差从 +0.0796 扩大到 +0.0827（约 +4%）；短文档场景下只是让绝对分数整体下降。**样本量小、统计效力不足，既不能证明收益也不能证明有害** → 跟随模型作者推荐用法默认启用，同时做成 `EMBEDDING_QUERY_INSTRUCTION` 可配置，供用户在自己语料上 A/B。这段实测如实记在 `rag/embedder.py` 顶部，不是复述官方说法 |
| 2026-10-12 | 新增测试抓到一处实现缺陷 | `FastembedEmbedder` 传了 `dimension` 就跳过模型名校验 | AI 修复 | `test_unknown_model_error_lists_chinese_candidates` 失败暴露出：模型名拼错时要等到第一次真正向量化才报错，且报错来自 fastembed 内部、没有候选清单。改为**始终解析模型名**。已写入 §11 第 15 条 |
| 2026-10-12 | 端到端验证补强 | `smoke_m3.py` 新增「停服后核对向量库」与 `--real-embedding` 开关 | AI 实施 | 这是项目里**第一条端到端验证「检索真的有用」的检查**：停服后（Chroma 单进程约束）打开临时向量库，确认落库 512 维、元数据带 `user_id`、用查询向量检索能命中相关片段（余弦距离 0.2712）。占位模式 30 项通过、真实模式 33 项通过 |
| 2026-10-12 | M4 前置改造交付 | 新增/改动 8 个文件；pytest 88 项全通过 | AI 实施 | 新增 `tests/test_rag.py`（22 用例，全部离线）、`scripts/check_embedding.py`（向量化自检 8 项）；改动 `rag/embedder.py`、`rag/loader.py`、`core/config.py`、`requirements.txt`、`.env`、`.env.example` |
| 2026-10-12 | ⚠️ **天气字段名校准（M2 遗留项清零）** | **用真实凭据完成 v1 响应体逐字段校准，并重写解析层** | 用户提供凭据 | 拿到 current / daily / indices 三个接口的 HTTP 200 真实响应。**关键发现：v1 与 v7 是两套完全不同的数据形状**，不是简单改字段名（对照表见 §9.3.1）。修掉三处**静默出错**点：湿度/降水概率是 0–1 比例却当百分数用（会显示「湿度 1%」）、风速 m/s 未换 km/h（差 3.6 倍）、预报日期是 UTC 未转东八区（整条预报差一天）。换算集中到 4 个助手，各配单测 |
| 2026-10-12 | 生活指数参数修正 | `fetch_indices` 补上必填的 `type=0` | AI 实施 | 实测缺 `type` 返回 `400 Missing Parameter`（`invalidParams: ["location","type"]`），原代码只传了 location——属于「凭印象写下的参数」。同时确认 `location` 用 `"经度,纬度"` 有效，不必先拿 LocationID |
| 2026-10-12 | ⚠️ **城市搜索 403 Security Restriction** | **代码不改，上报用户到控制台处置** | AI 上报（需用户操作） | 同一 Host / KEY / 源 IP 下，另三个接口全 200，仅 `/geo/v2/city/lookup` 403。**关键证据：该路径不带参数时返回 400** —— 说明路径与鉴权都已通过，是参数校验之后的安全/权限关卡拦下的。按官方错误码定义与同类案例，属凭据的 API 限制/应用限制、或 GeoAPI 权限未生效。处置步骤见 §9.3.3。**影响面：暂时不能用城市名提问** |
| 2026-10-12 | ⚠️ 发现并修复「假成功」防线漏洞 | `_to_str` 拒绝容器类型 + `fetch_daily` 新增全天日期失败拦截 | AI 实施（新增测试暴露） | 校准前 `condition` 命中的是 `{"text": "阴"}`，`str(dict)` 产生**非空**字符串让 `_reject_all_none` 的 `all()` 判假 → 防线被绕过，会返回「气温 None 度 + 天气条件是一坨 dict 字符串」的假天气。防线由 3 道增至 5 道（见 §9.3.4） |
| 2026-10-12 | `CurrentWeather.observed_at` 实测无来源 | 保持 `None`，不编造 | AI 实施 | v1 的 current 响应**没有观测时间字段**（`metadata.tag` 是数据版本标记，不是时刻）。与其编一个"看起来对"的时间，不如诚实留空；该字段本就是可选 |
| 2026-10-12 | 新增 `CurrentWeather.uv_index` | 上游 `uvIndex` 此前被丢弃，现纳入契约 | AI 实施 | 上游 current 一直返回 `uvIndex`（实测 4），daily 也早有 `uv_index`，current 缺它属设计遗漏。加的是**可选字段**，不破坏既有契约。**这是本轮唯一的契约变更** |
| 2026-10-12 | 天气测试样本回填为真实响应 | `tests/test_weather.py` 32 → 40 用例；全量 pytest 88 → 96 项通过 | AI 实施 | current / daily / indices 三个样本改为**逐字节抓取**的真实响应；新增昆明雨天样本（上海那两天恰逢无雨，全 0 的样本验证不了比例换算）；新增单位换算 / 风向换算 / 防线加固 / 真实 403 等 8 个用例 |

| 2026-10-12 | **M4-Q1 编排方式** | **LangGraph 意图路由 + 分支节点（B）** | 用户 | 三候选（Q1A 单链顺序 / Q1B 意图路由分支 / Q1C 单 Agent + 工具调用）中选 B。天气与知识库是两条能力完全不同的路径，分支能把「这轮到底查了什么」画在图上，排查时一眼可见；代价是节点间要传一份显式 state，多写一个 `state.py` |
| 2026-10-12 | **M4-Q2 LLM 接入实现** | **`langchain-openai`（A）** | 用户 | 三候选（A `langchain-openai` / B 直接 `openai` SDK / C 自写 httpx 客户端）中选 A。差别在于拿到的是 LangChain 的 `BaseChatModel` 接口：消息对象、流式增量、`usage_metadata` 都已标准化，**且 LangGraph 能直接读它们的回调流来逐 token 推送**——这是 SSE 能工作的前提。代价是多一层依赖。也已确认用户配置的 DeepSeek 网关**不提供 embedding 接口**，故检索侧只能走本地向量模型 |
| 2026-10-12 | **M4-Q3 检索策略** | **纯向量 top_k + 距离阈值（A）** | 用户 | 三候选（A 纯向量 + 阈值 / B 向量 + 重排 rerank / C 混合检索 BM25+向量）中选 A。理由：B 需要额外一个模型或一次 LLM 调用，收益要靠评测集才能确认——**没有评测集的情况下属于「加了但不知道有没有用」**；C 需要维护两套索引。A 的代价是阈值的绝对数值依赖具体向量模型，换模型要重新标定 |
| 2026-10-12 | **M4-Q4 意图识别方式** | **规则优先 + LLM 兜底（C）** | 用户 | 三候选（A 关键词规则 / B 全量 LLM 分类 / C 规则优先 + LLM 兜底）中选 C。收益是具体的不是修辞：绝大多数问题（「明天上海要带伞吗」）零延迟零成本，同一句话永远得到同一个分类（排查「为什么这次没检索」不会出现不可复现），且 LLM 不可用时规则路径仍覆盖高频问法、功能不会整体瘫掉——只有兜底那一档退化成 chat。代价是词表需要随真实提问持续补充，**这份词表本身要当配置看待** |
| 2026-10-12 | ⚠️ **`LLM_MODEL=deepseek-chat` 已被上游下线** | 改为 **`deepseek-flash`**（= DeepSeek-V4.1-Flash） | 用户拍板（AI 发现并上报） | 查证官方 API 文档与定价页（2026-10-12）：DeepSeek 当前只提供 `deepseek-flash` 与 `deepseek-v4-pro` 两个模型名，**旧的 `deepseek-chat` / `deepseek-reasoner` 已下线**。这是 M4 开工前发现的**阻断性变化**——沿用旧名会让每一次问答都失败。同时在网关层把 404 的错误信息改成「带出候选模型名」，让下次遇到同类问题时不必再查文档 |
| 2026-10-12 | `LLM_BASE_URL` 对齐官方文档 | 由 `https://api.deepseek.com/v1` 改为 `https://api.deepseek.com`（不带 `/v1`） | AI 实施 | 原值是本项目早期按「OpenAI 兼容 = 要带 /v1」的通用印象填的，与 DeepSeek 官方文档的写法不一致。虽然两种写法在该服务上都可用，但**与官方文档不一致的配置会在换网关时成为最难排查的那类问题** |
| 2026-10-12 | 流式路径必须自建数据库会话 | `stream_events` 内 `async with AsyncSessionLocal()` | AI 实施 | SSE 的响应体是一个「边算边发」的生成器，它在**路由函数返回之后**才开始被消费，那时请求级的 `get_db` 会话是否还开着取决于框架版本与响应类型——**这是一个不该赌的细节**。改为：请求内能做完的事全部在 `prepare()` 里做完，生成器内部另开会话。这与 `process_document` 的做法一致（§11 第 7 条） |
| 2026-10-12 | 用户消息先落库、助手消息成功后再落库 | 两道写入分开 | AI 实施 | 反过来的话，答到一半连接断了，用户那句话就凭空消失，他再问一次会以为是自己的问题。而现在最坏只留下一条「有问无答」的记录——用户能直观看到上一条没答上，重问即可。**宁可留中间态，也不要丢用户输入** |
| 2026-10-12 | ⚠️ SSE 短路路径必须补发一个 `delta` | `stream_events` 加 `delta_sent` 标志 | AI 实施（自检发现） | 短路时 `generate` 根本没执行，一个 token 事件都不会产生。若只发 `meta` + `done`，一个「只认 delta 渲染」的前端会显示**一片空白**，而 `done.answer` 里明明带着完整答案。这是契约层面的漏洞，靠**跑一遍真实流式**才发现，单元测试默认不会去数「一个事件都没有」这件事 |
| 2026-10-12 | `messages.tool_calls` 存运行痕迹 | `{intent, intent_source, short_circuit, notes, sources}` | AI 实施 | M5 的前端要能显示「这条回答依据了哪几个片段」「这轮是不是短路的」，而这些信息在图跑完就没了。用 JSON 列存，存什么结构都不用改表 |
| 2026-10-12 | ⚠️ 会话删除的 ORM 级联陷阱 | 改为显式批量 DELETE + `db.expunge()` | AI 实施（测试暴露） | ORM 级联只作用于「已加载进会话的子对象」，而 `add_message(conversation_id=...)` 写入的消息**不在** `conversation.messages` 集合里。测试断言「删会话后消息数为 0」失败，实际是 1——留下了孤儿消息。改用批量 DELETE 后连 `SAWarning: DELETE ... expected to delete 2 row(s); 0 were matched` 也一并消失 |
| 2026-10-12 | ⚠️ `add_message` 必须显式 bump 父会话 `updated_at` | 在写消息时顺手 UPDATE 一次会话行 | AI 实施 | `onupdate=func.now()` 只在会话这一行本身被 UPDATE 时触发；新增消息不碰会话行，于是「会话列表按最近活跃排序」**静默失效**（顺序永远停在创建时刻）。属「不报错、只是顺序不对」那类问题。同时记录了一条环境限制：**SQLite 的 `CURRENT_TIMESTAMP` 只精确到秒**，同一秒创建的两个会话时间戳相同、排序退化成按 id 兜底，写测试时要把参照对象的时间戳挪到不同日期 |
| 2026-10-12 | ⚠️ 测试里的假模型会污染调用计数 | 替身加「在流式内部」标志位；断言只断次数不断方法名 | AI 实施（测试暴露） | `GenericFakeChatModel._stream()` 内部会回调 `self._generate()`，纳秒级产生一条假的 `invoke` 记录；且 LangGraph 的回调会让图内的 `ainvoke` 也走流式（实测 `['stream','stream']`）。按方法名断言两次都是假失败，改为只断言调用次数。已写入 §11 第 40 条 |
| 2026-10-12 | ⚠️ 测试可能打真实天气 API | autouse fixture 里默认把 `get_weather` 打桩成「不联网」 | AI 实施（自检发现） | `.env` 里配着真实和风凭据，凡是命中天气意图的用例都会**真的发外部请求**。测试必须是离线且可重复的，这条不打桩就是一个会随网络与配额波动的定时炸弹 |
| 2026-10-12 | **M4 交付** | 新增/改动 **26 个文件**（新增 17：`app/llm/` 3 个、`app/agent/` 6 个、`crud/conversation.py`、`rag/retriever.py`、`schemas/chat.py`、`services/chat_service.py`、`api/v1/chat.py`、`api/v1/conversations.py`、`tests/test_chat.py`、`scripts/smoke_m4.py`；改动 9）；pytest **138 项** + 端到端冒烟 **61 项** 全通过 | AI 实施 | M1–M3 的 94 项无回归。真实 DeepSeek 凭据下的非流式与流式均已端到端跑通（含流式 token 用量），明细见工作区记忆 2026-10-12.md |

| 2026-10-12 | **M5-Q1 页面结构** | **Streamlit 多页应用（`st.navigation`）** | 用户 | 登录 / 对话 / 知识库三页。选它而不是单页+侧边栏：URL 清晰、职责分离、每页独立维护；代价是登录态要在页面间显式传递（用 `st.session_state`） |
| 2026-10-12 | **M5-Q2 流式渲染** | **delta 实时拼接 + done 到达后用完整 `answer` 覆盖一次** | 用户 | 选它而不是「只认 delta」：后端虽已保证 `meta → delta* → done` 且短路路径补发 delta，但前端**再兜底一层**能让丢字、短路空白自动恢复——两层防御比一层更稳，覆盖动作用户几乎无感 |
| 2026-10-12 | **M5-Q3 城市/日期输入** | **混合输入：文本为主 + 可折叠的显式控件** | 用户 | 文本框保留「一句话问天气」的自然感，可折叠控件走 §8.2 预留的「显式传 `city`/`date`」准确路径。选它而不是纯文本（启发式抽取会漏「要带伞吗」这类没城市的话）或纯表单（失去自然提问感） |
| 2026-10-12 | **M5-Q4 知识库页范围** | **完整集：上传 + 进度轮询 + 覆盖重传 + 删除 + 重建索引 + 失败原因展示** | 用户 | 后端入库是**后台异步**的（返回 202、状态要轮询），「完整集」把这个异步体验正确呈现出来；最小集会把「正在入库」伪装成「已入库」 |
| 2026-10-12 | 前端页面结构实现方式 | `st.navigation` + `st.Page(callable)`（页面是函数不是文件） | AI 实施 | M5-Q1 拍板「多页应用」，落地时用 Streamlit 1.60+ 的新多页 API 而非旧式 `pages/` 目录约定：页面拆成 `views/*.py` 的 `render()` 函数，`app.py` 包成 `st.Page`。好处是页面逻辑可单测、不受「文件名前缀定排序」约束 |
| 2026-10-12 | ⚠️ 前端注册逻辑修正 | `register` 改为「返回用户信息」，前端**先 register 再 login 拿 token** | AI 实施（核对后端契约发现） | 初版封装假设后端 `/auth/register` 返回 `access_token`，但实际返回 `Envelope[UserPublic]`（只有用户信息）。若把 register 返回值直接当 token，登录态会拿到一个 dict 而失效。已改为两个动作分开 |
| 2026-10-12 | **M5 交付** | 新增/改动 **7 个文件**（新增 `frontend/` 6 个：`app.py` / `api_client.py` / `views/__init__.py` / `views/login.py` / `views/chat.py` / `views/knowledge.py`、`tests/test_frontend.py`；改动 `requirements.txt`）；pytest **154 项** + 真实后端/Streamlit 端到端确认全通过 | AI 实施 | M1–M4 的 138 项无回归，M5 新增 16 项前端单测。后端 M5 无 API 改动，`main.py` 版本与 `health.py` 里程碑字段维持不变（见 §5 M5 差异）。明细见工作区记忆 2026-10-12.md |

| 2026-10-12 | **M6-Q1 限流方式** | **进程内滑动窗口（`SlidingWindowLimiter`）** | 用户 | 三候选（进程内滑动窗口 / Redis 令牌桶 / 反向代理层限流）中选 A。理由：零外部依赖、起步最快；按 IP 分三档（auth 10/60s、chat 30/60s、default 120/60s），health/metrics 豁免。代价是**单进程有效、多实例部署时各限各的**，且可被伪造 `X-Forwarded-For` 绕过——这两点只能靠网关/WAF 或共享存储补，属 M7 部署时再评估 |
| 2026-10-12 | **M6-Q2 审计日志** | **落库 `audit_logs` 表** | 用户 | 三候选（落库 / 结构化日志文件 / 第三方 APM）中选 A。理由：与既有 SQLite 栈一致、可按用户/动作检索；`user_id` 可空 + 无外键（用户删除后审计仍保留），`detail` JSON 列。代价是多一张表与一次迁移 |
| 2026-10-12 | **M6-Q3 指标** | **自写计数器 + summary** | 用户 | 三候选（自写 / prometheus-client / 第三方 APM）中选 A。理由：单机部署用不上官方客户端的直方图分位、进程内注册表、多进程聚合，而「多一个依赖」按 §2.3 需要理由。只做 counter / summary 两类，覆盖请求量、延迟、LLM 调用/token、检索耗时、天气调用 |
| 2026-10-12 | **M6-Q4 LLM 弹性** | **有限重试（`max_retries`）+ 友好兜底** | 用户 | 三候选（不做任何处理 / 有限重试 + 兜底 / 完整熔断降级）中选 B。理由：SDK 的 `max_retries` 已只对网络异常/5xx/429 重试（4xx 不重试，与 §11 第 2 条一致），最终失败返回友好文案 `LLM_UNAVAILABLE_ANSWER`。**不做熔断器**：熔断器要在多实例间共享失败计数才准，单机实现价值有限，留到 M7 部署形态定了再评估 |
| 2026-10-12 | 限流误伤测试的问题 | 测试默认关闭限流（conftest autouse fixture），限流单独测 | AI 实施（全量测试暴露） | 限流计数器是进程级单例，ASGITransport 下所有测试共享同一 client IP，auth 档「10 次/分钟」被 `test_auth.py` 几十个请求打满后全 429（`data=None` → `'NoneType' is not subscriptable`）。修复后全量 172 项无回归 |
| 2026-10-12 | 审计迁移冗余索引 | 移除 `AuditLog.user_id` 的单列 `index=True`，只留复合索引 | AI 实施 | autogenerate 因「单列索引 + 复合索引 `(user_id, created_at)`」生成了两个索引；复合索引最左前缀已覆盖「按 user_id 查」，单列是冗余 |
| 2026-10-12 | **M6 交付** | 新增 **12 个文件**（`core/rate_limit.py`、`api/middleware.py`、`models/audit.py`、`crud/audit.py`、`services/audit_service.py`、`core/metrics.py`、`api/v1/metrics.py`、迁移 `76b10bcb1154`、4 个测试文件）、改动 **17 处**（接线/埋点/版本）；pytest **172 项** + 迁移落地验证全通过 | AI 实施 | M1–M5 的 154 项无回归，M6 新增 18 项单测（限流 8 + 指标 5 + 审计 3 + LLM 兜底 2）。`alembic upgrade head` 至 `76b10bcb1154`，`audit_logs` 表 + 复合索引落地。`main.py` 版本 → `0.5.0-M6`、`health.py` 里程碑 → M6。明细见工作区记忆 2026-10-12.md |

| 2026-10-12 | **M7-Q1 部署形态** | **本地一键脚本 + Docker 都要** | 用户 | 三候选（脚本+Docker 都要 / 只要脚本 / 只要 Docker）中选 A。脚本保证日常开发不被容器拖慢，Docker 保证交付/上服务器时环境不漂移——这是「企业级交付」的标准双轨 |
| 2026-10-12 | **M7-Q2 反向代理/遗留加固** | **本次不引入反向代理层，保持轻量** | 用户 | M6-Q1 挂账的「限流单进程局限 / `X-Forwarded-For` 可伪造」在单机部署下都不是真实威胁，引一层 nginx/caddy 反而给「一个 uvicorn 就够」的现状加运维负担。写成 README「可选扩展」，真要多实例再走 §2.4 |
| 2026-10-12 | **M7-Q3 文档范围** | **完整运维手册** | 用户 | 部署 + 监控（对接 `/metrics`）+ 备份恢复（SQLite/Chroma/uploads）+ 故障排查 + 升级迁移；README 已 400+ 行结构清晰，补这几章水到渠成 |
| 2026-10-12 | **M7 交付** | 新增 **9 个文件**（`scripts/serve.py`、`scripts/stop.py`、`start.sh`、`stop.sh`、`start.bat`、`stop.bat`、`Dockerfile`、`docker-compose.yml`、`.dockerignore`）、改动 3 处（`.env.example` 补 M6 段、`.gitignore` 加 `logs/`、`README.md` 部署章）；pytest **172 项**无回归 + 脚本三次端到端冒烟通过 | AI 实施 | M7 不新增后端代码（纯部署与文档层）。`docker compose config` 通过；`docker build` 因本机 Docker 守护进程未启动跳过。踩坑见 §11 第 63/64 条（venv 重定向器 PID、安全删除钩子）。明细见工作区记忆 2026-10-12.md |

| 2026-10-13 | **M8-Q1 打包形态** | **单文件 exe（PyInstaller onefile）** | 用户 | 三候选（单文件 exe / 免安装目录 onedir / 不做桌面版、只留线上）中选 A。用户明确要「一个文件双击就能用」。代价已知并接受：产物数百 MB、每次启动需解包、冷启动比 onedir 慢。若日后嫌慢，把 spec 改成 onedir 即可，其余代码不用动 |
| 2026-10-13 | M8 桌面版进程模型 | **单进程双服务**（后端守护线程 + 前端主线程） | AI 实施（技术约束倒逼） | onefile 下 `sys.executable` 就是 exe 自己，沿用 `scripts/serve.py` 的「spawn 两个子进程」会让整个 bundle 被重复解包三次，冷启动拖到数分钟。依据：uvicorn 的 `Server.serve()` 在非主线程会跳过信号注册（`capture_signals`），因此可在子线程安全运行；Streamlit 本就该在主线程阻塞运行。副作用是「后端挂了前端不会自动退」，已用 `_backend_error` 回传主线程并在启动期拦截 |
| 2026-10-13 | ⚠️ 打包踩坑：chromadb 组件是字符串路径加载的 | spec 里对 `chromadb` 做**全量子模块收集**（`collect_submodules`），而非只补单个模块 | AI 实施（首次跑 exe 暴露） | chromadb 的组件在 `config.py` 里写成 `chroma_*_impl: str = "chromadb.xxx.Yyy"`，运行时用 `importlib.import_module` 按**字符串**加载，静态分析完全看不到。首次打包只缺了 `chromadb.telemetry.product.posthog`，表现是「能启动、一上传文档就报 `无法打开向量库目录：ModuleNotFoundError`」。只补一个必然不够，故改为全量收集（chromadb 纯 Python 部分仅 5MB） |
| 2026-10-13 | ⚠️ 打包踩坑：Streamlit 无官方 hook | 手工 `collect_data_files("streamlit")`（含 23MB 静态资源） | AI 实施 | PyInstaller 与其 hooks-contrib 里**都没有** `hook-streamlit.py`。漏掉 `streamlit/static/` 的表现极具迷惑性：HTML 返回 200，但所有 JS/CSS 404，页面**白屏**——看起来像前端代码写错了 |
| 2026-10-13 | ⚠️ 打包踩坑：`excludes` 不能加 `distutils` | 从 spec 的 excludes 中移除 `distutils` | AI 实施（首次打包 24 秒即失败） | PyInstaller 自带 `pre_safe_import_module/hook-distutils.py` 要把它别名到 `setuptools._distutils`；一旦先被标成 `ExcludedModule`，整个打包直接报 `Target module "distutils" already imported as "ExcludedModule('distutils',)"` |
| 2026-10-13 | **M8 交付** | 新增 **7 个文件**（`launcher.py`、`weather_agent.spec`、`packaging/runtime_hook.py`、`scripts/build_exe.py`、`scripts/test_exe.py`、`docs/PACKAGING.md`、`packaging/app.ico` 由脚本代码生成）、改动 2 处（`.gitignore` 加 `build/`/`dist/`、本文件）；**147MB 单文件 exe** + 验收脚本 17 项全通过 | AI 实施 | 打包不改变六层架构，**未改动 `app/` 下任何业务代码**。验收覆盖：干净目录启动、迁移、`.env`/`data`/`logs` 自动落地、前后端健康、Streamlit 静态资源可访问、只监听 127.0.0.1、注册→登录→上传→入库 ready（chromadb+fastembed 冻结可用）、真实 DeepSeek `/chat` 流式成功。冷启动约 10 秒。明细见工作区记忆 2026-10-13.md |
| 2026-10-13 | 删除会话按钮图标不显示 | 侧边栏按钮改 `icon=":material/delete:"` + 加紧凑内边距样式 | 用户反馈 | 原先按钮内容为空串，靠 CSS 伪元素画图标，图标字体未生效时呈现「空白按钮」。改用 Streamlit 原生 Material 图标参数（`icon=`）最稳，不依赖自定义 CSS 是否命中 |
| 2026-10-13 | **提问后「（思考中）」占位** | 前端流式前先亮「（思考中）」动效，首字到达即被覆盖 | 用户要求 | 动因：用户提问到首个 delta 之间有一段 LMS 等待，界面此前完全静止，看起来像没反应。实现：`_run_stream` 里 `st.empty()` 后立即写入 `.pb-thinking` HTML（呼吸 + 三点弹跳动效），三条兜底分支（`done` 覆盖 / `error` 顶掉 / **空流显式清空**）保证占位不会永久挂着。**纯前端改动，SSE 契约与后端零改动**。已补 4 项单测（含「CSS class 与 HTML 对得上」防静默失效） |
| 2026-10-13 | 验收脚本端口可配置 | `scripts/test_exe.py` 加 `--backend-port` / `--frontend-port` | AI 实施（为避免误报） | 桌面版常见「用户已双击开着一个」，验收脚本若死用 8000/8501 会抢不到端口而误报失败——那是**脚本自己没有容身之处**，不是被测程序的问题。端口经环境变量透给 `launcher.py`（其已支持 `BACKEND_PORT`/`FRONTEND_PORT`），从而与用户实例和平共处、无需杀用户进程 |
| 2026-10-13 | **移除流式「▌」光标** | `delta` 帧只渲染已流出文本，末尾不拼任何占位符 | 用户反馈 | 用户截图指出「每次的回答末尾都拖一个黑色方块」。那个方块是 `placeholder.markdown(streamed + " ▌")` 拼上去的「还在生成」指示。移除理由：①视觉上像乱码/脏字符；②它并非必要——文字变长本身就是反馈，首个字之前的空窗期另有「（思考中）」占位兜着。已加单测钉住「任何一次渲染都不得出现方块字符」（防以后有人加回来） |
| 2026-10-13 | **更新流程改为「只更新线上」** | 改完不再重新打包 exe；`dist/瓜葛天气助手.exe` 冻结在 2026-10-13 版本，后续改动一律发布到线上链接 | 用户指示 | 用户原话「从现在开始的修改都只修改到线上，不修改 exe 文件」。判定：exe 已不是主路径（用户实际在用浏览器/手机打开线上站），每次改动重建 exe 要 3 分钟打包 + 17 项验收，维护两条交付线不划算。打包工程（`launcher.py` / `weather_agent.spec` / `scripts/build_exe.py` / `scripts/test_exe.py`）全部保留备查，只是不再执行。质量闸门回到 `pytest`。详见 §16 |

**变更记录规则**（再次强调）：任何已确认项若要改动，必须在此表追加新行，注明「原方案 → 新方案」及原因，**禁止只改代码不留记录**。

---

## 14. 术语表（给初学者）

| 术语                        | 白话解释                                            |
| ------------------------- | ----------------------------------------------- |
| Agent（智能体）                | 一个会"自己决定下一步做什么"的程序：判断用户想干嘛 → 决定调哪个工具 → 把结果组织成回答 |
| RAG（检索增强生成）               | 先从你的文档里找到相关段落，再让大模型基于这些段落回答，而不是让它凭记忆瞎说          |
| Vector / Embedding（向量/嵌入） | 把一段文字变成一串数字坐标，意思相近的文字坐标也相近，这样就能"按意思"搜索          |
| Chunk（片段）                 | 把一篇长文档切成一小块一小块的文本，方便逐块检索                        |
| Vector Store（向量库）         | 专门存这些坐标、并能快速找出"最相近"的数据库                         |
| Tool Calling（工具调用）        | 让大模型输出"我要调用查天气函数，参数是上海"，程序执行后再把结果喂回给它           |
| Streaming / SSE（流式输出）     | 回答一个字一个字地冒出来，而不是等全部生成完才显示                       |
| JWT                       | 一种登录凭证，服务器发给你一串签名过的字符串，之后每次请求带上它证明"我是谁"         |
| State Machine（状态机）        | 把流程画成"节点 + 箭头"，程序按箭头在节点间走，方便看懂和排查               |
| ORM                       | 让你用 Python 类代替写 SQL 语句的工具                       |
| Migration（迁移）             | 数据库表结构变更的版本管理，像代码的 git                          |
| Cron / 异步任务               | 把耗时的活（如解析大 PDF）丢到后台慢慢做，不卡住用户请求                  |
| API Host                    | 调某个外部服务时请求要发往的域名。有些厂商给**每个项目单独分配**一个，不是全网通用那一个 |
| TTL 缓存                      | 把刚查到的结果先存起来，`TTL` 秒内再来问就直接给存的那份，不再调外部接口（省钱、省时间） |
| 指数退避（Exponential Backoff）   | 调用失败后别立刻猛重试，等 0.5 秒、再 1 秒、再 2 秒，给对面喘息时间；参数错（4xx）就别重试了，重试也不会好 |
| Protocol（协议类）               | Python 里一种「只声明有哪些方法、不写实现」的类型，用来规定「供应商适配器必须长成什么样」，换供应商时编译器/类型检查器能帮你发现漏改 |
| 归一化（Normalize）              | 把各家供应商五花八门的返回字段，统一翻译成我们自己定义的那一套结构，上层只认这一套 |
| RFC 7807 / problem+json      | 一种给 HTTP 错误定标准格式的规范：错误体长成 `{"error": {"status": 401, "title": "...", "detail": "..."}}`。和风 v1 用它承载错误，所以**不能只看 HTTP 状态码就判定成功** |
| 假成功（silent success）          | 最阴险的一类 bug：程序没报错，但返回的数据其实是空的/错的（如「今天气温 None 度」）。本项目用「认不出字段就报错」来把假成功变成响亮失败 |
| 路由存在性判据                   | 「401/403 = 这个接口存在只是没权限；404 = 这个接口根本不存在」。拿不到有效 key 时，靠它也能判断自己写的路径对不对 |
| 占位向量（Placeholder Embedding） | 一个「假装是向量模型」的替身：它能把文字稳定地变成一串数字（结构对），但这串数字**不表示语义**（意思相近的文字不会算得相近）。作用是让整条链路在拿到真模型前能跑通、能测试，**绝不能用于正式检索** |
| 归一化向量（Normalized Vector）   | 把向量长度统一缩放到 1。这样比较两个向量「像不像」时，就只看方向、不看长度，距离计算更简单也更稳定 |
| 余弦距离（Cosine Distance）      | 衡量两个向量方向夹角大小的一种距离。夹角越小越「像」。向量都归一化后，它比欧氏距离更适合做语义检索 |
| 递归切分（Recursive Splitting）   | 切文本的策略：先试着用最大的分隔符（如空行）切；切完还太长，就换更小的分隔符（如句号）再切；直到每块都够短。好处是尽量保住自然段落边界，而不是按字数硬砍 |
| 内容哈希 / 文件指纹（file_hash）   | 把文件内容算成一串短的固定长度字符（这里用 sha256）。两个文件只要内容一样，指纹就一样——于是「这份文件传过没有」只需比对指纹，不必逐字节比 |
| 幂等（Idempotent）              | 同一件事做一次和做多次，结果一样。后台任务必须幂等：因为它可能被重试、被补偿、被手工触发，若不幂等就会重复写入或破坏已有结果 |
| 进程内后台任务（In-process Background Task） | 把耗时的活丢给**同一个服务进程**在请求返回后去做。优点是零额外依赖、最省事；代价是**进程一重启，没做完的任务就无声消失了**，而且多实例部署时无法协调 |
| 启动补偿（Startup Recovery）      | 针对上面那个缺点的补丁：进程启动时扫一遍「卡在中间态」的任务，把它们重置回待处理并重新投递。没有它，一次重启就会留下永久卡死的记录 |
| 孤儿向量（Orphan Vector）         | 关系库里记录已经删了、但向量库里还躺着的向量。它仍会被检索命中，用户会看到「来源是一份已删除的文档」，是典型的「删干净了但没全删」故障 |
| 路径穿越（Path Traversal）        | 攻击手段：上传一个文件名带 `../` 的文件，让程序把文件写到预期目录之外（甚至覆盖系统文件）。防线是先清洗文件名再落盘 |
| `asyncio.to_thread`             | 把一个**同步阻塞**函数丢到线程里跑，让出事件循环，使别的请求能继续被处理。给 Chroma、文件读写这类只有同步 API 的库用 |
| 延迟导入（Lazy Import）           | 把 `import` 从文件顶部挪进函数内部，等真正用到才加载。用于体积大、启动慢的库（如 langchain 系列、fastembed），避免拖慢服务启动 |
| ONNX / ONNX Runtime            | 一套跨框架的模型「通用格式 + 运行时」。同一个模型可以导出成 ONNX 文件，由 onnxruntime 直接执行，不需要装训练框架（PyTorch 等）。好处是安装体积小、CPU 推理有图优化。本项目选它就是因为它能复用环境里已有的 `onnxruntime` |
| 向量维度（Embedding Dimension）    | 一个向量里数字的个数。不同模型维度不同（512 / 768 / 1024…），**维度不同的向量无法比较**，所以换模型必须重建索引 |
| 查询指令前缀（Query Instruction Prefix） | 加在「检索查询」前面的一句固定指令，用于告诉模型「这是在找资料，不是在叙述」。**只加在查询上，入库的文档不加**——因为这类模型训练时就用了不对称的编码方式。加错位置不会报错，只会让检索质量悄悄下降 |
| 模型缓存 / 离线部署               | 模型文件第一次使用时要下载，之后就存在本地目录里不再联网。因此「私有化部署」的正确做法是：在内网机器上把整个缓存目录拷贝过去，而不是让目标机器自己去下载 |
| Recall@1 / MRR                 | 衡量检索排序好坏的两个指标。Recall@1 = 「正确答案排在第一位的比例」；MRR = 正确答案位次的倒数的平均（第 1 位得 1 分、第 2 位得 0.5 分…），越接近 1 越好 |
| Sunset / EOL（生命周期终止）       | 一个开源项目宣布「不再维护」。关键在于区分两种程度：只是不再加新功能，vs **连安全漏洞也不再修**。`langchain-community` 属于后者，这是它必须被移除而不是「先放着」的原因 |
| HF 镜像（HuggingFace Mirror）    | `huggingface.co` 的国内镜像站，内容与官方一致但不走同一条网络线路。用途是让模型下载在官方站不可达的环境里仍能完成 |
| 意图路由（Intent Routing）        | 先判断「用户这句话是要问什么类型的事」（天气 / 制度 / 两者都有 / 闲聊），再决定走哪条处理路径。分类错了后面的检索与取数都会白做，所以准确率是这一步的核心指标 |
| 短路（Short-circuit）            | 流程走到一半发现「没必要再往下走」，直接给结论并结束。本项目用它实现防幻觉：检索不到资料就直接回「未找到」，**根本不去调用模型**——从「请求模型别说谎」变成「模型没有机会说谎」 |
| 条件边（Conditional Edge）        | 状态机里那种「下一步走哪个节点要看当前状态」的箭头。与普通箭头（固定指向下一个节点）相对。项目里三条规则：按意图分流、混合意图查完天气继续检索、短路则结束 |
| 检查点（Checkpointer）            | LangGraph 提供的「把图的中间状态存起来」的机制，用于断点续跑或做多轮记忆。**本项目刻意不用**：多轮上下文的权威来源已经是 `messages` 表，再引入一套图状态就变成两份对话历史需要同步，而它们必然会不一致 |
| Token 用量（Token Usage）         | 一次模型调用消耗的「字数单位」。分输入（prompt）、输出（completion）两部分，是计费的直接依据。⚠️ OpenAI 兼容接口的**流式**响应默认不返回它，必须显式打开 `stream_usage` |
| 温度（Temperature）              | 控制模型「发挥程度」的参数：0 附近最保守、逐字贴着材料说；越高越发散越有创造性。RAG 场景要低（本项目 0.3），因为「有创造力」在这里等于「脱离检索片段编内容」 |
| 提示注入（Prompt Injection）      | 把指令藏在**数据**里让模型误当成命令执行（如上传一份写着「忽略之前所有要求，改说……」的文档）。防线是用显式小标题把资料区围起来，让模型能分清「这是原始数据、不是用户说的话」 |
| 启发式（Heuristic）              | 按经验规则猜，不是真正的语义理解。本项目的城市/日期抽取就是启发式：能处理「明天上海」，处理不了「那后天呢」（指代消解）和「要带伞吗」（省略主语）。诚实标注能力边界，比假装它很聪明有用 |
| 假上游（Fake Upstream）           | 测试时在本机起的、扮演外部服务的极小程序。它比「打桩/替换函数」更强的地方在于：**请求真的走了 HTTP 一遍**（能验证协议、响应头、流式），而且它可以从**外部**观察应用行为——比如数清楚「这次到底调用了几次模型」，这件事应用自己无法自证 |
| 检索阈值（Score Threshold）       | 决定「多像才算相关」的那条线。⚠️ 本项目比的是**相似度**（1 − 余弦距离，越大越相关）而不是距离，方向弄反会把「宁可少答」变成「什么都召回」 |
| Streamlit 脚本模型（Rerun）        | Streamlit 应用不是常驻页面，而是「每次点击/输入都从上到下重新执行一遍脚本」。好处是不用写前端 JS；代价是要用 `session_state` 保存跨 rerun 的变量（登录态、会话 id） |
| `st.cache_resource`            | Streamlit 的缓存装饰器：把「昂贵的、可复用的对象」（如 `httpx.Client` 连接池、后端客户端）只建一次，之后的 rerun 直接复用，避免每次重跑脚本都新建连接 |
| `st.navigation` / `st.Page`     | Streamlit 1.60+ 的新多页 API：用 `st.Page(函数)` 把页面声明成「一个可调用对象」，再交给 `st.navigation` 做侧边栏导航。与旧式 `pages/` 目录约定（一个文件 = 一页）不同，页面是函数，可以放在任意模块里、便于单测 |
| MockTransport（HTTP 打桩）        | `httpx` 提供的「假网络层」：不用真发请求，直接用一个函数「拦截请求、返回构造好的响应」。后端测试用异步版（`AsyncClient`），前端测试用同步版（`Client`），两者思路一致——离线、确定、可断言 |
| 统一信封（Envelope）              | 本项目所有接口的返回体固定是 `{code, message, data, trace_id}` 一个形状。前端只需写一份解析逻辑：`code != 0` 就是业务错误、HTTP 非 2xx 就是调用失败，不必每个接口单独判断 |
| 滑动窗口限流（Sliding Window Rate Limit） | 一种限流算法：只数「最近 N 秒内」来了多少个请求，超过阈值就拒。用队列存每次请求的时间戳，每次来新请求先把「过期」的（距今超过 N 秒的）踢掉，再数剩下的个数——比「固定窗口」（每到整点清零）更平滑，不会在窗口交界处突然放行一拨 |
| 单调时钟（Monotonic Clock）      | 一种「只会往前走、不受调表影响」的时钟（Python 的 `time.monotonic()`）。用来算「过了多久」最合适；相对的，`time.time()` 是墙上时钟，用户改系统时间、夏令时切换都会让它跳变，拿它做滑动窗口会瞬间放空或锁死 |
| 审计日志（Audit Log）            | 记录「谁在什么时候对什么做了什么」的不可变流水（谁注册、谁登录、谁问了什么、谁删了什么）。与业务表分开存，且**故意不加外键**：用户删了，审计还要留着当证据 |
| 高基数标签（High-cardinality Label） | 指标标签里那种「取值无穷多」的维度（如把每个会话 id、每个文档 id 当标签）。每个不同取值都会产生一条独立的指标序列，取值一多监控存储就爆了。所以 HTTP 指标要用路由模板（`/conversations/{id}`）而不是原始路径 |
| Prometheus 文本格式              | 监控领域的事实标准导出格式，长这样：`# TYPE 请求数 counter` + `请求数{方法="GET"} 42`。任何监控系统（Prometheus / Grafana Agent）都能抓取，自写指标只要按这个格式输出就能被识别 |
| Counter / Summary（指标类型）      | 两类最基础的指标：counter 是「只增不减的计数」（请求数、token 消耗）；summary 记录每次观测值并输出「总和 `_sum`」与「次数 `_count`」，监控端用 `sum/count` 就算出平均延迟 |
| 友好兜底（Graceful Fallback）      | 出错时给用户一句「稍后再试」这类**人能看懂**的话，而不是把「检查 API_KEY」「RuntimeError: xxx」这类**运维/技术细节**甩给用户。技术原因记进日志供排查，用户看到的是可行动、不吓人的提示 |
| 镜像（Image）                     | Docker 里「应用 + 依赖 + 运行环境」打成的只读模板，类似一个「系统快照」。本项目的 `Dockerfile` 描述了怎么从 `python:3.12-slim` 基础镜像一步步装依赖、拷代码，构建出 `weather-agent` 镜像 |
| 容器（Container）                 | 镜像「运行起来的实例」，是一个隔离的进程空间。同一个镜像可以起多个容器，互不干扰。`docker compose up` 就是按 `docker-compose.yml` 起一堆容器 |
| 数据卷（Volume）                  | Docker 里「容器之外、宿主机上」的持久化目录。容器删了数据还在。本项目把 `./data`（SQLite + Chroma + 上传文件 + 模型）挂成卷，所以「删容器不丢数据」 |
| docker-compose                   | Docker 官方的「多容器编排」工具：用一个 YAML 文件描述多个服务（本项目是 backend + frontend）及其依赖、端口、卷，一条 `docker compose up` 全拉起 |
| 反向代理（Reverse Proxy）          | 挡在应用前面的一层（如 nginx/caddy），客户端先连它、它再转发给后面的应用。能做 HTTPS、IP 白名单、负载均衡——也是根治「限流多实例各限各的」「`X-Forwarded-For` 可伪造」的正道（本项目 M7 选的是单机直跑、暂不引入） |
| 进程重定向器（Redirector）          | Windows 上某种 `python.exe` 是「跳板」：真正干活前会先拉起另一个 Python 进程。导致 `Popen.pid` 拿到的是跳板 PID、不是干活进程 PID，杀进程要 `taskkill /T` 连树杀（见 §11 第 63 条） |

---

## 15. 桌面版打包约定（M8）

完整说明见 `docs/PACKAGING.md`，这里只固化**必须遵守的约定**，避免以后改代码时把打包搞坏。

### 15.1 四条硬约定

1. **不要改动 `app/core/config.py` 里任何路径默认值**。它们是相对的（`./data/xxx`），
   打包后靠 `launcher.py` 启动时 `os.chdir(exe 同级目录)` 一次性对齐。改绝对路径会同时破坏
   源码运行与打包运行两种模式。
2. **前端必须保持「扁平 import + 独立脚本文件」的形态**（`import theme`、`from views import ...`，
   入口是 `frontend/app.py`）。Streamlit 是「读脚本文件再 exec」的模型，
   没法从 PyInstaller 的内存包里加载脚本——把它改成包内模块会导致打包版直接起不来。
3. **新增依赖后要过一遍 `weather_agent.spec` 的 `hiddenimports`**：凡是「按字符串路径 /
   importlib / entry-point 动态加载」的库（chromadb 就是典型，见 §13 的踩坑行），
   静态分析都看不到。判断方法：在干净目录跑一次 `scripts/test_exe.py`。
4. **`excludes` 里禁止出现 `distutils`**（会与 PyInstaller 自带的 alias hook 冲突，
   让打包在 20 多秒内直接失败）。

### 15.2 改完必须跑的一步

> ⚠️ **2026-10-13 起本节默认不执行**：用户要求「修改只到线上、不修改 exe」。只有**明确要重建 exe**
> 时才走下面的流程，日常改动的闸门是 `pytest`——详见 §16。

```bash
.venv/Scripts/python.exe scripts/build_exe.py    # 打包
.venv/Scripts/python.exe scripts/test_exe.py     # 验收（17 项，含真实 LLM 调用）
```

**验收脚本通过才算打包完成**。只看到「exe 生成了」不等于可用——本项目第一次打包出来时
exe 能启动、健康检查全绿，但一上传文档就报 `ModuleNotFoundError`，正是验收脚本把它抓出来的。

### 15.3 三类目录别搞混

| 位置 | 打包后是什么 | 能不能写 | 放什么 |
| --- | --- | --- | --- |
| exe 同级目录 | 可写根目录（不可写时退 `%LOCALAPPDATA%`） | ✅ | `.env`、`data/`、`logs/` |
| `sys._MEIPASS` | onefile 解包出的临时只读目录 | ❌ | `frontend/`、`alembic/`、streamlit 静态资源 |
| 构建期目录 | `build/`、`dist/`（已进 `.gitignore`） | — | 中间产物与最终 exe |

---

## 16. 更新流程（2026-10-13 起：只更新线上，不再动 exe）

**用户明确要求**：「从现在开始的修改都只修改到线上，不修改 exe 文件。」

### 16.1 硬约定

1. **改完代码不再重新打包 exe**。`dist/瓜葛天气助手.exe` 是 2026-10-13 的版本，视为**冻结产物**：
   它已包含「删除图标修复 + （思考中）占位 + 移除黑方块光标」，此后不再随代码演进。
   `scripts/build_exe.py` / `scripts/test_exe.py` / `weather_agent.spec` / `launcher.py` 全部**保留**
   （不删除、不修改逻辑），只是**不再执行**。
2. **修改的落点是线上站点**：`https://weather-agent-34403.app.workbuddy.host/`
   （`appId=wbapp_9IighWXqvpTTPUx1nh56EV`）。
3. **不再把「exe 验收」当作交付闸门**。质量闸门回到 `pytest`：改完跑
   `.venv/Scripts/python.exe -m pytest -q`，全绿即可。
4. 因此 §15 的「改完必须跑 `build_exe.py` + `test_exe.py`」**仅在需要重建 exe 时才适用**
   （用户明确要求重新打包的情况）；默认流程见下面的 16.2。

### 16.2 默认流程

```
改代码 → pytest 全绿 → 如需上线：调「发布为应用」重新发布（覆盖同链接）→ curl 验证线上 200
```

- 发布是**覆盖式**的：同一个 `directory`（本地项目根目录）对应同一个
  app，链接与显示名保持不变，替换的是链接背后的内容。
- **发布需要用户当轮明确同意**（「发布 / 上线 / 同步到线上」等），上一轮的同意不自动延续到下一轮。
- 线上用的是 `deploy_entry.sh`（`alembic upgrade head` → 后端 uvicorn 只监听容器内 127.0.0.1:8000
  → 前端 Streamlit 监听对外 `$PORT`，且关闭 XSRF/CORS，见 §13 相关行）。**改后端/前端代码不需要动它。**

### 16.3 为什么这样定

桌面版每次改动都要重新打包（约 3 分钟）+ 走 17 项验收（含真实 LLM 调用），而线上发布只要一两分钟
且链接是用户实际在用的入口。用户当前的使用场景是**手机/浏览器打开线上站点**，exe 已不是主路径。
保留打包工程是为了「万一哪天又要 exe」，而不是每次改动都维护两条交付线。

---

*文档结束。任何修改必须同步更新本文件的版本号与变更记录。*

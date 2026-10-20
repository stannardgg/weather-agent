
## 技术栈（CLAUDE.md §3.0）

FastAPI + Streamlit + LangGraph + Chroma + SQLite + OpenAI 兼容网关 + pypdf/docx2txt 自写解析 + fastembed 本地向量 + 本地直跑

## 快速开始

> 💡 **装好环境后，日常开发/部署推荐直接用一键脚本**：`./start.sh`（或 `start.bat`）自动完成迁移 + 起后端 + 起前端，见下文「部署（M7）」。下面步骤是「手动跑」的完整过程，便于理解每一步在做什么。

```bash
cd weather-agent

# 1. 建虚拟环境并装依赖
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt

# 2. 准备配置（生产环境务必替换 SECRET_KEY，至少 32 字节，否则启动即报错）
cp .env.example .env

# 3. 建表 / 迁移到最新版本（首次必须执行，否则数据库里没有表）
.venv/Scripts/python.exe -m alembic upgrade head

# 4. 启动后端
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000

# 5.（另开一个终端）启动前端 Streamlit
.venv/Scripts/python.exe -m streamlit run frontend/app.py
# 后端地址默认 http://127.0.0.1:8000/api/v1，可用环境变量 API_BASE_URL 覆盖
```

启动后：

| 入口 | 地址 |
| --- | --- |
| 前端界面（登录 / 对话 / 知识库） | http://127.0.0.1:8501 |
| 交互式 API 文档 | http://127.0.0.1:8000/docs |
| 存活检查 | http://127.0.0.1:8000/api/v1/health |
| 就绪检查（含数据库连通性） | http://127.0.0.1:8000/api/v1/health/ready |

- 所有接口返回统一结构：`{"code": 0, "message": "ok", "data": {...}, "trace_id": "..."}`。
- 需要鉴权的接口带 `Authorization: Bearer <token>`；在 `/docs` 右上角 Authorize 里填 token 即可直接调试。
- 注册一个账号：在 `/docs` 里调 `POST /api/v1/auth/register`，再用 `POST /api/v1/auth/login` 拿 token。

## 测试与迁移

```bash
# 单元 / 接口测试（用内存 SQLite + 临时目录，不碰 data/ 下的库与向量库）
# 全程离线：不下载模型、不调外部 API
.venv/Scripts/python.exe -m pytest -q
# → 180 passed

# 只跑前端单测（httpx.MockTransport 打桩，不碰真实后端）
.venv/Scripts/python.exe -m pytest tests/test_frontend.py -q
# → 20 passed

# 对「已启动的服务」跑鉴权主链路冒烟（11 项检查）
.venv/Scripts/python.exe scripts/smoke_m1.py

# M3 端到端冒烟：自动起一个真实 uvicorn 进程 + 临时库/向量库，跑完自动清理
.venv/Scripts/python.exe scripts/smoke_m3.py
# → 30 项通过，0 项失败（占位向量模式）

# 同上，但用真实向量模型（首次会从 HF 镜像下载约 90MB 模型）
.venv/Scripts/python.exe scripts/smoke_m3.py --real-embedding
# → 33 项通过，0 项失败

# M4 端到端冒烟：问答 + 会话全链路（真实 uvicorn + 真实向量 + 真实 SSE）
# 默认用内置的本地假上游，离线、零额度、结果确定；--real-llm 才打真实 DeepSeek
.venv/Scripts/python.exe scripts/smoke_m4.py
# → 61 项通过，0 项失败

# 用真实 LLM（读 .env 的 LLM_* 配置）跑同一套冒烟，人工确认真实模型答得像不像话
.venv/Scripts/python.exe scripts/smoke_m4.py --real-llm

# 向量模型自检：配置回显 / 维度解析 / 装配 / 首次推理 / 确定性 / 语义有效性 / 检索排序 / 前缀影响
.venv/Scripts/python.exe scripts/check_embedding.py
# → 8 项通过

# 数据库迁移
.venv/Scripts/python.exe -m alembic current                    # 当前版本
.venv/Scripts/python.exe -m alembic upgrade head               # 升到最新
.venv/Scripts/python.exe -m alembic downgrade -1               # 回退一步
.venv/Scripts/python.exe -m alembic revision --autogenerate -m "描述"   # 改完 model 后生成脚本
```

> **首次运行真实向量** 需要下载模型。本机实测 `huggingface.co` 不可达（ReadTimeout），因此默认走国内镜像 `HF_ENDPOINT=https://hf-mirror.com`（已在 `.env.example` 里配好）。模型缓存在 `data/models/fastembed`（约 91MB），下一次启动直接命中缓存、无需联网。`pytest` 与「占位模式」冒烟都不触发下载。

> `alembic.ini` 必须保持纯 ASCII：Alembic 用系统区域编码读取它，中文 Windows 下任何中文注释都会导致解码失败。

> `smoke_m3.py` 会自己用 `alembic upgrade head` 在临时目录建库——它验证的是**真实进程**下的行为，`tests/` 里用 `ASGITransport` 的测试覆盖不到「进程内后台任务是否真会执行」这一环。

## 天气工具校准

`.env` 里需要有这两项——和风的 API Host 是**每个项目单独分配**的，不是通用域名，不填会直接报 503：

```bash
WEATHER_PROVIDER=qweather
WEATHER_API_HOST=xxxxx.re.qweatherapi.com   # 控制台里你的项目专属 Host
WEATHER_API_KEY=你的_API_KEY
```

然后运行校准脚本，它会打印 4 个接口的**原始 HTTP 状态码与原始返回体**：

```bash
.venv/Scripts/python.exe scripts/check_weather_api.py 上海
```

### 校准结果（2026-10-12 用真实凭据完成）

| 接口 | 状态 |
| --- | --- |
| `/weather/v1/current/{lat}/{lon}` 实时天气 | ✅ 200，字段名已逐项校准 |
| `/weather/v1/daily/{lat}/{lon}` 每日预报 | ✅ 200，字段名已逐项校准 |
| `/v7/indices/3d` 生活指数 | ✅ 200（**`type` 是必填参数**，缺了返回 400） |
| `/geo/v2/city/lookup` 城市搜索 | ⛔ **403 Security Restriction**（见下节） |
| 路径正确性 / 网络链路 / 错误解析（RFC 7807） / 4xx 不重试 / 降级 / 失败不入缓存 | ✅ 均已在假 key 阶段验证通过 |

### ⛔ 城市搜索当前不可用（403 Security Restriction）

**这不是代码问题。** 同一个 Host、同一个 KEY、同一个源 IP 下，另三个接口都返回 200，只有城市搜索被拒。**关键证据：该路径不带 `location` 参数时返回 400 Missing Parameter** —— 说明请求已经通过了路径匹配与鉴权，是在**参数校验之后**的安全/权限关卡被拦下的。

按官方错误码文档，`SECURITY RESTRICTION`（403）的含义是「违反了你设置的请求限制」。**处置需要到控制台操作**：

1. 控制台 → **项目管理** → 选中项目 → 选中凭据；
2. 查 **API 限制**：若开了白名单，确认 GeoAPI / 城市搜索在允许列表内（或直接关掉——官方称默认即允许所有 API）；
3. 查 **应用限制**：若设了 **IP 白名单**，本机出口公网 IP 必须在内（家庭宽带 IP 会变，测试期建议先留空 = 允许所有）；若设了 **网址（Referer）限制**，服务端调用不带 Referer，**必然被拒**；
4. 改完**等权限生效**——官方提示安全设置变更最长 **1–2 小时**生效，新建凭据通常更快。

**影响面**：「城市名 → 经纬度」这一步会抛 `WeatherAuthError`（503），因此**暂时不能用城市名提问**。权限开通后 `resolve_city` 无需改代码即可工作（解析逻辑已按 v2 结构写好，只差实测确认字段名）。

### ⚠️ v1 与 v7 是两套形状（本项目最容易踩的坑）

网上教程几乎都是旧 v7，而 v1 **不是「改了几个字段名」**：

| 维度 | 旧 v7 | 新 v1（实测） |
| --- | --- | --- |
| 结构 | 包在 `{"now": {...}}` 里 | **扁平顶层，无包装键** |
| 数值 | 字符串 `"22"` | **带单位的对象** `{"value": 22.51, "unit": "°C"}` |
| 湿度 / 降水概率 | `"65"` / `"80"`（百分数） | `0.71` / `0.7`（**0–1 比例**） |
| 风速 | `"12"`，单位含糊 | `{"value": 0.47, "unit": "m/s"}`（契约要 km/h，**×3.6**） |
| 风向 | `"东南风"` | `{"degree": 114, "compass": "ese"}` |
| 预报日期 | `fxDate: "2026-10-12"` | `forecastStartTime: "2026-10-11T16:00Z"`（**UTC，须转东八区**） |
| 预报列表键 | `daily` | **`days`** |

这三处**不报错，只会悄悄给错值**：湿度变成「1%」、风速差 3.6 倍、预报日期整体差一天。换算集中在 `_ratio_to_pct` / `_speed_to_kmh` / `_local_date_str` / `_wind_direction` 四个助手里，各自有单测。

**两级 Host 策略**（本地自测时最容易误判的一点）：同一共享 Host 下，`/weather/v1/*` 返回 401（可路由，只是 key 无效），而 `/geo/v2/*` 与 `/v7/*` 返回 403 Invalid Host（**必须项目专属 Host**）。

> `tests/test_weather.py` 里的 current / daily / indices 样本已是**从真实接口逐字节抓取**的响应（含一份昆明雨天样本，用于验证非零的比例换算——上海那两天恰逢无雨，全 0 的样本验证不了换算）。只有 `GEO_BODY` 仍是构造样本。五道「防假成功」防线见 `CLAUDE.md` §9.3.4。

## 知识库（M3）

六个端点，都需要登录（`Authorization: Bearer <token>`）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/knowledge/documents` | 上传文档（multipart，字段名 `file`）→ **202** |
| GET | `/api/v1/knowledge/documents` | 文档列表（`?page=1&page_size=20`） |
| GET | `/api/v1/knowledge/documents/{id}` | 文档详情（含切分预览，只回前 5 条，全量在 `total_chunks`） |
| PUT | `/api/v1/knowledge/documents/{id}` | 覆盖重解析 → **202** |
| DELETE | `/api/v1/knowledge/documents/{id}` | 删除文档，**同时清掉向量库里的向量与磁盘文件** |
| POST | `/api/v1/knowledge/reindex` | 全量重建索引 → **202** |

支持格式：`.txt` / `.md` / `.csv` 等文本类、`.pdf`、`.docx`。不支持的类型返回 **415**，超过 `MAX_UPLOAD_SIZE_MB` 返回 **413**。

**上传是异步的**：接口返回 **202 Accepted** 而不是 201——文档此刻只是被*接收*了，还在 `pending`。真正的入库在后台任务里跑，请轮询详情接口看状态流转：

```
pending → parsing → embedding → ready
                              ↘ failed（此时 error_message 说明原因）
```

**用 curl 走一遍完整流程**：

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"demo","password":"pw12345678"}' | python -c "import sys,json;print(json.load(sys.stdin)['data']['access_token'])")

# 上传
curl -s -X POST http://127.0.0.1:8000/api/v1/knowledge/documents \
  -H "Authorization: Bearer $TOKEN" \
  -F 'file=@./差旅制度.txt'

# 查状态（把 {id} 换成上一步返回的 document.id）
curl -s http://127.0.0.1:8000/api/v1/knowledge/documents/{id} \
  -H "Authorization: Bearer $TOKEN"
```

**行为约定**（理由见 `CLAUDE.md` §6 / §7 / §8.1）：

- **内容去重**：键是 `(user_id, file_hash)`，不是 `file_hash`。同一用户重复上传同一文件 → `deduplicated=true` 且返回原记录，不重复消耗 embedding；**不同用户上传同一文件各自拥有一份记录**（「每用户私有」的直接结果）。
- **用户隔离**：所有查询强制带 `user_id`。「文档不存在」与「文档属于别人」返回**同一个 404**，避免用错误差异探测文档是否存在。
- **响应体不含 `stored_path` / `file_hash`**：前者是服务器内部路径，后者是内容指纹，都不该给前端。
- **删除是物理删除**，顺序固定为「先删向量 → 再删记录 → 最后删文件」，避免留下会被检索命中的孤儿向量。
- **进程重启的补偿**：M3 用的是进程内后台任务，进程重启会让跑到一半的任务无声消失。启动时会自动把停在 `parsing` / `embedding` 的文档重置为 `pending` 并重新投递。

### 向量模型（真实本地 ONNX 推理）

默认配置：

```bash
EMBEDDING_PROVIDER=fastembed
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
EMBEDDING_DIM=512
HF_ENDPOINT=https://hf-mirror.com
FASTEMBED_CACHE_PATH=./data/models/fastembed
```

**为什么是它**（D-M4-1，2026-10-12 用户拍板，完整对比见 `CLAUDE.md` §13）：`fastembed` 直接跑 ONNX Runtime，**不需要 PyTorch**——本机环境里 `onnxruntime` 已随 chromadb 装好，所以增量成本只有 90MB 模型，而不是 2.5GB 的 torch。中文小模型里它体积、维度、许可三方面最均衡。

| 维度 | 值 |
| --- | --- |
| 模型 | `BAAI/bge-small-zh-v1.5`（中文优化，512 维） |
| 体积 | 约 90MB（缓存在 `data/models/fastembed`） |
| 许可 | MIT |
| 推理 | 纯 CPU，本地 ONNX，**数据不出本机** |
| 首次下载 | 走 `HF_ENDPOINT` 镜像（`huggingface.co` 本机不可达） |

**一个容易踩的坑：bge 中文系列的查询指令前缀。** 官方建议检索时给**查询**加前缀 `为这个句子生成表示以用于检索相关文章：`，**文档不加**。本项目实现了这一约定，但做成可配置：

- 默认对登记在案的中文 bge 模型自动加前缀（`embed_query` 加、`embed_documents` 不加）；
- 设 `EMBEDDING_QUERY_INSTRUCTION=`（空字符串）即可强制关闭，或直接写自定义前缀覆盖；
- **实测结论**：3 组样本 17 条中文查询下，加与不加**不改变检索排序**；仅长 chunk（209 字）对短查询（9 字）的相似度有约 +4% 提升（+0.0796 → +0.0827）。样本量不足，因此默认开启但保留开关。

**换模型时**：同时改 `EMBEDDING_MODEL` 与 `EMBEDDING_DIM`（维度不一致时启动会直接报错，不会静默降级），然后调 `POST /api/v1/knowledge/reindex` 重建索引。`fastembed` 支持的模型名可用 `TextEmbedding.list_supported_models()` 离线查看——注意 Python 版清单与 Rust 版不同，例如清单里**没有** `BAAI/bge-m3`。

### 占位向量（仅测试用，不要在生产使用）

`EMBEDDING_PROVIDER=placeholder` 是**开发期替身**：用 sha256 把文本摊成向量并归一化，保证「同一段文本永远得到同一向量」（重建索引可复现），但**不保证语义相近的文本向量相近**，检索结果与随机无异。

- 它现在只服务于 `pytest` 与冒烟脚本的默认路径，让测试无需下载模型即可跑。
- **非 dev 环境（`APP_ENV != dev`）设它会直接被启动校验拒绝**，这是刻意的——这类"看着能跑"的替身最容易被忘掉带上线。
- `/api/v1/health/ready` 里 `embedder` 组件会如实报 `ready=false` 并附告警语，不会假装绿灯。

## 问答（M4）

一个出题入口 + 三个会话管理端点，都需要登录。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/chat` | 提问。请求体 `stream=false`（默认）返回完整 JSON；`stream=true` 返回 **SSE** |
| GET | `/api/v1/conversations` | 会话列表（`?page=1&page_size=20`，按最近活跃倒序） |
| GET | `/api/v1/conversations/{id}/messages` | 历史消息（分页，**`page=1` 是最新一页**） |
| DELETE | `/api/v1/conversations/{id}` | 删除会话（连同其消息） |

### 请求体

```json
{
  "question": "一线城市住宿费每晚上限是多少元？",
  "conversation_id": null,     // 不传 = 新建会话；传了必须是自己的，否则 404
  "stream": false,             // true → SSE
  "city": null,                // 显式指定城市，优先于从问题里猜
  "date": null                 // YYYY-MM-DD，同上
}
```

`question` 为空或超过 `CHAT_MAX_QUESTION_CHARS`（默认 2000 字）→ **400**。

### 非流式响应（`data` 字段）

```json
{
  "conversation_id": 1,
  "message_id": 2,
  "answer": "一线城市住宿费每晚上限为六百元（差旅与响应制度.txt#第1段）。",
  "intent": "knowledge",
  "intent_source": "rule",
  "sources": [
    {"type": "knowledge", "label": "差旅与响应制度.txt#第1段", "snippet": "…", "score": 0.60, "document_id": 1}
  ],
  "short_circuit": false,
  "notes": ["意图识别[规则] knowledge …", "知识库检索到 5 条相关片段", "…"],
  "usage": {"prompt_tokens": 11, "completion_tokens": 36, "total_tokens": 47}
}
```

- `intent` ∈ `weather` / `knowledge` / `mixed` / `chat`；`intent_source` ∈ `rule` / `llm` / `fallback`。
- `sources` 是**答案的依据**：天气类给「城市 + 日期」，知识库类给 `文件名#第N段` 与相似度分数。
- `notes` 是过程痕迹（引擎调试用），正常回答可以忽略。
- `short_circuit=true` 表示这轮**没有调用模型**，答案是一句固定文案（见下）。

### 流式（SSE）事件顺序

固定为 `meta → delta* → done`，中途失败则最后一项换成 `error`：

```
event: meta
data: {"event":"meta","conversation_id":1,"intent":"chat","intent_source":"rule","sources":[]}

event: delta
data: {"event":"delta","text":"你"}

event: delta
data: {"event":"delta","text":"好"}

event: done
data: {"event":"done","conversation_id":1,"message_id":2,"answer":"你好…","short_circuit":false,"notes":[…],"usage":{…}}
```

**前端在首个 `delta` 之前会显示「（思考中）」**：从提交问题到第一个 `delta` 之间，后端要先跑一次 LLM 意图识别、再做知识库检索，实测有几秒。这段空窗期助手气泡里若什么都不显示，用户很容易当成卡死。所以前端在发送请求的同时就把占位打上去，等首个 `delta` 到达时由流式文本自然覆盖；出错时由错误提示顶掉；一条都没收到则显式清空（不让占位停在屏幕上）。占位是带三个跳动小点的动效（`frontend/theme.py` 的 `.pb-thinking`），静止的文字同样会被误认为卡住。

**流式正文本身不带任何「光标」**：`delta` 帧只渲染已流出的文字，末尾不拼方块/竖线之类的占位符。早先版本拼过一个「▌」作为「还在生成」的指示，但它在回答末尾会拖出一个黑色方块、相当扎眼，已移除——文字在变长本身就是反馈，首个字之前的空窗期另有上面的「（思考中）」兜着。

**用 curl 看一眼流式**：

```bash
curl -N -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"question":"你好，请随便说一句话。","stream":true}'
```

⚠️ **`error` 事件出现时 HTTP 状态码已经是 200**。流一旦开了，后续失败不可能再用状态码表达，只能靠这个事件告诉前端「这轮没答完」——少了它，前端会把截断的回答当成完整回答存下来。

### 三个必须知道的行为约定

1. **防幻觉是代码保证，不是提示词请求。**
   - 意图需要知识库、但一条片段都没检索到 → 回答 `"知识库中未找到相关内容。…"`；
   - 只问天气、但天气取数失败 → 回答 `"暂时没能取到天气数据…"`；
   - 这两种情况下 `short_circuit=true`，且**生成模型一次都没被调用**（`scripts/smoke_m4.py` 站在假上游一侧数请求次数，直接断言为 0）。模型没有机会编，因为它没被调用。

2. **检索按用户隔离，且做了两道锁。** 向量检索带 `where={"user_id": 当前用户}`（这是**必填**关键字参数，漏传直接 `TypeError`）；按 `vector_id` 回关系库取正文时**再校验一次归属**。冒烟脚本里让用户 B 问和 A 同一个问题验证过：B 拿到的是「未找到」，不会命中 A 的文档。

3. **天气意图目前会走短路路径。** 城市搜索 `/geo/v2/city/lookup` 尚被上游 403 挡住（见上文），所以「北京明天要带伞吗」这类问题会返回天气兜底文案而不是真实天气。知识库问答与闲聊不受影响。

### 意图识别怎么判的（Q4C：规则优先 + LLM 兜底）

1. 纯问候短句（≤12 字且不含任何业务词）→ `chat`，来源 `rule`，**不为分类多花一次模型调用**；
2. 天气词与知识库词同时命中 → `mixed`；只命中一侧 → `weather` / `knowledge`，来源 `rule`；
3. 都没命中 → 交给 LLM 分类，来源 `llm`；
4. LLM 不可用或返回无法解析 → 退回 `chat`，来源 `fallback`。

两份词表在 `app/agent/router.py` 里，**当配置看待**，需要随真实提问持续补充。刻意注意：「预警」这类跨语境的词（「橙色预警对应什么处置流程」vs「暴雨橙色预警」）**刻意不放进任何一侧**，否则必然把另一侧判错。

### Agent 编排模式（C 阶段）

系统有两套取数方式，由 `AGENT_MODE` 切换，下游（prompt 组装、短路、SSE 流式）完全共用：

| 模式 | 谁决定调什么 | 优点 | 代价 |
| --- | --- | --- | --- |
| `router`（默认） | 规则/模型判意图后走固定分支 | 确定、零额外 token、可预测 | 加工具要改图；模型没有选择权 |
| `tool_loop` | 模型自己看工具清单决定 | 加工具只需 `register()`；能处理组合意图 | 每轮多一次决策调用；行为依赖模型能力 |

```bash
AGENT_MODE=tool_loop          # 切到自主取数
AGENT_MAX_TOOL_ITERATIONS=3   # 轮次上限，防止「查不到就换个说法再查」的死循环
```

**降级是内建的**：模型不支持 `bind_tools`、或该调的没调、或工具全部失败时，节点会按意图用规则补一次取数，行为退回 `router` 模式。所以 `tool_loop` 的效果只会比 `router` 差在 token，不会差在可用性。

新增工具的步骤（不再改图）：

1. 在 `app/tools/builtin.py` 写一个 `async def handler(args, ctx) -> ToolResult`；
2. 在 `build_default_registry()` 里 `register()` 一个 `ToolSpec`；
3. 在 `evals/agent_cases.json` 加用例，跑评测确认模型会选它。

### 护栏（C 阶段）

- **输入侧**：命中典型劫持句式（伪造 `<|im_start|>` 角色标记、`ignore previous instructions`、索要系统提示词）时，请求**不进模型**，直接返回固定文案并置 `short_circuit=true`。高置信模式单条即拦，低置信模式需累计到 `GUARDRAIL_INJECTION_THRESHOLD`（默认 2），避免「忽略上面的空白」这类正常表达被误伤。
- **输出侧**：空回答替换为兜底话术；回答声称引用知识库却没有任何来源时记一条 note（**不改答案**，因为模型可能在引用历史对话）。

⚠️ 边界：这**不是内容安全审核**。涉政/涉黄/暴力等内容分级需要专门的内容安全服务，本模块只处理「试图劫持指令」这一类结构化攻击。

### 评测

```bash
python scripts/eval_agent.py                  # 规则层，完全离线，可进 CI
python scripts/eval_agent.py --mode full      # 真实模型，评工具选择准确率
```

金标集在 `evals/agent_cases.json`（20 条，覆盖 weather / knowledge / mixed / chat / 注入）。
当前基线：护栏准确率 100%，规则意图准确率 100%（仅在规则命中的样本上），规则覆盖率 70%。

**它证明什么**：规则覆盖度与工具选择是否走对。**不证明**答案的事实正确性——那需要人工评分标准，属于另一个层级的工作。`--mode full` 的结论会随上游模型版本漂移，所以它是人工验收工具，不进 CI。

### LLM 配置

```bash
LLM_BASE_URL=https://api.deepseek.com    # ⚠️ 官方写法不带 /v1
LLM_MODEL=deepseek-flash                 # ⚠️ deepseek-chat 已下线
LLM_API_KEY=sk-...
LLM_TEMPERATURE=0.3
LLM_TIMEOUT_SECONDS=60
```

密钥入口：<https://platform.deepseek.com/api_keys>（只显示一次，需账户余额）。

- **其它 OpenAI 兼容网关（通义 / 智谱 / 本地 vLLM / Ollama）只需改这三个值**，代码一行不动。
- **`LLM_*` 与 `WEATHER_*` 是两家不同服务商**，别把密钥配混——LLM 未配置时的报错文案里也写了这一点。
- **缺少 LLM 密钥不会导致服务起不来**（M1–M3 的功能不需要它）：`/health/ready` 会把 `llm` 组件标成未配置，调用 `/chat` 时才给出明确错误。

## 企业级加固（M6）

### 限流（Q1）

进程内滑动窗口限流，无需登录态、按来源 IP 分三档（`app/api/middleware.py`）：

| 分组 | 默认限额 | 窗口 |
| --- | --- | --- |
| `auth`（注册 / 登录） | 10 次 | 60 秒 |
| `chat`（问答） | 30 次 | 60 秒 |
| `default`（其余接口） | 120 次 | 60 秒 |

`/api/v1/health`、`/api/v1/health/ready`、`/api/v1/metrics` 豁免。超限返回 **429** 统一信封，错误码 `3009`。配置项：`RATE_LIMIT_ENABLED` / `RATE_LIMIT_AUTH_LIMIT` / `RATE_LIMIT_AUTH_WINDOW_SECONDS` / `RATE_LIMIT_CHAT_LIMIT` / `RATE_LIMIT_CHAT_WINDOW_SECONDS` / `RATE_LIMIT_DEFAULT_LIMIT` / `RATE_LIMIT_DEFAULT_WINDOW_SECONDS`。

> ⚠️ 进程内限流**只防打爆自己**，不防「伪造 `X-Forwarded-For` 绕过」；多实例部署时各限各的。真正的防护要在网关/WAF 层做——M7 部署时再评估。

### 审计日志（Q2）

关键操作落库 `audit_logs`（`models/audit.py` + `crud/audit.py` + `services/audit_service.py`），覆盖注册、登录、问答、上传、覆盖、删除、删会话。特点：

- `user_id` **可空且无外键**——用户删了，审计仍保留；
- `detail` 是 JSON 列，可存「意图 / 是否短路」这类运行痕迹；
- `record()` 内部 `try/except`：审计写不进只记警告，**绝不拖垮主流程**；
- `ip` / `trace_id` 由中间件绑定到 ContextVar，审计落库时自动读取。

### 指标（Q3）

`GET /api/v1/metrics` 返回 Prometheus 文本格式（`app/core/metrics.py` 自写，零依赖）。已埋点：HTTP 请求数 / 耗时、LLM 调用次数 / token 消耗 / 错误数、检索耗时、天气调用与缓存命中。配置 `METRICS_ENABLED=false` 可关闭（端点返回 404）。

> HTTP 计数用**路由模板**（`/conversations/{id}`）而不是原始路径做 label——原始路径里的会话/文档 id 是高基数 label，会让指标序列无限膨胀。

### LLM 弹性（Q4）

- **有限重试**：`ChatOpenAI(max_retries=LLM_MAX_RETRIES)`，只对网络异常与 5xx/429 重试，**4xx 不重试**（密钥错、参数错重试一万次也不会好）。
- **友好兜底**：重试耗尽后，非流式返回 **503**、流式发 `error` 事件，文案统一为 `LLM_UNAVAILABLE_ANSWER`（「抱歉，我暂时无法回答…」），**技术原因只进日志，不甩给用户**。

## 部署（M7）

### 本地一键启动（推荐日常开发）

项目根目录提供一键启动/停止脚本，自动完成「迁移 → 后端 → 前端」三步，日志落 `logs/`：

```bash
# Windows（Git Bash 里 ./start.sh，或 cmd 双击 start.bat）
./start.sh            # 或 start.bat
./stop.sh             # 或 stop.bat（清理残留进程用；前台运行时直接 Ctrl+C）

# Linux / macOS
bash start.sh
```

底层是 `scripts/serve.py`（跨平台 Python，用 `sys.executable` 精确定位当前 venv，不必猜 `.venv/bin` 还是 `.venv/Scripts`）：

```bash
.venv/Scripts/python.exe scripts/serve.py     # 前台，Ctrl+C 一起停
.venv/Scripts/python.exe scripts/stop.py      # 按 logs/*.pid 清理残留
```

- 端口可用环境变量覆盖：`BACKEND_PORT`（默认 8000）、`FRONTEND_PORT`（默认 8501）。
- 监听地址默认 `0.0.0.0`（覆盖本机 + 局域网）；设 `BACKEND_HOST=127.0.0.1`、`FRONTEND_ADDRESS=127.0.0.1` 收紧为仅本机。
- 每次启动都会先跑 `alembic upgrade head`，数据库结构始终对齐最新迁移。

### Docker 部署（交付 / 上服务器）

一条命令起前后端两个容器，数据挂在宿主机 `./data`（删容器不丢数据）：

```bash
cp .env.example .env      # 填好 WEATHER_* 与 LLM_* 密钥
docker compose up -d --build
```

| 服务 | 地址 | 说明 |
| --- | --- | --- |
| 前端 | http://localhost:8501 | Streamlit |
| 后端 | http://localhost:8000 | FastAPI，`/docs` 交互文档 |

- 后端容器启动前自动 `alembic upgrade head`。
- 数据持久化在宿主机 `./data`（SQLite + Chroma + 上传文件 + 模型缓存）。
- **首次运行会联网下载向量模型**（约 90MB，走 `HF_ENDPOINT` 镜像）到 `./data/models/fastembed`，之后完全离线。**私有化离线部署**：先在能联网的机器上 `docker compose up` 一次把模型下进 `./data`，再把整个 `./data` 目录拷到目标机。
- 停止：`docker compose down`（保留数据）；加 `-v` 才清数据卷（⚠️ 会删 `./data`）。

### 监控（对接 Prometheus）

后端暴露 `GET /api/v1/metrics`（Prometheus 文本格式，`METRICS_ENABLED=true` 时）。已埋点：HTTP 请求数/耗时、LLM 调用次数/token/错误、检索耗时、天气调用与缓存命中。

最小抓取配置片段：

```yaml
scrape_configs:
  - job_name: weather-agent
    static_configs:
      - targets: ["localhost:8000"]
    metrics_path: /api/v1/metrics
```

> 该端点不鉴权（只暴露进程计数、不泄露业务数据）。要限制访问请在反向代理层做 IP 白名单。

### 备份与恢复

数据全部在 `./data`，备份 = 拷贝这个目录：

| 内容 | 位置 | 说明 |
| --- | --- | --- |
| 关系库（用户/会话/审计/文档元数据） | `data/weather_agent.db` | SQLite 单文件 |
| 向量库 | `data/chroma/` | Chroma 持久化目录 |
| 上传原文 | `data/uploads/` | 按 `{user_id}/` 分子目录 |
| 向量模型缓存 | `data/models/fastembed/` | 约 90MB，可重下，非必需备份 |

```bash
# 停服后整体备份（服务运行中直接拷 SQLite 有风险）
docker compose down
cp -r data data.bak.$(date +%Y%m%d)

# 或只备份关系库（SQLite 在线一致性备份）
sqlite3 data/weather_agent.db ".backup 'data/weather_agent.bak.db'"
```

**恢复**：停服 → 用备份覆盖 `./data` → 启动。⚠️ 向量库与关系库要**成对恢复**（同一时刻的备份），否则 `vector_id` 对应的记录对不上，检索会出现「来源指向已删除文档」或查不到。

### 常见故障排查

| 症状 | 排查 |
| --- | --- |
| 启动报「未找到 venv」 | 先 `python -m venv .venv && pip install -r requirements.txt` |
| 后端 503 + 提示查 `.env` | `WEATHER_*` 或 `LLM_*` 密钥缺失/写错（见 `.env.example` 注释） |
| 问答走天气兜底文案 | 城市搜索 `/geo/v2/city/lookup` 仍被上游 403（见「天气工具校准」），需到和风控制台开通 |
| 首次启动卡在下载模型 | 本机 `huggingface.co` 不可达，确认 `HF_ENDPOINT=https://hf-mirror.com` |
| 端口被占 | `BACKEND_PORT` / `FRONTEND_PORT` 换端口，或跑 `scripts/stop.py` 清理残留 |
| 前端连不上后端（Docker） | 确认 frontend 的 `API_BASE_URL=http://backend:8000/api/v1` 用的是容器内服务名 |

### 升级迁移

1. 拉最新代码；
2. 本地：`start.sh`（自动 `alembic upgrade head`）；Docker：`docker compose up -d --build`；
3. 若换了向量模型（`EMBEDDING_MODEL`），改完 `.env` 后需 `POST /api/v1/knowledge/reindex` 重建索引（新老模型向量不在同一坐标系）。

> **反向代理（可选扩展）**：单机部署下进程内限流已够用；若要多实例部署或需要 HTTPS/IP 白名单，请在前面加一层 nginx/caddy——它能根治限流的「多实例各限各的」与 `X-Forwarded-For` 可伪造两个局限（M6-Q1 决策已记录，属新决策须走 `CLAUDE.md` §2.4）。

## 桌面版（M8）

不想装 Python、也不想开 Docker？可以打成一个**双击即用的单文件 exe**。

```bash
.venv/Scripts/python.exe scripts/build_exe.py     # 打包，产物在 dist/
.venv/Scripts/python.exe scripts/test_exe.py      # 验收（17 项，含真实 LLM 调用）
# 若本机已有实例占着 8000/8501，换端口跑即可（无需关掉那个实例）：
.venv/Scripts/python.exe scripts/test_exe.py --backend-port 18000 --frontend-port 18501
```

产物：`dist/瓜葛天气助手.exe`（约 **147 MB**）+ `dist/使用说明.txt`。
把这两个文件放在任意一个文件夹里，双击 exe 即可：

1. 首次运行自动在 exe 同级生成 `.env` 与 `data/`（数据库、上传文件、向量索引、模型缓存都在这里）；
2. 自动执行数据库迁移；
3. 起后端（`127.0.0.1:8000`）与界面（`127.0.0.1:8501`），并自动打开浏览器；
4. 关掉那个控制台窗口 = 完整退出。

**特点**

- **只监听 127.0.0.1**：局域网内其他机器访问不到（与「一键脚本」默认监听 `0.0.0.0` 不同，桌面版刻意收紧）。
- **数据可整体搬迁**：整个文件夹拷走就是完整备份，换机器直接能用。
- **配置可改**：exe 同级的 `.env` 用记事本就能改，重启生效。
- **零外网也能跑**（向量模型已随包/随目录提供时）；否则首次运行需联网下载一次模型。

**已知代价**（onefile 的固有特性，不是 bug）

- 体积大；每次启动要先解包到临时目录，本机实测**冷启动约 10 秒**（含迁移与建表）。
- 只保证 Windows。若日后再打包到 Linux/macOS，主要改图标格式与 `console` 行为。
- 端口固定 8000 / 8501，被占用时用环境变量 `BACKEND_PORT` / `FRONTEND_PORT` 覆盖。

设计与踩坑（含「为什么是单进程双服务」「spec 里每段在解决什么」）见 **`docs/PACKAGING.md`**。

## 目录结构

分层规则见 `CLAUDE.md` §4：依赖只能自上而下（L6 → L5 → L4 → L3 → L2 → L1），L1 基建层可被所有层引用，L2 数据层只能被 L3 访问。

```
app/
├── main.py                 # 【L5】FastAPI 入口：trace_id 中间件、异常处理、路由挂载、启动补偿
├── core/                   # 【L1】config 配置 / logging 日志 / security JWT+bcrypt / exceptions 异常
├── schemas/                # 横切层：common 统一响应与错误码 / user / weather / knowledge / chat（含 4 个 SSE 事件）
├── api/
│   ├── deps.py             # 【L5】依赖注入：get_db、get_current_user（不含任何 SQL）
│   └── v1/                 # 【L5】health / auth / knowledge / chat（含 SSE 帧化）/ conversations
├── db/                     # 【L2】base ORM 基类 / session 异步引擎与会话
├── models/                 # 【L2】user / conversation（含 messages）/ document（含 document_chunks）
├── crud/                   # 【L2】数据库唯一出口：user / health / document / conversation
├── services/               # 【L3-L4】user_service / knowledge_service（入库全流程）/ chat_service（问答编排）
├── tools/                  # 【L3】base 供应商 Protocol / cache TTL 缓存 / weather 和风 v1 实现+门面
├── rag/                    # 【L3】loader 解析(pypdf/docx2txt 自写) / splitter 中文切分 / embedder 向量化 / vectorstore / retriever 检索
├── agent/                  # 【L4】state 状态 / extract 城市日期抽取 / router 意图路由 / nodes 节点+条件边 / graph 拓扑
├── llm/                    # 【L1】gateway 统一网关（异常翻译 + 测试替身接缝） / prompts 提示词与固定文案
└── worker/                 # 【L1】tasks：run_guarded / schedule_guarded（刻意不含业务逻辑）
alembic/                    # 数据库迁移脚本（env.py 用 async 模板）
frontend/                   # 【L6】Streamlit（M5 落地）：app.py 入口 + api_client.py 后端封装 + views/{login,chat,knowledge}.py 三页
data/                       # 本地数据：weather_agent.db / uploads（按 user_id 分子目录）/ chroma / models
tests/                      # conftest 夹具 + test_auth + test_weather + test_knowledge + test_rag + test_chat + test_frontend + test_rate_limit + test_metrics + test_audit + test_llm_resilience（共 176 例）
scripts/                    # serve 一键启动 / stop 停止（M7）；build_exe 打包 / test_exe 验收（M8）；smoke_m1/m3/m4 冒烟；check_weather_api/check_embedding 自检
launcher.py                 # 【M8】桌面版启动入口（单进程双服务：后端守护线程 + 前端主线程）
weather_agent.spec          # 【M8】PyInstaller 打包配置（数据文件 / 动态导入 / 元数据 / 排除项）
packaging/                  # 【M8】runtime_hook.py 运行时钩子 + app.ico 程序图标（由构建脚本用代码生成）
Dockerfile                  # 【M7】前后端共用一镜像（python:3.12-slim + libgomp1）
docker-compose.yml          # 【M7】backend + frontend 两服务 + ./data 数据卷
.dockerignore               # 【M7】构建时排除 .venv / data / .env
start.sh / stop.sh          # 【M7】Git Bash / Linux / macOS 一键启动/停止（转调 scripts/serve.py / stop.py）
start.bat / stop.bat        # 【M7】Windows cmd 一键启动/停止
```

天气工具当前**没有 HTTP 端点**：`get_weather(city, date)` 是进程内函数，M4 的 Agent 直接调用，不经过 HTTP。`/health/ready` 里的 `weather` 组件只检查「是否配了 provider」，不发起真实外部请求。若日后要加 `/api/v1/weather` 供手工演示，属新增接口，须先走 `CLAUDE.md` §2.4 的决策流程。

**为什么 `worker/tasks.py` 里没有任何业务逻辑**：`CLAUDE.md` §4 规定依赖只能自上而下、L1 不得反向 import L3。若把「解析文档」写进 worker，L1 就要 import L3，依赖方向立刻破掉。所以 worker 只留两个防护函数，真正的业务在 `services/knowledge_service.py`，由 L5 路由层投递（L5 → L3 是允许的方向）。

**`AgentState` 里那个 `db` 字段不是越权**：它只是由 service 放进来的**会话句柄**，检索节点把它**原样转交**给 L3 的检索器，节点自己不构造会话、也不 import `crud`。§4 第 3 条约束的是「谁写 SQL」，而不是「谁能持有会话对象」。

## 里程碑

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| M0 | 技术选型确认 + 项目骨架 | ✅ 完成 |
| M1 | 基础设施：异步数据层、Alembic 迁移、JWT 鉴权、健康检查接入真实库 | ✅ 完成 |
| M2 | 天气工具：和风封装 + 归一化契约 + 缓存/重试 + 测试（字段名已用真实凭据校准；城市搜索待开通权限） | ✅ 完成 |
| M3 | 知识库入库：上传 → 解析 → 切分 → 向量化 → 入库，含去重/隔离/重建索引/启动补偿 | ✅ 完成（真实向量已接入） |
| M4 | 检索问答 + Agent 编排 + SSE 流式 + 多轮会话 | ✅ 完成 |
| M5 | 前端界面：Streamlit 登录页 / 对话页 / 知识库管理页 | ✅ 完成 |
| M6 | 企业级加固：限流、审计日志、指标、错误兜底 | ✅ 完成 |
| M7 | 部署与文档：一键启动脚本 / Dockerfile / 运维手册 | ✅ 完成 |
| M8 | 桌面版打包：单文件 exe（PyInstaller）+ 验收脚本 | ✅ 完成 |

每个阶段开工前会先列出实现方案供选择，确认后再写代码（`CLAUDE.md` §10）。

**M8 的一项决策**：

1. ✅ **Q1 打包形态**：单文件 exe（PyInstaller onefile）。代价已知并接受——产物约 147 MB、启动需先解包（实测冷启动约 10 秒）。实现上用「单进程双服务」避开 onefile 的重复解包，数据落在 exe 同级 `data/`，配置落在同级 `.env`。

**M7 的三项决策**：

1. ✅ **Q1 部署形态**：本地一键脚本 + Docker 都要（脚本日常开发用，Docker 交付/上服务器用）。
2. ✅ **Q2 反向代理/遗留加固**：本次不引入反向代理层、保持轻量（单机部署下限流单进程局限可接受，写成 README「可选扩展」，真要多实例再走 §2.4）。
3. ✅ **Q3 文档范围**：完整运维手册（部署 + 监控 + 备份恢复 + 故障排查 + 升级迁移）。

**M6 的四项决策**：

1. ✅ **Q1 限流**：进程内滑动窗口（`SlidingWindowLimiter`，`deque` 时间戳 + `time.monotonic()`），按 IP 分三档——auth 10/60s、chat 30/60s、default 120/60s，`health`/`metrics` 豁免，超限返回 429 统一信封。代价：单进程有效、多实例各限各的，且可被伪造 `X-Forwarded-For` 绕过（留到 M7 部署形态再评估）。
2. ✅ **Q2 审计**：落库 `audit_logs` 表（`user_id` 可空、无外键、`detail` JSON）。埋点覆盖注册/登录/问答/上传/删除/更新/删会话；`audit_service.record` 失败只记警告，绝不拖垮主流程。
3. ✅ **Q3 指标**：自写 Prometheus 文本（counter/summary，零依赖），`GET /api/v1/metrics` 暴露；HTTP 计数用路由模板避免高基数 label。
4. ✅ **Q4 LLM 弹性**：`ChatOpenAI(max_retries=...)` 只对网络异常/5xx/429 重试（4xx 不重试）；最终失败返回 `LLM_UNAVAILABLE_ANSWER` 友好兜底，技术原因只记日志。

**M5 的四项决策**：

1. ✅ **Q1 页面结构**：Streamlit 多页应用（`st.navigation`：登录 / 对话 / 知识库）。
2. ✅ **Q2 流式渲染**：delta 实时拼接 + `done` 到达后用完整 `answer` 覆盖兜底。
3. ✅ **Q3 城市/日期输入**：混合输入（文本为主 + 可折叠显式控件）。
4. ✅ **Q4 知识库页**：完整集（上传 + 进度轮询 + 覆盖重传 + 删除 + 重建索引 + 失败原因展示）。

**M4 的四项决策与遗留项**：

1. ✅ **Q1B** LangGraph 意图路由 + 分支节点（5 节点 + 3 条件边，不用 checkpointer）。
2. ✅ **Q2A** `langchain-openai`（换成 `BaseChatModel` 接口后，LangGraph 才能逐 token 捕获回调流，这是 SSE 的前提）。
3. ✅ **Q3A** 纯向量 top_k + 距离阈值。**刻意不做 rerank、不做混合检索**——没有评测集的情况下，重排属于「加了但不知道有没有用」，要做须走 §2.4。
4. ✅ **Q4C** 规则优先 + LLM 兜底。词表在 `app/agent/router.py`，当配置看待、需持续补充。
5. ✅ **LLM 凭据**：已提供并完成真实联调（非流式 + 流式均通过，含流式 token 用量）。
6. ⏳ **和风城市搜索仍 403**：非代码问题，需到控制台查凭据的 API 限制 / 应用限制。在它开通之前，「按城市名问天气」会走短路文案而不是真实数据（知识库问答与闲聊不受影响）。

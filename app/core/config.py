"""【L1】环境变量的唯一读取入口（CLAUDE.md §12）。

为什么其他模块禁止直接读 os.environ：一旦配置散落在各处，
「改了 .env 却没生效」这类问题就要满项目排查，而集中在这里只需看一个文件。
"""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 已实现的向量化方式（core/config.py 与 rag/embedder.py 共用的白名单）。
# 放在这里而不是 embedder 里，是为了让「配置写错」在应用启动时就报错，
# 而不是等到第一次上传文档才发现。
EMBEDDING_PROVIDERS = {"placeholder", "fastembed"}


class Settings(BaseSettings):
    """全部配置项。字段名小写，对应 .env 里的大写变量（pydantic 自动做大小写映射）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env 里有尚未使用的变量时不要报错，避免开发期被卡住
    )

    app_env: str = "dev"
    app_name: str = "weather-agent"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    log_level: str = "INFO"

    secret_key: str = "dev-only-secret-key-change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 720

    database_url: str = "sqlite+aiosqlite:///./data/weather_agent.db"
    sql_echo: bool = False
    vector_store_type: str = "chroma"
    chroma_dir: str = "./data/chroma"

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    # 生成温度（M4）。默认偏低：天气播报与知识库问答都应「贴着材料说」，
    # 温度越高模型越爱自由发挥，在 RAG 场景里就是越容易脱离检索片段编内容。
    llm_temperature: float = 0.3
    # 单次 LLM 调用超时。流式响应下这是**整体上限**，不是首个 token 的等待上限。
    llm_timeout_seconds: float = 60.0

    weather_provider: str = "qweather"
    weather_api_key: str = ""
    # 和风天气的 API Host 是「每个项目独立分配」的（形如 xxxxx.qweatherapi.com），
    # 不再是固定的 devapi.qweather.com，必须从控制台复制。
    weather_api_host: str = ""
    weather_timeout_seconds: float = 8.0
    weather_max_retries: int = 2
    weather_cache_ttl_seconds: int = 900

    max_upload_size_mb: int = 20
    chunk_size: int = 800
    chunk_overlap: int = 100
    retrieve_top_k: int = 5

    # --- M4：检索问答 ---
    # 向量检索的余弦距离阈值（余弦距离 = 1 − 余弦相似度，范围 0–2）。
    # 超过阈值的片段视为不相关直接丢弃——宁可不答，也不拿无关材料喂模型。
    # 这是 M4-Q3 的「纯向量 top_k + 距离阈值」方案（Q3A）里的那道阈值。
    retrieve_score_threshold: float = 0.45
    # 组装 prompt 时最多带回几条历史消息（用户与助手各算一条）
    chat_history_max_messages: int = 20
    # 单次提问的最大字符数，超出直接 400（防止超长输入烧 token）
    chat_max_question_chars: int = 2000
    # 检索片段拼进 prompt 的总字符上限，超出按相关度从低到高截断
    chat_max_retrieved_chars: int = 4000

    # --- M3：知识库入库 ---
    upload_dir: str = "./data/uploads"
    chroma_collection: str = "knowledge"
    # 向量化实现（D-M4-1 决策，2026-10-12 定稿）：
    #   fastembed  = 本地 ONNX 真实向量模型（默认，检索有语义）
    #   placeholder = 开发期占位替身（结构真、语义假），非 dev 环境会被下面的校验拦下
    embedding_provider: str = "fastembed"
    # fastembed 的模型名。换模型必须同时改 embedding_dim 并重建索引，否则新老向量不在同一坐标系。
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    # 向量维度。与模型真实输出维度不一致时会在构造向量化器时**直接报错**，
    # 避免「配置写着 256、实际存的是 512」这种查半天的问题。
    embedding_dim: int = 512
    # 检索查询的指令前缀。None = 按模型名自动选用（bge-zh 系列需要，文档不加）；
    # 显式给空字符串 = 强制不加前缀。见 rag/embedder.py 的说明。
    embedding_query_instruction: str | None = None
    embedding_batch_size: int = 32
    # 模型下载源。默认走镜像：huggingface.co 在部分网络环境下不可达，
    # 而模型只在**首次运行**时下载一次，之后全部离线。
    # 若你的环境能直连 HuggingFace，把它设为 https://huggingface.co 即可。
    hf_endpoint: str = "https://hf-mirror.com"
    # 模型缓存目录。放在项目内是为了「离线部署时能把整个目录拷到目标机器」。
    fastembed_cache_path: str = "./data/models/fastembed"
    # 文档详情里回传几个切分片段供前端预览
    document_preview_chunks: int = 5

    # --- M6：限流 / 审计 / 指标 ---
    # 限流总开关（限流是「防刷」的兜底，dev 下也可关闭方便调试）
    rate_limit_enabled: bool = True
    # 默认档：除 auth/chat 外的所有接口。窗口秒数 + 次数上限。
    rate_limit_default_limit: int = 120
    rate_limit_default_window_seconds: int = 60
    # 鉴权档：登录/注册，防暴力破解（最严）
    rate_limit_auth_limit: int = 10
    rate_limit_auth_window_seconds: int = 60
    # 问答档：/chat，防烧 LLM 配额（中等）
    rate_limit_chat_limit: int = 30
    rate_limit_chat_window_seconds: int = 60
    # 指标端点开关（企业内网可关闭）
    metrics_enabled: bool = True
    # LLM 调用弹性（M6-Q4）：对可恢复错误（网络/5xx/429）的重试次数，交给 SDK 的
    # max_retries 实现（其内部已按指数退避，且 4xx 不重试——与 §11 第 2 条一致）。
    llm_max_retries: int = 2

    @model_validator(mode="after")
    def _validate_secret_key(self) -> "Settings":
        """密钥长度与默认值校验。

        HS256 的密钥短于 32 字节会显著降低签名强度（RFC 7518 §3.2），PyJWT 对此
        只会发一条 InsecureKeyLengthWarning。这里直接拦成启动错误——否则这类警告
        极容易被忽略，最后用弱密钥一路带上线。
        """
        if len(self.secret_key.encode("utf-8")) < 32:
            raise ValueError(
                "SECRET_KEY 至少需要 32 字节。生成方式："
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        if self.app_env != "dev" and self.secret_key.startswith("dev-only"):
            raise ValueError("非 dev 环境必须通过环境变量设置独立的 SECRET_KEY")
        return self

    @model_validator(mode="after")
    def _validate_knowledge(self) -> "Settings":
        """知识库相关参数的启动期校验：把「上线才发现」的问题提前到启动。
        """
        if self.embedding_provider not in EMBEDDING_PROVIDERS:
            raise ValueError(
                f"不支持的 EMBEDDING_PROVIDER「{self.embedding_provider}」，"
                f"可选：{'、'.join(sorted(EMBEDDING_PROVIDERS))}"
            )
        # 占位向量必须拦在非 dev 环境外。它是 M3-Q1 的开发期替身，检索结果无意义；
        # 与 SECRET_KEY 的默认值一样，这类"看着能跑"的替身最容易被忘掉带上线。
        if not self.is_dev and self.embedding_provider == "placeholder":
            raise ValueError(
                "非 dev 环境禁止使用占位向量（EMBEDDING_PROVIDER=placeholder）。"
                "它会返回无语义的哈希向量，检索结果完全没有意义。"
                "请改用真实向量模型或 embedding 服务。"
            )
        # 重叠必须小于块大小，否则切分器无法推进（LangChain 会直接抛错），
        # 在这里拦住能给出比第三方报错更直白的提示。
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"CHUNK_OVERLAP({self.chunk_overlap}) 必须小于 CHUNK_SIZE({self.chunk_size})，"
                "否则切分窗口无法向前推进"
            )
        if self.embedding_batch_size < 1:
            raise ValueError("EMBEDDING_BATCH_SIZE 至少为 1")
        return self

    @model_validator(mode="after")
    def _validate_chat(self) -> "Settings":
        """M4 检索问答参数的启动期校验。

        这些值一旦配错，表现都是「结果不对但不报错」——阈值配成负数会把所有
        片段都过滤掉（永远答「知识库中未找到」），历史条数配成 0 会让多轮对话
        退化成单轮。这类问题排查起来最费时间，所以在启动时就拦掉。
        """
        # 余弦距离的理论范围是 [0, 2]（0 = 方向完全相同，2 = 方向完全相反）。
        if not 0 < self.retrieve_score_threshold <= 2:
            raise ValueError(
                f"RETRIEVE_SCORE_THRESHOLD({self.retrieve_score_threshold}) 必须在 (0, 2] 内。"
                "它是余弦距离阈值：越大越宽松（更爱召回），越小越严格（更容易判定「未找到」）。"
            )
        # 温度 0–2 是 OpenAI 兼容接口的通用范围，超出会被上游直接拒。
        if not 0 <= self.llm_temperature <= 2:
            raise ValueError(f"LLM_TEMPERATURE({self.llm_temperature}) 必须在 [0, 2] 内")
        if self.llm_timeout_seconds <= 0:
            raise ValueError("LLM_TIMEOUT_SECONDS 必须为正数")
        if self.retrieve_top_k < 1:
            raise ValueError("RETRIEVE_TOP_K 至少为 1")
        if self.chat_history_max_messages < 1:
            raise ValueError(
                "CHAT_HISTORY_MAX_MESSAGES 至少为 1，否则多轮对话会退化成单轮（且不会报错）"
            )
        if self.chat_max_question_chars < 1:
            raise ValueError("CHAT_MAX_QUESTION_CHARS 至少为 1")
        if self.chat_max_retrieved_chars < 1:
            raise ValueError("CHAT_MAX_RETRIEVED_CHARS 至少为 1")
        return self

    @model_validator(mode="after")
    def _validate_m6(self) -> "Settings":
        """M6 加固参数校验：限流上限至少为 1、窗口为正数。

        limit 配成 0 会把「放行」变成「永远拒绝」（所有请求 429），这类值一旦带上线
        排查起来最费时间，所以在启动时就拦掉。
        """
        for name in (
            "rate_limit_default_limit",
            "rate_limit_auth_limit",
            "rate_limit_chat_limit",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name.upper()} 至少为 1")
        for name in (
            "rate_limit_default_window_seconds",
            "rate_limit_auth_window_seconds",
            "rate_limit_chat_window_seconds",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name.upper()} 必须为正数")
        if self.llm_max_retries < 0:
            raise ValueError("LLM_MAX_RETRIES 不能为负")
        return self

    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"

    @property
    def llm_configured(self) -> bool:
        """LLM 凭据是否齐备。

        刻意**不**做成启动期硬校验：M1–M3 的功能（鉴权、天气、知识库入库）不需要
        LLM，若缺密钥就拒绝启动，等于让一个跟 LLM 无关的改动也跑不起来。改为在
        `/health/ready` 里上报状态、在 `/chat` 被调用时给出明确错误——错要错在
        真正用到它的那一步，而不是把所有功能一起拖下水。
        """
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)


@lru_cache
def get_settings() -> Settings:
    """缓存单例：.env 只在首次调用时解析一次。"""
    return Settings()


settings = get_settings()

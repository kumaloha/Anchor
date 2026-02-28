from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Database
    database_url: str = "sqlite+aiosqlite:///./anchor.db"

    # Anthropic（保留兼容）
    anthropic_api_key: str = ""

    # LLM 统一配置（优先级高于 anthropic_api_key）
    # llm_provider: "anthropic" | "openai"（兼容 Qwen/DeepSeek 等 OpenAI 接口）
    llm_provider: str = "anthropic"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    # 视觉模型（图片描述用）：不填则复用 llm_model；OpenAI 模式下通常需填 qwen-vl-plus 等
    llm_vision_model: str = ""

    # Twitter/X
    twitter_bearer_token: str = ""
    twitter_api_key: str = ""
    twitter_api_secret: str = ""
    twitter_access_token: str = ""
    twitter_access_secret: str = ""
    # 浏览器 Cookie（用于 X Article 全文抓取）
    twitter_auth_token: str = ""
    twitter_ct0: str = ""

    # Weibo
    weibo_app_key: str = ""
    weibo_app_secret: str = ""
    weibo_access_token: str = ""
    # 浏览器登录 Cookie（可选，比访客模式更稳定）
    # 从浏览器 DevTools 复制 Cookie 头，包含 SUB 和 SUBP 字段即可
    weibo_cookie: str = ""

    # Collector
    collector_interval_minutes: int = 60
    collector_max_results_per_query: int = 100

    # RSS — 空则使用内置列表
    rss_feeds: str = ""

    # ── 语音转录（YouTube 音频 → 文字）──────────────────────────────────────
    # 使用 OpenAI Whisper 兼容 API；不填则复用 llm_api_key
    asr_api_key: str = ""
    asr_base_url: str = ""          # 默认使用 OpenAI；可替换为 Groq 等兼容端点
    asr_model: str = "whisper-1"    # Groq 用 "whisper-large-v3-turbo"
    # YouTube 最大转录时长（秒），超出则截断；0 = 不限制；默认 30 分钟
    youtube_max_duration: int = 1800

    # ── Web Search（Layer3 联网核查用）────────────────────────────────────────
    # Tavily Search API Key（免费注册：https://app.tavily.com）
    # 不填则 Layer3 事实核查仅使用 LLM 训练知识（无联网能力）
    tavily_api_key: str = ""

    # ── 宏观数据 API Keys（Layer3 事实核查用）──────────────────────────────────
    # FRED API Key（免费注册：https://fred.stlouisfed.org/docs/api/api_key.html）
    # 不填仍可使用，但请求次数受限（1000次/天 vs 无限制）
    fred_api_key: str = ""
    # BLS API Key（免费注册：https://www.bls.gov/developers/home.htm）
    # 不填使用 v1（25次/天）；注册后 v2 500次/天
    bls_api_key: str = ""

    @property
    def rss_feed_list(self) -> list[str]:
        if self.rss_feeds.strip():
            return [f.strip() for f in self.rss_feeds.split(",") if f.strip()]
        return DEFAULT_RSS_FEEDS


DEFAULT_RSS_FEEDS = [
    # 中文财经
    "https://feedx.net/rss/cailianshe.xml",          # 财联社
    "https://36kr.com/feed",                          # 36氪
    "https://www.cls.cn/rss",                         # 财联社备用
    # 英文财经
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://feeds.reuters.com/reuters/businessNews",
    "https://www.wsj.com/xml/rss/3_7031.xml",        # WSJ Markets
]


settings = Settings()
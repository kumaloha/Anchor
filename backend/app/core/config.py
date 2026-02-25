from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://anchor:anchor@localhost:5432/anchor"

    # Redis / Celery
    REDIS_URL: str = "redis://localhost:6379/0"

    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"

    # Twitter / X
    TWITTER_BEARER_TOKEN: str = ""

    # App
    APP_TITLE: str = "Anchor"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

    # Celery beat
    CRAWL_INTERVAL_HOURS: int = 6

    # Whisper
    WHISPER_MODEL: str = "base"

    # YouTube: how many recent videos to fetch per channel
    YT_MAX_RECENT_VIDEOS: int = 5


settings = Settings()

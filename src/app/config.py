from pydantic_settings import BaseSettings, SettingsConfigDict

YOUTUBE_CHANNELS = [
    "@anthropic-ai",
    "@OpenAI",
    "@matthew_berman"
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://app:app@localhost:5432/ai_news_aggregator"
    webshare_proxy_username: str | None = None
    webshare_proxy_password: str | None = None


settings = Settings()

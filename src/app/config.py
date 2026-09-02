from pydantic_settings import BaseSettings, SettingsConfigDict

YOUTUBE_CHANNELS = [
    "@anthropic-ai",
    "@OpenAI",
    "@matthew_berman"
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://app:app@localhost:5432/ai_news_aggregator"


settings = Settings()

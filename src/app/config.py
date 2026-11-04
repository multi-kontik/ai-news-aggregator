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
    openai_api_key: str | None = None
    resend_api_key: str | None = None
    # Resend's shared test sender works without a verified domain, but can only
    # deliver to the email address that owns the Resend account.
    email_from: str = "AI News Digest <onboarding@resend.dev>"
    email_to: str | None = None


settings = Settings()

from pydantic_settings import BaseSettings, SettingsConfigDict

YOUTUBE_CHANNELS = [
    "@anthropic-ai",
    "@OpenAI",
    "@matthew_berman"
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://app:app@localhost:5432/ai_news_aggregator"
    # YouTube Data API v3 key. Used to list each channel's latest uploads, since
    # YouTube's public RSS feeds stopped working. Without it the feeds are tried.
    youtube_api_key: str | None = None
    webshare_proxy_username: str | None = None
    webshare_proxy_password: str | None = None
    # Optional comma-separated country codes to limit Webshare's rotating IPs to, e.g. "us,ca".
    webshare_proxy_countries: str | None = None
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    resend_api_key: str | None = None
    # Resend's shared test sender works without a verified domain, but can only
    # deliver to the email address that owns the Resend account.
    email_from: str = "AI News Digest <onboarding@resend.dev>"
    email_to: str | None = None


settings = Settings()

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://app:app@localhost:5432/ai_news_aggregator"
    # YouTube Data API v3 key, used to list each channel's latest uploads.
    # Without it, YouTube is skipped (its public RSS feeds return 404).
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

    @field_validator("database_url")
    @classmethod
    def _use_the_psycopg_driver(cls, url: str) -> str:
        """Point plain Postgres URLs at psycopg 3, the driver this app installs.

        Hosts like Render hand out `postgresql://...` (or `postgres://...`),
        which SQLAlchemy would read as a request for psycopg2.
        """
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url


settings = Settings()

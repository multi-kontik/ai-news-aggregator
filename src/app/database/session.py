import logging

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database.models import Base

logger = logging.getLogger(__name__)

engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine)


def create_tables(bind: Engine = engine) -> list[str]:
    """Create any tables that don't exist yet, then apply small schema upgrades, and return all table names.

    `create_all` only creates missing tables; it never changes an existing
    one, so column changes to existing tables are applied by `_upgrade_schema`.
    Both steps are safe to run on every start.
    """
    Base.metadata.create_all(bind)
    _upgrade_schema(bind)
    return list(Base.metadata.tables)


def _upgrade_schema(bind: Engine) -> None:
    """Bring an older database up to date with the current models. Each step checks before it changes anything."""
    inspector = inspect(bind)
    if "digests" not in inspector.get_table_names():
        return

    digest_columns = {column["name"] for column in inspector.get_columns("digests")}
    if "created_at" in digest_columns and "published_at" not in digest_columns:
        # digests.created_at (when the summary was written) became digests.published_at
        # (when the source was published). Rename it, then backfill from each digest's source row.
        logger.info("Upgrading schema: digests.created_at -> digests.published_at")
        with bind.begin() as connection:
            connection.execute(text("ALTER TABLE digests RENAME COLUMN created_at TO published_at"))
            connection.execute(
                text(
                    """
                    UPDATE digests SET published_at = COALESCE(
                        (SELECT published_at FROM youtube_videos WHERE youtube_videos.id = digests.youtube_video_id),
                        (SELECT published_at FROM openai_articles WHERE openai_articles.id = digests.openai_article_id),
                        (SELECT published_at FROM anthropic_articles WHERE anthropic_articles.id = digests.anthropic_article_id),
                        published_at
                    )
                    """
                )
            )

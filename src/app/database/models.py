from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class YouTubeVideo(Base):
    __tablename__ = "youtube_videos"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str]
    video_id: Mapped[str] = mapped_column(unique=True)
    title: Mapped[str]
    url: Mapped[str]
    description: Mapped[str | None]
    transcript: Mapped[str | None]
    transcript_unavailable: Mapped[bool] = mapped_column(default=False)
    published_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))


class OpenAIArticle(Base):
    __tablename__ = "openai_articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(unique=True)
    title: Mapped[str]
    summary: Mapped[str | None]
    category: Mapped[str | None]
    content: Mapped[str | None]
    published_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))


class AnthropicArticle(Base):
    __tablename__ = "anthropic_articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(unique=True)
    title: Mapped[str]
    summary: Mapped[str | None]
    category: Mapped[str | None]
    content: Mapped[str | None]
    published_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))


class Digest(Base):
    """A generated title + summary for one item from exactly one of the three source tables."""

    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(primary_key=True)
    youtube_video_id: Mapped[int | None] = mapped_column(ForeignKey("youtube_videos.id"), unique=True)
    openai_article_id: Mapped[int | None] = mapped_column(ForeignKey("openai_articles.id"), unique=True)
    anthropic_article_id: Mapped[int | None] = mapped_column(ForeignKey("anthropic_articles.id"), unique=True)
    url: Mapped[str]
    title: Mapped[str]
    summary: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))

    @property
    def source(self) -> str:
        """Which source table this digest was generated from, as a display name."""
        if self.youtube_video_id is not None:
            return "YouTube"
        if self.openai_article_id is not None:
            return "OpenAI"
        if self.anthropic_article_id is not None:
            return "Anthropic"
        return "Unknown"

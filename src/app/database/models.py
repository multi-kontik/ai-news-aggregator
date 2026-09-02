from __future__ import annotations

from datetime import datetime, timezone

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

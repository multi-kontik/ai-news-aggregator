from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import AnthropicArticle, Digest, OpenAIArticle, YouTubeVideo

ModelT = TypeVar("ModelT")


class Repository(Generic[ModelT]):
    """Create/list/delete for a single table, keyed on one unique field.

    `unique_field` is what identifies a row that's already been stored
    (YouTube's own video id, or an article's url). Ingestion runs repeatedly
    over overlapping time windows, so without this the same video/article
    would be inserted again on every run.
    """

    def __init__(self, session: Session, model: type[ModelT], unique_field: str):
        self.session = session
        self.model = model
        self.unique_field = unique_field

    def get(self, unique_value: str) -> ModelT | None:
        return self.session.scalar(select(self.model).where(getattr(self.model, self.unique_field) == unique_value))

    def create(self, **fields: object) -> ModelT | None:
        """Create the row if it doesn't exist yet, or skip it (return None) if it does.

        Existing rows are left untouched rather than overwritten.
        """
        if self.get(fields[self.unique_field]) is not None:
            return None
        row = self.model(**fields)
        self.session.add(row)
        return row

    def list(self, since: datetime | None = None) -> list[ModelT]:
        stmt = select(self.model).order_by(self.model.published_at.desc())
        if since is not None:
            stmt = stmt.where(self.model.published_at >= since)
        return list(self.session.scalars(stmt))

    def list_missing(self, field: str) -> list[ModelT]:
        """List rows where `field` is still NULL, e.g. not-yet-enriched transcript/content."""
        stmt = select(self.model).where(getattr(self.model, field).is_(None))
        return list(self.session.scalars(stmt))

    def delete(self, row: ModelT) -> None:
        self.session.delete(row)


class YouTubeVideoRepository(Repository[YouTubeVideo]):
    def __init__(self, session: Session):
        super().__init__(session, YouTubeVideo, unique_field="video_id")

    def list_needing_transcript(self) -> list[YouTubeVideo]:
        """Videos with no transcript yet that haven't already been confirmed unavailable.

        Excludes videos marked `transcript_unavailable`, so a video without
        captions doesn't get re-fetched (and re-fail) on every enrichment run.
        """
        stmt = select(YouTubeVideo).where(
            YouTubeVideo.transcript.is_(None),
            YouTubeVideo.transcript_unavailable.is_(False),
        )
        return list(self.session.scalars(stmt))


class OpenAIArticleRepository(Repository[OpenAIArticle]):
    def __init__(self, session: Session):
        super().__init__(session, OpenAIArticle, unique_field="url")


class AnthropicArticleRepository(Repository[AnthropicArticle]):
    def __init__(self, session: Session):
        super().__init__(session, AnthropicArticle, unique_field="url")


class DigestRepository:
    """Create/list digests, plus find source rows that don't have one yet.

    Doesn't use the generic `Repository`, since a digest's uniqueness is one
    of three possible FK columns rather than a single field.
    """

    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        *,
        url: str,
        title: str,
        summary: str,
        youtube_video_id: int | None = None,
        openai_article_id: int | None = None,
        anthropic_article_id: int | None = None,
    ) -> Digest:
        digest = Digest(
            url=url,
            title=title,
            summary=summary,
            youtube_video_id=youtube_video_id,
            openai_article_id=openai_article_id,
            anthropic_article_id=anthropic_article_id,
        )
        self.session.add(digest)
        return digest

    def list(self) -> list[Digest]:
        return list(self.session.scalars(select(Digest).order_by(Digest.created_at.desc())))

    def list_youtube_videos_without_digest(self) -> list[YouTubeVideo]:
        stmt = (
            select(YouTubeVideo)
            .outerjoin(Digest, Digest.youtube_video_id == YouTubeVideo.id)
            .where(Digest.id.is_(None))
        )
        return list(self.session.scalars(stmt))

    def list_openai_articles_without_digest(self) -> list[OpenAIArticle]:
        stmt = (
            select(OpenAIArticle)
            .outerjoin(Digest, Digest.openai_article_id == OpenAIArticle.id)
            .where(Digest.id.is_(None))
        )
        return list(self.session.scalars(stmt))

    def list_anthropic_articles_without_digest(self) -> list[AnthropicArticle]:
        stmt = (
            select(AnthropicArticle)
            .outerjoin(Digest, Digest.anthropic_article_id == AnthropicArticle.id)
            .where(Digest.id.is_(None))
        )
        return list(self.session.scalars(stmt))

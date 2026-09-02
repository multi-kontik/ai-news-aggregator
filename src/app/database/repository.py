from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import AnthropicArticle, OpenAIArticle, YouTubeVideo

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


class OpenAIArticleRepository(Repository[OpenAIArticle]):
    def __init__(self, session: Session):
        super().__init__(session, OpenAIArticle, unique_field="url")


class AnthropicArticleRepository(Repository[AnthropicArticle]):
    def __init__(self, session: Session):
        super().__init__(session, AnthropicArticle, unique_field="url")

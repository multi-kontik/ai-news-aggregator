from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Digest, SentDigest


class Repository:
    """Insert-if-new for one source table, keyed on the column that identifies a stored item.

    Ingestion runs over overlapping time windows, so the same video or article
    is fetched again and again; `unique_field` (a video id or an article url)
    is how an item that's already stored gets recognised and skipped.
    """

    def __init__(self, session: Session, model: type[Any], unique_field: str):
        self.session = session
        self.model = model
        self.unique_field = unique_field

    def get(self, unique_value: str) -> Any | None:
        return self.session.scalar(select(self.model).where(getattr(self.model, self.unique_field) == unique_value))

    def create(self, **fields: Any) -> Any | None:
        """Add the row if it isn't stored yet and return it, or return None if it is. Existing rows aren't touched."""
        if self.get(fields[self.unique_field]) is not None:
            return None
        row = self.model(**fields)
        self.session.add(row)
        return row


class DigestRepository:
    """Create and list digests, find source rows without one, and record which were emailed."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, *, url: str, title: str, summary: str, published_at: datetime, **source_link: int) -> Digest:
        """Add a digest. `source_link` is the one column linking it to its source row, e.g. `openai_article_id=3`."""
        digest = Digest(url=url, title=title, summary=summary, published_at=published_at, **source_link)
        self.session.add(digest)
        return digest

    def list(self, since: datetime | None = None) -> list[Digest]:
        """Digests by publish date, newest first, optionally only those published since `since`."""
        stmt = select(Digest).order_by(Digest.published_at.desc())
        if since is not None:
            stmt = stmt.where(Digest.published_at >= since)
        return list(self.session.scalars(stmt))

    def list_unsent(self, since: datetime | None = None) -> list[Digest]:
        """Like `list`, but only digests that haven't gone out in an email yet."""
        stmt = (
            select(Digest)
            .outerjoin(SentDigest, SentDigest.digest_id == Digest.id)
            .where(SentDigest.id.is_(None))
            .order_by(Digest.published_at.desc())
        )
        if since is not None:
            stmt = stmt.where(Digest.published_at >= since)
        return list(self.session.scalars(stmt))

    def list_without_digest(self, model: type[Any], link_field: str) -> list[Any]:
        """Rows of a source table that no digest points at yet, through the `Digest` column `link_field`."""
        stmt = (
            select(model)
            .outerjoin(Digest, getattr(Digest, link_field) == model.id)
            .where(Digest.id.is_(None))
            .order_by(model.id)
        )
        return list(self.session.scalars(stmt))

    def mark_sent(self, digest_ids: list[int], message_id: str | None) -> None:
        """Record that these digests went out in the email with `message_id`. Already-sent ids are skipped."""
        already_sent = set(self.session.scalars(select(SentDigest.digest_id).where(SentDigest.digest_id.in_(digest_ids))))
        for digest_id in dict.fromkeys(digest_ids):
            if digest_id not in already_sent:
                self.session.add(SentDigest(digest_id=digest_id, message_id=message_id))

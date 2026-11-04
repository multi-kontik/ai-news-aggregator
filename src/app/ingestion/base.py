"""The `Source` base class: everything the pipeline needs to know about one place news comes from.

The stages in `app.runner` only ever talk to sources through this interface,
so adding a source never means touching the stages. There's one subclass per
*kind* of source (`FeedSource` for RSS feeds, `YouTubeSource` for channels),
and each actual site is a configured instance of one of them, listed in
`app.ingestion.SOURCES`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from types import TracebackType
from typing import Any

from sqlalchemy import ColumnElement


class Source(ABC):
    """One source of news, stored in its own table and linked to digests through its own column.

    Subclasses set these, usually in `__init__`:

    - `name`: display name, matching `Digest.source` ("OpenAI", "YouTube", ...)
    - `model`: the SQLAlchemy model the source's items are stored in
    - `unique_field`: the column that identifies an already-stored item
    - `digest_link`: the `Digest` column that points at this source's table
    """

    name: str
    model: type[Any]
    unique_field: str
    digest_link: str

    @property
    def key(self) -> str:
        """Short lowercase label used in logs and error sources, e.g. "openai"."""
        return self.name.lower()

    def __enter__(self) -> Source:
        """Open any clients the source needs. Stages use every source as a context manager."""
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close whatever `__enter__` opened."""

    @abstractmethod
    def units(self) -> list[str]:
        """The parts that are fetched independently (feed URLs, channels). One failing doesn't stop the others."""

    @abstractmethod
    def fetch(self, unit: str, since: datetime) -> list[dict[str, Any]]:
        """Items in `unit` published since `since`, as column values for `model`."""

    @abstractmethod
    def needs_enrichment(self) -> ColumnElement[bool]:
        """SQL filter for stored rows that still need `enrich`."""

    @abstractmethod
    def enrich(self, row: Any) -> None:
        """Fill in a row's full text (transcript, article content) in place."""

    @abstractmethod
    def digest_content(self, row: Any) -> tuple[str, str]:
        """The richest text available to summarize a row, and a short label for what it is."""

"""RSS/Atom feed sources: any site that publishes posts through one or more feeds."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import cache
from typing import TYPE_CHECKING, Any

import feedparser
from pydantic import BaseModel
from sqlalchemy import ColumnElement

from app.ingestion.base import Source

if TYPE_CHECKING:
    from docling.document_converter import DocumentConverter

logger = logging.getLogger(__name__)


class Article(BaseModel):
    title: str
    url: str
    summary: str
    published_at: datetime
    category: str | None = None


def fetch_articles(feed_url: str, since: datetime | None = None) -> list[Article]:
    """Fetch articles from an RSS/Atom feed, optionally only those published since `since`.

    Raises on an HTTP error, or on a feed that's malformed *and* empty. A
    "bozo" feed that still has entries is accepted: some servers (e.g.
    raw.githubusercontent.com) serve valid XML with a non-XML content type,
    which feedparser flags even though everything parsed fine. Entries
    without a title, link or publish date are skipped rather than failing the feed.
    """
    feed = feedparser.parse(feed_url)
    status = getattr(feed, "status", None)
    if status is not None and status != 200:
        raise RuntimeError(f"Fetching feed {feed_url!r} failed with HTTP {status}")
    if getattr(feed, "bozo", False) and not feed.entries:
        raise RuntimeError(f"Feed {feed_url!r} could not be parsed: {feed.bozo_exception}")

    articles = []
    for entry in feed.entries:
        title, url, published_at = getattr(entry, "title", None), getattr(entry, "link", None), _published_at(entry)
        if not (title and url and published_at):
            logger.warning("Skipping an entry in %s with no title, link or publish date", feed_url)
            continue
        if since is not None and published_at < since:
            continue
        tags = getattr(entry, "tags", None)
        articles.append(
            Article(
                title=title,
                url=url,
                summary=getattr(entry, "summary", None) or "",
                published_at=published_at,
                category=tags[0].term if tags else None,
            )
        )
    return articles


def _published_at(entry: Any) -> datetime | None:
    parsed = getattr(entry, "published_parsed", None)
    return datetime(*parsed[:6], tzinfo=timezone.utc) if parsed else None


@cache
def _get_converter() -> DocumentConverter:
    """Build the docling converter on first use: importing docling is slow, and most commands never need it."""
    from docling.document_converter import DocumentConverter

    return DocumentConverter()


def fetch_article_markdown(url: str) -> str:
    """Fetch a URL's content and convert it to markdown."""
    return _get_converter().convert(url).document.export_to_markdown()


class FeedSource(Source):
    """A site published through RSS/Atom feeds. Each feed URL is fetched (and can fail) on its own.

    Articles are stored with the feed's title, link, date, summary and
    category, then enriched with the full page converted to markdown.
    """

    unique_field = "url"

    def __init__(self, *, name: str, model: type[Any], digest_link: str, feed_urls: list[str]) -> None:
        self.name = name
        self.model = model
        self.digest_link = digest_link
        self.feed_urls = feed_urls

    def units(self) -> list[str]:
        return self.feed_urls

    def fetch(self, unit: str, since: datetime) -> list[dict[str, Any]]:
        return [article.model_dump() for article in fetch_articles(unit, since=since)]

    def needs_enrichment(self) -> ColumnElement[bool]:
        return self.model.content.is_(None)

    def enrich(self, row: Any) -> None:
        row.content = fetch_article_markdown(row.url)

    def digest_content(self, row: Any) -> tuple[str, str]:
        if row.content:
            return row.content, "content"
        if row.summary:
            return row.summary, "feed summary"
        return row.title, "title only"

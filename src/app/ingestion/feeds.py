from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import cache
from typing import TYPE_CHECKING, Any

import feedparser
from pydantic import BaseModel

if TYPE_CHECKING:
    from docling.document_converter import DocumentConverter

logger = logging.getLogger(__name__)


class Article(BaseModel):
    title: str
    url: str
    summary: str
    published_at: datetime
    category: str | None = None


def parse_feed(feed_url: str) -> list[Any]:
    """Fetch and parse an RSS/Atom feed, returning its entries.

    Raises on an HTTP error, or on a feed that's malformed *and* empty. A
    "bozo" feed that still has entries is accepted: some servers (e.g.
    raw.githubusercontent.com) serve valid XML with a non-XML content type,
    which feedparser flags even though everything parsed fine.
    """
    feed = feedparser.parse(feed_url)

    status = getattr(feed, "status", None)
    if status is not None and status != 200:
        raise RuntimeError(f"Fetching feed {feed_url!r} failed with HTTP {status}")
    return _entries(feed, feed_url)


def parse_feed_content(content: bytes, feed_url: str) -> list[Any]:
    """Parse feed XML that was already downloaded (e.g. through a proxy). `feed_url` is only used in errors."""
    return _entries(feedparser.parse(content), feed_url)


def _entries(feed: Any, feed_url: str) -> list[Any]:
    if getattr(feed, "bozo", False) and not feed.entries:
        raise RuntimeError(f"Feed {feed_url!r} could not be parsed: {feed.bozo_exception}")
    return list(feed.entries)


def entry_published_at(entry: Any) -> datetime | None:
    """An entry's publish time in UTC, or None if the feed didn't give one."""
    parsed = getattr(entry, "published_parsed", None)
    return datetime(*parsed[:6], tzinfo=timezone.utc) if parsed else None


def fetch_articles(feed_url: str, since: datetime | None = None) -> list[Article]:
    """Fetch articles from an RSS feed, optionally filtered to those published after `since`.

    Entries without a title, link or publish date are skipped (and logged)
    rather than failing the whole feed.
    """
    articles = []
    for entry in parse_feed(feed_url):
        title, url, published_at = getattr(entry, "title", None), getattr(entry, "link", None), entry_published_at(entry)
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


@cache
def _get_converter() -> DocumentConverter:
    """Build the docling converter on first use: importing docling is slow, and most commands never need it."""
    from docling.document_converter import DocumentConverter

    return DocumentConverter()


def fetch_article_markdown(url: str) -> str:
    """Fetch a URL's content and convert it to markdown."""
    return _get_converter().convert(url).document.export_to_markdown()

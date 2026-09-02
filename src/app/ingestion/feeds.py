from __future__ import annotations

from datetime import datetime, timezone

import feedparser
from docling.document_converter import DocumentConverter
from pydantic import BaseModel

_converter = DocumentConverter()


class Article(BaseModel):
    title: str
    url: str
    summary: str
    published_at: datetime
    category: str | None = None


def fetch_articles(feed_url: str, since: datetime | None = None) -> list[Article]:
    """Fetch articles from an RSS feed, optionally filtered to those published after `since`."""
    feed = feedparser.parse(feed_url)

    status = getattr(feed, "status", None)
    if status is not None and status != 200:
        raise RuntimeError(f"Fetching feed {feed_url!r} failed with HTTP {status}")
    if getattr(feed, "bozo", False) and not feed.entries:
        raise RuntimeError(f"Feed {feed_url!r} could not be parsed: {feed.bozo_exception}")

    articles = []
    for entry in feed.entries:
        published_at = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        if since is not None and published_at < since:
            continue
        articles.append(
            Article(
                title=entry.title,
                url=entry.link,
                summary=entry.summary,
                published_at=published_at,
                category=entry.tags[0].term if getattr(entry, "tags", None) else None,
            )
        )
    return articles


def fetch_article_markdown(url: str) -> str:
    """Fetch a URL's content and convert it to markdown."""
    result = _converter.convert(url)
    return result.document.export_to_markdown()

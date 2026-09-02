from __future__ import annotations

from datetime import datetime

from app.ingestion.feeds import Article, fetch_article_markdown, fetch_articles

FEED_URL = "https://openai.com/news/rss.xml"

__all__ = ["FEED_URL", "fetch_article_markdown", "fetch_latest_articles"]


def fetch_latest_articles(since: datetime | None = None) -> list[Article]:
    """Fetch articles from OpenAI's news RSS feed, optionally filtered to those published after `since`."""
    return fetch_articles(FEED_URL, since=since)

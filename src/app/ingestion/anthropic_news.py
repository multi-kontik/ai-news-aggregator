from __future__ import annotations

from datetime import datetime

from app.ingestion.feeds import Article, fetch_article_markdown, fetch_articles

FEED_URLS = [
    "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_engineering.xml",
    "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_news.xml",
    "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_research.xml",
]

__all__ = ["FEED_URLS", "fetch_article_markdown", "fetch_latest_articles"]


def fetch_latest_articles(since: datetime | None = None) -> list[Article]:
    """Fetch and combine articles from Anthropic's engineering, news, and research feeds."""
    articles = [article for url in FEED_URLS for article in fetch_articles(url, since=since)]
    articles.sort(key=lambda article: article.published_at, reverse=True)
    return articles

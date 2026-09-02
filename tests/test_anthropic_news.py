from __future__ import annotations

from datetime import datetime, timezone

from app.ingestion.anthropic_news import FEED_URLS, fetch_latest_articles
from app.ingestion.feeds import Article


def _article(title: str, published_at: datetime) -> Article:
    return Article(title=title, url=f"https://anthropic.com/{title}", summary="", published_at=published_at)


def test_combines_and_sorts_articles_from_every_feed(mocker):
    per_feed = {
        FEED_URLS[0]: [_article("engineering post", datetime(2026, 1, 1, tzinfo=timezone.utc))],
        FEED_URLS[1]: [_article("news post", datetime(2026, 1, 3, tzinfo=timezone.utc))],
        FEED_URLS[2]: [_article("research post", datetime(2026, 1, 2, tzinfo=timezone.utc))],
    }
    mock_fetch_articles = mocker.patch(
        "app.ingestion.anthropic_news.fetch_articles",
        side_effect=lambda url, since=None: per_feed[url],
    )

    articles = fetch_latest_articles()

    assert mock_fetch_articles.call_count == len(FEED_URLS)
    assert [article.title for article in articles] == ["news post", "research post", "engineering post"]


def test_passes_since_through_to_every_feed(mocker):
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    mock_fetch_articles = mocker.patch("app.ingestion.anthropic_news.fetch_articles", return_value=[])

    fetch_latest_articles(since=since)

    for call in mock_fetch_articles.call_args_list:
        assert call.kwargs["since"] == since

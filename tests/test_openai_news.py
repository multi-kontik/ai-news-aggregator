from __future__ import annotations

from app.ingestion.openai_news import FEED_URL, fetch_latest_articles


def test_fetch_latest_articles_delegates_to_the_feeds_helper(mocker):
    since = object()
    mock_fetch_articles = mocker.patch("app.ingestion.openai_news.fetch_articles", return_value="the articles")

    result = fetch_latest_articles(since=since)

    mock_fetch_articles.assert_called_once_with(FEED_URL, since=since)
    assert result == "the articles"

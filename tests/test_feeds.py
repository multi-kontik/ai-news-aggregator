from __future__ import annotations

import time
import types
from datetime import datetime, timezone

import pytest

from app.ingestion.feeds import Article, fetch_article_markdown, fetch_articles

FEED_URL = "https://example.com/feed.xml"


def _feed_entry(
    title: str,
    published: tuple[int, int, int, int, int, int],
    summary: str = "",
    url: str = "https://example.com/article",
    tags: list[str] | None = None,
):
    return types.SimpleNamespace(
        title=title,
        link=url,
        summary=summary,
        published_parsed=time.struct_time(published + (0, 0, 0)),
        tags=[types.SimpleNamespace(term=tag) for tag in tags] if tags else None,
    )


def test_returns_all_entries_as_articles(mocker):
    feed = types.SimpleNamespace(
        entries=[
            _feed_entry("First article", (2026, 1, 1, 12, 0, 0), summary="desc one", tags=["Product"]),
            _feed_entry("Second article", (2026, 1, 2, 12, 0, 0), summary="desc two"),
        ]
    )
    mocker.patch("app.ingestion.feeds.feedparser.parse", return_value=feed)

    articles = fetch_articles(FEED_URL)

    assert articles == [
        Article(
            title="First article",
            url="https://example.com/article",
            summary="desc one",
            published_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            category="Product",
        ),
        Article(
            title="Second article",
            url="https://example.com/article",
            summary="desc two",
            published_at=datetime(2026, 1, 2, 12, 0, 0, tzinfo=timezone.utc),
            category=None,
        ),
    ]


def test_filters_out_entries_published_before_since(mocker):
    feed = types.SimpleNamespace(
        entries=[
            _feed_entry("Old article", (2026, 1, 1, 12, 0, 0)),
            _feed_entry("New article", (2026, 1, 3, 12, 0, 0)),
        ]
    )
    mocker.patch("app.ingestion.feeds.feedparser.parse", return_value=feed)

    articles = fetch_articles(FEED_URL, since=datetime(2026, 1, 2, tzinfo=timezone.utc))

    assert [article.title for article in articles] == ["New article"]


def test_raises_when_the_feed_fetch_fails(mocker):
    feed = types.SimpleNamespace(
        entries=[],
        status=404,
        bozo=True,
        bozo_exception=Exception("not well-formed"),
    )
    mocker.patch("app.ingestion.feeds.feedparser.parse", return_value=feed)

    with pytest.raises(RuntimeError, match="404"):
        fetch_articles(FEED_URL)


def test_tolerates_a_bozo_feed_that_still_has_entries(mocker):
    """Some servers (e.g. raw.githubusercontent.com) send XML with a non-XML content
    type, which feedparser flags as bozo even though the feed parsed fine."""
    feed = types.SimpleNamespace(
        entries=[_feed_entry("Real article", (2026, 1, 1, 12, 0, 0))],
        status=200,
        bozo=True,
        bozo_exception=Exception("text/plain; charset=utf-8 is not an XML media type"),
    )
    mocker.patch("app.ingestion.feeds.feedparser.parse", return_value=feed)

    articles = fetch_articles(FEED_URL)

    assert [article.title for article in articles] == ["Real article"]


def test_fetch_article_markdown_exports_the_converted_document(mocker):
    result = types.SimpleNamespace(document=types.SimpleNamespace(export_to_markdown=lambda: "# Title\n\nBody"))
    mock_convert = mocker.patch("app.ingestion.feeds._converter.convert", return_value=result)

    markdown = fetch_article_markdown("https://example.com/article")

    mock_convert.assert_called_once_with("https://example.com/article")
    assert markdown == "# Title\n\nBody"

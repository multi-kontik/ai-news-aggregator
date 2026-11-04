from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.database.models import Digest, OpenAIArticle
from app.ingestion import SOURCES
from app.ingestion.feeds import Article, FeedSource


def test_every_source_maps_onto_the_database_models():
    """A source's table, unique column and Digest link must exist, and its name must be what Digest.source reports."""
    assert len({source.name for source in SOURCES}) == len(SOURCES)
    for source in SOURCES:
        assert hasattr(source.model, source.unique_field)
        assert source.digest_link in Digest.__table__.columns
        digest = Digest(**{source.digest_link: 1})
        assert digest.source == source.name


@pytest.fixture
def source():
    return FeedSource(name="OpenAI", model=OpenAIArticle, digest_link="openai_article_id", feed_urls=["feed-1", "feed-2"])


def test_feed_source_fetches_each_feed_url_as_its_own_unit(source, mocker):
    article = Article(title="T", url="https://example.com/a", summary="s", published_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    fetch = mocker.patch("app.ingestion.feeds.fetch_articles", return_value=[article])
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)

    assert source.units() == ["feed-1", "feed-2"]
    assert source.fetch("feed-2", since) == [article.model_dump()]
    fetch.assert_called_once_with("feed-2", since=since)


def test_feed_source_enriches_with_the_page_as_markdown(source, mocker):
    mocker.patch("app.ingestion.feeds.fetch_article_markdown", return_value="# Page")
    row = OpenAIArticle(url="https://example.com/a", content=None)

    source.enrich(row)

    assert row.content == "# Page"


@pytest.mark.parametrize(
    "content, summary, expected",
    [("full", "teaser", ("full", "content")), (None, "teaser", ("teaser", "feed summary")), (None, "", ("Title", "title only"))],
)
def test_feed_source_summarizes_the_richest_text_available(source, content, summary, expected):
    assert source.digest_content(OpenAIArticle(title="Title", content=content, summary=summary)) == expected

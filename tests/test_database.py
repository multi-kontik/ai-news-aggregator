from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database.models import Base
from app.database.repository import AnthropicArticleRepository, OpenAIArticleRepository, YouTubeVideoRepository


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_youtube_video_repository_creates_a_new_video(session):
    repo = YouTubeVideoRepository(session)

    video = repo.create(
        channel="@anthropic-ai",
        video_id="abc123",
        title="Example video",
        url="https://youtube.com/watch?v=abc123",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        description="a description",
        transcript=None,
    )
    session.flush()

    assert video is not None
    assert video.id is not None


def test_youtube_video_repository_skips_and_does_not_override_an_existing_video(session):
    repo = YouTubeVideoRepository(session)
    repo.create(
        channel="@anthropic-ai",
        video_id="abc123",
        title="Old title",
        url="https://youtube.com/watch?v=abc123",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        description=None,
        transcript=None,
    )
    session.flush()

    result = repo.create(
        channel="@anthropic-ai",
        video_id="abc123",
        title="New title",
        url="https://youtube.com/watch?v=abc123",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        description=None,
        transcript=None,
    )
    session.flush()

    assert result is None
    stored = repo.list()
    assert len(stored) == 1
    assert stored[0].title == "Old title"


def test_youtube_video_repository_lists_missing_transcript(session):
    repo = YouTubeVideoRepository(session)
    repo.create(
        channel="c", video_id="has-transcript", title="Has", url="u1",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc), description=None, transcript="already fetched",
    )
    repo.create(
        channel="c", video_id="no-transcript", title="Missing", url="u2",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc), description=None, transcript=None,
    )

    missing = repo.list_missing("transcript")

    assert [video.video_id for video in missing] == ["no-transcript"]


def test_youtube_video_repository_lists_since(session):
    repo = YouTubeVideoRepository(session)
    repo.create(
        channel="c", video_id="old", title="Old", url="u1",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc), description=None, transcript=None,
    )
    repo.create(
        channel="c", video_id="new", title="New", url="u2",
        published_at=datetime(2026, 1, 3, tzinfo=timezone.utc), description=None, transcript=None,
    )

    videos = repo.list(since=datetime(2026, 1, 2, tzinfo=timezone.utc))

    assert [video.video_id for video in videos] == ["new"]


def test_youtube_video_repository_delete(session):
    repo = YouTubeVideoRepository(session)
    video = repo.create(
        channel="c", video_id="v1", title="t", url="u",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc), description=None, transcript=None,
    )
    session.flush()

    repo.delete(video)
    session.flush()

    assert repo.list() == []


def test_openai_article_repository_creates_and_skips_duplicates_by_url(session):
    repo = OpenAIArticleRepository(session)

    repo.create(
        url="https://openai.com/index/example",
        title="Old title",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        summary="a summary",
        category="Product",
    )
    session.flush()

    result = repo.create(
        url="https://openai.com/index/example",
        title="New title",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    session.flush()

    assert result is None
    stored = repo.list()
    assert len(stored) == 1
    assert stored[0].title == "Old title"


def test_openai_article_repository_lists_missing_content(session):
    repo = OpenAIArticleRepository(session)
    repo.create(
        url="https://openai.com/index/has-content",
        title="Has content",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        content="already scraped",
    )
    repo.create(
        url="https://openai.com/index/no-content",
        title="Missing content",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    missing = repo.list_missing("content")

    assert [article.url for article in missing] == ["https://openai.com/index/no-content"]


def test_anthropic_article_repository_creates_and_skips_duplicates_by_url(session):
    repo = AnthropicArticleRepository(session)

    repo.create(
        url="https://www.anthropic.com/news/example",
        title="Old title",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        summary="a summary",
        category="Announcements",
    )
    session.flush()

    result = repo.create(
        url="https://www.anthropic.com/news/example",
        title="New title",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    session.flush()

    assert result is None
    stored = repo.list()
    assert len(stored) == 1
    assert stored[0].title == "Old title"

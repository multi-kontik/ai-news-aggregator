from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database.models import Base
from app.database.repository import (
    AnthropicArticleRepository,
    DigestRepository,
    OpenAIArticleRepository,
    YouTubeVideoRepository,
)


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


def test_youtube_video_repository_list_needing_transcript_excludes_confirmed_unavailable(session):
    repo = YouTubeVideoRepository(session)
    repo.create(
        channel="c", video_id="untried", title="Untried", url="u1",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc), description=None, transcript=None,
    )
    repo.create(
        channel="c", video_id="confirmed-unavailable", title="Confirmed unavailable", url="u2",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc), description=None, transcript=None,
        transcript_unavailable=True,
    )

    needing = repo.list_needing_transcript()

    assert [video.video_id for video in needing] == ["untried"]


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


def test_digest_repository_creates_a_digest_linked_to_a_youtube_video(session):
    video = YouTubeVideoRepository(session).create(
        channel="c", video_id="vid1", title="Video", url="https://youtube.com/watch?v=vid1",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc), description=None, transcript="the transcript",
    )
    session.flush()

    digest = DigestRepository(session).create(
        youtube_video_id=video.id,
        url=video.url,
        title="Digest title",
        summary="Digest summary.",
    )
    session.flush()

    assert digest.id is not None
    assert digest.youtube_video_id == video.id
    assert digest.openai_article_id is None
    assert digest.anthropic_article_id is None


def test_digest_repository_list_youtube_videos_without_digest(session):
    videos = YouTubeVideoRepository(session)
    digests = DigestRepository(session)

    digested = videos.create(
        channel="c", video_id="has-digest", title="Has digest", url="u1",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc), description=None, transcript=None,
    )
    videos.create(
        channel="c", video_id="no-digest", title="No digest", url="u2",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc), description=None, transcript=None,
    )
    session.flush()
    digests.create(youtube_video_id=digested.id, url=digested.url, title="t", summary="s")

    pending = digests.list_youtube_videos_without_digest()

    assert [video.video_id for video in pending] == ["no-digest"]


def test_digest_repository_list_openai_articles_without_digest(session):
    articles = OpenAIArticleRepository(session)
    digests = DigestRepository(session)

    digested = articles.create(
        url="https://openai.com/index/has-digest", title="Has digest",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    articles.create(
        url="https://openai.com/index/no-digest", title="No digest",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    session.flush()
    digests.create(openai_article_id=digested.id, url=digested.url, title="t", summary="s")

    pending = digests.list_openai_articles_without_digest()

    assert [article.url for article in pending] == ["https://openai.com/index/no-digest"]


def test_digest_repository_list_anthropic_articles_without_digest(session):
    articles = AnthropicArticleRepository(session)
    digests = DigestRepository(session)

    digested = articles.create(
        url="https://www.anthropic.com/news/has-digest", title="Has digest",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    articles.create(
        url="https://www.anthropic.com/news/no-digest", title="No digest",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    session.flush()
    digests.create(anthropic_article_id=digested.id, url=digested.url, title="t", summary="s")

    pending = digests.list_anthropic_articles_without_digest()

    assert [article.url for article in pending] == ["https://www.anthropic.com/news/no-digest"]


def _digest(session, url: str) -> int:
    digest = DigestRepository(session).create(url=url, title="t", summary="s")
    session.flush()
    return digest.id


def test_list_unsent_excludes_digests_that_were_emailed(session):
    digests = DigestRepository(session)
    sent_id, unsent_id = _digest(session, "https://example.com/sent"), _digest(session, "https://example.com/unsent")

    digests.mark_sent([sent_id], message_id="msg_1")
    session.flush()

    assert [d.id for d in digests.list_unsent()] == [unsent_id]


def test_mark_sent_skips_digests_already_marked(session):
    digests = DigestRepository(session)
    digest_id = _digest(session, "https://example.com/a")

    digests.mark_sent([digest_id, digest_id], message_id="msg_1")
    session.flush()
    digests.mark_sent([digest_id], message_id="msg_2")
    session.flush()

    from app.database.models import SentDigest

    assert [(row.digest_id, row.message_id) for row in session.query(SentDigest)] == [(digest_id, "msg_1")]

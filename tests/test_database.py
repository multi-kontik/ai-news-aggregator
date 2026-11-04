from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database.models import AnthropicArticle, Base, Digest, OpenAIArticle, SentDigest, YouTubeVideo
from app.database.repository import DigestRepository, Repository

PUBLISHED = datetime(2026, 1, 1)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _article(model=OpenAIArticle, slug="a", **fields):
    return model(url=f"https://example.com/{slug}", title=slug, published_at=PUBLISHED, **fields)


def _digest(session, url="https://example.com/d", **source_link) -> Digest:
    digest = DigestRepository(session).create(url=url, title="t", summary="s", published_at=PUBLISHED, **source_link)
    session.flush()
    return digest


def test_repository_creates_a_row_and_skips_one_that_is_already_stored(session):
    repo = Repository(session, YouTubeVideo, "video_id")
    fields = dict(channel="@c", video_id="vid1", title="Old title", url="u", published_at=PUBLISHED)

    assert repo.create(**fields) is not None
    session.flush()
    assert repo.create(**{**fields, "title": "New title"}) is None
    session.flush()

    assert [v.title for v in session.scalars(select(YouTubeVideo))] == ["Old title"]


def test_digest_is_linked_to_its_source_row(session):
    article = _article()
    session.add(article)
    session.flush()

    digest = _digest(session, openai_article_id=article.id)

    assert digest.source == "OpenAI"
    assert (digest.youtube_video_id, digest.anthropic_article_id) == (None, None)


@pytest.mark.parametrize(
    "model, link_field",
    [(OpenAIArticle, "openai_article_id"), (AnthropicArticle, "anthropic_article_id")],
)
def test_list_without_digest_returns_only_rows_no_digest_points_at(session, model, link_field):
    digested, pending = _article(model, "digested"), _article(model, "pending")
    session.add_all([digested, pending])
    session.flush()
    _digest(session, **{link_field: digested.id})

    assert [row.url for row in DigestRepository(session).list_without_digest(model, link_field)] == [pending.url]


def test_list_without_digest_works_for_videos(session):
    video = YouTubeVideo(channel="@c", video_id="vid1", title="t", url="u", published_at=PUBLISHED)
    session.add(video)
    session.flush()

    assert DigestRepository(session).list_without_digest(YouTubeVideo, "youtube_video_id") == [video]


def test_list_filters_by_publish_date_newest_first(session):
    digests = DigestRepository(session)
    digests.create(url="old", title="t", summary="s", published_at=datetime(2026, 1, 1))
    digests.create(url="new", title="t", summary="s", published_at=datetime(2026, 1, 3))
    session.flush()

    assert [d.url for d in digests.list()] == ["new", "old"]
    assert [d.url for d in digests.list(since=datetime(2026, 1, 2))] == ["new"]


def test_list_unsent_excludes_digests_that_were_emailed(session):
    digests = DigestRepository(session)
    sent, unsent = _digest(session, url="sent"), _digest(session, url="unsent")

    digests.mark_sent([sent.id], message_id="msg_1")
    session.flush()

    assert digests.list_unsent() == [unsent]


def test_mark_sent_skips_digests_already_marked(session):
    digests = DigestRepository(session)
    digest = _digest(session)

    digests.mark_sent([digest.id, digest.id], message_id="msg_1")
    session.flush()
    digests.mark_sent([digest.id], message_id="msg_2")
    session.flush()

    assert [(row.digest_id, row.message_id) for row in session.scalars(select(SentDigest))] == [(digest.id, "msg_1")]

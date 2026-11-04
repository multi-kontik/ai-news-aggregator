from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent.editor import RankedDigest
from app.agent.email_agent import EmailIntroduction
from app.agent.summarizer import DigestSummary
from app.database.models import Base
from app.database.repository import (
    AnthropicArticleRepository,
    DigestRepository,
    OpenAIArticleRepository,
    YouTubeVideoRepository,
)
from app.ingestion.feeds import Article
from app.ingestion.youtube import ChannelVideo
from app.profile import UserProfile
from app.runner import process_digest, run_enrichment, run_email_digest, run_ingestion, run_ranking


@pytest.fixture(autouse=True)
def test_session_local(mocker):
    """Back the runner's DB writes with a real in-memory SQLite session, so the
    persistence code path is actually exercised instead of mocked away."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine)
    mocker.patch("app.runner.SessionLocal", session_local)
    return session_local


def _video(video_id: str, title: str | None = None) -> ChannelVideo:
    return ChannelVideo(
        video_id=video_id,
        channel_id="UC" + "x" * 22,
        title=title or video_id,
        url=f"https://youtube.com/watch?v={video_id}",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        description="",
    )


def _article(slug: str, title: str | None = None) -> Article:
    return Article(
        title=title or slug,
        url=f"https://example.com/{slug}",
        summary="",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_combines_videos_and_articles_from_every_source(mocker):
    mocker.patch("app.runner.YOUTUBE_CHANNELS", ["@channel-one", "@channel-two"])

    client = mocker.MagicMock()
    client.resolve_channel_id.side_effect = lambda channel: f"resolved-{channel}"
    client.fetch_latest_videos.side_effect = lambda channel_id, since: [_video(channel_id)]
    mocker.patch("app.runner.YouTubeClient", return_value=mocker.MagicMock(__enter__=lambda self: client, __exit__=lambda *a: None))

    mocker.patch("app.runner.openai_news.fetch_latest_articles", return_value=[_article("openai post")])
    mocker.patch("app.runner.anthropic_news.fetch_latest_articles", return_value=[_article("anthropic post")])

    result = run_ingestion(hours=24)

    assert [video.video_id for video in result.videos] == ["resolved-@channel-one", "resolved-@channel-two"]
    assert [article.title for article in result.articles] == ["openai post", "anthropic post"]


def test_passes_the_since_cutoff_derived_from_hours(mocker):
    client = mocker.MagicMock()
    client.resolve_channel_id.return_value = "UC" + "x" * 22
    client.fetch_latest_videos.return_value = []
    mocker.patch("app.runner.YouTubeClient", return_value=mocker.MagicMock(__enter__=lambda self: client, __exit__=lambda *a: None))

    mock_openai = mocker.patch("app.runner.openai_news.fetch_latest_articles", return_value=[])
    mock_anthropic = mocker.patch("app.runner.anthropic_news.fetch_latest_articles", return_value=[])

    run_ingestion(hours=1)

    openai_since = mock_openai.call_args.kwargs["since"]
    anthropic_since = mock_anthropic.call_args.kwargs["since"]
    video_since = client.fetch_latest_videos.call_args.kwargs["since"]
    assert openai_since == anthropic_since == video_since


def test_a_failing_youtube_channel_does_not_block_the_others(mocker):
    mocker.patch("app.runner.YOUTUBE_CHANNELS", ["@broken-channel", "@working-channel"])

    client = mocker.MagicMock()
    client.resolve_channel_id.side_effect = lambda channel: channel

    def fetch_latest_videos(channel_id, since):
        if channel_id == "@broken-channel":
            raise RuntimeError("feed is down")
        return [_video(channel_id)]

    client.fetch_latest_videos.side_effect = fetch_latest_videos
    mocker.patch("app.runner.YouTubeClient", return_value=mocker.MagicMock(__enter__=lambda self: client, __exit__=lambda *a: None))

    mocker.patch("app.runner.openai_news.fetch_latest_articles", return_value=[_article("openai post")])
    mocker.patch("app.runner.anthropic_news.fetch_latest_articles", return_value=[_article("anthropic post")])

    result = run_ingestion(hours=24)

    assert [video.video_id for video in result.videos] == ["@working-channel"]
    assert [article.title for article in result.articles] == ["openai post", "anthropic post"]
    assert [(error.source, error.error) for error in result.errors] == [("youtube:@broken-channel", "feed is down")]


def test_a_failing_article_source_does_not_block_the_others(mocker):
    mocker.patch("app.runner.YOUTUBE_CHANNELS", ["@channel-one"])

    client = mocker.MagicMock()
    client.resolve_channel_id.return_value = "UC" + "x" * 22
    client.fetch_latest_videos.return_value = [_video("vid1")]
    mocker.patch("app.runner.YouTubeClient", return_value=mocker.MagicMock(__enter__=lambda self: client, __exit__=lambda *a: None))

    mocker.patch("app.runner.openai_news.fetch_latest_articles", side_effect=RuntimeError("HTTP 404"))
    mocker.patch("app.runner.anthropic_news.fetch_latest_articles", return_value=[_article("anthropic post")])

    result = run_ingestion(hours=24)

    assert [video.video_id for video in result.videos] == ["vid1"]
    assert [article.title for article in result.articles] == ["anthropic post"]
    assert [(error.source, error.error) for error in result.errors] == [("openai", "HTTP 404")]


def test_persists_videos_and_articles_to_the_database(mocker, test_session_local):
    mocker.patch("app.runner.YOUTUBE_CHANNELS", ["@channel-one"])

    client = mocker.MagicMock()
    client.resolve_channel_id.return_value = "UC" + "x" * 22
    client.fetch_latest_videos.return_value = [_video("vid1")]
    mocker.patch("app.runner.YouTubeClient", return_value=mocker.MagicMock(__enter__=lambda self: client, __exit__=lambda *a: None))

    mocker.patch("app.runner.openai_news.fetch_latest_articles", return_value=[_article("openai post")])
    mocker.patch("app.runner.anthropic_news.fetch_latest_articles", return_value=[])

    run_ingestion(hours=24)

    with test_session_local() as session:
        stored_videos = YouTubeVideoRepository(session).list()
        stored_articles = OpenAIArticleRepository(session).list()
        assert [video.video_id for video in stored_videos] == ["vid1"]
        assert [article.url for article in stored_articles] == ["https://example.com/openai post"]


def test_rerunning_does_not_duplicate_or_overwrite_rows(mocker, test_session_local):
    mocker.patch("app.runner.YOUTUBE_CHANNELS", ["@channel-one"])

    client = mocker.MagicMock()
    client.resolve_channel_id.return_value = "UC" + "x" * 22
    client.fetch_latest_videos.return_value = [_video("vid1")]
    mocker.patch("app.runner.YouTubeClient", return_value=mocker.MagicMock(__enter__=lambda self: client, __exit__=lambda *a: None))

    mocker.patch("app.runner.openai_news.fetch_latest_articles", return_value=[_article("openai post")])
    mocker.patch("app.runner.anthropic_news.fetch_latest_articles", return_value=[])

    run_ingestion(hours=24)

    # second run sees the same video/article but with different (e.g. re-edited) titles
    client.fetch_latest_videos.return_value = [_video("vid1", title="retitled on the source")]
    mocker.patch("app.runner.openai_news.fetch_latest_articles", return_value=[_article("openai post", title="retitled on the source")])
    run_ingestion(hours=24)

    with test_session_local() as session:
        stored_videos = YouTubeVideoRepository(session).list()
        stored_articles = OpenAIArticleRepository(session).list()
        assert len(stored_videos) == 1
        assert len(stored_articles) == 1
        assert stored_videos[0].title == "vid1"
        assert stored_articles[0].title == "openai post"


def test_run_enrichment_fills_in_missing_transcripts_and_content(mocker, test_session_local):
    with test_session_local() as session:
        YouTubeVideoRepository(session).create(
            channel="c", video_id="vid1", title="Video", url="u",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc), description=None, transcript=None,
        )
        OpenAIArticleRepository(session).create(
            url="https://openai.com/index/example", title="OpenAI article",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        AnthropicArticleRepository(session).create(
            url="https://anthropic.com/news/example", title="Anthropic article",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        session.commit()

    client = mocker.MagicMock()
    client.get_transcript.return_value = mocker.MagicMock(text="the transcript")
    mocker.patch("app.runner.YouTubeClient", return_value=mocker.MagicMock(__enter__=lambda self: client, __exit__=lambda *a: None))
    mocker.patch("app.runner.fetch_article_markdown", return_value="# markdown content")

    result = run_enrichment()

    assert result.videos_enriched == 1
    assert result.articles_enriched == 2
    assert result.errors == []

    with test_session_local() as session:
        video = YouTubeVideoRepository(session).list()[0]
        openai_article = OpenAIArticleRepository(session).list()[0]
        anthropic_article = AnthropicArticleRepository(session).list()[0]
        assert video.transcript == "the transcript"
        assert openai_article.content == "# markdown content"
        assert anthropic_article.content == "# markdown content"


def test_run_enrichment_isolates_failures_per_row(mocker, test_session_local):
    with test_session_local() as session:
        YouTubeVideoRepository(session).create(
            channel="c", video_id="blocked", title="Blocked", url="u1",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc), description=None, transcript=None,
        )
        YouTubeVideoRepository(session).create(
            channel="c", video_id="works", title="Works", url="u2",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc), description=None, transcript=None,
        )
        session.commit()

    def get_transcript(video_id):
        if video_id == "blocked":
            raise RuntimeError("IP blocked")
        return mocker.MagicMock(text="the transcript")

    client = mocker.MagicMock()
    client.get_transcript.side_effect = get_transcript
    mocker.patch("app.runner.YouTubeClient", return_value=mocker.MagicMock(__enter__=lambda self: client, __exit__=lambda *a: None))
    mocker.patch("app.runner.fetch_article_markdown", return_value="content")

    result = run_enrichment()

    assert result.videos_enriched == 1
    assert [(error.source, error.error) for error in result.errors] == [("youtube-transcript:blocked", "IP blocked")]

    with test_session_local() as session:
        videos = {video.video_id: video.transcript for video in YouTubeVideoRepository(session).list()}
        assert videos == {"blocked": None, "works": "the transcript"}


def test_run_enrichment_marks_unavailable_transcripts_so_they_are_not_retried(mocker, test_session_local):
    with test_session_local() as session:
        YouTubeVideoRepository(session).create(
            channel="c", video_id="no-captions", title="No captions", url="u",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc), description=None, transcript=None,
        )
        session.commit()

    client = mocker.MagicMock()
    client.get_transcript.return_value = None
    mocker.patch("app.runner.YouTubeClient", return_value=mocker.MagicMock(__enter__=lambda self: client, __exit__=lambda *a: None))
    mocker.patch("app.runner.fetch_article_markdown", return_value="content")

    result = run_enrichment()

    assert result.videos_enriched == 1
    assert result.errors == []

    with test_session_local() as session:
        video = YouTubeVideoRepository(session).list()[0]
        assert video.transcript is None
        assert video.transcript_unavailable is True

    # a second run should not retry this video, since it's now marked unavailable
    client.get_transcript.reset_mock()
    run_enrichment()
    client.get_transcript.assert_not_called()


def test_run_enrichment_does_not_reprocess_already_enriched_rows(mocker, test_session_local):
    with test_session_local() as session:
        YouTubeVideoRepository(session).create(
            channel="c", video_id="vid1", title="Video", url="u",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc), description=None, transcript="already there",
        )
        session.commit()

    client = mocker.MagicMock()
    mocker.patch("app.runner.YouTubeClient", return_value=mocker.MagicMock(__enter__=lambda self: client, __exit__=lambda *a: None))
    mocker.patch("app.runner.fetch_article_markdown", return_value="content")

    result = run_enrichment()

    assert result.videos_enriched == 0
    client.get_transcript.assert_not_called()


def _mock_agent(mocker, summary: DigestSummary):
    agent = mocker.MagicMock()
    agent.summarize.return_value = summary
    mocker.patch("app.runner.SummarizerAgent", return_value=agent)
    return agent


def test_process_digest_creates_digests_for_everything_missing_one(mocker, test_session_local):
    with test_session_local() as session:
        YouTubeVideoRepository(session).create(
            channel="c", video_id="vid1", title="Video", url="https://youtube.com/watch?v=vid1",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc), description="desc", transcript="the transcript",
        )
        OpenAIArticleRepository(session).create(
            url="https://openai.com/index/example", title="OpenAI article",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc), content="the content",
        )
        AnthropicArticleRepository(session).create(
            url="https://anthropic.com/news/example", title="Anthropic article",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc), content="the content",
        )
        session.commit()

    summary = DigestSummary(title="Digest title", summary="A two to three sentence summary.")
    _mock_agent(mocker, summary)

    result = process_digest()

    assert result.digests_created == 3
    assert result.errors == []

    with test_session_local() as session:
        digests = DigestRepository(session).list()
        assert len(digests) == 3
        assert all(d.title == "Digest title" for d in digests)
        sources = {(d.youtube_video_id is not None, d.openai_article_id is not None, d.anthropic_article_id is not None) for d in digests}
        assert sources == {(True, False, False), (False, True, False), (False, False, True)}


def test_process_digest_isolates_failures_per_item(mocker, test_session_local):
    with test_session_local() as session:
        OpenAIArticleRepository(session).create(
            url="https://openai.com/index/broken", title="Broken",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        OpenAIArticleRepository(session).create(
            url="https://openai.com/index/works", title="Works",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        session.commit()

    agent = mocker.MagicMock()

    def summarize(*, title, url, source, content):
        if "broken" in url:
            raise RuntimeError("model error")
        return DigestSummary(title="Digest title", summary="A summary.")

    agent.summarize.side_effect = summarize
    mocker.patch("app.runner.SummarizerAgent", return_value=agent)

    result = process_digest()

    assert result.digests_created == 1
    assert [(error.source, error.error) for error in result.errors] == [
        ("digest-openai:https://openai.com/index/broken", "model error")
    ]


def test_process_digest_does_not_reprocess_already_digested_items(mocker, test_session_local):
    with test_session_local() as session:
        OpenAIArticleRepository(session).create(
            url="https://openai.com/index/example", title="Article",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        session.commit()

    summary = DigestSummary(title="Digest title", summary="A summary.")
    agent = _mock_agent(mocker, summary)

    process_digest()
    agent.summarize.reset_mock()
    result = process_digest()

    assert result.digests_created == 0
    agent.summarize.assert_not_called()


def test_process_digest_logs_which_item_it_is_processing(mocker, test_session_local, caplog):
    with test_session_local() as session:
        YouTubeVideoRepository(session).create(
            channel="c", video_id="vid1", title="My Video", url="https://youtube.com/watch?v=vid1",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc), transcript="the transcript",
        )
        YouTubeVideoRepository(session).create(
            channel="c", video_id="vid2", title="Broken Video", url="https://youtube.com/watch?v=vid2",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc), transcript="the transcript",
        )
        session.commit()

    agent = mocker.MagicMock()

    def summarize(*, title, url, source, content):
        if "vid2" in url:
            raise RuntimeError("model error")
        return DigestSummary(title="Digest title", summary="A summary.")

    agent.summarize.side_effect = summarize
    mocker.patch("app.runner.SummarizerAgent", return_value=agent)

    with caplog.at_level("INFO", logger="app.runner"):
        process_digest()

    messages = [record.getMessage() for record in caplog.records]
    assert "Digest: 2 item(s) need a summary (2 YouTube, 0 OpenAI, 0 Anthropic)" in messages
    assert "[YouTube 1/2] Summarizing 'My Video' (https://youtube.com/watch?v=vid1)" in messages
    assert "[YouTube 2/2] Summarizing 'Broken Video' (https://youtube.com/watch?v=vid2)" in messages
    assert "[YouTube 2/2] Failed to summarize https://youtube.com/watch?v=vid2" in messages
    assert "Digest: finished, 1 created, 1 error(s)" in messages


def test_process_digest_commits_each_digest_as_it_goes(mocker, test_session_local):
    """A crash partway through must not lose digests already created earlier in the run."""
    with test_session_local() as session:
        for slug in ("first", "second", "third"):
            OpenAIArticleRepository(session).create(
                url=f"https://openai.com/index/{slug}", title=slug,
                published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        session.commit()

    agent = mocker.MagicMock()

    def summarize(*, title, url, source, content):
        if title == "second":
            # e.g. Ctrl+C or the process being killed: not caught per-item
            raise KeyboardInterrupt
        return DigestSummary(title=f"Digest {title}", summary="A summary.")

    agent.summarize.side_effect = summarize
    mocker.patch("app.runner.SummarizerAgent", return_value=agent)

    with pytest.raises(KeyboardInterrupt):
        process_digest()

    with test_session_local() as session:
        assert [d.title for d in DigestRepository(session).list()] == ["Digest first"]


def test_process_digest_continues_after_a_failed_save(mocker, test_session_local):
    """A failed commit for one item is rolled back so later items can still be saved."""
    with test_session_local() as session:
        for slug in ("first", "second"):
            OpenAIArticleRepository(session).create(
                url=f"https://openai.com/index/{slug}", title=slug,
                published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        session.commit()

    _mock_agent(mocker, DigestSummary(title="Digest title", summary="A summary."))

    original_create = DigestRepository.create

    def create(self, **kwargs):
        if kwargs["url"].endswith("/first"):
            kwargs["title"] = None  # violates NOT NULL -> commit fails
        return original_create(self, **kwargs)

    mocker.patch.object(DigestRepository, "create", create)

    result = process_digest()

    assert result.digests_created == 1
    assert [e.source for e in result.errors] == ["digest-openai:https://openai.com/index/first"]
    with test_session_local() as session:
        assert [d.url for d in DigestRepository(session).list()] == ["https://openai.com/index/second"]


def _digest_row(session, *, published_at: datetime, title: str, **source_fk: int) -> None:
    DigestRepository(session).create(
        url=f"https://example.com/{title}", title=title, summary="s", published_at=published_at, **source_fk,
    )


def test_run_ranking_only_ranks_stories_published_in_the_last_n_hours(mocker, test_session_local):
    now = datetime.now(timezone.utc)
    with test_session_local() as session:
        video = YouTubeVideoRepository(session).create(
            channel="c", video_id="vid1", title="Video", url="https://youtube.com/watch?v=vid1",
            published_at=now, description=None, transcript=None,
        )
        article = OpenAIArticleRepository(session).create(url="https://openai.com/index/a", title="A", published_at=now)
        session.flush()
        _digest_row(session, published_at=now - timedelta(hours=1), title="recent", youtube_video_id=video.id)
        _digest_row(session, published_at=now - timedelta(hours=30), title="old", openai_article_id=article.id)
        session.commit()

    agent = mocker.MagicMock()
    agent.rank.return_value = []
    mocker.patch("app.runner.EditorAgent", return_value=agent)
    profile = UserProfile(name="Aaron", background="b", interests=["i"])

    result = run_ranking(hours=24, profile=profile)

    assert result.ranked == []
    call_kwargs = agent.rank.call_args.kwargs
    assert call_kwargs["profile"] is profile
    assert [(d.title, d.source) for d in call_kwargs["digests"]] == [("recent", "YouTube")]


def _ranked(rank: int) -> RankedDigest:
    return RankedDigest(
        rank=rank, digest_id=rank, title=f"Story {rank}", summary="s", url=f"https://example.com/{rank}",
        source="OpenAI", relevance_score=5, reasoning="r",
    )


def test_run_email_digest_uses_only_the_top_n_stories_and_sends(mocker):
    agent = mocker.MagicMock()
    agent.write_introduction.return_value = EmailIntroduction(introduction="Today's intro.")
    mocker.patch("app.runner.EmailAgent", return_value=agent)
    send = mocker.patch("app.runner.send_email", return_value="msg_1")
    profile = UserProfile(name="Aaron", background="b", interests=["i"])

    result = run_email_digest([_ranked(r) for r in (3, 1, 12, 2, 5, 4, 6, 7, 8, 9, 10, 11)], profile=profile, day=date(2026, 11, 3))

    assert [s.rank for s in result.stories] == list(range(1, 11))
    assert [s.rank for s in agent.write_introduction.call_args.kwargs["stories"]] == list(range(1, 11))
    assert result.message_id == "msg_1"
    assert "Hey Aaron" in result.email.text and "Today's intro." in result.email.text
    assert "Story 11" not in result.email.text
    send.assert_called_once_with(result.email)


def test_run_email_digest_dry_run_builds_but_does_not_send(mocker):
    agent = mocker.MagicMock()
    agent.write_introduction.return_value = EmailIntroduction(introduction="intro")
    mocker.patch("app.runner.EmailAgent", return_value=agent)
    send = mocker.patch("app.runner.send_email")

    result = run_email_digest([_ranked(1)], send=False)

    assert result.email is not None and result.message_id is None
    send.assert_not_called()


def test_run_email_digest_does_nothing_without_stories(mocker):
    email_agent = mocker.patch("app.runner.EmailAgent")
    send = mocker.patch("app.runner.send_email")

    result = run_email_digest([])

    assert result.email is None
    email_agent.assert_not_called()
    send.assert_not_called()


def test_run_ranking_skips_digests_that_were_already_emailed(mocker, test_session_local):
    now = datetime.now(timezone.utc)
    with test_session_local() as session:
        for title in ("sent", "unsent"):
            _digest_row(session, published_at=now - timedelta(hours=1), title=title)
        session.flush()
        sent = next(d for d in DigestRepository(session).list() if d.title == "sent")
        DigestRepository(session).mark_sent([sent.id], message_id="msg_old")
        session.commit()

    agent = mocker.MagicMock()
    agent.rank.return_value = []
    mocker.patch("app.runner.EditorAgent", return_value=agent)

    run_ranking(hours=24)

    assert [d.title for d in agent.rank.call_args.kwargs["digests"]] == ["unsent"]


def test_run_email_digest_records_the_sent_stories_but_not_on_a_dry_run(mocker, test_session_local):
    with test_session_local() as session:
        for title in ("one", "two"):
            _digest_row(session, published_at=datetime.now(timezone.utc), title=title)
        session.commit()
        ids = sorted(d.id for d in DigestRepository(session).list())

    agent = mocker.MagicMock()
    agent.write_introduction.return_value = EmailIntroduction(introduction="intro")
    mocker.patch("app.runner.EmailAgent", return_value=agent)
    mocker.patch("app.runner.send_email", return_value="msg_1")
    ranked = [_ranked(1).model_copy(update={"digest_id": ids[0]}), _ranked(2).model_copy(update={"digest_id": ids[1]})]

    run_email_digest(ranked, send=False)
    with test_session_local() as session:
        assert len(DigestRepository(session).list_unsent()) == 2

    run_email_digest(ranked, top_n=1)
    with test_session_local() as session:
        assert [d.id for d in DigestRepository(session).list_unsent()] == [ids[1]]


def test_run_ingestion_counts_only_new_rows(mocker):
    client = mocker.MagicMock()
    client.resolve_channel_id.return_value = "UC" + "x" * 22
    client.fetch_latest_videos.return_value = [_video("vid1")]
    mocker.patch("app.runner.YOUTUBE_CHANNELS", ["@channel"])
    mocker.patch("app.runner.YouTubeClient", return_value=mocker.MagicMock(__enter__=lambda self: client, __exit__=lambda *a: None))
    mocker.patch("app.runner.openai_news.fetch_latest_articles", return_value=[_article("post")])
    mocker.patch("app.runner.anthropic_news.fetch_latest_articles", return_value=[])

    assert run_ingestion().new_rows == 2
    assert run_ingestion().new_rows == 0


def test_process_digest_dates_each_digest_with_its_sources_publish_date(mocker, test_session_local):
    published = datetime(2026, 3, 14, 9, 30)
    with test_session_local() as session:
        YouTubeVideoRepository(session).create(
            channel="c", video_id="vid1", title="Video", url="https://youtube.com/watch?v=vid1",
            published_at=published, description="desc", transcript=None,
        )
        OpenAIArticleRepository(session).create(url="https://openai.com/index/a", title="A", published_at=published)
        session.commit()

    _mock_agent(mocker, DigestSummary(title="Digest title", summary="A summary."))

    process_digest()

    with test_session_local() as session:
        assert [d.published_at for d in DigestRepository(session).list()] == [published, published]

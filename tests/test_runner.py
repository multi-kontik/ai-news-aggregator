from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base
from app.database.repository import AnthropicArticleRepository, OpenAIArticleRepository, YouTubeVideoRepository
from app.ingestion.feeds import Article
from app.ingestion.youtube import ChannelVideo
from app.runner import run_enrichment, run_ingestion


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

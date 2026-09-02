from __future__ import annotations

from datetime import datetime, timezone

from app.ingestion.feeds import Article
from app.ingestion.youtube import ChannelVideo
from app.runner import run_ingestion


def _video(video_id: str) -> ChannelVideo:
    return ChannelVideo(
        video_id=video_id,
        channel_id="UC" + "x" * 22,
        title=video_id,
        url=f"https://youtube.com/watch?v={video_id}",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        description="",
    )


def _article(title: str) -> Article:
    return Article(
        title=title,
        url=f"https://example.com/{title}",
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

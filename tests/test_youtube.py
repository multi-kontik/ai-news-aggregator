from __future__ import annotations

import time
import types
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from youtube_transcript_api import IpBlocked, TranscriptsDisabled
from youtube_transcript_api.proxies import WebshareProxyConfig

from app.ingestion.youtube import ChannelVideo, Transcript, YouTubeClient

CHANNEL_ID = "UC" + "x" * 22


@pytest.fixture
def client():
    with YouTubeClient() as client:
        yield client


def _feed_entry(
    video_id: str,
    title: str,
    published: tuple[int, int, int, int, int, int],
    summary: str = "",
):
    return types.SimpleNamespace(
        yt_videoid=video_id,
        title=title,
        link=f"https://www.youtube.com/watch?v={video_id}",
        summary=summary,
        published_parsed=time.struct_time(published + (0, 0, 0)),
    )


class TestResolveChannelId:
    def test_passes_through_a_canonical_channel_id(self, client):
        assert client.resolve_channel_id(CHANNEL_ID) == CHANNEL_ID

    @respx.mock
    def test_resolves_a_handle_via_the_canonical_link(self, client):
        respx.get(f"https://www.youtube.com/@SomeHandle").mock(
            return_value=httpx.Response(
                200,
                text=f'<link rel="canonical" href="https://www.youtube.com/channel/{CHANNEL_ID}">',
            )
        )
        assert client.resolve_channel_id("@SomeHandle") == CHANNEL_ID

    @respx.mock
    def test_raises_when_no_canonical_link_is_found(self, client):
        respx.get("https://www.youtube.com/@Missing").mock(
            return_value=httpx.Response(200, text="<html>no canonical link here</html>")
        )
        with pytest.raises(ValueError, match="Missing"):
            client.resolve_channel_id("@Missing")


class TestFetchLatestVideos:
    def test_returns_all_entries_as_channel_videos(self, client, mocker):
        feed = types.SimpleNamespace(
            entries=[
                _feed_entry("vid1", "First video", (2026, 1, 1, 12, 0, 0), summary="desc one"),
                _feed_entry("vid2", "Second video", (2026, 1, 2, 12, 0, 0), summary="desc two"),
            ]
        )
        mocker.patch("app.ingestion.feeds.feedparser.parse", return_value=feed)

        videos = client.fetch_latest_videos(CHANNEL_ID)

        assert videos == [
            ChannelVideo(
                video_id="vid1",
                channel_id=CHANNEL_ID,
                title="First video",
                url="https://www.youtube.com/watch?v=vid1",
                published_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
                description="desc one",
            ),
            ChannelVideo(
                video_id="vid2",
                channel_id=CHANNEL_ID,
                title="Second video",
                url="https://www.youtube.com/watch?v=vid2",
                published_at=datetime(2026, 1, 2, 12, 0, 0, tzinfo=timezone.utc),
                description="desc two",
            ),
        ]

    def test_raises_when_the_feed_fetch_fails(self, client, mocker):
        feed = types.SimpleNamespace(
            entries=[],
            status=404,
            bozo=True,
            bozo_exception=Exception("not well-formed"),
        )
        mocker.patch("app.ingestion.feeds.feedparser.parse", return_value=feed)

        with pytest.raises(RuntimeError, match="404"):
            client.fetch_latest_videos(CHANNEL_ID)

    def test_filters_out_entries_published_before_since(self, client, mocker):
        feed = types.SimpleNamespace(
            entries=[
                _feed_entry("old", "Old video", (2026, 1, 1, 12, 0, 0)),
                _feed_entry("new", "New video", (2026, 1, 3, 12, 0, 0)),
            ]
        )
        mocker.patch("app.ingestion.feeds.feedparser.parse", return_value=feed)

        videos = client.fetch_latest_videos(
            CHANNEL_ID, since=datetime(2026, 1, 2, tzinfo=timezone.utc)
        )

        assert [video.video_id for video in videos] == ["new"]


class TestGetTranscript:
    def test_returns_the_joined_transcript_text(self, client, mocker):
        mocker.patch.object(
            client._transcript_api,
            "fetch",
            return_value=[
                types.SimpleNamespace(text="hello"),
                types.SimpleNamespace(text="world"),
            ],
        )

        assert client.get_transcript("vid1") == Transcript(text="hello world")

    def test_returns_none_when_the_transcript_is_unavailable(self, client, mocker):
        mocker.patch.object(
            client._transcript_api,
            "fetch",
            side_effect=TranscriptsDisabled("vid1"),
        )

        assert client.get_transcript("vid1") is None

    def test_raises_when_the_request_is_blocked(self, client, mocker):
        """IP blocks are an environmental failure, not a content-absence signal, and
        shouldn't be conflated with "this video has no transcript"."""
        mocker.patch.object(
            client._transcript_api,
            "fetch",
            side_effect=IpBlocked("vid1"),
        )

        with pytest.raises(IpBlocked):
            client.get_transcript("vid1")


class TestProxyConfig:
    def test_uses_no_proxy_when_credentials_are_unset(self, mocker):
        mocker.patch("app.ingestion.youtube.settings.webshare_proxy_username", None)
        mocker.patch("app.ingestion.youtube.settings.webshare_proxy_password", None)

        with YouTubeClient() as client:
            assert client._transcript_api._fetcher._proxy_config is None

    def test_uses_a_webshare_proxy_when_credentials_are_set(self, mocker):
        mocker.patch("app.ingestion.youtube.settings.webshare_proxy_username", "the-username")
        mocker.patch("app.ingestion.youtube.settings.webshare_proxy_password", "the-password")

        with YouTubeClient() as client:
            proxy_config = client._transcript_api._fetcher._proxy_config
            assert isinstance(proxy_config, WebshareProxyConfig)
            assert proxy_config.proxy_username == "the-username"
            assert proxy_config.proxy_password == "the-password"

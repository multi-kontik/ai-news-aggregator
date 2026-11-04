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


@pytest.fixture(autouse=True)
def no_proxy_and_no_waiting(mocker):
    """Don't pick up real Webshare credentials from .env, and don't sleep between retries."""
    mocker.patch("app.ingestion.youtube.settings.webshare_proxy_username", None)
    mocker.patch("app.ingestion.youtube.settings.webshare_proxy_password", None)
    mocker.patch("app.ingestion.youtube.settings.webshare_proxy_countries", None)
    mocker.patch("app.ingestion.youtube.settings.youtube_api_key", None)
    mocker.patch("app.ingestion.youtube.time.sleep")


@pytest.fixture
def client():
    with YouTubeClient() as client:
        yield client


FEED = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"


@pytest.fixture
def feed_ok():
    """The feed HTTP request succeeds; the parsed entries come from patching feedparser.parse."""
    with respx.mock:
        route = respx.get(FEED).mock(return_value=httpx.Response(200, content=b"<feed/>"))
        yield route


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
    def test_returns_all_entries_as_channel_videos(self, client, mocker, feed_ok):
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

    @respx.mock
    def test_raises_when_the_feed_keeps_failing(self, client):
        route = respx.get(FEED).mock(return_value=httpx.Response(404))

        with pytest.raises(RuntimeError, match="404"):
            client.fetch_latest_videos(CHANNEL_ID)
        assert route.call_count == 3

    @respx.mock
    def test_retries_a_rate_limited_feed_and_then_succeeds(self, client, mocker):
        respx.get(FEED).mock(side_effect=[httpx.Response(500), httpx.Response(200, content=b"<feed/>")])
        feed = types.SimpleNamespace(entries=[_feed_entry("vid1", "Video", (2026, 1, 1, 12, 0, 0))])
        mocker.patch("app.ingestion.feeds.feedparser.parse", return_value=feed)

        assert [video.video_id for video in client.fetch_latest_videos(CHANNEL_ID)] == ["vid1"]

    @respx.mock
    def test_does_not_retry_other_errors(self, client):
        route = respx.get(FEED).mock(return_value=httpx.Response(403))

        with pytest.raises(RuntimeError, match="403"):
            client.fetch_latest_videos(CHANNEL_ID)
        assert route.call_count == 1

    def test_filters_out_entries_published_before_since(self, client, mocker, feed_ok):
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
    def test_uses_no_proxy_when_credentials_are_unset(self):
        with YouTubeClient() as client:
            assert client._transcript_api._fetcher._proxy_config is None
            assert client._http._mounts == {}

    def test_uses_a_webshare_proxy_when_credentials_are_set(self, mocker):
        mocker.patch("app.ingestion.youtube.settings.webshare_proxy_username", "the-username")
        mocker.patch("app.ingestion.youtube.settings.webshare_proxy_password", "the-password")

        with YouTubeClient() as client:
            proxy_config = client._transcript_api._fetcher._proxy_config
            assert isinstance(proxy_config, WebshareProxyConfig)
            assert proxy_config.proxy_username == "the-username"
            assert proxy_config.proxy_password == "the-password"


    def test_routes_channel_pages_and_feeds_through_the_proxy_without_reusing_connections(self, mocker):
        mocker.patch("app.ingestion.youtube.settings.webshare_proxy_username", "the-username")
        mocker.patch("app.ingestion.youtube.settings.webshare_proxy_password", "the-password")
        proxy = mocker.spy(httpx, "Client")

        with YouTubeClient():
            pass

        kwargs = proxy.call_args.kwargs
        assert kwargs["proxy"] == WebshareProxyConfig("the-username", "the-password").url
        assert kwargs["limits"].max_keepalive_connections == 0

    def test_limits_proxy_ips_to_the_configured_countries(self, mocker):
        mocker.patch("app.ingestion.youtube.settings.webshare_proxy_username", "the-username")
        mocker.patch("app.ingestion.youtube.settings.webshare_proxy_password", "the-password")
        mocker.patch("app.ingestion.youtube.settings.webshare_proxy_countries", "US, ca")

        proxy = mocker.spy(httpx, "Client")

        with YouTubeClient() as client:
            assert client._transcript_api._fetcher._proxy_config.url.startswith("http://the-username-US-CA-rotate:")
        assert proxy.call_args.kwargs["proxy"].startswith("http://the-username-US-CA-rotate:")


API = "https://www.googleapis.com/youtube/v3"


@pytest.fixture
def api_client(mocker):
    mocker.patch("app.ingestion.youtube.settings.youtube_api_key", "the-api-key")
    with YouTubeClient() as client:
        yield client


def _playlist_item(video_id: str, published: str | None, title: str = "A video"):
    details = {"videoId": video_id}
    if published:
        details["videoPublishedAt"] = published
    return {"snippet": {"title": title, "description": f"About {video_id}"}, "contentDetails": details}


class TestDataApi:
    @respx.mock
    def test_resolves_a_handle_through_the_api_with_the_key_in_a_header(self, api_client):
        route = respx.get(f"{API}/channels", params={"part": "id", "forHandle": "@SomeHandle"}).mock(
            return_value=httpx.Response(200, json={"items": [{"id": CHANNEL_ID}]})
        )

        assert api_client.resolve_channel_id("@SomeHandle") == CHANNEL_ID
        request = route.calls.last.request
        assert request.headers["X-Goog-Api-Key"] == "the-api-key"
        assert "the-api-key" not in str(request.url)

    @respx.mock
    def test_raises_when_the_api_finds_no_channel_for_a_handle(self, api_client):
        respx.get(f"{API}/channels").mock(return_value=httpx.Response(200, json={}))

        with pytest.raises(ValueError, match="@Missing"):
            api_client.resolve_channel_id("@Missing")

    @respx.mock
    def test_lists_recent_uploads_from_the_uploads_playlist(self, api_client):
        route = respx.get(f"{API}/playlistItems").mock(return_value=httpx.Response(200, json={"items": [
            _playlist_item("new", "2026-01-03T12:00:00Z", title="New video"),
            _playlist_item("private", None, title="Private video"),
            _playlist_item("old", "2026-01-01T12:00:00Z"),
        ]}))

        videos = api_client.fetch_latest_videos(CHANNEL_ID, since=datetime(2026, 1, 2, tzinfo=timezone.utc))

        assert route.calls.last.request.url.params["playlistId"] == "UU" + CHANNEL_ID[2:]
        assert videos == [
            ChannelVideo(
                video_id="new",
                channel_id=CHANNEL_ID,
                title="New video",
                url="https://www.youtube.com/watch?v=new",
                published_at=datetime(2026, 1, 3, 12, 0, 0, tzinfo=timezone.utc),
                description="About new",
            )
        ]

    @respx.mock
    def test_reports_the_apis_error_message_without_the_key(self, api_client):
        respx.get(f"{API}/playlistItems").mock(return_value=httpx.Response(
            403, json={"error": {"message": "The request cannot be completed because you have exceeded your quota."}},
        ))

        with pytest.raises(RuntimeError, match="HTTP 403: The request cannot be completed") as error:
            api_client.fetch_latest_videos(CHANNEL_ID)
        assert "the-api-key" not in str(error.value)

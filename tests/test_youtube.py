from __future__ import annotations

import types
from datetime import datetime, timezone

import httpx
import pytest
import respx
from youtube_transcript_api import IpBlocked, TranscriptsDisabled

from app.database.models import YouTubeVideo
from app.ingestion.youtube import ChannelVideo, YouTubeClient, YouTubeSource

API = "https://www.googleapis.com/youtube/v3"
CHANNEL_ID = "UC" + "x" * 22


@pytest.fixture(autouse=True)
def settings(mocker):
    """Known settings for every test, so real keys in .env are never used."""
    for name, value in [
        ("youtube_api_key", "the-api-key"),
        ("webshare_proxy_username", None),
        ("webshare_proxy_password", None),
        ("webshare_proxy_countries", None),
    ]:
        mocker.patch(f"app.ingestion.youtube.settings.{name}", value)


@pytest.fixture
def client():
    client = YouTubeClient(api_key="the-api-key")
    yield client
    client.close()


def _playlist_item(video_id: str, published: str | None, title: str = "A video"):
    details = {"videoId": video_id}
    if published:
        details["videoPublishedAt"] = published
    return {"snippet": {"title": title, "description": f"About {video_id}"}, "contentDetails": details}


class TestDataApi:
    def test_passes_through_a_channel_id(self, client):
        assert client.resolve_channel_id(CHANNEL_ID) == CHANNEL_ID

    @respx.mock
    def test_resolves_a_handle_with_the_key_in_a_header_not_the_url(self, client):
        route = respx.get(f"{API}/channels", params={"part": "id", "forHandle": "@SomeHandle"}).mock(
            return_value=httpx.Response(200, json={"items": [{"id": CHANNEL_ID}]})
        )

        assert client.resolve_channel_id("@SomeHandle") == CHANNEL_ID
        request = route.calls.last.request
        assert request.headers["X-Goog-Api-Key"] == "the-api-key"
        assert "the-api-key" not in str(request.url)

    @respx.mock
    def test_raises_when_no_channel_matches_the_handle(self, client):
        respx.get(f"{API}/channels").mock(return_value=httpx.Response(200, json={}))

        with pytest.raises(ValueError, match="@Missing"):
            client.resolve_channel_id("@Missing")

    @respx.mock
    def test_lists_recent_public_uploads_from_the_uploads_playlist(self, client):
        route = respx.get(f"{API}/playlistItems").mock(return_value=httpx.Response(200, json={"items": [
            _playlist_item("new", "2026-01-03T12:00:00Z", title="New video"),
            _playlist_item("private", None, title="Private video"),
            _playlist_item("old", "2026-01-01T12:00:00Z"),
        ]}))

        videos = client.fetch_latest_videos(CHANNEL_ID, since=datetime(2026, 1, 2, tzinfo=timezone.utc))

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
    def test_reports_the_apis_error_message_without_the_key(self, client):
        respx.get(f"{API}/playlistItems").mock(return_value=httpx.Response(
            403, json={"error": {"message": "The request cannot be completed because you have exceeded your quota."}},
        ))

        with pytest.raises(RuntimeError, match="HTTP 403: The request cannot be completed") as error:
            client.fetch_latest_videos(CHANNEL_ID)
        assert "the-api-key" not in str(error.value)


class TestTranscripts:
    def test_returns_the_joined_transcript_text(self, client, mocker):
        snippets = [types.SimpleNamespace(text="hello"), types.SimpleNamespace(text="world")]
        mocker.patch.object(client._transcript_api, "fetch", return_value=snippets)

        assert client.get_transcript("vid1") == "hello world"

    def test_returns_none_when_the_video_has_no_captions(self, client, mocker):
        mocker.patch.object(client._transcript_api, "fetch", side_effect=TranscriptsDisabled("vid1"))

        assert client.get_transcript("vid1") is None

    def test_raises_when_the_request_is_blocked_so_it_is_retried_later(self, client, mocker):
        mocker.patch.object(client._transcript_api, "fetch", side_effect=IpBlocked("vid1"))

        with pytest.raises(IpBlocked):
            client.get_transcript("vid1")

    def test_go_direct_without_webshare_credentials(self, client):
        assert client._transcript_api._fetcher._proxy_config is None

    def test_go_through_webshare_limited_to_the_configured_countries(self, mocker):
        mocker.patch("app.ingestion.youtube.settings.webshare_proxy_username", "the-username")
        mocker.patch("app.ingestion.youtube.settings.webshare_proxy_password", "the-password")
        mocker.patch("app.ingestion.youtube.settings.webshare_proxy_countries", "US, ca")

        client = YouTubeClient(api_key="k")

        assert client._transcript_api._fetcher._proxy_config.url.startswith("http://the-username-US-CA-rotate:")
        client.close()


class TestYouTubeSource:
    def test_is_skipped_without_an_api_key(self, mocker):
        mocker.patch("app.ingestion.youtube.settings.youtube_api_key", None)

        assert YouTubeSource(channels=["@OpenAI"]).units() == []

    def test_fetches_each_channels_videos_as_rows(self, mocker):
        video = ChannelVideo(
            video_id="vid1", channel_id=CHANNEL_ID, title="Video", url="https://www.youtube.com/watch?v=vid1",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc), description="desc",
        )
        mocker.patch.object(YouTubeClient, "resolve_channel_id", return_value=CHANNEL_ID)
        mocker.patch.object(YouTubeClient, "fetch_latest_videos", return_value=[video])

        with YouTubeSource(channels=["@OpenAI"]) as source:
            assert source.units() == ["@OpenAI"]
            rows = source.fetch("@OpenAI", since=datetime(2026, 1, 1, tzinfo=timezone.utc))

        assert rows == [{
            "channel": "@OpenAI", "video_id": "vid1", "title": "Video", "url": "https://www.youtube.com/watch?v=vid1",
            "published_at": datetime(2026, 1, 1, tzinfo=timezone.utc), "description": "desc",
        }]

    def test_enrich_stores_the_transcript_or_marks_it_unavailable(self, mocker):
        mocker.patch.object(YouTubeClient, "get_transcript", side_effect=["the transcript", None])
        with_captions = YouTubeVideo(video_id="a", transcript=None, transcript_unavailable=False)
        without_captions = YouTubeVideo(video_id="b", transcript=None, transcript_unavailable=False)

        with YouTubeSource(channels=[]) as source:
            source.enrich(with_captions)
            source.enrich(without_captions)

        assert (with_captions.transcript, with_captions.transcript_unavailable) == ("the transcript", False)
        assert (without_captions.transcript, without_captions.transcript_unavailable) == (None, True)

    @pytest.mark.parametrize(
        "transcript, description, expected",
        [("words", "desc", ("words", "transcript")), (None, "desc", ("desc", "description")), (None, "", ("Title", "title only"))],
    )
    def test_summarizes_the_richest_text_available(self, transcript, description, expected):
        row = YouTubeVideo(title="Title", transcript=transcript, description=description)

        assert YouTubeSource(channels=[]).digest_content(row) == expected

    def test_needs_a_context_manager_for_its_client(self):
        with pytest.raises(RuntimeError, match="context manager"):
            YouTubeSource(channels=[]).client

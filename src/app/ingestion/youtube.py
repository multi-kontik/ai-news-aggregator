"""YouTube channels as a source: uploads come from the YouTube Data API, transcripts from YouTube itself.

YouTube's public RSS feeds return 404 for every channel, so listing uploads
needs a Data API key (`YOUTUBE_API_KEY`). Transcripts don't use the API: they
come from YouTube's own endpoints, which block most datacenter IPs, so they go
through Webshare's rotating residential proxy when its credentials are set.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel
from sqlalchemy import ColumnElement
from youtube_transcript_api import NoTranscriptFound, TranscriptsDisabled, VideoUnavailable, YouTubeTranscriptApi
from youtube_transcript_api.proxies import WebshareProxyConfig

from app.config import settings
from app.database.models import YouTubeVideo
from app.ingestion.base import Source

logger = logging.getLogger(__name__)

API_URL = "https://www.googleapis.com/youtube/v3"
_CHANNEL_ID_PATTERN = re.compile(r"^UC[\w-]{22}$")


def webshare_proxy_config() -> WebshareProxyConfig | None:
    """The Webshare rotating residential proxy from settings, or None if it isn't configured."""
    if not (settings.webshare_proxy_username and settings.webshare_proxy_password):
        return None
    countries = [c.strip().lower() for c in (settings.webshare_proxy_countries or "").split(",") if c.strip()]
    return WebshareProxyConfig(
        proxy_username=settings.webshare_proxy_username,
        proxy_password=settings.webshare_proxy_password,
        filter_ip_locations=countries or None,
    )


class ChannelVideo(BaseModel):
    video_id: str
    channel_id: str
    title: str
    url: str
    published_at: datetime
    description: str


class YouTubeClient:
    """Resolves channels and lists their uploads through the Data API, and fetches transcripts."""

    def __init__(self, api_key: str) -> None:
        # The key goes in a header, never the URL, so it can't leak into a log line or error message.
        self._api = httpx.Client(base_url=API_URL, headers={"X-Goog-Api-Key": api_key}, timeout=15)
        self._transcript_api = YouTubeTranscriptApi(proxy_config=webshare_proxy_config())

    def close(self) -> None:
        self._api.close()

    def _api_get(self, endpoint: str, **params: str | int) -> dict[str, Any]:
        """Call a Data API endpoint and return its JSON, raising with the API's own error message."""
        response = self._api.get(f"/{endpoint}", params=params)
        if response.status_code != 200:
            try:
                message = response.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = response.reason_phrase
            raise RuntimeError(f"YouTube Data API {endpoint} failed with HTTP {response.status_code}: {message}")
        return response.json()

    def resolve_channel_id(self, channel: str) -> str:
        """Turn a handle like "@OpenAI" into its UC... channel id. A channel id is returned as is."""
        channel = channel.strip()
        if _CHANNEL_ID_PATTERN.match(channel):
            return channel
        items = self._api_get("channels", part="id", forHandle=channel).get("items") or []
        if not items:
            raise ValueError(f"Could not resolve a channel id for {channel!r}")
        return items[0]["id"]

    def fetch_latest_videos(self, channel_id: str, since: datetime | None = None) -> list[ChannelVideo]:
        """A channel's 50 most recent uploads, newest first, optionally only those published since `since`.

        Uploads are read from the channel's uploads playlist, whose id is the
        channel id with "UC" swapped for "UU". Private and deleted videos are skipped.
        """
        data = self._api_get("playlistItems", part="snippet,contentDetails", playlistId="UU" + channel_id[2:], maxResults=50)

        videos = []
        for item in data.get("items", []):
            details, snippet = item.get("contentDetails", {}), item.get("snippet", {})
            published = details.get("videoPublishedAt")  # missing for private or deleted videos
            if not published:
                continue
            published_at = datetime.fromisoformat(published.replace("Z", "+00:00"))
            if since is not None and published_at < since:
                continue
            video_id = details["videoId"]
            videos.append(
                ChannelVideo(
                    video_id=video_id,
                    channel_id=channel_id,
                    title=snippet.get("title", video_id),
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    published_at=published_at,
                    description=snippet.get("description") or "",
                )
            )
        return videos

    def get_transcript(self, video_id: str, languages: tuple[str, ...] = ("en",)) -> str | None:
        """A video's transcript text, or None if it has no captions.

        "No captions" (disabled, none in the language, video unavailable)
        returns None. Anything else, like an IP block, raises, so the caller
        can retry it later instead of giving up on the video.
        """
        try:
            fetched = self._transcript_api.fetch(video_id, languages=list(languages))
        except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable):
            return None
        return " ".join(snippet.text for snippet in fetched)


class YouTubeSource(Source):
    """A list of YouTube channels. Each channel is fetched (and can fail) on its own.

    Videos are stored with their title and description, then enriched with
    their transcript. A video with no captions is marked `transcript_unavailable`
    so it isn't retried every run; a failed fetch (e.g. an IP block) is retried.
    Without `YOUTUBE_API_KEY` there's no way to list uploads, so the source is
    skipped with a warning.
    """

    name = "YouTube"
    model = YouTubeVideo
    unique_field = "video_id"
    digest_link = "youtube_video_id"

    def __init__(self, *, channels: list[str]) -> None:
        self.channels = channels
        self._client: YouTubeClient | None = None

    def __enter__(self) -> YouTubeSource:
        self._client = YouTubeClient(api_key=settings.youtube_api_key or "")
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    @property
    def client(self) -> YouTubeClient:
        if self._client is None:
            raise RuntimeError("YouTubeSource must be used as a context manager")
        return self._client

    def units(self) -> list[str]:
        if not settings.youtube_api_key:
            logger.warning("YOUTUBE_API_KEY is not set, so YouTube channels are skipped")
            return []
        return self.channels

    def fetch(self, unit: str, since: datetime) -> list[dict[str, Any]]:
        videos = self.client.fetch_latest_videos(self.client.resolve_channel_id(unit), since=since)
        return [
            {
                "channel": unit,
                "video_id": video.video_id,
                "title": video.title,
                "url": video.url,
                "published_at": video.published_at,
                "description": video.description,
            }
            for video in videos
        ]

    def needs_enrichment(self) -> ColumnElement[bool]:
        return YouTubeVideo.transcript.is_(None) & YouTubeVideo.transcript_unavailable.is_(False)

    def enrich(self, row: YouTubeVideo) -> None:
        transcript = self.client.get_transcript(row.video_id)
        if transcript is None:
            row.transcript_unavailable = True
        else:
            row.transcript = transcript

    def digest_content(self, row: YouTubeVideo) -> tuple[str, str]:
        if row.transcript:
            return row.transcript, "transcript"
        if row.description:
            return row.description, "description"
        return row.title, "title only"

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from types import TracebackType

import httpx
from pydantic import BaseModel
from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi,
)
from youtube_transcript_api.proxies import WebshareProxyConfig

from app.config import settings
from app.ingestion.feeds import entry_published_at, parse_feed_content

logger = logging.getLogger(__name__)

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
API_URL = "https://www.googleapis.com/youtube/v3"
_CHANNEL_ID_PATTERN = re.compile(r"^UC[\w-]{22}$")
_CANONICAL_CHANNEL_LINK_PATTERN = re.compile(
    r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"'
)
_USER_AGENT = "Mozilla/5.0"
# Statuses YouTube answers with when it's rate-limiting or blocking an IP. Its
# feed endpoint returns 404 or 500 for this, not just 429.
_RETRY_STATUSES = {404, 429, 500, 502, 503}
_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 2.0


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


class Transcript(BaseModel):
    text: str


class ChannelVideo(BaseModel):
    video_id: str
    channel_id: str
    title: str
    url: str
    published_at: datetime
    description: str
    transcript: Transcript | None = None


class YouTubeClient:
    """Surface for resolving channels, fetching recent uploads, and pulling transcripts.

    With a YouTube Data API key, channels and their latest uploads come from
    the official API. Without one, the channel page and public RSS feed are
    used instead, which YouTube has been answering with 404s.

    YouTube blocks most cloud/datacenter IPs and rate-limits busy ones. When
    Webshare credentials are set, every request (channel pages, feeds and
    transcripts) goes through Webshare's rotating residential proxy. Connections
    aren't reused, so each request (and each retry) gets a fresh IP.
    """

    def __init__(self) -> None:
        proxy_config = webshare_proxy_config()
        self._http = httpx.Client(
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
            timeout=20 if proxy_config else 10,
            proxy=proxy_config.url if proxy_config else None,
            limits=httpx.Limits(max_keepalive_connections=0) if proxy_config else httpx.Limits(),
        )
        self._transcript_api = YouTubeTranscriptApi(proxy_config=proxy_config)

        # The Data API is Google's own and isn't IP-blocked, so it goes direct, not
        # through the proxy. The key is sent as a header so it never shows up in a
        # URL, a log line or an error message.
        self._api = (
            httpx.Client(base_url=API_URL, headers={"X-Goog-Api-Key": settings.youtube_api_key}, timeout=15)
            if settings.youtube_api_key
            else None
        )

    def __enter__(self) -> YouTubeClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()
        if self._api is not None:
            self._api.close()

    def _api_get(self, endpoint: str, **params: str | int) -> dict:
        """Call a YouTube Data API endpoint and return its JSON, raising with the API's own error message."""
        assert self._api is not None
        response = self._api.get(f"/{endpoint}", params=params)
        if response.status_code != 200:
            try:
                message = response.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = response.reason_phrase
            raise RuntimeError(f"YouTube Data API {endpoint} failed with HTTP {response.status_code}: {message}")
        return response.json()

    def _get(self, url: str) -> httpx.Response:
        """GET `url`, retrying the statuses YouTube uses for rate limits and IP blocks. Returns the last response."""
        for attempt in range(1, _ATTEMPTS):
            response = self._http.get(url)
            if response.status_code not in _RETRY_STATUSES:
                return response
            logger.info("YouTube returned HTTP %d for %s, retrying (%d/%d)", response.status_code, url, attempt, _ATTEMPTS - 1)
            time.sleep(_RETRY_DELAY_SECONDS * attempt)
        return self._http.get(url)

    def resolve_channel_id(self, channel: str) -> str:
        """Resolve a channel handle/URL/id to its canonical UC... channel id.

        Accepts a raw channel id (UC...), a handle ("@name"), or a full channel
        URL. YouTube's RSS feed only accepts the UC id, not handles or custom
        URLs, so anything else is resolved by fetching the channel page and
        reading its canonical link.
        """
        channel = channel.strip()
        if _CHANNEL_ID_PATTERN.match(channel):
            return channel

        if self._api is not None and channel.startswith("@"):
            items = self._api_get("channels", part="id", forHandle=channel).get("items") or []
            if not items:
                raise ValueError(f"Could not resolve a channel id for {channel!r}")
            return items[0]["id"]

        url = channel if channel.startswith("http") else f"https://www.youtube.com/{channel.lstrip('/')}"
        response = self._get(url)
        response.raise_for_status()

        match = _CANONICAL_CHANNEL_LINK_PATTERN.search(response.text)
        if not match:
            raise ValueError(f"Could not resolve a channel id for {channel!r}")
        return match.group(1)

    def fetch_latest_videos(self, channel_id: str, since: datetime | None = None) -> list[ChannelVideo]:
        """Fetch videos from a channel's RSS feed, optionally filtered to those published after `since`.

        Uses the Data API when a key is set, otherwise the RSS feed. Either way
        only the most recent uploads are returned (50 via the API, ~15 via the
        feed), which is plenty for a daily sync but not for fetching full history.
        """
        if self._api is not None:
            return self._fetch_latest_videos_from_api(channel_id, since)

        feed_url = FEED_URL.format(channel_id=channel_id)
        response = self._get(feed_url)
        if response.status_code != 200:
            raise RuntimeError(f"Fetching feed for channel {channel_id!r} failed with HTTP {response.status_code}")

        videos = []
        for entry in parse_feed_content(response.content, feed_url):
            published_at = entry_published_at(entry)
            if published_at is None or (since is not None and published_at < since):
                continue
            videos.append(
                ChannelVideo(
                    video_id=entry.yt_videoid,
                    channel_id=channel_id,
                    title=entry.title,
                    url=entry.link,
                    published_at=published_at,
                    description=getattr(entry, "summary", None) or "",
                )
            )
        return videos

    def _fetch_latest_videos_from_api(self, channel_id: str, since: datetime | None) -> list[ChannelVideo]:
        """List a channel's uploads playlist (its id is the channel id with UC swapped for UU), newest first."""
        uploads_playlist_id = "UU" + channel_id[2:]
        data = self._api_get("playlistItems", part="snippet,contentDetails", playlistId=uploads_playlist_id, maxResults=50)

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

    def get_transcript(self, video_id: str, languages: tuple[str, ...] = ("en",)) -> Transcript | None:
        """Return a video's transcript, or None if unavailable.

        Covers the common failure modes (captions disabled, no transcript in the
        requested language, video unavailable) by returning None rather than
        raising, since a missing transcript shouldn't block ingesting the video
        itself.
        """
        try:
            fetched = self._transcript_api.fetch(video_id, languages=list(languages))
        except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable):
            return None
        return Transcript(text=" ".join(snippet.text for snippet in fetched))

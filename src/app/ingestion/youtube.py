from __future__ import annotations

import re
from datetime import datetime, timezone
from types import TracebackType

import feedparser
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

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
_CHANNEL_ID_PATTERN = re.compile(r"^UC[\w-]{22}$")
_CANONICAL_CHANNEL_LINK_PATTERN = re.compile(
    r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"'
)
_USER_AGENT = "Mozilla/5.0"


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
    """Surface for resolving channels, fetching recent uploads, and pulling transcripts."""

    def __init__(self) -> None:
        self._http = httpx.Client(follow_redirects=True, headers={"User-Agent": _USER_AGENT}, timeout=10)

        proxy_config = None
        if settings.webshare_proxy_username and settings.webshare_proxy_password:
            proxy_config = WebshareProxyConfig(
                proxy_username=settings.webshare_proxy_username,
                proxy_password=settings.webshare_proxy_password,
            )
        self._transcript_api = YouTubeTranscriptApi(proxy_config=proxy_config)

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

        url = channel if channel.startswith("http") else f"https://www.youtube.com/{channel.lstrip('/')}"
        response = self._http.get(url)
        response.raise_for_status()

        match = _CANONICAL_CHANNEL_LINK_PATTERN.search(response.text)
        if not match:
            raise ValueError(f"Could not resolve a channel id for {channel!r}")
        return match.group(1)

    def fetch_latest_videos(self, channel_id: str, since: datetime | None = None) -> list[ChannelVideo]:
        """Fetch videos from a channel's RSS feed, optionally filtered to those published after `since`.

        The feed only ever contains the channel's most recent ~15 uploads, which
        is fine for a daily sync but means it can't be used to fetch full history.
        """
        feed = feedparser.parse(FEED_URL.format(channel_id=channel_id))

        status = getattr(feed, "status", None)
        if status is not None and status != 200:
            raise RuntimeError(f"Fetching feed for channel {channel_id!r} failed with HTTP {status}")
        if getattr(feed, "bozo", False):
            raise RuntimeError(f"Feed for channel {channel_id!r} could not be parsed: {feed.bozo_exception}")

        videos = []
        for entry in feed.entries:
            published_at = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            if since is not None and published_at < since:
                continue
            videos.append(
                ChannelVideo(
                    video_id=entry.yt_videoid,
                    channel_id=channel_id,
                    title=entry.title,
                    url=entry.link,
                    published_at=published_at,
                    description=entry.summary,
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

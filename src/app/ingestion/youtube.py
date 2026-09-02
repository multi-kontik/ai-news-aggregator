from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

import feedparser
import httpx

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
_CHANNEL_ID_PATTERN = re.compile(r"^UC[\w-]{22}$")
_CANONICAL_CHANNEL_LINK_PATTERN = re.compile(
    r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"'
)
_USER_AGENT = "Mozilla/5.0"


@dataclass
class VideoEntry:
    video_id: str
    channel_id: str
    title: str
    url: str
    published_at: datetime


def resolve_channel_id(channel: str) -> str:
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
    response = httpx.get(url, follow_redirects=True, headers={"User-Agent": _USER_AGENT}, timeout=10)
    response.raise_for_status()

    match = _CANONICAL_CHANNEL_LINK_PATTERN.search(response.text)
    if not match:
        raise ValueError(f"Could not resolve a channel id for {channel!r}")
    return match.group(1)


def fetch_latest_videos(channel_id: str, since: datetime | None = None) -> list[VideoEntry]:
    """Fetch videos from a channel's RSS feed, optionally filtered to those published after `since`.

    The feed only ever contains the channel's most recent ~15 uploads, which
    is fine for a daily sync but means it can't be used to fetch full history.
    """
    feed = feedparser.parse(FEED_URL.format(channel_id=channel_id))

    videos = []
    for entry in feed.entries:
        published_at = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        if since is not None and published_at < since:
            continue
        videos.append(
            VideoEntry(
                video_id=entry.yt_videoid,
                channel_id=channel_id,
                title=entry.title,
                url=entry.link,
                published_at=published_at,
            )
        )
    return videos

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from app.config import YOUTUBE_CHANNELS
from app.ingestion import anthropic_news, openai_news
from app.ingestion.feeds import Article
from app.ingestion.youtube import ChannelVideo, YouTubeClient


class IngestionError(BaseModel):
    source: str
    error: str


class IngestionResult(BaseModel):
    videos: list[ChannelVideo]
    articles: list[Article]
    errors: list[IngestionError]


def run_ingestion(hours: int = 24) -> IngestionResult:
    """Fetch everything published in the last `hours` from YouTube, OpenAI, and Anthropic.

    Each source is fetched independently: one source failing (e.g. a flaky feed)
    doesn't block the others, it's just reported in `errors`.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    videos: list[ChannelVideo] = []
    articles: list[Article] = []
    errors: list[IngestionError] = []

    with YouTubeClient() as client:
        for channel in YOUTUBE_CHANNELS:
            try:
                channel_id = client.resolve_channel_id(channel)
                videos.extend(client.fetch_latest_videos(channel_id, since=since))
            except Exception as e:
                errors.append(IngestionError(source=f"youtube:{channel}", error=str(e)))

    for source, fetch_latest_articles in [
        ("openai", openai_news.fetch_latest_articles),
        ("anthropic", anthropic_news.fetch_latest_articles),
    ]:
        try:
            articles.extend(fetch_latest_articles(since=since))
        except Exception as e:
            errors.append(IngestionError(source=source, error=str(e)))

    return IngestionResult(videos=videos, articles=articles, errors=errors)

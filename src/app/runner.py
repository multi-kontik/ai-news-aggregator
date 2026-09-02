from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from app.config import YOUTUBE_CHANNELS
from app.database.repository import AnthropicArticleRepository, OpenAIArticleRepository, YouTubeVideoRepository
from app.database.session import SessionLocal
from app.ingestion import anthropic_news, openai_news
from app.ingestion.feeds import Article, fetch_article_markdown
from app.ingestion.youtube import ChannelVideo, YouTubeClient


class IngestionError(BaseModel):
    source: str
    error: str


class IngestionResult(BaseModel):
    videos: list[ChannelVideo]
    articles: list[Article]
    errors: list[IngestionError]


def run_ingestion(hours: int = 24) -> IngestionResult:
    """Fetch everything published in the last `hours` from YouTube, OpenAI, and Anthropic, and store it.

    Each source is fetched and stored independently, into its own table: one
    source failing (e.g. a flaky feed) doesn't block the others, it's just
    reported in `errors`.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    videos: list[ChannelVideo] = []
    articles: list[Article] = []
    errors: list[IngestionError] = []

    with SessionLocal() as session:
        youtube_videos = YouTubeVideoRepository(session)

        with YouTubeClient() as client:
            for channel in YOUTUBE_CHANNELS:
                try:
                    channel_id = client.resolve_channel_id(channel)
                    channel_videos = client.fetch_latest_videos(channel_id, since=since)

                    for video in channel_videos:
                        youtube_videos.create(
                            channel=channel,
                            video_id=video.video_id,
                            title=video.title,
                            url=video.url,
                            published_at=video.published_at,
                            description=video.description,
                            transcript=video.transcript.text if video.transcript else None,
                        )

                    videos.extend(channel_videos)
                except Exception as e:
                    errors.append(IngestionError(source=f"youtube:{channel}", error=str(e)))

        for source_name, fetch_latest_articles, article_repo in [
            ("openai", openai_news.fetch_latest_articles, OpenAIArticleRepository(session)),
            ("anthropic", anthropic_news.fetch_latest_articles, AnthropicArticleRepository(session)),
        ]:
            try:
                fetched_articles = fetch_latest_articles(since=since)

                for article in fetched_articles:
                    article_repo.create(
                        url=article.url,
                        title=article.title,
                        published_at=article.published_at,
                        summary=article.summary,
                        category=article.category,
                    )

                articles.extend(fetched_articles)
            except Exception as e:
                errors.append(IngestionError(source=source_name, error=str(e)))

        session.commit()

    return IngestionResult(videos=videos, articles=articles, errors=errors)


class EnrichmentResult(BaseModel):
    videos_enriched: int
    articles_enriched: int
    errors: list[IngestionError]


def run_enrichment() -> EnrichmentResult:
    """Fill in transcripts (YouTube) and markdown content (OpenAI/Anthropic) for rows that don't have it yet.

    Each row is enriched independently: one failure (e.g. an IP block on
    transcript fetching) doesn't stop the rest, it's just reported in `errors`.
    """
    videos_enriched = 0
    articles_enriched = 0
    errors: list[IngestionError] = []

    with SessionLocal() as session:
        youtube_videos = YouTubeVideoRepository(session)

        with YouTubeClient() as client:
            for video in youtube_videos.list_missing("transcript"):
                try:
                    transcript = client.get_transcript(video.video_id)
                    video.transcript = transcript.text if transcript else None
                    videos_enriched += 1
                except Exception as e:
                    errors.append(IngestionError(source=f"youtube-transcript:{video.video_id}", error=str(e)))

        for source_name, article_repo in [
            ("openai", OpenAIArticleRepository(session)),
            ("anthropic", AnthropicArticleRepository(session)),
        ]:
            for article in article_repo.list_missing("content"):
                try:
                    article.content = fetch_article_markdown(article.url)
                    articles_enriched += 1
                except Exception as e:
                    errors.append(IngestionError(source=f"{source_name}-content:{article.url}", error=str(e)))

        session.commit()

    return EnrichmentResult(videos_enriched=videos_enriched, articles_enriched=articles_enriched, errors=errors)

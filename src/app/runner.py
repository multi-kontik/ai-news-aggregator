from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from app.agent.summarizer import SummarizerAgent
from app.config import YOUTUBE_CHANNELS
from app.database.repository import (
    AnthropicArticleRepository,
    DigestRepository,
    OpenAIArticleRepository,
    YouTubeVideoRepository,
)
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

    A video with no captions available is marked `transcript_unavailable`
    rather than left with a `None` transcript, so it isn't retried (and
    re-fail) on every future run. A raised error (e.g. a transient IP block)
    is not marked unavailable, so those *are* retried next time.
    """
    videos_enriched = 0
    articles_enriched = 0
    errors: list[IngestionError] = []

    with SessionLocal() as session:
        youtube_videos = YouTubeVideoRepository(session)

        with YouTubeClient() as client:
            for video in youtube_videos.list_needing_transcript():
                try:
                    transcript = client.get_transcript(video.video_id)
                    if transcript is None:
                        video.transcript_unavailable = True
                    else:
                        video.transcript = transcript.text
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


class DigestResult(BaseModel):
    digests_created: int
    errors: list[IngestionError]


def process_digest() -> DigestResult:
    """Generate a title + 2-3 sentence summary for every stored item that doesn't have one yet.

    Each item is summarized independently: one failed summary (e.g. a bad
    model response) doesn't stop the rest, it's just reported in `errors`.
    Falls back to whatever text is available (title/summary/description) when
    an item hasn't been through stage-2 enrichment yet, so this stage doesn't
    require enrichment to have succeeded first.
    """
    digests_created = 0
    errors: list[IngestionError] = []

    with SessionLocal() as session:
        digests = DigestRepository(session)
        agent = SummarizerAgent()

        for video in digests.list_youtube_videos_without_digest():
            try:
                content = video.transcript or video.description or video.title
                result = agent.summarize(title=video.title, url=video.url, source="YouTube", content=content)
                digests.create(youtube_video_id=video.id, url=video.url, title=result.title, summary=result.summary)
                digests_created += 1
            except Exception as e:
                errors.append(IngestionError(source=f"digest-youtube:{video.video_id}", error=str(e)))

        for article in digests.list_openai_articles_without_digest():
            try:
                content = article.content or article.summary or article.title
                result = agent.summarize(title=article.title, url=article.url, source="OpenAI", content=content)
                digests.create(openai_article_id=article.id, url=article.url, title=result.title, summary=result.summary)
                digests_created += 1
            except Exception as e:
                errors.append(IngestionError(source=f"digest-openai:{article.url}", error=str(e)))

        for article in digests.list_anthropic_articles_without_digest():
            try:
                content = article.content or article.summary or article.title
                result = agent.summarize(title=article.title, url=article.url, source="Anthropic", content=content)
                digests.create(anthropic_article_id=article.id, url=article.url, title=result.title, summary=result.summary)
                digests_created += 1
            except Exception as e:
                errors.append(IngestionError(source=f"digest-anthropic:{article.url}", error=str(e)))

        session.commit()

    return DigestResult(digests_created=digests_created, errors=errors)

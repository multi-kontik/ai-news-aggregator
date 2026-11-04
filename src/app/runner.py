from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone

from pydantic import BaseModel

from app.agent.editor import DigestCandidate, EditorAgent, RankedDigest
from app.agent.email_agent import EmailAgent
from app.agent.summarizer import SummarizerAgent
from app.config import YOUTUBE_CHANNELS
from app.database.repository import (
    AnthropicArticleRepository,
    DigestRepository,
    OpenAIArticleRepository,
    YouTubeVideoRepository,
)
from app.database.session import SessionLocal
from app.email_digest import DigestEmail, render_digest_email, send_email
from app.ingestion import anthropic_news, openai_news
from app.ingestion.feeds import Article, fetch_article_markdown
from app.ingestion.youtube import ChannelVideo, YouTubeClient
from app.profile import USER_PROFILE, UserProfile

logger = logging.getLogger(__name__)


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
    Each digest is committed as soon as it's created, so finished summaries
    show up in the table immediately and survive a crash or interrupt later
    in the run (only the in-flight item is lost).

    Falls back to whatever text is available (title/summary/description) when
    an item hasn't been through stage-2 enrichment yet, so this stage doesn't
    require enrichment to have succeeded first.
    """
    digests_created = 0
    errors: list[IngestionError] = []

    with SessionLocal() as session:
        digests = DigestRepository(session)
        agent = SummarizerAgent()

        videos = digests.list_youtube_videos_without_digest()
        openai_articles = digests.list_openai_articles_without_digest()
        anthropic_articles = digests.list_anthropic_articles_without_digest()
        logger.info(
            "Digest: %d item(s) need a summary (%d YouTube, %d OpenAI, %d Anthropic)",
            len(videos) + len(openai_articles) + len(anthropic_articles),
            len(videos), len(openai_articles), len(anthropic_articles),
        )

        for i, video in enumerate(videos, start=1):
            logger.info("[YouTube %d/%d] Summarizing %r (%s)", i, len(videos), video.title, video.url)
            started = time.perf_counter()
            try:
                content = video.transcript or video.description or video.title
                content_kind = "transcript" if video.transcript else "description" if video.description else "title only"
                logger.debug("[YouTube %d/%d] Using %s (%d chars)", i, len(videos), content_kind, len(content))
                result = agent.summarize(title=video.title, url=video.url, source="YouTube", content=content)
                digests.create(youtube_video_id=video.id, url=video.url, title=result.title, summary=result.summary)
                session.commit()
                digests_created += 1
                logger.info("[YouTube %d/%d] Done in %.1fs -> %r", i, len(videos), time.perf_counter() - started, result.title)
            except Exception as e:
                session.rollback()
                logger.exception("[YouTube %d/%d] Failed to summarize %s", i, len(videos), video.url)
                errors.append(IngestionError(source=f"digest-youtube:{video.video_id}", error=str(e)))

        for source_name, error_prefix, link_field, items in [
            ("OpenAI", "digest-openai", "openai_article_id", openai_articles),
            ("Anthropic", "digest-anthropic", "anthropic_article_id", anthropic_articles),
        ]:
            for i, article in enumerate(items, start=1):
                logger.info("[%s %d/%d] Summarizing %r (%s)", source_name, i, len(items), article.title, article.url)
                started = time.perf_counter()
                try:
                    content = article.content or article.summary or article.title
                    result = agent.summarize(title=article.title, url=article.url, source=source_name, content=content)
                    digests.create(**{link_field: article.id}, url=article.url, title=result.title, summary=result.summary)
                    session.commit()
                    digests_created += 1
                    logger.info(
                        "[%s %d/%d] Done in %.1fs -> %r",
                        source_name, i, len(items), time.perf_counter() - started, result.title,
                    )
                except Exception as e:
                    session.rollback()
                    logger.exception("[%s %d/%d] Failed to summarize %s", source_name, i, len(items), article.url)
                    errors.append(IngestionError(source=f"{error_prefix}:{article.url}", error=str(e)))

    logger.info("Digest: finished, %d created, %d error(s)", digests_created, len(errors))
    return DigestResult(digests_created=digests_created, errors=errors)


class RankingResult(BaseModel):
    ranked: list[RankedDigest]


def run_ranking(hours: int = 24, profile: UserProfile = USER_PROFILE) -> RankingResult:
    """Rank the digests created in the last `hours` by how relevant they are to `profile`.

    Unlike the earlier stages there's no per-item error isolation: the editor
    ranks all digests in a single model call, so it either succeeds or raises.
    Nothing is written to the database.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    with SessionLocal() as session:
        candidates = [
            DigestCandidate(
                id=digest.id,
                title=digest.title,
                summary=digest.summary,
                url=digest.url,
                source=digest.source,
                created_at=digest.created_at,
            )
            for digest in DigestRepository(session).list(since=since)
        ]

    logger.info("Ranking: %d digest(s) from the last %d hour(s)", len(candidates), hours)
    ranked = EditorAgent().rank(digests=candidates, profile=profile)
    logger.info("Ranking: finished, %d ranked", len(ranked))
    return RankingResult(ranked=ranked)


class EmailDigestResult(BaseModel):
    email: DigestEmail | None
    stories: list[RankedDigest]
    message_id: str | None


def run_email_digest(
    ranked: list[RankedDigest] | None = None,
    *,
    top_n: int = 10,
    hours: int = 24,
    profile: UserProfile = USER_PROFILE,
    day: date | None = None,
    send: bool = True,
) -> EmailDigestResult:
    """Build the daily email from the editor's top `top_n` stories, and send it.

    Pass the editor's output as `ranked`; if it's omitted, the last `hours`
    of digests are ranked first. With `send=False` the email is built but not
    sent (useful for previewing). If there are no stories, nothing is built or sent.
    """
    if ranked is None:
        ranked = run_ranking(hours=hours, profile=profile).ranked

    stories = sorted(ranked, key=lambda story: story.rank)[:top_n]
    if not stories:
        logger.info("Email: no stories to send")
        return EmailDigestResult(email=None, stories=[], message_id=None)

    logger.info("Email: writing the introduction for the top %d stor(ies)", len(stories))
    introduction = EmailAgent().write_introduction(stories=stories, profile=profile)
    email = render_digest_email(
        name=profile.name,
        day=day or datetime.now().date(),
        introduction=introduction.introduction,
        stories=stories,
    )

    message_id = None
    if send:
        message_id = send_email(email)
        logger.info("Email: sent %r (Resend id %s)", email.subject, message_id)
    return EmailDigestResult(email=email, stories=stories, message_id=message_id)

"""The pipeline's stages, each runnable on its own. `app.pipeline` chains them into the daily run.

1. run_ingestion:   fetch recent videos/articles from every source and store new ones
2. run_enrichment:  fill in transcripts (YouTube) and full markdown content (articles)
3. process_digest:  write a title + short summary for every item without one
4. run_ranking:     rank the not-yet-emailed stories against the user profile
5. run_email_digest: email the top stories and record them as sent

Stages 1-3 isolate failures per item: one bad feed, transcript or model
response is logged and reported in `errors` without stopping the rest. Every
item is committed as soon as it's done, so an interrupted run keeps its progress.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
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


class StageError(BaseModel):
    """One item or source that failed in a stage, e.g. source="youtube:@OpenAI"."""

    source: str
    error: str


# ---------------------------------------------------------------------------
# Stage 1: ingestion
# ---------------------------------------------------------------------------


class IngestionResult(BaseModel):
    videos: list[ChannelVideo]
    articles: list[Article]
    new_rows: int
    errors: list[StageError]


def run_ingestion(hours: int = 24) -> IngestionResult:
    """Fetch everything published in the last `hours` from YouTube, OpenAI, and Anthropic, and store it.

    Rows that are already stored are skipped, so overlapping windows are
    safe; a wider window just catches posts that show up in a feed late.
    `videos`/`articles` are everything fetched; `new_rows` is how many were new.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    videos: list[ChannelVideo] = []
    articles: list[Article] = []
    new_rows = 0
    errors: list[StageError] = []

    with SessionLocal() as session:
        youtube_videos = YouTubeVideoRepository(session)

        with YouTubeClient() as client:
            for channel in YOUTUBE_CHANNELS:
                try:
                    channel_id = client.resolve_channel_id(channel)
                    channel_videos = client.fetch_latest_videos(channel_id, since=since)
                    for video in channel_videos:
                        created = youtube_videos.create(
                            channel=channel,
                            video_id=video.video_id,
                            title=video.title,
                            url=video.url,
                            published_at=video.published_at,
                            description=video.description,
                            transcript=video.transcript.text if video.transcript else None,
                        )
                        new_rows += created is not None
                    session.commit()
                    videos.extend(channel_videos)
                    logger.info("Ingestion: youtube:%s -> %d video(s)", channel, len(channel_videos))
                except Exception as e:
                    session.rollback()
                    # Usually YouTube rate-limiting its feeds (HTTP 404/429/500), which clears up on a later run.
                    logger.warning("Ingestion: youtube:%s failed: %s", channel, e)
                    errors.append(StageError(source=f"youtube:{channel}", error=str(e)))

        for source_name, fetch_latest_articles, article_repo in [
            ("openai", openai_news.fetch_latest_articles, OpenAIArticleRepository(session)),
            ("anthropic", anthropic_news.fetch_latest_articles, AnthropicArticleRepository(session)),
        ]:
            try:
                fetched_articles = fetch_latest_articles(since=since)
                for article in fetched_articles:
                    created = article_repo.create(
                        url=article.url,
                        title=article.title,
                        published_at=article.published_at,
                        summary=article.summary,
                        category=article.category,
                    )
                    new_rows += created is not None
                session.commit()
                articles.extend(fetched_articles)
                logger.info("Ingestion: %s -> %d article(s)", source_name, len(fetched_articles))
            except Exception as e:
                session.rollback()
                logger.warning("Ingestion: %s failed: %s", source_name, e)
                errors.append(StageError(source=source_name, error=str(e)))

    logger.info("Ingestion: finished, %d new row(s), %d error(s)", new_rows, len(errors))
    return IngestionResult(videos=videos, articles=articles, new_rows=new_rows, errors=errors)


# ---------------------------------------------------------------------------
# Stage 2: enrichment
# ---------------------------------------------------------------------------


class EnrichmentResult(BaseModel):
    videos_enriched: int
    articles_enriched: int
    errors: list[StageError]


def run_enrichment() -> EnrichmentResult:
    """Fill in transcripts (YouTube) and markdown content (OpenAI/Anthropic) for rows that don't have it yet.

    A video with no captions available is marked `transcript_unavailable`
    rather than left with a `None` transcript, so it isn't retried (and
    re-fail) on every future run. A raised error (e.g. a transient IP block)
    is not marked unavailable, so those *are* retried next time.
    """
    videos_enriched = 0
    articles_enriched = 0
    errors: list[StageError] = []

    with SessionLocal() as session:
        with YouTubeClient() as client:
            pending_videos = YouTubeVideoRepository(session).list_needing_transcript()
            logger.info("Enrichment: %d video(s) need a transcript", len(pending_videos))
            for video in pending_videos:
                video_id = video.video_id
                try:
                    transcript = client.get_transcript(video_id)
                    if transcript is None:
                        video.transcript_unavailable = True
                    else:
                        video.transcript = transcript.text
                    session.commit()
                    videos_enriched += 1
                except Exception as e:
                    session.rollback()
                    logger.warning("Enrichment: transcript for %s failed: %s", video_id, e)
                    errors.append(StageError(source=f"youtube-transcript:{video_id}", error=str(e)))

        for source_name, article_repo in [
            ("openai", OpenAIArticleRepository(session)),
            ("anthropic", AnthropicArticleRepository(session)),
        ]:
            pending_articles = article_repo.list_missing("content")
            logger.info("Enrichment: %d %s article(s) need content", len(pending_articles), source_name)
            for article in pending_articles:
                url = article.url
                try:
                    article.content = fetch_article_markdown(url)
                    session.commit()
                    articles_enriched += 1
                except Exception as e:
                    session.rollback()
                    logger.warning("Enrichment: content for %s failed: %s", url, e)
                    errors.append(StageError(source=f"{source_name}-content:{url}", error=str(e)))

    logger.info(
        "Enrichment: finished, %d video(s) and %d article(s) enriched, %d error(s)",
        videos_enriched, articles_enriched, len(errors),
    )
    return EnrichmentResult(videos_enriched=videos_enriched, articles_enriched=articles_enriched, errors=errors)


# ---------------------------------------------------------------------------
# Stage 3: digests
# ---------------------------------------------------------------------------


class DigestResult(BaseModel):
    digests_created: int
    errors: list[StageError]


@dataclass
class _DigestWork:
    """One source row to summarize, with everything needed to save its digest."""

    source: str
    error_source: str
    link: dict[str, int]
    title: str
    url: str
    published_at: datetime
    content: str
    content_kind: str


def _digest_work(digests: DigestRepository) -> list[_DigestWork]:
    """Everything still missing a digest, using the richest text available for each.

    Falls back to whatever text exists (transcript > description > title for
    videos, content > summary > title for articles), so this stage doesn't
    require enrichment to have succeeded first.
    """
    work = [
        _DigestWork(
            source="YouTube",
            error_source=f"digest-youtube:{video.video_id}",
            link={"youtube_video_id": video.id},
            title=video.title,
            url=video.url,
            published_at=video.published_at,
            content=video.transcript or video.description or video.title,
            content_kind="transcript" if video.transcript else "description" if video.description else "title only",
        )
        for video in digests.list_youtube_videos_without_digest()
    ]
    for source, error_prefix, link_field, articles in [
        ("OpenAI", "digest-openai", "openai_article_id", digests.list_openai_articles_without_digest()),
        ("Anthropic", "digest-anthropic", "anthropic_article_id", digests.list_anthropic_articles_without_digest()),
    ]:
        work.extend(
            _DigestWork(
                source=source,
                error_source=f"{error_prefix}:{article.url}",
                link={link_field: article.id},
                title=article.title,
                url=article.url,
                published_at=article.published_at,
                content=article.content or article.summary or article.title,
                content_kind="content" if article.content else "feed summary" if article.summary else "title only",
            )
            for article in articles
        )
    return work


def process_digest() -> DigestResult:
    """Generate a title + 2-3 sentence summary for every stored item that doesn't have one yet."""
    digests_created = 0
    errors: list[StageError] = []

    with SessionLocal() as session:
        digests = DigestRepository(session)
        agent = SummarizerAgent()

        work = _digest_work(digests)
        totals = {source: sum(item.source == source for item in work) for source in ("YouTube", "OpenAI", "Anthropic")}
        logger.info(
            "Digest: %d item(s) need a summary (%d YouTube, %d OpenAI, %d Anthropic)",
            len(work), totals["YouTube"], totals["OpenAI"], totals["Anthropic"],
        )

        position = dict.fromkeys(totals, 0)
        for item in work:
            position[item.source] += 1
            label = f"[{item.source} {position[item.source]}/{totals[item.source]}]"
            logger.info("%s Summarizing %r (%s)", label, item.title, item.url)
            logger.debug("%s Using %s (%d chars)", label, item.content_kind, len(item.content))
            started = time.perf_counter()
            try:
                result = agent.summarize(title=item.title, url=item.url, source=item.source, content=item.content)
                digests.create(
                    **item.link, url=item.url, title=result.title, summary=result.summary, published_at=item.published_at,
                )
                session.commit()
                digests_created += 1
                logger.info("%s Done in %.1fs -> %r", label, time.perf_counter() - started, result.title)
            except Exception as e:
                session.rollback()
                logger.exception("%s Failed to summarize %s", label, item.url)
                errors.append(StageError(source=item.error_source, error=str(e)))

    logger.info("Digest: finished, %d created, %d error(s)", digests_created, len(errors))
    return DigestResult(digests_created=digests_created, errors=errors)


# ---------------------------------------------------------------------------
# Stage 4: ranking
# ---------------------------------------------------------------------------


class RankingResult(BaseModel):
    ranked: list[RankedDigest]


def run_ranking(hours: int = 48, profile: UserProfile = USER_PROFILE) -> RankingResult:
    """Rank the not-yet-emailed stories published in the last `hours` by relevance to `profile`.

    Digests that already went out in an email are excluded, so overlapping
    windows never resend a story. The editor ranks everything in one model
    call, so this stage either succeeds or raises. Nothing is written.
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
                published_at=digest.published_at,
            )
            for digest in DigestRepository(session).list_unsent(since=since)
        ]

    logger.info("Ranking: %d unsent stor(ies) published in the last %d hour(s)", len(candidates), hours)
    ranked = EditorAgent().rank(digests=candidates, profile=profile)
    logger.info("Ranking: finished, %d ranked", len(ranked))
    return RankingResult(ranked=ranked)


# ---------------------------------------------------------------------------
# Stage 5: email
# ---------------------------------------------------------------------------


class EmailDigestResult(BaseModel):
    email: DigestEmail | None
    stories: list[RankedDigest]
    message_id: str | None


def run_email_digest(
    ranked: list[RankedDigest] | None = None,
    *,
    top_n: int = 10,
    hours: int = 48,
    profile: UserProfile = USER_PROFILE,
    day: date | None = None,
    send: bool = True,
) -> EmailDigestResult:
    """Build the daily email from the editor's top `top_n` stories, send it, and record them as sent.

    Pass the editor's output as `ranked`; if it's omitted, unsent stories
    published in the last `hours` are ranked first. With `send=False` the email is built
    but not sent or recorded (useful for previewing). With no stories, nothing
    is built or sent.
    """
    if ranked is None:
        ranked = run_ranking(hours=hours, profile=profile).ranked

    stories = sorted(ranked, key=lambda story: story.rank)[:top_n]
    if not stories:
        logger.info("Email: no new stories, nothing to send")
        return EmailDigestResult(email=None, stories=[], message_id=None)

    logger.info("Email: writing the introduction for the top %d stor(ies)", len(stories))
    introduction = EmailAgent().write_introduction(stories=stories, profile=profile)
    email = render_digest_email(
        name=profile.name,
        day=day or datetime.now().date(),
        introduction=introduction.introduction,
        stories=stories,
    )
    if not send:
        return EmailDigestResult(email=email, stories=stories, message_id=None)

    message_id = send_email(email)
    logger.info("Email: sent %r (Resend id %s)", email.subject, message_id)
    with SessionLocal() as session:
        DigestRepository(session).mark_sent([story.digest_id for story in stories], message_id)
        session.commit()
    return EmailDigestResult(email=email, stories=stories, message_id=message_id)

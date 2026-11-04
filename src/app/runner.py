"""The pipeline's stages, each runnable on its own. `app.pipeline` chains them into the daily run.

1. run_ingestion:    fetch recent items from every source and store the new ones
2. run_enrichment:   fill in each stored item's full text (transcripts, article markdown)
3. process_digest:   write a title + short summary for every item without one
4. run_ranking:      rank the not-yet-emailed stories against the user profile
5. run_email_digest: email the top stories and record them as sent

Stages 1-3 work through `app.ingestion.SOURCES` and know nothing about any
particular source. They isolate failures: one bad feed, channel, transcript or
model response is logged and reported in `errors` without stopping the rest.
Every item is committed as soon as it's done, so an interrupted run keeps its progress.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.editor import DigestCandidate, EditorAgent, RankedDigest
from app.agent.email_agent import EmailAgent
from app.agent.summarizer import SummarizerAgent
from app.database.repository import DigestRepository, Repository
from app.database.session import SessionLocal
from app.email_digest import DigestEmail, render_digest_email, send_email
from app.ingestion import SOURCES, Source
from app.profile import USER_PROFILE, UserProfile

logger = logging.getLogger(__name__)


class StageError(BaseModel):
    """One unit or item that failed in a stage, labelled stage:source:item, e.g. "ingest:youtube:@OpenAI"."""

    source: str
    error: str


class StageResult(BaseModel):
    """What stages 1-3 report: how many items they handled (new rows, enriched, digested) and what failed."""

    count: int
    errors: list[StageError]


def _fail(errors: list[StageError], session: Session, label: str, error: Exception) -> None:
    """Undo the failed item's changes, log it, and record it, so the stage can carry on with the next one."""
    session.rollback()
    logger.warning("%s failed: %s", label, error)
    errors.append(StageError(source=label, error=str(error)))


def run_ingestion(hours: int = 48, sources: Sequence[Source] = SOURCES) -> StageResult:
    """Fetch everything published in the last `hours` from every source, and store what's new.

    Stored items are skipped, so overlapping windows are safe; a wider window
    just catches posts that show up in a feed late. Each source unit (one feed,
    one channel) is fetched and committed on its own. `count` is the new rows.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    new_rows = 0
    errors: list[StageError] = []

    with SessionLocal() as session:
        for source in sources:
            repo = Repository(session, source.model, source.unique_field)
            with source:
                for unit in source.units():
                    label = f"ingest:{source.key}:{unit}"
                    try:
                        items = source.fetch(unit, since)
                        added = sum(repo.create(**fields) is not None for fields in items)
                        session.commit()
                        new_rows += added
                        logger.info("Ingestion: %s:%s -> %d item(s), %d new", source.key, unit, len(items), added)
                    except Exception as e:
                        _fail(errors, session, label, e)

    logger.info("Ingestion: finished, %d new row(s), %d error(s)", new_rows, len(errors))
    return StageResult(count=new_rows, errors=errors)


def run_enrichment(sources: Sequence[Source] = SOURCES) -> StageResult:
    """Fill in the full text (transcript, article content) of stored items that don't have it yet.

    A failure (e.g. an IP block or a 403) leaves the item as it was, so it's
    retried next run. `count` is the items enriched.
    """
    enriched = 0
    errors: list[StageError] = []

    with SessionLocal() as session:
        for source in sources:
            pending = list(session.scalars(select(source.model).where(source.needs_enrichment())))
            if not pending:
                continue
            logger.info("Enrichment: %d %s item(s) to enrich", len(pending), source.key)
            with source:
                for row in pending:
                    label = f"enrich:{source.key}:{getattr(row, source.unique_field)}"
                    try:
                        source.enrich(row)
                        session.commit()
                        enriched += 1
                    except Exception as e:
                        _fail(errors, session, label, e)

    logger.info("Enrichment: finished, %d item(s) enriched, %d error(s)", enriched, len(errors))
    return StageResult(count=enriched, errors=errors)


def process_digest(sources: Sequence[Source] = SOURCES) -> StageResult:
    """Write a title + 2-3 sentence summary for every stored item that doesn't have a digest yet.

    Each item is summarized from the richest text it has (see
    `Source.digest_content`), so this stage works even if enrichment failed.
    `count` is the digests created.
    """
    created = 0
    errors: list[StageError] = []

    with SessionLocal() as session:
        digests = DigestRepository(session)
        work = [(source, row) for source in sources for row in digests.list_without_digest(source.model, source.digest_link)]
        totals = Counter(source.name for source, _ in work)
        logger.info(
            "Digest: %d item(s) need a summary (%s)",
            len(work), ", ".join(f"{totals[s.name]} {s.name}" for s in sources) or "no sources",
        )

        agent = SummarizerAgent()
        position: Counter[str] = Counter()
        for source, row in work:
            position[source.name] += 1
            tag = f"[{source.name} {position[source.name]}/{totals[source.name]}]"
            label = f"digest:{source.key}:{getattr(row, source.unique_field)}"
            content, kind = source.digest_content(row)
            logger.info("%s Summarizing %r (%s)", tag, row.title, row.url)
            logger.debug("%s Using %s (%d chars)", tag, kind, len(content))
            started = time.perf_counter()
            try:
                result = agent.summarize(title=row.title, url=row.url, source=source.name, content=content)
                digests.create(
                    url=row.url, title=result.title, summary=result.summary, published_at=row.published_at,
                    **{source.digest_link: row.id},
                )
                session.commit()
                created += 1
                logger.info("%s Done in %.1fs -> %r", tag, time.perf_counter() - started, result.title)
            except Exception as e:
                _fail(errors, session, label, e)

    logger.info("Digest: finished, %d created, %d error(s)", created, len(errors))
    return StageResult(count=created, errors=errors)


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

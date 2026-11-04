"""The daily run: ingest -> enrich -> summarize -> rank -> email, in one call.

Built to be triggered once a day by a scheduler (cron, a Render cron job,
etc.) as a one-shot command: `uv run ai-news run`. Every stage is safe to
re-run, so a failed or repeated run never duplicates rows or resends a story.
"""

from __future__ import annotations

import logging
import time

from pydantic import BaseModel

from app.config import settings
from app.database.session import create_tables
from app.profile import USER_PROFILE, UserProfile
from app.runner import (
    EmailDigestResult,
    StageError,
    StageResult,
    process_digest,
    run_email_digest,
    run_enrichment,
    run_ingestion,
)

logger = logging.getLogger(__name__)

# Ingestion looks back further than a day on purpose: stored rows are
# deduplicated, so the overlap is free, and it catches posts that appear in a
# feed hours after their publish date (or a day the run didn't happen).
DEFAULT_INGEST_HOURS = 48
# Stories are picked for the email by their publish date. The window matches
# ingestion so a post that reached its feed late (or on a day the run didn't
# happen) still makes it in; already-emailed stories are excluded, so the
# overlap never causes a repeat.
DEFAULT_DIGEST_HOURS = 48


class PipelineResult(BaseModel):
    ingestion: StageResult
    enrichment: StageResult
    digest: StageResult
    email: EmailDigestResult

    @property
    def errors(self) -> list[StageError]:
        """Per-item errors from every stage. These didn't stop the run, but are worth a look."""
        return [*self.ingestion.errors, *self.enrichment.errors, *self.digest.errors]


def check_config(*, send: bool) -> None:
    """Fail before doing any work if a setting the run needs is missing."""
    missing = [name for name, value in [("OPENAI_API_KEY", settings.openai_api_key)] if not value]
    if send:
        missing += [
            name
            for name, value in [("RESEND_API_KEY", settings.resend_api_key), ("EMAIL_TO", settings.email_to)]
            if not value
        ]
    if missing:
        raise ValueError(f"Missing required setting(s): {', '.join(missing)}")


def run_pipeline(
    *,
    ingest_hours: int = DEFAULT_INGEST_HOURS,
    digest_hours: int = DEFAULT_DIGEST_HOURS,
    top_n: int = 10,
    send: bool = True,
    profile: UserProfile = USER_PROFILE,
) -> PipelineResult:
    """Run every stage in order and email the top `top_n` new stories.

    Per-item failures (one feed, transcript or summary) are collected in
    `errors` and don't stop the run. A failure that affects a whole stage
    (database down, OpenAI or Resend rejecting every call) raises.
    """
    check_config(send=send)
    started = time.perf_counter()

    create_tables()
    ingestion = run_ingestion(hours=ingest_hours)
    enrichment = run_enrichment()
    digest = process_digest()
    email = run_email_digest(top_n=top_n, hours=digest_hours, profile=profile, send=send)

    result = PipelineResult(ingestion=ingestion, enrichment=enrichment, digest=digest, email=email)
    logger.info(
        "Pipeline: finished in %.0fs: %d new item(s), %d digest(s), %d stor(ies) emailed, %d error(s)",
        time.perf_counter() - started,
        ingestion.count,
        digest.count,
        len(email.stories) if email.message_id else 0,
        len(result.errors),
    )
    return result

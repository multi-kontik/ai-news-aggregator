"""Command-line entry point: `uv run ai-news <command>`.

`run` is the full daily pipeline, meant for a scheduler. The other commands
run one stage at a time, for debugging or backfilling.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import typer

from app.runner import EmailDigestResult, StageError

app = typer.Typer(help="AI news aggregator: ingest, summarize, rank and email the daily AI news digest.", no_args_is_help=True)

DryRun = Annotated[bool, typer.Option("--dry-run", help="Build the email but don't send it (or mark stories as sent).")]
Preview = Annotated[Path | None, typer.Option(help="Also write the email's HTML to this file.")]
Top = Annotated[int, typer.Option("--top", help="How many stories to include in the email.")]

logger = logging.getLogger("app.cli")


@app.callback()
def _configure_logging(verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show debug logs.")] = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Keep per-request HTTP logs out of the way of the per-item progress.
    for noisy in ("httpx", "httpcore", "openai", "docling"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@app.command()
def run(
    ingest_hours: Annotated[int, typer.Option(help="Fetch items published in the last N hours.")] = 48,
    digest_hours: Annotated[int, typer.Option(help="Email unsent stories published in the last N hours.")] = 48,
    top: Top = 10,
    dry_run: DryRun = False,
    preview: Preview = None,
) -> None:
    """Run the whole daily pipeline: ingest, enrich, summarize, rank and email."""
    from app.pipeline import run_pipeline

    try:
        result = run_pipeline(ingest_hours=ingest_hours, digest_hours=digest_hours, top_n=top, send=not dry_run)
    except Exception:
        logger.exception("Pipeline failed")
        raise typer.Exit(1)

    _report_errors(result.errors)
    _report_email(result.email, preview)


@app.command("init-db")
def init_db() -> None:
    """Create any database tables that don't exist yet."""
    from app.database.session import create_tables

    typer.echo(f"Tables: {', '.join(create_tables())}")


@app.command()
def ingest(hours: Annotated[int, typer.Option(help="Fetch items published in the last N hours.")] = 24) -> None:
    """Stage 1: fetch recent videos and articles and store the new ones."""
    from app.runner import run_ingestion

    result = run_ingestion(hours=hours)
    typer.echo(f"{len(result.videos)} video(s) and {len(result.articles)} article(s) fetched, {result.new_rows} new")
    _report_errors(result.errors)


@app.command()
def enrich() -> None:
    """Stage 2: fill in missing transcripts and article content."""
    from app.runner import run_enrichment

    result = run_enrichment()
    typer.echo(f"{result.videos_enriched} video(s) and {result.articles_enriched} article(s) enriched")
    _report_errors(result.errors)


@app.command()
def digest() -> None:
    """Stage 3: summarize every stored item that doesn't have a digest yet."""
    from app.runner import process_digest

    result = process_digest()
    typer.echo(f"{result.digests_created} digest(s) created")
    _report_errors(result.errors)


@app.command()
def rank(hours: Annotated[int, typer.Option(help="Rank unsent stories published in the last N hours.")] = 48) -> None:
    """Stage 4: rank unsent digests against the user profile (read-only)."""
    from app.runner import run_ranking

    ranked = run_ranking(hours=hours).ranked
    if not ranked:
        typer.echo(f"No unsent stories published in the last {hours} hour(s) to rank.")
    for item in ranked:
        typer.echo(f"\n#{item.rank}  [{item.relevance_score}/10]  {item.title}  ({item.source})")
        typer.echo(f"    {item.url}")
        typer.echo(f"    Why: {item.reasoning}")


@app.command()
def send(
    hours: Annotated[int, typer.Option(help="Email unsent stories published in the last N hours.")] = 48,
    top: Top = 10,
    dry_run: DryRun = False,
    preview: Preview = None,
) -> None:
    """Stages 4-5: rank unsent digests and email the top stories."""
    from app.runner import run_email_digest

    _report_email(run_email_digest(top_n=top, hours=hours, send=not dry_run), preview)


def _report_errors(errors: list[StageError]) -> None:
    if errors:
        typer.echo(f"\n{len(errors)} error(s):")
        for error in errors:
            typer.echo(f"  - {error.source}: {error.error}")


def _report_email(result: EmailDigestResult, preview: Path | None) -> None:
    if result.email is None:
        typer.echo("No new stories, so no email was built.")
        return
    typer.echo(f"\nSubject: {result.email.subject}\n\n{result.email.text}")
    if preview:
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_text(result.email.html)
        typer.echo(f"HTML preview written to {preview}")
    typer.echo(f"Sent, Resend id {result.message_id}" if result.message_id else "Not sent (dry run).")


def main() -> None:
    app()


if __name__ == "__main__":
    main()

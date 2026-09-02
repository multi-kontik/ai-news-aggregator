"""Manual smoke test for stage 2: filling in transcripts/content for rows already in the DB.

Run with: uv run python scripts/run_enrichment.py
"""

from app.runner import run_enrichment

if __name__ == "__main__":
    result = run_enrichment()

    print(f"{result.videos_enriched} video(s) enriched with a transcript")
    print(f"{result.articles_enriched} article(s) enriched with content")

    print(f"\n{len(result.errors)} error(s)")
    for error in result.errors:
        print(f"  - {error.source}: {error.error}")

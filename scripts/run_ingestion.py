"""Manual smoke test for the combined ingestion runner (no DB involved yet).

Run with: uv run python scripts/run_ingestion.py
"""

from app.runner import run_ingestion

if __name__ == "__main__":
    result = run_ingestion(hours=24)

    print(f"{len(result.videos)} video(s)")
    for video in result.videos:
        print(f"  - [{video.published_at:%Y-%m-%d}] {video.title} ({video.url})")

    print(f"\n{len(result.articles)} article(s)")
    for article in result.articles:
        print(f"  - [{article.published_at:%Y-%m-%d}] {article.title} ({article.url})")

    print(f"\n{len(result.errors)} error(s)")
    for error in result.errors:
        print(f"  - {error.source}: {error.error}")

"""Manual smoke test for stage 3: generating title/summary digests for rows already in the DB.

Requires OPENAI_API_KEY to be set. Run with: uv run python scripts/process_digest.py
"""

from app.runner import process_digest

if __name__ == "__main__":
    result = process_digest()

    print(f"{result.digests_created} digest(s) created")

    print(f"\n{len(result.errors)} error(s)")
    for error in result.errors:
        print(f"  - {error.source}: {error.error}")

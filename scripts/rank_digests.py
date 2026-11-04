"""Manual smoke test for stage 4: ranking the last 24 hours of digests against the user profile.

Requires OPENAI_API_KEY to be set. Edit the profile in src/app/profile.py.
Run with: uv run python scripts/rank_digests.py [hours]
"""

import logging
import sys

from app.runner import run_ranking

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    result = run_ranking(hours=hours)

    if not result.ranked:
        print(f"No digests from the last {hours} hour(s) to rank.")
    for item in result.ranked:
        print(f"\n#{item.rank}  [{item.relevance_score}/10]  {item.title}  ({item.source})")
        print(f"    {item.url}")
        print(f"    Why: {item.reasoning}")

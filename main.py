"""Run the daily pipeline: ingest, enrich, summarize, rank and email the digest.

This is what the server's schedule runs. It's the same as `ai-news run`, and
takes the same options, e.g. `python main.py --dry-run --top 5`.
"""

import sys

from app.cli import main

if __name__ == "__main__":
    sys.argv = [sys.argv[0], "run", *sys.argv[1:]]
    main()

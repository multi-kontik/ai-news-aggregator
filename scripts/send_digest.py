"""Stages 4 + 5: rank the last 24 hours of digests and email the top 10.

Requires OPENAI_API_KEY, plus RESEND_API_KEY and EMAIL_TO to actually send.
Run with: uv run python scripts/send_digest.py
Preview without sending: uv run python scripts/send_digest.py --dry-run --preview digest.html
"""

import argparse
import logging
from pathlib import Path

from app.runner import run_email_digest

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hours", type=int, default=24, help="rank digests created in the last N hours")
    parser.add_argument("--top", type=int, default=10, help="how many stories to include")
    parser.add_argument("--dry-run", action="store_true", help="build the email but don't send it")
    parser.add_argument("--preview", type=Path, help="also write the email's HTML to this file")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    result = run_email_digest(top_n=args.top, hours=args.hours, send=not args.dry_run)

    if result.email is None:
        print(f"No digests from the last {args.hours} hour(s), so no email was built.")
        raise SystemExit(0)

    print(f"Subject: {result.email.subject}\n")
    print(result.email.text)
    if args.preview:
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        args.preview.write_text(result.email.html)
        print(f"HTML preview written to {args.preview}")
    print(f"Sent, Resend id {result.message_id}" if result.message_id else "Not sent (dry run).")

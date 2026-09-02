"""Manual smoke test for the YouTube ingestion surface (no DB involved yet).

Run with: uv run python scripts/try_youtube.py
"""

from datetime import datetime, timedelta, timezone

from app.ingestion.transcripts import get_transcript
from app.ingestion.youtube import fetch_latest_videos, resolve_channel_id

CHANNELS = [
    "@anthropic-ai",
    "@OpenAI",
]
WINDOW_HOURS = 24 * 60  # widened for testing since real channels don't post daily

if __name__ == "__main__":
    since = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)

    for channel in CHANNELS:
        channel_id = resolve_channel_id(channel)
        print(f"\n{channel} -> {channel_id}")

        videos = fetch_latest_videos(channel_id, since=since)
        print(f"  {len(videos)} video(s) since {since:%Y-%m-%d}")

        for video in videos:
            print(f"  - [{video.published_at:%Y-%m-%d}] {video.title} ({video.url})")
            transcript = get_transcript(video.video_id)
            if transcript is None:
                print("    transcript: unavailable")
            else:
                preview = transcript[:150].replace("\n", " ")
                print(f"    transcript ({len(transcript)} chars): {preview}...")

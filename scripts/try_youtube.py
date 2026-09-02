"""Manual smoke test for the YouTube ingestion surface (no DB involved yet).

Run with: uv run python scripts/try_youtube.py
"""

from datetime import datetime, timedelta, timezone

from app.ingestion.youtube import YouTubeClient

CHANNELS = [
    "@anthropic-ai",
    "@OpenAI",
]
WINDOW_HOURS = 24 * 60  # widened for testing since real channels don't post daily

if __name__ == "__main__":
    since = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)

    with YouTubeClient() as client:
        for channel in CHANNELS:
            channel_id = client.resolve_channel_id(channel)
            print(f"\n{channel} -> {channel_id}")

            videos = client.fetch_latest_videos(channel_id, since=since)
            print(f"  {len(videos)} video(s) since {since:%Y-%m-%d}")

            for video in videos:
                print(f"  - [{video.published_at:%Y-%m-%d}] {video.title} ({video.url})")
                transcript = client.get_transcript(video.video_id)
                if transcript is None:
                    print("    transcript: unavailable")
                else:
                    preview = transcript.text[:150].replace("\n", " ")
                    print(f"    transcript ({len(transcript.text)} chars): {preview}...")

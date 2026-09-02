from __future__ import annotations

from youtube_transcript_api import YouTubeTranscriptApi, YouTubeTranscriptApiException

_api = YouTubeTranscriptApi()


def get_transcript(video_id: str, languages: tuple[str, ...] = ("en",)) -> str | None:
    """Return a video's transcript as plain text, or None if unavailable.

    Covers the common failure modes (captions disabled, no transcript in the
    requested language, video unavailable) by returning None rather than
    raising, since a missing transcript shouldn't block ingesting the video
    itself.
    """
    try:
        fetched = _api.fetch(video_id, languages=list(languages))
    except YouTubeTranscriptApiException:
        return None
    return " ".join(snippet.text for snippet in fetched)

from __future__ import annotations

from openai import OpenAI
from pydantic import BaseModel

_MODEL = "gpt-4.1-mini"
_INSTRUCTIONS = (
    "You are a summarization assistant for an AI news digest read by a technical "
    "audience following AI industry developments.\n\n"
    "Given the title, source, and content of an article or video transcript, write:\n"
    "- title: a short, clear, factual headline (rewrite the original if it's vague or clickbait)\n"
    "- summary: exactly 2-3 sentences capturing the most newsworthy, concrete points\n\n"
    "Be neutral and factual. Do not add opinions, hype, or speculation beyond what's in the source."
)


class DigestSummary(BaseModel):
    title: str
    summary: str


class SummarizerAgent:
    """Generates a short digest title/summary for a piece of content via OpenAI's Responses API."""

    def __init__(self) -> None:
        self._client = OpenAI()

    def summarize(self, *, title: str, url: str, source: str, content: str) -> DigestSummary:
        input_text = f"Source: {source}\nTitle: {title}\nURL: {url}\n\nContent:\n{content}"
        response = self._client.responses.parse(
            model=_MODEL,
            instructions=_INSTRUCTIONS,
            input=input_text,
            text_format=DigestSummary,
        )
        if response.output_parsed is None:
            raise ValueError(f"Model did not return a parsed digest summary for {url!r}")
        return response.output_parsed

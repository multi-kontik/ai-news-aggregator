"""Writes the personal introduction at the top of the daily digest email.

The greeting line above it ("Hey Aaron, this is your daily digest...") is part
of the template in `app.email_digest`, so the name and date are always exactly
right. The model only writes the short overview that follows it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agent.base import StructuredAgent
from app.agent.editor import RankedDigest
from app.profile import UserProfile

_INSTRUCTIONS = (
    "You write the opening of a personalized daily AI news email. The reader has "
    "already been greeted by name; you write the short overview that comes right "
    "after the greeting.\n\n"
    "You are given the reader's profile and today's top stories, already ranked "
    "from most to least relevant for them. Write:\n"
    "- introduction: 2-3 short sentences, under 70 words in total, previewing what's in "
    "today's email. Lead with the top one or two stories in a few words each, then name "
    "the main themes of the rest. This is a teaser, not a summary: each story has its "
    "own summary further down.\n\n"
    "Rules:\n"
    "- Do not greet the reader or repeat their name or the date; that's already done.\n"
    "- Only mention stories from the list. Do not add facts that aren't in the summaries.\n"
    "- Be warm but concise and factual, like a sharp colleague's heads-up. No hype, "
    "no emojis, no sign-off."
)


class EmailIntroduction(BaseModel):
    introduction: str = Field(description="2-3 sentence teaser of today's stories, shown after the greeting.")


class EmailAgent(StructuredAgent):
    """Writes the email introduction for the day's top stories via OpenAI's Responses API."""

    def write_introduction(self, *, stories: list[RankedDigest], profile: UserProfile) -> EmailIntroduction:
        if not stories:
            raise ValueError("Cannot write an introduction without any stories")

        story_list = "\n\n".join(
            f"{story.rank}. {story.title} ({story.source})\n{story.summary}" for story in stories
        )
        input_text = (
            f"Today's top {len(stories)} stories, most relevant first:\n\n{story_list}\n\n---\n\n"
            f"Reader profile:\n\n{profile.to_prompt()}"
        )
        return self._parse(
            instructions=_INSTRUCTIONS,
            input=input_text,
            text_format=EmailIntroduction,
            what="email introduction",
        )

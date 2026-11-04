"""The editor: decides which of the day's digests matter most to the reader, and in what order.

How the ranking works:

1. Listwise scoring. All candidate digests go to the model in one call, so it
   can judge each story relative to the rest of the day's news (and spot
   near-duplicates) instead of scoring each one in isolation.
2. Anchored rubric. Each story gets an integer 0-10 relevance score against a
   fixed rubric, which keeps scores comparable across runs and stops the model
   from bunching everything at 7-8.
3. Reason first. The output schema puts `reasoning` before `relevance_score`,
   so the model commits to why a story does or doesn't fit the profile before
   it picks a number.
4. Code does the sorting. The model only scores; ordering, tie-breaking and
   rank numbers are computed here, so the final order is deterministic and any
   ids the model hallucinated, duplicated or skipped are handled explicitly.
"""

from __future__ import annotations

import logging
from datetime import datetime

from pydantic import BaseModel, Field

from app.agent.base import StructuredAgent
from app.profile import UserProfile

logger = logging.getLogger(__name__)

_INSTRUCTIONS = (
    "You are the editor of a personalized daily AI news digest. Your job is to decide "
    "how relevant each of today's stories is to one specific reader, whose profile is "
    "given below the stories.\n\n"
    "For every story, in the order given, return:\n"
    "- digest_id: the story's id, copied exactly\n"
    "- reasoning: one or two sentences on how the story relates to this reader's "
    "background, interests and dislikes. Name the specific interest it matches, or why it misses.\n"
    "- relevance_score: an integer from 0 to 10, using this rubric:\n"
    "  9-10: directly about one of the reader's top interests, and substantial or actionable "
    "(a new model, API or technique they could use now)\n"
    "  7-8: clearly matches an interest, but less central, less new or less concrete\n"
    "  4-6: tangentially related, or relevant but thin on technical substance\n"
    "  1-3: weak connection; mostly falls under what the reader is less interested in\n"
    "  0: no connection to the reader at all\n\n"
    "Rules:\n"
    "- Score every story exactly once. Do not invent, merge or skip ids.\n"
    "- Score each story on its own merits against the rubric, and use the full range: "
    "a typical day should have only a few stories at 9-10.\n"
    "- Judge substance, not framing. A hyped headline with no concrete detail scores lower.\n"
    "- If two stories cover the same news, score the more informative one higher and "
    "mark the other down as redundant in its reasoning."
)


class DigestCandidate(BaseModel):
    """One digest offered to the editor for ranking."""

    id: int
    title: str
    summary: str
    url: str
    source: str
    created_at: datetime


class DigestScore(BaseModel):
    """The model's verdict on one digest. Field order matters: reasoning comes before the score."""

    digest_id: int
    reasoning: str
    relevance_score: int = Field(ge=0, le=10)


class EditorScores(BaseModel):
    """The model's raw structured output: one score per candidate."""

    scores: list[DigestScore]


class RankedDigest(BaseModel):
    """One digest in the final, ranked order. `rank` 1 is the most relevant."""

    rank: int
    digest_id: int
    title: str
    summary: str
    url: str
    source: str
    relevance_score: int
    reasoning: str


_UNSCORED_REASONING = "Not scored by the model, so ranked last."


class EditorAgent(StructuredAgent):
    """Ranks digests by relevance to a user profile via OpenAI's Responses API."""

    def rank(self, *, digests: list[DigestCandidate], profile: UserProfile) -> list[RankedDigest]:
        if not digests:
            return []

        output = self._parse(
            instructions=_INSTRUCTIONS,
            input=_build_input(digests, profile),
            text_format=EditorScores,
            what="set of editor scores",
        )
        return _rank(digests, output.scores)


def _build_input(digests: list[DigestCandidate], profile: UserProfile) -> str:
    stories = "\n\n".join(
        f"[digest_id={d.id}] ({d.source})\nTitle: {d.title}\nSummary: {d.summary}" for d in digests
    )
    return f"Today's stories ({len(digests)}):\n\n{stories}\n\n---\n\nReader profile:\n\n{profile.to_prompt()}"


def _rank(digests: list[DigestCandidate], scores: list[DigestScore]) -> list[RankedDigest]:
    """Turn the model's scores into a validated, deterministic ranking.

    Ids the model made up are dropped, a repeated id keeps its first score,
    and any digest the model skipped is kept but placed last with score 0.
    Ties are broken by the newest digest first, then by id.
    """
    by_id = {d.id: d for d in digests}
    score_by_id: dict[int, DigestScore] = {}
    for score in scores:
        if score.digest_id not in by_id:
            logger.warning("Editor returned a score for unknown digest id %d; ignoring it", score.digest_id)
        elif score.digest_id in score_by_id:
            logger.warning("Editor scored digest id %d more than once; keeping the first score", score.digest_id)
        else:
            score_by_id[score.digest_id] = score

    missing = [d.id for d in digests if d.id not in score_by_id]
    if missing:
        logger.warning("Editor did not score digest id(s) %s; ranking them last", missing)

    def sort_key(d: DigestCandidate) -> tuple[bool, int, float, int]:
        score = score_by_id.get(d.id)
        return (score is None, -(score.relevance_score if score else 0), -d.created_at.timestamp(), d.id)

    ranked: list[RankedDigest] = []
    for rank, digest in enumerate(sorted(digests, key=sort_key), start=1):
        score = score_by_id.get(digest.id)
        ranked.append(
            RankedDigest(
                rank=rank,
                digest_id=digest.id,
                title=digest.title,
                summary=digest.summary,
                url=digest.url,
                source=digest.source,
                relevance_score=score.relevance_score if score else 0,
                reasoning=score.reasoning if score else _UNSCORED_REASONING,
            )
        )
    return ranked

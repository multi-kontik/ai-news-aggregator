from __future__ import annotations

import types
from datetime import datetime, timezone

import pytest

from app.agent.editor import DigestCandidate, DigestScore, EditorAgent, EditorScores
from app.config import settings
from app.profile import UserProfile

PROFILE = UserProfile(
    name="Aaron",
    background="Builds LLM agents in Python.",
    interests=["agent tool use"],
    less_interested_in=["office openings"],
    preferences=["technical depth"],
)


def _candidate(id: int, hour: int = 0, title: str | None = None) -> DigestCandidate:
    return DigestCandidate(
        id=id,
        title=title or f"Story {id}",
        summary=f"Summary of story {id}.",
        url=f"https://example.com/{id}",
        source="OpenAI",
        published_at=datetime(2026, 1, 1, hour, tzinfo=timezone.utc),
    )


def _score(digest_id: int, score: int) -> DigestScore:
    return DigestScore(digest_id=digest_id, reasoning=f"reason {digest_id}", relevance_score=score)


@pytest.fixture
def agent(mocker):
    mocker.patch("app.agent.base.OpenAI")
    return EditorAgent()


def _respond_with(agent, *scores: DigestScore) -> None:
    agent._client.responses.parse.return_value = types.SimpleNamespace(output_parsed=EditorScores(scores=list(scores)))


def test_rank_calls_the_responses_api_with_stories_and_profile(agent):
    _respond_with(agent, _score(1, 5))

    agent.rank(digests=[_candidate(1, title="Agents get better at tools")], profile=PROFILE)

    agent._client.responses.parse.assert_called_once()
    call_kwargs = agent._client.responses.parse.call_args.kwargs
    assert call_kwargs["model"] == settings.openai_model
    assert call_kwargs["text_format"] is EditorScores
    assert "digest_id=1" in call_kwargs["input"]
    assert "Agents get better at tools" in call_kwargs["input"]
    assert "Builds LLM agents in Python." in call_kwargs["input"]
    assert "agent tool use" in call_kwargs["input"]
    assert isinstance(call_kwargs["instructions"], str) and call_kwargs["instructions"]


def test_rank_orders_by_score_highest_first_and_numbers_ranks(agent):
    _respond_with(agent, _score(1, 3), _score(2, 9), _score(3, 6))

    ranked = agent.rank(digests=[_candidate(1), _candidate(2), _candidate(3)], profile=PROFILE)

    assert [(r.rank, r.digest_id, r.relevance_score) for r in ranked] == [(1, 2, 9), (2, 3, 6), (3, 1, 3)]
    assert ranked[0].reasoning == "reason 2"
    assert ranked[0].title == "Story 2"


def test_rank_breaks_ties_by_most_recently_published_first(agent):
    _respond_with(agent, _score(1, 7), _score(2, 7))

    ranked = agent.rank(digests=[_candidate(1, hour=1), _candidate(2, hour=5)], profile=PROFILE)

    assert [r.digest_id for r in ranked] == [2, 1]


def test_rank_ignores_unknown_ids_and_keeps_the_first_score_for_duplicates(agent):
    _respond_with(agent, _score(1, 4), _score(99, 10), _score(1, 10), _score(2, 6))

    ranked = agent.rank(digests=[_candidate(1), _candidate(2)], profile=PROFILE)

    assert [(r.digest_id, r.relevance_score) for r in ranked] == [(2, 6), (1, 4)]


def test_rank_puts_digests_the_model_skipped_last(agent):
    _respond_with(agent, _score(2, 1))

    ranked = agent.rank(digests=[_candidate(1, hour=9), _candidate(2)], profile=PROFILE)

    assert [r.digest_id for r in ranked] == [2, 1]
    assert ranked[1].relevance_score == 0
    assert "Not scored" in ranked[1].reasoning


def test_rank_returns_nothing_without_calling_the_model_when_there_are_no_digests(agent):
    assert agent.rank(digests=[], profile=PROFILE) == []
    agent._client.responses.parse.assert_not_called()


def test_rank_raises_when_the_model_returns_no_parsed_output(agent):
    agent._client.responses.parse.return_value = types.SimpleNamespace(output_parsed=None)

    with pytest.raises(ValueError):
        agent.rank(digests=[_candidate(1)], profile=PROFILE)


def test_scores_outside_the_rubric_range_are_rejected():
    with pytest.raises(ValueError):
        DigestScore(digest_id=1, reasoning="r", relevance_score=11)


def test_reasoning_comes_before_the_score_in_the_output_schema():
    fields = list(DigestScore.model_json_schema()["properties"])
    assert fields.index("reasoning") < fields.index("relevance_score")

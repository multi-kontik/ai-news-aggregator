from __future__ import annotations

import types

import pytest

from app.agent.summarizer import _MODEL, DigestSummary, SummarizerAgent


@pytest.fixture
def agent(mocker):
    mocker.patch("app.agent.summarizer.OpenAI")
    return SummarizerAgent()


def test_summarize_calls_the_responses_api_with_the_expected_arguments(agent):
    expected = DigestSummary(title="A clear title", summary="A concise summary.")
    agent._client.responses.parse.return_value = types.SimpleNamespace(output_parsed=expected)

    result = agent.summarize(title="Original title", url="https://example.com/a", source="OpenAI", content="the body")

    assert result == expected
    agent._client.responses.parse.assert_called_once()
    call_kwargs = agent._client.responses.parse.call_args.kwargs
    assert call_kwargs["model"] == _MODEL
    assert call_kwargs["text_format"] is DigestSummary
    assert "Original title" in call_kwargs["input"]
    assert "https://example.com/a" in call_kwargs["input"]
    assert "the body" in call_kwargs["input"]
    assert isinstance(call_kwargs["instructions"], str) and call_kwargs["instructions"]


def test_summarize_raises_when_the_model_returns_no_parsed_output(agent):
    agent._client.responses.parse.return_value = types.SimpleNamespace(output_parsed=None)

    with pytest.raises(ValueError, match="https://example.com/a"):
        agent.summarize(title="Original title", url="https://example.com/a", source="OpenAI", content="the body")

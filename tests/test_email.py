from __future__ import annotations

import types
from datetime import date

import pytest

from app.agent.editor import RankedDigest
from app.agent.email_agent import EmailAgent, EmailIntroduction
from app.config import settings
from app.email_digest import greeting, render_digest_email, send_email
from app.profile import UserProfile

PROFILE = UserProfile(name="Aaron", background="Builds LLM agents.", interests=["agents"])


def _story(rank: int, title: str | None = None, url: str | None = None) -> RankedDigest:
    return RankedDigest(
        rank=rank,
        digest_id=rank,
        title=title or f"Story {rank}",
        summary=f"Summary {rank}.",
        url=url or f"https://example.com/{rank}",
        source="OpenAI",
        relevance_score=10 - rank,
        reasoning=f"Reason {rank}.",
    )


@pytest.fixture
def agent(mocker):
    mocker.patch("app.agent.base.OpenAI")
    return EmailAgent()


def test_greeting_uses_the_name_and_a_readable_date():
    assert greeting("Aaron", date(2026, 11, 3)) == "Hey Aaron, this is your daily digest of AI news for November 3, 2026."


def test_write_introduction_calls_the_responses_api_with_stories_and_profile(agent):
    expected = EmailIntroduction(introduction="Today's big story is agents.")
    agent._client.responses.parse.return_value = types.SimpleNamespace(output_parsed=expected)

    result = agent.write_introduction(stories=[_story(1, title="Agents ship")], profile=PROFILE)

    assert result == expected
    call_kwargs = agent._client.responses.parse.call_args.kwargs
    assert call_kwargs["model"] == settings.openai_model
    assert call_kwargs["text_format"] is EmailIntroduction
    assert "Agents ship" in call_kwargs["input"]
    assert "Builds LLM agents." in call_kwargs["input"]


def test_write_introduction_raises_without_stories(agent):
    with pytest.raises(ValueError):
        agent.write_introduction(stories=[], profile=PROFILE)
    agent._client.responses.parse.assert_not_called()


def test_write_introduction_raises_when_the_model_returns_no_parsed_output(agent):
    agent._client.responses.parse.return_value = types.SimpleNamespace(output_parsed=None)

    with pytest.raises(ValueError):
        agent.write_introduction(stories=[_story(1)], profile=PROFILE)


def test_render_includes_greeting_intro_and_every_story_in_order():
    email = render_digest_email(
        name="Aaron", day=date(2026, 11, 3), introduction="Here's what's new.", stories=[_story(1), _story(2)]
    )

    assert email.subject == "Your AI news digest for November 3, 2026"
    for body in (email.html, email.text):
        assert "Hey Aaron, this is your daily digest of AI news for November 3, 2026." in body
        assert "Here's what's new." in body or "Here&#x27;s what&#x27;s new." in body
        assert body.index("Story 1") < body.index("Story 2")
        assert "https://example.com/2" in body


def test_render_escapes_html_in_story_content():
    email = render_digest_email(
        name="Aaron", day=date(2026, 11, 3), introduction="intro",
        stories=[_story(1, title="<script>alert(1)</script>", url='https://example.com/"x')],
    )

    assert "<script>" not in email.html
    assert "&lt;script&gt;" in email.html
    assert 'href="https://example.com/&quot;x"' in email.html


def test_send_email_sends_html_and_text_to_the_configured_recipient(mocker):
    mocker.patch("app.email_digest.settings", types.SimpleNamespace(
        resend_api_key="re_test", email_to="aaron@example.com", email_from="Digest <d@example.com>",
    ))
    send = mocker.patch("app.email_digest.resend.Emails.send", return_value={"id": "msg_123"})
    email = render_digest_email(name="Aaron", day=date(2026, 11, 3), introduction="intro", stories=[_story(1)])

    assert send_email(email) == "msg_123"
    params = send.call_args.args[0]
    assert params["to"] == ["aaron@example.com"]
    assert params["from"] == "Digest <d@example.com>"
    assert params["subject"] == email.subject
    assert params["html"] == email.html and params["text"] == email.text


@pytest.mark.parametrize("api_key, to, missing", [(None, "a@example.com", "RESEND_API_KEY"), ("re_test", None, "EMAIL_TO")])
def test_send_email_refuses_to_send_without_configuration(mocker, api_key, to, missing):
    mocker.patch("app.email_digest.settings", types.SimpleNamespace(resend_api_key=api_key, email_to=to, email_from="x"))
    send = mocker.patch("app.email_digest.resend.Emails.send")
    email = render_digest_email(name="Aaron", day=date(2026, 11, 3), introduction="intro", stories=[_story(1)])

    with pytest.raises(ValueError, match=missing):
        send_email(email)
    send.assert_not_called()

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.agent.editor import RankedDigest
from app.agent.email_agent import EmailIntroduction
from app.agent.summarizer import DigestSummary
from app.database.models import AnthropicArticle, Base, Digest, OpenAIArticle
from app.database.repository import DigestRepository
from app.ingestion.feeds import FeedSource
from app.profile import UserProfile
from app.runner import process_digest, run_email_digest, run_enrichment, run_ingestion, run_ranking

NOW = datetime(2026, 1, 1, 12, 0)


@pytest.fixture(autouse=True)
def test_session_local(mocker):
    """Back the runner's DB writes with a real in-memory SQLite session, so the
    persistence code path is actually exercised instead of mocked away."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine)
    mocker.patch("app.runner.SessionLocal", session_local)
    return session_local


class FakeSource(FeedSource):
    """A feed source whose feeds and pages are in-memory, with failures on demand."""

    def __init__(self, name="OpenAI", model=OpenAIArticle, digest_link="openai_article_id", feeds=None, pages=None):
        super().__init__(name=name, model=model, digest_link=digest_link, feed_urls=list(feeds or {}))
        self.feeds = feeds or {}
        self.pages = pages or {}
        self.since_seen: list[datetime] = []
        self.opened = 0

    def __enter__(self):
        self.opened += 1
        return self

    def fetch(self, unit, since):
        self.since_seen.append(since)
        items = self.feeds[unit]
        if isinstance(items, Exception):
            raise items
        return items

    def enrich(self, row):
        page = self.pages[row.url]
        if isinstance(page, Exception):
            raise page
        row.content = page


def _item(slug: str, *, summary: str = "", title: str | None = None) -> dict:
    return {"url": f"https://example.com/{slug}", "title": title or slug, "summary": summary, "published_at": NOW}


def _store(session_local, model=OpenAIArticle, **fields) -> None:
    with session_local() as session:
        session.add(model(**{"published_at": NOW, "title": fields.get("url", "t"), **fields}))
        session.commit()


def _mock_agent(mocker, summarize=None):
    agent = mocker.MagicMock()
    agent.summarize.side_effect = summarize or (lambda **kwargs: DigestSummary(title="Digest title", summary="A summary."))
    mocker.patch("app.runner.SummarizerAgent", return_value=agent)
    return agent


# --- ingestion ---------------------------------------------------------------


def test_ingestion_stores_new_items_and_skips_ones_already_stored(test_session_local):
    source = FakeSource(feeds={"feed": [_item("a"), _item("b")]})

    assert run_ingestion(sources=[source]).count == 2
    assert run_ingestion(sources=[source]).count == 0

    with test_session_local() as session:
        assert sorted(session.scalars(select(OpenAIArticle.url))) == ["https://example.com/a", "https://example.com/b"]


def test_ingestion_does_not_overwrite_stored_items(test_session_local):
    _store(test_session_local, url="https://example.com/a", title="Original title")

    run_ingestion(sources=[FakeSource(feeds={"feed": [_item("a", title="New title")]})])

    with test_session_local() as session:
        assert session.scalar(select(OpenAIArticle.title)) == "Original title"


def test_a_failing_unit_does_not_block_the_others(test_session_local):
    source = FakeSource(feeds={"broken": RuntimeError("feed down"), "ok": [_item("a")]})
    other = FakeSource(name="Anthropic", model=AnthropicArticle, digest_link="anthropic_article_id", feeds={"feed": [_item("b")]})

    result = run_ingestion(sources=[source, other])

    assert result.count == 2
    assert [(e.source, e.error) for e in result.errors] == [("ingest:openai:broken", "feed down")]


def test_ingestion_passes_the_since_cutoff_derived_from_hours():
    source = FakeSource(feeds={"feed": []})

    run_ingestion(hours=6, sources=[source])

    assert datetime.now(timezone.utc) - source.since_seen[0] == pytest.approx(timedelta(hours=6), abs=timedelta(seconds=5))


# --- enrichment --------------------------------------------------------------


def test_enrichment_fills_in_missing_content_and_retries_failures_later(test_session_local):
    _store(test_session_local, url="https://example.com/ok")
    _store(test_session_local, url="https://example.com/blocked")
    source = FakeSource(pages={"https://example.com/ok": "# Full text", "https://example.com/blocked": RuntimeError("403")})

    result = run_enrichment(sources=[source])

    assert result.count == 1
    assert [e.source for e in result.errors] == ["enrich:openai:https://example.com/blocked"]
    with test_session_local() as session:
        content = dict(session.execute(select(OpenAIArticle.url, OpenAIArticle.content)).all())
    assert content == {"https://example.com/ok": "# Full text", "https://example.com/blocked": None}

    source.pages["https://example.com/blocked"] = "# Now it works"
    assert run_enrichment(sources=[source]).count == 1


def test_enrichment_skips_sources_with_nothing_to_enrich(test_session_local):
    _store(test_session_local, url="https://example.com/a", content="already enriched")
    source = FakeSource()

    assert run_enrichment(sources=[source]).count == 0
    assert source.opened == 0


# --- digests -----------------------------------------------------------------


def test_digests_are_created_for_every_source_and_linked_to_their_row(mocker, test_session_local):
    _store(test_session_local, url="https://example.com/a")
    _store(test_session_local, model=AnthropicArticle, url="https://example.com/b")
    _mock_agent(mocker)
    sources = [FakeSource(), FakeSource(name="Anthropic", model=AnthropicArticle, digest_link="anthropic_article_id")]

    result = process_digest(sources=sources)

    assert result.count == 2 and result.errors == []
    with test_session_local() as session:
        digests = {d.url: d for d in session.scalars(select(Digest))}
        assert digests["https://example.com/a"].source == "OpenAI"
        assert digests["https://example.com/b"].source == "Anthropic"
        assert all(d.published_at == NOW and d.title == "Digest title" for d in digests.values())

    assert process_digest(sources=sources).count == 0


def test_digests_summarize_the_richest_text_available(mocker, test_session_local):
    _store(test_session_local, url="https://example.com/full", summary="teaser", content="full text")
    _store(test_session_local, url="https://example.com/teaser", summary="teaser")
    _store(test_session_local, url="https://example.com/bare", title="Just a title")
    agent = _mock_agent(mocker)

    process_digest(sources=[FakeSource()])

    used = {call.kwargs["url"]: call.kwargs["content"] for call in agent.summarize.call_args_list}
    assert used == {
        "https://example.com/full": "full text",
        "https://example.com/teaser": "teaser",
        "https://example.com/bare": "Just a title",
    }


def test_a_failed_summary_or_save_does_not_stop_the_rest(mocker, test_session_local):
    for slug in ("model-error", "save-error", "ok"):
        _store(test_session_local, url=f"https://example.com/{slug}")

    def summarize(*, url, **kwargs):
        if url.endswith("model-error"):
            raise RuntimeError("bad model response")
        title = None if url.endswith("save-error") else "Digest title"  # NULL title -> the commit fails
        return DigestSummary.model_construct(title=title, summary="A summary.")

    _mock_agent(mocker, summarize)

    result = process_digest(sources=[FakeSource()])

    assert result.count == 1
    assert sorted(e.source for e in result.errors) == [
        "digest:openai:https://example.com/model-error",
        "digest:openai:https://example.com/save-error",
    ]
    with test_session_local() as session:
        assert list(session.scalars(select(Digest.url))) == ["https://example.com/ok"]


def test_digest_progress_is_logged_per_source(mocker, test_session_local, caplog):
    _store(test_session_local, url="https://example.com/a", title="First")
    _store(test_session_local, url="https://example.com/b", title="Second")
    _mock_agent(mocker)

    with caplog.at_level("INFO", logger="app.runner"):
        process_digest(sources=[FakeSource()])

    messages = [record.getMessage() for record in caplog.records]
    assert "Digest: 2 item(s) need a summary (2 OpenAI)" in messages
    assert "[OpenAI 1/2] Summarizing 'First' (https://example.com/a)" in messages
    assert "[OpenAI 2/2] Summarizing 'Second' (https://example.com/b)" in messages
    assert "Digest: finished, 2 created, 0 error(s)" in messages


# --- ranking and email -------------------------------------------------------


def _digest_row(session, *, published_at: datetime, title: str) -> None:
    DigestRepository(session).create(url=f"https://example.com/{title}", title=title, summary="s", published_at=published_at)


def _ranked(rank: int) -> RankedDigest:
    return RankedDigest(
        rank=rank, digest_id=rank, title=f"Story {rank}", summary="s", url=f"https://example.com/{rank}",
        source="OpenAI", relevance_score=5, reasoning="r",
    )


def _mock_email_agent(mocker, introduction: str = "intro"):
    agent = mocker.MagicMock()
    agent.write_introduction.return_value = EmailIntroduction(introduction=introduction)
    mocker.patch("app.runner.EmailAgent", return_value=agent)
    return agent


def test_ranking_only_considers_unsent_stories_published_in_the_window(mocker, test_session_local):
    now = datetime.now(timezone.utc)
    with test_session_local() as session:
        _digest_row(session, published_at=now - timedelta(hours=1), title="recent")
        _digest_row(session, published_at=now - timedelta(hours=1), title="sent")
        _digest_row(session, published_at=now - timedelta(hours=30), title="old")
        session.flush()
        sent = next(d for d in DigestRepository(session).list() if d.title == "sent")
        DigestRepository(session).mark_sent([sent.id], message_id="msg_old")
        session.commit()

    agent = mocker.MagicMock()
    agent.rank.return_value = []
    mocker.patch("app.runner.EditorAgent", return_value=agent)
    profile = UserProfile(name="Aaron", background="b", interests=["i"])

    run_ranking(hours=24, profile=profile)

    assert agent.rank.call_args.kwargs["profile"] is profile
    assert [d.title for d in agent.rank.call_args.kwargs["digests"]] == ["recent"]


def test_email_uses_only_the_top_n_stories_and_sends(mocker):
    agent = _mock_email_agent(mocker, "Today's intro.")
    send = mocker.patch("app.runner.send_email", return_value="msg_1")
    profile = UserProfile(name="Aaron", background="b", interests=["i"])

    result = run_email_digest([_ranked(r) for r in (3, 1, 12, 2, 5, 4, 6, 7, 8, 9, 10, 11)], profile=profile, day=date(2026, 11, 3))

    assert [s.rank for s in result.stories] == list(range(1, 11))
    assert [s.rank for s in agent.write_introduction.call_args.kwargs["stories"]] == list(range(1, 11))
    assert result.message_id == "msg_1"
    assert "Hey Aaron" in result.email.text and "Today's intro." in result.email.text
    assert "Story 11" not in result.email.text
    send.assert_called_once_with(result.email)


def test_email_does_nothing_without_stories(mocker):
    email_agent = mocker.patch("app.runner.EmailAgent")
    send = mocker.patch("app.runner.send_email")

    assert run_email_digest([]).email is None
    email_agent.assert_not_called()
    send.assert_not_called()


def test_email_records_sent_stories_but_not_on_a_dry_run(mocker, test_session_local):
    with test_session_local() as session:
        for title in ("one", "two"):
            _digest_row(session, published_at=datetime.now(timezone.utc), title=title)
        session.commit()
        ids = sorted(d.id for d in DigestRepository(session).list())

    _mock_email_agent(mocker)
    send = mocker.patch("app.runner.send_email", return_value="msg_1")
    ranked = [_ranked(1).model_copy(update={"digest_id": ids[0]}), _ranked(2).model_copy(update={"digest_id": ids[1]})]

    dry = run_email_digest(ranked, send=False)
    assert dry.email is not None and dry.message_id is None
    send.assert_not_called()
    with test_session_local() as session:
        assert len(DigestRepository(session).list_unsent()) == 2

    run_email_digest(ranked, top_n=1)
    with test_session_local() as session:
        assert [d.id for d in DigestRepository(session).list_unsent()] == [ids[1]]


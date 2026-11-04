from __future__ import annotations

import types

import pytest
from typer.testing import CliRunner

from app import pipeline
from app.cli import app
from app.runner import EmailDigestResult, StageError, StageResult


@pytest.fixture
def stages(mocker):
    """Mock every stage, recording the order they're called in."""
    calls: list[str] = []

    def stage(name, result):
        return mocker.patch(f"app.pipeline.{name}", side_effect=lambda *a, **k: calls.append(name) or result)

    mocker.patch("app.pipeline.settings", types.SimpleNamespace(openai_api_key="sk", resend_api_key="re", email_to="a@example.com"))
    mocks = types.SimpleNamespace(
        calls=calls,
        create_tables=stage("create_tables", []),
        run_ingestion=stage("run_ingestion", StageResult(
            count=3, errors=[StageError(source="ingest:openai:feed", error="feed down")],
        )),
        run_enrichment=stage("run_enrichment", StageResult(count=3, errors=[])),
        process_digest=stage("process_digest", StageResult(count=3, errors=[])),
        run_email_digest=stage("run_email_digest", EmailDigestResult(email=None, stories=[], message_id=None)),
    )
    return mocks


def test_run_pipeline_runs_every_stage_in_order(stages):
    result = pipeline.run_pipeline(ingest_hours=48, digest_hours=24, top_n=5)

    assert stages.calls == ["create_tables", "run_ingestion", "run_enrichment", "process_digest", "run_email_digest"]
    stages.run_ingestion.assert_called_once_with(hours=48)
    email_kwargs = stages.run_email_digest.call_args.kwargs
    assert (email_kwargs["top_n"], email_kwargs["hours"], email_kwargs["send"]) == (5, 24, True)
    assert [e.source for e in result.errors] == ["ingest:openai:feed"]


def test_run_pipeline_checks_settings_before_doing_any_work(stages, mocker):
    mocker.patch("app.pipeline.settings", types.SimpleNamespace(openai_api_key="sk", resend_api_key=None, email_to=None))

    with pytest.raises(ValueError, match="RESEND_API_KEY, EMAIL_TO"):
        pipeline.run_pipeline()
    assert stages.calls == []


def test_a_dry_run_does_not_need_email_settings(stages, mocker):
    mocker.patch("app.pipeline.settings", types.SimpleNamespace(openai_api_key="sk", resend_api_key=None, email_to=None))

    pipeline.run_pipeline(send=False)

    assert stages.run_email_digest.call_args.kwargs["send"] is False


def test_cli_run_exits_non_zero_when_the_pipeline_fails(mocker):
    mocker.patch("app.pipeline.run_pipeline", side_effect=RuntimeError("database is down"))

    result = CliRunner().invoke(app, ["run"])

    assert result.exit_code == 1


def test_cli_run_reports_per_item_errors_but_succeeds(stages):
    result = CliRunner().invoke(app, ["run", "--dry-run"])

    assert result.exit_code == 0
    assert "ingest:openai:feed: feed down" in result.output
    assert "No new stories" in result.output

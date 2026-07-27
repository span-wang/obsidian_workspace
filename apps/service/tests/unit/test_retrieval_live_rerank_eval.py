from pathlib import Path
from types import SimpleNamespace

import pytest

from application import retrieval_live_rerank_eval
from api import retrieval_live_rerank_eval as live_rerank_command
from domain.retrieval_rerank import RerankCandidate, RerankProviderTarget, RerankValidationError


def _cases():
    candidates = (
        RerankCandidate("alpha", 1, ("First",), "paragraph", "First candidate."),
        RerankCandidate("beta", 2, ("Second",), "paragraph", "Second candidate."),
    )
    return (
        {
            "queryId": "case-one",
            "queryText": "Which candidate is relevant?",
            "expectedCandidateIds": ("alpha",),
            "candidates": candidates,
        },
        {
            "queryId": "case-two",
            "queryText": "Which other candidate is relevant?",
            "expectedCandidateIds": ("beta",),
            "candidates": candidates,
        },
    )


def _target() -> RerankProviderTarget:
    return RerankProviderTarget("provider-1", "rerank-1", "revision-1")


def test_live_rerank_evaluation_records_only_safe_native_measurements_and_keeps_default_disabled() -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []

    def request_rerank(query: str, documents: tuple[str, ...]) -> tuple[float, ...]:
        calls.append((query, documents))
        return (0.9, 0.1) if len(calls) == 1 else (0.1, 0.9)

    report = retrieval_live_rerank_eval.run_live_rerank_evaluation(
        _cases(), _target(), request_rerank, max_requests=2
    )

    assert len(calls) == 2
    assert calls[0][1][0] == "First candidate."
    assert report["adapter"]["kind"] == "rerank-compatible-live"
    assert report["adapter"]["networkEgress"] is True
    assert report["latencyMs"]["sampleCount"] == 2
    assert report["usage"]["status"] == "not-applicable-to-rerank"
    assert report["cost"] == {"status": "not-calculated-per-user-request", "usd": None}
    assert report["quality"]["macroRecallAt8"] == 1.0
    assert report["passesGate"] is False
    assert report["defaultEnabled"] is False
    assert "Which candidate" not in str(report)
    assert "First candidate" not in str(report)


def test_live_rerank_evaluation_rejects_an_incomplete_native_response() -> None:
    with pytest.raises(RerankValidationError, match="every candidate"):
        retrieval_live_rerank_eval.run_live_rerank_evaluation(
            _cases()[:1], _target(), lambda _query, _documents: (0.9,), max_requests=1
        )


def test_live_rerank_command_requires_an_explicit_egress_confirmation(tmp_path) -> None:
    exit_code = live_rerank_command.main(
        [
            "--fixture",
            "fixture.json",
            "--output",
            str(tmp_path / "report.json"),
            "--data-dir",
            str(tmp_path / "data"),
            "--provider-id",
            "provider-1",
            "--model-id",
            "rerank-1",
            "--max-requests",
            "1",
        ]
    )

    assert exit_code == 2


def test_live_rerank_command_rejects_an_unapproved_fixture_before_provider_access(
    tmp_path, monkeypatch
) -> None:
    unapproved_fixture = tmp_path / "unapproved.json"
    unapproved_fixture.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        live_rerank_command,
        "_provider_service",
        lambda _data_directory: pytest.fail("Provider access must not happen for an unapproved fixture."),
    )

    exit_code = live_rerank_command.main(
        [
            "--fixture",
            str(unapproved_fixture),
            "--output",
            str(tmp_path / "report.json"),
            "--data-dir",
            str(tmp_path / "data"),
            "--confirm-live-egress",
            "--provider-id",
            "provider-1",
            "--model-id",
            "rerank-1",
            "--max-requests",
            "1",
        ]
    )

    assert exit_code == 1


def test_live_rerank_command_rejects_a_changed_approved_fixture_revision(tmp_path, monkeypatch) -> None:
    changed_fixture = tmp_path / "retrieval-rerank-golden-v1.json"
    changed_fixture.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(live_rerank_command, "_APPROVED_LIVE_FIXTURE", Path(changed_fixture))

    with pytest.raises(ValueError, match="reviewed revision"):
        live_rerank_command._load_approved_live_fixture(changed_fixture)


def test_live_rerank_command_rejects_reports_outside_the_ignored_live_output_directory(tmp_path) -> None:
    with pytest.raises(ValueError, match="output/live-rerank"):
        live_rerank_command._live_output_path(tmp_path / "report.json")


def test_live_rerank_command_allows_a_bounded_measurement_without_cost_budget(
    tmp_path, monkeypatch
) -> None:
    class FakeService:
        def resolve_specific_model(self, model_type, provider_id, model_id, *, require_https):
            assert (model_type, provider_id, model_id, require_https) == (
                "rerank",
                "provider-1",
                "rerank-1",
                True,
            )
            return SimpleNamespace(
                provider=SimpleNamespace(
                    provider_id="provider-1", updated_at="revision-1"
                ),
                model=SimpleNamespace(model_id="rerank-1"),
            )

    output_directory = tmp_path / "output" / "live-rerank"
    monkeypatch.setattr(live_rerank_command, "_WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(live_rerank_command, "_LIVE_OUTPUT_DIRECTORY", output_directory)
    monkeypatch.setattr(live_rerank_command, "_provider_service", lambda _data_directory: FakeService())
    monkeypatch.setattr(
        live_rerank_command,
        "run_live_rerank_evaluation",
        lambda *_args, **_kwargs: {
            "adapter": {"stopReason": None},
            "cost": {"status": "not-calculated-per-user-request", "usd": None},
            "latencyMs": {"p50": 1.0, "p95": 1.0},
            "quality": {"gatePassed": True},
        },
    )

    exit_code = live_rerank_command.main(
        [
            "--fixture",
            str(live_rerank_command._APPROVED_LIVE_FIXTURE),
            "--output",
            "output/live-rerank/report.json",
            "--data-dir",
            str(tmp_path / "data"),
            "--confirm-live-egress",
            "--provider-id",
            "provider-1",
            "--model-id",
            "rerank-1",
            "--max-requests",
            "1",
        ]
    )

    assert exit_code == 0
    report = (output_directory / "report.json").read_text(encoding="utf-8")
    assert "not-calculated-per-user-request" in report

from __future__ import annotations

import argparse
import json
from hashlib import sha256
from pathlib import Path

from adapters.openai_compatible_provider import OpenAiCompatibleProviderClient
from adapters.sqlite_provider_repository import SqliteProviderRepository
from adapters.windows_credential_manager import WindowsCredentialManager
from application.providers import ProviderService, ProviderUnavailableError
from application.retrieval_live_rerank_eval import run_live_rerank_evaluation
from application.retrieval_rerank_eval import load_rerank_golden
from domain.retrieval_rerank import RerankProviderTarget, RerankValidationError


_APPROVED_LIVE_FIXTURE = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "retrieval-rerank-golden-v1.json"
)
_APPROVED_LIVE_FIXTURE_SHA256 = "1cb61083abe3e192f92baac35bf231cf76e4cc02e5a366f49467e491977c14ec"
_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
_LIVE_OUTPUT_DIRECTORY = _WORKSPACE_ROOT / "output" / "live-rerank"


class _NoopAuthorizationInvalidator:
    def invalidate_provider_authorizations(self, provider_id: str, updated_at: str) -> None:
        return None


def _provider_service(data_directory: Path) -> ProviderService:
    return ProviderService(
        repository=SqliteProviderRepository(data_directory / "vaults.sqlite3"),
        credentials=WindowsCredentialManager(),
        client=OpenAiCompatibleProviderClient(),
        authorization_invalidator=_NoopAuthorizationInvalidator(),
    )


def _load_approved_live_fixture(path: Path) -> tuple[tuple[dict[str, object], ...], dict[str, float]]:
    if path.resolve() != _APPROVED_LIVE_FIXTURE:
        raise ValueError("Live rerank evaluation only accepts the approved deidentified fixture.")
    fixture_digest = sha256(_APPROVED_LIVE_FIXTURE.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    if fixture_digest != _APPROVED_LIVE_FIXTURE_SHA256:
        raise ValueError("Approved live rerank fixture content does not match its reviewed revision.")
    return load_rerank_golden(_APPROVED_LIVE_FIXTURE)


def _live_output_path(path: Path) -> Path:
    output_path = (path if path.is_absolute() else _WORKSPACE_ROOT / path).resolve()
    approved_directory = _LIVE_OUTPUT_DIRECTORY.resolve()
    if not output_path.is_relative_to(approved_directory):
        raise ValueError("Live rerank reports must be written under output/live-rerank/.")
    if output_path.exists():
        raise ValueError("Live rerank report output already exists; choose a new path.")
    return output_path


def _write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _pending_report(target: RerankProviderTarget, *, max_requests: int) -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "fixtureId": "retrieval-rerank-golden-v1",
        "provenance": "synthetic-deidentified",
        "executionStatus": "preflight-complete-awaiting-request",
        "adapter": {
            "kind": "rerank-compatible-live",
            "networkEgress": False,
            "requestLimit": max_requests,
            "providerIdSha256": sha256(target.provider_id.encode("utf-8")).hexdigest(),
            "modelId": target.model_id,
            "providerConfigurationRevision": target.provider_configuration_revision,
        },
        "cost": {"status": "not-calculated-per-user-request", "usd": None},
        "passesGate": False,
        "defaultEnabled": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a bounded live rerank evaluation on deidentified data.")
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--confirm-live-egress", action="store_true")
    parser.add_argument("--provider-id", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--max-requests", required=True, type=int)
    arguments = parser.parse_args(argv)
    if not arguments.confirm_live_egress:
        print("Live rerank evaluation requires --confirm-live-egress; no Provider request was made.")
        return 2
    try:
        cases, gates = _load_approved_live_fixture(arguments.fixture)
        output_path = _live_output_path(arguments.output)
        service = _provider_service(arguments.data_dir)
        resolved = service.resolve_specific_model(
            "rerank", arguments.provider_id, arguments.model_id, require_https=True
        )
        target = RerankProviderTarget(
            resolved.provider.provider_id,
            resolved.model.model_id,
            resolved.provider.updated_at,
        )
        pending_report = _pending_report(target, max_requests=arguments.max_requests)
        _write_report(output_path, pending_report)
        try:
            report = run_live_rerank_evaluation(
                cases,
                target,
                lambda query, documents: service.rerank(
                    target.provider_id,
                    target.model_id,
                    query,
                    documents,
                    expected_provider_updated_at=target.provider_configuration_revision,
                ),
                max_requests=arguments.max_requests,
                gates=gates,
            )
        except (ProviderUnavailableError, RerankValidationError, ValueError):
            pending_report["executionStatus"] = "failed-after-egress"
            pending_report["adapter"]["networkEgress"] = True
            pending_report["adapter"]["stopReason"] = "provider-request-failed-or-response-invalid"
            _write_report(output_path, pending_report)
            raise
        report["executionStatus"] = "completed"
        _write_report(output_path, report)
    except (OSError, ValueError, ProviderUnavailableError, RerankValidationError) as error:
        print(f"live rerank evaluation failed: {error}")
        return 1
    print(json.dumps({"cost": report["cost"], "latencyMs": report["latencyMs"]}, ensure_ascii=True))
    return 0 if report["quality"]["gatePassed"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())

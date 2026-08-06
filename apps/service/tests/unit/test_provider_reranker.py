from dataclasses import replace

import pytest

from application.provider_reranker import (
    ProviderReranker,
    RerankerResponseError,
    RerankerUnavailableError,
)
from application.providers import ProviderUnavailableError
from domain.providers import ProbeResult, Provider, ProviderModel, ProviderProbeResults, ResolvedProviderModel
from domain.retrieval_rerank import RerankCandidate, RerankProviderTarget


def _resolved(endpoint: str = "https://provider.example/v1") -> ResolvedProviderModel:
    provider = Provider(
        "provider-1",
        "Fixture provider",
        endpoint,
        "credential-reference",
        True,
        ProviderProbeResults(ProbeResult.success(), ProbeResult.success()),
        (ProviderModel("provider-1", "rerank-1", "rerank", ProbeResult.success(), True, "now"),),
        "now",
        "created",
        "revision-1",
    )
    return ResolvedProviderModel(provider, provider.models[0])


def _candidate() -> RerankCandidate:
    return RerankCandidate(
        "candidateone", 1, ("Grammar", "Be forms"), "paragraph", "Use am is and are.", ("grammar",)
    )


def _target() -> RerankProviderTarget:
    return RerankProviderTarget("provider-1", "rerank-1", "revision-1")


class _Providers:
    def __init__(
        self, relevances: tuple[float, ...] = (0.9,), endpoint: str = "https://provider.example/v1"
    ) -> None:
        self.relevances = relevances
        self.resolved = _resolved(endpoint)
        self.calls: list[tuple[str, str, str, tuple[str, ...], str]] = []

    def resolve_specific_model(
        self, model_type: str, provider_id: str, model_id: str, *, require_https: bool = False
    ) -> ResolvedProviderModel:
        assert (model_type, provider_id, model_id, require_https) == (
            "rerank", "provider-1", "rerank-1", True
        )
        return self.resolved

    def rerank(
        self,
        provider_id: str,
        model_id: str,
        query: str,
        documents: tuple[str, ...],
        *,
        expected_provider_updated_at: str,
    ) -> tuple[float, ...]:
        self.calls.append((provider_id, model_id, query, documents, expected_provider_updated_at))
        return self.relevances


def test_provider_reranker_uses_the_verified_rerank_model_revision_and_path_free_documents() -> None:
    providers = _Providers()

    response = ProviderReranker(providers).rerank(
        "Which be form is used with I?", (_candidate(),), target=_target()
    )

    assert response.scores[0].relevance == 0.9
    assert [(provider_id, model_id, revision) for provider_id, model_id, _query, _documents, revision in providers.calls] == [
        ("provider-1", "rerank-1", "revision-1")
    ]
    assert providers.calls[0][3] == ("Use am is and are.",)
    assert "candidateone" not in providers.calls[0][3][0]
    assert "credential-reference" not in providers.calls[0][3][0]


def test_provider_reranker_requires_https_before_sending_documents() -> None:
    providers = _Providers(endpoint="http://127.0.0.1:9000/v1")

    with pytest.raises(RerankerUnavailableError, match="HTTPS") as error:
        ProviderReranker(providers).rerank("question", (_candidate(),), target=_target())

    assert providers.calls == []
    assert error.value.network_request_count == 0


def test_provider_reranker_rejects_a_changed_provider_revision_before_sending() -> None:
    providers = _Providers()
    providers.resolved = ResolvedProviderModel(
        replace(providers.resolved.provider, updated_at="revision-2"),
        providers.resolved.model,
    )

    with pytest.raises(RerankerUnavailableError, match="configuration changed") as error:
        ProviderReranker(providers).rerank("question", (_candidate(),), target=_target())

    assert providers.calls == []
    assert error.value.network_request_count == 0


def test_provider_reranker_rejects_duplicate_candidates_before_sending_documents() -> None:
    providers = _Providers()

    with pytest.raises(ValueError, match="must not repeat"):
        ProviderReranker(providers).rerank(
            "question", (_candidate(), _candidate()), target=_target()
        )

    assert providers.calls == []


def test_provider_reranker_rejects_invalid_scores_without_echoing_provider_data() -> None:
    providers = _Providers((2.0,))

    with pytest.raises(RerankerResponseError, match="invalid response") as error:
        ProviderReranker(providers).rerank("question", (_candidate(),), target=_target())

    assert error.value.network_request_count == 1


def test_provider_reranker_records_an_attempt_after_native_rerank_fails() -> None:
    class FailingProviders(_Providers):
        def rerank(
            self,
            provider_id: str,
            model_id: str,
            query: str,
            documents: tuple[str, ...],
            *,
            expected_provider_updated_at: str,
        ) -> tuple[float, ...]:
            self.calls.append((provider_id, model_id, query, documents, expected_provider_updated_at))
            raise ProviderUnavailableError("Provider request failed")

    providers = FailingProviders()

    with pytest.raises(RerankerUnavailableError, match="could not rerank") as error:
        ProviderReranker(providers).rerank("question", (_candidate(),), target=_target())

    assert len(providers.calls) == 1
    assert error.value.network_request_count == 1

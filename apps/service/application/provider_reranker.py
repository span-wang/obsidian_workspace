from __future__ import annotations

from urllib.parse import urlparse

from application.providers import ProviderService, ProviderUnavailableError
from domain.retrieval_rerank import (
    RerankCandidate,
    RerankProviderTarget,
    RerankResponse,
    RerankScore,
    RerankValidationError,
    rerank_documents,
    validate_rerank_candidates,
    validate_rerank_response,
)
from ports.reranker import RerankerExecutionError


class RerankerUnavailableError(RerankerExecutionError):
    """Raised when no verified HTTPS rerank Provider can serve a rerank request."""


class RerankerResponseError(RerankerExecutionError):
    """Raised when a reranker response cannot be adopted as a whole."""


class ProviderReranker:
    """Native rerank adapter; callers remain responsible for outbound authorization."""

    def __init__(self, provider_service: ProviderService) -> None:
        self.provider_service = provider_service

    def rerank(
        self,
        query: str,
        candidates: tuple[RerankCandidate, ...],
        *,
        target: RerankProviderTarget,
    ) -> RerankResponse:
        if not isinstance(query, str) or not query.strip() or len(query) > 2_000:
            raise RerankValidationError("Rerank query is invalid.")
        if not isinstance(target, RerankProviderTarget):
            raise RerankValidationError("Rerank Provider selection is invalid.")
        candidates = validate_rerank_candidates(candidates)
        try:
            resolved = self.provider_service.resolve_specific_model(
                "rerank", target.provider_id, target.model_id, require_https=True
            )
        except ProviderUnavailableError as error:
            if "HTTPS endpoint" in str(error):
                raise RerankerUnavailableError(
                    "Rerank requests require an HTTPS Provider endpoint.", network_request_count=0
                ) from error
            raise RerankerUnavailableError(
                "Choose a verified rerank Provider model before reranking.", network_request_count=0
            ) from error
        if resolved.provider.updated_at != target.provider_configuration_revision:
            raise RerankerUnavailableError(
                "The selected Provider configuration changed. Request a new authorization.",
                network_request_count=0,
            )
        if urlparse(resolved.provider.endpoint).scheme != "https":
            raise RerankerUnavailableError(
                "Rerank requests require an HTTPS Provider endpoint.", network_request_count=0
            )
        try:
            documents = rerank_documents(candidates)
        except RerankValidationError as error:
            raise RerankerUnavailableError("Rerank documents are invalid.", network_request_count=0) from error
        try:
            relevances = self.provider_service.rerank(
                target.provider_id,
                target.model_id,
                query.strip(),
                documents,
                expected_provider_updated_at=target.provider_configuration_revision,
            )
        except ProviderUnavailableError as error:
            raise RerankerUnavailableError(
                "The selected Provider could not rerank these candidates.", network_request_count=1
            ) from error
        try:
            if not isinstance(relevances, tuple) or len(relevances) != len(candidates):
                raise RerankValidationError("Rerank response must score every candidate.")
            candidates_by_id = {candidate.candidate_id: candidate for candidate in candidates}
            response = RerankResponse(
                tuple(
                    sorted(
                        (
                            RerankScore(candidate.candidate_id, relevance)
                            for candidate, relevance in zip(candidates, relevances, strict=True)
                        ),
                        key=lambda score: (
                            -score.relevance,
                            candidates_by_id[score.candidate_id].fused_rank,
                            score.candidate_id,
                        ),
                    )
                )
            )
            return validate_rerank_response(candidates, response)
        except (RerankValidationError, ValueError) as error:
            raise RerankerResponseError(
                "The reranker returned an invalid response.", network_request_count=1
            ) from error

from typing import Protocol

from domain.retrieval_rerank import (
    RerankCandidate,
    RerankProviderTarget,
    RerankResponse,
)


class RerankerExecutionError(RuntimeError):
    """A reranker failure annotated with whether its one Provider request was attempted."""

    def __init__(self, message: str, *, network_request_count: int) -> None:
        if network_request_count not in {0, 1}:
            raise ValueError("Reranker failures can report only zero or one network request.")
        super().__init__(message)
        self.network_request_count = network_request_count


class RerankerPort(Protocol):
    """Reorder a bounded set of already retrieved, path-free candidates."""

    def rerank(
        self,
        query: str,
        candidates: tuple[RerankCandidate, ...],
        *,
        target: RerankProviderTarget,
    ) -> RerankResponse: ...

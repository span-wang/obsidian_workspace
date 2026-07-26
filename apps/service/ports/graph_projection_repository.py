from __future__ import annotations

from typing import Protocol, runtime_checkable

from domain.graph_projection import DurableGraphProjection, GraphProjectionKey


@runtime_checkable
class GraphProjectionRepository(Protocol):
    """Index-side storage boundary for immutable selected graph projections."""

    def save_graph_projection(self, projection: DurableGraphProjection) -> None: ...

    def get_graph_projection(self, key: GraphProjectionKey) -> DurableGraphProjection | None: ...

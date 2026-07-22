from typing import Protocol

from domain.candidate_links import CandidateLinkProposal


class GraphCandidateRepository(Protocol):
    def list_candidate_link_proposals_for_vault(self, vault_id: str) -> list[CandidateLinkProposal]: ...

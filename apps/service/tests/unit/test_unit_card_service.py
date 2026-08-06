from dataclasses import dataclass

import pytest

from application.providers import ProviderUnavailableError
from application.unit_card_batches import CheckedUnitCardBatch
from application.unit_card_service import UnitCardExecutionError, UnitCardService
from domain.embeddings import EmbeddingProfileLocator
from domain.unit_card_batches import UNIT_CARD_CONTENT_CATEGORIES, UnitCardBatchPreview, UnitCardBatchScope
from domain.unit_cards import UnitCardBuildInput, UnitCardPromptSource, UnitCardScope, UnitCardSource


def _batch() -> CheckedUnitCardBatch:
    source = UnitCardPromptSource(
        UnitCardSource(
            document_id="document-a",
            relative_path="teaching/unit-01.md",
            sequence=1,
            block_content_sha256="a" * 64,
            candidate_id="candidate-a",
            knowledge_kind="grammar",
            concept_keys=("subject verb agreement",),
        ),
        "Subject verb agreement source text.",
    )
    preview = UnitCardBatchPreview(
        vault_id="vault-a",
        scope=UnitCardBatchScope("vault"),
        chat_provider_id="chat-provider",
        chat_provider_name="Chat",
        chat_model_id="chat-model",
        chat_provider_configuration_revision="chat-revision",
        embedding_provider_id="embed-provider",
        embedding_provider_name="Embedding",
        embedding_model_id="embed-model",
        embedding_provider_configuration_revision="embed-revision",
        task_id="unit-card:task",
        policy_revision=1,
        file_count=1,
        block_count=1,
        card_count=1,
        blocked_file_count=0,
        blocked_block_count=0,
        blocked_documents=(),
        content_categories=UNIT_CARD_CONTENT_CATEGORIES,
        relative_paths=("teaching/unit-01.md",),
    )
    return CheckedUnitCardBatch(
        preview,
        (UnitCardBuildInput("vault-a", UnitCardScope("english", "7a", 1), (source,)),),
    )


@dataclass
class FakeBatchService:
    batch: CheckedUnitCardBatch

    def default_batch(self, *_args):
        return self.batch


class FakeProviderService:
    def __init__(
        self,
        response: str,
        *,
        embedding_values: tuple[tuple[float, ...], ...] | None = None,
        locator_error: ProviderUnavailableError | None = None,
    ) -> None:
        self.response = response
        self.embedding_values = embedding_values
        self.locator_error = locator_error
        self.chat_calls = 0
        self.embedding_calls = 0

    def generate_chat(self, *_args, **_kwargs) -> str:
        self.chat_calls += 1
        return self.response

    def create_embeddings(self, _provider_id, _model_id, texts, **_kwargs):
        self.embedding_calls += 1
        return self.embedding_values or tuple((1.0, 0.0) for _ in texts)

    def embedding_profile_locator(self, _provider_id, _model_id, **_kwargs) -> EmbeddingProfileLocator:
        if self.locator_error is not None:
            raise self.locator_error
        return EmbeddingProfileLocator(
            "embed-provider", "https://embed.example/v1", "embed-revision", "embed-model"
        )


class FakeIndexRepository:
    def __init__(self) -> None:
        self.saved: tuple | None = None

    def save_unit_cards(self, vault_id, cards, vectors) -> None:
        self.saved = vault_id, cards, vectors


def test_unit_card_service_uses_eligible_inputs_and_persists_card_and_vector() -> None:
    batch_service = FakeBatchService(_batch())
    provider = FakeProviderService(
        '{"items":[{"knowledge_kind":"grammar","concept_keys":["subject verb agreement"]}]}'
    )
    repository = FakeIndexRepository()

    report = UnitCardService(batch_service, provider, repository).execute("vault-a", UnitCardBatchScope("vault"))

    assert (report.card_count, report.chat_network_request_count, report.embedding_network_request_count) == (
        1,
        1,
        1,
    )
    assert repository.saved is not None
    _vault_id, cards, vectors = repository.saved
    assert cards[0].sources[0].candidate_id == "candidate-a"
    assert vectors[0].card_id == cards[0].card_id


def test_unit_card_service_rejects_provider_concepts_outside_the_reviewed_input() -> None:
    provider = FakeProviderService(
        '{"items":[{"knowledge_kind":"grammar","concept_keys":["invented"]}]}'
    )

    with pytest.raises(UnitCardExecutionError, match="unreviewed concepts"):
        UnitCardService(FakeBatchService(_batch()), provider, FakeIndexRepository()).execute("vault-a", UnitCardBatchScope("vault"))


def test_unit_card_service_blocks_a_provider_change_after_embedding_egress() -> None:
    provider = FakeProviderService(
        '{"items":[{"knowledge_kind":"grammar","concept_keys":["subject verb agreement"]}]}',
        locator_error=ProviderUnavailableError("The embedding Provider configuration changed."),
    )
    repository = FakeIndexRepository()

    with pytest.raises(UnitCardExecutionError, match="configuration changed"):
        UnitCardService(FakeBatchService(_batch()), provider, repository).execute("vault-a", UnitCardBatchScope("vault"))

    assert provider.embedding_calls == 1
    assert repository.saved is None


def test_unit_card_service_rejects_zero_embedding_vectors_without_persisting() -> None:
    provider = FakeProviderService(
        '{"items":[{"knowledge_kind":"grammar","concept_keys":["subject verb agreement"]}]}',
        embedding_values=((0.0, 0.0),),
    )
    repository = FakeIndexRepository()

    with pytest.raises(UnitCardExecutionError, match="must not be zero"):
        UnitCardService(FakeBatchService(_batch()), provider, repository).execute("vault-a", UnitCardBatchScope("vault"))

    assert repository.saved is None

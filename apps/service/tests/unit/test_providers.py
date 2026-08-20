import threading
from dataclasses import replace

import pytest

from adapters.sqlite_provider_repository import SqliteProviderRepository
from application.providers import ProviderService, ProviderUnavailableError, ProviderValidationError
from domain.markdown_structuring import MarkdownProviderChunkBudget
from domain.providers import ChatGeneration, ChatUsage
from ports.provider_client import ProviderClientError


class FakeRepository:
    def __init__(self) -> None:
        self.providers = {}
        self.defaults = {}
        self.markdown_budget = MarkdownProviderChunkBudget()

    def save(self, provider) -> None:
        self.providers[provider.provider_id] = provider

    def get(self, provider_id):
        return self.providers[provider_id]

    def list(self):
        return list(self.providers.values())

    def delete(self, provider_id) -> None:
        del self.providers[provider_id]
        for model_type, selection in list(self.defaults.items()):
            if selection.provider_id == provider_id:
                del self.defaults[model_type]

    def remove_model(self, provider_id, model_id, updated_at) -> None:
        provider = self.providers[provider_id]
        self.providers[provider_id] = replace(
            provider,
            models=tuple(model for model in provider.models if model.model_id != model_id),
            updated_at=updated_at,
        )
        for model_type, selection in list(self.defaults.items()):
            if selection.provider_id == provider_id and selection.model_id == model_id:
                del self.defaults[model_type]

    def get_default(self, model_type):
        return self.defaults.get(model_type)

    def save_default(self, selection) -> None:
        self.defaults[selection.model_type] = selection

    def delete_default(self, model_type) -> None:
        self.defaults.pop(model_type, None)

    def get_markdown_structure_budget(self):
        return self.markdown_budget

    def save_markdown_structure_budget(self, budget) -> None:
        self.markdown_budget = budget


class FakeCredentials:
    def __init__(self) -> None:
        self.values = {}
        self.reads = []

    def save(self, reference, secret) -> None:
        self.values[reference] = secret

    def read(self, reference):
        self.reads.append(reference)
        return self.values[reference]

    def delete(self, reference) -> None:
        self.values.pop(reference, None)


class FakeClient:
    def __init__(self) -> None:
        self.calls = []
        self.secrets = []
        self.stream_started: threading.Event | None = None
        self.release_stream: threading.Event | None = None
        self.chat_response = '{"results":[]}'

    def discover_models(self, endpoint, secret, cancel_event=None):
        self.secrets.append(secret)
        self.calls.append("discover")
        return ("chat-model", "embedding-model", "rerank-model", "markdown-model")

    def health_check(self, endpoint, secret, cancel_event=None) -> None:
        self.secrets.append(secret)
        self.calls.append("health")

    def probe_streaming_generation(self, endpoint, secret, model_id, cancel_event=None) -> None:
        self.secrets.append(secret)
        self.calls.append(("chat", model_id))
        if self.stream_started and self.release_stream:
            self.stream_started.set()
            self.release_stream.wait(timeout=2)

    def probe_responses_generation(self, endpoint, secret, model_id, cancel_event=None) -> None:
        self.secrets.append(secret)
        self.calls.append(("responses", model_id))

    def probe_embedding(self, endpoint, secret, model_id, cancel_event=None) -> None:
        self.secrets.append(secret)
        self.calls.append(("embedding", model_id))

    def probe_rerank(self, endpoint, secret, model_id, cancel_event=None) -> None:
        self.secrets.append(secret)
        self.calls.append(("rerank-probe", model_id))


    def create_embeddings(self, endpoint, secret, model_id, inputs, cancel_event=None):
        self.secrets.append(secret)
        self.calls.append(("create-embeddings", model_id, inputs))
        return tuple((float(index), 1.0) for index, _value in enumerate(inputs, start=1))

    def rerank(self, endpoint, secret, model_id, query, documents, cancel_event=None):
        self.secrets.append(secret)
        self.calls.append(("rerank", model_id, query, documents))
        return tuple(1.0 / (index + 1) for index in range(len(documents)))

    def generate_chat_with_usage(
        self, endpoint, secret, model_id, prompt, max_output_tokens, cancel_event=None
    ):
        self.secrets.append(secret)
        self.calls.append(("generate-chat-with-usage", model_id, max_output_tokens))
        return ChatGeneration(self.chat_response, ChatUsage(10, 5, 15))

    def generate_responses(self, endpoint, secret, model_id, prompt, cancel_event=None):
        self.secrets.append(secret)
        self.calls.append(("generate-responses", model_id))
        return self.chat_response

    def generate_responses_with_usage(
        self, endpoint, secret, model_id, prompt, max_output_tokens, cancel_event=None
    ):
        self.secrets.append(secret)
        self.calls.append(("generate-responses-with-usage", model_id, max_output_tokens))
        return ChatGeneration(self.chat_response, ChatUsage(10, 5, 15))

    def stream_responses(self, endpoint, secret, model_id, prompt, cancel_event=None):
        self.secrets.append(secret)
        self.calls.append(("stream-responses", model_id))
        yield self.chat_response


def make_service(*, repository=None, client=None):
    credentials = FakeCredentials()
    service = ProviderService(
        repository=repository or FakeRepository(),
        credentials=credentials,
        client=client or FakeClient(),
    )
    return service, service.repository, credentials


def discovered_provider(service):
    return service.test(service.create("Cloud", "https://provider.example/v1", "secret").provider_id)


def test_provider_test_only_discovers_and_checks_health() -> None:
    client = FakeClient()
    service, _, _ = make_service(client=client)

    provider = discovered_provider(service)

    assert client.calls == ["discover", "health"]
    assert provider.verification.is_verified is True
    assert {model.model_id for model in provider.models} == {
        "chat-model",
        "embedding-model",
        "markdown-model",
        "rerank-model",
    }
    assert all(model.model_type is None for model in provider.models)


def test_loopback_provider_without_a_key_can_be_verified_and_used() -> None:
    client = FakeClient()
    service, _, credentials = make_service(client=client)

    provider = service.create("Local", "http://127.0.0.1:11434/v1")
    assert provider.credential_configured is False
    assert credentials.values == {}

    discovered = service.test(provider.provider_id)
    service.configure_model(discovered.provider_id, "chat-model", "chat")
    verified = service.test_model(discovered.provider_id, "chat-model")
    service.set_default("chat", verified.provider_id, "chat-model")
    response = service.generate_chat_with_usage(
        verified.provider_id,
        "chat-model",
        "Hello",
        max_output_tokens=32,
        expected_provider_updated_at=verified.updated_at,
    )

    assert response.content == '{"results":[]}'
    assert service.resolve_model("chat").provider.provider_id == provider.provider_id
    assert client.secrets == ["", "", "", ""]
    assert credentials.reads == []


def test_provider_without_a_key_must_use_a_loopback_endpoint() -> None:
    service, _, _ = make_service()

    with pytest.raises(ProviderValidationError, match="non-local"):
        service.create("Cloud", "https://provider.example/v1")


def test_models_are_verified_by_type_and_defaults_are_independent() -> None:
    client = FakeClient()
    service, _, _ = make_service(client=client)
    provider = discovered_provider(service)

    service.configure_model(provider.provider_id, "chat-model", "chat")
    service.configure_model(provider.provider_id, "embedding-model", "embedding")
    service.configure_model(provider.provider_id, "rerank-model", "rerank")
    service.test_model(provider.provider_id, "chat-model")
    service.test_model(provider.provider_id, "embedding-model")
    service.test_model(provider.provider_id, "rerank-model")
    service.set_default("chat", provider.provider_id, "chat-model")
    service.set_default("embedding", provider.provider_id, "embedding-model")
    service.set_default("rerank", provider.provider_id, "rerank-model")

    assert client.calls[-3:] == [
        ("chat", "chat-model"),
        ("embedding", "embedding-model"),
        ("rerank-probe", "rerank-model"),
    ]
    assert service.resolve_model("chat").model.model_id == "chat-model"
    assert service.resolve_model("embedding").model.model_id == "embedding-model"
    assert service.resolve_model("rerank").model.model_id == "rerank-model"


def test_responses_mode_probes_and_generates_with_the_responses_contract() -> None:
    client = FakeClient()
    service, _, _ = make_service(client=client)
    provider = service.create(
        "Responses Cloud", "https://provider.example/v1", "secret", api_mode="responses"
    )
    discovered = service.test(provider.provider_id)
    service.configure_model(discovered.provider_id, "chat-model", "chat")
    verified = service.test_model(discovered.provider_id, "chat-model")

    generation = service.generate_chat_with_usage(
        verified.provider_id,
        "chat-model",
        "rank these candidates",
        max_output_tokens=128,
        expected_provider_updated_at=verified.updated_at,
    )

    assert verified.api_mode == "responses"
    assert ("responses", "chat-model") in client.calls
    assert generation.content == '{"results":[]}'
    assert client.calls[-1] == ("generate-responses-with-usage", "chat-model", 128)


def test_model_verification_preserves_safe_provider_client_errors() -> None:
    class FailingClient(FakeClient):
        def probe_streaming_generation(self, endpoint, secret, model_id, cancel_event=None) -> None:
            raise ProviderClientError("Provider TLS connection failed.")

    service, _, _ = make_service(client=FailingClient())
    provider = discovered_provider(service)
    service.configure_model(provider.provider_id, "chat-model", "chat")

    tested = service.test_model(provider.provider_id, "chat-model")

    model = next(item for item in tested.models if item.model_id == "chat-model")
    assert model.verification.ok is False
    assert model.verification.reason == (
        "Chat model verification could not be completed. Provider TLS connection failed."
    )


def test_removing_a_model_clears_its_defaults_regardless_of_verification_result() -> None:
    service, _, _ = make_service()
    provider = discovered_provider(service)
    service.configure_model(provider.provider_id, "chat-model", "chat")
    verified = service.test_model(provider.provider_id, "chat-model")
    service.set_default("chat", verified.provider_id, "chat-model")

    removed = service.remove_model(verified.provider_id, "chat-model")

    assert all(model.model_id != "chat-model" for model in removed.models)
    assert service.get_default("chat") is None

def test_markdown_model_is_verified_and_generation_is_locked_to_its_default() -> None:
    client = FakeClient()
    client.chat_response = '{"blocks":[]}'
    service, _, _ = make_service(client=client)
    provider = discovered_provider(service)

    service.configure_model(provider.provider_id, "markdown-model", "markdown")
    verified = service.test_model(provider.provider_id, "markdown-model")
    service.set_default("markdown", verified.provider_id, "markdown-model")

    assert service.generate_markdown(
        verified.provider_id,
        "markdown-model",
        "structure this",
        max_output_tokens=128,
        expected_provider_updated_at=verified.updated_at,
    ) == '{"blocks":[]}'
    assert ("chat", "markdown-model") in client.calls


def test_markdown_generation_allows_the_large_structuring_output_budget() -> None:
    client = FakeClient()
    service, _, _ = make_service(client=client)
    provider = discovered_provider(service)
    service.configure_model(provider.provider_id, "markdown-model", "markdown")
    verified = service.test_model(provider.provider_id, "markdown-model")

    service.generate_markdown(
        verified.provider_id,
        "markdown-model",
        "structure this",
        max_output_tokens=24_576,
        expected_provider_updated_at=verified.updated_at,
    )

    assert client.calls[-1] == ("generate-chat-with-usage", "markdown-model", 24_576)


def test_markdown_structure_budget_is_validated_and_persisted() -> None:
    service, repository, _ = make_service()

    assert service.markdown_structure_budget() == MarkdownProviderChunkBudget()
    configured = service.set_markdown_structure_budget(10_000, 15_000, 20_000)

    assert configured == MarkdownProviderChunkBudget(10_000, 15_000, 20_000)
    assert repository.get_markdown_structure_budget() == configured
    with pytest.raises(ProviderValidationError, match="budget"):
        service.set_markdown_structure_budget(16_000, 10_000, 20_000)


def test_refresh_invalidates_previously_verified_models() -> None:
    service, _, _ = make_service()
    provider = discovered_provider(service)
    service.configure_model(provider.provider_id, "chat-model", "chat")
    service.test_model(provider.provider_id, "chat-model")
    service.set_default("chat", provider.provider_id, "chat-model")

    refreshed = service.test(provider.provider_id)

    chat = next(model for model in refreshed.models if model.model_id == "chat-model")
    assert chat.verification.ok is False
    with pytest.raises(ProviderUnavailableError, match="unavailable"):
        service.resolve_model("chat")


def test_invalid_update_keeps_the_existing_credential() -> None:
    service, repository, credentials = make_service()
    provider = discovered_provider(service)

    with pytest.raises(ProviderValidationError):
        service.update(provider.provider_id, "Cloud", "not-a-url", "replacement")

    assert credentials.read(provider.credential_reference) == "secret"
    assert repository.get(provider.provider_id).verification.is_verified is True


def test_inflight_model_test_cannot_resurrect_a_deleted_provider() -> None:
    client = FakeClient()
    client.stream_started = threading.Event()
    client.release_stream = threading.Event()
    service, repository, _ = make_service(client=client)
    provider = discovered_provider(service)
    service.configure_model(provider.provider_id, "chat-model", "chat")

    testing = threading.Thread(target=service.test_model, args=(provider.provider_id, "chat-model"))
    testing.start()
    assert client.stream_started.wait(timeout=1)
    deleting = threading.Thread(target=service.delete, args=(provider.provider_id,))
    deleting.start()
    assert deleting.is_alive()
    client.release_stream.set()
    testing.join(timeout=2)
    deleting.join(timeout=2)

    assert not testing.is_alive()
    assert not deleting.is_alive()
    with pytest.raises(KeyError):
        repository.get(provider.provider_id)


def test_sqlite_persists_typed_models_and_dual_defaults_without_secret(tmp_path) -> None:
    repository = SqliteProviderRepository(tmp_path / "providers.sqlite3")
    service, _, _ = make_service(repository=repository)
    provider = discovered_provider(service)
    service.configure_model(provider.provider_id, "chat-model", "chat")
    service.configure_model(provider.provider_id, "markdown-model", "markdown")
    service.test_model(provider.provider_id, "chat-model")
    service.test_model(provider.provider_id, "markdown-model")
    service.set_default("chat", provider.provider_id, "chat-model")
    service.set_default("markdown", provider.provider_id, "markdown-model")

    reopened = SqliteProviderRepository(tmp_path / "providers.sqlite3")
    assert reopened.get(provider.provider_id).models[0].model_type in {"chat", None}
    assert reopened.get_default("chat").model_id == "chat-model"
    assert reopened.get_default("markdown").model_id == "markdown-model"
    assert b"secret" not in (tmp_path / "providers.sqlite3").read_bytes()


def test_generation_exposes_only_safe_provider_client_errors() -> None:
    class FailingClient(FakeClient):
        def generate_chat(self, endpoint, secret, model_id, prompt, cancel_event=None):
            raise ProviderClientError("Provider request failed with HTTP 429.")

    service, _, _ = make_service(client=FailingClient())
    provider = discovered_provider(service)
    service.configure_model(provider.provider_id, "chat-model", "chat")
    service.test_model(provider.provider_id, "chat-model")

    with pytest.raises(ProviderUnavailableError, match="HTTP 429"):
        service.generate_chat(provider.provider_id, "chat-model", "prompt")


def test_generation_hides_unexpected_provider_errors() -> None:
    class FailingClient(FakeClient):
        def generate_chat(self, endpoint, secret, model_id, prompt, cancel_event=None):
            raise RuntimeError("credential=secret")

    service, _, _ = make_service(client=FailingClient())
    provider = discovered_provider(service)
    service.configure_model(provider.provider_id, "chat-model", "chat")
    service.test_model(provider.provider_id, "chat-model")

    with pytest.raises(ProviderUnavailableError, match="could not generate this section") as error:
        service.generate_chat(provider.provider_id, "chat-model", "prompt")
    assert "credential=secret" not in str(error.value)


def test_measured_generation_is_locked_to_the_expected_provider_revision() -> None:
    client = FakeClient()
    service, _, _ = make_service(client=client)
    provider = discovered_provider(service)
    service.configure_model(provider.provider_id, "chat-model", "chat")
    verified = service.test_model(provider.provider_id, "chat-model")

    generation = service.generate_chat_with_usage(
        verified.provider_id,
        "chat-model",
        "rank these candidates",
        max_output_tokens=128,
        expected_provider_updated_at=verified.updated_at,
    )

    assert generation.usage == ChatUsage(10, 5, 15)
    assert client.calls[-1] == ("generate-chat-with-usage", "chat-model", 128)


def test_measured_generation_rejects_a_stale_revision_before_provider_egress() -> None:
    client = FakeClient()
    service, _, _ = make_service(client=client)
    provider = discovered_provider(service)
    service.configure_model(provider.provider_id, "chat-model", "chat")
    service.test_model(provider.provider_id, "chat-model")

    with pytest.raises(ProviderUnavailableError, match="configuration changed"):
        service.generate_chat_with_usage(
            provider.provider_id,
            "chat-model",
            "rank these candidates",
            max_output_tokens=128,
            expected_provider_updated_at="stale-revision",
        )

    assert ("generate-chat-with-usage", "chat-model", 128) not in client.calls


def test_measured_generation_rejects_http_before_reading_a_provider_credential() -> None:
    client = FakeClient()
    service, _, credentials = make_service(client=client)
    provider = service.test(service.create("Cloud", "http://provider.example/v1", "secret").provider_id)
    service.configure_model(provider.provider_id, "chat-model", "chat")
    verified = service.test_model(provider.provider_id, "chat-model")
    credentials.reads.clear()

    with pytest.raises(ProviderUnavailableError, match="HTTPS"):
        service.generate_chat_with_usage(
            verified.provider_id,
            "chat-model",
            "rank these candidates",
            max_output_tokens=128,
            expected_provider_updated_at=verified.updated_at,
        )

    assert ("generate-chat-with-usage", "chat-model", 128) not in client.calls
    assert credentials.reads == []


def test_batch_embeddings_are_locked_to_the_expected_https_provider_revision() -> None:
    client = FakeClient()
    service, _, _ = make_service(client=client)
    provider = discovered_provider(service)
    service.configure_model(provider.provider_id, "embedding-model", "embedding")
    verified = service.test_model(provider.provider_id, "embedding-model")

    locator = service.embedding_profile_locator(
        verified.provider_id,
        "embedding-model",
        expected_provider_updated_at=verified.updated_at,
    )
    vectors = service.create_embeddings(
        verified.provider_id,
        "embedding-model",
        ("first", "second"),
        expected_provider_updated_at=verified.updated_at,
    )

    assert locator.endpoint == "https://provider.example/v1"
    assert locator.configuration_revision == verified.updated_at
    assert vectors == ((1.0, 1.0), (2.0, 1.0))
    assert client.calls[-1] == ("create-embeddings", "embedding-model", ("first", "second"))


def test_batch_embeddings_reject_stale_configuration_revisions_before_provider_egress() -> None:
    client = FakeClient()
    service, _, _ = make_service(client=client)
    provider = discovered_provider(service)
    service.configure_model(provider.provider_id, "embedding-model", "embedding")
    verified = service.test_model(provider.provider_id, "embedding-model")

    with pytest.raises(ProviderUnavailableError, match="configuration changed"):
        service.create_embeddings(
            verified.provider_id,
            "embedding-model",
            ("first",),
            expected_provider_updated_at="stale-revision",
        )

    assert not any(call[0] == "create-embeddings" for call in client.calls if isinstance(call, tuple))


def test_batch_embeddings_refuse_http_even_for_an_otherwise_verified_model() -> None:
    client = FakeClient()
    service, _, _ = make_service(client=client)
    provider = service.test(service.create("Cloud", "http://provider.example/v1", "secret").provider_id)
    service.configure_model(provider.provider_id, "embedding-model", "embedding")
    verified = service.test_model(provider.provider_id, "embedding-model")

    with pytest.raises(ProviderUnavailableError, match="HTTPS"):
        service.create_embeddings(
            verified.provider_id,
            "embedding-model",
            ("first",),
            expected_provider_updated_at=verified.updated_at,
        )

    assert not any(call[0] == "create-embeddings" for call in client.calls if isinstance(call, tuple))


def test_rerank_is_locked_to_a_verified_https_model_and_expected_provider_revision() -> None:
    client = FakeClient()
    service, _, _ = make_service(client=client)
    provider = discovered_provider(service)
    service.configure_model(provider.provider_id, "rerank-model", "rerank")
    verified = service.test_model(provider.provider_id, "rerank-model")

    scores = service.rerank(
        verified.provider_id,
        "rerank-model",
        "find the evidence",
        ("first", "second"),
        expected_provider_updated_at=verified.updated_at,
    )

    assert scores == (1.0, 0.5)
    assert client.calls[-1] == (
        "rerank",
        "rerank-model",
        "find the evidence",
        ("first", "second"),
    )


def test_rerank_rejects_a_stale_revision_before_provider_egress() -> None:
    client = FakeClient()
    service, _, _ = make_service(client=client)
    provider = discovered_provider(service)
    service.configure_model(provider.provider_id, "rerank-model", "rerank")
    service.test_model(provider.provider_id, "rerank-model")

    with pytest.raises(ProviderUnavailableError, match="configuration changed"):
        service.rerank(
            provider.provider_id,
            "rerank-model",
            "find the evidence",
            ("first",),
            expected_provider_updated_at="stale-revision",
        )

    assert not any(call[0] == "rerank" for call in client.calls if isinstance(call, tuple))


def test_rerank_rejects_http_before_reading_a_provider_credential_or_egress() -> None:
    client = FakeClient()
    service, repository, credentials = make_service(client=client)
    provider = discovered_provider(service)
    service.configure_model(provider.provider_id, "rerank-model", "rerank")
    verified = service.test_model(provider.provider_id, "rerank-model")
    repository.save(replace(verified, endpoint="http://provider.example/v1"))
    credentials.reads.clear()

    with pytest.raises(ProviderUnavailableError, match="HTTPS"):
        service.rerank(
            verified.provider_id,
            "rerank-model",
            "find the evidence",
            ("first",),
            expected_provider_updated_at=verified.updated_at,
        )

    assert not any(call[0] == "rerank" for call in client.calls if isinstance(call, tuple))
    assert credentials.reads == []


def test_rerank_model_test_rejects_http_before_reading_a_provider_credential() -> None:
    client = FakeClient()
    service, _, credentials = make_service(client=client)
    provider = service.test(service.create("Cloud", "http://provider.example/v1", "secret").provider_id)
    service.configure_model(provider.provider_id, "rerank-model", "rerank")
    credentials.reads.clear()

    tested = service.test_model(provider.provider_id, "rerank-model")

    model = next(item for item in tested.models if item.model_id == "rerank-model")
    assert model.verification.ok is False
    assert "HTTPS" in (model.verification.reason or "")
    assert ("rerank-probe", "rerank-model") not in client.calls
    assert credentials.reads == []

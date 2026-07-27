import threading
from dataclasses import replace

import pytest

from adapters.sqlite_provider_repository import SqliteProviderRepository
from application.providers import ProviderService, ProviderUnavailableError, ProviderValidationError
from domain.providers import ChatGeneration, ChatUsage
from ports.provider_client import ProviderClientError


class FakeRepository:
    def __init__(self) -> None:
        self.providers = {}
        self.defaults = {}

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

    def get_default(self, model_type):
        return self.defaults.get(model_type)

    def save_default(self, selection) -> None:
        self.defaults[selection.model_type] = selection

    def delete_default(self, model_type) -> None:
        self.defaults.pop(model_type, None)


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
        self.stream_started: threading.Event | None = None
        self.release_stream: threading.Event | None = None

    def discover_models(self, endpoint, secret, cancel_event=None):
        self.calls.append("discover")
        return ("chat-model", "embedding-model", "rerank-model")

    def health_check(self, endpoint, secret, cancel_event=None) -> None:
        self.calls.append("health")

    def probe_streaming_generation(self, endpoint, secret, model_id, cancel_event=None) -> None:
        self.calls.append(("chat", model_id))
        if self.stream_started and self.release_stream:
            self.stream_started.set()
            self.release_stream.wait(timeout=2)

    def probe_embedding(self, endpoint, secret, model_id, cancel_event=None) -> None:
        self.calls.append(("embedding", model_id))

    def probe_rerank(self, endpoint, secret, model_id, cancel_event=None) -> None:
        self.calls.append(("rerank-probe", model_id))

    def create_embeddings(self, endpoint, secret, model_id, inputs, cancel_event=None):
        self.calls.append(("create-embeddings", model_id, inputs))
        return tuple((float(index), 1.0) for index, _value in enumerate(inputs, start=1))

    def rerank(self, endpoint, secret, model_id, query, documents, cancel_event=None):
        self.calls.append(("rerank", model_id, query, documents))
        return tuple(1.0 / (index + 1) for index in range(len(documents)))

    def generate_chat_with_usage(
        self, endpoint, secret, model_id, prompt, max_output_tokens, cancel_event=None
    ):
        self.calls.append(("generate-chat-with-usage", model_id, max_output_tokens))
        return ChatGeneration("{\"results\":[]}", ChatUsage(10, 5, 15))


class FakeInvalidator:
    def __init__(self) -> None:
        self.calls = []

    def invalidate_provider_authorizations(self, provider_id, updated_at) -> None:
        self.calls.append(provider_id)


class FakeUnitCardInvalidator:
    def __init__(self) -> None:
        self.calls = []

    def invalidate_unit_cards_for_provider_change(self, provider_id, updated_at) -> None:
        self.calls.append((provider_id, updated_at))


def make_service(*, repository=None, client=None, unit_card_invalidator=None):
    credentials = FakeCredentials()
    service = ProviderService(
        repository=repository or FakeRepository(),
        credentials=credentials,
        client=client or FakeClient(),
        authorization_invalidator=FakeInvalidator(),
        unit_card_invalidator=unit_card_invalidator,
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
        "rerank-model",
    }
    assert all(model.model_type is None for model in provider.models)


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


def test_provider_selection_changes_invalidate_unit_card_projections() -> None:
    unit_card_invalidator = FakeUnitCardInvalidator()
    service, _, _ = make_service(unit_card_invalidator=unit_card_invalidator)
    provider = discovered_provider(service)
    service.configure_model(provider.provider_id, "chat-model", "chat")
    service.test_model(provider.provider_id, "chat-model")
    unit_card_invalidator.calls.clear()

    service.set_default("chat", provider.provider_id, "chat-model")

    assert [provider_id for provider_id, _updated_at in unit_card_invalidator.calls] == [
        provider.provider_id
    ]


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
    service.test_model(provider.provider_id, "chat-model")
    service.set_default("chat", provider.provider_id, "chat-model")

    reopened = SqliteProviderRepository(tmp_path / "providers.sqlite3")
    assert reopened.get(provider.provider_id).models[0].model_type in {"chat", None}
    assert reopened.get_default("chat").model_id == "chat-model"
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
    provider = service.test(service.create("Cloud", "http://127.0.0.1/v1", "secret").provider_id)
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
    provider = service.test(service.create("Cloud", "http://127.0.0.1/v1", "secret").provider_id)
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
    repository.save(replace(verified, endpoint="http://127.0.0.1/v1"))
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
    provider = service.test(service.create("Cloud", "http://127.0.0.1/v1", "secret").provider_id)
    service.configure_model(provider.provider_id, "rerank-model", "rerank")
    credentials.reads.clear()

    tested = service.test_model(provider.provider_id, "rerank-model")

    model = next(item for item in tested.models if item.model_id == "rerank-model")
    assert model.verification.ok is False
    assert "HTTPS" in (model.verification.reason or "")
    assert ("rerank-probe", "rerank-model") not in client.calls
    assert credentials.reads == []

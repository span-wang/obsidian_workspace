import threading

import pytest

from adapters.sqlite_provider_repository import SqliteProviderRepository
from application.providers import ProviderService, ProviderUnavailableError, ProviderValidationError


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

    def save(self, reference, secret) -> None:
        self.values[reference] = secret

    def read(self, reference):
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
        return ("chat-model", "embedding-model")

    def health_check(self, endpoint, secret, cancel_event=None) -> None:
        self.calls.append("health")

    def probe_streaming_generation(self, endpoint, secret, model_id, cancel_event=None) -> None:
        self.calls.append(("chat", model_id))
        if self.stream_started and self.release_stream:
            self.stream_started.set()
            self.release_stream.wait(timeout=2)

    def probe_embedding(self, endpoint, secret, model_id, cancel_event=None) -> None:
        self.calls.append(("embedding", model_id))


class FakeInvalidator:
    def __init__(self) -> None:
        self.calls = []

    def invalidate_provider_authorizations(self, provider_id, updated_at) -> None:
        self.calls.append(provider_id)


def make_service(*, repository=None, client=None):
    credentials = FakeCredentials()
    service = ProviderService(repository=repository or FakeRepository(), credentials=credentials,
                              client=client or FakeClient(), authorization_invalidator=FakeInvalidator())
    return service, service.repository, credentials


def discovered_provider(service):
    return service.test(service.create("Cloud", "https://provider.example/v1", "secret").provider_id)


def test_provider_test_only_discovers_and_checks_health() -> None:
    client = FakeClient()
    service, _, _ = make_service(client=client)

    provider = discovered_provider(service)

    assert client.calls == ["discover", "health"]
    assert provider.verification.is_verified is True
    assert {model.model_id for model in provider.models} == {"chat-model", "embedding-model"}
    assert all(model.model_type is None for model in provider.models)


def test_models_are_verified_by_type_and_defaults_are_independent() -> None:
    client = FakeClient()
    service, _, _ = make_service(client=client)
    provider = discovered_provider(service)

    service.configure_model(provider.provider_id, "chat-model", "chat")
    service.configure_model(provider.provider_id, "embedding-model", "embedding")
    service.test_model(provider.provider_id, "chat-model")
    service.test_model(provider.provider_id, "embedding-model")
    service.set_default("chat", provider.provider_id, "chat-model")
    service.set_default("embedding", provider.provider_id, "embedding-model")

    assert client.calls[-2:] == [("chat", "chat-model"), ("embedding", "embedding-model")]
    assert service.resolve_model("chat").model.model_id == "chat-model"
    assert service.resolve_model("embedding").model.model_id == "embedding-model"


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
    service.test_model(provider.provider_id, "chat-model")
    service.set_default("chat", provider.provider_id, "chat-model")

    reopened = SqliteProviderRepository(tmp_path / "providers.sqlite3")
    assert reopened.get(provider.provider_id).models[0].model_type in {"chat", None}
    assert reopened.get_default("chat").model_id == "chat-model"
    assert b"secret" not in (tmp_path / "providers.sqlite3").read_bytes()

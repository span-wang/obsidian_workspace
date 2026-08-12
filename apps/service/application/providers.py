from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from ipaddress import ip_address
from threading import Event, Lock, RLock
from urllib.parse import urlparse
from uuid import uuid4

from domain.embeddings import EmbeddingProfileLocator
from domain.providers import (
    ChatGeneration,
    MODEL_TYPES,
    ModelSelection,
    ProbeResult,
    Provider,
    ProviderModel,
    ProviderProbeResults,
    ResolvedProviderModel,
)
from domain.markdown_structuring import MAX_MARKDOWN_PROVIDER_OUTPUT_TOKENS, MarkdownProviderChunkBudget
from ports.credential_store import CredentialStore
from ports.provider_client import ProviderClient, ProviderClientError
from ports.provider_repository import ProviderRepository
from ports.provider_unit_card_invalidator import ProviderUnitCardInvalidator


class ProviderValidationError(ValueError):
    """Raised when a Provider configuration or default is invalid."""


class ProviderUnavailableError(RuntimeError):
    """Raised when a configured Provider model cannot be used."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProviderService:
    def __init__(
        self,
        *,
        repository: ProviderRepository,
        credentials: CredentialStore,
        client: ProviderClient,
        unit_card_invalidator: ProviderUnitCardInvalidator | None = None,
    ) -> None:
        self.repository = repository
        self.credentials = credentials
        self.client = client
        self.unit_card_invalidator = unit_card_invalidator
        self._locks_guard = Lock()
        self._provider_locks: dict[str, RLock] = {}

    def create(self, name: str, endpoint: str, secret: str | None = None) -> Provider:
        normalized_name = self._validate_name(name)
        normalized_endpoint = self._normalize_endpoint(endpoint)
        if secret is not None and not isinstance(secret, str):
            raise ProviderValidationError("Provider credential is invalid.")
        secret = secret.strip() if secret is not None else None
        if not secret:
            secret = None
        if secret is not None:
            self._validate_secret(secret)
        elif not self._is_loopback_endpoint(normalized_endpoint):
            raise ProviderValidationError("Provider credential is required for non-local endpoints.")
        timestamp = utc_now()
        provider_id = str(uuid4())
        reference = f"ObsidianPersonalKnowledgePlatform/{provider_id}"
        if secret is not None:
            self.credentials.save(reference, secret)
        provider = Provider(provider_id, normalized_name, normalized_endpoint, reference, secret is not None,
                            ProviderProbeResults.not_run(), (), None, timestamp, timestamp)
        try:
            self.repository.save(provider)
        except Exception:
            if secret is not None:
                self.credentials.delete(reference)
            raise
        self._invalidate(provider_id, timestamp)
        return provider

    def update(self, provider_id: str, name: str, endpoint: str, secret: str | None = None) -> Provider:
        normalized_name = self._validate_name(name)
        normalized_endpoint = self._normalize_endpoint(endpoint)
        if secret is not None:
            self._validate_secret(secret)
        with self._provider_lock(provider_id):
            existing = self.repository.get(provider_id)
            timestamp = utc_now()
            updated = replace(existing, name=normalized_name, endpoint=normalized_endpoint,
                              credential_configured=existing.credential_configured or secret is not None,
                              verification=ProviderProbeResults.not_run(), models=(),
                              last_tested_at=None, updated_at=timestamp)
            previous_secret: str | None = None
            if secret is not None:
                try:
                    previous_secret = self.credentials.read(existing.credential_reference)
                except Exception:
                    pass
                self.credentials.save(existing.credential_reference, secret)
            try:
                self.repository.save(updated)
            except Exception:
                if secret is not None:
                    try:
                        if previous_secret is None:
                            self.credentials.delete(existing.credential_reference)
                        else:
                            self.credentials.save(existing.credential_reference, previous_secret)
                    except Exception:
                        pass
                raise
            self._invalidate(provider_id, timestamp)
            return updated

    def get(self, provider_id: str) -> Provider:
        return self.repository.get(provider_id)

    def list(self) -> list[Provider]:
        return self.repository.list()

    def markdown_structure_budget(self) -> MarkdownProviderChunkBudget:
        return self.repository.get_markdown_structure_budget()

    def set_markdown_structure_budget(
        self, minimum_tokens: int, target_tokens: int, maximum_tokens: int
    ) -> MarkdownProviderChunkBudget:
        try:
            budget = MarkdownProviderChunkBudget(minimum_tokens, target_tokens, maximum_tokens)
        except ValueError as error:
            raise ProviderValidationError(str(error)) from error
        self.repository.save_markdown_structure_budget(budget)
        return budget

    def delete(self, provider_id: str) -> None:
        with self._provider_lock(provider_id):
            provider = self.repository.get(provider_id)
            timestamp = utc_now()
            self.credentials.delete(provider.credential_reference)
            self.repository.delete(provider_id)
            self._invalidate(provider_id, timestamp)

    def test(self, provider_id: str, cancel_event: Event | None = None) -> Provider:
        with self._provider_lock(provider_id):
            provider = self.repository.get(provider_id)
            timestamp = utc_now()
            invalidated = replace(
                provider,
                verification=ProviderProbeResults.not_run(),
                models=tuple(replace(model, verification=ProbeResult.not_run(), verified_at=None)
                             for model in provider.models),
                last_tested_at=None,
                updated_at=timestamp,
            )
            self.repository.save(invalidated)
            self._invalidate(provider_id, timestamp)
            try:
                secret = self._credential_for(provider)
            except ProviderUnavailableError:
                unavailable = replace(invalidated, credential_configured=False,
                                      verification=self._failed_verification("Credential is unavailable."),
                                      last_tested_at=timestamp)
                self.repository.save(unavailable)
                self._invalidate(provider_id, timestamp)
                return unavailable

            discovered_models, discovery = self._probe_discovery(invalidated, secret, cancel_event)
            health = self._probe(
                lambda: self.client.health_check(invalidated.endpoint, secret, cancel_event),
                "Provider health check could not be completed.",
            )
            verification = ProviderProbeResults(discovery=discovery, health=health)
            existing_models = {model.model_id: model for model in invalidated.models}
            models = tuple(self._refreshed_model(provider_id, model_id, existing_models.get(model_id))
                           for model_id in discovered_models)
            disappeared_models = tuple(
                replace(model, is_discovered=False,
                        verification=ProbeResult.failed("Model is no longer discoverable."),
                        verified_at=timestamp)
                for model_id, model in existing_models.items() if model_id not in discovered_models
            )
            tested = replace(invalidated, verification=verification,
                             models=models + disappeared_models, last_tested_at=timestamp)
            self.repository.save(tested)
            self._invalidate(provider_id, timestamp)
            return tested

    def configure_model(self, provider_id: str, model_id: str, model_type: str) -> Provider:
        self._validate_model_type(model_type)
        with self._provider_lock(provider_id):
            provider = self.repository.get(provider_id)
            if not provider.verification.is_verified:
                raise ProviderValidationError("The model must appear in the latest successful Provider discovery.")
            model = self._find_model(provider, model_id)
            if not model.is_discovered:
                raise ProviderValidationError("The model must appear in the latest successful Provider discovery.")
            timestamp = utc_now()
            configured = replace(model, model_type=model_type, verification=ProbeResult.not_run(), verified_at=None)
            updated = replace(provider, models=tuple(configured if item.model_id == model_id else item
                                                      for item in provider.models), updated_at=timestamp)
            self.repository.save(updated)
            self._invalidate(provider_id, timestamp)
            return updated

    def test_model(self, provider_id: str, model_id: str, cancel_event: Event | None = None) -> Provider:
        with self._provider_lock(provider_id):
            provider = self.repository.get(provider_id)
            model = self._find_model(provider, model_id)
            if model.model_type is None:
                raise ProviderValidationError("Choose a model type before testing the model.")
            self._validate_model_type(model.model_type)
            if not provider.verification.is_verified or not model.is_discovered:
                raise ProviderValidationError("Run Provider discovery before testing this model.")
            timestamp = utc_now()
            invalidated_model = replace(model, verification=ProbeResult.not_run(), verified_at=None)
            invalidated = replace(provider, models=tuple(
                invalidated_model if item.model_id == model_id else item for item in provider.models
            ), updated_at=timestamp)
            self.repository.save(invalidated)
            self._invalidate(provider_id, timestamp)
            if model.model_type == "rerank" and not self._is_secure_endpoint(provider.endpoint):
                tested_model = replace(
                    invalidated_model,
                    verification=ProbeResult.failed(
                        "Rerank model verification requires an HTTPS or loopback Provider endpoint."
                    ),
                    verified_at=timestamp,
                )
                updated = invalidated
            else:
                try:
                    secret = self._credential_for(provider)
                except ProviderUnavailableError:
                    tested_model = replace(
                        invalidated_model,
                        verification=ProbeResult.failed("Credential is unavailable."),
                        verified_at=timestamp,
                    )
                    updated = replace(invalidated, credential_configured=False)
                else:
                    if model.model_type in {"chat", "markdown"}:
                        verification = self._probe(
                            lambda: self.client.probe_streaming_generation(
                                invalidated.endpoint, secret, model_id, cancel_event
                            ),
                            "Chat model verification could not be completed.",
                        )
                    elif model.model_type == "embedding":
                        verification = self._probe(
                            lambda: self.client.probe_embedding(
                                invalidated.endpoint, secret, model_id, cancel_event
                            ),
                            "Embedding model verification could not be completed.",
                        )
                    else:
                        verification = self._probe(
                            lambda: self.client.probe_rerank(
                                invalidated.endpoint, secret, model_id, cancel_event
                            ),
                            "Rerank model verification could not be completed.",
                        )
                    tested_model = replace(
                        invalidated_model, verification=verification, verified_at=timestamp
                    )
                    updated = invalidated
            updated = replace(updated, models=tuple(
                tested_model if item.model_id == model_id else item for item in updated.models
            ), updated_at=timestamp)
            self.repository.save(updated)
            self._invalidate(provider_id, timestamp)
            return updated

    def get_default(self, model_type: str) -> ModelSelection | None:
        self._validate_model_type(model_type)
        return self.repository.get_default(model_type)

    def set_default(self, model_type: str, provider_id: str, model_id: str) -> ModelSelection:
        self._validate_model_type(model_type)
        with self._provider_lock(provider_id):
            provider = self.repository.get(provider_id)
            model = self._find_model(provider, model_id)
            if model.model_type != model_type or not model.verification.ok or not model.is_discovered:
                raise ProviderValidationError("The selected Provider model is not verified for this model type.")
            if not provider.verification.is_verified:
                raise ProviderValidationError("The selected Provider has not passed discovery and health checks.")
            if not self._credential_is_available(provider):
                raise ProviderValidationError("The selected Provider credential is unavailable.")
            previous = self.repository.get_default(model_type)
            selection = ModelSelection(model_type, provider_id, model_id, utc_now())
            self.repository.save_default(selection)
            if previous is not None and previous.provider_id != provider_id:
                self._invalidate(previous.provider_id, selection.updated_at)
            self._invalidate(provider_id, selection.updated_at)
            return selection

    def clear_default(self, model_type: str) -> None:
        self._validate_model_type(model_type)
        previous = self.repository.get_default(model_type)
        if previous is None:
            return
        with self._provider_lock(previous.provider_id):
            self.repository.delete_default(model_type)
            self._invalidate(previous.provider_id, utc_now())

    def resolve_model(self, model_type: str) -> ResolvedProviderModel:
        self._validate_model_type(model_type)
        selection = self.repository.get_default(model_type)
        if selection is None:
            raise ProviderUnavailableError(f"No {model_type} Provider model is selected. Choose a verified model.")
        return self.resolve_specific_model(model_type, selection.provider_id, selection.model_id)

    def resolve_specific_model(
        self, model_type: str, provider_id: str, model_id: str, *, require_https: bool = False
    ) -> ResolvedProviderModel:
        self._validate_model_type(model_type)
        try:
            provider = self.repository.get(provider_id)
        except KeyError as error:
            raise ProviderUnavailableError(f"The selected {model_type} Provider is unavailable. Choose another model.") from error
        if require_https and not self._is_secure_endpoint(provider.endpoint):
            raise ProviderUnavailableError("The selected Provider must use an HTTPS or loopback endpoint.")
        if not self._credential_is_available(provider):
            raise ProviderUnavailableError("The selected Provider credential is unavailable. Reconfigure it.")
        if not provider.verification.is_verified:
            raise ProviderUnavailableError("The selected Provider has not passed discovery and health checks.")
        try:
            model = self._find_model(provider, model_id)
        except ProviderValidationError as error:
            raise ProviderUnavailableError(f"The selected {model_type} Model is unavailable. Choose another model.") from error
        if model.model_type != model_type or not model.verification.ok or not model.is_discovered:
            raise ProviderUnavailableError(f"The selected {model_type} Model is unavailable. Choose another model.")
        return ResolvedProviderModel(provider, model)

    def generate_chat(
        self,
        provider_id: str,
        model_id: str,
        prompt: str,
        *,
        expected_provider_updated_at: str | None = None,
    ) -> str:
        normalized_prompt = prompt.strip()
        if not normalized_prompt or len(normalized_prompt) > 200_000:
            raise ProviderUnavailableError("The generation request is invalid.")
        if expected_provider_updated_at is not None and not expected_provider_updated_at:
            raise ProviderUnavailableError("The chat Provider configuration revision is invalid.")
        with self._provider_lock(provider_id):
            resolved = self.resolve_specific_model("chat", provider_id, model_id)
            if (
                expected_provider_updated_at is not None
                and resolved.provider.updated_at != expected_provider_updated_at
            ):
                raise ProviderUnavailableError(
                    "The chat Provider configuration changed. Retry the request."
                )
            secret = self._credential_for(resolved.provider)
            try:
                return self.client.generate_chat(
                    resolved.provider.endpoint, secret, resolved.model.model_id, normalized_prompt
                )
            except ProviderClientError as error:
                raise ProviderUnavailableError(
                    f"The selected Provider could not generate this section: {error}"
                ) from error
            except Exception as error:
                raise ProviderUnavailableError("The selected Provider could not generate this section.") from error

    def generate_markdown(
        self,
        provider_id: str,
        model_id: str,
        prompt: str,
        *,
        max_output_tokens: int,
        expected_provider_updated_at: str | None = None,
    ) -> str:
        normalized_prompt = prompt.strip()
        if not normalized_prompt or len(normalized_prompt) > 200_000:
            raise ProviderUnavailableError("The Markdown structuring request is invalid.")
        if (
            type(max_output_tokens) is not int
            or not 1 <= max_output_tokens <= MAX_MARKDOWN_PROVIDER_OUTPUT_TOKENS
        ):
            raise ProviderUnavailableError("The Markdown structuring output token limit is invalid.")
        if expected_provider_updated_at is not None and not expected_provider_updated_at:
            raise ProviderUnavailableError("The Markdown Provider configuration revision is invalid.")
        with self._provider_lock(provider_id):
            resolved = self.resolve_specific_model("markdown", provider_id, model_id, require_https=True)
            if (
                expected_provider_updated_at is not None
                and resolved.provider.updated_at != expected_provider_updated_at
            ):
                raise ProviderUnavailableError(
                    "The Markdown Provider configuration changed. Retry the request."
                )
            secret = self._credential_for(resolved.provider)
            try:
                return self.client.generate_chat_with_usage(
                    resolved.provider.endpoint,
                    secret,
                    resolved.model.model_id,
                    normalized_prompt,
                    max_output_tokens,
                ).content
            except ProviderClientError as error:
                raise ProviderUnavailableError(
                    f"The selected Markdown Provider could not structure this section: {error}"
                ) from error
            except Exception as error:
                raise ProviderUnavailableError(
                    "The selected Markdown Provider could not structure this section."
                ) from error

    def generate_chat_with_usage(
        self,
        provider_id: str,
        model_id: str,
        prompt: str,
        *,
        max_output_tokens: int,
        expected_provider_updated_at: str | None = None,
    ) -> ChatGeneration:
        normalized_prompt = prompt.strip()
        if not normalized_prompt or len(normalized_prompt) > 200_000:
            raise ProviderUnavailableError("The generation request is invalid.")
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 4_096:
            raise ProviderUnavailableError("The generation output token limit is invalid.")
        if expected_provider_updated_at is not None and not expected_provider_updated_at:
            raise ProviderUnavailableError("The chat Provider configuration revision is invalid.")
        with self._provider_lock(provider_id):
            resolved = self.resolve_specific_model(
                "chat", provider_id, model_id, require_https=True
            )
            if (
                expected_provider_updated_at is not None
                and resolved.provider.updated_at != expected_provider_updated_at
            ):
                raise ProviderUnavailableError(
                    "The chat Provider configuration changed. Retry the request."
                )
            secret = self._credential_for(resolved.provider)
            try:
                return self.client.generate_chat_with_usage(
                    resolved.provider.endpoint,
                    secret,
                    resolved.model.model_id,
                    normalized_prompt,
                    max_output_tokens,
                )
            except ProviderClientError as error:
                raise ProviderUnavailableError(
                    f"The selected Provider could not generate this section: {error}"
                ) from error
            except Exception as error:
                raise ProviderUnavailableError("The selected Provider could not generate this section.") from error

    def stream_chat(self, provider_id: str, model_id: str, prompt: str):
        normalized_prompt = prompt.strip()
        if not normalized_prompt or len(normalized_prompt) > 200_000:
            raise ProviderUnavailableError("The generation request is invalid.")
        with self._provider_lock(provider_id):
            resolved = self.resolve_specific_model("chat", provider_id, model_id)
            secret = self._credential_for(resolved.provider)
            try:
                yield from self.client.stream_chat(
                    resolved.provider.endpoint, secret, resolved.model.model_id, normalized_prompt
                )
            except ProviderClientError as error:
                raise ProviderUnavailableError(
                    f"The selected Provider could not generate this section: {error}"
                ) from error
            except Exception as error:
                raise ProviderUnavailableError("The selected Provider could not generate this section.") from error

    def create_embeddings(
        self,
        provider_id: str,
        model_id: str,
        inputs: tuple[str, ...],
        *,
        expected_provider_updated_at: str,
    ) -> tuple[tuple[float, ...], ...]:
        if not isinstance(expected_provider_updated_at, str) or not expected_provider_updated_at:
            raise ProviderUnavailableError("The embedding Provider configuration revision is invalid.")
        with self._provider_lock(provider_id):
            resolved = self.resolve_specific_model("embedding", provider_id, model_id)
            if resolved.provider.updated_at != expected_provider_updated_at:
                raise ProviderUnavailableError(
                    "The embedding Provider configuration changed. Retry the request."
                )
            if not self._is_secure_endpoint(resolved.provider.endpoint):
                raise ProviderUnavailableError("Embedding requests require an HTTPS or loopback Provider endpoint.")
            secret = self._credential_for(resolved.provider)
            try:
                return self.client.create_embeddings(
                    resolved.provider.endpoint, secret, resolved.model.model_id, inputs
                )
            except ProviderClientError as error:
                raise ProviderUnavailableError(
                    f"The selected Provider could not create embeddings: {error}"
                ) from error
            except Exception as error:
                raise ProviderUnavailableError(
                    "The selected Provider could not create embeddings."
                ) from error

    def rerank(
        self,
        provider_id: str,
        model_id: str,
        query: str,
        documents: tuple[str, ...],
        *,
        expected_provider_updated_at: str,
    ) -> tuple[float, ...]:
        if not isinstance(query, str) or not query.strip() or len(query) > 2_000:
            raise ProviderUnavailableError("The rerank request is invalid.")
        if not isinstance(documents, tuple) or not documents:
            raise ProviderUnavailableError("The rerank request is invalid.")
        if not isinstance(expected_provider_updated_at, str) or not expected_provider_updated_at:
            raise ProviderUnavailableError("The rerank Provider configuration revision is invalid.")
        with self._provider_lock(provider_id):
            resolved = self.resolve_specific_model(
                "rerank", provider_id, model_id, require_https=True
            )
            if resolved.provider.updated_at != expected_provider_updated_at:
                raise ProviderUnavailableError(
                    "The rerank Provider configuration changed. Retry the request."
                )
            secret = self._credential_for(resolved.provider)
            try:
                return self.client.rerank(
                    resolved.provider.endpoint,
                    secret,
                    resolved.model.model_id,
                    query.strip(),
                    documents,
                )
            except ProviderClientError as error:
                raise ProviderUnavailableError(
                    f"The selected Provider could not rerank these candidates: {error}"
                ) from error
            except Exception as error:
                raise ProviderUnavailableError(
                    "The selected Provider could not rerank these candidates."
                ) from error

    def embedding_profile_locator(
        self,
        provider_id: str,
        model_id: str,
        *,
        expected_provider_updated_at: str,
    ) -> EmbeddingProfileLocator:
        if not isinstance(expected_provider_updated_at, str) or not expected_provider_updated_at:
            raise ProviderUnavailableError("The embedding Provider configuration revision is invalid.")
        with self._provider_lock(provider_id):
            resolved = self.resolve_specific_model("embedding", provider_id, model_id)
            if resolved.provider.updated_at != expected_provider_updated_at:
                raise ProviderUnavailableError(
                    "The embedding Provider configuration changed. Retry the request."
                )
            if not self._is_secure_endpoint(resolved.provider.endpoint):
                raise ProviderUnavailableError("Embedding requests require an HTTPS or loopback Provider endpoint.")
            return EmbeddingProfileLocator(
                provider_id=resolved.provider.provider_id,
                endpoint=resolved.provider.endpoint,
                configuration_revision=resolved.provider.updated_at,
                model_id=resolved.model.model_id,
            )

    @staticmethod
    def _validate_name(name: str) -> str:
        normalized = name.strip()
        if not normalized:
            raise ProviderValidationError("Provider name is required.")
        return normalized[:120]

    @staticmethod
    def _normalize_endpoint(endpoint: str) -> str:
        normalized = endpoint.strip().rstrip("/")
        parsed = urlparse(normalized)
        if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ProviderValidationError("Provider endpoint must be an absolute HTTP or HTTPS URL.")
        return normalized

    @staticmethod
    def _validate_secret(secret: str) -> None:
        if not secret:
            raise ProviderValidationError("Provider credential is required.")

    @staticmethod
    def _validate_model_type(model_type: str) -> None:
        if model_type not in MODEL_TYPES:
            raise ProviderValidationError("Model type must be chat, embedding, rerank, or markdown.")

    @staticmethod
    def _find_model(provider: Provider, model_id: str) -> ProviderModel:
        for model in provider.models:
            if model.model_id == model_id:
                return model
        raise ProviderValidationError("The selected Provider model was not discovered.")

    @staticmethod
    def _refreshed_model(provider_id: str, model_id: str, existing: ProviderModel | None) -> ProviderModel:
        if existing is None:
            return ProviderModel(provider_id, model_id, None, ProbeResult.not_run(), True, None)
        return replace(existing, is_discovered=True, verification=ProbeResult.not_run(), verified_at=None)

    def _credential_is_available(self, provider: Provider) -> bool:
        if not provider.credential_configured:
            return self._is_loopback_endpoint(provider.endpoint)
        try:
            self.credentials.read(provider.credential_reference)
        except Exception:
            return False
        return True

    def _credential_for(self, provider: Provider) -> str:
        if not provider.credential_configured:
            if self._is_loopback_endpoint(provider.endpoint):
                return ""
            raise ProviderUnavailableError("The selected Provider credential is unavailable.")
        try:
            return self.credentials.read(provider.credential_reference)
        except Exception as error:
            raise ProviderUnavailableError("The selected Provider credential is unavailable.") from error

    @staticmethod
    def _is_loopback_endpoint(endpoint: str) -> bool:
        hostname = urlparse(endpoint).hostname
        if hostname is None:
            return False
        normalized_hostname = hostname.lower().rstrip(".")
        if normalized_hostname == "localhost" or normalized_hostname.endswith(".localhost"):
            return True
        try:
            return ip_address(normalized_hostname).is_loopback
        except ValueError:
            return False

    @classmethod
    def _is_secure_endpoint(cls, endpoint: str) -> bool:
        return urlparse(endpoint).scheme == "https" or cls._is_loopback_endpoint(endpoint)

    def _probe_discovery(self, provider: Provider, secret: str, cancel_event: Event | None) -> tuple[tuple[str, ...], ProbeResult]:
        try:
            return self.client.discover_models(provider.endpoint, secret, cancel_event), ProbeResult.success()
        except Exception:
            return (), ProbeResult.failed("Model discovery could not be completed.")

    @staticmethod
    def _probe(operation, failure_reason: str) -> ProbeResult:
        try:
            operation()
        except Exception:
            return ProbeResult.failed(failure_reason)
        return ProbeResult.success()

    @staticmethod
    def _failed_verification(reason: str) -> ProviderProbeResults:
        failed = ProbeResult.failed(reason)
        return ProviderProbeResults(failed, failed)

    def _invalidate(self, provider_id: str, timestamp: str) -> None:
        if self.unit_card_invalidator is not None:
            self.unit_card_invalidator.invalidate_unit_cards_for_provider_change(provider_id, timestamp)

    @contextmanager
    def _provider_lock(self, provider_id: str):
        with self._locks_guard:
            lock = self._provider_locks.setdefault(provider_id, RLock())
        with lock:
            yield

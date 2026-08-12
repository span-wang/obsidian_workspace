from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from application.policies import PolicyService
from domain.online_document_parser import OnlineParseProvider, OnlineParseSelection
from ports.credential_store import CredentialStore
from ports.online_document_parser import OnlineDocumentParser, OnlineDocumentParserError
from ports.online_parse_provider_repository import OnlineParseProviderRepository


class OnlineParseProviderValidationError(ValueError):
    pass


class OnlineParseProviderUnavailableError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OnlineParseProviderService:
    def __init__(
        self,
        repository: OnlineParseProviderRepository,
        credentials: CredentialStore,
        parser: OnlineDocumentParser,
        policy_service: PolicyService,
    ) -> None:
        self.repository = repository
        self.credentials = credentials
        self.parser = parser
        self.policy_service = policy_service

    def list(self) -> tuple[OnlineParseProvider, ...]:
        return self.repository.list()

    def get(self, provider_id: str) -> OnlineParseProvider:
        return self.repository.get(provider_id)

    def configure(
        self, provider_id: str, *, endpoint: str | None, secret: str | None
    ) -> OnlineParseProvider:
        provider = self.repository.get(provider_id)
        if endpoint is not None and not endpoint.startswith("https://"):
            raise OnlineParseProviderValidationError("在线解析 Provider 地址必须使用 HTTPS。")
        if secret is not None and not secret.strip():
            raise OnlineParseProviderValidationError("Provider 凭据不能为空。")
        timestamp = utc_now()
        previous_secret: str | None = None
        if secret is not None:
            try:
                previous_secret = self.credentials.read(provider.credential_reference)
            except KeyError:
                previous_secret = None
            self.credentials.save(provider.credential_reference, secret.strip())
        configured = replace(
            provider,
            endpoint=endpoint,
            credential_configured=provider.credential_configured or secret is not None,
            verified=False,
            verification_reason="配置已更新，需重新测试。",
            last_tested_at=None,
            updated_at=timestamp,
        )
        try:
            self.repository.save(configured)
        except Exception:
            if secret is not None:
                if previous_secret is None:
                    self.credentials.delete(provider.credential_reference)
                else:
                    self.credentials.save(provider.credential_reference, previous_secret)
            raise
        return configured

    def test(self, provider_id: str) -> OnlineParseProvider:
        provider = self.repository.get(provider_id)
        timestamp = utc_now()
        try:
            secret = self.credentials.read(provider.credential_reference)
        except KeyError:
            unavailable = replace(
                provider,
                credential_configured=False,
                verified=False,
                verification_reason="凭据不可用。",
                last_tested_at=timestamp,
                updated_at=timestamp,
            )
            self.repository.save(unavailable)
            return unavailable
        selection = self._selection(provider, policy_revision=0, policy_path="provider-test")
        try:
            self.parser.test(selection, secret)
        except OnlineDocumentParserError as error:
            tested = replace(
                provider,
                verified=False,
                verification_reason=str(error),
                last_tested_at=timestamp,
                updated_at=timestamp,
            )
        else:
            tested = replace(
                provider,
                credential_configured=True,
                verified=True,
                verification_reason=None,
                last_tested_at=timestamp,
                updated_at=timestamp,
            )
        self.repository.save(tested)
        return tested

    def select_for_import(
        self, provider_id: str, vault_id: str, source_path: Path
    ) -> OnlineParseSelection:
        provider = self.repository.get(provider_id)
        if source_path.suffix.casefold() != ".pdf":
            raise OnlineParseProviderValidationError("在线解析仅支持 PDF 文件。")
        self._require_available(provider)
        policy_path = source_path.name
        policy = self.policy_service.get(vault_id)
        evaluation = self.policy_service.preview(vault_id, policy_path, None, "outbound")
        if not evaluation.allowed:
            raise OnlineParseProviderValidationError("Vault 外发策略不允许将此 PDF 发送给在线解析 Provider。")
        return self._selection(
            provider, policy_revision=policy.policy_revision, policy_path=policy_path
        )

    def revalidate(self, selection: OnlineParseSelection, vault_id: str) -> None:
        provider = self.repository.get(selection.provider_id)
        if provider.kind != selection.provider_kind or provider.endpoint != selection.endpoint:
            raise OnlineParseProviderUnavailableError("在线解析 Provider 配置已变化；请重新创建任务。")
        self._require_available(provider)
        evaluation = self.policy_service.preview(vault_id, selection.policy_path, None, "outbound")
        if not evaluation.allowed:
            raise OnlineParseProviderUnavailableError("Vault 外发策略不允许提交此 PDF。")

    def _require_available(self, provider: OnlineParseProvider) -> None:
        if not provider.verified:
            raise OnlineParseProviderUnavailableError("请选择已完成连接测试的在线解析 Provider。")
        if not provider.credential_configured:
            raise OnlineParseProviderUnavailableError("在线解析 Provider 的凭据不可用。")
        try:
            self.credentials.read(provider.credential_reference)
        except KeyError as error:
            raise OnlineParseProviderUnavailableError("在线解析 Provider 的凭据不可用。") from error

    @staticmethod
    def _selection(
        provider: OnlineParseProvider, *, policy_revision: int, policy_path: str
    ) -> OnlineParseSelection:
        return OnlineParseSelection(
            provider_id=provider.provider_id,
            provider_kind=provider.kind,
            provider_name=provider.name,
            endpoint=provider.endpoint,
            model=provider.model,
            credential_reference=provider.credential_reference,
            policy_revision=policy_revision,
            policy_path=policy_path,
        )

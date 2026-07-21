import asyncio
import json
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.local_import_task_runner import LocalImportTaskRunner
from adapters.openai_compatible_provider import OpenAiCompatibleProviderClient
from adapters.sqlite_provider_repository import SqliteProviderRepository
from adapters.sqlite_source_repository import SqliteSourceRepository
from adapters.sqlite_task_repository import SqliteImportTaskRepository
from adapters.sqlite_vault_repository import SqliteVaultRepository
from adapters.windows_credential_manager import WindowsCredentialManager
from adapters.windows_directory_picker import WindowsDirectoryPicker
from adapters.windows_import_picker import WindowsImportPicker
from application.directory_selections import DirectorySelectionError, DirectorySelectionStore
from application.import_selections import ImportSelectionError, ImportSelectionStore
from application.ingest import ImportTaskError, ImportTaskService
from application.local_session import LocalSession, create_local_session
from application.policies import (
    OutboundAuthorizationDenied,
    PolicyService,
    PolicyValidationError,
)
from application.providers import (
    ProviderService,
    ProviderUnavailableError,
    ProviderValidationError,
)
from application.vaults import VaultConflictError, VaultService, VaultValidationError
from api.errors import error_response
from api.runtime import RuntimeState, initialize_runtime
from domain.policies import (
    ExclusionRule,
    OutboundAuthorization,
    OutboundScope,
    PolicyEvaluation,
    VaultPolicy,
)
from domain.providers import ModelSelection, ProbeResult, Provider, ProviderModel
from domain.tasks import ImportTask, ImportTaskEvent, ImportTaskItem
from domain.vaults import Vault


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 6240
SERVICE_NAME = "obsidian-personal-knowledge-platform"
WEB_BUILD_DIRECTORY = Path(__file__).resolve().parents[2] / "web" / "dist"
LOCAL_SESSION_COOKIE_NAME = "obsidian_platform_session"
DEFAULT_BROWSER_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/"


class VaultPathCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection_id: str
    managed_root: str = "platform"


class PolicyModeCommand(BaseModel):
    outbound_mode: str


class ExclusionRuleCommand(BaseModel):
    kind: str
    relative_path: str


class PolicyPreviewCommand(BaseModel):
    source_path: str
    derived_path: str | None = None
    stage: str
    candidate_kind: str | None = None
    candidate_relative_path: str | None = None
    replacing_rule_id: str | None = None


class OutboundScopeCommand(BaseModel):
    source_path: str
    derived_path: str | None = None


class OutboundAuthorizationCommand(BaseModel):
    provider_id: str | None = None
    model_id: str | None = None
    operation: str
    task_id: str
    scopes: list[OutboundScopeCommand]


class OutboundAuthorizationCheckCommand(OutboundAuthorizationCommand):
    pass


class AuthorizationConfirmationCommand(BaseModel):
    approved: bool


class ProviderCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    endpoint: str
    secret: str


class ProviderUpdateCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    endpoint: str
    secret: str | None = None


class ModelDefaultCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    model_id: str


class ModelConfigurationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str
    model_type: str


class ModelTestCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str


class ImportFilesSelectionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    multiple: bool = False


class ImportTaskCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vault_id: str
    selection_id: str


class PortInUseError(RuntimeError):
    """Raised when the fixed loopback endpoint belongs to another process."""


class WebBuildMissingError(RuntimeError):
    """Raised when the production web build has not been created."""


def reserve_loopback_listener(
    host: str = DEFAULT_HOST, port: int = DEFAULT_PORT
) -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        listener.bind((host, port))
        listener.listen()
    except OSError as error:
        listener.close()
        raise PortInUseError(f"Port {port} is already in use.") from error
    return listener


def is_verified_running_instance() -> bool:
    try:
        with urlopen(f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/api/health", timeout=1) as response:
            payload = json.loads(response.read())
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return False
    return (
        response.status == 200
        and isinstance(payload, dict)
        and payload.get("service") == SERVICE_NAME
        and payload.get("host") == DEFAULT_HOST
        and payload.get("port") == DEFAULT_PORT
    )


def require_web_build() -> Path:
    if not WEB_BUILD_DIRECTORY.is_dir():
        raise WebBuildMissingError("Web build is missing. Run npm run build.")
    return WEB_BUILD_DIRECTORY


def workbench_response(local_session: LocalSession) -> FileResponse:
    response = FileResponse(WEB_BUILD_DIRECTORY / "index.html")
    response.set_cookie(
        key=LOCAL_SESSION_COOKIE_NAME,
        value=local_session.secret,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return response


def local_session_status(local_session: LocalSession, candidate: str | None) -> dict[str, str]:
    if not local_session.is_valid(candidate):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "local_session_required",
                "message": "A valid local browser session is required.",
                "details": {},
                "retryable": False,
            },
        )
    return {"status": "ok", "scope": "local"}


def rule_payload(rule: ExclusionRule) -> dict[str, str]:
    return {
        "rule_id": rule.rule_id,
        "vault_id": rule.vault_id,
        "kind": rule.kind,
        "relative_path": rule.relative_path,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    }


def policy_payload(policy: VaultPolicy, rules: list[ExclusionRule]) -> dict[str, object]:
    return {
        "outbound_mode": policy.outbound_mode,
        "policy_revision": policy.policy_revision,
        "updated_at": policy.updated_at,
        "rules": [rule_payload(rule) for rule in rules],
    }


def preview_payload(preview: PolicyEvaluation) -> dict[str, object]:
    return {
        "allowed": preview.allowed,
        "stage": preview.stage,
        "matched_rule_ids": list(preview.matched_rule_ids),
        "matched_kinds": list(preview.matched_kinds),
        "reason": preview.reason,
    }


def authorization_payload(authorization: OutboundAuthorization) -> dict[str, object]:
    return {
        "authorization_id": authorization.authorization_id,
        "vault_id": authorization.vault_id,
        "policy_revision": authorization.policy_revision,
        "provider_id": authorization.provider_id,
        "model_id": authorization.model_id,
        "operation": authorization.operation,
        "task_id": authorization.task_id,
        "snapshot_digest": authorization.snapshot_digest,
        "scope_summary": authorization.scope_summary,
        "actual_scope_summary": authorization.actual_scope_summary,
        "actual_scope_digest": authorization.actual_scope_digest,
        "status": authorization.status,
        "created_at": authorization.created_at,
        "updated_at": authorization.updated_at,
    }


def probe_payload(probe: ProbeResult) -> dict[str, object]:
    return {"ok": probe.ok, "reason": probe.reason}


def provider_model_payload(model: ProviderModel) -> dict[str, object]:
    return {
        "model_id": model.model_id,
        "model_type": model.model_type,
        "verification": probe_payload(model.verification),
        "is_discovered": model.is_discovered,
        "verified_at": model.verified_at,
    }


def provider_payload(provider: Provider) -> dict[str, object]:
    return {
        "provider_id": provider.provider_id,
        "name": provider.name,
        "endpoint": provider.endpoint,
        "transport": provider.transport,
        "credential_configured": provider.credential_configured,
        "verification": {
            "discovery": probe_payload(provider.verification.discovery),
            "health": probe_payload(provider.verification.health),
            "is_verified": provider.verification.is_verified,
        },
        "models": [provider_model_payload(model) for model in provider.models],
        "last_tested_at": provider.last_tested_at,
        "created_at": provider.created_at,
        "updated_at": provider.updated_at,
    }


def model_default_payload(provider_service: ProviderService, model_type: str) -> dict[str, object]:
    selection = provider_service.get_default(model_type)
    if selection is None:
        return {
            "default": None,
            "status": "unconfigured",
            "reason": f"No {model_type} Provider model is selected.",
        }
    selection_payload = {
        "provider_id": selection.provider_id,
        "model_id": selection.model_id,
        "updated_at": selection.updated_at,
    }
    try:
        provider_service.resolve_model(model_type)
    except ProviderUnavailableError as error:
        return {"default": selection_payload, "status": "unavailable", "reason": str(error)[:200]}
    return {"default": selection_payload, "status": "available", "reason": None}


def model_defaults_payload(provider_service: ProviderService) -> dict[str, object]:
    return {
        "chat": model_default_payload(provider_service, "chat"),
        "embedding": model_default_payload(provider_service, "embedding"),
    }


def vault_payload(
    vault: Vault, policy: VaultPolicy | None = None, rules: list[ExclusionRule] | None = None
) -> dict[str, object]:
    payload: dict[str, object] = {
        "vault_id": vault.vault_id,
        "path": str(vault.path),
        "managed_root_relative_path": vault.managed_root_relative_path,
        "managed_root": str(vault.managed_root),
        "source_directory": str(vault.source_directory),
        "note_directory": str(vault.note_directory),
        "authorization_status": vault.authorization_status,
        "access_status": vault.access_status,
        "access_reason": vault.access_reason,
        "index_status": vault.index_status,
        "created_at": vault.created_at,
        "updated_at": vault.updated_at,
        "is_current": vault.is_current,
        "recovery_actions": list(vault.recovery_actions),
    }
    if policy is not None:
        payload["policy"] = policy_payload(policy, rules or [])
    return payload


def import_task_payload(task: ImportTask) -> dict[str, object]:
    return {
        "task_id": task.task_id,
        "vault_id": task.vault_id,
        "vault_label": task.vault_label,
        "scope_label": task.scope_label,
        "lifecycle": task.lifecycle,
        "phase": task.phase,
        "current_item_label": task.current_item_label,
        "counts": {
            "discovered": task.counts.discovered,
            "supported": task.counts.supported,
            "skipped": task.counts.skipped,
            "unsupported": task.counts.unsupported,
            "failed": task.counts.failed,
            "new": task.counts.new,
            "duplicate": task.counts.duplicate,
            "possible_version": task.counts.possible_version,
            "identity_failed": task.counts.identity_failed,
            "parsed": task.counts.parsed,
            "parse_failed": task.counts.parse_failed,
            "required_check": task.counts.required_check,
        },
        "recovery_actions": list(task.recovery_actions),
        "failure_reason": task.failure_reason,
        "parent_task_id": task.parent_task_id,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def import_task_item_payload(item: ImportTaskItem) -> dict[str, object]:
    return {
        "item_id": item.item_id,
        "label": item.label,
        "category": item.category,
        "document_kind": item.document_kind,
        "reason": item.reason,
        "content_sha256": item.content_sha256,
        "source_id": item.source_id,
        "identity_status": item.identity_status,
        "parse_status": item.parse_status,
        "parse_confidence": item.parse_confidence,
        "parse_issue_count": item.parse_issue_count,
        "parse_locator_summary": item.parse_locator_summary,
        "parse_issue_summary": item.parse_issue_summary,
        "version_suggestion": (
            {
                "candidate_source_id": item.version_suggestion.candidate_source_id,
                "previous_content_sha256": item.version_suggestion.previous_content_sha256,
                "reason": item.version_suggestion.reason,
                "status": item.version_suggestion.status,
            }
            if item.version_suggestion is not None
            else None
        ),
    }


def import_task_event_payload(event: ImportTaskEvent) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "created_at": event.created_at,
    }


def vault_error(error: Exception) -> HTTPException:
    if isinstance(error, DirectorySelectionError):
        return HTTPException(
            status_code=400,
            detail={
                "code": "directory_selection_invalid",
                "message": str(error),
                "details": {},
                "retryable": True,
            },
        )
    if isinstance(error, VaultConflictError):
        return HTTPException(
            status_code=409,
            detail={
                "code": "vault_conflict",
                "message": str(error),
                "details": {},
                "retryable": False,
            },
        )
    if isinstance(error, KeyError):
        return HTTPException(
            status_code=404,
            detail={
                "code": "vault_not_found",
                "message": "Vault authorization was not found.",
                "details": {},
                "retryable": False,
            },
        )
    if isinstance(error, VaultValidationError):
        return HTTPException(
            status_code=400,
            detail={
                "code": "vault_validation_failed",
                "message": str(error),
                "details": {},
                "retryable": True,
            },
        )
    if isinstance(error, PolicyValidationError):
        return HTTPException(
            status_code=400,
            detail={
                "code": "policy_validation_failed",
                "message": str(error),
                "details": {},
                "retryable": True,
            },
        )
    if isinstance(error, OutboundAuthorizationDenied):
        return HTTPException(
            status_code=403,
            detail={
                "code": "outbound_authorization_denied",
                "message": str(error),
                "details": {},
                "retryable": False,
            },
        )
    return HTTPException(
        status_code=500,
        detail={
            "code": "vault_operation_failed",
            "message": "The vault operation could not be completed.",
            "details": {},
            "retryable": True,
        },
    )


def import_task_error(error: Exception) -> HTTPException:
    if isinstance(error, ImportSelectionError):
        return HTTPException(
            status_code=400,
            detail={
                "code": "import_selection_invalid",
                "message": str(error),
                "details": {},
                "retryable": True,
            },
        )
    if isinstance(error, KeyError):
        return HTTPException(
            status_code=404,
            detail={
                "code": "import_task_not_found",
                "message": "Import task was not found.",
                "details": {},
                "retryable": False,
            },
        )
    if isinstance(error, (ImportTaskError, VaultValidationError)):
        return HTTPException(
            status_code=400,
            detail={
                "code": "import_task_validation_failed",
                "message": str(error),
                "details": {},
                "retryable": True,
            },
        )
    return HTTPException(
        status_code=500,
        detail={
            "code": "import_task_operation_failed",
            "message": "The import task operation could not be completed.",
            "details": {},
            "retryable": True,
        },
    )


def provider_error(error: Exception) -> HTTPException:
    if isinstance(error, KeyError):
        return HTTPException(
            status_code=404,
            detail={
                "code": "provider_not_found",
                "message": "Provider configuration was not found.",
                "details": {},
                "retryable": False,
            },
        )
    if isinstance(error, ProviderValidationError):
        return HTTPException(
            status_code=400,
            detail={
                "code": "provider_validation_failed",
                "message": str(error),
                "details": {},
                "retryable": True,
            },
        )
    if isinstance(error, ProviderUnavailableError):
        return HTTPException(
            status_code=409,
            detail={
                "code": "background_model_unavailable",
                "message": str(error),
                "details": {},
                "retryable": True,
            },
        )
    return HTTPException(
        status_code=500,
        detail={
            "code": "provider_operation_failed",
            "message": "The Provider operation could not be completed.",
            "details": {},
            "retryable": True,
        },
    )


def redacted_validation_errors(errors: list[dict[str, object]]) -> list[dict[str, object]]:
    return [{key: value for key, value in error.items() if key != "input"} for error in errors]


def require_local_session(app: FastAPI, request: Request) -> str:
    candidate = request.cookies.get(LOCAL_SESSION_COOKIE_NAME)
    local_session_status(
        app.state.local_session,
        candidate,
    )
    assert candidate is not None
    return candidate


def vault_with_policy_payload(app: FastAPI, vault: Vault) -> dict[str, object]:
    policy = app.state.policy_service.get(vault.vault_id)
    return vault_payload(
        vault,
        policy=policy,
        rules=app.state.policy_service.list_rules(vault.vault_id),
    )


def create_app(
    *,
    runtime: RuntimeState | None = None,
    vault_service: VaultService | None = None,
    policy_service: PolicyService | None = None,
    provider_service: ProviderService | None = None,
    directory_picker: WindowsDirectoryPicker | None = None,
    directory_selections: DirectorySelectionStore | None = None,
    import_task_service: ImportTaskService | None = None,
    import_picker: WindowsImportPicker | None = None,
    import_selections: ImportSelectionStore | None = None,
) -> FastAPI:
    web_build_directory = require_web_build()
    runtime = runtime or initialize_runtime()
    app = FastAPI(title="Obsidian Personal Knowledge Platform")
    app.state.runtime = runtime
    app.state.local_session = create_local_session()
    if vault_service is None:
        repository = SqliteVaultRepository(runtime.data_directory / "vaults.sqlite3")
        vault_service = VaultService(
            repository=repository,
            filesystem=LocalVaultFilesystem(),
            policy_repository=repository,
        )
    app.state.vault_service = vault_service
    app.state.policy_service = policy_service or PolicyService(
        app.state.vault_service, app.state.vault_service.repository
    )
    app.state.provider_service = provider_service or ProviderService(
        repository=SqliteProviderRepository(runtime.data_directory / "vaults.sqlite3"),
        credentials=WindowsCredentialManager(),
        client=OpenAiCompatibleProviderClient(),
        authorization_invalidator=app.state.vault_service.repository,
    )
    app.state.directory_picker = directory_picker or WindowsDirectoryPicker()
    app.state.directory_selections = directory_selections or DirectorySelectionStore()
    app.state.import_picker = import_picker or WindowsImportPicker()
    app.state.import_selections = import_selections or ImportSelectionStore()
    if import_task_service is None:
        task_repository = SqliteImportTaskRepository(runtime.data_directory / "tasks.sqlite3")
        import_task_service = ImportTaskService(
            app.state.vault_service,
            task_repository,
            LocalImportTaskRunner(),
            app.state.policy_service,
            SqliteSourceRepository(runtime.data_directory / "tasks.sqlite3"),
        )
        task_repository.recover_interrupted_tasks()
    app.state.import_task_service = import_task_service

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exception: StarletteHTTPException
    ):
        if isinstance(exception.detail, dict) and {
            "code",
            "message",
            "details",
            "retryable",
        }.issubset(exception.detail):
            return error_response(
                status_code=exception.status_code,
                code=exception.detail["code"],
                message=exception.detail["message"],
                details=exception.detail["details"],
                retryable=exception.detail["retryable"],
            )
        if exception.status_code == 404:
            return error_response(
                status_code=404,
                code="not_found",
                message="Resource not found.",
                details={"path": request.url.path},
                retryable=False,
            )
        return error_response(
            status_code=exception.status_code,
            code="http_error",
            message="The request could not be completed.",
            details={"path": request.url.path},
            retryable=False,
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        request: Request, exception: RequestValidationError
    ):
        return error_response(
            status_code=422,
            code="request_validation_failed",
            message="Request validation failed.",
            details={"errors": redacted_validation_errors(exception.errors()), "path": request.url.path},
            retryable=False,
        )

    @app.get("/api/health")
    def health() -> dict[str, str | int]:
        return {
            "status": "ok",
            "service": SERVICE_NAME,
            "host": DEFAULT_HOST,
            "port": DEFAULT_PORT,
            "sqlite_version": runtime.sqlite_version,
        }

    @app.get("/api/session")
    def local_session(request: Request) -> dict[str, str]:
        return local_session_status(
            app.state.local_session,
            request.cookies.get(LOCAL_SESSION_COOKIE_NAME),
        )

    @app.get("/api/providers/defaults")
    def get_model_defaults(request: Request) -> dict[str, object]:
        require_local_session(app, request)
        try:
            return model_defaults_payload(app.state.provider_service)
        except Exception as error:
            raise provider_error(error) from error

    @app.put("/api/providers/defaults/{model_type}")
    def set_model_default(
        request: Request, model_type: str, command: ModelDefaultCommand
    ) -> dict[str, object]:
        require_local_session(app, request)
        try:
            selection: ModelSelection = app.state.provider_service.set_default(
                model_type, command.provider_id, command.model_id
            )
            return {
                "default": {
                    "provider_id": selection.provider_id,
                    "model_id": selection.model_id,
                    "updated_at": selection.updated_at,
                }
            }
        except Exception as error:
            raise provider_error(error) from error

    @app.delete("/api/providers/defaults/{model_type}")
    def clear_model_default(request: Request, model_type: str) -> dict[str, str]:
        require_local_session(app, request)
        try:
            app.state.provider_service.clear_default(model_type)
            return {"status": "cleared"}
        except Exception as error:
            raise provider_error(error) from error

    @app.get("/api/providers/defaults/{model_type}/resolved")
    def resolve_model(request: Request, model_type: str) -> dict[str, object]:
        require_local_session(app, request)
        try:
            resolved = app.state.provider_service.resolve_model(model_type)
            return {
                "provider": provider_payload(resolved.provider),
                "model": provider_model_payload(resolved.model),
            }
        except Exception as error:
            raise provider_error(error) from error

    @app.get("/api/providers")
    def list_providers(request: Request) -> dict[str, list[dict[str, object]]]:
        require_local_session(app, request)
        try:
            return {"providers": [provider_payload(provider) for provider in app.state.provider_service.list()]}
        except Exception as error:
            raise provider_error(error) from error

    @app.post("/api/providers")
    def create_provider(request: Request, command: ProviderCommand) -> dict[str, object]:
        require_local_session(app, request)
        try:
            provider = app.state.provider_service.create(
                command.name, command.endpoint, command.secret
            )
            return {"provider": provider_payload(provider)}
        except Exception as error:
            raise provider_error(error) from error

    @app.get("/api/providers/{provider_id}")
    def get_provider(request: Request, provider_id: str) -> dict[str, object]:
        require_local_session(app, request)
        try:
            return {"provider": provider_payload(app.state.provider_service.get(provider_id))}
        except Exception as error:
            raise provider_error(error) from error

    @app.put("/api/providers/{provider_id}")
    def update_provider(
        request: Request, provider_id: str, command: ProviderUpdateCommand
    ) -> dict[str, object]:
        require_local_session(app, request)
        try:
            provider = app.state.provider_service.update(
                provider_id, command.name, command.endpoint, command.secret
            )
            return {"provider": provider_payload(provider)}
        except Exception as error:
            raise provider_error(error) from error

    @app.post("/api/providers/{provider_id}/test")
    def test_provider(request: Request, provider_id: str) -> dict[str, object]:
        require_local_session(app, request)
        try:
            return {"provider": provider_payload(app.state.provider_service.test(provider_id))}
        except Exception as error:
            raise provider_error(error) from error

    @app.put("/api/providers/{provider_id}/models")
    def configure_provider_model(
        request: Request, provider_id: str, command: ModelConfigurationCommand
    ) -> dict[str, object]:
        require_local_session(app, request)
        try:
            return {"provider": provider_payload(app.state.provider_service.configure_model(
                provider_id, command.model_id, command.model_type
            ))}
        except Exception as error:
            raise provider_error(error) from error

    @app.post("/api/providers/{provider_id}/models/test")
    def test_provider_model(
        request: Request, provider_id: str, command: ModelTestCommand
    ) -> dict[str, object]:
        require_local_session(app, request)
        try:
            return {"provider": provider_payload(app.state.provider_service.test_model(
                provider_id, command.model_id
            ))}
        except Exception as error:
            raise provider_error(error) from error

    @app.delete("/api/providers/{provider_id}")
    def delete_provider(request: Request, provider_id: str) -> dict[str, str]:
        require_local_session(app, request)
        try:
            app.state.provider_service.delete(provider_id)
        except Exception as error:
            raise provider_error(error) from error
        return {"status": "removed"}

    @app.post("/api/import-selections/files")
    async def select_import_files(
        request: Request, command: ImportFilesSelectionCommand
    ) -> dict[str, str | None]:
        session_secret = require_local_session(app, request)
        try:
            paths = await asyncio.to_thread(
                app.state.import_picker.select_files, multiple=command.multiple
            )
        except RuntimeError as error:
            raise HTTPException(
                status_code=501,
                detail={
                    "code": "import_picker_unavailable",
                    "message": str(error),
                    "details": {},
                    "retryable": False,
                },
            ) from error
        if paths is None:
            return {"selection_id": None, "label": None}
        selection_id = app.state.import_selections.remember(session_secret, "files", paths)
        label = paths[0].name if len(paths) == 1 else f"{paths[0].name} and {len(paths) - 1} more"
        return {"selection_id": selection_id, "label": label}

    @app.post("/api/import-selections/directory")
    async def select_import_directory(request: Request) -> dict[str, str | None]:
        session_secret = require_local_session(app, request)
        try:
            selected_path = await asyncio.to_thread(app.state.import_picker.select_directory)
        except RuntimeError as error:
            raise HTTPException(
                status_code=501,
                detail={
                    "code": "import_picker_unavailable",
                    "message": str(error),
                    "details": {},
                    "retryable": False,
                },
            ) from error
        if selected_path is None:
            return {"selection_id": None, "label": None}
        selection_id = app.state.import_selections.remember(
            session_secret, "directory", (selected_path,)
        )
        return {"selection_id": selection_id, "label": selected_path.name}

    @app.post("/api/import-tasks")
    def create_import_task(request: Request, command: ImportTaskCommand) -> dict[str, object]:
        session_secret = require_local_session(app, request)
        try:
            selection = app.state.import_selections.consume(command.selection_id, session_secret)
            task = app.state.import_task_service.create(command.vault_id, selection)
            return {"task": import_task_payload(task)}
        except Exception as error:
            raise import_task_error(error) from error

    @app.get("/api/import-tasks")
    def list_import_tasks(request: Request) -> dict[str, list[dict[str, object]]]:
        require_local_session(app, request)
        try:
            return {"tasks": [import_task_payload(task) for task in app.state.import_task_service.list()]}
        except Exception as error:
            raise import_task_error(error) from error

    @app.get("/api/import-tasks/{task_id}")
    def get_import_task(request: Request, task_id: str) -> dict[str, object]:
        require_local_session(app, request)
        try:
            task, items, event_cursor = app.state.import_task_service.detail_snapshot(task_id)
            return {
                "task": import_task_payload(task),
                "items": [import_task_item_payload(item) for item in items],
                "event_cursor": event_cursor,
            }
        except Exception as error:
            raise import_task_error(error) from error

    @app.post("/api/import-tasks/{task_id}/cancel")
    def cancel_import_task(request: Request, task_id: str) -> dict[str, object]:
        require_local_session(app, request)
        try:
            return {"task": import_task_payload(app.state.import_task_service.cancel(task_id))}
        except Exception as error:
            raise import_task_error(error) from error

    @app.post("/api/import-tasks/{task_id}/resume")
    def resume_import_task(request: Request, task_id: str) -> dict[str, object]:
        require_local_session(app, request)
        try:
            return {"task": import_task_payload(app.state.import_task_service.resume(task_id))}
        except Exception as error:
            raise import_task_error(error) from error

    @app.get("/api/import-tasks/{task_id}/events")
    async def import_task_events(request: Request, task_id: str):
        require_local_session(app, request)
        try:
            app.state.import_task_service.get(task_id)
        except Exception as error:
            raise import_task_error(error) from error
        event_cursor = request.headers.get("last-event-id")
        if event_cursor is None:
            event_cursor = request.query_params.get("after", "0")
        try:
            last_event_id = int(event_cursor)
        except ValueError:
            last_event_id = 0

        async def stream_events():
            event_id = max(last_event_id, 0)
            while not await request.is_disconnected():
                events = app.state.import_task_service.events_after(task_id, event_id)
                for event in events:
                    event_id = event.event_id
                    data = json.dumps(import_task_event_payload(event))
                    yield f"id: {event.event_id}\nevent: task-update\ndata: {data}\n\n"
                if not events:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(
            stream_events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/vaults")
    def list_vaults(request: Request) -> dict[str, list[dict[str, object]]]:
        require_local_session(app, request)
        try:
            return {
                "vaults": [
                    vault_with_policy_payload(app, vault)
                    for vault in app.state.vault_service.list()
                ]
            }
        except Exception as error:
            raise vault_error(error) from error

    @app.post("/api/vaults")
    def authorize_vault(request: Request, command: VaultPathCommand) -> dict[str, object]:
        session_secret = require_local_session(app, request)
        try:
            selected_path = app.state.directory_selections.resolve(
                command.selection_id, session_secret
            )
            vault = app.state.vault_service.authorize(selected_path, command.managed_root)
            app.state.directory_selections.discard(command.selection_id)
            return {
                "vault": vault_with_policy_payload(
                    app,
                    vault,
                )
            }
        except Exception as error:
            raise vault_error(error) from error

    @app.post("/api/vaults/select-directory")
    def select_vault_directory(request: Request) -> dict[str, str | None]:
        session_secret = require_local_session(app, request)
        try:
            selected_path = app.state.directory_picker.select_directory()
        except RuntimeError as error:
            raise HTTPException(
                status_code=501,
                detail={
                    "code": "directory_picker_unavailable",
                    "message": str(error),
                    "details": {},
                    "retryable": False,
                },
            ) from error
        if selected_path is None:
            return {"selection_id": None, "label": None}
        selection_id = app.state.directory_selections.remember(session_secret, selected_path)
        return {"selection_id": selection_id, "label": selected_path.name}

    @app.get("/api/vaults/{vault_id}")
    def get_vault(request: Request, vault_id: str) -> dict[str, object]:
        require_local_session(app, request)
        try:
            return {
                "vault": vault_with_policy_payload(
                    app, app.state.vault_service.inspect(vault_id)
                )
            }
        except Exception as error:
            raise vault_error(error) from error

    @app.post("/api/vaults/{vault_id}/reauthorize")
    def reauthorize_vault(request: Request, vault_id: str) -> dict[str, object]:
        require_local_session(app, request)
        try:
            return {
                "vault": vault_with_policy_payload(
                    app, app.state.vault_service.reauthorize(vault_id)
                )
            }
        except Exception as error:
            raise vault_error(error) from error

    @app.put("/api/vaults/{vault_id}/path")
    def relink_vault(
        request: Request, vault_id: str, command: VaultPathCommand
    ) -> dict[str, object]:
        session_secret = require_local_session(app, request)
        try:
            selected_path = app.state.directory_selections.resolve(
                command.selection_id, session_secret
            )
            vault = app.state.vault_service.relink(
                vault_id, selected_path, command.managed_root
            )
            app.state.directory_selections.discard(command.selection_id)
            return {
                "vault": vault_with_policy_payload(
                    app,
                    vault,
                )
            }
        except Exception as error:
            raise vault_error(error) from error

    @app.post("/api/vaults/{vault_id}/current")
    def set_current_vault(request: Request, vault_id: str) -> dict[str, object]:
        require_local_session(app, request)
        try:
            return {
                "vault": vault_with_policy_payload(
                    app, app.state.vault_service.set_current(vault_id)
                )
            }
        except Exception as error:
            raise vault_error(error) from error

    @app.post("/api/vaults/{vault_id}/deactivate")
    def deactivate_vault(request: Request, vault_id: str) -> dict[str, object]:
        require_local_session(app, request)
        try:
            return {
                "vault": vault_with_policy_payload(
                    app, app.state.vault_service.deactivate(vault_id)
                )
            }
        except Exception as error:
            raise vault_error(error) from error

    @app.delete("/api/vaults/{vault_id}")
    def remove_vault(request: Request, vault_id: str) -> dict[str, str]:
        require_local_session(app, request)
        try:
            app.state.vault_service.remove(vault_id)
        except Exception as error:
            raise vault_error(error) from error
        return {"status": "removed"}

    @app.get("/api/vaults/{vault_id}/policy")
    def get_vault_policy(request: Request, vault_id: str) -> dict[str, object]:
        require_local_session(app, request)
        try:
            policy = app.state.policy_service.get(vault_id)
            return {
                "policy": policy_payload(
                    policy, app.state.policy_service.list_rules(vault_id)
                )
            }
        except Exception as error:
            raise vault_error(error) from error

    @app.put("/api/vaults/{vault_id}/policy/mode")
    def set_vault_policy_mode(
        request: Request, vault_id: str, command: PolicyModeCommand
    ) -> dict[str, object]:
        require_local_session(app, request)
        try:
            policy = app.state.policy_service.set_outbound_mode(
                vault_id, command.outbound_mode
            )
            return {
                "policy": policy_payload(
                    policy, app.state.policy_service.list_rules(vault_id)
                )
            }
        except Exception as error:
            raise vault_error(error) from error

    @app.post("/api/vaults/{vault_id}/policy/rules")
    def add_vault_policy_rule(
        request: Request, vault_id: str, command: ExclusionRuleCommand
    ) -> dict[str, object]:
        require_local_session(app, request)
        try:
            rule = app.state.policy_service.add_rule(
                vault_id, command.kind, command.relative_path
            )
            return {"rule": rule_payload(rule)}
        except Exception as error:
            raise vault_error(error) from error

    @app.put("/api/vaults/{vault_id}/policy/rules/{rule_id}")
    def update_vault_policy_rule(
        request: Request,
        vault_id: str,
        rule_id: str,
        command: ExclusionRuleCommand,
    ) -> dict[str, object]:
        require_local_session(app, request)
        try:
            rule = app.state.policy_service.update_rule(
                vault_id, rule_id, command.kind, command.relative_path
            )
            return {"rule": rule_payload(rule)}
        except Exception as error:
            raise vault_error(error) from error

    @app.delete("/api/vaults/{vault_id}/policy/rules/{rule_id}")
    def remove_vault_policy_rule(
        request: Request, vault_id: str, rule_id: str
    ) -> dict[str, str]:
        require_local_session(app, request)
        try:
            app.state.policy_service.remove_rule(vault_id, rule_id)
        except Exception as error:
            raise vault_error(error) from error
        return {"status": "removed"}

    @app.post("/api/vaults/{vault_id}/policy/preview")
    def preview_vault_policy(
        request: Request, vault_id: str, command: PolicyPreviewCommand
    ) -> dict[str, object]:
        require_local_session(app, request)
        try:
            preview = app.state.policy_service.preview(
                vault_id,
                command.source_path,
                command.derived_path,
                command.stage,
                candidate_kind=command.candidate_kind,
                candidate_relative_path=command.candidate_relative_path,
                replacing_rule_id=command.replacing_rule_id,
            )
            return {"preview": preview_payload(preview)}
        except Exception as error:
            raise vault_error(error) from error

    @app.post("/api/vaults/{vault_id}/outbound-authorizations")
    def request_outbound_authorization(
        request: Request, vault_id: str, command: OutboundAuthorizationCommand
    ) -> dict[str, object]:
        require_local_session(app, request)
        try:
            authorization = app.state.policy_service.request_outbound_authorization(
                vault_id,
                provider_id=command.provider_id,
                model_id=command.model_id,
                operation=command.operation,
                task_id=command.task_id,
                scopes=[
                    OutboundScope(
                        source_path=scope.source_path,
                        derived_path=scope.derived_path,
                    )
                    for scope in command.scopes
                ],
            )
            return {"authorization": authorization_payload(authorization)}
        except Exception as error:
            raise vault_error(error) from error

    @app.post(
        "/api/vaults/{vault_id}/outbound-authorizations/{authorization_id}/confirm"
    )
    def confirm_outbound_authorization(
        request: Request,
        vault_id: str,
        authorization_id: str,
        command: AuthorizationConfirmationCommand,
    ) -> dict[str, object]:
        require_local_session(app, request)
        try:
            authorization = app.state.policy_service.confirm_outbound_authorization(
                vault_id, authorization_id, approved=command.approved
            )
            return {"authorization": authorization_payload(authorization)}
        except Exception as error:
            raise vault_error(error) from error

    @app.post(
        "/api/vaults/{vault_id}/outbound-authorizations/{authorization_id}/check"
    )
    def check_outbound_authorization(
        request: Request,
        vault_id: str,
        authorization_id: str,
        command: OutboundAuthorizationCheckCommand,
    ) -> dict[str, object]:
        require_local_session(app, request)
        try:
            authorization = app.state.policy_service.check_outbound_authorization(
                vault_id,
                authorization_id,
                provider_id=command.provider_id,
                model_id=command.model_id,
                operation=command.operation,
                task_id=command.task_id,
                scopes=[
                    OutboundScope(
                        source_path=scope.source_path,
                        derived_path=scope.derived_path,
                    )
                    for scope in command.scopes
                ],
            )
            return {"authorization": authorization_payload(authorization)}
        except Exception as error:
            raise vault_error(error) from error

    @app.get("/", include_in_schema=False)
    def workbench() -> FileResponse:
        return workbench_response(app.state.local_session)

    app.mount("/", StaticFiles(directory=web_build_directory, html=True), name="web")
    return app


def launch_browser_when_started(
    server: uvicorn.Server,
    browser_opener=webbrowser.open,
    poll_interval: float = 0.01,
) -> None:
    while not server.started and not server.should_exit:
        time.sleep(poll_interval)
    if server.started:
        browser_opener(DEFAULT_BROWSER_URL)


def run(*, open_browser: bool = True) -> int:
    try:
        listener = reserve_loopback_listener()
    except PortInUseError as error:
        if is_verified_running_instance():
            print("Verified application instance is already running.")
            return 0
        print(error)
        return 1

    try:
        config = uvicorn.Config(create_app(), host=DEFAULT_HOST, port=DEFAULT_PORT, log_level="info")
        server = uvicorn.Server(config)
        if open_browser:
            threading.Thread(
                target=launch_browser_when_started,
                args=(server,),
                daemon=True,
            ).start()
        server.run(sockets=[listener])
    finally:
        listener.close()
    return 0


if __name__ == "__main__":
    sys.exit(run(open_browser="--no-browser" not in sys.argv[1:]))

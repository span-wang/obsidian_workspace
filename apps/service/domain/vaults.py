from dataclasses import dataclass
from pathlib import Path


class CrossVaultLinkError(ValueError):
    """Raised when a relationship would cross a vault isolation boundary."""


def ensure_same_vault(source_vault_id: str, target_vault_id: str) -> None:
    if source_vault_id != target_vault_id:
        raise CrossVaultLinkError("Cross-vault Markdown links are not allowed.")


@dataclass(frozen=True)
class Vault:
    vault_id: str
    path: Path
    managed_root_relative_path: str
    authorization_status: str
    access_status: str
    index_status: str
    created_at: str
    updated_at: str
    is_current: bool
    access_reason: str | None = None

    @property
    def display_name(self) -> str:
        return self.path.name or self.path.drive or "vault"

    @property
    def managed_root(self) -> Path:
        return self.path / self.managed_root_relative_path

    @property
    def source_directory(self) -> Path:
        return self.managed_root / "sources"

    @property
    def note_directory(self) -> Path:
        return self.managed_root / "notes"

    @property
    def recovery_actions(self) -> tuple[str, ...]:
        if self.access_status != "available":
            return ("reauthorize", "relink", "read-only")
        if self.authorization_status == "inactive":
            return ("reauthorize", "read-only")
        return ()

from dataclasses import dataclass
from hmac import compare_digest
from secrets import token_urlsafe


@dataclass(frozen=True)
class LocalSession:
    secret: str

    def is_valid(self, candidate: str | None) -> bool:
        return candidate is not None and compare_digest(self.secret, candidate)


def create_local_session() -> LocalSession:
    return LocalSession(secret=token_urlsafe(32))

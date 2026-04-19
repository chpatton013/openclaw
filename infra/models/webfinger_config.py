from dataclasses import dataclass
from typing import Any, Self


@dataclass(frozen=True)
class WebFingerConfig:
    subject: str
    oidc_issuer_url: str

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            subject=data["subject"],
            oidc_issuer_url=data["oidc_issuer_url"],
        )

from dataclasses import dataclass
from typing import Any, Self


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    from_email_address: str

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            host=data["host"],
            port=data.get("port", 587),
            from_email_address=data["from_email_address"],
        )

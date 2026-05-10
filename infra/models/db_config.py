from dataclasses import dataclass
from typing import Any, Self


@dataclass(frozen=True)
class DbConfig:
    name: str
    secret_name: str
    collation: str | None = None
    ctype: str | None = None
    template: str | None = None

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            name=data["name"],
            secret_name=data["secret_name"],
            collation=data.get("collation"),
            ctype=data.get("ctype"),
            template=data.get("template"),
        )

from dataclasses import dataclass
from typing import Any, Self


@dataclass(frozen=True)
class DbConfig:
    name: str
    secret_name: str

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            name=data["name"],
            secret_name=data["secret_name"],
        )

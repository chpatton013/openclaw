from dataclasses import dataclass
from typing import Any, Self


@dataclass(frozen=True)
class FoundationConfig:
    root_domain: str

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            root_domain=data["root_domain"],
        )

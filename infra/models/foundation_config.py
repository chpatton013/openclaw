from dataclasses import dataclass
from typing import Any, Self


@dataclass(frozen=True)
class FoundationConfig:
    root_domain: str

    @staticmethod
    def load(data: dict[str, Any]) -> Self:
        return FoundationConfig(
            root_domain=data["root_domain"],
        )

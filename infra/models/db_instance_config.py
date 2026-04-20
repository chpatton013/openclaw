from dataclasses import dataclass
from typing import Any, Self


@dataclass(frozen=True)
class DbInstanceConfig:
    instance_type: str
    allocated_storage_gib: int
    port: int

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            instance_type=data["instance_type"],
            allocated_storage_gib=data["allocated_storage_gib"],
            port=data.get("port", 5432),
        )

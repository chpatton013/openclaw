from dataclasses import dataclass
from typing import Any, Self


@dataclass(frozen=True)
class DataConfig:
    instance_type: str
    allocated_storage_gib: int

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            instance_type=data["instance_type"],
            allocated_storage_gib=data["allocated_storage_gib"],
        )

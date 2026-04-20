from dataclasses import dataclass
from typing import Any, Self


@dataclass(frozen=True)
class FargateTaskConfig:
    cpu: int
    memory_limit_mib: int
    desired_count: int
    min_healthy_percent: int

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            cpu=data["cpu"],
            memory_limit_mib=data["memory_limit_mib"],
            desired_count=data["desired_count"],
            min_healthy_percent=data["min_healthy_percent"],
        )

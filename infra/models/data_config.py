from dataclasses import dataclass
from typing import Any, Self

from .db_instance_config import DbInstanceConfig


@dataclass(frozen=True)
class DataConfig:
    instance: DbInstanceConfig
    master_secret_name: str

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            instance=DbInstanceConfig.load(data["instance"]),
            master_secret_name=data["master_secret_name"],
        )

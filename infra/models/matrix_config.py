from dataclasses import dataclass
from typing import Any, Self

from .db_config import DbConfig
from .fargate_task_config import FargateTaskConfig


@dataclass(frozen=True)
class MatrixConfig:
    subdomain: str
    image_version: str
    db: DbConfig
    task: FargateTaskConfig
    remote_media_lifetime: str

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            subdomain=data["subdomain"],
            image_version=data["image_version"],
            db=DbConfig.load(data["db"]),
            task=FargateTaskConfig.load(data["task"]),
            remote_media_lifetime=data.get("remote_media_lifetime", "90d"),
        )

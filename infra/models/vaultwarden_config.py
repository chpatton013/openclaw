from dataclasses import dataclass
from typing import Any, Self

from .db_config import DbConfig
from .fargate_task_config import FargateTaskConfig
from .smtp_config import SmtpConfig


@dataclass(frozen=True)
class VaultwardenConfig:
    subdomain: str
    image_version: str
    db: DbConfig
    task: FargateTaskConfig
    smtp: SmtpConfig

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            subdomain=data["subdomain"],
            image_version=data["image_version"],
            db=DbConfig.load(data["db"]),
            task=FargateTaskConfig.load(data["task"]),
            smtp=SmtpConfig.load(data["smtp"]),
        )

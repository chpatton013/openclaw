from dataclasses import dataclass
from typing import Any, Self

from .db_config import DbConfig
from .fargate_task_config import FargateTaskConfig
from .smtp_config import SmtpConfig


@dataclass(frozen=True)
class AuthentikConfig:
    subdomain: str
    image_version: str
    db: DbConfig
    server: FargateTaskConfig
    worker: FargateTaskConfig
    smtp: SmtpConfig

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            subdomain=data["subdomain"],
            image_version=data["image_version"],
            db=DbConfig.load(data["db"]),
            server=FargateTaskConfig.load(data["server"]),
            worker=FargateTaskConfig.load(data["worker"]),
            smtp=SmtpConfig.load(data["smtp"]),
        )

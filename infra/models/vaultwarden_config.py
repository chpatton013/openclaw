from dataclasses import dataclass
from typing import Any, Self

from .db_config import DbConfig
from .fargate_task_config import FargateTaskConfig


@dataclass(frozen=True)
class VaultwardenSmtpConfig:
    host: str
    port: int
    from_email_address: str

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            host=data["host"],
            port=data.get("port", 587),
            from_email_address=data["from_email_address"],
        )


@dataclass(frozen=True)
class VaultwardenConfig:
    subdomain: str
    image_version: str
    db: DbConfig
    task: FargateTaskConfig
    smtp: VaultwardenSmtpConfig

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            subdomain=data["subdomain"],
            image_version=data["image_version"],
            db=DbConfig.load(data["db"]),
            task=FargateTaskConfig.load(data["task"]),
            smtp=VaultwardenSmtpConfig.load(data["smtp"]),
        )

from dataclasses import dataclass
from typing import Any, Self

from .db_config import DbConfig
from .fargate_task_config import FargateTaskConfig


@dataclass(frozen=True)
class AuthentikSmtpConfig:
    host: str
    port: int
    use_ssl: bool
    use_tls: bool
    from_email_address: str

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            host=data["host"],
            port=data.get("port", 587),
            use_ssl=data.get("use_ssl", False),
            use_tls=data.get("use_tls", True),
            from_email_address=data["from_email_address"],
        )


@dataclass(frozen=True)
class AuthentikConfig:
    subdomain: str
    image_version: str
    db: DbConfig
    server: FargateTaskConfig
    worker: FargateTaskConfig
    smtp: AuthentikSmtpConfig

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            subdomain=data["subdomain"],
            image_version=data["image_version"],
            db=DbConfig.load(data["db"]),
            server=FargateTaskConfig.load(data["server"]),
            worker=FargateTaskConfig.load(data["worker"]),
            smtp=AuthentikSmtpConfig.load(data["smtp"]),
        )

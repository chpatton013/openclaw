from dataclasses import dataclass
from typing import Any, Self


@dataclass(frozen=True)
class AuthentikDbConfig:
    name: str
    instance_type: str
    allocated_storage_gib: int

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            name=data["name"],
            instance_type=data["instance_type"],
            allocated_storage_gib=data["allocated_storage_gib"],
        )


@dataclass(frozen=True)
class AuthentikTaskConfig:
    cpu: int
    memory_limit_mib: int
    desired_count: int

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            cpu=data["cpu"],
            memory_limit_mib=data["memory_limit_mib"],
            desired_count=data["desired_count"],
        )


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
            port=data["port"],
            use_ssl=data["use_ssl"],
            use_tls=data["use_tls"],
            from_email_address=data["from_email_address"],
        )


@dataclass(frozen=True)
class AuthentikConfig:
    subdomain: str
    image_version: str
    db: AuthentikDbConfig
    server: AuthentikTaskConfig
    worker: AuthentikTaskConfig
    smtp: AuthentikSmtpConfig

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            subdomain=data["subdomain"],
            image_version=data["image_version"],
            db=AuthentikDbConfig.load(data["db"]),
            server=AuthentikTaskConfig.load(data["server"]),
            worker=AuthentikTaskConfig.load(data["worker"]),
            smtp=AuthentikSmtpConfig.load(data["smtp"]),
        )

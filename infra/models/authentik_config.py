from dataclasses import dataclass
from typing import Any, Self


@dataclass(frozen=True)
class AuthentikDbConfig:
    name: str
    username: str
    instance_type: str
    allocated_storage_gib: int

    @staticmethod
    def load(data: dict[str, Any]) -> Self:
        return AuthentikDbConfig(
            name=data["name"],
            username=data["username"],
            instance_type=data["instance_type"],
            allocated_storage_gib=data["allocated_storage_gib"],
        )


@dataclass(frozen=True)
class AuthentikTaskConfig:
    cpu: int
    memory_limit_mib: int
    desired_count: int

    @staticmethod
    def load(data: dict[str, Any]) -> Self:
        return AuthentikTaskConfig(
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
    username: str
    from_email_address: str

    @staticmethod
    def load(data: dict[str, Any]) -> Self:
        return AuthentikSmtpConfig(
            host=data["host"],
            port=data["port"],
            use_ssl=data["use_ssl"],
            use_tls=data["use_tls"],
            username=data["username"],
            from_email_address=data["from_email_address"],
        )


@dataclass(frozen=True)
class AuthentikConfig:
    subdomain: str
    image_version: str
    bootstrap_email: str
    db: AuthentikDbConfig
    server: AuthentikTaskConfig
    worker: AuthentikTaskConfig
    smtp: AuthentikSmtpConfig

    @staticmethod
    def load(data: dict[str, Any]) -> Self:
        return AuthentikConfig(
            subdomain=data["subdomain"],
            image_version=data["image_version"],
            bootstrap_email=data["bootstrap_email"],
            db=AuthentikDbConfig.load(data["db"]),
            server=AuthentikTaskConfig.load(data["server"]),
            worker=AuthentikTaskConfig.load(data["worker"]),
            smtp=AuthentikSmtpConfig.load(data["smtp"]),
        )

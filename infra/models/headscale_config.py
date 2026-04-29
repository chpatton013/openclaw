from dataclasses import dataclass
from typing import Any, Self

from .db_config import DbConfig
from .fargate_task_config import FargateTaskConfig


@dataclass(frozen=True)
class ExitNodeConfig:
    instance_type: str
    tailscale_image_version: str
    hostname: str
    preauthkey_user: str

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            instance_type=data["instance_type"],
            tailscale_image_version=data["tailscale_image_version"],
            hostname=data["hostname"],
            preauthkey_user=data["preauthkey_user"],
        )


@dataclass(frozen=True)
class HeadscaleConfig:
    headscale_subdomain: str
    headplane_subdomain: str
    dns_subdomain: str
    headscale_image_version: str
    headplane_image_version: str
    headscale: FargateTaskConfig
    headplane: FargateTaskConfig
    db: DbConfig
    oidc_issuer_application: str
    dns_nameservers: list[str]
    log_level: str
    exit_node: ExitNodeConfig

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            headscale_subdomain=data["headscale_subdomain"],
            headplane_subdomain=data["headplane_subdomain"],
            dns_subdomain=data["dns_subdomain"],
            headscale_image_version=data["headscale_image_version"],
            headplane_image_version=data["headplane_image_version"],
            headscale=FargateTaskConfig.load(data["headscale"]),
            headplane=FargateTaskConfig.load(data["headplane"]),
            db=DbConfig.load(data["db"]),
            oidc_issuer_application=data["oidc_issuer_application"],
            dns_nameservers=list(data["dns_nameservers"]),
            log_level=data["log_level"],
            exit_node=ExitNodeConfig.load(data["exit_node"]),
        )

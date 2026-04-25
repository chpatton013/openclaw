from dataclasses import dataclass
from typing import Any, Self

from .db_config import DbConfig
from .fargate_task_config import FargateTaskConfig


@dataclass(frozen=True)
class HeadscaleConfig:
    headscale_subdomain: str
    dns_subdomain: str
    headscale_image_version: str
    headplane_image_version: str
    headscale: FargateTaskConfig
    headplane: FargateTaskConfig
    db: DbConfig
    oidc_issuer_application: str
    dns_nameservers: list[str]
    log_level: str

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            headscale_subdomain=data["headscale_subdomain"],
            dns_subdomain=data["dns_subdomain"],
            headscale_image_version=data["headscale_image_version"],
            headplane_image_version=data["headplane_image_version"],
            headscale=FargateTaskConfig.load(data["headscale"]),
            headplane=FargateTaskConfig.load(data["headplane"]),
            db=DbConfig.load(data["db"]),
            oidc_issuer_application=data["oidc_issuer_application"],
            dns_nameservers=list(data["dns_nameservers"]),
            log_level=data["log_level"],
        )

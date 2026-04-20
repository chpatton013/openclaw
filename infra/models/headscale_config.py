from dataclasses import dataclass
from typing import Any, Self


@dataclass(frozen=True)
class HeadscaleTaskConfig:
    cpu: int
    memory_limit_mib: int
    desired_count: int
    min_healthy_percent: float

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            cpu=data["cpu"],
            memory_limit_mib=data["memory_limit_mib"],
            desired_count=data["desired_count"],
            min_healthy_percent=data["min_healthy_percent"],
        )


@dataclass(frozen=True)
class HeadscaleDbConfig:
    name: str

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(name=data["name"])


@dataclass(frozen=True)
class HeadscaleConfig:
    control_plane_subdomain: str
    admin_subdomain: str
    private_subdomain: str
    headscale_image_version: str
    headplane_image_version: str
    headscale: HeadscaleTaskConfig
    headplane: HeadscaleTaskConfig
    db: HeadscaleDbConfig
    oidc_issuer_url: str

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            control_plane_subdomain=data["control_plane_subdomain"],
            admin_subdomain=data["admin_subdomain"],
            private_subdomain=data["private_subdomain"],
            headscale_image_version=data["headscale_image_version"],
            headplane_image_version=data["headplane_image_version"],
            headscale=HeadscaleTaskConfig.load(data["headscale"]),
            headplane=HeadscaleTaskConfig.load(data["headplane"]),
            db=HeadscaleDbConfig.load(data["db"]),
            oidc_issuer_url=data["oidc_issuer_url"],
        )

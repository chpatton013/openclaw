import pathlib
import tomllib
from dataclasses import dataclass
from typing import Any, Self

from .authentik_config import AuthentikConfig


@dataclass(frozen=True)
class AppConfig:
    root_domain: str
    tailscale_admin_email: str
    authentik: AuthentikConfig

    @staticmethod
    def load(data: dict[str, Any]) -> Self:
        return AppConfig(
            root_domain=data["root_domain"],
            tailscale_admin_email=data["tailscale_admin_email"],
            authentik=AuthentikConfig.load(data["authentik"]),
        )


def load_config(path: pathlib.Path) -> AppConfig:
    with path.open("rb") as f:
        data = tomllib.load(f)
    return AppConfig.load(data)

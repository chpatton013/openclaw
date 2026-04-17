import pathlib
import tomllib
from dataclasses import dataclass
from typing import Any, Self

from .authentik_config import AuthentikConfig
from .foundation_config import FoundationConfig


@dataclass(frozen=True)
class AppConfig:
    tailscale_admin_email: str
    foundation: FoundationConfig
    authentik: AuthentikConfig

    @staticmethod
    def load(data: dict[str, Any]) -> Self:
        return AppConfig(
            tailscale_admin_email=data["tailscale_admin_email"],
            foundation=FoundationConfig.load(data["foundation"]),
            authentik=AuthentikConfig.load(data["authentik"]),
        )


def load_config(path: pathlib.Path) -> AppConfig:
    with path.open("rb") as f:
        data = tomllib.load(f)
    return AppConfig.load(data)

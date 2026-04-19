import pathlib
import tomllib
from dataclasses import dataclass
from typing import Any, Self

from .authentik_config import AuthentikConfig
from .foundation_config import FoundationConfig
from .webfinger_config import WebFingerConfig


@dataclass(frozen=True)
class AppConfig:
    foundation: FoundationConfig
    authentik: AuthentikConfig
    webfinger: WebFingerConfig

    @classmethod
    def load(cls, data: dict[str, Any]) -> Self:
        return cls(
            foundation=FoundationConfig.load(data["foundation"]),
            authentik=AuthentikConfig.load(data["authentik"]),
            webfinger=WebFingerConfig.load(data["webfinger"]),
        )


def load_config(path: pathlib.Path) -> AppConfig:
    with path.open("rb") as f:
        data = tomllib.load(f)
    return AppConfig.load(data)

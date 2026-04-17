import pathlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    file: pathlib.Path | None = None
    runtime_s: float = 0.0
    messages: tuple[str, ...] = ()
    fixed: bool = False


@dataclass(frozen=True)
class BaseConfig:
    include_files: tuple[str, ...] = ()
    exclude_files: tuple[str, ...] = ()


class Validator(ABC):
    name: ClassVar[str]
    fixer: ClassVar[bool] = False
    priority: ClassVar[int] = 0
    Config: ClassVar[type] = BaseConfig

    def __init__(self, config: Any, repo_root: pathlib.Path) -> None:
        self.config = config
        self.repo_root = repo_root

    @abstractmethod
    def check(self, file: pathlib.Path) -> ValidationResult: ...

    def fix(self, file: pathlib.Path) -> ValidationResult:
        raise NotImplementedError

    @classmethod
    def config_from_options(cls, opts: dict) -> Any:
        return cls.Config(
            include_files=tuple(opts.get("include_files", ())),
            exclude_files=tuple(opts.get("exclude_files", ())),
        )

import difflib
import pathlib

from pyupgrade._data import Settings
from pyupgrade._main import _fix_plugins, _fix_tokens

from validator.base import ValidationResult, Validator

_SETTINGS = Settings(min_version=(3, 10))


def _upgrade(src: str) -> str:
    return _fix_tokens(_fix_plugins(src, settings=_SETTINGS))


class PyupgradeValidator(Validator):
    name = "pyupgrade"
    fixer = True
    priority = 15

    def check(self, file: pathlib.Path) -> ValidationResult:
        src = file.read_text()
        try:
            upgraded = _upgrade(src)
        except SyntaxError as e:
            return ValidationResult(ok=False, messages=(f"syntax error: {e}",))
        if upgraded == src:
            return ValidationResult(ok=True)
        diff = "".join(
            difflib.unified_diff(
                src.splitlines(keepends=True),
                upgraded.splitlines(keepends=True),
                fromfile=str(file),
                tofile=str(file),
            )
        )
        return ValidationResult(ok=False, messages=(diff,))

    def fix(self, file: pathlib.Path) -> ValidationResult:
        src = file.read_text()
        try:
            upgraded = _upgrade(src)
        except SyntaxError as e:
            return ValidationResult(ok=False, messages=(f"syntax error: {e}",))
        if upgraded == src:
            return ValidationResult(ok=True, fixed=False)
        file.write_text(upgraded)
        return ValidationResult(ok=True, fixed=True)

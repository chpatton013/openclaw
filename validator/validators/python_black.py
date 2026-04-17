import difflib
import pathlib

import black
from black.parsing import InvalidInput
from black.report import NothingChanged

from validator.base import ValidationResult, Validator

_MODE = black.Mode()


class PythonBlackValidator(Validator):
    name = "python-black"
    fixer = True
    priority = 20

    def check(self, file: pathlib.Path) -> ValidationResult:
        src = file.read_text()
        try:
            formatted = black.format_file_contents(src, fast=False, mode=_MODE)
        except NothingChanged:
            return ValidationResult(ok=True)
        except InvalidInput as e:
            return ValidationResult(ok=False, messages=(f"invalid input: {e}",))
        diff = "".join(
            difflib.unified_diff(
                src.splitlines(keepends=True),
                formatted.splitlines(keepends=True),
                fromfile=str(file),
                tofile=str(file),
            )
        )
        return ValidationResult(ok=False, messages=(diff,))

    def fix(self, file: pathlib.Path) -> ValidationResult:
        src = file.read_text()
        try:
            formatted = black.format_file_contents(src, fast=False, mode=_MODE)
        except NothingChanged:
            return ValidationResult(ok=True, fixed=False)
        except InvalidInput as e:
            return ValidationResult(ok=False, messages=(f"invalid input: {e}",))
        file.write_text(formatted)
        return ValidationResult(ok=True, fixed=True)

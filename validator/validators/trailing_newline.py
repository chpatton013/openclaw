import pathlib

from validator.base import ValidationResult, Validator


class TrailingNewlineValidator(Validator):
    name = "trailing-newline"
    fixer = True
    priority = 10

    def check(self, file: pathlib.Path) -> ValidationResult:
        data = file.read_bytes()
        if not data or data.endswith(b"\n"):
            return ValidationResult(ok=True)
        return ValidationResult(ok=False, messages=("missing trailing newline",))

    def fix(self, file: pathlib.Path) -> ValidationResult:
        data = file.read_bytes()
        if not data or data.endswith(b"\n"):
            return ValidationResult(ok=True, fixed=False)
        file.write_bytes(data + b"\n")
        return ValidationResult(ok=True, fixed=True)

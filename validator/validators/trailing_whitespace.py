import pathlib
import re

from validator.base import ValidationResult, Validator


class TrailingWhitespaceValidator(Validator):
    name = "trailing-whitespace"
    fixer = True
    priority = 10

    def check(self, file: pathlib.Path) -> ValidationResult:
        data = file.read_bytes()
        messages = [
            f"line {lineno}: trailing whitespace"
            for lineno, line in enumerate(data.splitlines(), 1)
            if line != line.rstrip(b" \t")
        ]
        if messages:
            return ValidationResult(ok=False, messages=tuple(messages))
        return ValidationResult(ok=True)

    def fix(self, file: pathlib.Path) -> ValidationResult:
        data = file.read_bytes()
        fixed = re.sub(rb"[ \t]+(\r?\n|\r)", rb"\1", data)
        fixed = re.sub(rb"[ \t]+\Z", b"", fixed)
        if fixed == data:
            return ValidationResult(ok=True, fixed=False)
        file.write_bytes(fixed)
        return ValidationResult(ok=True, fixed=True)

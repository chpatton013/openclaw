import pathlib

from validator.base import ValidationResult, Validator


class TabsValidator(Validator):
    name = "tabs"
    fixer = False

    def check(self, file: pathlib.Path) -> ValidationResult:
        messages = []
        for lineno, line in enumerate(file.read_bytes().splitlines(), 1):
            tab_cols = [col for col, b in enumerate(line, 1) if b == 0x09]
            if tab_cols:
                messages.append(
                    f"line {lineno}: tab at col {', '.join(str(c) for c in tab_cols)}"
                )
        if messages:
            return ValidationResult(ok=False, messages=tuple(messages))
        return ValidationResult(ok=True)

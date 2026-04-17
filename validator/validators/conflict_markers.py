import pathlib

from validator.base import ValidationResult, Validator

_MARKERS = (b"<<<<<<<", b"=======", b">>>>>>>")


class ConflictMarkersValidator(Validator):
    name = "conflict-markers"
    fixer = False

    def check(self, file: pathlib.Path) -> ValidationResult:
        data = file.read_bytes()
        messages = [
            f"line {lineno}: conflict marker '{line[:7].decode()}'"
            for lineno, line in enumerate(data.splitlines(), 1)
            if any(line.startswith(m) for m in _MARKERS)
        ]
        if messages:
            return ValidationResult(ok=False, messages=tuple(messages))
        return ValidationResult(ok=True)

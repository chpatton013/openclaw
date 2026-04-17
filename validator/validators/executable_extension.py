import pathlib
import stat

from validator.base import ValidationResult, Validator


class ExecutableExtensionValidator(Validator):
    name = "executable-extension"
    fixer = False

    def check(self, file: pathlib.Path) -> ValidationResult:
        if not file.suffix:
            return ValidationResult(ok=True)
        if file.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            return ValidationResult(
                ok=False,
                messages=(f"executable file has extension '{file.suffix}'",),
            )
        return ValidationResult(ok=True)

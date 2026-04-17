import pathlib
import re

from validator.base import ValidationResult, Validator

_ALLOWED = re.compile(r"^[a-zA-Z0-9_]+$")


class PythonFilenameValidator(Validator):
    name = "python-filename"
    fixer = False

    def check(self, file: pathlib.Path) -> ValidationResult:
        rel = file.relative_to(self.repo_root).with_suffix("")
        bad = [part for part in rel.parts if not _ALLOWED.match(part)]
        if not bad:
            return ValidationResult(ok=True)
        return ValidationResult(
            ok=False,
            messages=(f"path components must match [a-zA-Z0-9_]+: {', '.join(bad)}",),
        )

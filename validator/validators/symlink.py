import os
import pathlib

from validator.base import ValidationResult, Validator


class SymlinkValidator(Validator):
    name = "symlink"
    fixer = False

    def check(self, file: pathlib.Path) -> ValidationResult:
        if not file.is_symlink():
            return ValidationResult(ok=True)

        raw_target = os.readlink(file)
        resolved = file.resolve()

        if not resolved.exists():
            return ValidationResult(
                ok=False,
                messages=(f"broken symlink: {raw_target}",),
            )

        if not resolved.is_relative_to(self.repo_root.resolve()):
            return ValidationResult(
                ok=False,
                messages=(f"symlink points outside repository: {raw_target}",),
            )

        return ValidationResult(ok=True)

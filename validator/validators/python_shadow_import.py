import pathlib

from validator.base import ValidationResult, Validator


class PythonShadowImportValidator(Validator):
    name = "python-shadow-import"
    fixer = False

    def check(self, file: pathlib.Path) -> ValidationResult:
        sibling_dir = file.with_suffix("")
        if sibling_dir.is_dir():
            rel = sibling_dir.relative_to(self.repo_root)
            return ValidationResult(
                ok=False,
                messages=(f"'{file.name}' shadows sibling directory '{rel}/'",),
            )
        return ValidationResult(ok=True)

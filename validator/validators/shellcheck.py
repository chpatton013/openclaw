import pathlib
import subprocess

from validator.base import ValidationResult, Validator


class ShellcheckValidator(Validator):
    name = "shellcheck"
    fixer = False

    def check(self, file: pathlib.Path) -> ValidationResult:
        bin_path = self.repo_root / "bin" / "shellcheck"
        r = subprocess.run(
            [str(bin_path), str(file)],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            return ValidationResult(ok=True)
        return ValidationResult(
            ok=False,
            messages=tuple(m for m in (r.stdout, r.stderr) if m),
        )

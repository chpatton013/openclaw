import pathlib
import subprocess

from validator.base import ValidationResult, Validator


class GitleaksValidator(Validator):
    name = "gitleaks"
    fixer = False

    def check(self, file: pathlib.Path) -> ValidationResult:
        bin_path = self.repo_root / "bin" / "gitleaks"
        r = subprocess.run(
            [
                str(bin_path),
                "dir",
                "--no-banner",
                "--no-color",
                "--exit-code",
                "1",
                "-l",
                "warn",
                "-v",
                str(file),
            ],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            return ValidationResult(ok=True)
        return ValidationResult(
            ok=False,
            messages=tuple(m for m in (r.stdout, r.stderr) if m),
        )

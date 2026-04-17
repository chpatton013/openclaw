import pathlib
import subprocess

from validator.base import ValidationResult, Validator


class TerraformFmtValidator(Validator):
    name = "terraform-fmt"
    fixer = True
    priority = 20

    def check(self, file: pathlib.Path) -> ValidationResult:
        bin_path = self.repo_root / "bin" / "terraform"
        r = subprocess.run(
            [str(bin_path), "fmt", "-check", "-diff", str(file)],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            return ValidationResult(ok=True)
        return ValidationResult(
            ok=False,
            messages=tuple(m for m in (r.stdout, r.stderr) if m),
        )

    def fix(self, file: pathlib.Path) -> ValidationResult:
        bin_path = self.repo_root / "bin" / "terraform"
        before = file.read_bytes()
        r = subprocess.run(
            [str(bin_path), "fmt", str(file)],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            return ValidationResult(
                ok=False,
                messages=tuple(m for m in (r.stdout, r.stderr) if m),
            )
        return ValidationResult(ok=True, fixed=file.read_bytes() != before)

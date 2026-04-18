import pathlib
import subprocess

from validator.base import ValidationResult, Validator


class YamlfmtValidator(Validator):
    name = "yamlfmt"
    fixer = True
    priority = 20

    def check(self, file: pathlib.Path) -> ValidationResult:
        bin_path = self.repo_root / "bin" / "yamlfmt"
        r = subprocess.run(
            [str(bin_path), "-lint", str(file)],
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
        bin_path = self.repo_root / "bin" / "yamlfmt"
        before = file.read_bytes()
        r = subprocess.run(
            [str(bin_path), str(file)],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            return ValidationResult(
                ok=False,
                messages=tuple(m for m in (r.stdout, r.stderr) if m),
            )
        return ValidationResult(ok=True, fixed=file.read_bytes() != before)

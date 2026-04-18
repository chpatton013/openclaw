import pathlib
import subprocess

from validator.base import ValidationResult, Validator

_SHEBANG = b"#!/usr/bin/env dotslash\n"
_HEADER = b"#!/usr/bin/env dotslash\n\n"


class DotslashValidator(Validator):
    name = "dotslash"
    fixer = True
    priority = 20

    def _body(self, file: pathlib.Path) -> bytes | None:
        data = file.read_bytes()
        if not data.startswith(_SHEBANG):
            return None
        rest = data[len(_SHEBANG) :]
        if rest.startswith(b"\n"):
            rest = rest[1:]
        return rest

    def _format(self, body: bytes) -> subprocess.CompletedProcess:
        bin_path = self.repo_root / "bin" / "biome"
        return subprocess.run(
            [
                str(bin_path),
                "format",
                "--indent-style=space",
                "--indent-width=2",
                "--stdin-file-path=manifest.jsonc",
                "-",
            ],
            input=body,
            capture_output=True,
        )

    def check(self, file: pathlib.Path) -> ValidationResult:
        body = self._body(file)
        if body is None:
            return ValidationResult(ok=True)
        r = self._format(body)
        if r.returncode != 0:
            return ValidationResult(
                ok=False,
                messages=tuple(
                    m
                    for m in (
                        r.stdout.decode(errors="replace"),
                        r.stderr.decode(errors="replace"),
                    )
                    if m
                ),
            )
        if file.read_bytes() != _HEADER + r.stdout:
            return ValidationResult(
                ok=False,
                messages=("dotslash manifest is not biome-formatted",),
            )
        return ValidationResult(ok=True)

    def fix(self, file: pathlib.Path) -> ValidationResult:
        body = self._body(file)
        if body is None:
            return ValidationResult(ok=True, fixed=False)
        r = self._format(body)
        if r.returncode != 0:
            return ValidationResult(
                ok=False,
                messages=tuple(
                    m
                    for m in (
                        r.stdout.decode(errors="replace"),
                        r.stderr.decode(errors="replace"),
                    )
                    if m
                ),
            )
        expected = _HEADER + r.stdout
        if file.read_bytes() == expected:
            return ValidationResult(ok=True, fixed=False)
        file.write_bytes(expected)
        return ValidationResult(ok=True, fixed=True)

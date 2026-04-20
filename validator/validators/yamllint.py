import pathlib

from yamllint.config import YamlLintConfig
from yamllint.linter import run

from validator.base import ValidationResult, Validator

_CONFIG = YamlLintConfig("""
extends: default
rules:
  line-length: disable
""")


class YamllintValidator(Validator):
    name = "yamllint"
    fixer = False

    def check(self, file: pathlib.Path) -> ValidationResult:
        problems = [
            f"{p.line}:{p.column}: {p.message} ({p.rule})"
            for p in run(file.read_text(), _CONFIG)
            if p.level == "error"
        ]
        if problems:
            return ValidationResult(ok=False, messages=tuple(problems))
        return ValidationResult(ok=True)

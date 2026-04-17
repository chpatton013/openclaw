import pathlib

from validator.base import ValidationResult, Validator

_FORBIDDEN = set("\0 \t\\?*:;|\"'`<>")


class FilenameCharsValidator(Validator):
    name = "filename-chars"
    fixer = False

    def check(self, file: pathlib.Path) -> ValidationResult:
        rel = file.relative_to(self.repo_root)
        messages = []
        for part in rel.parts:
            bad = sorted({c for c in part if c in _FORBIDDEN}, key=ord)
            if bad:
                chars = ", ".join(repr(c)[1:-1] for c in bad)
                messages.append(f"'{part}': forbidden characters: {chars}")
        if messages:
            return ValidationResult(ok=False, messages=tuple(messages))
        return ValidationResult(ok=True)

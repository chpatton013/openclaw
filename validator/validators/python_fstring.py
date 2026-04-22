import ast
import io
import pathlib
import re
import tokenize

from validator.base import ValidationResult, Validator

# Matches {IDENTIFIER} but not:
# - {{IDENTIFIER}} (double-brace escape, not preceded by another {)
# - ${IDENTIFIER} (shell variable)
_PATTERN = re.compile(r"(?<![{$])\{[A-Za-z_][A-Za-z0-9_]*\}(?!\})")


def _string_prefix(token_string: str) -> str:
    prefix = []
    for ch in token_string:
        if ch in ('"', "'"):
            break
        prefix.append(ch)
    return "".join(prefix).lower()


class PythonFstringValidator(Validator):
    name = "python-fstring"
    fixer = False

    def check(self, file: pathlib.Path) -> ValidationResult:
        try:
            source = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ValidationResult(ok=True)

        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
        except tokenize.TokenError:
            return ValidationResult(ok=True)

        messages = []
        for tok in tokens:
            if tok.type != tokenize.STRING:
                continue
            prefix = _string_prefix(tok.string)
            # Skip f-strings (already substituting) and r-strings (intentional escape hatch)
            if "f" in prefix or "r" in prefix:
                continue
            try:
                value = ast.literal_eval(tok.string)
            except Exception:
                continue
            if not isinstance(value, str):
                continue
            matches = _PATTERN.findall(value)
            if matches:
                lineno = tok.start[0]
                placeholders = ", ".join(matches)
                messages.append(
                    f"line {lineno}: plain string has f-string-like placeholders"
                    f" ({placeholders}); add 'f' prefix or use r-string to suppress"
                )

        if messages:
            return ValidationResult(ok=False, messages=tuple(messages))
        return ValidationResult(ok=True)

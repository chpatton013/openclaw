import pathlib

from validator.base import ValidationResult, Validator

DIRECT_REPLACEMENTS: dict[str, str] = {
    "\u2014": "-",  # Em dash
    "\u2013": "-",  # En dash
    "\u2012": "-",  # Figure dash
    "\u2010": "-",  # Hyphen
    "\u00ad": "-",  # Soft hyphen
    "\u2018": "'",  # Left single quotation mark
    "\u2019": "'",  # Right single quotation mark
    "\u201a": "'",  # Single low-9 quotation mark
    "\u201b": "'",  # Single high-reversed-9 quotation mark
    "\u2032": "'",  # Prime
    "\u201c": '"',  # Left double quotation mark
    "\u201d": '"',  # Right double quotation mark
    "\u201e": '"',  # Double low-9 quotation mark
    "\u201f": '"',  # Double high-reversed-9 quotation mark
    "\u2033": '"',  # Double prime
    "\u2026": "...",  # Horizontal ellipsis
    "\u00a0": " ",  # Non-breaking space
    "\u202f": " ",  # Narrow no-break space
    "\u2009": " ",  # Thin space
    "\u200a": " ",  # Hair space
    "\u2003": " ",  # Em space
    "\u2002": " ",  # En space
    "\u200b": "",  # Zero-width space
    "\u200c": "",  # Zero-width non-joiner
    "\u200d": "",  # Zero-width joiner
    "\ufeff": "",  # Byte order mark / zero-width no-break space
    "\u2060": "",  # Word joiner
    "\u2022": "*",  # Bullet
    "\u00b7": "*",  # Middle dot
    "\u2039": "<",  # Single left-pointing angle quotation mark
    "\u203a": ">",  # Single right-pointing angle quotation mark
    "\u00ab": "<<",  # Left-pointing double angle quotation mark
    "\u00bb": ">>",  # Right-pointing double angle quotation mark
}


def _check_bytes(data: bytes) -> ValidationResult:
    messages = []
    for lineno, line in enumerate(data.splitlines(), 1):
        uni_cols = [col for col, b in enumerate(line, 1) if b > 0x7F]
        if uni_cols:
            messages.append(
                f"line {lineno}: non-ASCII byte at col {', '.join(str(c) for c in uni_cols)}"
            )
    if messages:
        return ValidationResult(ok=False, messages=tuple(messages))
    return ValidationResult(ok=True)


class UnicodeValidator(Validator):
    name = "unicode"
    fixer = True
    priority = 0

    def check(self, file: pathlib.Path) -> ValidationResult:
        return _check_bytes(file.read_bytes())

    def fix(self, file: pathlib.Path) -> ValidationResult:
        data = file.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return _check_bytes(data)

        for src, dst in DIRECT_REPLACEMENTS.items():
            text = text.replace(src, dst)

        fixed = text.encode("utf-8")
        changed = fixed != data
        if changed:
            file.write_bytes(fixed)

        result = _check_bytes(fixed)
        return ValidationResult(ok=result.ok, messages=result.messages, fixed=changed)

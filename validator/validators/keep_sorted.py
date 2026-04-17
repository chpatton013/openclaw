import pathlib

from validator.base import ValidationResult, Validator

# (prefix, suffix) for keep-sorted comment markers, keyed by file extension.
# Files with unrecognized extensions are skipped.
_COMMENT_STYLES: dict[str, tuple[str, str]] = {
    "": ("#", ""),  # no extension - shell scripts, bin/* etc.
    ".py": ("#", ""),
    ".sh": ("#", ""),
    ".bash": ("#", ""),
    ".zsh": ("#", ""),
    ".toml": ("#", ""),
    ".yaml": ("#", ""),
    ".yml": ("#", ""),
    ".tf": ("#", ""),
    ".rb": ("#", ""),
    ".pl": ("#", ""),
    ".r": ("#", ""),
    ".js": ("//", ""),
    ".ts": ("//", ""),
    ".jsx": ("//", ""),
    ".tsx": ("//", ""),
    ".java": ("//", ""),
    ".c": ("//", ""),
    ".cpp": ("//", ""),
    ".cc": ("//", ""),
    ".h": ("//", ""),
    ".hpp": ("//", ""),
    ".go": ("//", ""),
    ".rs": ("//", ""),
    ".swift": ("//", ""),
    ".kt": ("//", ""),
    ".cs": ("//", ""),
    ".scss": ("//", ""),
    ".css": ("/*", "*/"),
    ".html": ("<!--", "-->"),
    ".htm": ("<!--", "-->"),
    ".xml": ("<!--", "-->"),
    ".svg": ("<!--", "-->"),
}


def _marker(prefix: str, suffix: str, word: str) -> str:
    if suffix:
        return f"{prefix} keep-sorted {word} {suffix}"
    return f"{prefix} keep-sorted {word}"


class _MarkerError(Exception):
    pass


def _find_regions(lines: list[str], begin: str, end: str) -> list[tuple[int, int]]:
    """Return list of (begin_idx, end_idx) index pairs (exclusive of markers).

    Raises _MarkerError on structural problems (nested begins, orphaned ends).
    """
    regions: list[tuple[int, int]] = []
    start: int | None = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == begin:
            if start is not None:
                raise _MarkerError(f"line {start + 1}: nested keep-sorted begin")
            start = i
        elif stripped == end:
            if start is None:
                raise _MarkerError(f"line {i + 1}: keep-sorted end without begin")
            regions.append((start + 1, i))
            start = None
    if start is not None:
        raise _MarkerError(f"line {start + 1}: keep-sorted begin without end")
    return regions


def _sort_key(line: str) -> str:
    return line.strip().lower()


class KeepSortedValidator(Validator):
    name = "keep-sorted"
    fixer = True
    priority = 25

    def _markers(self, file: pathlib.Path) -> tuple[str, str] | None:
        style = _COMMENT_STYLES.get(file.suffix)
        if style is None:
            return None
        prefix, suffix = style
        return _marker(prefix, suffix, "begin"), _marker(prefix, suffix, "end")

    def check(self, file: pathlib.Path) -> ValidationResult:
        markers = self._markers(file)
        if markers is None:
            return ValidationResult(ok=True)
        begin, end = markers
        lines = file.read_text(errors="replace").splitlines(keepends=True)
        try:
            regions = _find_regions(lines, begin, end)
        except _MarkerError as e:
            return ValidationResult(ok=False, messages=(str(e),))
        messages = [
            f"line {start}: keep-sorted region is not sorted"
            for start, stop in regions
            if lines[start:stop] != sorted(lines[start:stop], key=_sort_key)
        ]
        if messages:
            return ValidationResult(ok=False, messages=tuple(messages))
        return ValidationResult(ok=True)

    def fix(self, file: pathlib.Path) -> ValidationResult:
        markers = self._markers(file)
        if markers is None:
            return ValidationResult(ok=True)
        begin, end = markers

        text = file.read_text(errors="replace")
        lines = text.splitlines(keepends=True)
        try:
            regions = _find_regions(lines, begin, end)
        except _MarkerError as e:
            return ValidationResult(ok=False, messages=(str(e),))

        changed = False
        for start, stop in regions:
            region = lines[start:stop]
            sorted_region = sorted(region, key=_sort_key)
            if region != sorted_region:
                lines[start:stop] = sorted_region
                changed = True

        if changed:
            file.write_text("".join(lines))
        return ValidationResult(ok=True, fixed=changed)

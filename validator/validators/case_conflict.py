import pathlib
import subprocess
import threading

from validator.base import ValidationResult, Validator


class CaseConflictValidator(Validator):
    name = "case-conflict"
    fixer = False

    _cache: dict[pathlib.Path, dict[str, list[pathlib.Path]]] = {}
    _lock = threading.Lock()

    def _lower_map(self) -> dict[str, list[pathlib.Path]]:
        with self._lock:
            if self.repo_root not in self._cache:
                r = subprocess.run(
                    [
                        "git",
                        "ls-files",
                        "-z",
                        "--cached",
                        "--others",
                        "--exclude-standard",
                    ],
                    cwd=self.repo_root,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                mapping: dict[str, list[pathlib.Path]] = {}
                for p in r.stdout.split("\0"):
                    if p:
                        mapping.setdefault(p.lower(), []).append(pathlib.Path(p))
                self._cache[self.repo_root] = mapping
        return self._cache[self.repo_root]

    def check(self, file: pathlib.Path) -> ValidationResult:
        rel = str(file.relative_to(self.repo_root))
        conflicts = [
            str(p) for p in self._lower_map().get(rel.lower(), []) if str(p) != rel
        ]
        if conflicts:
            return ValidationResult(
                ok=False,
                messages=(f"case-insensitive conflict with: {', '.join(conflicts)}",),
            )
        return ValidationResult(ok=True)

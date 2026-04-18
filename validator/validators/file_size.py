import dataclasses
import pathlib
import threading

import pathspec

from validator.base import BaseConfig, ValidationResult, Validator

_LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1"

_gitattributes_cache: dict[pathlib.Path, list[pathspec.PathSpec]] = {}
_gitattributes_lock = threading.Lock()


def _is_lfs_pointer(data: bytes) -> bool:
    return data.startswith(_LFS_POINTER_PREFIX)


def _lfs_patterns_for(
    repo_root: pathlib.Path, file: pathlib.Path
) -> list[pathspec.PathSpec]:
    """Return all gitattributes-derived LFS PathSpecs that cover `file`."""
    results = []
    for directory in [repo_root, *file.parents]:
        if not directory.is_relative_to(repo_root):
            break
        ga_path = directory / ".gitattributes"
        with _gitattributes_lock:
            if ga_path not in _gitattributes_cache:
                _gitattributes_cache[ga_path] = _parse_lfs_gitattributes(ga_path)
            specs = _gitattributes_cache[ga_path]
        if specs:
            results.extend(specs)
    return results


def _parse_lfs_gitattributes(path: pathlib.Path) -> list[pathspec.PathSpec]:
    if not path.exists():
        return []
    specs = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2 and any("filter=lfs" in p for p in parts[1:]):
                specs.append(pathspec.PathSpec.from_lines("gitignore", [parts[0]]))
    except OSError:
        pass
    return specs


def _is_lfs_tracked(repo_root: pathlib.Path, file: pathlib.Path) -> bool:
    rel = file.relative_to(repo_root)
    specs = _lfs_patterns_for(repo_root, file)
    return any(spec.match_file(str(rel)) for spec in specs)


@dataclasses.dataclass(frozen=True)
class FileSizeConfig(BaseConfig):
    max_bytes: int = 10 * 1024 * 1024  # 10 MB


class FileSizeValidator(Validator):
    name = "file-size"
    fixer = False
    Config = FileSizeConfig

    @classmethod
    def config_from_options(cls, opts: dict) -> FileSizeConfig:
        return FileSizeConfig(
            include_files=tuple(opts.get("include_files", ())),
            exclude_files=tuple(opts.get("exclude_files", ())),
            max_bytes=int(opts.get("max_bytes", 10 * 1024 * 1024)),
        )

    def check(self, file: pathlib.Path) -> ValidationResult:
        data = file.read_bytes()

        if _is_lfs_pointer(data):
            return ValidationResult(ok=True)

        if _is_lfs_tracked(self.repo_root, file):
            return ValidationResult(
                ok=False,
                messages=(
                    f"file is configured for git-lfs in .gitattributes but is stored as a blob "
                    f"({len(data):,} bytes); run `git lfs migrate import --include='{file.name}'` "
                    f"or remove the lfs filter from .gitattributes",
                ),
            )

        max_bytes = self.config.max_bytes
        if len(data) > max_bytes:
            return ValidationResult(
                ok=False,
                messages=(
                    f"file size {len(data):,} bytes exceeds limit of {max_bytes:,} bytes",
                ),
            )

        return ValidationResult(ok=True)

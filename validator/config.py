import pathlib
import subprocess
import tomllib
from typing import Any

from pathspec import PathSpec


def find_validator_tomls(repo_root: pathlib.Path) -> list[pathlib.Path]:
    result = subprocess.run(
        [
            "git",
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            ":(glob)**/.validator.toml",
            ".validator.toml",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    if not result.stdout:
        return []
    paths = {repo_root / p for p in result.stdout.split("\0") if p}
    return sorted(paths, key=lambda p: len(p.parts))


def load_tomls(repo_root: pathlib.Path) -> list[tuple[pathlib.Path, dict]]:
    return [(p, tomllib.loads(p.read_text())) for p in find_validator_tomls(repo_root)]


def _matches_any(globs: list[str], file_rel: str) -> bool:
    if not globs:
        return False
    spec = PathSpec.from_lines("gitwildmatch", globs)
    return spec.match_file(file_rel)


def effective_config_for_file(
    tomls: list[tuple[pathlib.Path, dict]],
    validator_name: str,
    file: pathlib.Path,
    repo_root: pathlib.Path,
) -> tuple[bool, dict] | None:
    file_abs = file if file.is_absolute() else (repo_root / file).resolve()
    try:
        file_rel = str(file_abs.relative_to(repo_root))
    except ValueError:
        return None

    matched = False
    opts: dict[str, Any] = {}
    any_config = False

    for toml_path, toml_data in tomls:
        toml_dir = toml_path.parent
        if toml_dir != repo_root:
            try:
                file_abs.relative_to(toml_dir)
            except ValueError:
                continue

        section = toml_data.get("validator", {}).get(validator_name)
        if section is None:
            continue
        any_config = True

        dir_rel = toml_dir.relative_to(repo_root)
        prefix = "" if str(dir_rel) == "." else str(dir_rel) + "/"

        includes = [prefix + g for g in section.get("include_files", [])]
        excludes = [prefix + g for g in section.get("exclude_files", [])]

        if _matches_any(includes, file_rel):
            matched = True
        if _matches_any(excludes, file_rel):
            matched = False

        for k, v in section.items():
            if k in ("include_files", "exclude_files"):
                continue
            if isinstance(v, list) and isinstance(opts.get(k), list):
                opts[k] = opts[k] + v
            else:
                opts[k] = v

    if not any_config:
        return None
    return matched, opts

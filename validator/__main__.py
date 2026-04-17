import argparse
import glob
import pathlib
import subprocess
import sys

from validator.runner import run


def _find_repo_root() -> pathlib.Path:
    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return pathlib.Path(r.stdout.strip())


def _resolve_files(args: list[str], repo_root: pathlib.Path) -> list[pathlib.Path]:
    result: list[pathlib.Path] = []
    for arg in args:
        if any(c in arg for c in "*?["):
            result.extend(pathlib.Path(x) for x in glob.glob(arg, recursive=True))
            continue
        p = pathlib.Path(arg)
        if not p.is_absolute():
            p = repo_root / p
        if p.is_dir():
            ls = subprocess.run(
                [
                    "git",
                    "ls-files",
                    "-z",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                    str(p),
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=True,
            )
            result.extend(repo_root / x for x in ls.stdout.split("\0") if x)
        elif p.exists():
            result.append(p)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(prog="validate")
    parser.add_argument("--fix", action="store_true")
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--task-timeout", type=int, default=60)
    parser.add_argument(
        "--dirty",
        action="store_true",
        help="Validate staged files instead of the whole repo.",
    )
    parser.add_argument("files", nargs="*")
    args = parser.parse_args()

    if args.dirty and args.files:
        parser.error("--dirty is mutually exclusive with FILE args")

    repo_root = _find_repo_root()
    files = _resolve_files(args.files, repo_root) if args.files else None

    return run(
        files=files,
        repo_root=repo_root,
        fix=args.fix,
        workers=args.workers,
        dirty=args.dirty,
        task_timeout=args.task_timeout,
    )


if __name__ == "__main__":
    sys.exit(main())

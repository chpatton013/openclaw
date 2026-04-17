import argparse
import pathlib
import subprocess
import sys

from validator.precommit.hook import run_hook
from validator.precommit.install import install_hook


def _find_repo_root() -> pathlib.Path:
    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return pathlib.Path(r.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="pre-commit",
        description=(
            "Run validators against staged files, or install this tool as the "
            "git pre-commit hook."
        ),
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Install this script as the pre-commit hook and exit.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="With --install, overwrite an existing pre-commit hook.",
    )
    args = parser.parse_args()

    repo_root = _find_repo_root()
    if args.install:
        return install_hook(repo_root, force=args.force)
    if args.force:
        parser.error("--force requires --install")
    return run_hook(repo_root)


if __name__ == "__main__":
    sys.exit(main())

import argparse
import os
import pathlib
import subprocess
import sys
from dataclasses import dataclass

HERE = pathlib.Path(__file__).parent
REPO_ROOT = HERE.parent.parent
BIN_DIR = REPO_ROOT / "bin"
HOOK_WRAPPER = BIN_DIR / "pre-commit"
GIT_HOOK_PATH = REPO_ROOT / ".git" / "hooks" / "pre-commit"


@dataclass(frozen=True)
class Check:
    name: str
    cmd: list[str]
    fix_hint: str | None


CHECKS = [
    Check(
        name="black",
        cmd=[str(BIN_DIR / "black"), "--check", str(REPO_ROOT)],
        fix_hint=f"{BIN_DIR / 'black'} {REPO_ROOT}",
    ),
    Check(
        name="pyright",
        cmd=[str(BIN_DIR / "pyright")],
        fix_hint=None,
    ),
]


def run_checks() -> int:
    failures: list[tuple[Check, subprocess.CompletedProcess[str]]] = []
    for check in CHECKS:
        result = subprocess.run(
            check.cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            failures.append((check, result))

    if not failures:
        return 0

    for check, result in failures:
        print(f"=== {check.name} ===", file=sys.stderr)
        if result.stdout:
            sys.stderr.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)

    print("", file=sys.stderr)
    print("Pre-commit checks failed:", file=sys.stderr)
    for check, _ in failures:
        line = f"  - {check.name}"
        if check.fix_hint is not None:
            line += f"  (fix: {check.fix_hint})"
        print(line, file=sys.stderr)
    return 1


def install_hook() -> int:
    if not HOOK_WRAPPER.exists():
        print(f"Hook wrapper not found at {HOOK_WRAPPER}", file=sys.stderr)
        return 1

    hooks_dir = GIT_HOOK_PATH.parent
    if not hooks_dir.is_dir():
        print(f"Git hooks dir not found at {hooks_dir}", file=sys.stderr)
        return 1

    target = os.path.relpath(HOOK_WRAPPER, start=hooks_dir)

    if GIT_HOOK_PATH.is_symlink():
        if os.readlink(GIT_HOOK_PATH) == target:
            print(f"Already installed: {GIT_HOOK_PATH} -> {target}")
            return 0
        print(
            f"Refusing to overwrite existing hook at {GIT_HOOK_PATH} "
            f"(symlink -> {os.readlink(GIT_HOOK_PATH)})",
            file=sys.stderr,
        )
        return 1
    if GIT_HOOK_PATH.exists():
        print(
            f"Refusing to overwrite existing hook at {GIT_HOOK_PATH}", file=sys.stderr
        )
        return 1

    GIT_HOOK_PATH.symlink_to(target)
    print(f"Installed: {GIT_HOOK_PATH} -> {target}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run pre-commit checks, or install this script as the repo's git pre-commit hook."
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Install this script as the pre-commit hook and exit.",
    )
    args = parser.parse_args()

    if args.install:
        return install_hook()
    return run_checks()


if __name__ == "__main__":
    sys.exit(main())

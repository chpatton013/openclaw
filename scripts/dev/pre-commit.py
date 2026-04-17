import pathlib
import subprocess
import sys
from dataclasses import dataclass

HERE = pathlib.Path(__file__).parent
REPO_ROOT = HERE.parent.parent


@dataclass(frozen=True)
class Check:
    name: str
    cmd: list[str]
    fix_hint: str | None


CHECKS = [
    Check(
        name="black",
        cmd=[str(HERE / "black"), "--check", str(REPO_ROOT)],
        fix_hint=f"{HERE / 'black'} {REPO_ROOT}",
    ),
    Check(
        name="pyright",
        cmd=[str(HERE / "pyright")],
        fix_hint=None,
    ),
]


def main() -> int:
    failures: list[Check] = []
    for check in CHECKS:
        print(f"=== {check.name} ===", flush=True)
        result = subprocess.run(check.cmd, cwd=REPO_ROOT)
        if result.returncode != 0:
            failures.append(check)

    if not failures:
        return 0

    print("", file=sys.stderr)
    print("Pre-commit checks failed:", file=sys.stderr)
    for check in failures:
        line = f"  - {check.name}"
        if check.fix_hint is not None:
            line += f"  (fix: {check.fix_hint})"
        print(line, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

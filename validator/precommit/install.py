import os
import pathlib
import sys


def install_hook(repo_root: pathlib.Path, *, force: bool = False) -> int:
    hook_wrapper = repo_root / "bin" / "pre-commit"
    git_hook_path = repo_root / ".git" / "hooks" / "pre-commit"

    if not hook_wrapper.exists():
        print(f"Hook wrapper not found at {hook_wrapper}", file=sys.stderr)
        return 1

    hooks_dir = git_hook_path.parent
    if not hooks_dir.is_dir():
        print(f"Git hooks dir not found at {hooks_dir}", file=sys.stderr)
        return 1

    target = os.path.relpath(hook_wrapper, start=hooks_dir)

    if git_hook_path.is_symlink():
        if os.readlink(git_hook_path) == target:
            print(f"Already installed: {git_hook_path} -> {target}")
            return 0
        if not force:
            print(
                f"Refusing to overwrite existing hook at {git_hook_path} "
                f"(symlink -> {os.readlink(git_hook_path)}); pass --force to replace",
                file=sys.stderr,
            )
            return 1
        git_hook_path.unlink()
    elif git_hook_path.exists():
        if not force:
            print(
                f"Refusing to overwrite existing hook at {git_hook_path}; "
                f"pass --force to replace",
                file=sys.stderr,
            )
            return 1
        git_hook_path.unlink()

    git_hook_path.symlink_to(target)
    print(f"Installed: {git_hook_path} -> {target}")
    return 0

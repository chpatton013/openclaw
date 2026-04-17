import pathlib

from validator.runner import run


def run_hook(repo_root: pathlib.Path) -> int:
    return run(repo_root=repo_root, dirty=True, fix=False)

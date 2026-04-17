import dataclasses
import os
import pathlib
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from validator.base import ValidationResult, Validator
from validator.config import effective_config_for_file, load_tomls
from validator.registry import all_validators

_CANCEL = threading.Event()


def _install_cancel_handler() -> None:
    if threading.current_thread() is not threading.main_thread():
        return

    def handler(signum, frame):
        _CANCEL.set()
        signal.signal(signal.SIGINT, signal.SIG_DFL)

    signal.signal(signal.SIGINT, handler)


@dataclass(frozen=True)
class Task:
    validator: Validator
    file: pathlib.Path

    @property
    def validator_name(self) -> str:
        return type(self.validator).name


def _build_tasks(
    files: list[pathlib.Path],
    *,
    repo_root: pathlib.Path,
    validators: dict[str, type[Validator]],
    tomls: list[tuple[pathlib.Path, dict]],
) -> list[Task]:
    tasks: list[Task] = []
    for file in files:
        rel = file.relative_to(repo_root)
        for name, cls in validators.items():
            resolved = effective_config_for_file(tomls, name, file, repo_root)
            if resolved is None:
                continue
            matched, opts = resolved
            if not matched:
                continue
            cfg = cls.config_from_options(opts)
            tasks.append(Task(cls(cfg, repo_root), rel))
    return tasks


def _run_check(task: Task, repo_root: pathlib.Path, timeout: int) -> ValidationResult:
    if _CANCEL.is_set():
        return ValidationResult(ok=False, file=task.file, messages=("cancelled",))
    t0, c0 = time.perf_counter(), time.thread_time()
    try:
        res = task.validator.check(repo_root / task.file)
    except subprocess.TimeoutExpired:
        return ValidationResult(
            ok=False,
            file=task.file,
            runtime_s=time.perf_counter() - t0,
            cpu_s=time.thread_time() - c0,
            messages=(f"timeout after {timeout}s",),
        )
    return dataclasses.replace(
        res,
        file=task.file,
        runtime_s=time.perf_counter() - t0,
        cpu_s=time.thread_time() - c0,
    )


def _run_fix_chain(
    tasks: list[Task], repo_root: pathlib.Path, timeout: int
) -> list[tuple[Task, ValidationResult]]:
    results: list[tuple[Task, ValidationResult]] = []
    for task in tasks:
        if _CANCEL.is_set():
            results.append(
                (
                    task,
                    ValidationResult(ok=False, file=task.file, messages=("cancelled",)),
                )
            )
            continue
        t0, c0 = time.perf_counter(), time.thread_time()
        try:
            res = task.validator.fix(repo_root / task.file)
            res = dataclasses.replace(
                res,
                file=task.file,
                runtime_s=time.perf_counter() - t0,
                cpu_s=time.thread_time() - c0,
            )
        except subprocess.TimeoutExpired:
            res = ValidationResult(
                ok=False,
                file=task.file,
                runtime_s=time.perf_counter() - t0,
                cpu_s=time.thread_time() - c0,
                messages=(f"timeout after {timeout}s",),
            )
        results.append((task, res))
    return results


def _staged_files(repo_root: pathlib.Path) -> list[pathlib.Path]:
    r = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [repo_root / p for p in r.stdout.split("\0") if p]


def _all_tracked_files(repo_root: pathlib.Path) -> list[pathlib.Path]:
    r = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [repo_root / p for p in r.stdout.split("\0") if p]


def _print_profile(results: list[tuple[Task, ValidationResult]]) -> None:
    stats: dict[str, list[float]] = {}
    for task, res in results:
        stats.setdefault(task.validator_name, []).append(res.cpu_s)
    rows = sorted(stats.items(), key=lambda x: -sum(x[1]))
    name_w = max(len(name) for name, _ in rows)
    print("\n=== Profile (CPU time) ===", file=sys.stderr)
    for name, times in rows:
        total = sum(times)
        count = len(times)
        print(
            f"  {name:{name_w}}  {total:8.4f}s  {count:5d} calls"
            f"  {total / count:.6f}s avg",
            file=sys.stderr,
        )


def run(
    files: list[pathlib.Path] | None = None,
    *,
    repo_root: pathlib.Path,
    fix: bool = False,
    workers: int | None = None,
    dirty: bool = False,
    task_timeout: int = 60,
    profile: bool = False,
) -> int:
    _CANCEL.clear()
    _install_cancel_handler()

    if dirty:
        if files:
            raise ValueError("dirty=True is incompatible with explicit file list")
        files = _staged_files(repo_root)
    elif not files:
        files = _all_tracked_files(repo_root)

    files = [f if f.is_absolute() else (repo_root / f).resolve() for f in files]
    files = [f for f in files if f.is_file() and f.is_relative_to(repo_root)]
    if not files:
        return 0

    validators = all_validators()
    tomls = load_tomls(repo_root)
    if workers is None:
        workers = min(32, (os.cpu_count() or 1) + 4)

    failures: list[tuple[Task, ValidationResult]] = []
    profile_results: list[tuple[Task, ValidationResult]] | None = (
        [] if profile else None
    )

    all_tasks = _build_tasks(
        files, repo_root=repo_root, validators=validators, tomls=tomls
    )

    if fix:
        fix_tasks = [t for t in all_tasks if type(t.validator).fixer]
        check_tasks = [t for t in all_tasks if not type(t.validator).fixer]

        by_file: dict[pathlib.Path, list[Task]] = {}
        for t in fix_tasks:
            by_file.setdefault(t.file, []).append(t)
        for chain in by_file.values():
            chain.sort(key=lambda t: (type(t.validator).priority, t.validator_name))

        if by_file:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = {
                    pool.submit(_run_fix_chain, chain, repo_root, task_timeout): file
                    for file, chain in by_file.items()
                }
                for fut in as_completed(futs):
                    for task, res in fut.result():
                        if profile_results is not None:
                            profile_results.append((task, res))
                        if not res.ok:
                            failures.append((task, res))
    else:
        check_tasks = all_tasks

    if check_tasks:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {
                pool.submit(_run_check, t, repo_root, task_timeout): t
                for t in check_tasks
            }
            for fut in as_completed(futs):
                task = futs[fut]
                res = fut.result()
                if profile_results is not None:
                    profile_results.append((task, res))
                if not res.ok:
                    failures.append((task, res))

    failures.sort(key=lambda x: (x[0].validator_name, str(x[0].file)))
    for task, res in failures:
        print(f"=== {task.validator_name}: {task.file} ===", file=sys.stderr)
        for msg in res.messages:
            sys.stderr.write(msg)
            if not msg.endswith("\n"):
                sys.stderr.write("\n")

    if profile_results:
        _print_profile(profile_results)

    return 1 if failures else 0

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence


class GitError(RuntimeError):
    pass


def is_git_repo(repo_root: Path) -> bool:
    try:
        run_git(repo_root, ["rev-parse", "--is-inside-work-tree"])
        return True
    except GitError:
        return False


def run_git(repo_root: Path, args: Sequence[str]) -> str:
    cmd = ["git", "-C", str(repo_root), *args]
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError as e:
        raise GitError("git not found") from e
    if p.returncode != 0:
        raise GitError(p.stderr.strip() or f"git failed: {' '.join(cmd)}")
    return p.stdout

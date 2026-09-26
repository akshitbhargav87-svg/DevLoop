"""Read-only Git history tools for DevLoop."""

from __future__ import annotations

import os
from pathlib import Path

from git import Repo


def _repo(workspace: str | os.PathLike[str]) -> Repo:
    path = Path(workspace).expanduser().resolve(strict=True)
    return Repo(path, search_parent_directories=False)


def recent_commits(workspace: str | os.PathLike[str], n: int) -> list[dict[str, str]]:
    """Return up to n commits, newest first."""
    if n < 0:
        raise ValueError("n must be non-negative")
    repo = _repo(workspace)
    return [
        {
            "sha": commit.hexsha,
            "message": commit.message.strip(),
            "author": commit.author.name or "",
            "date": commit.committed_datetime.isoformat(),
        }
        for commit in repo.iter_commits(max_count=n)
    ]


def show_commit_diff(workspace: str | os.PathLike[str], sha: str) -> str:
    """Return a commit's unified diff against its first parent (or empty tree)."""
    repo = _repo(workspace)
    commit = repo.commit(sha)
    if not commit.parents:
        diff = repo.git.show("--format=", "--root", "--no-ext-diff", "--no-renames", commit.hexsha)
    else:
        diff = repo.git.diff(
            "--no-ext-diff", "--no-renames", "--no-color", "--unified=3",
            commit.parents[0].hexsha, commit.hexsha, "--",
        )
    return diff

"""Filesystem tools used by DevLoop agents."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from os import PathLike
from git import Repo


def _workspace_path(workspace: str | os.PathLike[str]) -> Path:
    path = Path(workspace).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise NotADirectoryError(f"Workspace is not a directory: {path}")
    return path


def _inside_workspace(
    root: Path,
    rel_path: str | PathLike[str],
) -> Path:
    candidate: Path = (root / rel_path).resolve()

    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("Path must stay inside the workspace") from exc

    return candidate


def clone_repo(url: str, branch: str) -> str:
    """Clone a URL or local Git repository branch into a temporary workspace."""
    destination = Path(tempfile.mkdtemp(prefix="devloop-repo-"))
    try:
        Repo.clone_from(url, str(destination), branch=branch, single_branch=True)
    except Exception:
        # Do not leave an unusable partial clone behind.
        import shutil

        shutil.rmtree(destination, ignore_errors=True)
        raise
    return str(destination)


def list_files(workspace: str | os.PathLike[str]) -> str:
    """Return sorted, workspace-relative paths, excluding Git internals."""
    root = _workspace_path(workspace)
    files = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink() and ".git" not in path.relative_to(root).parts
    )
    return "\n".join(files)


def read_file(workspace: str | os.PathLike[str], rel_path: str) -> str:
    """Read a UTF-8 file inside a workspace; reject traversal and directories."""
    root = _workspace_path(workspace)
    path = _inside_workspace(root, rel_path)
    if not path.is_file():
        raise IsADirectoryError(f"Not a file: {rel_path}")
    return path.read_text(encoding="utf-8")


def grep_symbol(workspace: str | os.PathLike[str], symbol: str) -> list[dict[str, object]]:
    """Find literal, case-sensitive symbol occurrences in UTF-8 text files."""
    if not symbol:
        raise ValueError("symbol must not be empty")
    root = _workspace_path(workspace)
    matches: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if not path.is_file() or path.is_symlink() or ".git" in relative.parts:
            continue
        try:
            with path.open("r", encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, start=1):
                    if symbol in line:
                        matches.append({"file": relative.as_posix(), "line": line_number, "text": line.rstrip("\r\n")})
        except (UnicodeDecodeError, OSError):
            # Binary or unreadable files are not searchable text.
            continue
    return matches

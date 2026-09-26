"""Local patch application and pytest runner. No remote Git operations."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def _workspace_path(workspace: str | os.PathLike[str]) -> Path:
    path = Path(workspace).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise NotADirectoryError(f"Workspace is not a directory: {path}")
    return path


def apply_patch(workspace: str | os.PathLike[str], diff_str: str) -> bool:
    """Apply a unified diff locally after validating it; never contacts a remote."""
    if not diff_str.strip():
        return False
    root = _workspace_path(workspace)
    patch_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", suffix=".patch", dir=root, delete=False
        ) as patch_file:
            patch_file.write(diff_str)
            patch_path = Path(patch_file.name)
        check = subprocess.run(
            ["git", "apply", "--check", "--whitespace=nowarn", str(patch_path)],
            cwd=root, text=True, capture_output=True, timeout=30, check=False,
        )
        if check.returncode != 0:
            return False
        result = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", str(patch_path)],
            cwd=root, text=True, capture_output=True, timeout=30, check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        if patch_path is not None:
            patch_path.unlink(missing_ok=True)


def run_pytest(workspace: str | os.PathLike[str]) -> dict[str, object]:
    """Run pytest in the workspace and return concise structured results."""
    root = _workspace_path(workspace)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=root, text=True, capture_output=True, timeout=300, check=False,
        )
        output = (result.stdout + result.stderr).strip()
        summary: re.Match[str] | None = re.search(r"(?m)^\s*(?P<summary>(?:\d+\s+\w+(?:,\s*)?)+)\s+in\s+\d+(?:\.\d+)?s\s*$",output,)
        summary_text: str = summary.group("summary") if summary else ""
        counts: dict[str, int] = {
           name: int(count)
           for count, name in re.findall(
                r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed)",
                 summary_text,
                 )
        }
        failed = counts.get("failed", 0)
        errors_count = counts.get("error", counts.get("errors", 0))
        errors = []
        for line in output.splitlines():
            if line.startswith("E   ") or "ERROR collecting" in line or line.startswith("ERROR "):
                errors.append(line.strip())
        if result.returncode not in (0, 1) and not errors:
            errors.append(f"pytest exited with status {result.returncode}")
        if errors_count and not errors:
            errors.append(f"pytest reported {errors_count} error(s)")
        return {
            "passed": counts.get("passed", 0),
            "failed": failed,
            "errors": errors,
            "output_excerpt": output[-4000:],
        }
    except subprocess.TimeoutExpired as exc:
        output = "\n".join(part.decode(errors="replace") if isinstance(part, bytes) else part or "" for part in (exc.stdout, exc.stderr)).strip()
        return {"passed": 0, "failed": 0, "errors": ["pytest timed out after 300 seconds"], "output_excerpt": output[-4000:]}
    except OSError as exc:
        return {"passed": 0, "failed": 0, "errors": [str(exc)], "output_excerpt": ""}


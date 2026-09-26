"""Local patch application and pytest runner. No remote Git operations."""

from __future__ import annotations

import os
import logging
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def _workspace_path(workspace: str | os.PathLike[str]) -> Path:
    path = Path(workspace).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise NotADirectoryError(f"Workspace is not a directory: {path}")
    return path


def _sanitize_patch(diff_str: str) -> str:
    """Remove LLM-generated artefacts that cause 'git apply' to reject the patch.

    LLMs frequently emit:
    - Markdown code fences  (```diff â€¦ ```)
    - Fake ``index`` lines  (index 1234567..89abcdef 100644)
    - Spurious mode-change lines  (old mode / new mode)

    None of these are required for ``git apply`` and all of them cause
    ``error: corrupt patch`` when the SHA values are fabricated.
    """
    # Strip markdown code fences that wrap the diff
    diff_str = re.sub(r"(?m)^```(?:diff)?\s*\n?", "", diff_str)
    diff_str = re.sub(r"(?m)^```\s*$", "", diff_str)
    # Remove fake index lines (git requires real object SHAs; LLMs invent them)
    diff_str = re.sub(r"(?m)^index [0-9a-f]+\.\.[0-9a-f]+(?: [0-9]+)?\n", "", diff_str)
    # Remove spurious mode-change lines
    diff_str = re.sub(r"(?m)^(?:old|new) mode [0-9]+\n", "", diff_str)
    return diff_str


_HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))?"
    r" \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<context>.*)$"
)


def _repair_hunk_headers(diff_str: str) -> str:
    """Repair incorrect unified-diff hunk line counts emitted by LLMs.

    The hunk body is authoritative for the number of old/new lines.
    This only changes the counts in @@ headers; it does not invent or
    modify patch content.
    """
    lines = diff_str.splitlines(keepends=True)
    repaired: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        match = _HUNK_HEADER_RE.match(line.rstrip("\r\n"))

        if not match:
            repaired.append(line)
            i += 1
            continue

        old_count = 0
        new_count = 0
        j = i + 1

        while j < len(lines):
            body_line = lines[j]

            if _HUNK_HEADER_RE.match(body_line.rstrip("\r\n")):
                break

            if body_line.startswith(("diff --git ", "--- ", "+++ ")):
                break

            if body_line.startswith((" ", "-")):
                old_count += 1

            if body_line.startswith((" ", "+")):
                new_count += 1

            # Git's special marker is metadata, not a hunk line.
            if body_line.startswith("\\ No newline at end of file"):
                pass

            j += 1

        old_start = int(match.group("old_start"))
        new_start = int(match.group("new_start"))
        context = match.group("context")
        line_ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""

        repaired.append(
            f"@@ -{old_start},{old_count} +{new_start},{new_count} @@{context}{line_ending}"
        )
        repaired.extend(lines[i + 1 : j])

        i = j

    return "".join(repaired)


def _patch_targets(diff_str: str) -> set[str]:
    targets: set[str] = set()
    for line in diff_str.splitlines():
        match = re.match(r"^diff --git a/(\S+) b/(\S+)$", line)
        if match:
            targets.update(match.groups())
            continue
        match = re.match(r"^(?:--- a/(\S+)|\+\+\+ b/(\S+))$", line)
        if match:
            targets.update(path for path in match.groups() if path)
    return targets


def _valid_patch_targets(root: Path, diff_str: str, expected_files: set[str] | None = None) -> bool:
    targets = _patch_targets(diff_str)
    if not targets or (expected_files is not None and targets != expected_files):
        return False
    for rel_path in targets:
        if rel_path.startswith(("/", "\\")) or ".." in Path(rel_path).parts:
            return False
        candidate = (root / rel_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return False
        if not candidate.is_file() or candidate.is_symlink():
            return False
    return True


def _repair_unprefixed_context(root: Path, diff_str: str) -> str:
    """Restore missing context markers only for exact lines in target files."""
    lines = diff_str.splitlines(keepends=True)
    repaired: list[str] = []
    current_path: str | None = None
    source_lines: set[str] = set()
    in_hunk = False
    for line in lines:
        stripped = line.rstrip("\r\n")
        git_header = re.match(r"^diff --git a/\S+ b/(\S+)$", stripped)
        if git_header:
            current_path = git_header.group(1)
            source = (root / current_path).resolve()
            source_lines = set(source.read_text(encoding="utf-8").splitlines()) if source.is_file() else set()
            in_hunk = False
        elif _HUNK_HEADER_RE.match(stripped):
            in_hunk = True
        elif stripped.startswith(("diff --git ", "--- ", "+++ ")):
            in_hunk = False
        elif in_hunk and stripped in source_lines and (
            not stripped.startswith(" ") or stripped[1:] not in source_lines
        ):
            line = " " + line
        repaired.append(line)
    return "".join(repaired)


def apply_patch(workspace: str | os.PathLike[str], diff_str: str) -> bool:
    """Apply a unified diff locally after validating it; never contacts a remote."""
    if not diff_str.strip():
        return False
    root = _workspace_path(workspace)
    diff_str = _sanitize_patch(diff_str)
    diff_str = _repair_unprefixed_context(root, diff_str)
    diff_str = _repair_hunk_headers(diff_str)
    if not diff_str.strip():
        return False
    if not _valid_patch_targets(root, diff_str):
        return False
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
            logger.warning("git apply --check failed: %s", check.stderr.strip())
            return False
        result = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", str(patch_path)],
            cwd=root, text=True, capture_output=True, timeout=30, check=False,
        )
        if result.returncode != 0:
            logger.warning("git apply failed: %s", result.stderr.strip())
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        if patch_path is not None:
            patch_path.unlink(missing_ok=True)


def validate_patch(
    workspace: str | os.PathLike[str],
    diff_str: str,
    test_command: str | None = None,
    expected_files: list[str] | None = None,
) -> dict[str, object]:
    """Apply a candidate patch to a disposable copy and require tests to pass."""
    root = _workspace_path(workspace)
    sanitized = _repair_unprefixed_context(root, _sanitize_patch(diff_str))
    sanitized = _repair_hunk_headers(sanitized)
    if not _valid_patch_targets(root, sanitized, set(expected_files) if expected_files is not None else None):
        return {"passed": False, "error": "Candidate patch targets do not match existing workspace files."}
    with tempfile.TemporaryDirectory(prefix="devloop-patch-check-") as temp_dir:
        candidate = Path(temp_dir) / "workspace"
        shutil.copytree(
            root,
            candidate,
            ignore=shutil.ignore_patterns(
                ".pytest_cache",
                "__pycache__",
                ".venv",
                "venv",
                "node_modules",
            ),
        )
        if not apply_patch(candidate, diff_str):
            return {
                "passed": False,
                "error": "Candidate patch does not apply cleanly.",
            }

        test_results = run_pytest(candidate, test_command)
        passed = (
            test_results.get("passed", 0) > 0
            and test_results.get("failed", 0) == 0
            and test_results.get("errors", []) == []
        )
        return {
            "passed": passed,
            "test_results": test_results,
        }


def run_pytest(
    workspace: str | os.PathLike[str],
    test_command: str | None = None,
) -> dict[str, object]:
    """Run the selected test command in the workspace and summarize results."""
    root = _workspace_path(workspace)
    try:
        if test_command and test_command.strip():
            command = shlex.split(test_command, posix=os.name != "nt")
            if os.name == "nt":
                command = [
                    arg[1:-1]
                    if len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in "\"'"
                    else arg
                    for arg in command
                ]
            if not command:
                return {
                    "passed": 0,
                    "failed": 0,
                    "errors": ["Test command is empty"],
                    "output_excerpt": "",
                }
            if os.path.basename(command[0]).lower() in {"pytest", "pytest.exe"}:
                command = [sys.executable, "-m", "pytest", *command[1:]]
        else:
            command = [sys.executable, "-m", "pytest", "-q"]

        result = subprocess.run(
            command,
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
            if "ERROR collecting" in line or line.startswith("ERROR "):
                errors.append(line.strip())
        if result.returncode not in (0, 1) and not errors:
            errors.append(f"pytest exited with status {result.returncode}")
        if errors_count and not errors:
            errors.append(f"pytest reported {errors_count} error(s)")
        passed = counts.get("passed", 0)
        if test_command and not summary_text:
            if result.returncode == 0:
                passed = 1
            elif not errors:
                errors.append(f"Test command exited with status {result.returncode}")
        return {
            "passed": passed,
            "failed": failed or (1 if test_command and result.returncode != 0 and not summary_text else 0),
            "errors": errors,
            "output_excerpt": output[-4000:],
        }
    except subprocess.TimeoutExpired as exc:
        output = "\n".join(part.decode(errors="replace") if isinstance(part, bytes) else part or "" for part in (exc.stdout, exc.stderr)).strip()
        return {"passed": 0, "failed": 0, "errors": ["pytest timed out after 300 seconds"], "output_excerpt": output[-4000:]}
    except OSError as exc:
        return {"passed": 0, "failed": 0, "errors": [str(exc)], "output_excerpt": ""}
    except ValueError as exc:
        return {"passed": 0, "failed": 0, "errors": [f"Invalid test command: {exc}"], "output_excerpt": ""}

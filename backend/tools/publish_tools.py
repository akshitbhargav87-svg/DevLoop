"""Safely publish a verified DevLoop fix to a new GitHub branch.

Only the caller may invoke this after explicit patch approval and successful tests.
The function never updates the base branch and never persists a token in Git config.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlparse

from git import Actor, Repo
from git.exc import GitCommandError


def canonical_github_repo_url(value: str) -> str:
    """Return a credential-free HTTPS URL for a GitHub owner/repository URL."""
    raw = (value or "").strip()
    if not raw:
        raise ValueError("A GitHub repository URL is required.")

    if raw.startswith("git@github.com:"):
        path = raw.removeprefix("git@github.com:")
    else:
        parsed = urlparse(raw)
        if parsed.scheme not in {"https", "ssh"} or (parsed.hostname or "").lower() != "github.com":
            raise ValueError("GitHub publishing supports GitHub repository URLs only.")
        if parsed.password or (parsed.scheme == "https" and parsed.username):
            raise ValueError("Remove credentials from the repository URL; configure GITHUB_TOKEN separately.")
        if parsed.scheme == "ssh" and parsed.username not in {None, "git"}:
            raise ValueError("Unsupported SSH repository URL.")
        path = parsed.path.lstrip("/")

    path = path.split("?", 1)[0].split("#", 1)[0].strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.split("/")
    if len(parts) != 2 or any(
        not part or not re.fullmatch(r"[A-Za-z0-9_.-]+", part)
        for part in parts
    ):
        raise ValueError("Expected a GitHub URL in owner/repository format.")
    owner, repository = parts
    return f"https://github.com/{owner}/{repository}.git"


def make_fix_branch_name(run_id: str) -> str:
    """Create a deterministic, collision-resistant branch name for this run."""
    suffix = re.sub(r"[^a-z0-9-]+", "-", (run_id or "").lower()).strip("-")[:16]
    if not suffix:
        raise ValueError("A workflow run ID is required to name the fix branch.")
    return f"devloop/fix-{suffix}"


def normalise_publish_paths(paths: list[str]) -> list[str]:
    """Validate relative patch paths and reject common secret/config files."""
    if not isinstance(paths, list) or not paths:
        raise ValueError("The patch does not declare any files to publish.")

    result: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("The patch contains an invalid file path.")
        value = raw_path.strip().replace("\\", "/")
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or re.match(r"^[A-Za-z]:/", value)
            or any(part in {"", ".", "..", ".git"} for part in path.parts)
        ):
            raise ValueError(f"Refusing to publish an unsafe repository path: {raw_path!r}")

        name = path.name.lower()
        blocked = (
            name == ".env"
            or name.startswith(".env.")
            or name in {"credentials.json", "id_rsa", "id_ed25519", "known_hosts"}
            or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}
            or any(part.lower() == ".git" for part in path.parts)
        )
        if blocked:
            raise ValueError(f"Refusing to publish a likely secret or private file: {raw_path!r}")

        normalised = path.as_posix()
        if normalised not in seen:
            seen.add(normalised)
            result.append(normalised)
    return result


def publish_verified_fix(
    workspace: str,
    github_repository_url: str,
    base_branch: str,
    run_id: str,
    changed_files: list[str],
    github_token: str,
) -> dict[str, object]:
    """Commit only approved changed files, then push only a fresh DevLoop branch.

    The supplied workspace must be a fresh DevLoop clone with the approved patch
    already applied and its tests already passing. The token is passed through
    per-command Git environment configuration and is never written to .git/config.
    """
    token = (github_token or "").strip()
    if not token:
        raise ValueError("GitHub publishing requires GITHUB_TOKEN in backend/.env.")

    root = Path(workspace).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("The publishing workspace is not a directory.")

    expected_remote = canonical_github_repo_url(github_repository_url)
    repo = Repo(str(root), search_parent_directories=False)
    if repo.bare:
        raise ValueError("The publishing workspace is not a working tree.")
    if "origin" not in [remote.name for remote in repo.remotes]:
        raise ValueError("The cloned repository has no origin remote.")
    actual_remote = canonical_github_repo_url(repo.remotes.origin.url)
    if actual_remote.casefold() != expected_remote.casefold():
        raise ValueError("The cloned origin does not match the requested GitHub repository.")

    try:
        active_branch = repo.active_branch.name
    except TypeError as exc:
        raise ValueError("The publishing workspace is detached from a branch.") from exc
    if active_branch != (base_branch or "main"):
        raise ValueError(
            f"Expected to publish from base branch '{base_branch or 'main'}', "
            f"but the workspace is on '{active_branch}'."
        )

    # A fresh clone should have an empty index. Never mix in pre-staged changes.
    if list(repo.index.diff("HEAD")):
        raise ValueError("The workspace index already contains staged changes; refusing to publish.")

    allowed = normalise_publish_paths(changed_files)
    tracked_changes = {
        item for item in repo.git.diff("--name-only", "HEAD", "--").splitlines() if item
    }
    unexpected_changes = sorted(tracked_changes - set(allowed))
    if unexpected_changes:
        raise ValueError(
            "Unexpected tracked files changed outside the approved patch: "
            + ", ".join(unexpected_changes)
        )

    untracked = set(repo.untracked_files)
    changed_allowed = [
        path for path in allowed
        if path in tracked_changes or path in untracked
    ]
    if not changed_allowed:
        raise ValueError("No approved patch changes were found in the workspace.")

    for relative in changed_allowed:
        candidate = root / relative
        if candidate.is_symlink():
            raise ValueError(f"Refusing to publish a symlink: {relative}")
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Patch path escapes the repository: {relative}") from exc
        if not resolved.is_file():
            raise ValueError(f"Changed file is missing or is not a regular file: {relative}")

    branch_name = make_fix_branch_name(run_id)
    if branch_name == active_branch or branch_name in {"main", "master"}:
        raise ValueError("Refusing to publish a fix to the base branch.")
    if branch_name in [head.name for head in repo.heads]:
        raise ValueError(f"Local branch already exists: {branch_name}")

    # Branch first, then stage only the file list approved by the patch validator.
    branch = repo.create_head(branch_name, repo.head.commit)
    branch.checkout()
    repo.index.add(changed_allowed)
    staged = {
        item for item in repo.git.diff("--cached", "--name-only", "HEAD").splitlines() if item
    }
    if not staged or not staged.issubset(set(allowed)):
        raise ValueError("Staged files do not match the approved patch file list.")

    author = Actor("DevLoop", "devloop@users.noreply.github.com")
    commit = repo.index.commit(
        f"DevLoop: publish verified fix ({run_id[:12]})",
        author=author,
        committer=author,
    )

    # Use ephemeral Git config variables for auth and canonical HTTPS origin.
    # Neither the token nor the authenticated URL is persisted on disk.
    encoded_credentials = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
    extra_header = f"AUTHORIZATION: basic {encoded_credentials}"
    try:
        with repo.git.custom_environment(
            GIT_TERMINAL_PROMPT="0",
            GIT_CONFIG_COUNT="2",
            GIT_CONFIG_KEY_0="remote.origin.url",
            GIT_CONFIG_VALUE_0=expected_remote,
            GIT_CONFIG_KEY_1="http.https://github.com/.extraheader",
            GIT_CONFIG_VALUE_1=extra_header,
        ):
            repo.git.push("origin", f"HEAD:refs/heads/{branch_name}")
    except GitCommandError as exc:
        raise RuntimeError(
            "GitHub push failed. Check GITHUB_TOKEN has repository Contents read/write "
            "permission and that repository policy allows branch creation."
        ) from exc

    return {
        "status": "published",
        "branch": branch_name,
        "commit_sha": commit.hexsha,
        "branch_url": expected_remote.removesuffix(".git") + "/tree/" + quote(branch_name, safe="/"),
        "changed_files": sorted(staged),
        "base_branch": active_branch,
    }

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from backend.tools.git_tools import recent_commits, show_commit_diff
from backend.tools.repo_tools import (
    clone_repo,
    grep_symbol,
    hash_files,
    list_files,
    read_file,
    verify_source_checkout,
)
from backend.tools.test_tools import _sanitize_patch, apply_patch, run_pytest, validate_patch


ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo-target"


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "demo-target"
    shutil.copytree(DEMO, destination, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    return destination


def test_clone_repo(demo_copy: Path, tmp_path: Path) -> None:
    clone = Path(clone_repo(str(demo_copy), "main"))
    try:
        assert clone.is_dir()
        assert (clone / "store" / "pricing.py").is_file()
    finally:
        shutil.rmtree(clone, ignore_errors=True)


def test_list_read_and_grep(demo_copy: Path) -> None:
    files = list_files(demo_copy)
    assert "store/pricing.py" in files
    assert ".git/" not in files
    assert "return order_total * (coupon_rate or 1)" in read_file(demo_copy, "store/pricing.py")
    matches = grep_symbol(demo_copy, "calculate_discount")
    assert any(item["file"] == "store/pricing.py" and item["line"] == 1 for item in matches)


def test_read_file_rejects_traversal(demo_copy: Path) -> None:
    with pytest.raises(ValueError):
        read_file(demo_copy, "../outside.txt")


def test_verify_source_checkout_rejects_modified_patch_target(demo_copy: Path) -> None:
    from git import Repo

    repo = Repo(str(demo_copy), search_parent_directories=False)
    expected_hashes = hash_files(demo_copy, ["store/pricing.py"])
    assert verify_source_checkout(
        demo_copy,
        repo.active_branch.name,
        repo.head.commit.hexsha,
        expected_hashes,
    ) == str(demo_copy.resolve())

    pricing_file = demo_copy / "store" / "pricing.py"
    pricing_file.write_text(
        pricing_file.read_text(encoding="utf-8") + "\n# local edit\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="Patch target files changed"):
        verify_source_checkout(
            demo_copy,
            repo.active_branch.name,
            repo.head.commit.hexsha,
            expected_hashes,
        )


def test_hash_files_normalizes_windows_line_endings(demo_copy: Path) -> None:
    pricing_file = demo_copy / "store" / "pricing.py"
    original = pricing_file.read_bytes()
    expected_hash = hash_files(demo_copy, ["store/pricing.py"])
    pricing_file.write_bytes(original.replace(b"\n", b"\r\n"))

    assert hash_files(demo_copy, ["store/pricing.py"]) == expected_hash


def test_git_history_and_diff(demo_copy: Path) -> None:
    commits = recent_commits(demo_copy, 3)
    assert len(commits) == 3
    assert commits[0]["message"] == "test: add checkout tests"
    diff = show_commit_diff(demo_copy, commits[1]["sha"])
    assert "pricing.py" in diff
    assert "calculate_discount" in diff


def test_patch_and_pytest(demo_copy: Path) -> None:
    baseline = run_pytest(demo_copy)
    assert baseline["passed"] == 1
    assert baseline["failed"] == 1
    patch = '''diff --git a/store/pricing.py b/store/pricing.py
--- a/store/pricing.py
+++ b/store/pricing.py
@@ -1,3 +1,3 @@
 def calculate_discount(order_total: float, coupon_rate: float) -> float:
     \"\"\"Return the discounted price for an order.\"\"\"
-    return order_total * (coupon_rate or 1)
+    return order_total * (1 - coupon_rate)
'''
    assert apply_patch(demo_copy, patch)
    result = run_pytest(demo_copy)
    assert result["passed"] == 2
    assert result["failed"] == 0
    assert result["errors"] == []


def test_custom_pytest_command_uses_current_interpreter(demo_copy: Path) -> None:
    result = run_pytest(demo_copy, "pytest -q")

    assert result["passed"] == 1
    assert result["failed"] == 1
    assert result["errors"] == []
    assert "Expected 75.0, got 25.0" in result["output_excerpt"]
    assert "WinError 2" not in result["output_excerpt"]


def test_validate_patch_rejects_candidate_that_does_not_fix_tests(demo_copy: Path) -> None:
    patch = """diff --git a/store/pricing.py b/store/pricing.py
--- a/store/pricing.py
+++ b/store/pricing.py
@@ -1,3 +1,5 @@
 def calculate_discount(order_total: float, coupon_rate: float) -> float:
     \"\"\"Return the discounted price for an order.\"\"\"
+    if coupon_rate is None:
+        return order_total
     return order_total * (coupon_rate or 1)
"""

    result = validate_patch(demo_copy, patch)

    assert result["passed"] is False
    assert result["test_results"]["failed"] == 1
    assert "Expected 75.0, got 25.0" in result["test_results"]["output_excerpt"]
    assert "return order_total * (coupon_rate or 1)" in (
        demo_copy / "store" / "pricing.py"
    ).read_text(encoding="utf-8")


def test_validate_patch_accepts_candidate_that_fixes_tests(demo_copy: Path) -> None:
    patch = """diff --git a/store/pricing.py b/store/pricing.py
--- a/store/pricing.py
+++ b/store/pricing.py
@@ -1,3 +1,3 @@
 def calculate_discount(order_total: float, coupon_rate: float) -> float:
     \"\"\"Return the discounted price for an order.\"\"\"
-    return order_total * (coupon_rate or 1)
+    return order_total * (1 - coupon_rate)
"""

    result = validate_patch(demo_copy, patch)

    assert result["passed"] is True
    assert result["test_results"]["passed"] == 2


def test_invalid_patch_returns_false(demo_copy: Path) -> None:
    assert apply_patch(demo_copy, "not a unified diff") is False


def test_patch_to_nonexistent_file_is_rejected(demo_copy: Path) -> None:
    patch = """diff --git a/app/coupon/discount.py b/app/coupon/discount.py
--- a/app/coupon/discount.py
+++ b/app/coupon/discount.py
@@ -1 +1 @@
-old
+new
"""
    assert apply_patch(demo_copy, patch) is False
    result = validate_patch(demo_copy, patch)
    assert result["passed"] is False
    assert "existing workspace files" in result["error"]


def test_patch_targets_must_match_declared_files(demo_copy: Path) -> None:
    result = validate_patch(demo_copy, _GOOD_HUNK, expected_files=["path/to/file.py"])
    assert result["passed"] is False


# ---------------------------------------------------------------------------
# Patch sanitisation â€” these reproduce the exact [WinError 2] / "corrupt
# patch" failure that occurs when the LLM includes fabricated index lines or
# markdown fences in its diff output.
# ---------------------------------------------------------------------------

_GOOD_HUNK = """\
diff --git a/store/pricing.py b/store/pricing.py
--- a/store/pricing.py
+++ b/store/pricing.py
@@ -1,3 +1,3 @@
 def calculate_discount(order_total: float, coupon_rate: float) -> float:
     \"\"\"Return the discounted price for an order.\"\"\"
-    return order_total * (coupon_rate or 1)
+    return order_total * (1 - coupon_rate)
"""


def test_apply_patch_repairs_malformed_hunk_header(demo_copy: Path) -> None:
    """Fix Qwen hunk counts from the actual body before git validates the diff."""
    pricing_file = demo_copy / "store" / "pricing.py"
    original_lines = pricing_file.read_text(encoding="utf-8").splitlines()
    pricing_file.write_text(
        "\n".join([*original_lines[:2], "", *original_lines[2:]]) + "\n",
        encoding="utf-8",
    )

    patch = "\n".join(
        [
            "diff --git a/store/pricing.py b/store/pricing.py",
            "--- a/store/pricing.py",
            "+++ b/store/pricing.py",
            "@@ -1,5 +1,5 @@",
            " def calculate_discount(order_total: float, coupon_rate: float) -> float:",
            '     """Return the discounted price for an order."""',
            " ",
            "-    return order_total * (coupon_rate or 1)",
            "+    return order_total * (1 - coupon_rate)",
            "",
        ]
    )

    assert apply_patch(demo_copy, patch) is True
    assert "return order_total * (1 - coupon_rate)" in pricing_file.read_text(
        encoding="utf-8"
    )


def test_apply_patch_restores_missing_marker_for_exact_context_line(demo_copy: Path) -> None:
    patch = '''diff --git a/store/pricing.py b/store/pricing.py
--- a/store/pricing.py
+++ b/store/pricing.py
@@ -1,3 +1,3 @@
 def calculate_discount(order_total: float, coupon_rate: float) -> float:
    """Return the discounted price for an order."""
-    return order_total * (coupon_rate or 1)
+    return order_total * (1 - coupon_rate)
'''

    assert apply_patch(demo_copy, patch) is True
    assert "return order_total * (1 - coupon_rate)" in (
        demo_copy / "store" / "pricing.py"
    ).read_text(encoding="utf-8")


def test_apply_patch_accepts_fake_index_line(demo_copy: Path) -> None:
    """Fake 'index' lines generated by LLMs must not prevent git apply.

    Root cause of the original [WinError 2] / success:false failure:
    git-apply exits 128 with 'corrupt patch' when it encounters an
    ``index`` line whose SHA values are placeholders (e.g. 1234567).
    _sanitize_patch strips those lines so the diff applies cleanly.
    """
    patch_with_fake_index = (
        "diff --git a/store/pricing.py b/store/pricing.py\n"
        "index 1234567..89abcdef 100644\n"
    ) + _GOOD_HUNK[_GOOD_HUNK.index("--- a/"):]

    assert apply_patch(demo_copy, patch_with_fake_index) is True
    content = (demo_copy / "store" / "pricing.py").read_text(encoding="utf-8")
    assert "1 - coupon_rate" in content


def test_apply_patch_accepts_markdown_fenced_diff(demo_copy: Path) -> None:
    """Markdown code fences around the diff must be stripped before git apply."""
    fenced = "```diff\n" + _GOOD_HUNK + "```\n"
    assert apply_patch(demo_copy, fenced) is True
    content = (demo_copy / "store" / "pricing.py").read_text(encoding="utf-8")
    assert "1 - coupon_rate" in content


def test_apply_patch_accepts_old_new_mode_lines(demo_copy: Path) -> None:
    """Spurious 'old mode'/'new mode' lines from LLMs must be stripped."""
    patch_with_mode = (
        "diff --git a/store/pricing.py b/store/pricing.py\n"
        "old mode 100644\n"
        "new mode 100755\n"
    ) + _GOOD_HUNK[_GOOD_HUNK.index("--- a/"):]

    assert apply_patch(demo_copy, patch_with_mode) is True
    content = (demo_copy / "store" / "pricing.py").read_text(encoding="utf-8")
    assert "1 - coupon_rate" in content


def test_sanitize_patch_strips_all_artefacts() -> None:
    """Unit-test _sanitize_patch in isolation."""
    raw = (
        "```diff\n"
        "diff --git a/foo.py b/foo.py\n"
        "index abc123..def456 100644\n"
        "old mode 100644\n"
        "new mode 100755\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "```\n"
    )
    cleaned = _sanitize_patch(raw)
    assert "```" not in cleaned
    assert "index " not in cleaned
    assert "old mode" not in cleaned
    assert "new mode" not in cleaned
    assert "--- a/foo.py" in cleaned
    assert "+new" in cleaned

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from backend.tools.git_tools import recent_commits, show_commit_diff
from backend.tools.repo_tools import clone_repo, grep_symbol, list_files, read_file
from backend.tools.test_tools import apply_patch, run_pytest


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
    patch = """diff --git a/store/pricing.py b/store/pricing.py
--- a/store/pricing.py
+++ b/store/pricing.py
@@ -1,3 +1,3 @@
 def calculate_discount(order_total: float, coupon_rate: float) -> float:
     \"\"\"Return the discounted price for an order.\"\"\"
-    return order_total * (coupon_rate or 1)
+    return order_total * (1 - coupon_rate)
"""
    assert apply_patch(demo_copy, patch)
    result = run_pytest(demo_copy)
    assert result["passed"] == 2
    assert result["failed"] == 0
    assert result["errors"] == []


def test_invalid_patch_returns_false(demo_copy: Path) -> None:
    assert apply_patch(demo_copy, "not a unified diff") is False

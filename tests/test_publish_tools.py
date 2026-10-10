import pytest

from backend.tools.publish_tools import (
    canonical_github_repo_url,
    make_fix_branch_name,
    normalise_publish_paths,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/octo/sample", "https://github.com/octo/sample.git"),
        ("https://github.com/octo/sample.git", "https://github.com/octo/sample.git"),
        ("git@github.com:octo/sample.git", "https://github.com/octo/sample.git"),
        ("ssh://git@github.com/octo/sample.git", "https://github.com/octo/sample.git"),
    ],
)
def test_canonical_github_repo_url(url, expected):
    assert canonical_github_repo_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/octo/sample.git",
        "https://user:secret@github.com/octo/sample.git",
        "https://github.com/octo",
        "https://github.com/octo/sample/tree/main",
    ],
)
def test_rejects_unsupported_repository_urls(url):
    with pytest.raises(ValueError):
        canonical_github_repo_url(url)


def test_fix_branch_name_is_always_namespaced_and_safe():
    name = make_fix_branch_name("A19LI5_01-XYZ")
    assert name == "devloop/fix-a19li5-01-xyz"
    assert name != "main"


def test_normalise_publish_paths_deduplicates_and_normalises_separators():
    assert normalise_publish_paths(["store/pricing.py", "store\\pricing.py"]) == ["store/pricing.py"]


@pytest.mark.parametrize(
    "path",
    [
        "../outside.py",
        "/absolute/path.py",
        "C:\\Users\\me\\secret.py",
        ".env",
        ".env.production",
        "keys/private.pem",
        ".git/config",
    ],
)
def test_rejects_unsafe_or_secret_paths(path):
    with pytest.raises(ValueError):
        normalise_publish_paths([path])

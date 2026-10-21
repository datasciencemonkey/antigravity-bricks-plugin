import re
import subprocess
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_pyproject():
    with open(REPO_ROOT / "pyproject.toml", "rb") as handle:
        return tomllib.load(handle)


def test_project_requires_python_311_or_newer():
    assert load_pyproject()["project"]["requires-python"] == ">=3.11"


def test_pytest_is_configured_to_collect_only_the_tests_directory():
    assert load_pyproject()["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]


def test_dev_dependency_group_declares_pytest_and_pyyaml():
    requirements = load_pyproject()["dependency-groups"]["dev"]
    names = {re.match(r"[A-Za-z0-9_.-]+", requirement).group(0).lower() for requirement in requirements}
    assert {"pytest", "pyyaml"} <= names


def test_uv_lock_is_visible_to_git_so_it_can_be_committed():
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "uv.lock"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert listed == ["uv.lock"]


def test_uv_lock_pins_pytest_and_pyyaml():
    lock = (REPO_ROOT / "uv.lock").read_text()
    assert 'name = "pytest"' in lock
    assert 'name = "pyyaml"' in lock


def test_default_package_index_is_pinned_to_public_pypi():
    """Otherwise uv writes whatever index the machine is configured with (for example an internal proxy) into uv.lock."""
    indexes = load_pyproject()["tool"]["uv"]["index"]

    assert [index["url"] for index in indexes if index.get("default")] == ["https://pypi.org/simple"]


def test_uv_lock_only_points_at_public_pypi():
    lock = (REPO_ROOT / "uv.lock").read_text()
    registries = set(re.findall(r'registry = "([^"]+)"', lock))
    file_urls = re.findall(r'url = "([^"]+)"', lock)

    assert registries == {"https://pypi.org/simple"}
    assert file_urls and all(url.startswith("https://files.pythonhosted.org/packages/") for url in file_urls)


def test_python_bytecode_caches_are_git_ignored():
    check = subprocess.run(["git", "check-ignore", "-q", "tools/__pycache__/repo_checks.cpython-312.pyc"], cwd=REPO_ROOT)

    assert check.returncode == 0  # 0 means ignored, 1 means not ignored

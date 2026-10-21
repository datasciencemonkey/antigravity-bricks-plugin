"""Git exports GIT_DIR and friends to hooks, aliases and `rebase --exec`; the suite must not let them aim its git at a real repo."""
import os
import subprocess
import sys
from pathlib import Path

from conftest import TempRepo

TESTS = Path(__file__).resolve().parent


def refs(repo):
    return repo.git("for-each-ref", "--format=%(refname) %(objectname)")


def test_an_inherited_git_dir_cannot_point_the_suites_temporary_repos_at_another_repo(tmp_path):
    sentinel = TempRepo(tmp_path / "sentinel")
    sentinel.write("keep.txt", "untouched\n")
    sentinel.commit("Sentinel commit")
    before = refs(sentinel)
    git_dir = sentinel.path / ".git"

    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(TESTS / "test_git_history.py")],
        cwd=TESTS.parent, capture_output=True, text=True,
        env={**os.environ, "GIT_DIR": str(git_dir), "GIT_INDEX_FILE": str(git_dir / "index")},
    )

    assert refs(sentinel) == before
    assert sentinel.git("config", "--get", "core.bare") == "false"

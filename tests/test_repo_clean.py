"""The push gate's real job: the repo as it stands must be free of leaks, wrong identities and missing ignores."""
import warnings
from pathlib import Path

import pytest

from tools import repo_checks

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def local_denylist():
    """This machine's deny-list of organization-specific patterns. CI has none: those checks are then skipped, with a
    warning that pytest shows in its summary."""
    return repo_checks.load_denylist(REPO_ROOT, warn=warnings.warn)


def report(findings):
    return "repo checks found problems:\n" + "\n".join(f"  {finding}" for finding in findings)


def test_the_files_and_gitignore_of_the_current_repo_are_clean(local_denylist):
    gitignore = (REPO_ROOT / ".gitignore").read_text()

    problems = repo_checks.scan_files(REPO_ROOT, denylist=local_denylist)
    problems += repo_checks.find_missing_gitignore_entries(gitignore)

    assert problems == [], report(problems)


def test_every_commit_from_origin_main_to_head_has_the_right_identity_and_a_clean_message(local_denylist):
    commits = repo_checks.read_commits(REPO_ROOT)

    problems = repo_checks.find_bad_commit_identities(commits)
    problems += repo_checks.scan_commit_messages(commits, denylist=local_denylist)

    assert problems == [], report(problems)

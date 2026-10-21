"""The push gate's real job: the repo as it stands must be free of leaks, wrong identities and missing ignores."""
from pathlib import Path

from tools import repo_checks

REPO_ROOT = Path(__file__).resolve().parent.parent


def report(findings):
    return "repo checks found problems:\n" + "\n".join(f"  {finding}" for finding in findings)


def test_the_files_and_gitignore_of_the_current_repo_are_clean():
    gitignore = (REPO_ROOT / ".gitignore").read_text()

    problems = repo_checks.scan_files(REPO_ROOT) + repo_checks.find_missing_gitignore_entries(gitignore)

    assert problems == [], report(problems)


def test_every_commit_from_origin_main_to_head_has_the_right_identity_and_a_clean_message():
    commits = repo_checks.read_commits(REPO_ROOT)

    problems = repo_checks.find_bad_commit_identities(commits) + repo_checks.scan_commit_messages(commits)

    assert problems == [], report(problems)

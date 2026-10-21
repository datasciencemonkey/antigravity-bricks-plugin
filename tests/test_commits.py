import pytest

from tools import repo_checks

GOOD = "datasciencemonkey@gmail.com"
OTHER = "someone" + "@example.com"
SLACK = "databricks" + ".slack.com"  # built at runtime so this file passes the repo scan


def commit(author=GOOD, committer=GOOD, message="Do a thing", sha="abc1234" + "0" * 33):
    return repo_checks.Commit(
        sha=sha,
        author_name="Some Author",
        author_email=author,
        committer_name="Some Committer",
        committer_email=committer,
        message=message,
    )


def test_flags_a_commit_authored_with_another_email():
    findings = repo_checks.find_bad_commit_identities([commit(author=OTHER)])

    assert [finding.where for finding in findings] == ["commit abc1234"]
    assert "author" in findings[0].what
    assert OTHER in findings[0].what


def test_flags_a_commit_committed_with_another_email():
    findings = repo_checks.find_bad_commit_identities([commit(committer=OTHER)])

    assert [finding.where for finding in findings] == ["commit abc1234"]
    assert "committer" in findings[0].what
    assert OTHER in findings[0].what


def test_accepts_a_commit_with_the_right_author_and_committer():
    assert repo_checks.find_bad_commit_identities([commit()]) == []


def test_ignores_co_authored_by_trailers():
    message = "Do a thing\n\nCo-authored-by: Isaac <no-reply@databricks.com>\nCo-authored-by: Pat <pat@example.com>\n"

    assert repo_checks.find_bad_commit_identities([commit(message=message)]) == []


def test_checks_every_commit_and_reports_each_wrong_role():
    commits = [commit(author=OTHER, committer=OTHER, sha="1" * 40), commit(sha="2" * 40), commit(committer=OTHER, sha="3" * 40)]

    findings = repo_checks.find_bad_commit_identities(commits)

    assert [finding.where for finding in findings] == ["commit 1111111", "commit 1111111", "commit 3333333"]


def test_flags_internal_references_in_commit_messages():
    message = f"Fix docs\n\nSee https://{SLACK}/archives/C1\n"

    findings = repo_checks.scan_commit_messages([commit(message=message)])

    assert [finding.where for finding in findings] == ["commit abc1234 message:3"]


def test_flags_secrets_in_commit_messages_without_repeating_them():
    token = "dapi" + "0123456789abcdef" * 2
    message = f"Add config\n\nTemporary token: {token}\n"

    findings = repo_checks.scan_commit_messages([commit(message=message)])

    assert [finding.where for finding in findings] == ["commit abc1234 message:3"]
    assert token not in str(findings)

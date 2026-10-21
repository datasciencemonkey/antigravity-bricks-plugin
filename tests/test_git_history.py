import pytest
from conftest import GOOD_EMAIL
from tools import repo_checks


def test_reads_the_commits_between_origin_main_and_head_newest_first(repo):
    first = repo.commit("Add notes\n\nCo-authored-by: Isaac <no-reply@databricks.com>", committer_email="ci@example.com")
    second = repo.commit("Second change")

    commits = repo_checks.read_commits(repo.path)

    assert [commit.sha for commit in commits] == [second, first]
    older = commits[1]
    assert (older.author_name, older.author_email) == ("Author Name", GOOD_EMAIL)
    assert (older.committer_name, older.committer_email) == ("Committer Name", "ci@example.com")
    assert older.message.startswith("Add notes\n\nCo-authored-by: Isaac")


def test_the_range_is_empty_when_head_is_origin_main_itself(repo):
    assert repo_checks.read_commits(repo.path) == []


def test_a_missing_origin_main_is_reported_clearly_instead_of_scanning_nothing(repo):
    repo.git("update-ref", "-d", "refs/remotes/origin/main")

    with pytest.raises(RuntimeError, match="origin/main"):
        repo_checks.read_commits(repo.path)

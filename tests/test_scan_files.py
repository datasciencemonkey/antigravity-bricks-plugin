import pytest

from tools import repo_checks

# Fixtures are assembled at runtime so this file passes the repo-wide scan itself.
PAT = "dapi" + "0123456789abcdef" * 2


def test_flags_a_secret_in_an_untracked_file_with_its_location(repo):
    repo.write("notes/leak.txt", f"fine\ntoken={PAT}\n")

    findings = repo_checks.scan_files(repo.path)

    assert [finding.where for finding in findings] == ["notes/leak.txt:2"]


def test_also_flags_a_secret_in_a_committed_file(repo):
    repo.write("committed.txt", f"token={PAT}\n")
    repo.commit("Add a leak")

    findings = repo_checks.scan_files(repo.path)

    assert [finding.where for finding in findings] == ["committed.txt:1"]


@pytest.mark.parametrize(
    "line",
    [
        "see https://" + "databricks" + ".slack.com/archives/C1",
        "host: https://dbc-1a2b3c4d-e5f6" + ".cloud.databricks.com",
    ],
    ids=["internal reference", "workspace hostname"],
)
def test_files_get_the_internal_reference_and_hostname_checks_too(repo, line):
    repo.write("docs/page.md", f"intro\n{line}\n")

    findings = repo_checks.scan_files(repo.path)

    assert [finding.where for finding in findings] == ["docs/page.md:2"]


def test_skips_binary_files(repo):
    repo.write("image.bin", b"\x00\x01binary-ish " + PAT.encode() + b"\n")

    assert repo_checks.scan_files(repo.path) == []


def test_still_scans_text_files_that_are_not_valid_utf8(repo):
    repo.write("legacy.txt", b"caf\xe9\ntoken=" + PAT.encode() + b"\n")

    findings = repo_checks.scan_files(repo.path)

    assert [finding.where for finding in findings] == ["legacy.txt:2"]


def test_skips_tracked_files_that_were_deleted_from_the_working_tree(repo):
    repo.write("gone.txt", "temporary\n")
    repo.commit("Add gone.txt")
    (repo.path / "gone.txt").unlink()

    assert repo_checks.scan_files(repo.path) == []


def test_handles_file_names_with_spaces_and_non_ascii_characters(repo):
    name = "naïve notes/café page.md"
    repo.write(name, f"token={PAT}\n")

    findings = repo_checks.scan_files(repo.path)

    assert [finding.where for finding in findings] == [f"{name}:1"]


def test_skips_files_ignored_by_gitignore(repo):
    repo.write(".gitignore", "private/\n")
    repo.write("private/notes.txt", f"token={PAT}\n")

    assert repo_checks.scan_files(repo.path) == []

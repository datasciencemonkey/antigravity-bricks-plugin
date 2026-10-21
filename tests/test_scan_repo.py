from tools import repo_checks

# Fixtures are assembled at runtime so this file passes the repo-wide scan itself.
PAT = "dapi" + "0123456789abcdef" * 2


def test_a_clean_repo_has_no_findings(repo):
    assert repo_checks.scan_repo(repo.path) == []


def test_reports_findings_from_files(repo):
    repo.write("leak.txt", f"token={PAT}\n")

    findings = repo_checks.scan_repo(repo.path)

    assert [finding.where for finding in findings] == ["leak.txt:1"]


def test_reports_required_gitignore_entries_that_are_missing(repo):
    repo.write(".gitignore", ".omc/\n.isaac/\n.build/\n")

    findings = repo_checks.scan_repo(repo.path)

    assert [(finding.check, finding.where) for finding in findings] == [("gitignore", ".gitignore")]
    assert "Antigravity Plugin Marketplace Publishing Guide.pdf" in findings[0].what


def test_treats_a_missing_gitignore_file_as_missing_every_entry(repo):
    (repo.path / ".gitignore").unlink()

    findings = repo_checks.scan_repo(repo.path)

    assert [finding.check for finding in findings] == ["gitignore"] * 4


def test_reports_commits_with_a_wrong_identity(repo):
    sha = repo.commit("Change something", author_email="someone" + "@example.com")

    findings = repo_checks.scan_repo(repo.path)

    assert [(finding.check, finding.where) for finding in findings] == [("identity", f"commit {sha[:7]}")]


def test_reports_leaks_in_commit_messages(repo):
    sha = repo.commit(f"Add config\n\nTemporary token: {PAT}\n")

    findings = repo_checks.scan_repo(repo.path)

    assert [(finding.check, finding.where) for finding in findings] == [("secret", f"commit {sha[:7]} message:3")]


def test_a_missing_origin_main_becomes_a_finding_and_the_file_checks_still_run(repo):
    repo.git("update-ref", "-d", "refs/remotes/origin/main")
    repo.write("leak.txt", f"token={PAT}\n")

    findings = repo_checks.scan_repo(repo.path)

    assert [finding.check for finding in findings] == ["secret", "commit-range"]
    assert "origin/main" in findings[1].what

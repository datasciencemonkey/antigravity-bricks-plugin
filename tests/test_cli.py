import io
import os
import subprocess
import sys
from pathlib import Path

from tools import repo_checks

# Fixtures are assembled at runtime so this file passes the repo-wide scan itself.
PAT = "dapi" + "0123456789abcdef" * 2
SLACK = "databricks" + ".slack.com"
AWS_HOST = "dbc-1a2b3c4d-e5f6" + ".cloud.databricks.com"


def test_exits_zero_and_says_so_on_a_clean_repo(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo.path)

    assert repo_checks.main([]) == 0
    assert "no findings" in capsys.readouterr().out


def test_exits_non_zero_with_a_readable_report_when_a_file_leaks(repo, monkeypatch, capsys):
    repo.write("leak.txt", f"fine\ntoken={PAT}\n")
    monkeypatch.chdir(repo.path)

    status = repo_checks.main([])

    report = capsys.readouterr().out
    assert status == 1
    assert "leak.txt:2: [secret] Databricks personal access token: dapi..." in report
    assert "1 finding" in report
    assert PAT not in report


def test_scans_the_whole_repo_even_when_started_from_a_subdirectory(repo, monkeypatch, capsys):
    repo.write("leak.txt", f"token={PAT}\n")
    repo.write("docs/readme.md", "fine\n")
    monkeypatch.chdir(repo.path / "docs")

    status = repo_checks.main([])

    assert status == 1
    assert "leak.txt:1" in capsys.readouterr().out


def test_stdin_mode_exits_zero_for_clean_text_and_needs_no_git_repo(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # not a git repo: stdin mode must not touch git
    monkeypatch.setattr("sys.stdin", io.StringIO("A perfectly ordinary pull request description.\n"))

    assert repo_checks.main(["--stdin"]) == 0
    assert "no findings" in capsys.readouterr().out


def test_stdin_mode_exits_non_zero_and_reports_each_finding_with_its_line(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(f"Summary\nSee https://{SLACK}/archives/C1 and https://{AWS_HOST}\n"))

    status = repo_checks.main(["--stdin"])

    report = capsys.readouterr().out
    assert status == 1
    assert "<stdin>:2: [internal-reference]" in report
    assert "<stdin>:2: [hostname]" in report


def run_module(cwd, *args, stdin_text=None):
    """Run `python -m tools.repo_checks` the way `uv run python -m tools.repo_checks` does."""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)}
    return subprocess.run(
        [sys.executable, "-m", "tools.repo_checks", *args],
        cwd=cwd, env=env, input=stdin_text, capture_output=True, text=True,
    )


def test_module_entry_point_scans_the_repo_and_fails_on_a_leak(repo):
    repo.write("leak.txt", f"token={PAT}\n")

    result = run_module(repo.path)

    assert result.returncode == 1
    assert "leak.txt:1" in result.stdout


def test_module_entry_point_checks_piped_text_with_stdin(tmp_path):
    clean = run_module(tmp_path, "--stdin", stdin_text="nothing to see here\n")
    leaky = run_module(tmp_path, "--stdin", stdin_text=f"see https://{SLACK}/archives/C1\n")

    assert clean.returncode == 0
    assert leaky.returncode == 1
    assert "<stdin>:1" in leaky.stdout


def test_a_commit_with_a_wrong_email_fails_the_run(repo, monkeypatch, capsys):
    sha = repo.commit("Change something", committer_email="someone" + "@example.com")
    monkeypatch.chdir(repo.path)

    status = repo_checks.main([])

    assert status == 1
    assert f"commit {sha[:7]}: [identity] committer email" in capsys.readouterr().out


def test_a_correctly_attributed_commit_with_a_co_author_trailer_passes(repo, monkeypatch, capsys):
    repo.commit("Change something\n\nCo-authored-by: Isaac <no-reply@databricks.com>")
    monkeypatch.chdir(repo.path)

    assert repo_checks.main([]) == 0
    assert "no findings" in capsys.readouterr().out

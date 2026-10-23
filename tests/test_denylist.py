"""The local deny-list: patterns that name this organization's own systems, kept out of the public repo."""
import io
from pathlib import Path

import pytest
from conftest import FAKE_HOST, FAKE_TRACKER

from tools import repo_checks

LIST_ENV = "REPO_CHECKS_DENYLIST"


# --- reading a list ---------------------------------------------------------------------------------------------------


def test_reads_one_rule_per_pattern_line_and_skips_comments_and_blank_lines(tmp_path):
    path = tmp_path / "denylist.txt"
    path.write_text(
        "# a comment\n"
        "\n"
        r"first\.example" "\n"
        "   \n"
        "  # an indented comment\n"
        "second-[0-9]+\n"
    )

    rules = repo_checks.read_denylist(path)

    assert [pattern.pattern for _, pattern in rules] == [r"first\.example", "second-[0-9]+"]


def test_tolerates_windows_line_endings_a_byte_order_mark_and_spaces_around_a_pattern(tmp_path):
    path = tmp_path / "denylist.txt"
    path.write_bytes(b"\xef\xbb\xbf  (?i)first\\.example  \r\n# note\r\n")

    rules = repo_checks.read_denylist(path)

    assert [pattern.pattern for _, pattern in rules] == [r"(?i)first\.example"]


@pytest.mark.parametrize("name", ["missing.txt", "."], ids=["no such file", "a directory"])
def test_a_list_that_is_not_there_reads_as_none(tmp_path, name):
    assert repo_checks.read_denylist(tmp_path / name) is None


def test_an_invalid_pattern_is_an_error_that_names_its_line(tmp_path):
    path = tmp_path / "denylist.txt"
    path.write_text("# header\n\nfine\n(unclosed\n")

    with pytest.raises(repo_checks.DenylistError, match=r"denylist\.txt:4:"):
        repo_checks.read_denylist(path)


def test_patterns_are_case_sensitive_unless_they_start_with_the_ignore_case_flag(tmp_path):
    path = tmp_path / "denylist.txt"
    path.write_text("(?i)loud-host\\.example\nQuiet-Name\n")
    rules = repo_checks.read_denylist(path)

    findings = repo_checks.find_internal_references("LOUD-HOST.EXAMPLE quiet-name Quiet-Name", denylist=rules)

    assert [finding.what.split(": ", 1)[1] for finding in findings] == ["LOUD-HOST.EXAMPLE", "Quiet-Name"]


# --- finding the list -------------------------------------------------------------------------------------------------


def test_the_variable_names_the_list_when_it_is_set():
    assert repo_checks.denylist_path("/repo", {LIST_ENV: "/elsewhere/list.txt"}) == Path("/elsewhere/list.txt")


def test_the_variable_may_start_with_a_tilde():
    assert repo_checks.denylist_path("/repo", {LIST_ENV: "~/list.txt"}) == Path.home() / "list.txt"


@pytest.mark.parametrize("environ", [{}, {LIST_ENV: ""}], ids=["unset", "empty"])
def test_without_the_variable_the_list_is_omc_denylist_in_the_repo_root(environ):
    assert repo_checks.denylist_path("/repo", environ) == Path("/repo/.omc/denylist.txt")


def test_loading_a_list_that_exists_gives_its_rules_and_no_warning(tmp_path, fake_denylist_file):
    warned = []

    rules = repo_checks.load_denylist(tmp_path, {LIST_ENV: str(fake_denylist_file)}, warn=warned.append)

    assert len(rules) == 3
    assert warned == []


def test_loading_without_a_list_gives_no_rules_and_one_warning_that_says_how_to_add_one(tmp_path):
    warned = []

    rules = repo_checks.load_denylist(tmp_path, {}, warn=warned.append)

    assert rules == []
    (message,) = warned
    assert str(tmp_path / ".omc" / "denylist.txt") in message
    assert LIST_ENV in message
    assert "off" in message


def test_the_warning_goes_to_stderr_by_default(tmp_path, capsys):
    repo_checks.load_denylist(tmp_path, {})

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no deny-list" in captured.err


# --- the command line -------------------------------------------------------------------------------------------------


def test_a_run_without_a_list_passes_and_warns_once_on_stderr(repo, monkeypatch, capsys, tmp_path):
    missing = tmp_path / "no-such-list.txt"
    monkeypatch.setenv(LIST_ENV, str(missing))
    monkeypatch.chdir(repo.path)

    assert repo_checks.main([]) == 0

    captured = capsys.readouterr()
    assert "no findings" in captured.out
    assert captured.err.lower().count("warning") == 1
    assert str(missing) in captured.err


def test_a_run_with_a_list_prints_no_warning(repo, monkeypatch, capsys, fake_denylist_file):
    monkeypatch.setenv(LIST_ENV, str(fake_denylist_file))
    monkeypatch.chdir(repo.path)

    assert repo_checks.main([]) == 0

    assert capsys.readouterr().err == ""


def test_repo_files_get_the_list(repo, monkeypatch, capsys, fake_denylist_file):
    monkeypatch.setenv(LIST_ENV, str(fake_denylist_file))
    repo.write("docs/page.md", f"intro\nsee https://{FAKE_HOST}/archives/C1\n")
    monkeypatch.chdir(repo.path)

    status = repo_checks.main([])

    report = capsys.readouterr().out
    assert status == 1
    assert "docs/page.md:2: [internal-reference]" in report
    assert FAKE_HOST in report
    assert "1 finding" in report


def test_commit_messages_get_the_list(repo, monkeypatch, capsys, fake_denylist_file):
    monkeypatch.setenv(LIST_ENV, str(fake_denylist_file))
    sha = repo.commit(f"Add notes\n\nSee https://{FAKE_HOST}/page\n")
    monkeypatch.chdir(repo.path)

    status = repo_checks.main([])

    assert status == 1
    assert f"commit {sha[:7]} message:3: [internal-reference]" in capsys.readouterr().out


def test_stdin_text_gets_the_list(tmp_path, monkeypatch, capsys, fake_denylist_file):
    monkeypatch.setenv(LIST_ENV, str(fake_denylist_file))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(f"Summary\nSee https://{FAKE_TRACKER}/browse/EXAMPLE-12\n"))

    status = repo_checks.main(["--stdin"])

    report = capsys.readouterr().out
    assert status == 1
    assert report.count("<stdin>:2: [internal-reference]") == 2  # the host and the ticket key
    assert FAKE_TRACKER in report
    assert "EXAMPLE-12" in report


def test_stdin_mode_without_a_list_passes_and_warns(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(LIST_ENV, str(tmp_path / "no-such-list.txt"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO("An ordinary pull request description.\n"))

    assert repo_checks.main(["--stdin"]) == 0

    captured = capsys.readouterr()
    assert "no findings" in captured.out
    assert captured.err.lower().count("warning") == 1


def test_without_the_variable_a_run_reads_omc_denylist_at_the_repo_root_even_from_a_subdirectory(
    repo, monkeypatch, capsys
):
    monkeypatch.delenv(LIST_ENV, raising=False)
    repo.write(".omc/denylist.txt", "(?i)example-internal\\.invalid\n")  # .omc/ is git-ignored, so it isn't scanned
    repo.write("docs/page.md", f"see https://{FAKE_HOST}\n")
    monkeypatch.chdir(repo.path / "docs")

    status = repo_checks.main([])

    captured = capsys.readouterr()
    assert status == 1
    assert "docs/page.md:1" in captured.out
    assert "1 finding" in captured.out
    assert captured.err == ""


def test_a_bad_pattern_stops_the_run_with_an_error_instead_of_skipping_the_list(repo, monkeypatch, capsys, tmp_path):
    bad = tmp_path / "bad-list.txt"
    bad.write_text("(unclosed\n")
    monkeypatch.setenv(LIST_ENV, str(bad))
    monkeypatch.chdir(repo.path)

    status = repo_checks.main([])

    captured = capsys.readouterr()
    assert status == 2
    assert "bad-list.txt:1:" in captured.err
    assert captured.out == ""

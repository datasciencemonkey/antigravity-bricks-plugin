import pytest

from tools import repo_checks

REQUIRED = [".omc/", ".isaac/", ".build/", "Antigravity Plugin Marketplace Publishing Guide.pdf"]


def test_reports_every_required_entry_missing_from_an_empty_gitignore():
    findings = repo_checks.find_missing_gitignore_entries("")

    assert [finding.where for finding in findings] == [".gitignore"] * 4
    for entry in REQUIRED:
        assert sum(entry in finding.what for finding in findings) == 1


COMPLETE = "# local state\n" + "\n".join(REQUIRED) + "\n\n.claude/settings.local.json\n"


def test_reports_nothing_when_every_required_entry_is_present():
    assert repo_checks.find_missing_gitignore_entries(COMPLETE) == []


@pytest.mark.parametrize("lookalike", ["# {}", "!{}", "docs/{}"])
def test_a_commented_negated_or_nested_line_does_not_count_as_the_entry(lookalike):
    others = [entry for entry in REQUIRED if entry != ".omc/"]
    text = "\n".join([lookalike.format(".omc/"), *others]) + "\n"

    findings = repo_checks.find_missing_gitignore_entries(text)

    assert len(findings) == 1
    assert ".omc/" in findings[0].what


def test_tolerates_windows_line_endings_and_surrounding_spaces():
    text = "".join(f"  {entry}  \r\n" for entry in REQUIRED)

    assert repo_checks.find_missing_gitignore_entries(text) == []

from pathlib import Path

import pytest

from tools import repo_checks

REPO_ROOT = Path(__file__).resolve().parent.parent

SUPERSET_HOOKS = ".github/hooks/superset-notify.json"  # the Superset app writes this into every worktree
CLAUDE_AGENTS = ".claude/agents/"  # dev-loop subagent definitions: kept on disk, never shipped
REQUIRED = [
    ".omc/",
    ".isaac/",
    ".build/",
    "Antigravity Plugin Marketplace Publishing Guide.pdf",
    SUPERSET_HOOKS,
    CLAUDE_AGENTS,
]


def test_reports_every_required_entry_missing_from_an_empty_gitignore():
    findings = repo_checks.find_missing_gitignore_entries("")

    assert [finding.where for finding in findings] == [".gitignore"] * len(REQUIRED)
    for entry in REQUIRED:
        assert sum(entry in finding.what for finding in findings) == 1


def test_flags_a_gitignore_that_does_not_ignore_the_superset_hooks_file():
    others = [entry for entry in REQUIRED if entry != SUPERSET_HOOKS]

    findings = repo_checks.find_missing_gitignore_entries("\n".join(others) + "\n")

    assert [finding.what for finding in findings] == [f"missing entry: {SUPERSET_HOOKS}"]


def test_flags_a_gitignore_that_does_not_ignore_the_claude_agents_folder():
    others = [entry for entry in REQUIRED if entry != CLAUDE_AGENTS]

    findings = repo_checks.find_missing_gitignore_entries("\n".join(others) + "\n")

    assert [finding.what for finding in findings] == [f"missing entry: {CLAUDE_AGENTS}"]


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


def test_finder_metadata_is_git_ignored_so_a_stray_ds_store_cannot_break_the_plugin_tests():
    # A Finder-made .DS_Store inside plugins/databricks-genie/ is an untracked file there. Without this entry it fails the
    # version gate (an untracked file counts as a change) on a checkout that has changed nothing.
    entries = {line.strip() for line in (REPO_ROOT / ".gitignore").read_text().splitlines()}

    assert ".DS_Store" in entries

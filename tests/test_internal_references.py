import pytest

from tools.repo_checks import find_internal_references

# Fixtures are assembled at runtime, so this file never contains an internal reference itself
# (the repo-wide scan covers tests/ too).
SLACK = "databricks" + ".slack.com"
ATLASSIAN = "databricks" + ".atlassian.net"
GLEAN = "glean" + ".com"
TECH_DOCS = "tech-docs" + ".dev.databricks.com"
UNIVERSE = "databricks-eng" + "/universe"
GOOGLE_DOCS = "docs.google" + ".com"
GO = "go" + "/"  # prefix of an internal go-link
AUTH_KEY = "AUTH" + "-"  # prefixes of internal Jira projects
FEIP_KEY = "FEIP" + "-"
INTERNAL_TAG = "[INTER" + "NAL]"  # the literal tag that marks internal-only material


def test_flags_the_internal_slack_workspace_with_its_location():
    findings = find_internal_references(f"intro\nask in https://{SLACK}/archives/C1\n", path="notes.md")

    assert [finding.where for finding in findings] == ["notes.md:2"]
    assert SLACK in findings[0].what


def test_flags_the_internal_jira_site():
    findings = find_internal_references(f"see https://{ATLASSIAN}/browse/X-1", path="notes.md")

    assert [finding.where for finding in findings] == ["notes.md:1"]


def test_flags_the_glean_site():
    findings = find_internal_references(f"search https://app.{GLEAN}/q", path="notes.md")

    assert [finding.where for finding in findings] == ["notes.md:1"]


def test_flags_the_internal_tech_docs_site():
    findings = find_internal_references(f"read https://{TECH_DOCS}/guide", path="notes.md")

    assert [finding.where for finding in findings] == ["notes.md:1"]


def test_flags_the_internal_universe_repo():
    findings = find_internal_references(f"cloned from github.com/{UNIVERSE}/pull/1", path="notes.md")

    assert [finding.where for finding in findings] == ["notes.md:1"]


@pytest.mark.parametrize("kind", ["document", "spreadsheets", "presentation"])
def test_flags_google_doc_sheet_and_slide_links(kind):
    findings = find_internal_references(f"notes: https://{GOOGLE_DOCS}/{kind}/d/1AbCdEf_-1234567890/edit", path="notes.md")

    assert [finding.where for finding in findings] == ["notes.md:1"]


def test_flags_an_internal_go_link():
    findings = find_internal_references(f"see {GO}foo for details", path="notes.md")

    assert [finding.where for finding in findings] == ["notes.md:1"]


@pytest.mark.parametrize("text", [f"the {GO}no-go decision", f"{GO}no-go at noon", f"it was a {GO}nogo call"])
def test_does_not_flag_the_go_no_go_phrase(text):
    assert find_internal_references(text, path="notes.md") == []


@pytest.mark.parametrize(
    "text",
    ["https://github.com/golang/go/issues/1", "export PATH=$PATH:/usr/local/go/bin", "cd ~/go/pkg", "see django/contrib"],
)
def test_does_not_flag_go_directories_inside_paths_and_urls(text):
    assert find_internal_references(text, path="notes.md") == []


@pytest.mark.parametrize("text", [f"visit http://{GO}foo", f"visit https://{GO}foo/bar", f"(see {GO}foo)", f'link: "{GO}foo"'])
def test_flags_go_links_written_as_urls_and_inside_punctuation(text):
    assert len(find_internal_references(text, path="notes.md")) == 1


@pytest.mark.parametrize("key", [f"{AUTH_KEY}123", f"{FEIP_KEY}4567"])
def test_flags_jira_keys_with_the_known_internal_prefixes(key):
    findings = find_internal_references(f"tracked in {key} for now", path="notes.md")

    assert [finding.where for finding in findings] == ["notes.md:1"]
    assert key in findings[0].what


@pytest.mark.parametrize(
    "text",
    ["SHA-256 digest", "UTF-8 text", "ISO-8601 dates", "CVE-2024-1234", "OAUTH-2 flow", "AUTH- without a number"],
)
def test_does_not_flag_generic_upper_case_keys(text):
    assert find_internal_references(text, path="notes.md") == []


def test_flags_the_literal_internal_tag():
    findings = find_internal_references(f"Source: wiki page {INTERNAL_TAG}", path="notes.md")

    assert [finding.where for finding in findings] == ["notes.md:1"]
    assert INTERNAL_TAG in findings[0].what


@pytest.mark.parametrize(
    "text",
    [
        "see the Glean doc title",
        "ask in Slack",
        "tools: mcp__glean__search, mcp__glean__get_document_content",
        "Back claims with evidence (file path, URL, issue number, or Glean doc title)",
    ],
)
def test_does_not_flag_plain_glean_and_slack_mentions(text):
    assert find_internal_references(text, path="notes.md") == []


DIRECTOR_PATH = ".claude/agents/director.md"
ALLOWED_LINE = (
    "- Back claims with evidence (file path, URL, issue number, or Glean doc title) and a confidence tag: "
    "CONFIRMED, LIKELY, or UNVERIFIED. Mark internal-only sources " + INTERNAL_TAG + "."
)


def test_allows_the_one_known_internal_tag_line_in_the_director_agent():
    assert find_internal_references(f"intro\n{ALLOWED_LINE}\noutro", path=DIRECTOR_PATH) == []


def test_allowed_line_is_still_flagged_in_any_other_file():
    findings = find_internal_references(ALLOWED_LINE, path="docs/notes.md")

    assert [finding.where for finding in findings] == ["docs/notes.md:1"]


def test_allowed_line_stays_allowed_wherever_it_sits_in_the_file():
    moved_down = "\n".join(["first", "second", "third", ALLOWED_LINE, "fifth"])

    assert find_internal_references(moved_down, path=DIRECTOR_PATH) == []
    assert find_internal_references(ALLOWED_LINE, path=DIRECTOR_PATH) == []


def test_other_tag_lines_in_the_director_agent_are_still_flagged():
    changed_line = ALLOWED_LINE + " Extra words."
    other_line = f"Another note {INTERNAL_TAG}"

    findings = find_internal_references(f"{changed_line}\n{other_line}", path=DIRECTOR_PATH)

    assert [finding.where for finding in findings] == [f"{DIRECTOR_PATH}:1", f"{DIRECTOR_PATH}:2"]

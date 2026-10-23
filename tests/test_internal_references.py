import re

import pytest
from conftest import FAKE_HOST, FAKE_TRACKER

from tools.repo_checks import find_internal_references

# Fixtures are assembled at runtime, so this file never contains an internal reference itself
# (the repo-wide scan covers tests/ too).
GLEAN = "glean" + ".com"
GOOGLE_DOCS = "docs.google" + ".com"
GO = "go" + "/"  # prefix of an internal go-link
WORD = "INTER" + "NAL"  # the word inside the tag that marks internal-only material
INTERNAL_TAG = f"[{WORD}]"


def test_flags_the_glean_site():
    findings = find_internal_references(f"search https://app.{GLEAN}/q", path="notes.md")

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


def test_flags_the_literal_internal_tag():
    findings = find_internal_references(f"Source: wiki page {INTERNAL_TAG}", path="notes.md")

    assert [finding.where for finding in findings] == ["notes.md:1"]
    assert INTERNAL_TAG in findings[0].what


TAGGED_FORMS = [
    f"[{WORD.title()}]",
    f"[{WORD}: from the wiki]",
    "[" + WORD.lower() + "-only]",
    f"[{WORD} ONLY]",
    f"[{WORD.title()} Use Only]",
    "[" + WORD.lower() + "_only: a note]",
    f"[ {WORD} ]",
]


@pytest.mark.parametrize("tag", TAGGED_FORMS)
def test_flags_tagged_forms_of_the_internal_tag_in_any_case(tag):
    findings = find_internal_references(f"Source: wiki page {tag}", path="notes.md")

    assert [finding.where for finding in findings] == ["notes.md:1"]
    assert tag in findings[0].what


@pytest.mark.parametrize(
    "text",
    [
        "see [internal-reference] in the report",
        "read the [internal docs](https://example.test/docs)",
        "[internals] and [internally hosted]",
        "items[internal_state] = 1",
        "an internal-only note without brackets",
    ],
)
def test_does_not_flag_bracketed_words_that_merely_start_with_internal(text):
    assert find_internal_references(text, path="notes.md") == []


# --- patterns from the local deny-list (made up here; the real ones are in a git-ignored file) ------------------------


def test_flags_text_that_matches_a_deny_list_pattern_with_its_location(fake_denylist):
    text = f"intro\nask in https://{FAKE_HOST}/archives/C1\n"

    findings = find_internal_references(text, path="notes.md", denylist=fake_denylist)

    assert [finding.where for finding in findings] == ["notes.md:2"]
    assert findings[0].check == "internal-reference"
    assert FAKE_HOST in findings[0].what


@pytest.mark.parametrize("host", [FAKE_HOST, FAKE_TRACKER, FAKE_HOST.upper()])
def test_flags_every_host_in_the_list_in_any_case_the_list_asks_for(fake_denylist, host):
    findings = find_internal_references(f"see https://{host}/page", path="notes.md", denylist=fake_denylist)

    assert [finding.where for finding in findings] == ["notes.md:1"]
    assert host in findings[0].what


def test_does_not_flag_deny_listed_text_when_there_is_no_list():
    assert find_internal_references(f"see https://{FAKE_HOST}/page", path="notes.md") == []


@pytest.mark.parametrize("key", ["EXAMPLE-123", "DEMO-4567"])
def test_flags_ticket_keys_with_the_prefixes_in_the_list(fake_denylist, key):
    findings = find_internal_references(f"tracked in {key} for now", path="notes.md", denylist=fake_denylist)

    assert [finding.where for finding in findings] == ["notes.md:1"]
    assert key in findings[0].what


@pytest.mark.parametrize(
    "text",
    [
        "SHA-256 digest",
        "UTF-8 text",
        "ISO-8601 dates",
        "CVE-2024-1234",
        "COUNTEREXAMPLE-2 flow",
        "EXAMPLE- without a number",
    ],
)
def test_does_not_flag_generic_upper_case_keys(fake_denylist, text):
    assert find_internal_references(text, path="notes.md", denylist=fake_denylist) == []


def test_a_pattern_that_can_also_match_nothing_does_not_flag_every_line():
    rules = [("deny-list pattern", re.compile(r"example-internal\.invalid|"))]  # the stray | adds an empty alternative

    findings = find_internal_references("fine\nexample-internal.invalid\nfine\n", path="notes.md", denylist=rules)

    assert [finding.where for finding in findings] == ["notes.md:2"]


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

"""tools/release_notes.py: release notes from the pull requests merged since the previous release tag.

Pull request titles and bodies are untrusted text, so these tests feed the tool hostile ones and check that the notes
stay inert, bounded and well-formed.
"""
import ast
import io
import json
import re
import sys
from pathlib import Path

import pytest
from conftest import ISOLATED_GIT

from tools import release_notes

SOURCE = Path(release_notes.__file__)


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch):
    """The tool runs git itself; keep the machine's git config out of it, as the suite does for its own git."""
    for name, value in ISOLATED_GIT.items():
        monkeypatch.setenv(name, value)


def sha(number):
    return f"{number:040x}"


SHAS = {sha(number) for number in range(1, 1000)}


def pr(number, title="A change", body=None, commit=None, merged_at="2026-10-23T10:00:00Z"):
    """One item of the JSON that `gh api repos/.../pulls` returns, with the fields the tool reads."""
    commit = commit or sha(number)
    return {
        "number": number, "title": title, "body": body, "merged_at": merged_at,
        "merge_commit_sha": commit, "head": {"sha": commit},
    }


# The pull request description layout from the project's working rules.
BODY = """## Summary
- The bridge now waits up to 150 s for Genie.
- It gives up before Antigravity's 3-minute limit.

## Issues
Closes #28

## Testing
- ran the whole suite

This pull request and its description were written by Isaac.
"""


def bullets(body):
    return release_notes.summary_bullets(body)


# --- finding the bullets of "## Summary" -------------------------------------------------------------------------


def test_the_summary_bullets_are_the_bullets_under_the_summary_heading():
    assert bullets(BODY) == ["The bridge now waits up to 150 s for Genie.", "It gives up before Antigravity's 3-minute limit."]


def test_the_summary_ends_at_the_next_heading_of_the_same_or_a_higher_level():
    assert bullets("## Summary\n- one\n## Issues\n- not this\n") == ["one"]
    assert bullets("## Summary\n- one\n# Top\n- not this\n") == ["one"]


def test_a_deeper_heading_inside_the_summary_does_not_end_it():
    assert bullets("## Summary\n- one\n### Details\n- two\n## Issues\n- three\n") == ["one", "two"]


@pytest.mark.parametrize("marker", ["-", "*", "+", "1.", "2)", "10."])
def test_every_common_bullet_marker_counts(marker):
    assert bullets(f"## Summary\n{marker} item\n") == ["item"]


def test_an_indented_sub_bullet_counts_as_a_bullet():
    assert bullets("## Summary\n- parent\n  - child\n") == ["parent", "child"]


def test_windows_line_endings_work():
    assert bullets("## Summary\r\n- one\r\n- two\r\n\r\n## Issues\r\n- no\r\n") == ["one", "two"]


@pytest.mark.parametrize("heading", ["## Summary", "## summary", "## SUMMARY", "## Summary ##", "##   Summary  ", "  ## Summary", "### Summary"])
def test_the_heading_is_found_whatever_its_case_spacing_or_closing_hashes(heading):
    assert bullets(f"{heading}\n- one\n") == ["one"]


@pytest.mark.parametrize(
    "heading", ["Summary", "# Summary of changes", "##Summary", "## Summaries", "## The summary", "    ## Summary", "- ## Summary"]
)
def test_lines_that_are_not_the_summary_heading_are_not_found(heading):
    assert bullets(f"{heading}\n- one\n") == []


def test_a_wrapped_bullet_is_joined_and_a_blank_line_ends_it():
    body = "## Summary\n- first line\n  second line\nthird line\n\nloose text\n- next\n"

    assert bullets(body) == ["first line second line third line", "next"]


def test_text_that_is_not_a_bullet_is_not_part_of_the_summary():
    assert bullets("## Summary\nThis change does things.\n\n- one\n") == ["one"]


def test_a_body_without_a_summary_heading_has_no_summary_bullets():
    assert bullets("- one\n## Issues\nCloses #1\n") == []


def test_a_summary_without_bullets_has_no_summary_bullets():
    assert bullets("## Summary\nJust a sentence.\n\n## Issues\n") == []


def test_only_the_first_summary_section_counts():
    assert bullets("## Summary\n- one\n## Summary\n- two\n") == ["one"]


def test_a_summary_heading_inside_a_code_fence_is_not_a_heading():
    assert bullets("```\n## Summary\n- fake\n```\n## Summary\n- real\n") == ["real"]


def test_a_heading_like_line_inside_a_fence_does_not_end_the_summary():
    assert bullets("## Summary\n- one\n```sh\n# a comment\n## Issues\n```\n- two\n") == ["one", "two"]


def test_bullets_inside_a_fence_are_code_not_bullets():
    assert bullets("## Summary\n```\n- code\n```\n- real\n") == ["real"]


def test_an_unclosed_fence_swallows_the_rest_without_failing():
    assert bullets("## Summary\n- one\n```\n- two\n") == ["one"]


@pytest.mark.parametrize("body", [None, 5, ["## Summary", "- x"], {"a": 1}, ""])
def test_a_body_that_is_not_text_has_no_bullets(body):
    assert bullets(body) == []


# --- the notes -------------------------------------------------------------------------------------------------


def notes_for(*pulls, previous="v0.3.0", shas=SHAS):
    return release_notes.build_notes(list(pulls), shas, previous)


def test_each_pull_request_shows_its_title_its_number_and_its_summary_bullets():
    notes = notes_for(pr(28, title="Bridge: wait up to 150 s", body=BODY))

    assert notes == (
        "## Pull requests since v0.3.0\n"
        "\n"
        "- Bridge: wait up to 150 s (#28)\n"
        "  - The bridge now waits up to 150 s for Genie.\n"
        "  - It gives up before Antigravity's 3-minute limit.\n"
    )


def test_a_pull_request_without_a_summary_is_listed_by_its_title_alone():
    assert notes_for(pr(5, title="Fix the thing", body="No headings.\n- a bullet\n")) == (
        "## Pull requests since v0.3.0\n\n- Fix the thing (#5)\n"
    )


def test_a_pull_request_without_a_title_is_still_listed():
    assert "- (no title) (#5)" in notes_for(pr(5, title=None))


def test_only_pull_requests_merged_in_the_range_are_listed_oldest_first():
    pulls = [
        pr(3, "Third", merged_at="2026-10-23T12:00:00Z"),
        pr(1, "Before the tag", merged_at="2026-10-20T12:00:00Z"),
        pr(2, "Second", merged_at="2026-10-22T12:00:00Z"),
        pr(4, "Closed, never merged", merged_at=None),
        pr(5, "Merge time left empty", merged_at=""),
    ]

    notes = notes_for(*pulls, shas={sha(2), sha(3), sha(4), sha(5)})

    assert [line for line in notes.splitlines() if line.startswith("- ")] == ["- Second (#2)", "- Third (#3)"]


def test_a_pull_request_counts_by_its_merge_commit_or_by_its_head_commit():
    merged_by_github = pr(1, "Merged on GitHub")  # its commits reach main under another SHA, the merge commit's
    merged_by_github["merge_commit_sha"], merged_by_github["head"]["sha"] = sha(50), sha(900)
    fast_forwarded = pr(2, "Fast-forwarded")  # main moved to its head commit itself
    fast_forwarded["merge_commit_sha"] = None
    elsewhere = pr(3, "Somewhere else")
    elsewhere["merge_commit_sha"], elsewhere["head"]["sha"] = sha(901), sha(902)

    notes = notes_for(merged_by_github, fast_forwarded, elsewhere, shas={sha(50), sha(2)})

    assert [line for line in notes.splitlines() if line.startswith("- ")] == ["- Merged on GitHub (#1)", "- Fast-forwarded (#2)"]


def test_a_pull_request_that_appears_twice_is_listed_once():
    assert notes_for(pr(1, "Once"), pr(1, "Once")).count("(#1)") == 1


def test_without_a_previous_tag_every_merged_pull_request_is_listed():
    notes = release_notes.build_notes([pr(1, "One"), pr(2, "Two")], SHAS, None)

    assert notes == "## Pull requests\n\n- One (#1)\n- Two (#2)\n"


def test_no_pull_requests_gives_a_plain_sentence():
    assert notes_for() == "## Pull requests since v0.3.0\n\nNo pull requests were merged since v0.3.0.\n"
    assert release_notes.build_notes([], SHAS, None) == "## Pull requests\n\nNo pull requests have been merged yet.\n"


@pytest.mark.parametrize("number", [True, None, "7", 7.5, [7], {"n": 7}])
def test_a_pull_request_without_a_whole_number_is_skipped(number):
    broken = pr(1, "Broken")
    broken["number"] = number

    assert notes_for(broken) == notes_for()


def test_items_that_are_not_pull_request_objects_are_skipped():
    assert notes_for(None, 5, "text", [], pr(1, "Real")).count("(#") == 1


@pytest.mark.parametrize("tag", ["v1", "latest", "1.2.3", "v1.2.3-rc1", "v1.2.3; rm -rf /", "v1.2.3\n## Injected", ""])
def test_a_previous_tag_that_is_not_a_release_tag_is_refused(tag):
    with pytest.raises(ValueError):
        release_notes.build_notes([], SHAS, tag)


# --- hostile text -------------------------------------------------------------------------------------------------

SHELL = "`touch pwned` $(touch pwned) ${IFS} ; rm -rf / | cat > /dev/null & echo 'x' \"y\" \\ <(id) >> out"


def test_shell_metacharacters_in_a_bullet_come_through_as_the_same_text():
    assert bullets(f"## Summary\n- {SHELL}\n") == [SHELL]


def test_shell_metacharacters_in_every_field_are_only_text_in_the_notes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    notes = notes_for(pr(1, title=SHELL, body=f"## Summary\n- {SHELL}\n"))

    assert notes.count(SHELL) == 2
    assert list(tmp_path.iterdir()) == []  # nothing ran and nothing was written


MARKERS = [
    "# Fake heading", "## Release v9.9.9", "###### deep", "> quoted", "- nested", "* star", "+ plus", "1. numbered",
    "2) numbered", "10. ten", "=== underline", "| a | b |", "```python", "~~~", "--- rule", "*** rule",
]


BLOCK_START = re.compile(r"[#>+*=|~-]|`{3}|\d+[.)]")


@pytest.mark.parametrize("text", MARKERS)
def test_a_title_or_a_bullet_cannot_start_a_markdown_block(text):
    notes = notes_for(pr(1, title=text, body=f"## Summary\n- {text}\n"))

    assert [line for line in notes.splitlines() if line.startswith("#")] == ["## Pull requests since v0.3.0"]
    for line in notes.splitlines()[2:]:  # after the heading and the blank line, only the lists the tool writes
        assert line.startswith(("- ", "  - "))
        assert not BLOCK_START.match(re.sub(r"^ *- ", "", line)), line  # what follows the list marker starts no block
    assert text.replace("\\", "") in notes.replace("\\", "")  # still readable once the escapes are rendered


def test_a_heading_inside_the_title_or_the_summary_cannot_forge_a_section():
    body = "## Summary\n- real\n# Release v9.9.9\n- hidden\n"

    notes = notes_for(pr(1, title="Fine\n## Release v9.9.9\n- forged (#99)", body=body))

    assert [line for line in notes.splitlines() if line.startswith("#")] == ["## Pull requests since v0.3.0"]
    assert "hidden" not in notes
    assert notes.count("\n- ") == 1  # one list item at the top level: this pull request


def test_newlines_and_control_characters_cannot_break_a_line():
    title = "One\ntwo\r\nthree\ttab\x00nul\x1b[31mred‮evil​zero sep\u0085nel"

    notes = notes_for(pr(1, title=title, body="## Summary\n- a b c\n"))

    lines = notes.split("\n")  # not splitlines(), which would itself break on the separators this test is about
    assert lines[2] == "- One two three tabnul[31mredevilzero sep nel (#1)"
    assert lines[3] == "  - a b c"
    assert len(lines) == 5 and lines[4] == ""  # heading, blank, the title line, one bullet, and the final newline
    for bad in ("\x00", "\x1b", "‮", "​", " ", " ", "\u0085", "\t", "\r"):
        assert bad not in notes


def test_a_very_long_body_gives_a_short_bounded_entry():
    huge = "## Summary\n" + "- bullet\n" * 200_000 + "- " + "x" * 3_000_000 + "\n"

    notes = notes_for(pr(1, body=huge))

    assert len(notes) < 4_000
    assert sum(line.startswith("  - ") for line in notes.splitlines()) <= release_notes.BULLETS_PER_PR + 1
    assert re.search(r"  - …and \d+ more\n", notes)


def test_a_very_long_title_or_bullet_is_cut_short_with_an_ellipsis():
    notes = notes_for(pr(1, title="T" * 5000, body="## Summary\n- " + "w" * 5000 + "\n"))

    lines = notes.splitlines()
    assert len(lines[2]) <= len("- ") + release_notes.TITLE_MAX + len(" (#1)") + 1 and "…" in lines[2]
    assert len(lines[3]) <= len("  - ") + release_notes.BULLET_MAX + 1 and "…" in lines[3]


def test_a_body_with_nothing_but_whitespace_and_zero_width_characters_adds_no_empty_bullets():
    notes = notes_for(pr(1, title="Title", body="## Summary\n- ​​\n-   \n- real\n"))

    assert notes.endswith("- Title (#1)\n  - real\n")


def test_the_whole_notes_stay_under_their_cap_and_say_how_many_were_left_out():
    summary = "## Summary\n" + "".join(f"- {'word ' * 60}{index}\n" for index in range(12))
    pulls = [pr(number, f"Change {number}", summary) for number in range(1, 600)]

    notes = release_notes.build_notes(pulls, {sha(number) for number in range(1, 600)}, "v0.3.0")

    assert len(notes) <= release_notes.NOTES_MAX
    assert re.fullmatch(r"- …and \d+ more pull requests\b.*", notes.splitlines()[-1])
    assert "(#1)" in notes and "(#599)" not in notes  # oldest first, so the newest are the ones left out


# --- reading the JSON that gh prints -------------------------------------------------------------------------------


def test_one_array_of_pull_requests_is_read():
    assert [item["number"] for item in release_notes.load_pulls(json.dumps([pr(1), pr(2)]))] == [1, 2]


def test_pages_printed_one_after_another_are_read():
    pages = json.dumps([pr(1)]) + json.dumps([pr(2), pr(3)]) + "\n"

    assert [item["number"] for item in release_notes.load_pulls(pages)] == [1, 2, 3]


def test_an_array_of_pages_is_read():
    assert [item["number"] for item in release_notes.load_pulls(json.dumps([[pr(1)], [pr(2)]]))] == [1, 2]


def test_one_object_per_line_is_read():
    lines = "\n".join(json.dumps(pr(number)) for number in (1, 2))

    assert [item["number"] for item in release_notes.load_pulls(lines)] == [1, 2]


@pytest.mark.parametrize("text", ["", " \n\t"])
def test_empty_input_is_no_pull_requests(text):
    assert release_notes.load_pulls(text) == []


@pytest.mark.parametrize("text", ["{", "[1, 2", "not json", "[1] trailing words", "[" * 100_000 + "]" * 100_000])
def test_input_that_is_not_json_is_refused(text):
    with pytest.raises(ValueError):
        release_notes.load_pulls(text)


# --- the previous tag and the commits since it -----------------------------------------------------------------------


def test_the_previous_tag_is_the_highest_release_tag_merged_into_the_head(repo):
    repo.commit("a")
    repo.git("tag", "v0.2.0")
    repo.commit("b")
    repo.git("tag", "v0.10.0")  # numerically above v0.9.0, though it sorts below it as text
    repo.commit("c")
    for name in ("v0.9.0", "v1", "latest", "v0.11.0-rc1", "release-9.9.9", "v1.2.3.4", "V2.0.0"):
        repo.git("tag", name)
    other = repo.commit("on a branch that is not merged")
    repo.git("tag", "v9.9.9")
    repo.git("reset", "-q", "--hard", "HEAD~1")
    head = repo.commit("head")

    assert other != head
    assert release_notes.previous_tag(head, cwd=repo.path) == "v0.10.0"


def test_with_no_release_tag_there_is_no_previous_tag(repo):
    repo.git("tag", "v1")
    head = repo.commit("a")

    assert release_notes.previous_tag(head, cwd=repo.path) is None


def test_the_commits_since_a_tag_are_the_ones_after_it(repo):
    repo.commit("a")
    repo.git("tag", "v0.1.0")
    b, c = repo.commit("b"), repo.commit("c")

    assert release_notes.commits_since("v0.1.0", c, cwd=repo.path) == {b, c}


def test_without_a_tag_all_history_up_to_the_head_counts(repo):
    base = repo.git("rev-parse", "HEAD")
    a = repo.commit("a")
    repo.commit("after the head")

    assert release_notes.commits_since(None, a, cwd=repo.path) == {base, a}


@pytest.mark.parametrize("head", ["HEAD", "main", "abc", "--all", "-h", "0" * 39, "g" * 40, "A" * 40, "0" * 40 + "\n", ""])
def test_a_head_that_is_not_a_full_lowercase_sha_is_refused_before_git_runs(head, repo):
    with pytest.raises(ValueError):
        release_notes.previous_tag(head, cwd=repo.path)
    with pytest.raises(ValueError):
        release_notes.commits_since(None, head, cwd=repo.path)


# --- the command line -------------------------------------------------------------------------------------------------


@pytest.fixture
def history(repo, monkeypatch):
    """Three commits, a tag after the first, and the pull requests that brought them in."""
    before = repo.commit("before the release")
    repo.git("tag", "v0.3.0")
    one, two = repo.commit("one"), repo.commit("two")
    monkeypatch.chdir(repo.path)
    pulls = [pr(1, "Before", commit=before), pr(2, "One", commit=one), pr(3, "Two", commit=two)]
    return repo, pulls, two


def run(tmp_path, pulls, head, extra=()):
    source = tmp_path / "pulls.json"
    source.write_text(pulls if isinstance(pulls, str) else json.dumps(pulls))
    output = tmp_path / "notes.md"
    # --head=VALUE, so that a value which looks like an option reaches the tool's own check instead of argparse's
    code = release_notes.main(["--pulls", str(source), f"--head={head}", "--output", str(output), *extra])
    return code, output


def test_the_command_line_writes_the_pull_requests_since_the_previous_tag(history, tmp_path, capsys):
    _, pulls, head = history

    code, output = run(tmp_path, pulls, head)

    assert code == 0
    assert output.read_text() == "## Pull requests since v0.3.0\n\n- One (#2)\n- Two (#3)\n"
    assert capsys.readouterr().out == "release_notes: previous tag v0.3.0\n"


def test_without_a_tag_the_command_line_lists_every_merged_pull_request_up_to_the_head(repo, tmp_path, monkeypatch, capsys):
    one, two = repo.commit("one"), repo.commit("two")
    repo.commit("merged after the head")
    monkeypatch.chdir(repo.path)

    code, output = run(tmp_path, [pr(1, "One", commit=one), pr(2, "Two", commit=two), pr(3, "Later", commit=sha(77))], two)

    assert code == 0
    assert output.read_text() == "## Pull requests\n\n- One (#1)\n- Two (#2)\n"
    assert capsys.readouterr().out == "release_notes: previous tag none\n"


def test_the_command_line_reads_standard_input_unless_given_a_file(history, tmp_path, monkeypatch):
    _, pulls, head = history
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(pulls)))
    output = tmp_path / "notes.md"

    code = release_notes.main(["--head", head, "--output", str(output)])

    assert code == 0 and "- Two (#3)" in output.read_text()


def test_hostile_text_is_written_as_text_and_nothing_runs(history, tmp_path):
    repo, _, head = history
    hostile = pr(2, title=SHELL + " $(touch pwned)", body=f"## Summary\n- {SHELL}\n", commit=head)

    code, output = run(tmp_path, [hostile], head)

    assert code == 0
    assert SHELL in output.read_text()
    assert not (repo.path / "pwned").exists() and not (tmp_path / "pwned").exists()


@pytest.mark.parametrize("head", ["HEAD", "--all", "abc", "A" * 40, "0" * 40])
def test_a_bad_or_unknown_head_fails_without_writing_anything(head, history, tmp_path, capsys):
    _, pulls, _ = history

    code, output = run(tmp_path, pulls, head)

    assert code != 0 and not output.exists()
    assert capsys.readouterr().err.startswith("release_notes:")


def test_unreadable_json_fails_without_echoing_it(history, tmp_path, capsys):
    _, _, head = history
    text = '{"title": "private words that must stay out of the log" nonsense'

    code, output = run(tmp_path, text, head)

    assert code != 0 and not output.exists()
    err = capsys.readouterr().err
    assert err.startswith("release_notes:") and "private words" not in err


def test_a_missing_pulls_file_or_a_missing_output_folder_fails(history, tmp_path, capsys):
    _, pulls, head = history

    missing_input = release_notes.main(["--pulls", str(tmp_path / "none.json"), "--head", head, "--output", str(tmp_path / "n.md")])
    source = tmp_path / "pulls.json"
    source.write_text(json.dumps(pulls))
    missing_folder = release_notes.main(["--pulls", str(source), "--head", head, "--output", str(tmp_path / "no" / "n.md")])

    assert missing_input != 0 and missing_folder != 0
    assert capsys.readouterr().err.count("release_notes:") == 2


# --- what the tool is allowed to do -----------------------------------------------------------------------------------


def calls_in_the_tool():
    return [node for node in ast.walk(ast.parse(SOURCE.read_text())) if isinstance(node, ast.Call)]


def test_the_tool_never_goes_through_a_shell():
    calls = calls_in_the_tool()
    names = {ast.unparse(call.func) for call in calls}

    assert not names & {"os.system", "os.popen", "os.exec", "os.spawnv", "subprocess.Popen", "subprocess.call", "eval", "exec"}
    assert not [call for call in calls for keyword in call.keywords if keyword.arg == "shell"]
    runs = [call for call in calls if ast.unparse(call.func) == "subprocess.run"]
    assert runs and all(isinstance(call.args[0], ast.List) for call in runs)  # a list of arguments, never one string


def test_the_tool_only_runs_git():
    commands = [
        call.args[0].elts[0] for call in calls_in_the_tool() if ast.unparse(call.func) == "subprocess.run"
    ]

    assert [ast.literal_eval(command) for command in commands] == ["git"]


def test_the_tool_uses_only_the_standard_library_so_a_bare_runner_can_run_it():
    imported = set()
    for node in ast.walk(ast.parse(SOURCE.read_text())):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module.split(".")[0])

    assert imported <= set(sys.stdlib_module_names), imported - set(sys.stdlib_module_names)

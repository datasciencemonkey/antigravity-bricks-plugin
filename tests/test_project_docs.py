"""AGENTS.md and docs/progress.md are the project's source of truth, so they have to say what is true now."""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS = REPO_ROOT / "AGENTS.md"
PROGRESS = REPO_ROOT / "docs" / "progress.md"
CLOSED_AS_NOT_PLANNED = (6, 7, 9, 13, 15, 16, 17, 18, 22, 24, 29, 32)


def section(path, heading):
    """The text under a Markdown heading, up to the next heading of the same or a higher level."""
    text = path.read_text()
    level = len(heading) - len(heading.lstrip("#"))
    match = re.search(rf"^{re.escape(heading)}\n(.*?)(?=^#{{1,{level}}} |\Z)", text, re.MULTILINE | re.DOTALL)
    assert match, f"{path.name} has no '{heading}' section"
    return match.group(1)


def bullet(text, start):
    """The one bullet (with its continuation lines) that starts with `start`."""
    found = re.findall(rf"^{re.escape(start)}.*?(?=^- |^\S|\Z)", text, re.MULTILINE | re.DOTALL)
    assert len(found) == 1, f"{len(found)} bullets start with {start!r}"
    return found[0]


def working_rules():
    return section(AGENTS, "## Working rules")


def what_we_know():
    return section(AGENTS, "## What we know")


def test_the_repo_is_public_and_agents_md_no_longer_calls_it_private():
    text = AGENTS.read_text()

    assert "The repo is public (since 2026-10-20)" in text
    assert "even though the repo is private" not in text


def test_the_rule_about_internal_material_stays_now_that_the_repo_is_public():
    rule = bullet(working_rules(), "- The repo is public")

    assert "Internal-only Databricks material" in rule and "customer names" in rule
    assert ".omc/research/" in rule and "GitHub issues, PRs and committed files" in rule


def test_agents_md_names_the_antigravity_and_agy_versions_installed_now():
    text = AGENTS.read_text()

    assert "2.25.0" in text and "1.4.6" in text
    assert "Antigravity 2.24.0" not in text and "(2.24.0, which holds the plugin engine)" not in text


def test_agents_md_says_plugins_do_not_run_in_the_ide_yet():
    text = AGENTS.read_text()
    products = bullet(what_we_know(), "- Antigravity is three products")

    assert "Plugins apply to all three" not in text
    assert "doesn't run plugin MCP servers" in products and "(#30)" in products


def test_the_subagent_frontmatter_line_follows_googles_subagent_docs():
    line = bullet(what_we_know(), "- Subagent frontmatter")

    for fact in ("antigravity.google/docs/subagents", "`tools`", "`mcpServers`", "`commandExecutionPolicy`",
                 "`enable_*`"):
        assert fact in line, fact
    assert "enable_write_tools" not in line


def test_the_non_genie_work_is_recorded_as_closed_as_not_planned_not_parked():
    text = AGENTS.read_text()
    line = bullet(section(AGENTS, "## Status (2026-10-23)"), "- Closed as not planned")

    assert "Parked for later" not in text
    assert "2026-10-23" in line
    for number in CLOSED_AS_NOT_PLANNED:
        assert f"#{number}" in line, number
    assert "#31 was folded into #35" in line


def test_the_mvp_closes_on_the_cli_and_the_desktop_app_and_the_ide_waits_on_google():
    decision = bullet(section(AGENTS, "## Decisions"), "- MVP scope")
    criteria = section(AGENTS, "## MVP success criteria (set by the user, 2026-10-23)")

    assert "closes on the `agy` CLI and the desktop app" in decision and "waits on Google (#30)" in decision
    assert "waits on Google" in criteria


def test_the_sign_in_paths_decision_puts_u2m_first_and_the_service_principal_path_after_the_mvp():
    decision = bullet(section(AGENTS, "## Decisions"), "- Sign-in paths (the user, 2026-10-23)")

    for fact in ("U2M through the Databricks CLI is the default", "OAuth M2M", "after the MVP", "opt-in",
                 "env-provided client credentials", "live CI test", "(#39)"):
        assert fact in decision, fact


def test_agents_md_says_why_the_manifest_ships_author_and_license_but_leaves_out_category_and_tags():
    note = bullet(what_we_know(), "- The manifest ships `author` and `license` anyway")
    review = bullet(section(AGENTS, "## Decisions"), "- Guide review (#34, 2026-10-23)")

    for fact in ("`author`", "`license`", "`category`", "`tags`", "--strict", "unknown field"):
        assert fact in note, fact
    for fact in ("follows the app", "`category` or `tags`", "version 0.3.1"):
        assert fact in review, fact


def test_the_this_machine_note_says_the_mcp_config_json_is_no_longer_empty_without_quoting_it():
    note = bullet(what_we_know(), "- Two apps are installed")

    assert "`mcp_config.json`" in note and "leftover" in note and "is empty" not in note
    assert "never print" in note.lower()


def test_the_working_rules_forbid_printing_config_and_credential_files():
    rule = bullet(working_rules(), "- Never print the contents of config or credential files")

    for fact in ("`~/.gemini/**`", "`~/.databrickscfg`", "key names or hashes", "every subagent prompt"):
        assert fact in rule, fact


def test_the_working_rules_name_the_local_deny_list_that_is_never_committed():
    rule = bullet(working_rules(), "- Organization-specific patterns")

    for fact in (".omc/denylist.txt", "REPO_CHECKS_DENYLIST", "customer names", "never committed"):
        assert fact in rule, fact


def test_the_director_rule_says_it_is_a_local_git_ignored_helper_that_the_repo_does_not_ship():
    rule = bullet(working_rules(), "- Questions and dispatch plans go to the `director` subagent first.")

    for fact in ("local dev-loop helper", "git-ignored `.claude/agents/director.md`", "not shipped in the repo",
                 "Consult it before dispatching any other subagent"):
        assert fact in rule, fact


def test_the_pr_rule_gives_the_description_parts_in_order_and_merges_by_fast_forward():
    rule = bullet(working_rules(), "- **PRs.**")

    parts = ["`## Summary`", "`## Issues`", "`Closes #N`", "`## Testing`", "written by Isaac"]
    assert [rule.index(part) for part in parts] == sorted(rule.index(part) for part in parts)
    for fact in ("tools.repo_checks --stdin", "git push origin <head>:main", "GitHub marks the PR merged",
                 "Never use GitHub's merge buttons, which make GitHub the committer",
                 "from the Summary sections of the PRs merged since the previous tag"):
        assert fact in rule, fact
    assert "git push origin HEAD:main" not in AGENTS.read_text()


def test_the_versions_rule_bumps_only_the_last_number_and_leaves_tagging_to_the_release_workflow():
    rule = bullet(working_rules(), "- **Versions:**")

    for fact in ("bumps only the last number", "0.3.0 → 0.3.1 → 0.3.2", "`v<version>`", "GitHub Release",
                 "passes CI on `main`", "It never bumps anything itself"):
        assert fact in rule, fact


def test_the_versions_rule_describes_the_test_as_strict_about_major_and_minor():
    rule = bullet(working_rules(), "- **Versions:**")

    assert "keeps the merge-base's major and minor and has a higher last number" in rule
    assert "the version isn't higher" not in rule


def test_progress_md_describes_the_version_gate_as_a_bump_of_only_the_last_number():
    gate = bullet(section(PROGRESS, "### The plugin (`plugins/databricks-genie/`)"), "- ~~A version gate")

    assert "keeps the merge-base's major and minor" in gate and "raises the last number" in gate
    assert "isn't higher" not in gate


def test_progress_md_lists_the_mvp_with_each_item_done_or_in_progress():
    mvp = section(PROGRESS, "### The MVP")

    for issue in ("#30", "#33", "#34", "#35", "#36"):
        assert issue in mvp, issue
    assert "in progress" in mvp and "~~" in mvp  # something is crossed out as done, and something is still going


def test_progress_md_no_longer_lists_the_repo_url_install_as_untested_while_private():
    text = PROGRESS.read_text()

    assert "untested while the repo is private" not in text
    assert "~~Install from the GitHub repo URL" in text

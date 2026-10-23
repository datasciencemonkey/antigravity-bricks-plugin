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
    for fact in ("follows the app", "`category` or `tags`", "version 0.3.2"):
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


def test_agents_md_describes_the_version_gate_as_strict_about_major_and_minor():
    # It says so in What we know. The working rules' Versions line is left alone: the release workflow PR rewrites it.
    note = bullet(what_we_know(), "- The manifest ships `author` and `license` anyway")

    assert "keeps the merge-base's major and minor and has a higher last number" in note


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


def test_progress_md_hard_codes_no_test_count_because_it_goes_stale_with_the_next_test():
    assert re.search(r"\b\d+ tests\b", PROGRESS.read_text()) is None


def test_progress_md_explains_profile_switching_without_launchctl_and_keeps_the_note_that_it_is_untested_live():
    item = bullet(section(PROGRESS, "### The plugin (`plugins/databricks-genie/`)"), "- README: explain how to point")

    for fact in ("DATABRICKS_CONFIG_PROFILE=<name> agy", "open -a Antigravity --env DATABRICKS_CONFIG_PROFILE=<name>",
                 "Not `launchctl setenv`, which reaches every app opened until logout",
                 "still need checking live with a second profile"):
        assert fact in item, fact


def test_the_first_use_decision_does_not_promise_the_ide_behaves_like_the_other_surfaces():
    decision = bullet(section(AGENTS, "## Decisions"), "- First-use setup")

    assert "every surface that runs the plugin behaves the same (the IDE waits on Google, #30)" in decision
    assert "the desktop app and the IDE behave the same" not in decision


def test_the_public_docs_name_no_internal_tool():
    internal_tool = "FE" "VM"  # split, so this file doesn't contain the name either
    for path in (AGENTS, PROGRESS, REPO_ROOT / "README.md", REPO_ROOT / "plugins" / "databricks-genie" / "README.md"):
        assert internal_tool not in path.read_text(), path.name


# What the #33 follow-ups changed: the bridge's hidden-profile rule, the profile's host, a machine with no config file, the
# timeout errors, and the Databricks CLI behaviours they were checked against.


def decisions():
    return section(AGENTS, "## Decisions")


def test_the_sign_in_decision_reads_the_workspace_from_auth_profiles_not_the_retired_auth_describe():
    decision = bullet(decisions(), "- Sign-in (#8)")

    assert "`databricks auth profiles`" in decision and "no network call" in decision
    assert "auth describe" not in decision


def test_the_first_use_decision_records_what_the_bridge_does_with_the_config_file():
    decision = bullet(decisions(), "- First-use setup")

    for fact in ("A missing config file means no profiles", "`auth profiles` exits 1 when there is no file",
                 "reads the key names of each section", "never a value", "at least one key",
                 "Keys before the first header count as DEFAULT's", "go-ini", "A header with no keys doesn't count",
                 "`databricks configure` writes an empty `[DEFAULT]` header into a new config",
                 "A config file the bridge can't read counts as holding keys"):
        assert fact in decision, fact
    assert "checks the section names" not in decision  # it reads key names now


def test_the_first_use_decision_says_the_workspace_comes_only_from_the_profile():
    decision = bullet(decisions(), "- First-use setup")

    for fact in ("The workspace comes only from the profile: the bridge reads no workspace from the environment",
                 "an inherited `DATABRICKS_HOST` doesn't steer where it calls",
                 "`GENIE_MCP_URL`", "only for a loopback URL"):
        assert fact in decision, fact
    assert "the CLI calls get no `DATABRICKS_*` variable" not in decision  # the CLI's environment is left alone


def test_the_first_use_decision_says_the_bridge_asks_the_cli_for_its_profiles_before_it_trusts_its_own_guess_at_the_config():
    decision = bullet(decisions(), "- First-use setup")

    for fact in ("The bridge asks the CLI first and takes its failure for no profiles only when the config file is missing "
                 "where the bridge looks too", "on Windows it uses USERPROFILE, not HOME"):
        assert fact in decision, fact


def test_the_cli_owns_its_environment_decision_is_recorded_with_its_price_and_its_history():
    decision = bullet(decisions(), "- The CLI owns its environment")

    for fact in ("(the user, 2026-10-23)", "the bridge leaves the CLI's environment alone",
                 "the CLI owns its config and environment variables", "get the bridge's environment unchanged, as in the "
                 "user's terminal", "`DATABRICKS_AUTH_STORAGE`", "`DATABRICKS_OAUTH_CALLBACK_PORT`",
                 "reads only `DATABRICKS_CONFIG_PROFILE`", "`DATABRICKS_CONFIG_FILE`", "`GENIE_BRIDGE_DATABRICKS_CLI`",
                 "it never reads `DATABRICKS_HOST`", "comes only from the CLI's profile listing",
                 "An exported `DATABRICKS_HOST` costs nothing in v1.22.1",
                 "the bridge names the profile on every call (and `--host` for a sign-in)",
                 "answers `auth token --profile` with the profile's own U2M token", "A contract test on the real CLI pins it",
                 "`GENIE_MCP_URL`", "only for a loopback URL",
                 "An earlier version filtered the CLI's environment", "looked like `login_needed`"):
        assert fact in decision, fact
    for old in ("can make the CLI report a sign-in error", "unset them"):  # it doesn't: the real CLI was checked
        assert old not in decision, old


def test_the_standalone_connector_decision_says_what_each_kind_of_timeout_tells_the_agent():
    decision = bullet(decisions(), "- Standalone connector")

    for fact in ("asking again starts a duplicate question", "`genie_cancel_response` with its `conversation_id` first",
                 "gets its IDs back for polling", "Fetching a result or cancelling starts no question",
                 "the request wasn't sent"):
        assert fact in decision, fact


def test_what_we_know_records_how_the_real_databricks_cli_behaves():
    known = what_we_know()
    exits = bullet(known, "- `databricks auth profiles --skip-validate` exits 1")
    storage = bullet(known, "- `DATABRICKS_AUTH_STORAGE`")
    token = bullet(known, "- `auth token --profile P` answers with P's own cached U2M token")
    lists = bullet(known, "- It lists only config sections that have a host or an account_id")
    parser = bullet(known, "- The INI parser (go-ini)")
    configure = bullet(known, "- `databricks configure --host <host> --profile <name>`")
    stand_in = bullet(known, "- The tests keep a stand-in")

    assert "**The Databricks CLI (v1.22.1, checked against the real CLI in #33)**" in known
    for fact in ("config file is missing", "a new machine has none", "a folder", '`{"profiles": null}` for an empty file',
                 "On Windows the CLI looks under USERPROFILE for its config, wherever HOME points"):
        assert fact in exits, fact
    for fact in ("`plaintext` or `secure`", "the file cache or the OS keyring", "overrides `[__settings__] auth_storage`",
                 "tell users to set it", "`DATABRICKS_OAUTH_CALLBACK_PORT`", "`DATABRICKS_DISABLE_OAUTH_REFRESH_TOKEN`",
                 "which is why the bridge leaves its environment alone"):
        assert fact in storage, fact
    for fact in ("`DATABRICKS_HOST` for another workspace", "`DATABRICKS_TOKEN`", "`DATABRICKS_AUTH_TYPE`",
                 "`DATABRICKS_CLIENT_ID`", "`DATABRICKS_ACCOUNT_ID`", "checked offline",
                 "`<HOME>/.databricks/token-cache.json`", "a cache keyed by host is not"):
        assert fact in token, fact
    for fact in ("`account_id`", "`azure_workspace_resource_id`", "an empty header"):
        assert fact in lists, fact
    for fact in ("keys before the first header into DEFAULT", "merges them with a later `[DEFAULT]` header",
                 "first `=` or `:`", "no delimiter"):
        assert fact in parser, fact
    for fact in ("empty `[DEFAULT]` header", "`[__settings__]`", "`default_profile`"):
        assert fact in configure, fact
    for fact in ("same tests run against the real CLI when it's installed", "temporary folder as HOME",
                 "neither reads the real `~/.databrickscfg`"):
        assert fact in stand_in, fact


def test_open_questions_name_the_default_profile_setting_the_bridge_ignores():
    questions = section(AGENTS, "## Open questions")

    assert "`[__settings__] default_profile`" in questions and "ignores" in questions
    assert "`DATABRICKS_CONFIG_PROFILE`" in questions and "never picks a profile for the user" in questions
    assert "None right now" not in questions
    assert "New questions go to the director first" in questions


def test_progress_md_lists_the_review_fixes_for_the_hidden_profile_the_missing_config_the_host_and_the_timeouts():
    fixes = bullet(section(PROGRESS, "### The plugin (`plugins/databricks-genie/`)"), "- Review fixes (#33):")

    for fact in ("reading only key names, never values", "at least one key", "An empty header", "`[DEFAULT]`",
                 "keys before the first header count as DEFAULT's",
                 "A machine with no config file is `not_configured` and can set up",
                 "The profile's host decides where the bridge calls", "`GENIE_MCP_URL`", "only for a loopback URL",
                 "Databricks CLI gets the bridge's environment unchanged", "`DATABRICKS_AUTH_STORAGE`",
                 "a headless sign-in made with plaintext storage looked like `login_needed`",
                 "The bridge asks the CLI for its profiles first", "can't hide a listed profile",
                 "A contract test on the real CLI shows `auth token --profile` still returns the profile's own token",
                 "reads only two `DATABRICKS_*` variables", "`GENIE_BRIDGE_DATABRICKS_CLI`",
                 "cancel its running question first", "the request wasn't sent"):
        assert fact in fixes, fact
    assert "Genie can report a sign-in error" not in fixes  # the old claim: the real CLI was checked, and it doesn't
    assert "reading only section names" not in fixes
    assert "no `DATABRICKS_*` variable except" not in fixes


def test_progress_md_records_the_contract_tests_that_run_the_stand_in_cli_and_the_real_one():
    item = bullet(section(PROGRESS, "### Engineering setup"), "- ~~Contract tests for the Databricks CLI")

    for fact in ("stand-in `databricks` CLI", "CLI v1.22.1", "the real CLI when it's installed", "temporary folder as HOME",
                 "neither reads the real `~/.databrickscfg`"):
        assert fact in item, fact

"""The root README is for end users: what the plugin does, what it needs, how to install and use it, and where it stops.

The plugin's own README (plugins/databricks-genie/README.md) is shown next to the plugin, so it has to say the same
things. Development notes live in AGENTS.md, not here.
"""
import importlib.util
import json
import re
from pathlib import Path

import pytest

from tools import repo_checks

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN = REPO_ROOT / "plugins" / "databricks-genie"
README = REPO_ROOT / "README.md"
PLUGIN_README = PLUGIN / "README.md"
REPO_URL = "https://github.com/datasciencemonkey/antigravity-bricks-plugin"
INSTALL_AGY = "curl -fsSL https://antigravity.google/cli/install.sh | bash"
INSTALL_PLUGIN = f"agy plugin install {REPO_URL}"
WORKSPACE_LINE = "Asking Genie in <host> (Databricks CLI profile <profile>)"
HEADLESS_TOOL_NOT_ALLOWED = "genie_setup"
DOCUMENTS = [pytest.param(README, id="root"), pytest.param(PLUGIN_README, id="plugin")]
TRADEMARK = "Databricks and the Databricks logo are trademarks of Databricks, Inc., and the MIT license covers the code, not the marks."

# What both READMEs must state, word for word, so the plugin's README never contradicts the root one.
SHARED_FACTS = [
    INSTALL_AGY,
    INSTALL_PLUGIN,
    "desktop app 2.25 or later",
    "Python 3.9 or later",
    "macOS or Linux",
    "Windows is untested",
    WORKSPACE_LINE,
    "OAuth U2M",
    "tell the agent in the chat",
    "DATABRICKS_CONFIG_PROFILE=<name> agy",
    "open -a Antigravity --env DATABRICKS_CONFIG_PROFILE=<name>",
    "~/.gemini/antigravity-cli/settings.json",
    "unsupported_auth",
    "IP access list",
    "IDE 2.5.5",
    "doesn't run plugin MCP servers",
    "has no `~/.databrickscfg` yet, and that's fine: the first sign-in creates it",
    "the plugin never takes a workspace from your environment",
    "gets your environment unchanged and reads its usual `DATABRICKS_*` variables",
    TRADEMARK,
]


def read(path):
    return path.read_text()


def section(path, title):
    """The body of the `## title` section of a Markdown file."""
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", read(path), re.MULTILINE | re.DOTALL)
    assert match, f"{path.name} has no '## {title}' section"
    return match.group(1)


def manifest():
    return json.loads((PLUGIN / "plugin.json").read_text())


def troubleshooting_bullet(path, label):
    """The one Troubleshooting bullet of a README that starts with the bold label, label included."""
    found = re.findall(rf"^- \*\*{re.escape(label)}.*$", section(path, "Troubleshooting"), re.MULTILINE)
    assert len(found) == 1, f"{path.name}: {len(found)} Troubleshooting bullets start with {label!r}"
    return found[0]


def load_bridge():
    spec = importlib.util.spec_from_file_location("genie_bridge", PLUGIN / "scripts" / "genie_bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    return bridge


def tool_names_the_headless_rules_must_allow():
    """Genie One's four tools plus the bridge's own, except genie_setup (it opens a browser and writes the CLI config)."""
    genie_one = [tool["name"] for tool in json.loads((PLUGIN / "scripts" / "genie_tools.json").read_text())]
    return (set(genie_one) | {tool["name"] for tool in load_bridge().BRIDGE_TOOLS}) - {HEADLESS_TOOL_NOT_ALLOWED}


def test_the_readme_opens_with_the_plugins_name_and_says_what_it_does():
    text = read(README)
    intro = text.split("\n## ")[0]

    assert text.startswith("# Databricks Genie for Antigravity\n")
    for words in ("Genie", "SQL", "Antigravity"):
        assert words in intro
    assert "## What it does" in text


def test_the_requirements_name_the_app_the_workspace_python_git_and_the_operating_systems():
    requirements = section(README, "Requirements")

    for fact in ("desktop app 2.25 or later", "`agy`", "Genie One", "SQL warehouse", "Databricks CLI",
                 "Python 3.9 or later", "`git`", "macOS or Linux", "Windows is untested"):
        assert fact in requirements, fact


def test_the_install_steps_install_agy_then_the_plugin_from_the_repo_url_then_restart_the_app():
    install = section(README, "Install")

    assert install.index(INSTALL_AGY) < install.index(INSTALL_PLUGIN) < install.index("Restart the Antigravity desktop app")


def test_the_readme_says_how_to_check_update_and_remove_the_plugin():
    install = section(README, "Install")

    for command in ("agy plugin list", "agy plugin uninstall databricks-genie"):
        assert command in install


def test_updating_means_running_the_same_install_command_again_because_agy_has_no_update_command():
    install = section(README, "Install")

    assert f"To update, run the same `{INSTALL_PLUGIN}` command again, then restart Antigravity." in install
    assert "There is no `agy plugin update`" in install
    assert "turned on or off carries over" in install  # a reinstall keeps the plugin enabled or disabled as it was
    assert "run the install command again and restart the app" not in install  # the vaguer wording it replaces


def test_first_use_walks_through_the_databricks_cli_profile_check_and_the_sign_in():
    first_use = section(README, "First use")

    for fact in ("Databricks CLI profile", "workspace URL", "https://<workspace>.cloud.databricks.com", "never guesses",
                 "never picks a profile", "browser", "official installer", "only does so after you say yes",
                 "OAuth U2M", "safest", "single sign-on", WORKSPACE_LINE):
        assert fact in first_use, fact


def test_first_use_checks_the_databricks_cli_first_like_the_bridge_does():
    first_use = section(README, "First use")

    assert first_use.index("isn't installed") < first_use.index("is signed in") < first_use.index("workspace URL")


def test_first_use_says_the_user_tells_the_agent_when_the_sign_in_is_done_instead_of_promising_an_answer():
    first_use = section(README, "First use")

    assert "tell the agent in the chat" in first_use
    assert "Once you have, Genie answers" not in first_use


def test_the_readme_promises_only_the_line_the_agent_says_before_it_asks_not_one_that_ends_the_answer():
    # The bridge adds "Asked Genie in ..." to the genie_ask result, which on the polling path is only the
    # acknowledgement, so no answer is guaranteed to end with it.
    assert "Asked Genie in" not in read(README)


def test_try_it_lists_the_manifests_suggested_prompts_and_the_slash_command():
    try_it = section(README, "Try it")

    for prompt in manifest()["suggestedPrompts"]:
        assert f'- "{prompt}"' in try_it
    assert "/antigravity-databricks-genie" in try_it


def test_the_readme_explains_how_to_use_another_databricks_cli_profile():
    profile = section(README, "Use another Databricks CLI profile")

    for command in ("DATABRICKS_CONFIG_PROFILE", "DATABRICKS_CONFIG_PROFILE=<name> agy ...",
                    "open -a Antigravity --env DATABRICKS_CONFIG_PROFILE=<name>"):
        assert command in profile, command
    assert "quit the app fully" in profile and "that launch only" in profile


@pytest.mark.parametrize("path", DOCUMENTS)
def test_neither_readme_switches_profiles_with_launchctl_which_reaches_every_app_opened_until_logout(path):
    assert "launchctl" not in read(path)


@pytest.mark.parametrize("path", DOCUMENTS)
def test_the_headless_allow_rules_cover_the_genie_tools_and_leave_genie_setup_out(path):
    headless = section(path, "Headless runs")
    server = f"{manifest()['name']}_{next(iter(json.loads((PLUGIN / 'mcp_config.json').read_text())['mcpServers']))}"

    allowed = set(re.findall(rf'"mcp\({re.escape(server)}/(\w+)\)"', headless))

    assert allowed == tool_names_the_headless_rules_must_allow()
    assert "agy -p" in headless and "permissions" in headless and "allow" in headless
    assert HEADLESS_TOOL_NOT_ALLOWED in headless  # it says that genie_setup stays out, and why


def test_the_readme_says_the_ide_is_not_supported_yet_and_why():
    where = section(README, "Where it works")

    for fact in ("`agy` CLI", "desktop app", "IDE 2.5.5", "not supported yet", "doesn't run plugin MCP servers",
                 "manifest", "/issues/30"):
        assert fact in where, fact


def test_the_security_section_says_how_sign_in_works_and_what_is_stored():
    security = section(README, "Security")

    for fact in ("OAuth U2M", "store no secrets", "never printed", "your permissions", "OAuth M2M", "unsupported_auth",
                 "profile_exists_unreadable", "/issues/39"):
        assert fact in security, fact


def test_security_says_which_file_the_plugin_writes_and_that_it_never_holds_a_token(tmp_path, monkeypatch):
    security = section(README, "Security")
    monkeypatch.setenv("HOME", str(tmp_path))  # the bridge keeps its sign-in record under the home folder
    record = Path(load_bridge().login_file("DEFAULT", create=True))
    folder = f"~/{record.parent.relative_to(tmp_path).as_posix()}/"

    assert folder == "~/.cache/antigravity-databricks-genie/"  # the folder the README names is the one the bridge uses
    assert folder in security
    for fact in ("only file the plugin itself writes", "process id", "never a token", "only you can use"):
        assert fact in security, fact


def test_troubleshooting_covers_403_unsupported_auth_timeouts_and_polling_again():
    troubleshooting = section(README, "Troubleshooting")

    for fact in ("403", "VPN", "IP access list", "unsupported_auth", "timed out", "poll", "same"):
        assert fact in troubleshooting, fact


@pytest.mark.parametrize(
    "label", ["`profile_exists_unreadable`:", '"Genie returned a non-JSON page":', "A question times out:",
              "`DATABRICKS_HOST` or `DATABRICKS_TOKEN` in your shell:"]
)
def test_troubleshooting_explains_the_follow_up_failures_in_the_same_words_in_both_readmes(label):
    assert troubleshooting_bullet(README, label) == troubleshooting_bullet(PLUGIN_README, label)


@pytest.mark.parametrize("path", DOCUMENTS)
def test_a_question_that_times_out_is_explained_with_the_limits_and_the_duplicate_warning(path):
    entry = troubleshooting_bullet(path, "A question times out:")

    assert "3 minutes" in entry and "150 seconds" in entry
    assert "polls again with the same IDs" in entry  # a timed-out poll still has its IDs
    assert "its IDs never came back, and asking again starts a duplicate question" in entry  # a timed-out genie_ask doesn't


@pytest.mark.parametrize("path", DOCUMENTS)
def test_the_timeout_entry_covers_follow_ups_results_and_a_connection_that_never_happened(path):
    entry = troubleshooting_bullet(path, "A question times out:")

    assert "For a follow-up, which continues an earlier conversation, it first cancels the question still running there" in entry
    assert "A timeout while fetching a full result or cancelling starts no question, so the agent just tries again" in entry
    assert "the request wasn't sent and nothing runs twice" in entry
    assert "network and the VPN" in entry


@pytest.mark.parametrize("path", DOCUMENTS)
def test_profile_exists_unreadable_says_keys_count_an_empty_header_does_not_and_headerless_keys_are_defaults(path):
    entry = troubleshooting_bullet(path, "`profile_exists_unreadable`:")

    for fact in ("holds keys for it", "reads only key names, never a value",
                 "Keys that come before the first header belong to the `DEFAULT` profile",
                 "A header with no keys under it holds nothing to lose",
                 "the empty `[DEFAULT]` header that `databricks configure` writes"):
        assert fact in entry, fact
    assert "section names" not in entry  # the bridge reads key names now


@pytest.mark.parametrize("path", DOCUMENTS)
def test_use_another_profile_says_the_workspace_comes_from_the_profile_and_the_cli_keeps_its_environment(path):
    profile = section(path, "Use another Databricks CLI profile")

    for fact in ("always called at the workspace of the Databricks CLI profile it uses",
                 "the plugin never takes a workspace from your environment, so an exported `DATABRICKS_HOST` doesn't change "
                 "where it calls",
                 "The plugin itself reads only `DATABRICKS_CONFIG_PROFILE` and `DATABRICKS_CONFIG_FILE`",
                 "The Databricks CLI it runs gets your environment unchanged and reads its usual `DATABRICKS_*` variables, "
                 "such as `DATABRICKS_AUTH_STORAGE`, as it does in your terminal"):
        assert fact in profile, fact


@pytest.mark.parametrize("path", DOCUMENTS)
def test_the_readmes_no_longer_say_the_plugin_ignores_or_withholds_databricks_variables(path):
    # The CLI owns its environment variables. An earlier wording had the plugin drop them before it ran the CLI.
    for old in ("sign-in variables in your environment are ignored", "doesn't pass them to the Databricks CLI",
                "doesn't pass it to the Databricks CLI", "the only place the profile's token goes"):
        assert old not in read(path), old


@pytest.mark.parametrize("path", DOCUMENTS)
def test_troubleshooting_says_to_unset_a_databricks_host_or_token_exported_for_another_workspace(path):
    entry = troubleshooting_bullet(path, "`DATABRICKS_HOST` or `DATABRICKS_TOKEN` in your shell:")

    for fact in ("the Databricks CLI reads them, as it does in your terminal",
                 "If your shell exports either one for another workspace, Genie can report a sign-in error",
                 "Unset them in the environment Antigravity starts from", "restart Antigravity",
                 "ask the agent to check the Genie connection"):
        assert fact in entry, fact


def test_the_security_section_says_where_the_token_goes_and_when_a_profile_is_profile_exists_unreadable():
    security = section(README, "Security")
    (where,) = [line for line in security.splitlines() if line.startswith("- **Where your token goes:**")]

    for fact in ("only to the workspace of the Databricks CLI profile the plugin uses",
                 "never takes a workspace from your environment", "an exported `DATABRICKS_HOST` can't make it call, or send "
                 "a token to, another host"):
        assert fact in where, fact
    assert "one that the CLI doesn't list but that has settings in the Databricks config file gets `profile_exists_unreadable`" in security


@pytest.mark.parametrize("path", DOCUMENTS)
def test_first_use_says_a_machine_with_no_config_file_can_set_up(path):
    first_use = section(path, "First use")

    assert "A machine that has never used the Databricks CLI has no `~/.databrickscfg` yet, and that's fine: the first sign-in creates it." in first_use


@pytest.mark.parametrize("path", DOCUMENTS)
def test_the_user_docs_leave_out_the_bridges_test_hooks(path):
    text = read(path)

    assert "GENIE_MCP_URL" not in text and "GENIE_BRIDGE_DATABRICKS_CLI" not in text
    assert not re.search(r"(?<![A-Z0-9_])DATABRICKS_CLI(?![A-Z0-9_])", text)  # the name the CLI's path hook had before


@pytest.mark.parametrize("path", DOCUMENTS)
def test_the_older_entry_that_said_to_ask_the_agent_to_check_again_is_merged_into_the_one_above(path):
    # It told readers the agent polls with the same IDs, which a question that timed out never got.
    assert "**A call timed out:**" not in section(path, "Troubleshooting")


def test_whats_in_this_repo_comes_just_before_development_and_tells_users_the_install_url_is_all_they_need():
    headings = re.findall(r"^## (.*)$", read(README), re.MULTILINE)
    body = section(README, "What's in this repo")

    assert headings[headings.index("Development") - 1] == "What's in this repo"
    assert "only the install URL" in body
    assert "a manifest, the bridge script, the skill, the rule and the logo" in body
    assert "for developing it" in body


def test_whats_in_this_repo_names_only_files_and_folders_that_exist():
    body = section(README, "What's in this repo")

    for path in ("plugins/databricks-genie/", "tests/", "tools/", "scripts/hooks/", ".github/workflows/", "pyproject.toml", "uv.lock",
                 "AGENTS.md", "docs/progress.md"):
        assert f"`{path}`" in body, path
        assert (REPO_ROOT / path).exists(), path
    for part in ("plugin.json", "scripts/genie_bridge.py", "skills/antigravity-databricks-genie/SKILL.md", "rules/AGENTS.md",
                 "assets/logo.png"):  # the plugin's manifest, bridge script, skill, rule and logo
        assert (PLUGIN / part).is_file(), part


def test_whats_in_this_repo_stays_short():
    assert len(section(README, "What's in this repo").strip().splitlines()) <= 8


def test_development_points_at_the_test_command_and_agents_md():
    development = section(README, "Development")

    assert "uv run --locked pytest" in development
    assert "[AGENTS.md](AGENTS.md)" in development


def test_the_license_section_says_mit_and_the_license_file_is_the_mit_license():
    assert "MIT" in section(README, "License")
    assert read(REPO_ROOT / "LICENSE").startswith("MIT License")


@pytest.mark.parametrize("path", DOCUMENTS)
def test_the_license_section_says_the_mit_license_covers_the_code_and_not_the_databricks_marks(path):
    # The plugin's logo is the Databricks icon, and an MIT license grants nothing in a trademark.
    assert TRADEMARK in section(path, "License")


def test_the_readme_uses_workspace_placeholders_never_real_hostnames():
    text = read(README)

    assert "https://<workspace>.cloud.databricks.com" in text
    assert repo_checks.find_workspace_hostnames(text, "README.md") == []


def test_the_readme_says_databricks_cli_profile_so_nobody_reads_it_as_an_agy_setting():
    assert re.findall(r"(?<!Databricks )CLI profile", read(README)) == []


def test_the_readmes_relative_links_point_at_files_in_the_repo():
    for target in re.findall(r"\]\((?!https?://|#)([^)\s]+)\)", read(README)):
        assert (REPO_ROOT / target.split("#")[0]).exists(), target


@pytest.mark.parametrize("fact", SHARED_FACTS)
def test_the_root_readme_and_the_plugins_readme_both_state(fact):
    assert fact in read(README), "the root README"
    assert fact in read(PLUGIN_README), "the plugin's README"


def test_the_plugins_readme_does_not_promise_the_ide_shares_the_setup():
    text = read(PLUGIN_README)

    assert "the IDE all use" not in text and "desktop app or the IDE" not in text

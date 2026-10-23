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
    "export DATABRICKS_CONFIG_PROFILE=<name>",
    "launchctl setenv DATABRICKS_CONFIG_PROFILE <name>",
    "launchctl unsetenv DATABRICKS_CONFIG_PROFILE",
    "~/.gemini/antigravity-cli/settings.json",
    "unsupported_auth",
    "IP access list",
    "IDE 2.5.5",
    "doesn't run plugin MCP servers",
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
                 "OAuth U2M", "safest", "single sign-on", WORKSPACE_LINE, "Asked Genie in <host>"):
        assert fact in first_use, fact


def test_try_it_lists_the_manifests_suggested_prompts_and_the_slash_command():
    try_it = section(README, "Try it")

    for prompt in manifest()["suggestedPrompts"]:
        assert f'- "{prompt}"' in try_it
    assert "/antigravity-databricks-genie" in try_it


def test_the_readme_explains_how_to_use_another_databricks_cli_profile():
    profile = section(README, "Use another Databricks CLI profile")

    for command in ("DATABRICKS_CONFIG_PROFILE", "export DATABRICKS_CONFIG_PROFILE=<name>",
                    "launchctl setenv DATABRICKS_CONFIG_PROFILE <name>", "launchctl unsetenv DATABRICKS_CONFIG_PROFILE"):
        assert command in profile, command
    assert "restart" in profile.lower()


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


@pytest.mark.parametrize("label", ["`profile_exists_unreadable`:", '"Genie returned a non-JSON page":'])
def test_troubleshooting_explains_the_follow_up_failures_in_the_same_words_as_the_plugins_readme(label):
    assert troubleshooting_bullet(README, label) == troubleshooting_bullet(PLUGIN_README, label)


def test_a_question_that_times_out_is_explained_as_in_the_plugins_readme_with_the_duplicate_warning():
    label = "A question times out:"
    root = troubleshooting_bullet(README, label)
    plugin_words = troubleshooting_bullet(PLUGIN_README, label).split(":**", 1)[1].strip()

    assert plugin_words in root
    assert "3 minutes" in root and "150 seconds" in root
    assert "starts a duplicate question" in root


def test_the_older_entry_that_said_to_ask_the_agent_to_check_again_is_merged_into_the_one_above():
    # It told readers to poll with the same IDs, which a question that timed out never got.
    assert "**A call timed out:**" not in section(README, "Troubleshooting")


def test_development_points_at_the_test_command_and_agents_md():
    development = section(README, "Development")

    assert "uv run --locked pytest" in development
    assert "[AGENTS.md](AGENTS.md)" in development


def test_the_license_section_says_mit_and_the_license_file_is_the_mit_license():
    assert "MIT" in section(README, "License")
    assert read(REPO_ROOT / "LICENSE").startswith("MIT License")


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

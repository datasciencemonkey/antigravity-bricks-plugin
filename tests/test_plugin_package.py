"""The Databricks Genie plugin package: the files Antigravity actually loads, and nothing it would drop or choke on."""
import importlib.util
import json
import re
import shutil
import struct
import subprocess
from pathlib import Path


import yaml

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "plugins" / "databricks-genie"
SKILL = "antigravity-databricks-genie"
GENIE_ONE_TOOLS = ["genie_ask", "genie_poll_response", "genie_get_query_result", "genie_cancel_response"]
STATUSES = ["ready", "not_configured", "login_needed", "login_in_progress", "login_failed", "cli_missing",
            "unsupported_auth", "profile_exists_unreadable"]
WORKSPACE_LINE = "Asking Genie in <host> (Databricks CLI profile <profile>)"
# The shipped loader reads only these keys. It silently drops everything else, including the metadata the publishing
# guide asks for (`author`), recommends (`homepage`, `license`, `keywords`) or allows (`repository`).
LOADER_KEYS = {"name", "description", "displayName", "logo", "suggestedPrompts", "version", "disabled", "skills"}
# `agy plugin validate --strict` accepts those guide fields, so they may ride along. It rejects `category` and `tags`
# (`unknown field ... is ignored`), which the guide also recommends, so they stay out.
GUIDE_KEYS = {"author", "homepage", "keywords", "license", "repository"}
AUTHOR = {"name": "Sathish Gangichetty", "url": "https://github.com/datasciencemonkey"}  # no email, on purpose
# A prompt chip is the first message of a new chat, so none may lean on an earlier answer.
BACK_REFERENCE = re.compile(r"\b(that|those|them|above|previous|earlier|again|same)\b", re.IGNORECASE)
IDENTIFIER_CHARACTERS = re.compile(r"[`_.]")  # backticks, underscores and dots mark table, column and file names
PLACEHOLDERS = {"PLUGIN_ROOT", "PLUGIN_DATA"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_RGBA = 6  # the IHDR color type for red, green, blue and alpha


def manifest():
    return json.loads((PLUGIN / "plugin.json").read_text())


def mcp_config():
    return json.loads((PLUGIN / "mcp_config.json").read_text())


def frontmatter(path):
    _, front, _ = path.read_text().split("---", 2)
    return yaml.safe_load(front)


def test_manifest_name_is_kebab_case_and_matches_the_plugin_folder():
    assert manifest()["name"] == PLUGIN.name
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", manifest()["name"])


def test_manifest_uses_only_keys_the_loader_reads_or_the_guide_asks_for_and_the_validator_accepts():
    assert set(manifest()) <= LOADER_KEYS | GUIDE_KEYS


def test_manifest_has_the_marketplace_presentation_fields():
    m = manifest()
    assert m["displayName"] == "Databricks Genie"
    assert re.fullmatch(r"\d+\.\d+\.\d+", m["version"])
    assert 60 <= len(m["description"]) <= 160
    assert 1 <= len(m["suggestedPrompts"]) <= 3 and all(p.strip() for p in m["suggestedPrompts"])


def test_the_manifest_names_its_author_with_a_name_and_a_url_but_no_email():
    # The guide requires `author` for the Marketplace. The loader drops it, but the validator accepts it.
    assert manifest()["author"] == AUTHOR


def test_the_plugin_carries_the_repos_mit_license_and_the_manifest_says_so():
    # A subfolder install copies only the plugin's folder, so the license text has to travel inside it.
    assert manifest()["license"] == "MIT"
    assert (PLUGIN / "LICENSE").read_text() == (REPO / "LICENSE").read_text()


def test_each_suggested_prompt_works_on_its_own_in_a_new_chat_and_names_no_dataset():
    prompts = manifest()["suggestedPrompts"]

    assert len(prompts) == 3
    for prompt in prompts:
        assert "Genie" in prompt, prompt
        assert not BACK_REFERENCE.search(prompt), prompt
        assert not IDENTIFIER_CHARACTERS.search(prompt), prompt


def test_logo_is_a_bundled_square_rgba_png_of_at_least_128_pixels():
    # The guide asks for a square logo, 128 x 128 or larger, with transparent padding (so an alpha channel). The PNG's
    # own header says all of that: signature, then the IHDR chunk (length, type, width, height, bit depth, color type).
    logo = PLUGIN / manifest()["logo"]
    data = logo.read_bytes()

    assert logo.suffix == ".png" and data.startswith(PNG_SIGNATURE)
    length, chunk_type = struct.unpack(">I4s", data[8:16])
    assert (length, chunk_type) == (13, b"IHDR")
    width, height, _bit_depth, color_type = struct.unpack(">IIBB", data[16:26])
    assert width == height >= 128
    assert color_type == PNG_RGBA


def test_the_only_mcp_server_is_the_local_genie_bridge():
    (bridge,) = mcp_config()["mcpServers"].values()
    server = bridge
    # Python, not Node: Antigravity doesn't give plugin servers its bundled Node, and every developer Mac has python3.
    assert server["command"] == "python3"
    assert server["args"] == ["${PLUGIN_ROOT}/scripts/genie_bridge.py"]
    assert (PLUGIN / "scripts" / "genie_bridge.py").is_file()


def server():
    (only,) = mcp_config()["mcpServers"].values()
    return only


def test_the_server_is_named_genie_so_it_shows_up_as_databricks_genie_genie():
    assert list(mcp_config()["mcpServers"]) == ["genie"]


def test_the_server_needs_no_settings_so_it_starts_right_after_install():
    # A server that declares `variables` stays off until the user fills them in, and Antigravity stores them as
    # plain text. The bridge reads the workspace and sign-in from the Databricks CLI profile instead.
    assert set(mcp_config()) == {"mcpServers"}
    assert "variables" not in server() and "headers" not in server()
    used = set(re.findall(r"\$\{([A-Z0-9_]+)\}", json.dumps(server()))) - PLACEHOLDERS
    assert used == set()


def test_plugin_never_uses_the_retired_genie_endpoint():
    assert PLUGIN.is_dir()
    retired = "/api/2.0/mcp/" + "genie"
    offenders = [p for p in PLUGIN.rglob("*") if p.is_file() and retired in p.read_text(errors="ignore")]
    assert offenders == []


def test_skills_have_frontmatter_the_loader_accepts():
    skills = sorted((PLUGIN / "skills").glob("*/SKILL.md"))
    assert skills
    for skill in skills:
        front = frontmatter(skill)
        assert front["name"] == skill.parent.name
        assert 1 <= len(front["description"]) <= 1024


def test_the_plugin_ships_exactly_one_skill_and_it_is_antigravity_databricks_genie():
    folders = [path.name for path in (PLUGIN / "skills").iterdir() if path.is_dir()]

    assert folders == [SKILL]


def test_the_skill_is_named_antigravity_databricks_genie_so_its_slash_command_is_too():
    assert frontmatter(PLUGIN / "skills" / SKILL / "SKILL.md")["name"] == SKILL


def test_the_rule_and_readme_point_at_the_skill_by_its_current_name():
    for document in ("rules/AGENTS.md", "README.md"):
        assert f"`{SKILL}`" in (PLUGIN / document).read_text()


def test_the_always_on_rule_steers_data_questions_to_genie():
    rule = (PLUGIN / "rules" / "AGENTS.md").read_text()
    assert "genie_ask" in rule


def test_readme_lists_the_prerequisites():
    readme = (PLUGIN / "README.md").read_text()
    for prerequisite in ("databricks auth login", "agy plugin install", "CAN USE", "Python 3.9"):
        assert prerequisite in readme


def test_antigravitys_own_strict_validator_finds_nothing_wrong():
    # The Antigravity CLI is a prerequisite for this suite; CI installs it.
    agy = shutil.which("agy") or str(Path.home() / ".local" / "bin" / "agy")
    result = subprocess.run([agy, "plugin", "validate", str(PLUGIN), "--strict", "--json"], capture_output=True, text=True)
    report = json.loads(result.stdout[result.stdout.index("{"):result.stdout.rindex("}") + 1])
    # Strict means no errors or warnings. Notes are informational (e.g. "off until the user fills in its settings").
    assert [f for f in report["findings"] if f["severity"] != "note"] == []
    assert result.returncode == 0
    assert report["components"]["mcpServers"] == 1 and report["components"]["skills"] >= 1


def test_the_skill_names_the_server_antigravity_registers_so_the_agent_can_call_genie_directly():
    # Antigravity registers plugin servers as <plugin>_<server> and exposes their tools through call_mcp_tool.
    skill = (PLUGIN / "skills" / SKILL / "SKILL.md").read_text()
    assert "call_mcp_tool" in skill
    assert f"{manifest()['name']}_{next(iter(mcp_config()['mcpServers']))}" in skill


def test_the_skill_says_to_poll_again_with_the_same_ids_when_a_poll_times_out():
    skill = (PLUGIN / "skills" / SKILL / "SKILL.md").read_text()
    (step,) = [line.lower() for line in skill.splitlines() if "genie_poll_response" in line]

    assert "time" in step and "poll again" in step and "same" in step


def test_the_skill_says_that_asking_again_after_a_genie_ask_times_out_starts_a_duplicate_question():
    # The timed-out question keeps running in Genie and its IDs never come back, so there is nothing to poll with.
    (step,) = [line.lower() for line in skill_text().splitlines() if "duplicate" in line.lower()]

    assert "`genie_ask`" in step and "times out" in step and "wait" in step


def test_the_skill_says_to_cancel_a_follow_up_that_timed_out_before_asking_again():
    # A follow-up has a conversation_id, and genie_cancel_response is conversation-scoped.
    (step,) = [line.lower() for line in skill_text().splitlines() if "duplicate" in line.lower()]

    assert "follow-up" in step and "`genie_cancel_response`" in step and "`conversation_id`" in step
    assert step.index("`genie_cancel_response`") < step.index("ask once more")


def skill_text():
    return (PLUGIN / "skills" / SKILL / "SKILL.md").read_text()


def rule_text():
    return (PLUGIN / "rules" / "AGENTS.md").read_text()


def readme_text():
    return (PLUGIN / "README.md").read_text()


def status_step(status):
    """The skill's step for one genie_connection status: the list item that starts with it."""
    (item,) = [line for line in skill_text().splitlines() if line.startswith(f"- `{status}`")]
    return item


def test_the_bundled_genie_tools_are_a_copy_of_genie_ones_four_tools():
    # The bridge lists these before Genie One can be reached, so the tools show up before any sign-in.
    tools = json.loads((PLUGIN / "scripts" / "genie_tools.json").read_text())

    assert [tool["name"] for tool in tools] == GENIE_ONE_TOOLS
    for tool in tools:
        assert tool["description"].strip()
        assert tool["inputSchema"]["type"] == "object" and tool["inputSchema"]["properties"]
    assert tools[0]["inputSchema"]["required"] == ["question"]


def test_the_bridge_reports_the_plugins_version():
    spec = importlib.util.spec_from_file_location("genie_bridge", PLUGIN / "scripts" / "genie_bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)

    assert bridge.VERSION == manifest()["version"]


def test_the_skill_checks_the_connection_once_per_conversation():
    skill = skill_text()

    assert "`genie_connection`" in skill and "once per conversation" in skill


def test_the_skill_covers_every_status_genie_connection_reports():
    for status in STATUSES:
        assert status_step(status)


def test_the_skill_shows_the_workspace_and_profile_before_each_genie_ask():
    (step,) = [line for line in skill_text().splitlines() if "before each `genie_ask`" in line.lower()]

    assert WORKSPACE_LINE in step


def test_the_skill_asks_the_user_for_the_workspace_url_when_the_profile_is_not_set_up():
    step = status_step("not_configured").lower()

    assert "workspace url" in step and "never guess" in step and "browser" in step and "`genie_setup`" in step


def test_the_skill_explains_the_cli_sign_in_the_first_time_it_asks_for_the_workspace_url():
    # Users who already have the CLI never see the install ask, so they get the same short explanation here.
    step = status_step("not_configured").lower()

    assert "oauth u2m" in step and "safest" in step and "token" in step


def test_the_skill_never_picks_a_profile_or_workspace_url_for_the_user():
    assert "never pick a profile or a workspace url for the user" in skill_text().lower()


def test_the_skill_asks_before_installing_the_databricks_cli():
    step = status_step("cli_missing")
    lower = step.lower()

    assert "official installer" in lower and "may i install" in lower
    assert "only after the user approves" in lower and "never install it silently" in lower
    for command in ("brew install databricks/tap/databricks",
                    "curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh",
                    "winget install Databricks.DatabricksCLI"):
        assert command in step


def test_the_skills_install_step_says_the_cli_is_the_safest_way_to_run_oauth_u2m_locally():
    step = status_step("cli_missing").lower()

    assert "oauth u2m" in step and "safest" in step and "own machine" in step
    assert "single sign-on" in step and "short-lived" in step


def test_the_always_on_rule_checks_the_connection_and_shows_the_workspace_before_each_question():
    rule = rule_text()

    assert "`genie_connection`" in rule and "once per conversation" in rule
    assert WORKSPACE_LINE in rule


def test_the_always_on_rule_sets_up_a_missing_profile_without_picking_one():
    rule = rule_text().lower()

    assert "workspace url" in rule and "browser" in rule and "`genie_setup`" in rule
    assert "never pick a profile or a workspace url for the user" in rule


def test_the_always_on_rule_installs_the_cli_only_after_the_user_approves():
    (line,) = [line for line in rule_text().splitlines() if "install" in line.lower()]
    lower = line.lower()

    assert "official installer" in lower and "only after the user approves" in lower and "oauth u2m" in lower


def test_readme_explains_the_first_use_flow():
    readme = readme_text()

    assert "## First use" in readme
    for words in ("`genie_connection`", "`genie_setup`", "workspace URL", "browser", "never changes"):
        assert words in readme


def test_readme_explains_how_to_switch_cli_profiles():
    readme = readme_text()

    assert "DATABRICKS_CONFIG_PROFILE=<name> agy" in readme
    assert "open -a Antigravity --env DATABRICKS_CONFIG_PROFILE=<name>" in readme  # this launch only
    assert "launchctl" not in readme  # setenv would reach every app opened until logout, Terminal included


def test_readme_try_it_lists_the_manifests_suggested_prompts_word_for_word():
    readme = readme_text()

    for prompt in manifest()["suggestedPrompts"]:
        assert f'- "{prompt}"' in readme


def test_readme_names_the_license():
    readme = readme_text()

    assert "## License" in readme and "MIT" in readme


def test_the_skill_quotes_the_line_the_bridge_adds_to_each_genie_ask_result():
    assert "Asked Genie in <host> (Databricks CLI profile <profile>)" in skill_text()


def test_the_progress_notes_quote_the_line_the_bridge_adds_to_each_genie_ask_result_and_not_the_one_the_agent_says():
    # The agent says "Asking Genie in ..." before a question; the bridge ends the result with "Asked Genie in ...".
    progress = (PLUGIN.parent.parent / "docs" / "progress.md").read_text()

    assert "Asked Genie in <host> (Databricks CLI profile <profile>)" in progress
    assert "the same line" not in progress


def test_the_plugin_docs_say_databricks_cli_profile_so_nobody_reads_it_as_an_agy_setting():
    for document in ("README.md", "rules/AGENTS.md", f"skills/{SKILL}/SKILL.md"):
        assert re.findall(r"(?<!Databricks )CLI profile", (PLUGIN / document).read_text()) == [], document


def test_the_skill_leaves_a_profile_that_signs_in_another_way_alone():
    step = status_step("unsupported_auth")

    assert "OAuth U2M" in step and "DATABRICKS_CONFIG_PROFILE" in step and "never" in step.lower()


def test_the_skill_leaves_a_profile_the_cli_does_not_list_alone_and_suggests_a_separate_one():
    step = status_step("profile_exists_unreadable")

    assert "DATABRICKS_CONFIG_PROFILE" in step and "separate profile" in step and "never" in step.lower()
    assert "secret" in step


def test_genie_connections_own_description_names_every_status_it_can_report():
    spec = importlib.util.spec_from_file_location("genie_bridge", PLUGIN / "scripts" / "genie_bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    (tool,) = [tool for tool in bridge.BRIDGE_TOOLS if tool["name"] == "genie_connection"]

    assert [status for status in STATUSES if status not in tool["description"]] == []

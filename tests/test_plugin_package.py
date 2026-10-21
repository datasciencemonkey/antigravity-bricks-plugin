"""The Databricks Genie plugin package: the files Antigravity 2.24.0 actually loads, and nothing it would drop or choke on."""
import json
import re
import shutil
import subprocess
from pathlib import Path


import yaml

PLUGIN = Path(__file__).resolve().parent.parent / "plugins" / "databricks-genie"
# The 2.24.0 loader reads only these keys; `author` and `homepage` are silently dropped.
LOADER_KEYS = {"name", "description", "displayName", "logo", "suggestedPrompts", "version", "disabled", "skills"}
PLACEHOLDERS = {"PLUGIN_ROOT", "PLUGIN_DATA"}


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


def test_manifest_uses_only_keys_the_loader_reads():
    assert set(manifest()) <= LOADER_KEYS


def test_manifest_has_the_marketplace_presentation_fields():
    m = manifest()
    assert m["displayName"] == "Databricks Genie"
    assert re.fullmatch(r"\d+\.\d+\.\d+", m["version"])
    assert 60 <= len(m["description"]) <= 160
    assert 1 <= len(m["suggestedPrompts"]) <= 3 and all(p.strip() for p in m["suggestedPrompts"])


def test_logo_is_a_bundled_svg():
    logo = PLUGIN / manifest()["logo"]
    assert logo.suffix == ".svg" and logo.read_text().lstrip().startswith("<svg")


def test_the_only_mcp_server_is_the_local_genie_bridge():
    (bridge,) = mcp_config()["mcpServers"].values()
    server = bridge
    assert server["command"] == "node"
    assert server["args"] == ["${PLUGIN_ROOT}/scripts/genie-bridge.mjs"]
    assert (PLUGIN / "scripts" / "genie-bridge.mjs").is_file()


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


def test_the_always_on_rule_steers_data_questions_to_genie():
    rule = (PLUGIN / "rules" / "AGENTS.md").read_text()
    assert "genie_ask" in rule


def test_readme_lists_the_prerequisites():
    readme = (PLUGIN / "README.md").read_text()
    for prerequisite in ("databricks auth login", "agy plugin install", "CAN USE"):
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
    skill = (PLUGIN / "skills" / "genie" / "SKILL.md").read_text()
    assert "call_mcp_tool" in skill
    assert f"{manifest()['name']}_{next(iter(mcp_config()['mcpServers']))}" in skill

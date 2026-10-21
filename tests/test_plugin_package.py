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
    (server,) = mcp_config()["mcpServers"].values()
    assert server["command"] == "node"
    assert server["args"] == ["${PLUGIN_ROOT}/scripts/genie-bridge.mjs"]
    assert (PLUGIN / "scripts" / "genie-bridge.mjs").is_file()


def server():
    (only,) = mcp_config()["mcpServers"].values()
    return only


def test_settings_live_on_the_server_entry_not_at_the_top_level():
    # 2.24.0 reads `variables` per server (McpServerSpec.variables); a top-level `variables` is ignored.
    assert set(mcp_config()) == {"mcpServers"}


def test_every_setting_the_server_uses_is_declared_and_described():
    settings_free = {key: value for key, value in server().items() if key != "variables"}
    used = set(re.findall(r"\$\{([A-Z0-9_]+)\}", json.dumps(settings_free))) - PLACEHOLDERS
    declared = {v["name"]: v for v in server()["variables"]}
    assert used == set(declared)
    for variable in declared.values():
        assert set(variable) == {"name", "title", "description", "link"}


def test_no_setting_holds_a_secret():
    # Antigravity stores settings as plain text, so sign-in has to come from the Databricks CLI login instead.
    names = [v["name"] for v in server()["variables"]]
    assert not [n for n in names if re.search(r"TOKEN|SECRET|PASSWORD|PAT\b", n)]


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

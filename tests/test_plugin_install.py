"""Install the plugin from the repo the way a user does, and check what Antigravity found.

`agy plugin install <repo root>` finds plugins/databricks-genie/ as a plugins directory, the same detection that makes
`agy plugin install https://github.com/<owner>/<repo>` work. Every agy call here runs with HOME set to a fresh temporary
folder, so nothing it reads or writes is the real ~/.gemini.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN = REPO_ROOT / "plugins" / "databricks-genie"


def component_names(folder, folders_only=False):
    """What a plugin folder holds, by name. Dotfiles are left out: the Finder drops a .DS_Store into any folder it opens,
    and agy doesn't list it as a skill or a rule."""
    return sorted(
        path.name for path in folder.iterdir()
        if not path.name.startswith(".") and (path.is_dir() or not folders_only)
    )


def find_agy():
    fallback = Path.home() / ".local" / "bin" / "agy"  # where the installer puts it, before a new shell has it on PATH
    return shutil.which("agy") or (str(fallback) if fallback.is_file() else None)


class Agy:
    """The Antigravity CLI, run with HOME set to an empty folder."""

    def __init__(self, binary, home):
        self.binary = binary
        self.home = home

    def run(self, *args):
        # Only HOME and PATH go in, so no token or Databricks setting from this machine reaches agy.
        env = {"HOME": str(self.home), "PATH": os.environ["PATH"], "GIT_TERMINAL_PROMPT": "0"}
        return subprocess.run(
            [self.binary, *args], cwd=self.home, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=120,
        )

    def plugins(self):
        listed = self.run("plugin", "list", "--json")
        assert listed.returncode == 0, listed.stderr
        return json.loads(listed.stdout)


@pytest.fixture
def agy(tmp_path):
    binary = find_agy()
    if binary is None:
        pytest.skip("the Antigravity CLI (agy) is on neither PATH nor ~/.local/bin")
    home = tmp_path / "home"
    home.mkdir()
    cli = Agy(binary, home)
    # If agy ignored HOME, this would list the plugins really installed on this machine, and installing would change
    # them. Stop before anything is installed.
    assert cli.plugins() == [], "agy listed plugins in an empty HOME, so it isn't using HOME; nothing was installed"
    return cli


def test_component_names_leave_out_dotfiles_such_as_the_ds_store_the_finder_leaves_behind(tmp_path):
    (tmp_path / "skills" / "real-skill").mkdir(parents=True)
    (tmp_path / "skills" / ".DS_Store").write_text("")
    (tmp_path / "skills" / "stray.txt").write_text("")
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "AGENTS.md").write_text("")
    (tmp_path / "rules" / ".DS_Store").write_text("")

    assert component_names(tmp_path / "skills", folders_only=True) == ["real-skill"]
    assert component_names(tmp_path / "rules") == ["AGENTS.md"]


def test_installing_the_repo_gives_the_plugin_its_skill_rule_and_mcp_server_at_the_current_version(agy):
    installed = agy.run("plugin", "install", str(REPO_ROOT))
    assert installed.returncode == 0, installed.stdout + installed.stderr

    listed = agy.plugins()
    assert [plugin["name"] for plugin in listed].count("databricks-genie") == 1, listed
    (plugin,) = [plugin for plugin in listed if plugin["name"] == "databricks-genie"]
    assert plugin["version"] == json.loads((PLUGIN / "plugin.json").read_text())["version"]
    assert plugin["enabled"] is True
    # agy leaves "skills" out of its report when it loaded none (seen with a skill whose frontmatter it couldn't read).
    assert sorted(plugin.get("skills", [])) == component_names(PLUGIN / "skills", folders_only=True)
    assert sorted(plugin.get("rules", [])) == component_names(PLUGIN / "rules")
    servers = json.loads((PLUGIN / "mcp_config.json").read_text())["mcpServers"]
    assert sorted(plugin.get("mcpServers", [])) == sorted(servers)
    assert Path(plugin["path"]).resolve().is_relative_to(agy.home.resolve())  # it went into the temporary HOME

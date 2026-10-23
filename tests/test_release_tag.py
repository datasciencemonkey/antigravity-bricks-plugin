"""tools/release_tag.py: the release tag is "v" plus the manifest's version, and a version that isn't three numbers stops the release."""
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tools import release_tag

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "plugins" / "databricks-genie" / "plugin.json"
SCRIPT = REPO_ROOT / "tools" / "release_tag.py"


def manifest_with(tmp_path, content):
    path = tmp_path / "plugin.json"
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    return path


def run_main(path, capsys):
    code = release_tag.main([str(path)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_prints_v_and_the_version_from_the_manifest(tmp_path, capsys):
    code, out, err = run_main(manifest_with(tmp_path, {"name": "databricks-genie", "version": "0.3.1"}), capsys)

    assert (code, out, err) == (0, "v0.3.1\n", "")


def test_the_tag_printed_for_the_real_manifest_is_v_plus_its_version(tmp_path):
    version = json.loads(MANIFEST.read_text())["version"]

    # Run from another folder: the tool finds the manifest from its own location, not from the working directory.
    result = subprocess.run([sys.executable, str(SCRIPT)], cwd=tmp_path, capture_output=True, text=True)

    assert (result.returncode, result.stdout, result.stderr) == (0, f"v{version}\n", "")


def test_the_current_version_is_three_numbers_so_a_release_only_ever_bumps_the_last_one():
    version = json.loads(MANIFEST.read_text())["version"]

    assert re.fullmatch(r"\d+\.\d+\.\d+", version), version


def test_the_docstring_states_the_version_rule():
    doc = release_tag.__doc__

    assert "last number" in doc and "0.3.0" in doc and "0.3.1" in doc and "0.3.2" in doc
    assert "manifest" in doc and "never" in doc


INVALID_VERSIONS = [
    "1.0.0; rm -rf /",
    "v1",
    "",
    "1.0",
    "1",
    "1.0.0.0",
    "v1.0.0",
    "1.0.0-rc1",
    "1.0.0+build5",
    "1.0.x",
    "1..0",
    ".1.0",
    "1.0.",
    "-1.0.0",
    " 1.0.0",
    "1.0.0 ",
    "1.0.0\n",  # `$` in a pattern would let this through
    "1.0.0\n::error::forged workflow command",
    "$(id)",
    "`id`",
    "1.0.0 && echo pwned",
    "1.0.0|cat /etc/passwd",
    "１.０.０",  # full-width digits are digits to a Unicode-aware pattern
    "١.٠.٠",  # Arabic-Indic digits
    "9" * 5000 + ".0",
]


@pytest.mark.parametrize("version", INVALID_VERSIONS, ids=[repr(version[:40]) for version in INVALID_VERSIONS])
def test_a_version_that_is_not_three_numbers_fails_and_prints_no_tag(version, tmp_path, capsys):
    code, out, err = run_main(manifest_with(tmp_path, {"version": version}), capsys)

    assert code != 0
    assert out == ""
    assert err.startswith("release_tag:")
    assert not any(line.startswith("::") for line in err.splitlines())  # never echo something the runner would run as a command
    assert len(err) < 400


@pytest.mark.parametrize("version", [1, 1.5, 0, None, True, ["1.0.0"], {"major": 1}])
def test_a_version_that_is_not_a_string_fails(version, tmp_path, capsys):
    code, out, err = run_main(manifest_with(tmp_path, {"version": version}), capsys)

    assert code != 0 and out == "" and err.startswith("release_tag:")


@pytest.mark.parametrize("content", [{"name": "databricks-genie"}, [], ["0.3.0"], "0.3.0", None, "", "{not json", "{"])
def test_a_manifest_without_a_usable_version_fails(content, tmp_path, capsys):
    path = manifest_with(tmp_path, content if isinstance(content, str) else json.dumps(content))

    code, out, err = run_main(path, capsys)

    assert code != 0 and out == "" and err.startswith("release_tag:")


def test_a_missing_manifest_fails_and_names_the_file(tmp_path, capsys):
    path = tmp_path / "nowhere" / "plugin.json"

    code, out, err = run_main(path, capsys)

    assert code != 0 and out == ""
    assert err.startswith("release_tag:") and str(path) in err


def test_a_bad_version_makes_the_script_exit_non_zero_so_a_workflow_step_stops(tmp_path):
    path = manifest_with(tmp_path, {"version": "1.0.0; rm -rf /"})

    result = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)

    assert result.returncode != 0
    assert result.stdout == ""


@pytest.mark.parametrize("version", ["0.0.0", "0.3.0", "0.3.1", "10.20.30", "0.3.100"])
def test_every_three_number_version_is_accepted(version, tmp_path, capsys):
    code, out, _ = run_main(manifest_with(tmp_path, {"version": version}), capsys)

    assert (code, out) == (0, f"v{version}\n")


def test_the_tool_uses_only_the_standard_library_so_a_bare_runner_can_run_it():
    imported = set()
    for node in ast.walk(ast.parse(SCRIPT.read_text())):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module.split(".")[0])

    assert imported <= set(sys.stdlib_module_names), imported - set(sys.stdlib_module_names)

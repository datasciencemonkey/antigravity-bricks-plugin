"""tools/release_tag.py: the release tag is "v" plus the manifest's version, and a version that isn't three numbers stops the release."""
import ast
import io
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
    assert "leading zero" in doc


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
    "0.3.01",  # a leading zero would let one release go by two names (0.3.1 and 0.3.01)
    "01.0.0",
    "1.02.3",
    "00.0.0",
    "0.00.0",
    "0.0.00",
    "007.0.0",
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


@pytest.mark.parametrize("version", ["0.0.0", "0.3.0", "0.3.1", "0.3.10", "0.10.0", "10.20.30", "0.3.100", "100.200.300"])
def test_every_three_number_version_is_accepted(version, tmp_path, capsys):
    code, out, _ = run_main(manifest_with(tmp_path, {"version": version}), capsys)

    assert (code, out) == (0, f"v{version}\n")


# --- --newer-than-tags: the version must be newer than every release tag that already exists ----------------------


def newer_than(tags, version, tmp_path, capsys, monkeypatch):
    """Run --newer-than-tags with the given tag names on standard input (one per line, as git tag --list prints them)."""
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(f"{tag}\n" for tag in tags)))
    code = release_tag.main(["--newer-than-tags", str(manifest_with(tmp_path, {"version": version}))])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.parametrize(
    "version, tags",
    [
        ("0.3.1", []),
        ("0.3.1", ["v0.3.0"]),
        ("0.3.1", ["v0.2.9", "v0.3.0", "v0.1.0"]),
        ("0.3.10", ["v0.3.9"]),  # 10 is above 9, though "10" sorts below "9" as text
        ("1.0.0", ["v0.99.99"]),
        ("0.10.0", ["v0.9.9", "v0.9.10"]),
        ("1.0.0", ["v0.3.0", "v0.3.5", "v0.10.0"]),
    ],
)
def test_a_version_above_every_existing_release_tag_is_printed(version, tags, tmp_path, capsys, monkeypatch):
    code, out, err = newer_than(tags, version, tmp_path, capsys, monkeypatch)

    assert (code, out, err) == (0, f"v{version}\n", "")


@pytest.mark.parametrize(
    "version, tags, newest",
    [
        ("0.3.1", ["v0.3.1"], "v0.3.1"),  # the same version: its tag exists
        ("0.3.1", ["v0.3.5"], "v0.3.5"),
        ("0.3.1", ["v0.3.0", "v0.3.5", "v0.2.0"], "v0.3.5"),
        ("0.3.9", ["v0.3.10"], "v0.3.10"),
        ("0.9.0", ["v0.10.0"], "v0.10.0"),
        ("0.99.99", ["v1.0.0"], "v1.0.0"),
    ],
)
def test_a_version_at_or_below_an_existing_release_tag_prints_nothing_and_says_why(version, tags, newest, tmp_path, capsys, monkeypatch):
    code, out, err = newer_than(tags, version, tmp_path, capsys, monkeypatch)

    assert (code, out) == (0, "")  # nothing to release is not a failure; the empty output is the answer
    assert err.startswith("release_tag:") and f"v{version}" in err and newest in err


@pytest.mark.parametrize(
    "stray",
    [
        "v99", "v1", "latest", "v1.0.0-rc1", "v0.3.01", "v01.0.0", "V9.9.9", "release-9.9.9", "v1.2.3.4", "v9.9",
        "v9.9.9+build", "main", "", "v\u0663.\u0660.\u0660", "9.9.9",
    ],
)
def test_names_that_are_not_release_tags_do_not_count(stray, tmp_path, capsys, monkeypatch):
    code, out, err = newer_than([stray, "v0.3.0"], "0.3.1", tmp_path, capsys, monkeypatch)

    assert (code, out, err) == (0, "v0.3.1\n", "")


def test_a_trailing_carriage_return_or_space_on_a_tag_line_does_not_hide_the_tag(tmp_path, capsys, monkeypatch):
    # Reading too strictly could let a release through out of order; a stray space can only make the check stricter.
    code, out, _ = newer_than(["v0.3.5\r", " v0.3.4 "], "0.3.1", tmp_path, capsys, monkeypatch)

    assert (code, out) == (0, "")


def test_a_bad_version_still_fails_in_this_mode_and_prints_nothing(tmp_path, capsys, monkeypatch):
    code, out, err = newer_than(["v0.3.0"], "0.3.01", tmp_path, capsys, monkeypatch)

    assert code == 1 and out == "" and err.startswith("release_tag:")


def test_the_plain_mode_never_reads_standard_input(tmp_path, capsys, monkeypatch):
    class Unreadable(io.StringIO):
        def read(self, *args):
            raise AssertionError("standard input was read")

        __iter__ = readline = readlines = read

    monkeypatch.setattr(sys, "stdin", Unreadable())

    code, out, _ = run_main(manifest_with(tmp_path, {"version": "0.3.1"}), capsys)

    assert (code, out) == (0, "v0.3.1\n")


def test_the_mode_works_as_a_command_in_a_pipe_the_way_the_workflow_uses_it(tmp_path):
    manifest = manifest_with(tmp_path, {"version": "0.3.1"})
    command = [sys.executable, str(SCRIPT), "--newer-than-tags", str(manifest)]

    newer = subprocess.run(command, input="v0.2.0\nv0.3.0\n", capture_output=True, text=True)
    not_newer = subprocess.run(command, input="v0.2.0\nv0.3.1\n", capture_output=True, text=True)

    assert (newer.returncode, newer.stdout) == (0, "v0.3.1\n")
    assert (not_newer.returncode, not_newer.stdout) == (0, "") and "v0.3.1" in not_newer.stderr


def test_the_tool_uses_only_the_standard_library_so_a_bare_runner_can_run_it():
    imported = set()
    for node in ast.walk(ast.parse(SCRIPT.read_text())):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module.split(".")[0])

    assert imported <= set(sys.stdlib_module_names), imported - set(sys.stdlib_module_names)

"""The version gate: a change to the plugin must ship under a higher version.

The publishing guide asks that every published update increase `version`. Antigravity doesn't enforce it, so this does:
if any file under plugins/databricks-genie/ differs from the merge-base with origin/main, plugin.json's version has to be
higher than it was there.
"""
import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = "plugins/databricks-genie"
MANIFEST = f"{PLUGIN_DIR}/plugin.json"
BASE = "origin/main"


class NoBase(Exception):
    """There is nothing to compare with: origin/main is missing, or it shares no history with HEAD."""


def git(root, *args, check=True):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=check)


def version_numbers(version):
    return tuple(int(part) for part in version.split("."))  # numbers, not text: 1.10.0 is higher than 1.9.0


def unbumped_change(root):
    """A message if the plugin's files differ from the merge-base with origin/main without a higher version, else None.

    "Differ" covers the working tree too: committed, edited, deleted and new files. Raises NoBase when there is no
    merge-base to compare with.
    """
    found = git(root, "merge-base", BASE, "HEAD", check=False)
    if found.returncode != 0:
        raise NoBase(f"there is no merge-base with {BASE}")
    ancestor = found.stdout.strip()
    differing = git(root, "diff", "--name-only", "-z", ancestor, "--", PLUGIN_DIR).stdout
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "-z", "--", PLUGIN_DIR).stdout
    changed = sorted(name for name in (differing + untracked).split("\0") if name)
    if not changed:
        return None
    on_main = git(root, "show", f"{ancestor}:{MANIFEST}", check=False)
    if on_main.returncode != 0:
        return None  # main has no plugin yet, so any version is a fresh start
    before = json.loads(on_main.stdout)["version"]
    now = json.loads((Path(root) / MANIFEST).read_text())["version"]
    if version_numbers(now) > version_numbers(before):
        return None
    shown = ", ".join(changed[:5]) + (", ..." if len(changed) > 5 else "")
    return (
        f"{PLUGIN_DIR} differs from the merge-base with {BASE} ({shown}) but its version is {now}, which isn't higher "
        f"than {before} there. Bump the last number of the version in {MANIFEST}."
    )


def set_version(repo, version):
    repo.write(MANIFEST, json.dumps({"name": "databricks-genie", "version": version}))


@pytest.fixture
def plugin_repo(repo):
    """A temporary repo whose origin/main already holds the plugin, at version 1.0.0."""
    set_version(repo, "1.0.0")
    repo.write(f"{PLUGIN_DIR}/scripts/bridge.py", "print('first')\n")
    repo.commit("Add the plugin")
    repo.git("update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


def test_a_plugin_that_has_not_changed_needs_no_bump_so_main_itself_passes(plugin_repo):
    assert unbumped_change(plugin_repo.path) is None


def test_a_change_outside_the_plugin_needs_no_bump(plugin_repo):
    plugin_repo.write("README.md", "# changed\n")
    plugin_repo.commit("Edit the repo README")

    assert unbumped_change(plugin_repo.path) is None


def test_a_changed_plugin_file_with_the_same_version_is_reported_with_the_file_and_the_version(plugin_repo):
    plugin_repo.write(f"{PLUGIN_DIR}/scripts/bridge.py", "print('second')\n")
    plugin_repo.commit("Change the bridge")

    problem = unbumped_change(plugin_repo.path)

    assert problem is not None and "scripts/bridge.py" in problem and "1.0.0" in problem


def test_a_changed_plugin_with_a_higher_version_passes(plugin_repo):
    plugin_repo.write(f"{PLUGIN_DIR}/scripts/bridge.py", "print('second')\n")
    set_version(plugin_repo, "1.0.1")
    plugin_repo.commit("Change the bridge and bump the version")

    assert unbumped_change(plugin_repo.path) is None


def test_the_bump_may_come_in_a_later_commit_of_the_same_branch(plugin_repo):
    plugin_repo.write(f"{PLUGIN_DIR}/scripts/bridge.py", "print('second')\n")
    plugin_repo.commit("Change the bridge")
    set_version(plugin_repo, "1.1.0")
    plugin_repo.commit("Bump the version")

    assert unbumped_change(plugin_repo.path) is None


def test_a_version_that_is_lower_than_the_one_on_main_is_reported(plugin_repo):
    plugin_repo.write(f"{PLUGIN_DIR}/scripts/bridge.py", "print('second')\n")
    set_version(plugin_repo, "0.9.9")
    plugin_repo.commit("Change the bridge and lower the version")

    assert unbumped_change(plugin_repo.path) is not None


def test_versions_compare_as_numbers_so_1_10_0_is_higher_than_1_9_0(plugin_repo):
    set_version(plugin_repo, "1.9.0")
    plugin_repo.commit("Bump to 1.9.0")
    plugin_repo.git("update-ref", "refs/remotes/origin/main", "HEAD")
    plugin_repo.write(f"{PLUGIN_DIR}/scripts/bridge.py", "print('second')\n")
    set_version(plugin_repo, "1.10.0")
    plugin_repo.commit("Change the bridge and bump to 1.10.0")

    assert unbumped_change(plugin_repo.path) is None


def test_an_edit_that_is_not_committed_yet_counts_as_a_change(plugin_repo):
    plugin_repo.write(f"{PLUGIN_DIR}/scripts/bridge.py", "print('second')\n")

    assert unbumped_change(plugin_repo.path) is not None


def test_a_new_file_that_is_not_added_yet_counts_as_a_change(plugin_repo):
    plugin_repo.write(f"{PLUGIN_DIR}/assets/new.txt", "new\n")

    problem = unbumped_change(plugin_repo.path)

    assert problem is not None and "assets/new.txt" in problem


def test_a_deleted_plugin_file_counts_as_a_change(plugin_repo):
    plugin_repo.git("rm", "-q", f"{PLUGIN_DIR}/scripts/bridge.py")
    plugin_repo.commit("Remove the bridge")

    assert unbumped_change(plugin_repo.path) is not None


def test_a_plugin_that_main_does_not_have_yet_can_start_at_any_version(repo):
    set_version(repo, "0.1.0")
    repo.commit("Add the plugin")

    assert unbumped_change(repo.path) is None


def test_a_missing_origin_main_leaves_nothing_to_compare_with(plugin_repo):
    plugin_repo.git("update-ref", "-d", "refs/remotes/origin/main")

    with pytest.raises(NoBase, match="origin/main"):
        unbumped_change(plugin_repo.path)


def test_a_history_that_shares_nothing_with_origin_main_leaves_nothing_to_compare_with(plugin_repo):
    plugin_repo.git("checkout", "-q", "--orphan", "unrelated")
    plugin_repo.commit("A root commit with no parent")

    with pytest.raises(NoBase, match="origin/main"):
        unbumped_change(plugin_repo.path)


def test_the_plugin_in_this_repo_is_not_changed_without_a_higher_version():
    try:
        problem = unbumped_change(REPO_ROOT)
    except NoBase as why:
        pytest.skip(f"{why}; fetch it with: git fetch origin main")

    assert problem is None, problem

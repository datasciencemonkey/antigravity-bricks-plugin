"""The release workflow: after the tests pass on main, tag and release the tested commit if its version has no tag yet."""
import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest
import yaml
from conftest import ISOLATED_GIT, TempRepo

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
RELEASE = WORKFLOWS / "release.yml"
TESTS = WORKFLOWS / "tests.yml"
MANIFEST = "plugins/databricks-genie/plugin.json"
TOOLS = ["tools/__init__.py", "tools/release_tag.py"]

COMPLETED_AND_GREEN = "github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.event == 'push'"
HEAD_SHA = "${{ github.event.workflow_run.head_sha }}"


def load(path):
    return yaml.safe_load(path.read_text())


def release_workflow():
    return load(RELEASE)


def triggers(workflow):
    return workflow["on"] if "on" in workflow else workflow[True]  # YAML 1.1 reads the key `on` as the boolean True


def job(workflow):
    (only,) = workflow["jobs"].values()  # a single job
    return only


def steps(workflow):
    return job(workflow)["steps"]


def run_scripts(workflow):
    return [step["run"] for step in steps(workflow) if "run" in step]


def release_step(workflow):
    (step,) = [step for step in steps(workflow) if "gh release create" in step.get("run", "")]
    return step


def test_the_tests_workflow_is_still_named_tests_because_the_release_waits_for_it_by_name():
    assert load(TESTS)["name"] == "tests"


def test_runs_only_when_the_tests_workflow_completes_on_main():
    on = triggers(release_workflow())

    assert set(on) == {"workflow_run"}
    assert on["workflow_run"] == {"workflows": [load(TESTS)["name"]], "types": ["completed"], "branches": ["main"]}


def test_releases_only_when_the_tests_passed_and_the_run_was_a_push():
    condition = " ".join(str(job(release_workflow())["if"]).split())

    assert condition == COMPLETED_AND_GREEN


def test_the_token_can_write_contents_and_nothing_else():
    workflow = release_workflow()

    assert workflow["permissions"] == {"contents": "write"}
    assert "permissions" not in job(workflow)  # a job-level block would replace the workflow-level one


def test_every_action_is_pinned_to_a_full_commit_sha():
    used = [step["uses"] for step in steps(release_workflow()) if "uses" in step]

    assert used
    for action in used:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}", action), action


def checkout(workflow):
    (step,) = [step for step in steps(workflow) if step.get("uses", "").startswith("actions/checkout@")]
    return step


def test_checkout_is_the_same_pinned_commit_as_the_tests_workflow_uses():
    assert checkout(release_workflow())["uses"] == checkout(load(TESTS))["uses"]


def test_checks_out_the_commit_the_tests_passed_on_with_every_tag():
    options = checkout(release_workflow())["with"]

    assert options["ref"] == HEAD_SHA  # not whatever main has moved on to
    assert options["fetch-depth"] == 0 and options["fetch-tags"] is True
    assert options["persist-credentials"] is False  # nothing pushes; gh uses the token from the environment


def test_no_run_script_has_an_expression_in_it():
    scripts = run_scripts(release_workflow())

    assert scripts
    for script in scripts:
        assert "${{" not in script


def test_event_data_and_the_token_reach_the_script_only_through_env():
    step = release_step(release_workflow())

    assert step["env"]["HEAD_SHA"] == HEAD_SHA
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert step["env"]["GH_REPO"] == "${{ github.repository }}"
    assert '"$HEAD_SHA"' in step["run"] and "$HEAD_SHA" not in step["run"].replace('"$HEAD_SHA"', "")


def test_gets_the_tag_from_release_tag_py():
    assert "python3 tools/release_tag.py" in release_step(release_workflow())["run"]


def test_checks_for_the_tag_and_the_release_before_it_creates_anything():
    script = release_step(release_workflow())["run"]

    tag_exists = script.index("git rev-parse -q --verify")
    release_exists = script.index("gh release view")
    create = script.index("gh release create")
    assert tag_exists < release_exists < create
    assert script[tag_exists:release_exists].count("exit 0") == 1  # a tag that exists ends the run
    assert script[release_exists:create].count("exit 0") == 1  # so does a release that exists


def test_creates_the_release_at_the_tested_commit_and_names_it_after_the_tag():
    script = release_step(release_workflow())["run"]

    assert re.search(r'gh release create "\$TAG" --target "\$HEAD_SHA" --title "\$TAG"', script)


def test_never_edits_the_manifest_or_pushes_or_commits():
    for script in run_scripts(release_workflow()):
        for forbidden in ("plugin.json", "git commit", "git push", "git tag", "git add", "sed ", "> plugins"):
            assert forbidden not in script, forbidden


# The scripts below run the workflow's real shell script against a temporary checkout, with a stand-in for gh.

FAKE_GH = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
if args[:2] == ["release", "view"]:
    sys.exit(0 if os.environ.get("FAKE_GH_RELEASE_EXISTS") == "1" else 1)
sys.exit(0)
"""


class Checkout:
    """A temporary checkout shaped like the runner's, and a way to run the workflow's release script in it."""

    def __init__(self, tmp_path):
        self.repo = TempRepo(tmp_path / "checkout")
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        fake = self.bin / "gh"
        fake.write_text(FAKE_GH)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.log = tmp_path / "gh-calls.jsonl"
        self.log.write_text("")
        self.script = tmp_path / "release-step.sh"
        self.script.write_text(release_step(release_workflow())["run"])
        for name in TOOLS:
            self.repo.write(name, (REPO_ROOT / name).read_bytes())
        self.set_version(json.loads((REPO_ROOT / MANIFEST).read_text())["version"])

    def set_version(self, version):
        self.repo.write(MANIFEST, json.dumps({"name": "databricks-genie", "version": version}, indent=2) + "\n")
        self.head = self.repo.commit(f"Version {version}")

    def run(self, head_sha=None, release_exists=False):
        env = {
            **os.environ, **ISOLATED_GIT,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GH_LOG": str(self.log), "FAKE_GH_RELEASE_EXISTS": "1" if release_exists else "0",
            "GH_TOKEN": "not-a-real-token", "GH_REPO": "owner/repo", "HEAD_SHA": head_sha or self.head,
        }
        # GitHub runs `run:` steps as `bash --noprofile --norc -eo pipefail {0}`.
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(self.script)],
            cwd=self.repo.path, env=env, capture_output=True, text=True,
        )

    def gh_calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def creates(self):
        return [call for call in self.gh_calls() if call[:2] == ["release", "create"]]


@pytest.fixture
def checkout_dir(tmp_path):
    return Checkout(tmp_path)


def test_a_new_version_gets_a_tag_and_a_release_at_the_tested_commit(checkout_dir):
    result = checkout_dir.run()

    assert result.returncode == 0, result.stderr
    (create,) = checkout_dir.creates()
    version = json.loads((REPO_ROOT / MANIFEST).read_text())["version"]
    assert create[:6] == ["release", "create", f"v{version}", "--target", checkout_dir.head, "--title"]
    assert create[6] == f"v{version}"


def test_the_tag_is_v_plus_the_version_in_the_manifest_of_the_tested_commit(checkout_dir):
    checkout_dir.set_version("7.8.9")

    result = checkout_dir.run()

    assert result.returncode == 0, result.stderr
    (create,) = checkout_dir.creates()
    assert create[2] == "v7.8.9" and create[create.index("--title") + 1] == "v7.8.9"


def test_a_version_whose_tag_exists_is_left_alone(checkout_dir):
    checkout_dir.repo.git("tag", "v0.3.0")
    checkout_dir.set_version("0.3.0")

    result = checkout_dir.run()

    assert result.returncode == 0, result.stderr
    assert checkout_dir.creates() == []


def test_a_version_that_already_has_a_release_is_left_alone(checkout_dir):
    result = checkout_dir.run(release_exists=True)

    assert result.returncode == 0, result.stderr
    assert checkout_dir.creates() == []


def test_a_tag_for_another_version_does_not_stop_a_new_version(checkout_dir):
    checkout_dir.repo.git("tag", "v0.2.9")
    checkout_dir.set_version("0.3.1")

    result = checkout_dir.run()

    assert result.returncode == 0, result.stderr
    assert [create[2] for create in checkout_dir.creates()] == ["v0.3.1"]


def test_a_version_that_is_not_three_numbers_fails_the_step_and_creates_nothing(checkout_dir):
    checkout_dir.set_version("1.0.0; touch pwned")

    result = checkout_dir.run()

    assert result.returncode != 0
    assert checkout_dir.gh_calls() == []
    assert not (checkout_dir.repo.path / "pwned").exists()


def test_a_hostile_head_sha_stays_one_argument_and_runs_nothing(checkout_dir):
    hostile = "abc; touch pwned && echo $(id)"

    result = checkout_dir.run(head_sha=hostile)

    assert result.returncode == 0, result.stderr
    (create,) = checkout_dir.creates()
    assert create[create.index("--target") + 1] == hostile
    assert not (checkout_dir.repo.path / "pwned").exists()


def test_the_run_changes_nothing_in_the_checkout(checkout_dir):
    manifest = checkout_dir.repo.path / MANIFEST
    before = (manifest.read_bytes(), checkout_dir.repo.git("rev-parse", "HEAD"), checkout_dir.repo.git("tag", "--list"))

    result = checkout_dir.run()

    assert result.returncode == 0, result.stderr
    after = (manifest.read_bytes(), checkout_dir.repo.git("rev-parse", "HEAD"), checkout_dir.repo.git("tag", "--list"))
    assert after == before  # gh makes the tag on GitHub; the checkout gets no tag, no commit and no version change
    assert checkout_dir.repo.git("status", "--porcelain") == ""

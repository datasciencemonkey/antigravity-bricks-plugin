"""The release workflow: after the tests pass on main, tag and release the tested commit if its version has no tag yet."""
import json
import os
import re
import shutil
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
TOOLS = ["tools/__init__.py", "tools/release_tag.py", "tools/release_notes.py"]

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


def test_the_notes_are_built_by_release_notes_py_and_reach_gh_only_as_a_file():
    script = release_step(release_workflow())["run"]
    create = next(line for line in script.splitlines() if "gh release create" in line)

    assert 'gh api --paginate "repos/{owner}/{repo}/pulls?state=closed&base=main' in script
    # --repo: only pull requests from branches of this repository, by its owner, members and collaborators, are listed
    assert 'python3 tools/release_notes.py --repo "$GH_REPO" --pulls "$PULLS" --head "$HEAD_SHA" --output "$NOTES"' in script
    assert '--notes-file "$NOTES"' in create and "--generate-notes" in create  # ours first, then GitHub's generated list
    assert "--notes " not in create and "--notes=" not in create  # never the text itself on the command line
    assert script.index("tools/release_notes.py") < script.index("gh release create")
    assert re.fullmatch(r'NOTES="\$RUNNER_TEMP/[\w.-]+\.md"', next(line.strip() for line in script.splitlines() if line.strip().startswith("NOTES=")))


def test_never_edits_the_manifest_or_pushes_or_commits():
    for script in run_scripts(release_workflow()):
        for forbidden in (r"plugin\.json", r"git commit", r"git push", r"git tag", r"git add", r"\bsed\b", r"> plugins"):
            assert not re.search(forbidden, script), forbidden


def test_only_a_commit_that_is_on_main_is_released_and_that_is_checked_before_anything_is_read_or_asked():
    script = release_step(release_workflow())["run"]

    ancestor = script.index('git merge-base --is-ancestor "$HEAD_SHA" origin/main')
    for later in ("python3 tools/release_tag.py", "git rev-parse", "gh release view", "gh api", "gh release create"):
        assert ancestor < script.index(later), later
    after = script[ancestor:script.index("python3 tools/release_tag.py")]
    assert "-eq 1" in after and "exit 0" in after  # not an ancestor: skip
    assert "exit \"$status\"" in after  # any other failure (no origin/main, a bad object) stops the step


def test_the_commit_from_the_event_is_checked_to_be_a_full_sha_before_git_gets_it():
    script = release_step(release_workflow())["run"]

    check = script.index('[[ "$HEAD_SHA" =~ ^[0-9a-f]{40}$ ]]')
    assert check < script.index("git merge-base") and "exit 1" in script[check:script.index("git merge-base")]


def test_no_notice_or_error_line_repeats_event_data():
    # Workflow commands are read from the log, so text from the event must not be echoed into one.
    for line in release_step(release_workflow())["run"].splitlines():
        if "::notice" in line or "::error" in line:
            assert "HEAD_SHA" not in line and "GH_" not in line, line


def test_the_checkout_fetches_every_branch_so_origin_main_is_there_to_compare_with():
    assert checkout(release_workflow())["with"]["fetch-depth"] == 0  # all history of all branches and tags


def test_a_commit_without_the_release_scripts_is_skipped_with_a_notice_before_anything_else_runs():
    script = release_step(release_workflow())["run"]

    assert "::notice" in script and "exit 0" in script[script.index("::notice"):script.index("python3 tools/release_tag.py")]
    for later in ("python3 tools/release_tag.py", "git rev-parse", "gh release view", "gh api", "gh release create"):
        assert script.index("::notice") < script.index(later), later


# The scripts below run the workflow's real shell script against a temporary checkout, with a stand-in for gh.

FAKE_GH = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
notes = None
if "--notes-file" in args:  # the real gh reads the file when it is called, so the stand-in does too
    with open(args[args.index("--notes-file") + 1]) as handle:
        notes = handle.read()
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps({"args": args, "notes": notes}) + "\\n")
if args[:2] == ["release", "view"]:
    sys.exit(0 if os.environ.get("FAKE_GH_RELEASE_EXISTS") == "1" else 1)
if args[:1] == ["api"]:
    sys.stdout.write(os.environ.get("FAKE_GH_PULLS", "[]"))
sys.exit(0)
"""


def pull(number, title, commit, body=None, source="owner/repo", association="OWNER"):
    """One item of the JSON that `gh api repos/<owner>/<repo>/pulls` returns (the harness runs as owner/repo).

    source is the repository the head branch lives in and association the author's `author_association`.
    """
    return {
        "number": number, "title": title, "body": body, "merged_at": f"2026-10-23T10:{number:02d}:00Z",
        "author_association": association, "merge_commit_sha": commit, "head": {"sha": commit, "repo": {"full_name": source}},
    }


class Checkout:
    """A temporary checkout shaped like the runner's, and a way to run the workflow's release script in it."""

    def __init__(self, tmp_path):
        self.root = tmp_path
        self.repo = TempRepo(tmp_path / "checkout")
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        fake = self.bin / "gh"
        fake.write_text(FAKE_GH)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.log = tmp_path / "gh-calls.jsonl"
        self.log.write_text("")
        self.runner_temp = tmp_path / "runner-temp"
        self.runner_temp.mkdir()
        self.script = tmp_path / "release-step.sh"
        self.script.write_text(release_step(release_workflow())["run"])
        for name in TOOLS:
            self.repo.write(name, (REPO_ROOT / name).read_bytes())
        self.set_version(json.loads((REPO_ROOT / MANIFEST).read_text())["version"])

    def set_version(self, version):
        self.repo.write(MANIFEST, json.dumps({"name": "databricks-genie", "version": version}, indent=2) + "\n")
        self.head = self.repo.commit(f"Version {version}")
        self.repo.git("update-ref", "refs/remotes/origin/main", self.head)  # fetch-depth 0 fetches every branch

    def commit_off_main(self, message="A commit that is not on main"):
        """A commit on a side branch, which is what a pushed tag can point at. The checkout goes back to main."""
        self.repo.git("checkout", "-q", "-b", "side")
        sha = self.repo.commit(message)
        self.repo.git("checkout", "-q", "main")
        return sha

    def check_out(self, sha):
        self.repo.git("checkout", "-q", "--detach", sha)

    def run(self, head_sha=None, release_exists=False, pulls="[]"):
        """Run the step. pulls is what the stand-in gh prints for `gh api`: JSON text, or a list to turn into it."""
        env = {
            **os.environ, **ISOLATED_GIT,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GH_LOG": str(self.log), "FAKE_GH_RELEASE_EXISTS": "1" if release_exists else "0",
            "FAKE_GH_PULLS": pulls if isinstance(pulls, str) else json.dumps(pulls),
            "GH_TOKEN": "not-a-real-token", "GH_REPO": "owner/repo", "HEAD_SHA": self.head if head_sha is None else head_sha,
            "RUNNER_TEMP": str(self.runner_temp),
        }
        # GitHub runs `run:` steps as `bash --noprofile --norc -eo pipefail {0}`.
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(self.script)],
            cwd=self.repo.path, env=env, capture_output=True, text=True,
        )

    def ran_the_canary(self):
        """Whether something ran `touch canary`. Hostile text in these tests tries that; the step runs in the checkout."""
        return any((folder / "canary").exists() for folder in (self.repo.path, self.runner_temp, self.root))

    def entries(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def gh_calls(self):
        return [entry["args"] for entry in self.entries()]

    def creates(self):
        return [call for call in self.gh_calls() if call[:2] == ["release", "create"]]

    def create_entries(self):
        return [entry for entry in self.entries() if entry["args"][:2] == ["release", "create"]]


@pytest.fixture
def checkout_dir(tmp_path):
    return Checkout(tmp_path)


def test_a_new_version_gets_a_tag_and_a_release_at_the_tested_commit(checkout_dir):
    result = checkout_dir.run()

    assert result.returncode == 0, result.stderr
    assert "::notice" not in result.stdout  # the scripts are there, so nothing is skipped
    (create,) = checkout_dir.creates()
    version = json.loads((REPO_ROOT / MANIFEST).read_text())["version"]
    assert create[:6] == ["release", "create", f"v{version}", "--target", checkout_dir.head, "--title"]
    assert create[6] == f"v{version}"


@pytest.mark.parametrize("missing", ["tools/release_tag.py", "tools/release_notes.py", "tools"])
def test_an_older_commit_without_the_release_scripts_is_skipped_with_a_notice_not_failed(missing, checkout_dir):
    # This workflow runs from main, so a tests run that ends after it landed can fire it for a commit that predates it.
    target = checkout_dir.repo.path / missing
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    checkout_dir.head = checkout_dir.repo.commit("An older commit")

    result = checkout_dir.run()

    assert result.returncode == 0, result.stderr
    assert checkout_dir.gh_calls() == []  # no tag looked up, no pull requests listed, nothing created
    (notice,) = [line for line in result.stdout.splitlines() if line.startswith("::notice")]
    assert (missing if missing != "tools" else "tools/release_tag.py") in notice


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
    checkout_dir.set_version("1.0.0; touch canary")

    result = checkout_dir.run()

    assert result.returncode != 0
    assert checkout_dir.gh_calls() == []
    assert not checkout_dir.ran_the_canary()


@pytest.mark.parametrize(
    "head_sha",
    ["abc; touch canary && echo $(id)", "--all", "HEAD", "main", "abc", "", "A" * 40, "g" * 40, "0" * 39, "0" * 41, "0" * 40 + "\n"],
)
def test_a_head_sha_that_is_not_a_full_lowercase_sha_stops_the_release_before_anything_runs(head_sha, checkout_dir):
    result = checkout_dir.run(head_sha=head_sha)

    assert result.returncode != 0
    assert checkout_dir.gh_calls() == []
    assert "::error::" in result.stdout
    assert not checkout_dir.ran_the_canary()


def test_a_commit_that_is_not_on_main_is_skipped_with_a_notice(checkout_dir):
    # The tests also run for a pushed tag, and the event's branch is then the tag's name, which can be "main".
    side = checkout_dir.commit_off_main()

    result = checkout_dir.run(head_sha=side)

    assert result.returncode == 0, result.stderr
    assert checkout_dir.gh_calls() == []
    (notice,) = [line for line in result.stdout.splitlines() if line.startswith("::notice")]
    assert "main" in notice and side not in notice


def test_a_commit_that_main_has_moved_past_is_still_released(checkout_dir):
    older = checkout_dir.head
    checkout_dir.set_version("0.3.1")  # main moves on while the tests ran
    checkout_dir.check_out(older)  # the workflow checks out the tested commit

    result = checkout_dir.run(head_sha=older)

    assert result.returncode == 0, result.stderr
    version = json.loads((REPO_ROOT / MANIFEST).read_text())["version"]
    assert [create[2] for create in checkout_dir.creates()] == [f"v{version}"]


def test_a_checkout_without_origin_main_stops_the_release_instead_of_guessing(checkout_dir):
    checkout_dir.repo.git("update-ref", "-d", "refs/remotes/origin/main")

    result = checkout_dir.run()

    assert result.returncode != 0
    assert checkout_dir.gh_calls() == []


def test_the_release_gets_a_notes_file_of_the_pull_requests_merged_since_the_previous_tag(checkout_dir):
    before = checkout_dir.head
    checkout_dir.repo.git("tag", "v0.3.0")
    checkout_dir.set_version("0.3.1")
    pulls = [
        pull(1, "Merged before the previous release", before, "## Summary\n- Old news.\n"),
        pull(2, "Bump the version", checkout_dir.head, "## Summary\n- Fixes the thing.\n- Needs no setup.\n\n## Issues\nCloses #9\n"),
    ]

    result = checkout_dir.run(pulls=pulls)

    assert result.returncode == 0, result.stderr
    (entry,) = checkout_dir.create_entries()
    args = entry["args"]
    assert Path(args[args.index("--notes-file") + 1]).parent == checkout_dir.runner_temp
    assert "--generate-notes" in args  # GitHub's own list follows ours
    assert entry["notes"] == (
        "## Pull requests since v0.3.0\n\n- Bump the version (#2)\n  - Fixes the thing.\n  - Needs no setup.\n"
    )
    assert [call[0] for call in checkout_dir.gh_calls()] == ["release", "api", "release"]  # view, the pulls, create


def test_the_notes_leave_out_pull_requests_from_forks_and_from_outsiders(checkout_dir):
    # A pull request opened from a fork at a commit we publish is marked merged when our fast-forward lands that commit.
    checkout_dir.set_version("0.3.1")
    pulls = [
        pull(2, "Ours", checkout_dir.head, "## Summary\n- Fixes the thing.\n"),
        pull(3, "From a fork", checkout_dir.head, "## Summary\n- Forged.\n", source="someone-else/repo"),
        pull(4, "From a stranger", checkout_dir.head, "## Summary\n- Also forged.\n", association="NONE"),
    ]

    result = checkout_dir.run(pulls=pulls)

    assert result.returncode == 0, result.stderr
    (entry,) = checkout_dir.create_entries()
    assert entry["notes"] == "## Pull requests\n\n- Ours (#2)\n  - Fixes the thing.\n"


def test_the_first_release_lists_every_merged_pull_request(checkout_dir):
    pulls = [pull(1, "The first change", checkout_dir.head)]

    result = checkout_dir.run(pulls=pulls)

    assert result.returncode == 0, result.stderr
    (entry,) = checkout_dir.create_entries()
    assert entry["notes"] == "## Pull requests\n\n- The first change (#1)\n"


def test_pull_request_text_reaches_the_notes_file_but_never_a_command_line_or_a_shell(checkout_dir):
    hostile = "`touch canary` $(touch canary); touch canary # --title injected"
    checkout_dir.set_version("0.3.1")
    pulls = [pull(2, hostile, checkout_dir.head, f"## Summary\n- {hostile}\n")]

    result = checkout_dir.run(pulls=pulls)

    assert result.returncode == 0, result.stderr
    (entry,) = checkout_dir.create_entries()
    assert entry["notes"].count(hostile) == 2
    for call in checkout_dir.gh_calls():
        assert not any("canary" in argument or "injected" in argument for argument in call)
    assert not checkout_dir.ran_the_canary()


def test_the_canary_check_does_notice_a_script_that_evaluates_pull_request_text(checkout_dir):
    # The tests above look for the canary file after hostile text went through the step. This one proves the look can
    # succeed: the same step with one careless line added, which evaluates the notes as shell, does create the file.
    careless = checkout_dir.script.read_text().replace(
        '--output "$NOTES"\n', '--output "$NOTES"\neval "$(cat "$NOTES")"\n', 1
    )
    assert 'eval "$(cat "$NOTES")"' in careless
    checkout_dir.script.write_text(careless)
    checkout_dir.set_version("0.3.1")

    checkout_dir.run(pulls=[pull(2, "$(touch canary) #", checkout_dir.head)])

    assert checkout_dir.ran_the_canary()


def test_pull_request_json_that_cannot_be_read_stops_the_release(checkout_dir):
    result = checkout_dir.run(pulls='{"number": 1, "title": ')

    assert result.returncode != 0
    assert checkout_dir.creates() == []


def test_the_run_changes_nothing_in_the_checkout(checkout_dir):
    manifest = checkout_dir.repo.path / MANIFEST
    before = (manifest.read_bytes(), checkout_dir.repo.git("rev-parse", "HEAD"), checkout_dir.repo.git("tag", "--list"))

    result = checkout_dir.run()

    assert result.returncode == 0, result.stderr
    after = (manifest.read_bytes(), checkout_dir.repo.git("rev-parse", "HEAD"), checkout_dir.repo.git("tag", "--list"))
    assert after == before  # gh makes the tag on GitHub; the checkout gets no tag, no commit and no version change
    assert checkout_dir.repo.git("status", "--porcelain") == ""

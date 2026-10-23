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


def test_only_one_release_job_runs_at_a_time_and_a_running_one_is_never_cancelled():
    # Two pushes in quick succession would otherwise both look for a tag, find none, and both try to make it.
    assert release_workflow()["concurrency"] == {"group": "release", "cancel-in-progress": False}


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
    newer = script.index("--newer-than-tags")
    create = script.index("gh release create")
    assert tag_exists < release_exists < newer < create
    assert script[tag_exists:release_exists].count("exit 0") == 1  # a tag that exists ends the run
    assert script[release_exists:newer].count("exit 0") == 1  # so does a release that exists
    assert script[newer:create].count("exit 0") == 1  # and so does a version that is not newer than a release tag


def test_a_version_not_newer_than_an_existing_release_tag_is_skipped_with_a_notice():
    script = release_step(release_workflow())["run"]

    command = "git tag --list 'v*' | python3 tools/release_tag.py --newer-than-tags"
    assert f'NEWER="$({command})"' in script  # in a variable, so a failing pipe stops the step (set -e, pipefail)
    after = script[script.index(command):script.index("PULLS=")]
    assert '[ -z "$NEWER" ]' in after and "::notice" in after and "exit 0" in after


def test_the_release_has_only_our_own_notes_because_githubs_generated_list_names_pull_requests_from_forks():
    script = release_step(release_workflow())["run"]
    commands = "\n".join(line for line in script.splitlines() if not line.strip().startswith("#"))  # without the comments

    assert "--generate-notes" not in commands and "generate-notes" not in commands and "--notes " not in commands


def test_the_answer_to_whether_a_release_exists_is_not_thrown_away():
    script = release_step(release_workflow())["run"]
    view = next(line for line in script.splitlines() if "gh release view" in line)

    assert "/dev/null" not in view and "2>&1" in view  # the error text is kept, to tell "not found" from a real failure
    assert 'case "$RELEASE" in' in script and '*"release not found"*' in script


def test_makes_the_tag_at_the_tested_commit_through_the_api_and_then_releases_that_tag():
    script = release_step(release_workflow())["run"]
    commands = "\n".join(line for line in script.splitlines() if not line.strip().startswith("#"))  # without the comments
    make_tag = 'gh api "repos/$GH_REPO/git/refs" -f ref="refs/tags/$TAG" -f sha="$HEAD_SHA"'

    assert make_tag in commands
    # --target would only choose the commit while the tag doesn't exist; --verify-tag makes gh use the tag made above
    assert re.search(r'gh release create "\$TAG" --verify-tag --title "\$TAG"', commands) and "--target" not in commands
    assert commands.index("tools/release_notes.py") < commands.index(make_tag) < commands.index("gh release create")


def test_a_release_that_cannot_be_made_takes_back_the_tag_only_when_gh_confirms_there_is_no_release():
    script = release_step(release_workflow())["run"]
    create = script.index("if ! gh release create")

    after = script[create:]
    view = after.index("gh release view")
    delete = after.index('gh api -X DELETE "repos/$GH_REPO/git/refs/tags/$TAG"')
    assert view < delete  # the tag is taken back only after asking whether the release exists ...
    assert '*"release not found"*' in after[view:delete]  # ... and only when gh says that it does not
    assert not re.search(r"\|\|\s*true", script)  # a failed delete is not hidden
    assert "::error::" in after[delete:]  # it is reported
    ending = [line.strip() for line in after.rstrip().splitlines()[-2:]]
    assert ending == ["exit 1", "fi"] and after.count("exit") == 1  # whatever happened, the step fails
    assert "gh api -X DELETE" not in script[:create]  # only after this run made the tag, never before


def test_the_notes_are_built_by_release_notes_py_and_reach_gh_only_as_a_file():
    script = release_step(release_workflow())["run"]
    create = next(line for line in script.splitlines() if "gh release create" in line)

    assert 'gh api --paginate "repos/{owner}/{repo}/pulls?state=closed&base=main' in script
    # --repo: only pull requests from branches of this repository, by its owner, members and collaborators, are listed;
    # --tag: the tag being released, which the closing "Full changelog" link names
    assert (
        'python3 tools/release_notes.py --repo "$GH_REPO" --tag "$TAG" --pulls "$PULLS" --head "$HEAD_SHA" --output "$NOTES"'
        in script
    )
    assert '--notes-file "$NOTES"' in create
    assert "--notes " not in create and "--notes=" not in create  # never the text itself on the command line
    assert script.index("tools/release_notes.py") < script.index("gh release create")
    assert re.fullmatch(r'NOTES="\$RUNNER_TEMP/[\w.-]+\.md"', next(line.strip() for line in script.splitlines() if line.strip().startswith("NOTES=")))


def test_never_edits_the_manifest_or_pushes_or_commits():
    for script in run_scripts(release_workflow()):
        # (git tag --list only reads the tags; the tag itself is made on GitHub, by the release)
        for forbidden in (r"plugin\.json", r"git commit", r"git push", r"git tag(?! --list)", r"git add", r"\bsed\b", r"> plugins"):
            assert not re.search(forbidden, script), forbidden


def test_only_a_commit_that_is_on_main_is_released_and_that_is_checked_before_anything_is_read_or_asked():
    script = release_step(release_workflow())["run"]

    ancestor = script.index('git merge-base --is-ancestor "$HEAD_SHA" refs/remotes/origin/main')
    for later in ("python3 tools/release_tag.py", "git rev-parse", "gh release view", "gh api", "gh release create"):
        assert ancestor < script.index(later), later
    after = script[ancestor:script.index("python3 tools/release_tag.py")]
    assert "-eq 1" in after and "exit 0" in after  # not an ancestor: skip
    assert "exit \"$status\"" in after  # any other failure (no origin/main, a bad object) stops the step


def test_the_branch_to_compare_with_is_named_in_full_so_that_no_tag_can_stand_in_for_it():
    script = release_step(release_workflow())["run"]
    commands = "\n".join(line for line in script.splitlines() if not line.strip().startswith("#"))  # without the comments

    assert "refs/remotes/origin/main" in commands
    assert not re.search(r"(?<!refs/remotes/)\borigin/main", commands)  # git reads a bare origin/main as a tag first


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
env = os.environ.get
notes = None
if "--notes-file" in args:  # the real gh reads the file when it is called, so the stand-in does too
    with open(args[args.index("--notes-file") + 1]) as handle:
        notes = handle.read()
with open(env("FAKE_GH_LOG")) as log:
    earlier = [json.loads(line)["args"] for line in log]  # the calls made before this one
with open(env("FAKE_GH_LOG"), "a") as log:
    log.write(json.dumps({"args": args, "notes": notes}) + "\\n")


def fail(message):
    sys.stderr.write(message + "\\n")
    sys.exit(1)


if args[:2] == ["release", "view"]:
    made = any(call[:2] == ["release", "create"] for call in earlier)
    if made and env("FAKE_GH_VIEW_AFTER_CREATE") == "exists":  # create reported a failure after GitHub made the release
        sys.exit(0)
    if made and env("FAKE_GH_VIEW_AFTER_CREATE_ERROR"):  # and now the API is failing
        fail(env("FAKE_GH_VIEW_AFTER_CREATE_ERROR"))
    if env("FAKE_GH_VIEW_ERROR"):  # the API is down, or the token can't read releases
        fail(env("FAKE_GH_VIEW_ERROR"))
    if env("FAKE_GH_RELEASE_EXISTS") != "1":
        fail("release not found")  # what the real gh says when there is no such release
elif args[:2] == ["release", "create"]:
    if env("FAKE_GH_CREATE_FAILS") == "1":
        fail("HTTP 422: Validation Failed")
elif args[:1] == ["api"]:
    endpoint = next(arg for arg in args[1:] if arg.startswith("repos/"))
    if endpoint.endswith("/git/refs"):  # making a tag
        if env("FAKE_GH_TAG_EXISTS") == "1":  # someone made it since the checkout
            fail("gh: Reference already exists (HTTP 422)")
        print("{}")
    elif "DELETE" in args:  # taking a tag away
        if env("FAKE_GH_DELETE_FAILS") == "1":
            fail("gh: Server Error (HTTP 500)")
        print("{}")
    else:  # listing the pull requests
        sys.stdout.write(env("FAKE_GH_PULLS", "[]"))
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

    def run(self, head_sha=None, release_exists=False, pulls="[]", fake=None):
        """Run the step. pulls is what the stand-in gh prints for `gh api`: JSON text, or a list to turn into it.

        fake is a dict of extra FAKE_GH_* variables that make the stand-in fail the way the real gh can.
        """
        env = {
            **os.environ, **ISOLATED_GIT,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GH_LOG": str(self.log), "FAKE_GH_RELEASE_EXISTS": "1" if release_exists else "0",
            "FAKE_GH_PULLS": pulls if isinstance(pulls, str) else json.dumps(pulls),
            "GH_TOKEN": "not-a-real-token", "GH_REPO": "owner/repo", "HEAD_SHA": self.head if head_sha is None else head_sha,
            "RUNNER_TEMP": str(self.runner_temp), **(fake or {}),
        }
        # A `run:` step with no `shell:` runs as `bash -e {0}`: errors stop it, but a failure inside a pipe doesn't unless
        # the script sets pipefail itself (only an explicit `shell: bash` adds it), so the stand-in run doesn't add it.
        return subprocess.run(
            ["bash", "-e", str(self.script)], cwd=self.repo.path, env=env, capture_output=True, text=True,
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

    def tag_refs(self):
        """The calls that make a tag on GitHub."""
        return [call for call in self.gh_calls() if call[:1] == ["api"] and call[1].endswith("/git/refs")]

    def tag_deletions(self):
        return [call for call in self.gh_calls() if call[:1] == ["api"] and "DELETE" in call]

    def create_entries(self):
        return [entry for entry in self.entries() if entry["args"][:2] == ["release", "create"]]


@pytest.fixture
def checkout_dir(tmp_path):
    return Checkout(tmp_path)


def test_a_new_version_gets_a_tag_and_a_release_at_the_tested_commit(checkout_dir):
    result = checkout_dir.run()

    assert result.returncode == 0, result.stderr
    assert "::notice" not in result.stdout  # the scripts are there, so nothing is skipped
    version = json.loads((REPO_ROOT / MANIFEST).read_text())["version"]
    (tag_ref,) = checkout_dir.tag_refs()
    assert tag_ref == ["api", "repos/owner/repo/git/refs", "-f", f"ref=refs/tags/v{version}", "-f", f"sha={checkout_dir.head}"]
    (create,) = checkout_dir.creates()
    assert create[:6] == ["release", "create", f"v{version}", "--verify-tag", "--title", f"v{version}"]
    assert "--target" not in create and "--generate-notes" not in create
    assert checkout_dir.tag_deletions() == []
    calls = checkout_dir.gh_calls()
    assert calls.index(tag_ref) < calls.index(create)  # the tag exists before the release is made from it


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


@pytest.mark.parametrize(
    "message",
    [
        "HTTP 502: Bad Gateway (https://api.example.invalid/repos/owner/repo/releases/tags/v0.3.0)",
        "HTTP 403: Resource not accessible by integration",
        "error connecting to the API server",
    ],
)
def test_a_failure_to_find_out_whether_the_release_exists_stops_the_release(message, checkout_dir):
    result = checkout_dir.run(fake={"FAKE_GH_VIEW_ERROR": message})

    assert result.returncode != 0
    assert [call[:2] for call in checkout_dir.gh_calls()] == [["release", "view"]]  # nothing after the failed question
    assert "::error::" in result.stdout and message in result.stderr  # and the log says why


def test_a_release_that_gh_reports_as_not_found_lets_the_release_go_ahead(checkout_dir):
    result = checkout_dir.run()  # the stand-in says "release not found", as the real gh does

    assert result.returncode == 0, result.stderr
    assert len(checkout_dir.creates()) == 1


def test_a_version_that_already_has_a_release_is_left_alone(checkout_dir):
    result = checkout_dir.run(release_exists=True)

    assert result.returncode == 0, result.stderr
    assert checkout_dir.creates() == []


def test_a_re_run_for_a_version_below_an_existing_release_tag_is_skipped_with_a_notice(checkout_dir):
    # The tests ran again for an old commit, whose version was never tagged, while a newer release exists.
    checkout_dir.repo.git("tag", "v0.3.5")
    checkout_dir.set_version("0.3.1")

    result = checkout_dir.run()

    assert result.returncode == 0, result.stderr
    assert [call[:2] for call in checkout_dir.gh_calls()] == [["release", "view"]]  # no pull requests, no tag, no release
    (notice,) = [line for line in result.stdout.splitlines() if line.startswith("::notice")]
    assert "v0.3.1" in notice


@pytest.mark.parametrize(
    "version, tag, released",
    [("0.3.10", "v0.3.9", True), ("0.3.9", "v0.3.10", False), ("1.0.0", "v0.99.99", True), ("0.9.0", "v0.10.0", False)],
)
def test_versions_are_compared_as_numbers_not_as_text(version, tag, released, checkout_dir):
    checkout_dir.repo.git("tag", tag)
    checkout_dir.set_version(version)

    result = checkout_dir.run()

    assert result.returncode == 0, result.stderr
    assert (len(checkout_dir.creates()) == 1) is released


def test_names_that_are_not_release_tags_do_not_hold_a_release_back(checkout_dir):
    for stray in ("v99", "v1", "latest", "v0.3.01", "v1.0.0-rc1", "V9.9.9", "v9.9"):
        checkout_dir.repo.git("tag", stray)
    checkout_dir.set_version("0.3.1")

    result = checkout_dir.run()

    assert result.returncode == 0, result.stderr
    assert [create[2] for create in checkout_dir.creates()] == ["v0.3.1"]


def test_a_git_failure_while_listing_the_tags_stops_the_release_instead_of_reading_as_no_tags(checkout_dir):
    shim = checkout_dir.bin / "git"
    shim.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = tag ] && [ "$2" = --list ] && [ "$3" = "v*" ]; then echo "fatal: simulated failure" >&2; exit 128; fi\n'
        f'exec "{shutil.which("git")}" "$@"\n'
    )
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)

    result = checkout_dir.run()

    assert result.returncode != 0 and "simulated failure" in result.stderr
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


def test_tags_named_like_the_branch_cannot_stand_in_for_it(checkout_dir):
    # git reads a short `origin/main` as the tag refs/tags/origin/main before the branch refs/remotes/origin/main.
    # Someone who can push tags could put `main` and `origin/main` on a commit that is not on main, and the step would
    # then tag and release that commit, running its own scripts with the token.
    side = checkout_dir.commit_off_main()
    checkout_dir.repo.git("tag", "main", side)
    checkout_dir.repo.git("tag", "origin/main", side)
    assert checkout_dir.repo.git("rev-parse", "origin/main") == side  # the short name is shadowed, as the attack needs

    result = checkout_dir.run(head_sha=side)

    assert result.returncode == 0, result.stderr
    assert checkout_dir.gh_calls() == []
    (notice,) = [line for line in result.stdout.splitlines() if line.startswith("::notice")]
    assert "not on main" in notice


def test_a_commit_on_main_is_still_released_while_such_tags_exist(checkout_dir):
    side = checkout_dir.commit_off_main()
    checkout_dir.repo.git("tag", "main", side)
    checkout_dir.repo.git("tag", "origin/main", side)

    result = checkout_dir.run()  # the commit main points at

    assert result.returncode == 0, result.stderr
    assert len(checkout_dir.creates()) == 1


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
    assert "--generate-notes" not in args  # GitHub's list would name pull requests that are not ours to list
    assert entry["notes"] == (
        "## Pull requests since v0.3.0\n\n- Bump the version (#2)\n  - Fixes the thing.\n  - Needs no setup.\n"
        "\nFull changelog: https://github.com/owner/repo/compare/v0.3.0...v0.3.1\n"  # GH_REPO is owner/repo
    )
    assert [call[0] for call in checkout_dir.gh_calls()] == ["release", "api", "api", "release"]  # view, pulls, tag, release


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


def test_a_tag_that_appeared_since_the_checkout_stops_the_release_and_is_left_alone(checkout_dir):
    # Someone made the tag after the checkout, perhaps at another commit. gh release create --target would then have
    # released wherever that tag points. Making the tag through the API fails instead, and nothing else happens.
    result = checkout_dir.run(fake={"FAKE_GH_TAG_EXISTS": "1"})

    assert result.returncode != 0 and "Reference already exists" in result.stderr
    assert len(checkout_dir.tag_refs()) == 1
    assert checkout_dir.creates() == []
    assert checkout_dir.tag_deletions() == []  # it is not ours to take away


def test_a_release_that_cannot_be_made_does_not_leave_its_tag_behind(checkout_dir):
    # The next run would see the tag, count the version as released, and never make the release.
    result = checkout_dir.run(fake={"FAKE_GH_CREATE_FAILS": "1"})

    version = json.loads((REPO_ROOT / MANIFEST).read_text())["version"]
    assert result.returncode != 0
    assert checkout_dir.tag_deletions() == [["api", "-X", "DELETE", f"repos/owner/repo/git/refs/tags/v{version}"]]
    calls = checkout_dir.gh_calls()
    after_create = calls[calls.index(checkout_dir.creates()[0]):]
    assert [call[:2] for call in after_create] == [["release", "create"], ["release", "view"], ["api", "-X"]]  # asked first
    assert "::error::" not in result.stdout  # the tag was taken back, so there is nothing more to report


def test_a_release_that_exists_though_gh_reported_a_failure_keeps_its_tag(checkout_dir):
    # A lost response or a 502 can come after GitHub made the release. Taking its tag away would turn the live release
    # into a draft, which the next run's gh release view would find and count as released for good.
    result = checkout_dir.run(fake={"FAKE_GH_CREATE_FAILS": "1", "FAKE_GH_VIEW_AFTER_CREATE": "exists"})

    version = json.loads((REPO_ROOT / MANIFEST).read_text())["version"]
    assert result.returncode != 0
    assert checkout_dir.tag_deletions() == []
    (error,) = [line for line in result.stdout.splitlines() if line.startswith("::error::")]
    assert f"v{version}" in error and "exists" in error


@pytest.mark.parametrize("message", ["HTTP 502: Bad Gateway", "error connecting to the API server"])
def test_a_release_that_cannot_be_looked_up_after_a_failed_create_keeps_its_tag(message, checkout_dir):
    # Not knowing whether the release exists is not the same as knowing that it does not.
    result = checkout_dir.run(fake={"FAKE_GH_CREATE_FAILS": "1", "FAKE_GH_VIEW_AFTER_CREATE_ERROR": message})

    assert result.returncode != 0
    assert checkout_dir.tag_deletions() == []
    assert "::error::" in result.stdout and message in result.stderr


def test_a_tag_that_cannot_be_taken_back_is_reported(checkout_dir):
    result = checkout_dir.run(fake={"FAKE_GH_CREATE_FAILS": "1", "FAKE_GH_DELETE_FAILS": "1"})

    version = json.loads((REPO_ROOT / MANIFEST).read_text())["version"]
    assert result.returncode != 0
    assert len(checkout_dir.tag_deletions()) == 1  # it was tried
    (error,) = [line for line in result.stdout.splitlines() if line.startswith("::error::")]
    assert f"v{version}" in error and "by hand" in error


def test_the_first_release_lists_every_merged_pull_request(checkout_dir):
    pulls = [pull(1, "The first change", checkout_dir.head)]

    result = checkout_dir.run(pulls=pulls)

    assert result.returncode == 0, result.stderr
    (entry,) = checkout_dir.create_entries()
    assert entry["notes"] == "## Pull requests\n\n- The first change (#1)\n"  # no previous release, so no changelog link


def test_the_changelog_link_names_the_tag_being_released_and_the_previous_release(checkout_dir):
    checkout_dir.repo.git("tag", "v0.2.9")
    checkout_dir.set_version("0.3.0")

    result = checkout_dir.run()

    assert result.returncode == 0, result.stderr
    (entry,) = checkout_dir.create_entries()
    assert entry["notes"].splitlines()[-1] == "Full changelog: https://github.com/owner/repo/compare/v0.2.9...v0.3.0"


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

"""The CI workflow: `uv run --locked pytest` on every push, with the permissions and history the checks need."""
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "tests.yml"


def load_workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def triggers(workflow):
    on = workflow["on"] if "on" in workflow else workflow[True]  # YAML 1.1 reads the key `on` as the boolean True
    return {on: None} if isinstance(on, str) else (dict.fromkeys(on) if isinstance(on, list) else on)


def test_runs_on_every_push_and_not_on_pull_requests():
    on = triggers(load_workflow())

    assert set(on) == {"push"}  # a pull_request run would test GitHub's synthetic merge commit and fail the identity check
    assert not on["push"]  # no branch, tag or path filters: every push is tested


def test_token_permissions_are_read_only_contents():
    assert load_workflow()["permissions"] == {"contents": "read"}


def steps(workflow):
    (job,) = workflow["jobs"].values()  # a single job
    return job["steps"]


def step_using(workflow, action):
    return next(step for step in steps(workflow) if step.get("uses", "").startswith(f"{action}@"))


def test_checkout_fetches_the_full_history_so_origin_main_exists_for_the_commit_range_check():
    assert step_using(load_workflow(), "actions/checkout")["with"]["fetch-depth"] == 0


def test_sets_up_uv_after_checkout_and_then_runs_pytest_against_the_locked_dependencies():
    sequence = [step.get("uses", "").split("@")[0] or step.get("run") for step in steps(load_workflow())]

    assert sequence == ["actions/checkout", "astral-sh/setup-uv", "uv run --locked pytest"]

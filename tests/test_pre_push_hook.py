"""The tracked pre-push hook, run against a stand-in `uv` (the real one would run this very suite again, forever)."""
import os
import subprocess
from pathlib import Path

import pytest
from conftest import ISOLATED_GIT

HOOK = Path(__file__).resolve().parent.parent / "scripts" / "hooks" / "pre-push"
PUSHED_REFS = f"refs/heads/work {'1' * 40} refs/heads/work {'0' * 40}\n"


@pytest.fixture
def fake_uv(tmp_path):
    """A stand-in `uv` that records its working directory, arguments, stdin and git env; FAKE_UV_STATUS is its exit status."""
    bin_dir = tmp_path / "bin"
    calls = tmp_path / "uv-calls"
    bin_dir.mkdir()
    calls.mkdir()
    stub = bin_dir / "uv"
    stub.write_text(
        '#!/bin/sh\n'
        'pwd -P > "$FAKE_UV_CALLS/cwd"\n'
        'echo "$@" > "$FAKE_UV_CALLS/args"\n'
        'cat > "$FAKE_UV_CALLS/stdin"\n'
        'printf "%s" "${GIT_DIR-unset}" > "$FAKE_UV_CALLS/git_dir"\n'
        'printf "%s" "${GIT_INDEX_FILE-unset}" > "$FAKE_UV_CALLS/git_index_file"\n'
        'exit "${FAKE_UV_STATUS:-0}"\n'
    )
    stub.chmod(0o755)
    return bin_dir, calls


def run_hook(cwd, fake_uv, uv_status=0, extra_env=None):
    bin_dir, calls = fake_uv
    env = {
        **os.environ, **ISOLATED_GIT,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_UV_CALLS": str(calls),
        "FAKE_UV_STATUS": str(uv_status),
        **(extra_env or {}),
    }
    return subprocess.run(
        [str(HOOK), "origin", "https://example.test/repo.git"],
        cwd=cwd, env=env, input=PUSHED_REFS, capture_output=True, text=True,
    )


def test_runs_pytest_through_uv_against_the_locked_dependencies_and_passes_when_it_passes(repo, fake_uv):
    result = run_hook(repo.path, fake_uv)

    assert result.returncode == 0
    assert (fake_uv[1] / "args").read_text().strip() == "run --locked pytest"


def test_clears_gits_repo_local_environment_so_tests_that_make_their_own_repos_are_not_pointed_at_this_one(repo, fake_uv):
    git_dir = Path(repo.path) / ".git"
    run_hook(repo.path, fake_uv, extra_env={"GIT_DIR": str(git_dir), "GIT_INDEX_FILE": str(git_dir / "index")})

    assert (fake_uv[1] / "git_dir").read_text() == "unset"
    assert (fake_uv[1] / "git_index_file").read_text() == "unset"


@pytest.mark.parametrize("uv_status", [1, 5])
def test_fails_the_push_with_pytests_own_status_when_the_tests_fail(repo, fake_uv, uv_status):
    assert run_hook(repo.path, fake_uv, uv_status=uv_status).returncode == uv_status


def test_runs_from_the_top_of_the_repo_even_when_started_in_a_subdirectory(repo, fake_uv):
    subdirectory = repo.path / "docs" / "deep"
    subdirectory.mkdir(parents=True)

    run_hook(subdirectory, fake_uv)

    assert (fake_uv[1] / "cwd").read_text().strip() == os.path.realpath(repo.path)


def test_does_not_hand_the_pushed_refs_on_to_the_test_run(repo, fake_uv):
    run_hook(repo.path, fake_uv)

    assert (fake_uv[1] / "stdin").read_text() == ""

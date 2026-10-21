import os
import shutil
import subprocess
import sys
from pathlib import Path

from tools import install_hooks

HOOK_SOURCE = Path(__file__).resolve().parent.parent / "scripts" / "hooks" / "pre-push"


def test_installs_the_tracked_hook_into_the_git_hooks_directory(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo.path)

    assert install_hooks.main([]) == 0

    installed = repo.path / ".git" / "hooks" / "pre-push"
    assert installed.read_bytes() == HOOK_SOURCE.read_bytes()
    assert os.access(installed, os.X_OK)
    assert "installed" in capsys.readouterr().out


def test_running_it_twice_changes_nothing_the_second_time(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo.path)
    install_hooks.main([])
    capsys.readouterr()
    installed = repo.path / ".git" / "hooks" / "pre-push"
    modified_before = installed.stat().st_mtime_ns

    assert install_hooks.main([]) == 0

    assert "already installed" in capsys.readouterr().out
    assert installed.stat().st_mtime_ns == modified_before


def test_refuses_to_replace_a_different_existing_hook(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo.path)
    existing = repo.path / ".git" / "hooks" / "pre-push"
    existing.write_text("#!/bin/sh\necho somebody else's hook\n")

    assert install_hooks.main([]) == 1

    assert existing.read_text() == "#!/bin/sh\necho somebody else's hook\n"
    assert "--force" in capsys.readouterr().out


def test_force_replaces_a_different_existing_hook(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo.path)
    existing = repo.path / ".git" / "hooks" / "pre-push"
    existing.write_text("#!/bin/sh\necho somebody else's hook\n")

    assert install_hooks.main(["--force"]) == 0

    assert existing.read_bytes() == HOOK_SOURCE.read_bytes()
    assert os.access(existing, os.X_OK)
    assert "installed" in capsys.readouterr().out


def test_creates_the_hooks_directory_when_the_repo_has_none(repo, monkeypatch):
    monkeypatch.chdir(repo.path)
    shutil.rmtree(repo.path / ".git" / "hooks")

    assert install_hooks.main([]) == 0

    assert (repo.path / ".git" / "hooks" / "pre-push").read_bytes() == HOOK_SOURCE.read_bytes()


def test_installs_into_the_shared_common_dir_when_run_from_a_linked_worktree(repo, monkeypatch):
    linked = repo.path.parent / "linked-worktree"
    repo.git("worktree", "add", "-q", "-b", "feature", str(linked))
    monkeypatch.chdir(linked)

    assert install_hooks.main([]) == 0

    assert (repo.path / ".git" / "hooks" / "pre-push").read_bytes() == HOOK_SOURCE.read_bytes()


def test_module_entry_point_installs_the_hook(repo):
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)}

    result = subprocess.run(
        [sys.executable, "-m", "tools.install_hooks"], cwd=repo.path, env=env, capture_output=True, text=True
    )

    assert result.returncode == 0
    assert (repo.path / ".git" / "hooks" / "pre-push").read_bytes() == HOOK_SOURCE.read_bytes()


def test_an_identical_hook_that_lost_its_executable_bit_is_repaired(repo, monkeypatch):
    monkeypatch.chdir(repo.path)
    installed = repo.path / ".git" / "hooks" / "pre-push"
    installed.write_bytes(HOOK_SOURCE.read_bytes())
    installed.chmod(0o644)

    assert install_hooks.main([]) == 0

    assert os.access(installed, os.X_OK)

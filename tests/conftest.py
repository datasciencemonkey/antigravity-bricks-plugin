import os
import subprocess

import pytest

GOOD_EMAIL = "datasciencemonkey@gmail.com"

# Git exports GIT_DIR and friends to hooks, aliases and `rebase --exec`. Inherited here, they would aim every temporary
# repo's git commands at the real repository, so clear them before any test runs.
for _name in subprocess.run(["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True).stdout.split():
    os.environ.pop(_name, None)

# Git run by the tests ignores the machine's global and system config (hooks path, signing, templates), so the
# tests behave the same everywhere and never trigger managed hooks.
ISOLATED_GIT = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "GIT_TERMINAL_PROMPT": "0"}


class TempRepo:
    def __init__(self, path):
        self.path = path
        path.mkdir()
        self.git("init", "-q", "-b", "main")

    def git(self, *args, env=None):
        result = subprocess.run(
            ["git", *args], cwd=self.path, capture_output=True, text=True, check=True,
            env={**os.environ, **ISOLATED_GIT, **(env or {})},
        )
        return result.stdout.strip()

    def write(self, name, content):
        path = self.path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)

    def commit(self, message, author_email=GOOD_EMAIL, committer_email=GOOD_EMAIL):
        identity = {
            "GIT_AUTHOR_NAME": "Author Name", "GIT_AUTHOR_EMAIL": author_email,
            "GIT_COMMITTER_NAME": "Committer Name", "GIT_COMMITTER_EMAIL": committer_email,
        }
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message, env=identity)
        return self.git("rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    """A temporary git repo with one clean commit (README and a complete .gitignore), and origin/main pointing at it."""
    temp = TempRepo(tmp_path / "repo")
    temp.write("README.md", "# temp repo\n")
    temp.write(
        ".gitignore",
        ".omc/\n.isaac/\n.build/\nAntigravity Plugin Marketplace Publishing Guide.pdf\n.github/hooks/superset-notify.json\n",
    )
    temp.commit("Base commit")
    temp.git("update-ref", "refs/remotes/origin/main", "HEAD")
    return temp

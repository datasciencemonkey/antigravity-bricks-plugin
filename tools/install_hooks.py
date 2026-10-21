"""Install the tracked pre-push hook into this repo's git hooks directory."""
import argparse
import shutil
import subprocess
from pathlib import Path

HOOK_SOURCE = Path(__file__).resolve().parent.parent / "scripts" / "hooks" / "pre-push"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Install the tracked pre-push hook (it runs the test suite before every push)."
    )
    parser.add_argument(
        "--force", action="store_true", help="replace a different pre-push hook that is already installed"
    )
    args = parser.parse_args(argv)

    # The common dir is shared by every worktree of the repo; a linked worktree's own git dir has no hooks of its own.
    common_dir = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"], capture_output=True, text=True, check=True
    ).stdout.strip()
    target = Path(common_dir).resolve() / "hooks" / "pre-push"
    if target.exists():
        if target.read_bytes() == HOOK_SOURCE.read_bytes():
            target.chmod(0o755)  # git ignores a hook that isn't executable
            print(f"the pre-push hook is already installed at {target}")
            return 0
        if not args.force:
            print(f"a different pre-push hook is already at {target}; to replace it, run again with --force")
            return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HOOK_SOURCE, target)
    target.chmod(0o755)
    print(f"installed the pre-push hook at {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

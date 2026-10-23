"""Print the release tag for the Databricks Genie plugin: "v" plus the version in its manifest.

The release workflow (.github/workflows/release.yml) runs this to learn which tag the commit it is looking at should
carry. The version rule:

- plugins/databricks-genie/plugin.json is the only source of the version, and the tag is always "v" plus that exact
  string. This tool and the workflow never compute, bump or write a version.
- Each release bumps only the last number: 0.3.0, then 0.3.1, then 0.3.2. A person changes the manifest; the workflow
  only reads it.
- The version must be exactly three numbers separated by dots. Anything else ("1.0.0; rm -rf /", "v1", an empty
  string) prints no tag, says why on stderr and exits with status 1, so the workflow stops before it tags anything.

Standard library only: the runner runs it with a bare python3.
"""
import argparse
import json
import re
import sys
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent.parent / "plugins" / "databricks-genie" / "plugin.json"

# Not ^...$ (the $ accepts a trailing newline), and ASCII only (a bare \d also matches digits from other scripts).
VERSION = re.compile(r"\d+\.\d+\.\d+", re.ASCII)


class VersionError(Exception):
    """The manifest can't give a release version."""


def read_version(manifest):
    """The manifest's version, if it is a string of three dot-separated numbers; otherwise raise VersionError."""
    try:
        data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    except OSError as problem:
        raise VersionError(f"cannot read {manifest}: {problem.strerror or problem}") from problem
    except ValueError as problem:  # not JSON, or not UTF-8
        raise VersionError(f"{manifest} is not valid JSON") from problem
    version = data.get("version") if isinstance(data, dict) else None
    if not isinstance(version, str):
        raise VersionError(f'{manifest} has no "version" string')
    if not VERSION.fullmatch(version):
        # repr() keeps a newline in the value from starting a line of its own in the log.
        raise VersionError(f"the version {version[:60]!r} in {manifest} is not three numbers separated by dots (like 0.3.1)")
    return version


def main(argv=None):
    parser = argparse.ArgumentParser(description="Print the release tag: v plus the version in the plugin manifest.")
    parser.add_argument(
        "manifest", nargs="?", type=Path, default=MANIFEST, help="the manifest to read (default: the Genie plugin's)"
    )
    args = parser.parse_args(argv)
    try:
        version = read_version(args.manifest)
    except VersionError as problem:
        print(f"release_tag: {problem}", file=sys.stderr)
        return 1
    print(f"v{version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
